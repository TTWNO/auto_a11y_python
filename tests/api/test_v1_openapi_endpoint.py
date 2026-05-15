"""Tests for /api/v1/openapi.{json,yaml} dynamic endpoints."""
from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

from auto_a11y.web.api import register_api_error_handlers


@pytest.fixture
def app() -> Flask:
    """Flask app with api_bp + v1_openapi routes registered."""
    from auto_a11y.web.routes.api import api_bp
    from auto_a11y.web.routes import v1_openapi  # registers /openapi.{json,yaml} on api_bp

    # Silence "imported but unused" — v1_openapi is imported for side effects.
    _ = (v1_openapi.openapi_json, v1_openapi.openapi_yaml)

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    register_api_error_handlers(flask_app)
    flask_app.register_blueprint(api_bp, url_prefix="/api/v1")
    return flask_app


@pytest.fixture
def client(app: Flask) -> Any:
    return app.test_client()


def test_openapi_json_returns_200_and_3_1_version(client: Any) -> None:
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    body = r.get_json()
    assert body["openapi"] == "3.1.0"


def test_openapi_yaml_returns_200_and_yaml_mimetype(client: Any) -> None:
    r = client.get("/api/v1/openapi.yaml")
    assert r.status_code == 200
    assert r.mimetype in {"application/yaml", "application/x-yaml", "text/yaml"}
    assert b"openapi: 3.1.0" in r.data


def test_openapi_endpoint_is_public(client: Any) -> None:
    # No Authorization header, no session cookie.
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
