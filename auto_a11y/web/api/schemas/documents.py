"""Pydantic schemas for the /api/v1 document-references endpoint (§5.2).

A DocumentReference records a non-HTML resource (PDF, Word doc, etc.)
linked from a page during crawling. ``GET /websites/<id>/documents``
returns the cursor-paginated list with an optional ``?is_internal=``
filter; no other endpoints in this group ship in PR #51.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


class DocumentReferenceOut(StrictModel):
    """Response body for a single document-reference row.

    Mirrors :func:`_serialize_document_reference` field-for-field:
    timestamps emitted as ISO 8601 strings, the Mongo ``_id`` exposed
    via the stringified ``id`` property.
    """

    id: Optional[str] = None
    website_id: str
    document_url: str
    referring_page_url: str
    mime_type: str
    is_internal: bool
    link_text: Optional[str] = None
    file_extension: Optional[str] = None
    language: Optional[str] = None
    language_confidence: Optional[float] = None
    discovered_at: Optional[str] = None
    last_seen: Optional[str] = None
    seen_count: int
    via_redirect: bool


class DocumentReferenceListOut(StrictModel):
    """Response body for ``GET /api/v1/websites/<id>/documents``.

    Cursor-paginated; same ``{items, next_cursor}`` shape as the other
    v1 list endpoints.
    """

    items: list[DocumentReferenceOut]
    next_cursor: Optional[str] = None
