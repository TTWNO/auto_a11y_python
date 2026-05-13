"""REST API scaffolding shared by all `/api/v1` endpoints.

See `docs/REST_API_ROADMAP.md` for the design that this package implements.

Public surface:

- :class:`ApiError` and its subclasses — raise these from handlers; the
  ``@api_endpoint`` decorator translates them into RFC 7807 Problem
  Details responses.
- :func:`json_response`, :func:`created`, :func:`no_content` — builders for
  the bare-body 2xx response shape.
- :func:`paginate`, :class:`Cursor` — cursor pagination helpers.
- :class:`IdempotencyStore` — Mongo-backed idempotency key store.
- :func:`api_endpoint` — decorator that wires error translation onto a
  Flask handler.
"""
from __future__ import annotations

from auto_a11y.web.api.auth import (
    require_authenticated,
    require_global_permission,
    require_project_role,
    require_superadmin,
)
from auto_a11y.web.api.decorators import api_endpoint
from auto_a11y.web.api.deprecation import (
    apply_deprecation_headers,
    deprecated,
)
from auto_a11y.web.api.errors import (
    ApiError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ProblemDetails,
    UnauthorizedError,
    ValidationError,
    register_api_error_handlers,
)
from auto_a11y.web.api.idempotency import IdempotencyStore
from auto_a11y.web.api.pagination import Cursor, Page, paginate
from auto_a11y.web.api.responses import created, json_response, no_content

__all__ = [
    "ApiError",
    "ConflictError",
    "Cursor",
    "ForbiddenError",
    "IdempotencyStore",
    "NotFoundError",
    "Page",
    "ProblemDetails",
    "UnauthorizedError",
    "ValidationError",
    "api_endpoint",
    "apply_deprecation_headers",
    "created",
    "deprecated",
    "json_response",
    "no_content",
    "paginate",
    "register_api_error_handlers",
    "require_authenticated",
    "require_global_permission",
    "require_project_role",
    "require_superadmin",
]
