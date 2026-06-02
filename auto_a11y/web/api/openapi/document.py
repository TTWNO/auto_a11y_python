"""The @document decorator — validates, serializes, and registers an endpoint.

Four jobs in one wrapper:

1. **JSON request validation.** If ``request=Model`` is set, parse the JSON
   body and call ``Model.model_validate(...)``. The validated model becomes
   the ``body`` keyword arg to the handler. On ValidationError, return a
   400 Problem via the central mapper.
2. **Multipart request validation.** If ``request_form=Model`` is set,
   validate ``request.form`` (the non-file fields) and pass the result as
   ``form``. If ``request_files=[<name>, ...]`` is set, pass each named
   ``request.files[<name>]`` as a keyword arg of the same name. Both apply
   together to handlers that take file uploads alongside metadata.
3. **Response serialization.** If the handler returns a BaseModel (or
   ``(BaseModel, int)``), dump it with ``model_dump(mode='json',
   by_alias=True)`` and ``jsonify()``. ``exclude_none`` is intentionally
   not used: null-valued fields stay on the wire as explicit ``null`` so
   the response shape (e.g. cursor pagination's ``next_cursor``) is stable
   — see ``_serialize``. If it returns a Flask Response (or
   ``(Response, int)``), pass it through unchanged.
4. **Documentation.** Attach an EndpointDoc to the wrapper; a separate
   ``register_documented_views(app)`` walk inserts it into the module-level
   registry once Flask has bound the rule.

The decorator preserves the wrapped view's signature via ParamSpec; no
``Any`` leaks into the wrapped callable.
"""
from __future__ import annotations

import functools
from typing import (
    Callable,
    Optional,
    ParamSpec,
    Protocol,
    Union,
    cast,
    runtime_checkable,
)

from flask import Flask, Response, jsonify, request as flask_request
from pydantic import BaseModel, ValidationError
from werkzeug.datastructures import FileStorage

from auto_a11y.web.api.errors import FieldError, ProblemDetails, problem_response
from auto_a11y.web.api.openapi.errors import validation_error_to_problem


def _wrong_content_type_problem(*, expected: str) -> tuple[Response, int]:
    """Return a (400 Problem, 400) tuple for a wrong-content-type request.

    Multipart-only endpoints raise this when a client posts JSON to a
    ``request_form=`` handler. The wire shape mirrors
    :func:`validation_error_to_problem` so callers can de-multiplex on
    ``error[*].code == 'wrong_content_type'``.
    """
    problem = ProblemDetails(
        type="https://auto-a11y/errors/validation",
        title="Validation failed",
        status=400,
        detail=f"this endpoint requires Content-Type: {expected}",
        errors=(
            FieldError(
                field="<request>",
                code="wrong_content_type",
                message=f"expected {expected}",
            ),
        ),
    )
    return problem_response(problem)
from auto_a11y.web.api.openapi.registry import (
    EndpointDoc,
    SecurityScheme,
    register,
)

# Return shape of a documented view.
ResponseLike = Union[
    BaseModel,
    tuple[BaseModel, int],
    Response,
    tuple[Response, int],
]
# Return shape of the wrapper (after serialization).
WrappedReturn = Union[Response, tuple[Response, int]]

P = ParamSpec("P")


@runtime_checkable
class DocumentedView(Protocol):
    """Structural type for the wrapper @document returns.

    Carries an ``__doc_meta__`` attribute the registration walk reads, and
    is callable with the signature Flask's view-function dispatcher expects.
    """

    __doc_meta__: EndpointDoc

    def __call__(self, *args: object, **kwargs: object) -> WrappedReturn: ...


def document(
    *,
    request: Optional[type[BaseModel]] = None,
    request_form: Optional[type[BaseModel]] = None,
    request_files: Optional[list[str]] = None,
    response_200: Optional[type[BaseModel]] = None,
    response_201: Optional[type[BaseModel]] = None,
    response_202: Optional[type[BaseModel]] = None,
    response_204: Optional[type[BaseModel]] = None,
    errors: Optional[list[int]] = None,
    tags: list[str],
    summary: str,
    description: Optional[str] = None,
    security: SecurityScheme = "bearer+session",
    allow_json_alternative: bool = False,
) -> Callable[[Callable[P, ResponseLike]], DocumentedView]:
    """Decorate a view with validation, serialization, and documentation.

    Exactly one of ``request`` or ``request_form`` may be set; setting both is
    a programmer error (mixing JSON and multipart on the same endpoint).

    ``allow_json_alternative=True`` opts a multipart endpoint into also
    accepting an ``application/json`` body. The decorator skips form
    validation and the file-kwarg binding for JSON requests — the
    handler is responsible for reading the JSON body itself (typically
    via ``flask.request.get_json()``). Used by hybrid endpoints like
    ``POST /api/v1/projects/<id>/pdfs`` that accept either a multipart
    upload OR a JSON URL-fetch instruction. Defaults to ``False`` so
    pure-multipart endpoints stay strict (a JSON body 400s).
    """
    if request is not None and request_form is not None:
        raise RuntimeError(
            "@document(request=..., request_form=...) is contradictory; pick one"
        )

    responses: dict[int, type[BaseModel]] = {}
    if response_200 is not None:
        responses[200] = response_200
    if response_201 is not None:
        responses[201] = response_201
    if response_202 is not None:
        responses[202] = response_202
    if response_204 is not None:
        responses[204] = response_204

    def decorator(view: Callable[P, ResponseLike]) -> DocumentedView:
        doc = EndpointDoc(
            view=view,
            request_model=request,
            request_form_model=request_form,
            request_files=tuple(request_files or ()),
            responses=responses,
            errors=errors or [],
            tags=tags,
            summary=summary,
            description=description,
            security=security,
        )

        @functools.wraps(view)
        def wrapper(*args: object, **kwargs: object) -> WrappedReturn:
            if request is not None:
                raw_payload: object = flask_request.get_json(silent=True)
                payload: object = raw_payload if raw_payload is not None else {}
                try:
                    body = request.model_validate(payload)
                except ValidationError as exc:
                    return validation_error_to_problem(exc)
                kwargs["body"] = body
            elif request_form is not None:
                # Multipart endpoints reject non-multipart requests at the
                # boundary unless ``allow_json_alternative=True`` was
                # set on the @document decoration. ``flask_request.form``
                # is silently empty for a JSON body, which would
                # otherwise let an empty ``request_form`` model pass
                # validation and run the handler — masking the wrong
                # content type. Surface a clean 400 instead so clients
                # see the contract violation.
                #
                # The ``allow_json_alternative`` carve-out is for hybrid
                # endpoints that branch on Content-Type inside the
                # handler (e.g. PDF upload accepting either multipart or
                # a JSON URL-fetch shape). For JSON requests we skip
                # form validation entirely and hand back an empty
                # ``request_form`` model — the handler is responsible
                # for reading ``request.get_json()`` itself.
                is_multipart = (flask_request.mimetype or "").startswith(
                    "multipart/form-data"
                )
                if not is_multipart and not allow_json_alternative:
                    return _wrong_content_type_problem(expected="multipart/form-data")

                if is_multipart:
                    form_payload: dict[str, object] = dict(flask_request.form.items())
                    try:
                        form_model = request_form.model_validate(form_payload)
                    except ValidationError as exc:
                        return validation_error_to_problem(exc)
                    kwargs["form"] = form_model
                    for file_field in request_files or ():
                        file_obj: Optional[FileStorage] = flask_request.files.get(
                            file_field
                        )
                        kwargs[file_field] = file_obj
                else:
                    # JSON alternative: pass an empty form model and
                    # ``None`` for every file kwarg. The handler reads
                    # the JSON body itself.
                    kwargs["form"] = request_form.model_validate({})
                    for file_field in request_files or ():
                        kwargs[file_field] = None
            result = cast(Callable[..., ResponseLike], view)(*args, **kwargs)
            return _serialize(result)

        setattr(wrapper, "__doc_meta__", doc)
        return cast(DocumentedView, wrapper)

    return decorator


def _serialize(result: ResponseLike) -> WrappedReturn:
    """Convert a view's return value into a Flask response.

    ``exclude_none`` is deliberately ``False``: every field a response
    model declares — even ones whose runtime value is ``None`` — appears
    on the wire as an explicit ``null``. Callers (tests, frontend) treat
    "key absent" as a contract change; keeping the field present
    preserves the cursor-pagination shape (``items``, ``next_cursor``)
    where ``next_cursor`` is ``None`` on the final page but MUST still
    be readable as ``body["next_cursor"]``.
    """
    if isinstance(result, BaseModel):
        return jsonify(result.model_dump(mode="json", by_alias=True))
    if isinstance(result, Response):
        return result
    # ``result`` is a 2-tuple: either (BaseModel, int) or (Response, int).
    body, status = result
    if isinstance(body, BaseModel):
        payload: Response = jsonify(
            body.model_dump(mode="json", by_alias=True)
        )
        return payload, status
    return body, status


def register_documented_views(app: Flask, *, prefix: str = "/api/v1") -> None:
    """Walk app.url_map and insert each documented view's EndpointDoc.

    Idempotent across multiple calls per app (skips entries already present)
    so the test fixture can call it after each ``add_url_rule`` without
    raising duplicate-registration errors for unchanged routes.

    Rule normalization: the registry stores keys relative to ``prefix``.
    For ``prefix='/api/v1'``, the rule string in url_map is
    ``/api/v1/projects`` and the registry key becomes ``/projects``.

    Tests that register views on a vanilla app (no blueprint, no prefix)
    pass ``prefix=""`` to keep the rules as-is.
    """
    for rule in app.url_map.iter_rules():
        view_obj = app.view_functions.get(rule.endpoint)
        if not isinstance(view_obj, DocumentedView):
            continue
        meta = view_obj.__doc_meta__
        registry_rule = _relativize(rule.rule, prefix)
        for method in sorted(rule.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            register(method, registry_rule, meta)


def _relativize(rule: str, prefix: str) -> str:
    """Strip ``prefix`` from ``rule`` if it leads, else return unchanged."""
    if prefix and rule.startswith(prefix):
        stripped = rule[len(prefix):]
        return stripped or "/"
    return rule
