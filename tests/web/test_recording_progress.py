"""Tests for the progress page, cancel endpoint, and cost panel.

Phase 8 of the audioA11y integration. Covers:

  - GET /recordings/<id> when ``status == 'processing'`` renders the
    progress card partial **and** a meta-refresh tag.
  - GET /recordings/<id> when ``status == 'complete'`` renders the cost
    panel and the issue rendering, but **not** a meta-refresh.
  - GET /recordings/<id> when ``status == 'failed'`` renders the
    ``error_message`` in a medium-severity alert.
  - POST /recordings/<id>/cancel when ``status == 'processing'`` flips
    the recording to ``cancelling`` and 302s back to the detail page.
  - POST /recordings/<id>/cancel when ``status == 'complete'`` is a
    safe no-op (302 redirect; status unchanged).

The view route reaches into ``ManualAccessibilityScorer`` and the WCAG
parser. We patch those to keep the test surface narrow to the Phase-8
branching markup.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

from bson import ObjectId
from flask.testing import FlaskClient


def _make_recording(
    *,
    status: str,
    progress: dict[str, object] | None = None,
    actual_cost_usd: float | None = None,
    cost_breakdown: dict[str, object] | None = None,
    error_message: str | None = None,
    estimated_cost_usd: float | None = None,
) -> MagicMock:
    """Build a Recording-shaped MagicMock for the detail/cancel routes."""
    rec = MagicMock()
    rec.mongo_id = ObjectId()
    rec.id = str(rec.mongo_id)
    rec.recording_id = 'REC-PROGRESS-TEST'
    rec.title = 'Phase 8 progress page test recording'
    rec.status = status
    rec.progress = progress
    rec.actual_cost_usd = actual_cost_usd
    rec.cost_breakdown = cost_breakdown
    rec.estimated_cost_usd = estimated_cost_usd
    rec.error_message = error_message
    rec.project_id = None
    rec.recording_type.value = 'audit'
    rec.testing_scope = {}
    rec.available_languages = ['en']
    return rec


@contextmanager
def _view_patches() -> Iterator[None]:
    """Patch ``ManualAccessibilityScorer`` + WCAG parser used by view_recording.

    The view itself is not under test here — we just need it to render
    far enough to hit the Phase-8 markup. Both patches return inert
    MagicMocks shaped just enough to keep the scoring sidebar happy.
    """
    scores = MagicMock()
    scores.accessibility_score = 0
    scores.compliance_score = None
    scores.total_applicable_criteria = 0
    scores.passed_criteria = 0
    scores.failed_criteria = 0

    scorer = MagicMock()
    scorer.calculate_scores.return_value = scores
    scorer.scope_mapper.get_applicable_criteria.return_value = []

    parser = MagicMock()
    parser.get_criteria_for_level.return_value = []

    with ExitStack() as stack:
        stack.enter_context(patch(
            'auto_a11y.scoring.ManualAccessibilityScorer',
            return_value=scorer,
        ))
        stack.enter_context(patch(
            'auto_a11y.wcag_parser.get_wcag_parser',
            return_value=parser,
        ))
        yield


def test_progress_page_processing_includes_progress_card_and_meta_refresh(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """processing → progress card markup + meta-refresh in the response."""
    rec = _make_recording(
        status='processing',
        progress={
            'stage': 'transcribing',
            'current': 2,
            'total': 7,
            'elapsed_ms': 65_000,
        },
        actual_cost_usd=0.0123,
    )
    mock_db.get_recording.return_value = rec
    mock_db.get_recording_issues_for_recording.return_value = []

    with _view_patches():
        resp = client.get(f'/recordings/{rec.id}')

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<meta http-equiv="refresh"' in body
    # The progress card landmark with role=status.
    assert 'role="status"' in body
    # Status badge with the Fluent string for "Processing".
    assert 'Processing' in body
    # Stage label resolved via ``audio-stage-transcribing``.
    assert 'Transcribing' in body
    # Progress bar exposes its current value.
    assert 'aria-valuenow="2"' in body
    # Cancel form posts to the cancel endpoint.
    assert f'/recordings/{rec.id}/cancel' in body
    # Running cost rendered.
    assert '0.0123' in body


def test_progress_page_complete_renders_cost_panel_and_no_meta_refresh(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """complete → cost panel + issue rendering, but NO meta-refresh."""
    rec = _make_recording(
        status='complete',
        actual_cost_usd=1.2345,
        cost_breakdown={'deepgram': 0.4, 'claude_en': 0.8345},
        estimated_cost_usd=1.10,
    )
    mock_db.get_recording.return_value = rec
    mock_db.get_recording_issues_for_recording.return_value = []

    with _view_patches():
        resp = client.get(f'/recordings/{rec.id}')

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # No meta-refresh on terminal pages.
    assert '<meta http-equiv="refresh"' not in body
    # Cost panel heading and total are present.
    assert 'Cost breakdown' in body
    assert '1.2345' in body
    # The stub also marks the "issue rendering" branch when complete.
    assert 'issue list' in body


def test_progress_page_failed_renders_error_message_alert(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """failed → ``error_message`` rendered in a medium-severity alert."""
    rec = _make_recording(
        status='failed',
        progress={
            'stage': 'analyzing',
            'current': 5,
            'total': 7,
            'elapsed_ms': 90_000,
        },
        error_message='Deepgram returned HTTP 401 (auth failed).',
    )
    mock_db.get_recording.return_value = rec
    mock_db.get_recording_issues_for_recording.return_value = []

    with _view_patches():
        resp = client.get(f'/recordings/{rec.id}')

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Deepgram returned HTTP 401 (auth failed).' in body
    assert 'alert-medium' in body
    # Failed pages are terminal — no meta-refresh.
    assert '<meta http-equiv="refresh"' not in body


def test_cancel_endpoint_flips_processing_to_cancelling(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """POST cancel while processing → 302 + status flipped to ``cancelling``."""
    rec = _make_recording(status='processing')
    mock_db.get_recording.return_value = rec

    resp = client.post(f'/recordings/{rec.id}/cancel')

    assert resp.status_code == 302
    assert f'/recordings/{rec.id}' in resp.headers['Location']
    assert rec.status == 'cancelling'
    assert mock_db.update_recording.called


def test_cancel_endpoint_is_noop_for_complete_recording(
    client: FlaskClient,
    mock_db: MagicMock,
) -> None:
    """POST cancel after complete → 302 + status unchanged + no DB write."""
    rec = _make_recording(status='complete')
    mock_db.get_recording.return_value = rec

    resp = client.post(f'/recordings/{rec.id}/cancel')

    assert resp.status_code == 302
    assert rec.status == 'complete'
    assert not mock_db.update_recording.called
