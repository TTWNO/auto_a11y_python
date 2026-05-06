"""Shared fixtures for the REST API test suite.

The scaffolding tests in this directory exercise the pure helpers in
``auto_a11y.web.api`` — Problem Details, cursor pagination, the
``@api_endpoint`` decorator. A minimal Flask app is sufficient; the full
``create_app`` factory is not needed and would force a live MongoDB to
be running for unrelated reasons.

Endpoint tests added later (per #27 commit B) will introduce a
larger-scope ``app`` fixture wired to a real Mongo instance — see
``docs/REST_API_ROADMAP.md`` §7.
"""
from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

from auto_a11y.web.api import register_api_error_handlers


@pytest.fixture
def app() -> Flask:
    """Bare Flask app with API error handlers wired up.

    No blueprints are registered by default; individual tests attach the
    handlers they want to exercise. This keeps tests fully isolated from
    the production blueprint surface.
    """
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    register_api_error_handlers(flask_app)
    return flask_app


@pytest.fixture
def client(app: Flask) -> Any:
    """Flask test client paired with the ``app`` fixture above."""
    return app.test_client()
