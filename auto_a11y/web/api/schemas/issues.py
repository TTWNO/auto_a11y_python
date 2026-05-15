"""Pydantic schemas for the /api/v1/issues endpoints (issue #51).

The issue-catalog REST surface exposes the documentation status (the
production_ready flag) for each canonical issue code. The catalog
content itself (description, WCAG ids, how-to-fix, ...) is read-only
and lives in ``auto_a11y/reporting/issue_catalog.py``; the database
only persists the per-code production-readiness flag and audit metadata.

Successor for the legacy:
- ``GET  /projects/api/test-details/<test_id>`` →
  ``GET /api/v1/issues/<code>``
- ``POST /projects/api/test-details/<test_id>/production-ready`` →
  ``PATCH /api/v1/issues/<code>`` with body ``{production_ready: bool}``

Wire-shape preservation
-----------------------
The GET response mirrors the legacy ``api_test_details`` payload's
``test`` envelope contents but unwraps it — clients receive the issue
fields directly at the top level rather than under ``{success, test}``.
The PATCH endpoint is partial: only ``production_ready`` is mutable
from this surface (the catalog content is build-time data).
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


class IssueMessageTemplateOut(StrictModel):
    """Per-issue user-facing copy block.

    Mirrors the ``message_template`` dict the legacy handler optionally
    attached when the enhanced issue description was available.
    """

    title: str
    what: str
    why: str
    remediation: str


class IssueOut(StrictModel):
    """Response body for ``GET /api/v1/issues/<code>``.

    Combines the static catalog entry with the per-code database
    documentation status (the ``production_ready`` flag). ``message_template``
    is optional — populated only when the enhanced description table
    has a matching entry.

    Fields without a translation in the catalog default to empty
    strings rather than ``None`` so clients can render them
    unconditionally; the legacy handler did the same.
    """

    id: str
    type: str
    impact: str
    wcag: list[str]
    wcag_full: str
    category: str
    description: str
    why_it_matters: str
    who_it_affects: str
    how_to_fix: str
    production_ready: bool
    message_template: Optional[IssueMessageTemplateOut] = None


class IssuePatch(StrictModel):
    """Request body for ``PATCH /api/v1/issues/<code>``.

    Only ``production_ready`` is patchable from the REST surface — the
    rest of the issue catalog is static build-time data. The body is
    *partial* (Pydantic's ``model_fields_set`` is the signal) so future
    fields can be added without forcing every existing client to send
    them.
    """

    production_ready: Optional[bool] = None


__all__ = [
    "IssueMessageTemplateOut",
    "IssueOut",
    "IssuePatch",
]
