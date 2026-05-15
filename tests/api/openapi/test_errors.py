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
    # The mapper emits the same canonical envelope every other
    # ``@api_endpoint`` route uses — see
    # :class:`auto_a11y.web.api.errors.FieldError` for the per-field shape
    # (``{field, code, message}``). Keeping the two paths divergent would
    # break every test that exercises a documented endpoint's validation
    # branch.
    assert payload["type"] == "https://auto-a11y/errors/validation"
    assert payload["title"] == "Validation failed"
    assert payload["status"] == 400
    assert payload["detail"] == "request body failed validation"
    assert isinstance(payload["errors"], list)
    # Two errors: missing 'name', invalid 'count'.
    fields = sorted(e["field"] for e in payload["errors"])
    assert fields == ["count", "name"]
    # Every field-error carries the canonical triple — verify the keys
    # are present so a future shape regression fails this test loudly.
    for entry in payload["errors"]:
        assert set(entry.keys()) == {"field", "code", "message"}


def test_mapper_content_type_is_problem_json() -> None:
    app = Flask(__name__)
    with app.app_context():
        body, _status = validation_error_to_problem(_capture_error())
    assert body.mimetype == "application/problem+json"
