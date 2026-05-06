"""Tests for `auto_a11y.web.api.decorators` — the ``@api_endpoint`` wrapper.

Coverage focus:

- Successful return values pass through unchanged.
- :class:`ApiError` raised inside the handler becomes a Problem Details
  response with the documented status, type, and content type.
- ``problem.instance`` is filled in with ``request.path`` even when the
  handler did not set it.
- Unexpected exceptions become a generic 500 Problem Details response —
  not a Flask debug page or a 500 with a stack trace in the body.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from flask import Flask, jsonify

from auto_a11y.web.api import api_endpoint
from auto_a11y.web.api.errors import ERROR_TYPE_BASE, NotFoundError, ValidationError


def test_decorator_passes_through_successful_response(app: Flask) -> None:
    @api_endpoint
    def ok() -> Any:
        return jsonify({"hello": "world"}), 200

    app.add_url_rule("/ok", view_func=ok)

    response = app.test_client().get("/ok")
    assert response.status_code == 200
    assert response.get_json() == {"hello": "world"}


def test_decorator_translates_apierror_to_problem_details(app: Flask) -> None:
    @api_endpoint
    def missing() -> Any:
        raise NotFoundError("nope")

    app.add_url_rule("/missing", view_func=missing)

    response = app.test_client().get("/missing")
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.headers["Content-Type"] == "application/problem+json"
    body = response.get_json()
    assert body["type"] == f"{ERROR_TYPE_BASE}/not-found"
    assert body["status"] == HTTPStatus.NOT_FOUND.value
    assert body["detail"] == "nope"


def test_decorator_fills_problem_instance_with_request_path(app: Flask) -> None:
    @api_endpoint
    def missing(thing_id: str) -> Any:
        raise NotFoundError(f"thing {thing_id} not found")

    app.add_url_rule("/missing/<thing_id>", view_func=missing)

    response = app.test_client().get("/missing/abc123")
    body = response.get_json()
    assert body["instance"] == "/missing/abc123"


def test_decorator_preserves_handler_supplied_instance(app: Flask) -> None:
    @api_endpoint
    def explicit() -> Any:
        raise NotFoundError("nope", instance="custom-instance")

    app.add_url_rule("/explicit", view_func=explicit)

    response = app.test_client().get("/explicit")
    body = response.get_json()
    assert body["instance"] == "custom-instance"


def test_decorator_serializes_field_errors_in_problem_body(app: Flask) -> None:
    from auto_a11y.web.api.errors import FieldError

    @api_endpoint
    def bad() -> Any:
        raise ValidationError(
            "bad input",
            errors=(
                FieldError(field="name", code="required", message="required"),
            ),
        )

    app.add_url_rule("/bad", view_func=bad)

    response = app.test_client().get("/bad")
    body = response.get_json()
    assert body["status"] == HTTPStatus.BAD_REQUEST.value
    assert body["errors"] == [
        {"field": "name", "code": "required", "message": "required"},
    ]


def test_decorator_swallows_unexpected_exception_into_500(app: Flask) -> None:
    @api_endpoint
    def boom() -> Any:
        raise RuntimeError("internal detail that must not leak")

    app.add_url_rule("/boom", view_func=boom)

    response = app.test_client().get("/boom")
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.get_json()
    assert body["type"] == f"{ERROR_TYPE_BASE}/internal"
    assert body["status"] == HTTPStatus.INTERNAL_SERVER_ERROR.value
    # The internal exception message must not appear in the response body.
    assert "internal detail that must not leak" not in str(body)
