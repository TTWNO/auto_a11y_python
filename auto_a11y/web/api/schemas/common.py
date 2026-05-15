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


class LenientPutModel(BaseModel):
    """Base for PUT request bodies that accept (and silently ignore) extras.

    ``PUT`` semantics in REST is full-resource replacement: the canonical
    client workflow is ``GET`` → mutate → ``PUT`` the same body back. Any
    server-managed fields (``id``, ``created_at``, ``page_count``,
    ``project_id``, ``last_scraped``, ``last_tested``, ...) round-trip
    on the response side and reappear in the PUT body. With
    ``extra='forbid'`` (the StrictModel default) that round-trip would
    400 on every well-behaved client; ``extra='ignore'`` keeps the
    contract clean — the handler only acts on the editable fields it
    declares, and any extras are silently dropped at validation time.

    POST and PATCH stay on :class:`StrictModel` because their wire
    shapes are deliberately narrow: a malformed ``POST`` body should
    surface as a 400 rather than silently dropping fields the client
    expected to take effect.
    """

    model_config = ConfigDict(extra='ignore', populate_by_name=True)


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
