"""Pydantic schemas for the /api/v1/issues documentation-status endpoints.

The IssueCatalog itself lives in Python
(``auto_a11y/reporting/issue_catalog.py``); only the per-code
``production_ready`` flag persists in Mongo. These schemas back two
endpoints from §5.1 of the roadmap:

- ``GET /api/v1/issues/documentation-stats`` — counts + code lists,
  available to any authenticated user.
- ``PATCH /api/v1/issues/<code>`` — toggle the ``production_ready``
  flag. Superadmin only (the legacy endpoint had no auth at all).
"""
from __future__ import annotations

from pydantic import StrictBool

from auto_a11y.web.api.schemas.common import StrictModel


class IssueDocumentationStatsCounts(StrictModel):
    """Inner ``stats`` block on the documentation-stats response."""

    total: int
    production_ready: int
    pending: int
    percentage_ready: float


class IssueDocumentationStatsOut(StrictModel):
    """Response body for ``GET /api/v1/issues/documentation-stats``."""

    stats: IssueDocumentationStatsCounts
    production_ready_codes: list[str]
    pending_codes: list[str]


class IssueDocumentationPatch(StrictModel):
    """Request body for ``PATCH /api/v1/issues/<code>``.

    Currently the only mutable flag is ``production_ready``; declared as
    required because the endpoint has no other content. ``StrictBool``
    rejects coerced inputs (``"yes"`` / ``1``) — the legacy endpoint
    silently truthy-checked anything, which is a foot-gun the REST
    surface intentionally closes.
    """

    production_ready: StrictBool


class IssueDocumentationOut(StrictModel):
    """Response body for ``PATCH /api/v1/issues/<code>``."""

    issue_code: str
    production_ready: bool
