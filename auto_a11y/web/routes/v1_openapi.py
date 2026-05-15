"""Compatibility shim — the dynamic OpenAPI 3.1 spec endpoints now live
directly on ``api_bp`` inside :mod:`auto_a11y.web.routes.api` (search for
"Dynamic OpenAPI 3.1 spec endpoints"). Co-locating the route definitions
eliminates a pytest-only import-order trap: a prior test could register
``api_bp`` against its test app and freeze the blueprint before this
module's module-level ``add_url_rule`` calls ran.

This file re-exports the two view functions so existing callers
(``auto_a11y/web/app.py``, ``scripts/generate_openapi.py``, the contract
and endpoint tests) keep working without churn. Importing the module no
longer has side effects.
"""
from __future__ import annotations

from auto_a11y.web.routes.api import openapi_json, openapi_yaml

__all__ = ["openapi_json", "openapi_yaml"]
