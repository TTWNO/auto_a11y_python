"""build_spec(app) -> an OpenAPI 3.1 dict.

Walks app.url_map joined with the documented-endpoint registry. For each
documented (method, rule), emits an Operation Object. Aggregates every
referenced Pydantic model into components.schemas via model_json_schema.
"""
from __future__ import annotations

import re
from typing import cast

from flask import Flask
from pydantic import BaseModel

from auto_a11y.web.api.openapi.registry import EndpointDoc, registry_snapshot
from auto_a11y.web.api.schemas.common import Problem

# Map Flask converter prefixes to OpenAPI path-parameter schemas.
_CONVERTER_SCHEMAS: dict[str, dict[str, object]] = {
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "uuid": {"type": "string", "format": "uuid"},
    "path": {"type": "string"},
    "string": {"type": "string"},
    "any": {"type": "string"},
    "default": {"type": "string"},
}

_PARAM_RE = re.compile(r"<(?:(?P<conv>[a-z_]+):)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)>")


def build_spec(app: Flask) -> dict[str, object]:
    """Return an OpenAPI 3.1 spec dict for ``app``."""
    paths: dict[str, dict[str, object]] = {}
    schemas: dict[str, object] = {}
    snapshot = registry_snapshot()

    # Always include Problem in components.schemas.
    _merge_model_schema(schemas, Problem)

    for (method, rule), doc in snapshot.items():
        oapi_path, path_params = _translate_path(rule)
        op = _build_operation(doc, path_params, schemas)
        paths.setdefault(oapi_path, {})[method.lower()] = op

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Auto A11y REST API",
            "version": "1.0.0",
            "description": "Documented surface for /api/v1/*.",
        },
        "servers": [{"url": "/api/v1", "description": "Live"}],
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "Token",
                    "description": "API token issued via /auth/tokens. Prefix: a11y_",
                },
                "sessionAuth": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "session",
                    "description": "Flask-Login session cookie.",
                },
            },
        },
    }


def _translate_path(rule: str) -> tuple[str, list[dict[str, object]]]:
    """Convert Flask path syntax to OpenAPI {placeholder} syntax."""
    params: list[dict[str, object]] = []

    def _replace(match: re.Match[str]) -> str:
        conv = match.group("conv") or "default"
        name = match.group("name")
        schema = _CONVERTER_SCHEMAS.get(conv, {"type": "string"})
        params.append({
            "name": name,
            "in": "path",
            "required": True,
            "schema": schema,
        })
        return f"{{{name}}}"

    new_rule = _PARAM_RE.sub(_replace, rule)
    return new_rule, params


def _build_operation(
    doc: EndpointDoc,
    path_params: list[dict[str, object]],
    schemas: dict[str, object],
) -> dict[str, object]:
    """Build one OpenAPI Operation Object."""
    op: dict[str, object] = {
        "summary": doc.summary,
        "tags": list(doc.tags),
        "parameters": list(path_params),
    }
    if doc.description is not None:
        op["description"] = doc.description

    if doc.request_model is not None:
        _merge_model_schema(schemas, doc.request_model)
        op["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{doc.request_model.__name__}"},
                },
            },
        }

    responses: dict[str, dict[str, object]] = {}
    for status, model in doc.responses.items():
        _merge_model_schema(schemas, model)
        responses[str(status)] = {
            "description": _status_description(status),
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{model.__name__}"},
                },
            },
        }
    for status in doc.errors:
        responses.setdefault(str(status), {
            "description": _status_description(status),
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/Problem"},
                },
            },
        })
    op["responses"] = responses

    if doc.security == "bearer+session":
        op["security"] = [{"bearerAuth": []}, {"sessionAuth": []}]
    elif doc.security == "bearer":
        op["security"] = [{"bearerAuth": []}]
    elif doc.security == "public":
        op["security"] = []

    return op


def _merge_model_schema(schemas: dict[str, object], model: type[BaseModel]) -> None:
    """Insert ``model`` and all its $defs into ``schemas``."""
    if model.__name__ in schemas:
        return
    js: dict[str, object] = model.model_json_schema(ref_template="#/components/schemas/{model}")
    defs_obj = js.pop("$defs", {})
    schemas[model.__name__] = js
    if isinstance(defs_obj, dict):
        defs_dict = cast(dict[str, object], defs_obj)
        for name, defn in defs_dict.items():
            schemas.setdefault(name, defn)


def _status_description(status: int) -> str:
    table = {
        200: "OK", 201: "Created", 202: "Accepted", 204: "No Content",
        400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
        404: "Not Found", 409: "Conflict", 410: "Gone",
        422: "Unprocessable Entity", 500: "Internal Server Error",
        503: "Service Unavailable",
    }
    return table.get(status, "")
