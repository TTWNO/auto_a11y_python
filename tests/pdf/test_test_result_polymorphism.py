"""Tests for TestResult polymorphic target_type/target_id."""
from __future__ import annotations

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
