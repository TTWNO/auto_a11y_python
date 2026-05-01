"""Tests for new PDF-specific touchpoints."""
from __future__ import annotations

from auto_a11y.core.touchpoints import TouchpointID, get_all_touchpoints


def test_pdf_touchpoints_exist() -> None:
    assert TouchpointID.PDF_TAGGING.value == 'pdf_tagging'
    assert TouchpointID.PDF_DOCUMENT_PROPERTIES.value == 'pdf_document_properties'
    assert TouchpointID.PDF_ANNOTATIONS.value == 'pdf_annotations'


def test_pdf_touchpoints_appear_in_registry() -> None:
    all_tp = get_all_touchpoints()
    ids = {tp.id.value for tp in all_tp}
    assert 'pdf_tagging' in ids
    assert 'pdf_document_properties' in ids
    assert 'pdf_annotations' in ids


def test_pdf_touchpoints_have_expected_wcag_criteria() -> None:
    from auto_a11y.core.touchpoints import TOUCHPOINTS
    assert TOUCHPOINTS[TouchpointID.PDF_TAGGING].wcag_criteria == ['1.3.1', '1.3.2', '4.1.2']
    assert TOUCHPOINTS[TouchpointID.PDF_DOCUMENT_PROPERTIES].wcag_criteria == ['2.4.2', '3.1.1', '1.4.8']
    assert TOUCHPOINTS[TouchpointID.PDF_ANNOTATIONS].wcag_criteria == ['1.3.1', '2.4.3', '4.1.2']
