"""Tests for ``auto_a11y.pdf.audit.checks.language``.

Six language-related checks ported from pdfMax's
``pdf_accessibility_audit.py`` (lines ~4453-5400). See the module
docstring of :mod:`auto_a11y.pdf.audit.checks.language` for the full
list and the deferred checks (``Language of parts markup`` and
``Cross-language link targets identified``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pikepdf

from auto_a11y.pdf.audit.checks.language import (
    LANGUAGE_CHECKS,
    check_abbreviation_expansion,
    check_annotation_language_determinable,
    check_document_language,
    check_form_field_tooltip_language_determinable,
    check_lang_values_valid_bcp47,
    check_metadata_language_determinable,
)
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _StreamMaker(Protocol):
    """Typed view of ``pikepdf.Pdf.make_stream`` for single-arg calls."""

    def __call__(self, data: bytes) -> pikepdf.Stream: ...


def _typed_make_stream(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """Strictly-typed wrapper around ``Pdf.make_stream``.

    The pikepdf stub declares ``Pdf.make_stream(d=None, **kwargs)``
    without param types, so we route through a Protocol to narrow.
    Mirrors the helpers in ``test_colors.py`` /
    ``test_reading_order.py``.
    """
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data)


def _ctx(
    pdf: pikepdf.Pdf,
    elements: list[StructElement] | None = None,
) -> AuditContext:
    """Build an ``AuditContext`` over an in-memory pikepdf.Pdf."""
    return AuditContext(
        pdf=pdf,
        pdf_path=Path("/tmp/test.pdf"),
        elements=elements if elements is not None else [],
        role_map={},
    )


def _new_pdf_with_lang(lang: str | None) -> pikepdf.Pdf:
    """Return a fresh ``pikepdf.Pdf`` with /Lang optionally set."""
    pdf = pikepdf.Pdf.new()
    if lang is not None:
        pdf.Root["/Lang"] = pikepdf.String(lang)
    return pdf


def _span(
    index: int,
    text: str,
    *,
    e_attr: str | None = None,
    a_e_attr: str | None = None,
    obj: pikepdf.Dictionary | None = None,
) -> StructElement:
    """Build a ``Span`` structure element used by the abbreviation check.

    ``e_attr`` puts an ``/E`` directly on the obj. ``a_e_attr`` puts an
    ``/A`` containing ``/E``. Both can be set to verify the precedence
    rules.
    """
    if obj is None:
        obj = pikepdf.Dictionary()
    if e_attr is not None:
        obj["/E"] = pikepdf.String(e_attr)
    if a_e_attr is not None:
        obj["/A"] = pikepdf.Dictionary({"/E": pikepdf.String(a_e_attr)})
    elem = StructElement(
        index=index,
        custom_tag="/Span",
        resolved_tag="Span",
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=obj,
    )
    elem.text_content = text
    return elem


def _struct_with_lang(
    index: int,
    tag: str,
    lang: str | None,
) -> StructElement:
    """Build a non-Span structure element for ``check_lang_values_valid_bcp47``."""
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=None,
        actual_text=None,
        lang=lang,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=pikepdf.Dictionary(),
    )


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_language_checks_registry_lists_all_six_functions() -> None:
    """``LANGUAGE_CHECKS`` is the phase-5 entry point — must list every check."""
    assert LANGUAGE_CHECKS == [
        check_document_language,
        check_lang_values_valid_bcp47,
        check_annotation_language_determinable,
        check_form_field_tooltip_language_determinable,
        check_metadata_language_determinable,
        check_abbreviation_expansion,
    ]


# ---------------------------------------------------------------------------
# check_document_language
# ---------------------------------------------------------------------------


def test_document_language_passes_when_lang_set() -> None:
    res = _only(check_document_language(_ctx(_new_pdf_with_lang("en-US"))))
    assert res.name == "Document language set"
    assert res.standard == "PDF/UA, WCAG 3.1.1"
    assert res.result == "PASS"
    assert "en-US" in res.details


def test_document_language_fails_when_lang_missing() -> None:
    res = _only(check_document_language(_ctx(_new_pdf_with_lang(None))))
    assert res.result == "FAIL"
    assert "/Lang" in res.details


def test_document_language_fails_when_lang_blank() -> None:
    """A whitespace-only /Lang value is treated as not declared."""
    pdf = _new_pdf_with_lang("   ")
    res = _only(check_document_language(_ctx(pdf)))
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_lang_values_valid_bcp47
# ---------------------------------------------------------------------------


def test_lang_values_passes_when_no_lang_anywhere() -> None:
    res = _only(check_lang_values_valid_bcp47(_ctx(_new_pdf_with_lang(None))))
    assert res.name == "Lang values are valid BCP 47"
    assert res.standard == "Matterhorn 11-003"
    assert res.result == "PASS"
    assert "No /Lang" in res.details


def test_lang_values_passes_when_all_valid() -> None:
    pdf = _new_pdf_with_lang("en-US")
    elems = [
        _struct_with_lang(0, "P", "fr-CA"),
        _struct_with_lang(1, "Span", "de"),
        _struct_with_lang(2, "P", "zh-Hant-TW"),
    ]
    res = _only(check_lang_values_valid_bcp47(_ctx(pdf, elems)))
    assert res.result == "PASS"
    assert "4" in res.details  # 1 catalog + 3 elements


def test_lang_values_passes_on_grandfathered_tag() -> None:
    """``i-klingon`` is grandfathered — must validate."""
    pdf = _new_pdf_with_lang("i-klingon")
    res = _only(check_lang_values_valid_bcp47(_ctx(pdf)))
    assert res.result == "PASS"


def test_lang_values_fails_on_invalid_tag() -> None:
    pdf = _new_pdf_with_lang("not_a_lang!")
    elems = [_struct_with_lang(0, "P", "x")]  # too short, invalid
    res = _only(check_lang_values_valid_bcp47(_ctx(pdf, elems)))
    assert res.result == "FAIL"
    assert "Document catalog" in res.details
    assert "[0] P" in res.details


# ---------------------------------------------------------------------------
# check_annotation_language_determinable
# ---------------------------------------------------------------------------


def _add_annot(
    pdf: pikepdf.Pdf,
    *,
    contents: str | None = "Note",
    subtype: str = "/Text",
    annot_lang: str | None = None,
    page_lang: str | None = None,
) -> None:
    """Add a single annotation to the (single) page of ``pdf``."""
    page = pdf.pages[0]
    annot_dict: dict[str, pikepdf.Object] = {
        "/Type": pikepdf.Name("/Annot"),
        "/Subtype": pikepdf.Name(subtype),
    }
    if contents is not None:
        annot_dict["/Contents"] = pikepdf.String(contents)
    if annot_lang is not None:
        annot_dict["/Lang"] = pikepdf.String(annot_lang)
    annot = pikepdf.Dictionary(annot_dict)
    page.obj["/Annots"] = pikepdf.Array([pdf.make_indirect(annot)])
    if page_lang is not None:
        page.obj["/Lang"] = pikepdf.String(page_lang)


def test_annotation_language_passes_when_no_annots() -> None:
    pdf = _new_pdf_with_lang(None)
    pdf.add_blank_page(page_size=(612, 792))
    res = _only(check_annotation_language_determinable(_ctx(pdf)))
    assert res.name == "Annotation contents language determinable"
    assert res.standard == "Matterhorn 11-004"
    assert res.result == "PASS"


def test_annotation_language_passes_when_annot_has_no_contents() -> None:
    """Annotations without /Contents are out of scope."""
    pdf = _new_pdf_with_lang(None)
    pdf.add_blank_page(page_size=(612, 792))
    _add_annot(pdf, contents=None)
    res = _only(check_annotation_language_determinable(_ctx(pdf)))
    assert res.result == "PASS"


def test_annotation_language_passes_via_doc_lang() -> None:
    pdf = _new_pdf_with_lang("en-US")
    pdf.add_blank_page(page_size=(612, 792))
    _add_annot(pdf, contents="Note text")
    res = _only(check_annotation_language_determinable(_ctx(pdf)))
    assert res.result == "PASS"
    assert "1 annotation" in res.details


def test_annotation_language_passes_via_annot_lang() -> None:
    pdf = _new_pdf_with_lang(None)
    pdf.add_blank_page(page_size=(612, 792))
    _add_annot(pdf, contents="Note", annot_lang="fr")
    res = _only(check_annotation_language_determinable(_ctx(pdf)))
    assert res.result == "PASS"


def test_annotation_language_passes_via_page_lang() -> None:
    pdf = _new_pdf_with_lang(None)
    pdf.add_blank_page(page_size=(612, 792))
    _add_annot(pdf, contents="Note", page_lang="de")
    res = _only(check_annotation_language_determinable(_ctx(pdf)))
    assert res.result == "PASS"


def test_annotation_language_fails_when_no_lang_anywhere() -> None:
    pdf = _new_pdf_with_lang(None)
    pdf.add_blank_page(page_size=(612, 792))
    _add_annot(pdf, contents="Note", subtype="/Text")
    res = _only(check_annotation_language_determinable(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1 annotation" in res.details
    assert "p.1 Text" in res.details


# ---------------------------------------------------------------------------
# check_form_field_tooltip_language_determinable
# ---------------------------------------------------------------------------


def _add_form_field(
    pdf: pikepdf.Pdf,
    *,
    tu: str | None = "tooltip text",
    field_lang: str | None = None,
    field_name: str = "fld1",
) -> None:
    """Attach a single AcroForm /Tx field to ``pdf``."""
    field_dict: dict[str, pikepdf.Object] = {
        "/Type": pikepdf.Name("/Annot"),
        "/Subtype": pikepdf.Name("/Widget"),
        "/FT": pikepdf.Name("/Tx"),
        "/T": pikepdf.String(field_name),
    }
    if tu is not None:
        field_dict["/TU"] = pikepdf.String(tu)
    if field_lang is not None:
        field_dict["/Lang"] = pikepdf.String(field_lang)
    field_ref = pdf.make_indirect(pikepdf.Dictionary(field_dict))
    pdf.Root["/AcroForm"] = pdf.make_indirect(
        pikepdf.Dictionary({"/Fields": pikepdf.Array([field_ref])})
    )


def test_form_field_tooltip_passes_when_no_acroform() -> None:
    pdf = _new_pdf_with_lang(None)
    res = _only(check_form_field_tooltip_language_determinable(_ctx(pdf)))
    assert res.name == "Form field tooltip language determinable"
    assert res.standard == "Matterhorn 11-005"
    assert res.result == "PASS"


def test_form_field_tooltip_passes_when_field_has_no_tu() -> None:
    pdf = _new_pdf_with_lang(None)
    _add_form_field(pdf, tu=None)
    res = _only(check_form_field_tooltip_language_determinable(_ctx(pdf)))
    assert res.result == "PASS"
    assert "No form fields" in res.details


def test_form_field_tooltip_passes_via_doc_lang() -> None:
    pdf = _new_pdf_with_lang("en")
    _add_form_field(pdf, tu="hover help")
    res = _only(check_form_field_tooltip_language_determinable(_ctx(pdf)))
    assert res.result == "PASS"
    assert "1 field" in res.details


def test_form_field_tooltip_passes_via_field_lang() -> None:
    pdf = _new_pdf_with_lang(None)
    _add_form_field(pdf, tu="hover help", field_lang="fr")
    res = _only(check_form_field_tooltip_language_determinable(_ctx(pdf)))
    assert res.result == "PASS"


def test_form_field_tooltip_fails_when_no_lang_source() -> None:
    pdf = _new_pdf_with_lang(None)
    _add_form_field(pdf, tu="hover help", field_name="email_addr")
    res = _only(check_form_field_tooltip_language_determinable(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1 field" in res.details
    assert "email_addr" in res.details


def test_form_field_tooltip_flattens_kids_groups() -> None:
    """A /Kids group with no /FT is intermediate; the terminal child wins."""
    pdf = _new_pdf_with_lang(None)
    terminal = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": pikepdf.Name("/Annot"),
        "/Subtype": pikepdf.Name("/Widget"),
        "/FT": pikepdf.Name("/Tx"),
        "/T": pikepdf.String("child_field"),
        "/TU": pikepdf.String("help"),
    }))
    group = pdf.make_indirect(pikepdf.Dictionary({
        "/Kids": pikepdf.Array([terminal]),
    }))
    pdf.Root["/AcroForm"] = pdf.make_indirect(
        pikepdf.Dictionary({"/Fields": pikepdf.Array([group])})
    )
    res = _only(check_form_field_tooltip_language_determinable(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "child_field" in res.details


# ---------------------------------------------------------------------------
# check_metadata_language_determinable
# ---------------------------------------------------------------------------


def test_metadata_language_passes_via_doc_lang() -> None:
    pdf = _new_pdf_with_lang("en-US")
    res = _only(check_metadata_language_determinable(_ctx(pdf)))
    assert res.name == "Document metadata language determinable"
    assert res.standard == "Matterhorn 11-006"
    assert res.result == "PASS"
    assert "document /Lang" in res.details


def test_metadata_language_passes_via_xmp_xml_lang() -> None:
    pdf = _new_pdf_with_lang(None)
    xmp = (
        b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xml:lang="en-US">'
        b"</x:xmpmeta>"
    )
    pdf.Root["/Metadata"] = _typed_make_stream(pdf,xmp)
    res = _only(check_metadata_language_determinable(_ctx(pdf)))
    assert res.result == "PASS"
    assert "XMP xml:lang" in res.details


def test_metadata_language_fails_when_neither_source() -> None:
    pdf = _new_pdf_with_lang(None)
    # Metadata stream without xml:lang
    pdf.Root["/Metadata"] = _typed_make_stream(pdf,b"<x:xmpmeta></x:xmpmeta>")
    res = _only(check_metadata_language_determinable(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "Neither" in res.details


def test_metadata_language_fails_when_no_metadata_stream() -> None:
    pdf = _new_pdf_with_lang(None)
    res = _only(check_metadata_language_determinable(_ctx(pdf)))
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_abbreviation_expansion
# ---------------------------------------------------------------------------


def test_abbreviation_passes_when_no_spans() -> None:
    pdf = _new_pdf_with_lang(None)
    res = _only(check_abbreviation_expansion(_ctx(pdf)))
    assert res.name == "Abbreviations have /E expansion"
    assert res.standard == "WCAG 3.1.4 (PDF8)"
    assert res.result == "PASS"


def test_abbreviation_passes_when_span_has_e() -> None:
    """Direct /E on the Span obj satisfies the check."""
    pdf = _new_pdf_with_lang(None)
    elems = [_span(0, "FAQ", e_attr="Frequently Asked Questions")]
    res = _only(check_abbreviation_expansion(_ctx(pdf, elems)))
    assert res.result == "PASS"


def test_abbreviation_passes_when_span_has_a_e() -> None:
    """/E inside an /A attribute dictionary also satisfies the check."""
    pdf = _new_pdf_with_lang(None)
    elems = [_span(0, "USA", a_e_attr="United States of America")]
    res = _only(check_abbreviation_expansion(_ctx(pdf, elems)))
    assert res.result == "PASS"


def test_abbreviation_warns_when_short_uppercase_span_has_no_e() -> None:
    pdf = _new_pdf_with_lang(None)
    elems = [_span(0, "PDF")]
    res = _only(check_abbreviation_expansion(_ctx(pdf, elems)))
    assert res.result == "WARN"
    assert "1 abbreviation" in res.details
    assert "PDF" in res.details


def test_abbreviation_skips_long_text() -> None:
    """A 7-char span is too long for the heuristic; ignored."""
    pdf = _new_pdf_with_lang(None)
    elems = [_span(0, "EXAMPLES")]
    res = _only(check_abbreviation_expansion(_ctx(pdf, elems)))
    assert res.result == "PASS"


def test_abbreviation_skips_mostly_lowercase_span() -> None:
    """A short span that's not mostly uppercase isn't abbreviation-like."""
    pdf = _new_pdf_with_lang(None)
    elems = [_span(0, "hello")]
    res = _only(check_abbreviation_expansion(_ctx(pdf, elems)))
    assert res.result == "PASS"


def test_abbreviation_skips_non_span_tags() -> None:
    """Only ``Span`` tags are considered abbreviation candidates."""
    pdf = _new_pdf_with_lang(None)
    p = StructElement(
        index=0,
        custom_tag="/P",
        resolved_tag="P",
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=pikepdf.Dictionary(),
    )
    p.text_content = "FAQ"
    res = _only(check_abbreviation_expansion(_ctx(pdf, [p])))
    assert res.result == "PASS"
