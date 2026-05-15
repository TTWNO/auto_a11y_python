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
   by_alias=True, exclude_none=True)`` and ``jsonify()``. If it returns a
   Flask Response (or ``(Response, int)``), pass it through unchanged.
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

from auto_a11y.web.api.openapi.errors import validation_error_to_problem
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
) -> Callable[[Callable[P, ResponseLike]], DocumentedView]:
    """Decorate a view with validation, serialization, and documentation.

    Exactly one of ``request`` or ``request_form`` may be set; setting both is
    a programmer error (mixing JSON and multipart on the same endpoint).
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
                form_payload: dict[str, object] = dict(flask_request.form.items())
                try:
                    form_model = request_form.model_validate(form_payload)
                except ValidationError as exc:
                    return validation_error_to_problem(exc)
                kwargs["form"] = form_model
                for file_field in request_files or ():
                    file_obj: Optional[FileStorage] = flask_request.files.get(file_field)
                    kwargs[file_field] = file_obj
            result = cast(Callable[..., ResponseLike], view)(*args, **kwargs)
            return _serialize(result)

        setattr(wrapper, "__doc_meta__", doc)
        return cast(DocumentedView, wrapper)

    return decorator


def _serialize(result: ResponseLike) -> WrappedReturn:
    """Convert a view's return value into a Flask response."""
    if isinstance(result, BaseModel):
        return jsonify(result.model_dump(mode="json", by_alias=True, exclude_none=True))
    if isinstance(result, Response):
        return result
    # ``result`` is a 2-tuple: either (BaseModel, int) or (Response, int).
    body, status = result
    if isinstance(body, BaseModel):
        payload: Response = jsonify(
            body.model_dump(mode="json", by_alias=True, exclude_none=True)
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
