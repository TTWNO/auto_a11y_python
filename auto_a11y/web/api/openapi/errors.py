"""Translation from pydantic.ValidationError to RFC 7807 Problem.

This is the single place where Pydantic validation failures become HTTP
responses. Every @document-wrapped handler that takes a request model
funnels its ValidationError through here.

The wire shape matches :class:`auto_a11y.web.api.errors.FieldError` so
both error paths (``@api_endpoint`` raising :class:`ValidationError`
directly, and ``@document`` catching Pydantic) project to the same
``{field, code, message}`` envelope. Tests assert on ``e["field"]``;
keeping the two paths divergent (Pydantic-native ``loc/msg/type`` vs.
canonical ``field/code/message``) would break every test that
exercises a documented endpoint.
"""
from __future__ import annotations

from flask import Response
from pydantic import ValidationError

from auto_a11y.web.api.errors import (
    FieldError,
    ProblemDetails,
    problem_response,
)


def _loc_to_field_path(loc: tuple[int | str, ...]) -> str:
    """Render Pydantic's ``loc`` tuple as a canonical dot+bracket path.

    Pydantic emits ``loc`` as a tuple of strings (field names) and ints
    (list / tuple indices). The canonical projection matches what hand-
    built ``FieldError`` records use: dots between named segments,
    bracket notation around integer indices (e.g.
    ``"document_links[0]"``, ``"config.touchpoints.headings.tests[2]"``).
    An empty ``loc`` collapses to the literal ``"<root>"`` sentinel —
    same sentinel handlers use when they raise
    :class:`ValidationError` for top-level body shape mismatches.
    """
    if not loc:
        return "<root>"
    rendered = ""
    for entry in loc:
        if isinstance(entry, int):
            rendered += f"[{entry}]"
        else:
            rendered = f"{rendered}.{entry}" if rendered else entry
    return rendered


def validation_error_to_problem(exc: ValidationError) -> tuple[Response, int]:
    """Return a (400 Problem, 400) tuple suitable for direct return.

    The Problem body conforms to RFC 7807 and uses the same shape every
    other ``@api_endpoint`` route emits: ``{type, title, status, detail,
    errors:[{field, code, message}, ...]}``. Content-Type is set to
    ``application/problem+json``.
    """
    field_errors: list[FieldError] = []
    for err in exc.errors():
        field_errors.append(
            FieldError(
                field=_loc_to_field_path(err.get("loc", ())),
                code=str(err.get("type", "")),
                message=str(err.get("msg", "")),
            )
        )

    problem = ProblemDetails(
        type="https://auto-a11y/errors/validation",
        title="Validation failed",
        status=400,
        detail="request body failed validation",
        errors=tuple(field_errors),
    )
    return problem_response(problem)
