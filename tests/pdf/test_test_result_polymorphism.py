"""Tests for TestResult polymorphic target_type/target_id."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from auto_a11y.models.test_result import TargetType, TestResult


def test_test_result_new_writes_set_both_fields() -> None:
    tr = TestResult(page_id='p1', website_id='w1', target_type=TargetType.PAGE, target_id='p1')
    d = tr.to_dict()
    assert d['target_type'] == 'page'
    assert d['target_id'] == 'p1'
    assert d['page_id'] == 'p1'  # retained for back-compat


def test_test_result_pdf_target() -> None:
    tr = TestResult(
        page_id=None,
        website_id='w1',
        target_type=TargetType.PDF_DOCUMENT,
        target_id='pdf-42',
    )
    d = tr.to_dict()
    assert d['target_type'] == 'pdf_document'
    assert d['target_id'] == 'pdf-42'
    assert d['page_id'] is None


def test_test_result_old_record_infers_page_target() -> None:
    # Simulate a record written before this change
    data = {
        'page_id': 'p1',
        'website_id': 'w1',
        # no target_type, no target_id
    }
    tr = TestResult.from_dict(data)
    assert tr.target_type is TargetType.PAGE
    assert tr.target_id == 'p1'


def test_pdf_document_target_requires_id() -> None:
    """A PDF-targeted result must carry a target_id; missing raises ValueError."""
    with pytest.raises(ValueError, match="target_type=PDF_DOCUMENT requires target_id"):
        TestResult(
            page_id=None,
            website_id='w1',
            target_type=TargetType.PDF_DOCUMENT,
            target_id='',
        )


def test_page_target_requires_page_id() -> None:
    """A PAGE-targeted result must carry a page_id; missing raises ValueError."""
    with pytest.raises(ValueError, match="PAGE-targeted TestResult requires page_id"):
        TestResult(
            page_id=None,
            website_id='w1',
            target_type=TargetType.PAGE,
            target_id='x',
        )


def test_from_dict_explicit_pdf_target() -> None:
    """A record explicitly tagged as PDF must read back with target_type=PDF
    and a None page_id — the from_dict back-compat shim must NOT copy
    target_id into page_id when the record is PDF-targeted."""
    data: dict[str, Any] = {
        'target_type': 'pdf_document',
        'target_id': 'pdf-x',
        'website_id': 'w1',
        # page_id intentionally absent
    }
    tr = TestResult.from_dict(data)
    assert tr.target_type is TargetType.PDF_DOCUMENT
    assert tr.target_id == 'pdf-x'
    assert tr.page_id is None


def test_round_trip_pdf_target() -> None:
    """A PDF-targeted TestResult must survive a to_dict / from_dict cycle
    with all polymorphism-relevant fields intact."""
    test_date = datetime(2026, 4, 24, 12, 0, 0)
    original = TestResult(
        page_id=None,
        website_id='w1',
        test_date=test_date,
        duration_ms=42,
        target_type=TargetType.PDF_DOCUMENT,
        target_id='pdf-99',
    )
    round_tripped = TestResult.from_dict(original.to_dict())
    assert round_tripped.target_type is original.target_type
    assert round_tripped.target_id == original.target_id
    assert round_tripped.page_id == original.page_id
    assert round_tripped.website_id == original.website_id
    assert round_tripped.test_date == original.test_date
    assert round_tripped.duration_ms == original.duration_ms
    assert round_tripped.score == original.score
