"""RFC 7807 Problem Details for the REST API.

Why this module exists: REST clients need a consistent, machine-readable
error envelope independent of the success-body wrappers used by the
existing ``success: true`` endpoints. The scaffolding establishes the
new shape; individual handlers raise :class:`ApiError` (or a subclass)
and the ``@api_endpoint`` decorator turns the exception into a 4xx/5xx
response with a ``Content-Type: application/problem+json`` body.

Error messages here are technical and developer-facing — they are NOT
user-visible UI copy and intentionally do not pass through Fluent. The
frontend is responsible for mapping the ``type`` URI to a localized
human message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final

from flask import Response, jsonify

if TYPE_CHECKING:
    from flask import Blueprint, Flask


# Base URI for ``type`` slugs. Treated as opaque identifiers; clients SHOULD
# match on the URI string, not dereference it. Must remain stable across
# versions — adding new slugs is fine; renaming an existing one is a breaking
# change.
ERROR_TYPE_BASE: Final[str] = "https://auto-a11y/errors"


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldError:
    """A single field-level validation problem.

    Attributes:
        field: Dot-path of the offending field (e.g. ``"config.wcag_level"``).
        code: Stable machine code (e.g. ``"too_long"``, ``"required"``).
        message: Developer-readable description.
    """

    field: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True, kw_only=True)
class ProblemDetails:
    """An RFC 7807 Problem Details payload.

    The ``status`` field MUST match the HTTP status code carried on the
    response. The decorator preserves that invariant.
    """

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    errors: tuple[FieldError, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
        }
        if self.detail is not None:
            body["detail"] = self.detail
        if self.instance is not None:
            body["instance"] = self.instance
        if self.errors:
            body["errors"] = [e.to_dict() for e in self.errors]
        return body


class ApiError(Exception):
    """Base class for errors raised inside REST handlers.

    Subclass for each error category. The ``@api_endpoint`` decorator
    catches any :class:`ApiError` and turns ``self.problem`` into the
    response body.
    """

    type_slug: str = "internal"
    status: int = HTTPStatus.INTERNAL_SERVER_ERROR
    title: str = "Internal server error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: tuple[FieldError, ...] = (),
        instance: str | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.errors = errors
        self.instance = instance

    def problem(self) -> ProblemDetails:
        return ProblemDetails(
            type=f"{ERROR_TYPE_BASE}/{self.type_slug}",
            title=self.title,
            status=self.status,
            detail=self.detail,
            instance=self.instance,
            errors=self.errors,
        )


class ValidationError(ApiError):
    """The request body or parameters failed validation."""

    type_slug = "validation"
    status = HTTPStatus.BAD_REQUEST
    title = "Validation failed"


class UnauthorizedError(ApiError):
    """The caller is not authenticated."""

    type_slug = "unauthorized"
    status = HTTPStatus.UNAUTHORIZED
    title = "Authentication required"


class ForbiddenError(ApiError):
    """The caller is authenticated but lacks permission for this resource."""

    type_slug = "forbidden"
    status = HTTPStatus.FORBIDDEN
    title = "Forbidden"


class NotFoundError(ApiError):
    """The requested resource does not exist (or is hidden from the caller)."""

    type_slug = "not-found"
    status = HTTPStatus.NOT_FOUND
    title = "Resource not found"


class ConflictError(ApiError):
    """The request conflicts with the current state of the resource.

    Used for: idempotency-key collisions with mismatched bodies, attempts
    to mutate a resource locked by another job, and similar cases.
    """

    type_slug = "conflict"
    status = HTTPStatus.CONFLICT
    title = "Conflict"


def problem_response(problem: ProblemDetails) -> tuple[Response, int]:
    """Serialize a :class:`ProblemDetails` to a Flask response."""
    response = jsonify(problem.to_dict())
    response.headers["Content-Type"] = "application/problem+json"
    return response, problem.status


def register_api_error_handlers(bp: Blueprint | Flask) -> None:
    """Register ``ApiError`` handlers on a blueprint or app.

    Call once at startup against the ``/api/v1`` blueprint. Endpoints that
    use ``@api_endpoint`` already translate exceptions inline; this handler
    catches the case where an :class:`ApiError` escapes a handler that
    forgot the decorator.
    """

    def _handle(error: ApiError) -> tuple[Response, int]:
        return problem_response(error.problem())

    bp.register_error_handler(ApiError, _handle)
