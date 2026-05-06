"""Tests for `auto_a11y.web.api.errors` — RFC 7807 Problem Details.

Coverage focus:

- :class:`ProblemDetails` serializes the documented JSON shape.
- :class:`FieldError` serializes the documented array shape.
- Each :class:`ApiError` subclass produces the documented status code
  and ``type`` slug.
- ``register_api_error_handlers`` causes uncaught :class:`ApiError`
  exceptions to be returned as ``application/problem+json`` responses.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from flask import Flask

from auto_a11y.web.api.errors import (
    ApiError,
    ConflictError,
    ERROR_TYPE_BASE,
    FieldError,
    ForbiddenError,
    NotFoundError,
    ProblemDetails,
    UnauthorizedError,
    ValidationError,
    problem_response,
)


def test_field_error_to_dict_returns_documented_keys() -> None:
    err = FieldError(field="name", code="too_long", message="must be ≤ 200 chars")
    assert err.to_dict() == {
        "field": "name",
        "code": "too_long",
        "message": "must be ≤ 200 chars",
    }


def test_problem_details_includes_only_set_fields() -> None:
    minimal = ProblemDetails(
        type="https://example/errors/x",
        title="X",
        status=400,
    )
    assert minimal.to_dict() == {
        "type": "https://example/errors/x",
        "title": "X",
        "status": 400,
    }


def test_problem_details_includes_all_optional_fields() -> None:
    full = ProblemDetails(
        type="https://example/errors/x",
        title="X",
        status=400,
        detail="something specific",
        instance="/api/v1/foo",
        errors=(
            FieldError(field="a", code="required", message="required"),
        ),
    )
    body = full.to_dict()
    assert body["detail"] == "something specific"
    assert body["instance"] == "/api/v1/foo"
    assert body["errors"] == [{"field": "a", "code": "required", "message": "required"}]


@pytest.mark.parametrize(
    ("error_cls", "expected_status", "expected_slug"),
    [
        (ValidationError, HTTPStatus.BAD_REQUEST, "validation"),
        (UnauthorizedError, HTTPStatus.UNAUTHORIZED, "unauthorized"),
        (ForbiddenError, HTTPStatus.FORBIDDEN, "forbidden"),
        (NotFoundError, HTTPStatus.NOT_FOUND, "not-found"),
        (ConflictError, HTTPStatus.CONFLICT, "conflict"),
    ],
)
def test_api_error_subclasses_have_documented_status_and_type(
    error_cls: type[ApiError],
    expected_status: HTTPStatus,
    expected_slug: str,
) -> None:
    err = error_cls("a detail")
    problem = err.problem()
    assert problem.status == expected_status.value
    assert problem.type == f"{ERROR_TYPE_BASE}/{expected_slug}"
    assert problem.detail == "a detail"


def test_validation_error_carries_field_errors() -> None:
    err = ValidationError(
        "validation failed",
        errors=(
            FieldError(field="name", code="required", message="required"),
            FieldError(field="age", code="invalid_type", message="must be int"),
        ),
    )
    problem = err.problem()
    assert len(problem.errors) == 2
    assert problem.errors[0].field == "name"


def test_problem_response_sets_problem_json_content_type(app: Flask) -> None:
    err = NotFoundError("nope")
    with app.test_request_context():
        response, status = problem_response(err.problem())
    assert status == HTTPStatus.NOT_FOUND
    assert response.headers["Content-Type"] == "application/problem+json"


def test_register_api_error_handlers_translates_uncaught_apierror(app: Flask) -> None:
    """Handlers without ``@api_endpoint`` still get translated by the app-level handler."""

    def raises() -> Any:
        raise NotFoundError("the thing is gone")

    app.add_url_rule("/raises", view_func=raises)

    response = app.test_client().get("/raises")
    assert response.status_code == 404
    assert response.headers["Content-Type"] == "application/problem+json"
    body = response.get_json()
    assert body["type"] == f"{ERROR_TYPE_BASE}/not-found"
    assert body["status"] == 404
    assert body["detail"] == "the thing is gone"
