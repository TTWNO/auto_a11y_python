"""Module-level registry of documented /api/v1/* endpoints.

The @document decorator inserts an EndpointDoc here at module import time.
The spec builder reads it. Nothing else should touch the registry directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Optional

from pydantic import BaseModel

# Two security shorthand values used by every documented endpoint.
SecurityScheme = Literal["bearer+session", "bearer", "public"]


@dataclass(frozen=True)
class EndpointDoc:
    """Metadata for one (method, path) entry in the OpenAPI spec.

    Frozen so accidental mutation between decoration and spec generation
    cannot cause drift. The fields mirror what the OpenAPI builder needs to
    emit a single ``operationObject``.

    ``request_model`` is the JSON body schema (for application/json).
    ``request_form_model`` is the schema for multipart/form-data form fields.
    ``request_files`` is a tuple of file-field names for multipart uploads.
    At most one of ``request_model`` and ``request_form_model`` is non-None.
    """

    view: Callable[..., object]
    request_model: Optional[type[BaseModel]]
    request_form_model: Optional[type[BaseModel]]
    request_files: tuple[str, ...]
    responses: Mapping[int, type[BaseModel]]
    errors: list[int]
    tags: list[str]
    summary: str
    description: Optional[str]
    security: SecurityScheme


REGISTRY: dict[tuple[str, str], EndpointDoc] = {}


def register(method: str, rule: str, doc: EndpointDoc) -> None:
    """Insert an EndpointDoc for ``(method, rule)``.

    Idempotent for identical-by-identity EndpointDocs so register_documented_views()
    can re-walk an app without error. Raises ``RuntimeError`` only when two
    DISTINCT EndpointDoc objects collide on the same (method, rule) — a
    real copy-paste bug.
    """
    key = (method.upper(), rule)
    existing = REGISTRY.get(key)
    if existing is doc:
        return  # idempotent re-registration of the same EndpointDoc
    if existing is not None:
        method_upper = method.upper()
        raise RuntimeError(
            f"duplicate @document for {method_upper} {rule}; the registry already has a different EndpointDoc for this (method, rule). This is almost always a copy-paste bug."
        )
    REGISTRY[key] = doc


def registry_snapshot() -> dict[tuple[str, str], EndpointDoc]:
    """Return a shallow copy of the registry for read-only inspection."""
    return dict(REGISTRY)


def reset_registry_for_tests() -> None:
    """Clear the registry. Tests only — never call from production code."""
    REGISTRY.clear()
