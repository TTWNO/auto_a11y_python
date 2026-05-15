"""Cross-cutting schema types shared by all /api/v1/* resources."""
from __future__ import annotations

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for every schema model.

    ``extra='forbid'`` makes unknown input fields a validation error, which is
    what we want for an API surface that promises specific shapes.
    """

    model_config = ConfigDict(extra='forbid', populate_by_name=True)


class Problem(StrictModel):
    """RFC 7807 Problem Details for HTTP APIs."""

    type: str = Field(default="about:blank")
    title: str
    status: int
    detail: Optional[str] = None
    instance: Optional[str] = None
    errors: Optional[list[dict[str, object]]] = None


class PaginationMeta(StrictModel):
    """Pagination block returned with every list envelope."""

    total: int
    page: int
    per_page: int
    total_pages: int


T = TypeVar("T", bound=BaseModel)


class ListEnvelope(StrictModel, Generic[T]):
    """Envelope for list responses.

    Every list endpoint returns this shape:

        {
          "data": [...resource objects...],
          "pagination": {...PaginationMeta...}
        }
    """

    data: list[T]
    pagination: PaginationMeta


class Empty(StrictModel):
    """Empty body — for 204 responses or endpoints that return ``{}``."""

    pass
