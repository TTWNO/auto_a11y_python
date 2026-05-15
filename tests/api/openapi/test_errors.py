"""Tests for the validation-error-to-Problem mapper."""
from __future__ import annotations

from flask import Flask
from pydantic import BaseModel, ValidationError

from auto_a11y.web.api.openapi.errors import validation_error_to_problem


class _Body(BaseModel):
    name: str
    count: int


def _capture_error() -> ValidationError:
    try:
        _Body.model_validate({"count": "not-a-number"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ValidationError")


def test_mapper_returns_400() -> None:
    app = Flask(__name__)
    with app.app_context():
        _body, status = validation_error_to_problem(_capture_error())
    assert status == 400


def test_mapper_body_has_problem_shape() -> None:
    app = Flask(__name__)
    with app.app_context():
        body, _status = validation_error_to_problem(_capture_error())
    payload = body.get_json()
    assert payload["type"] == "about:blank"
    assert payload["title"] == "Bad Request"
    assert payload["status"] == 400
    assert payload["detail"] == "request body failed validation"
    assert isinstance(payload["errors"], list)
    # Two errors: missing 'name', invalid 'count'.
    locs = sorted(["/".join(str(p) for p in e["loc"]) for e in payload["errors"]])
    assert locs == ["count", "name"]


def test_mapper_content_type_is_problem_json() -> None:
    app = Flask(__name__)
    with app.app_context():
        body, _status = validation_error_to_problem(_capture_error())
    assert body.mimetype == "application/problem+json"
