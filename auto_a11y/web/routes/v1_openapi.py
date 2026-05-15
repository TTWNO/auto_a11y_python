"""Dynamic OpenAPI 3.1 spec endpoints for /api/v1/*.

These views rebuild the spec on every request from the live registry, so
the response can never be stale. They attach to the existing api_bp at
module load time (no new blueprint).
"""
from __future__ import annotations

import yaml
from flask import Response, current_app, jsonify

from auto_a11y.web.api.openapi.builder import build_spec
from auto_a11y.web.routes.api import api_bp


def openapi_json() -> Response:
    """GET /api/v1/openapi.json — JSON representation."""
    return jsonify(build_spec(current_app))


def openapi_yaml() -> Response:
    """GET /api/v1/openapi.yaml — YAML representation, deterministic ordering."""
    spec = build_spec(current_app)
    body = yaml.safe_dump(spec, sort_keys=True, default_flow_style=False, allow_unicode=True)
    return Response(body, mimetype="application/yaml")


api_bp.add_url_rule("/openapi.json", view_func=openapi_json, methods=["GET"])
api_bp.add_url_rule("/openapi.yaml", view_func=openapi_yaml, methods=["GET"])
