"""
Audit regression tests for auto_a11y.drupal.issue_importer.IssueImporter.

Covers two bugs found in a code audit:

* Bug A: Drupal JSON:API returns ``created`` / ``changed`` as ISO-8601 strings,
  but ``convert_to_issue_model`` parsed them with ``datetime.fromtimestamp``
  (which requires a numeric POSIX timestamp) -> ``TypeError`` for every real node.
* Bug B: the pagination loop in ``fetch_issues_for_audit`` only terminated on an
  empty/short page, so a server that ignores ``page[offset]`` (and keeps returning
  a full page) would loop forever.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from auto_a11y.drupal.issue_importer import IssueImporter
from auto_a11y.models import ImpactLevel


def _minimal_drupal_issue(
    created: str | None,
    changed: str | None,
) -> dict[str, Any]:
    """Build the minimal parsed-issue dict that convert_to_issue_model expects."""
    return {
        'uuid': 'abcd-1234',
        'title': 'Missing alt text',
        'description': 'An image has no alternative text.',
        'impact': 'high',
        'issue_type': None,
        'location_on_page': None,
        'wcag_criteria': ['1.1.1'],
        'xpath': '/html/body/img',
        'url': 'https://example.com/page',
        'video_timecode': None,
        'drupal_issue_id': 42,
        'drupal_nid': 99,
        'created_timestamp': created,
        'changed_timestamp': changed,
    }


# --------------------------------------------------------------------------- #
# Bug A: ISO-8601 timestamp parsing
# --------------------------------------------------------------------------- #


def test_convert_parses_iso8601_timestamps() -> None:
    importer = IssueImporter(client=object())
    drupal_issue = _minimal_drupal_issue(
        created='2024-01-15T10:30:00+00:00',
        changed='2024-02-20T08:15:30+00:00',
    )

    issue = importer.convert_to_issue_model(drupal_issue, project_id='proj-1')

    assert issue.created_at == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    assert issue.updated_at == datetime(2024, 2, 20, 8, 15, 30, tzinfo=timezone.utc)
    assert issue.impact == ImpactLevel.HIGH


def test_convert_parses_iso8601_with_trailing_z() -> None:
    importer = IssueImporter(client=object())
    drupal_issue = _minimal_drupal_issue(
        created='2024-01-15T10:30:00Z',
        changed='2024-02-20T08:15:30Z',
    )

    issue = importer.convert_to_issue_model(drupal_issue, project_id='proj-1')

    assert issue.created_at == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    assert issue.updated_at == datetime(2024, 2, 20, 8, 15, 30, tzinfo=timezone.utc)


def test_convert_handles_none_timestamps() -> None:
    importer = IssueImporter(client=object())
    drupal_issue = _minimal_drupal_issue(created=None, changed=None)

    before = datetime.now()
    issue = importer.convert_to_issue_model(drupal_issue, project_id='proj-1')
    after = datetime.now()

    # Falls back to "now" without raising.
    assert before <= issue.created_at <= after
    assert before <= issue.updated_at <= after


# --------------------------------------------------------------------------- #
# Bug B: bounded pagination loop
# --------------------------------------------------------------------------- #


class _OffsetIgnoringClient:
    """Mock client that always returns a full page, ignoring page[offset]."""

    def __init__(self, page_limit: int) -> None:
        self.page_limit = page_limit
        self.call_count = 0

    def get(self, _endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.call_count += 1
        # Always return a *full* page of distinct-looking nodes => never short,
        # never empty. Without a safety bound this loops forever.
        data = [
            {'id': f'node-{i}', 'attributes': {'title': f'Issue {i}'}, 'relationships': {}}
            for i in range(self.page_limit)
        ]
        return {'data': data, 'included': []}


def test_pagination_terminates_when_server_ignores_offset() -> None:
    importer = IssueImporter(client=object())
    page_limit = 50
    client = _OffsetIgnoringClient(page_limit=page_limit)
    importer.client = client

    # Should return rather than hang. The hard cap bounds the number of fetches.
    issues = importer.fetch_issues_for_audit('audit-uuid')

    assert client.call_count <= importer.MAX_PAGES
    assert client.call_count > 0
    # Bounded accumulation, not unbounded.
    assert len(issues) <= importer.MAX_PAGES * page_limit


def test_pagination_normal_short_page_terminates() -> None:
    importer = IssueImporter(client=object())

    class _ShortPageClient:
        def __init__(self) -> None:
            self.call_count = 0

        def get(self, _endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            self.call_count += 1
            # One short page -> normal termination after a single fetch.
            data = [
                {'id': 'node-1', 'attributes': {'title': 'Only issue'}, 'relationships': {}}
            ]
            return {'data': data, 'included': []}

    client = _ShortPageClient()
    importer.client = client

    issues = importer.fetch_issues_for_audit('audit-uuid')

    assert client.call_count == 1
    assert len(issues) == 1
