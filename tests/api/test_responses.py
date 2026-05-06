"""Tests for `auto_a11y.web.api.responses` — bare-body response helpers."""
from __future__ import annotations

from http import HTTPStatus

from flask import Flask

from auto_a11y.web.api.responses import created, json_response, no_content


def test_json_response_default_status_is_200() -> None:
    app = Flask(__name__)
    with app.test_request_context():
        response, status = json_response({"x": 1})
    assert status == HTTPStatus.OK
    assert response.get_json() == {"x": 1}


def test_json_response_custom_status() -> None:
    app = Flask(__name__)
    with app.test_request_context():
        _, status = json_response({"x": 1}, status=HTTPStatus.ACCEPTED)
    assert status == HTTPStatus.ACCEPTED


def test_created_sets_location_header() -> None:
    app = Flask(__name__)
    with app.test_request_context():
        response, status = created({"id": "abc"}, location="/api/v1/things/abc")
    assert status == HTTPStatus.CREATED
    assert response.headers["Location"] == "/api/v1/things/abc"
    assert response.get_json() == {"id": "abc"}


def test_no_content_returns_empty_204() -> None:
    response, status = no_content()
    assert status == HTTPStatus.NO_CONTENT
    assert response.status_code == HTTPStatus.NO_CONTENT
    assert response.data == b""
