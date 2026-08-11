"""Tests for ``auto_a11y.pdf.audit.checks.document_properties``.

Thirteen document-level checks ported from pdfMax's
``pdf_accessibility_audit.py`` (lines ~4476-4555, ~5174-5180, ~7048-7063,
~7470-7502, ~7593-7600, ~7706-7732):

* ``check_document_title_set`` (PDF/UA, WCAG 2.4.2).
* ``check_pdf_is_tagged`` (PDF/UA, WCAG 1.3.1).
* ``check_no_suspect_tags`` (Matterhorn 09-004).
* ``check_pdfua_identifier`` (Matterhorn 06-002).
* ``check_xmp_metadata_stream_present`` (Matterhorn 06-001).
* ``check_xmp_dc_title_present`` (Matterhorn 06-003).
* ``check_metadata_completeness`` (PDF/UA-1 7.20).
* ``check_page_labels_consistent`` (WCAG (PDF17)).
* ``check_pdf_header_catalog_version_consistent`` (WCAG 2.2).
* ``check_pdf20_structure_elements_used_correctly`` (PDF/UA-2).
* ``check_pdfua2_accessibility_declarations_in_xmp`` (PDF/UA-2).
* ``check_pdf20_namespace_in_structure_tree`` (PDF/UA-2).
* ``check_pdfua2_requires_pdf20`` (PDF/UA-2).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pikepdf

from auto_a11y.pdf.audit.checks.document_properties import (
    DOCUMENT_PROPERTIES_CHECKS,
    check_document_title_set,
    check_metadata_completeness,
    check_no_suspect_tags,
    check_page_labels_consistent,
    check_pdf20_namespace_in_structure_tree,
    check_pdf20_structure_elements_used_correctly,
    check_pdf_header_catalog_version_consistent,
    check_pdf_is_tagged,
    check_pdfua2_accessibility_declarations_in_xmp,
    check_pdfua2_requires_pdf20,
    check_pdfua_identifier,
    check_xmp_dc_title_present,
    check_xmp_metadata_stream_present,
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
    """Strictly-typed wrapper around ``Pdf.make_stream``."""
    maker: _StreamMaker = getattr(pdf, "make_stream")
    return maker(data)


def _ctx(
    pdf: pikepdf.Pdf,
    elements: list[StructElement] | None = None,
    role_map: dict[str, str] | None = None,
) -> AuditContext:
    """Build an ``AuditContext`` over an in-memory pikepdf.Pdf."""
    return AuditContext(
        pdf=pdf,
        pdf_path=Path("/tmp/test.pdf"),
        elements=elements if elements is not None else [],
        role_map=role_map if role_map is not None else {},
    )


def _new_pdf() -> pikepdf.Pdf:
    """Return a fresh ``pikepdf.Pdf``."""
    return pikepdf.Pdf.new()


def _struct_elem(
    index: int,
    tag: str,
    custom_tag: str | None = None,
) -> StructElement:
    """Build a structure element for the PDF 2.0 element checks."""
    return StructElement(
        index=index,
        custom_tag=custom_tag if custom_tag is not None else f"/{tag}",
        resolved_tag=tag,
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=pikepdf.Dictionary(),
    )


def _set_metadata(pdf: pikepdf.Pdf, xmp_text: str) -> None:
    """Attach an XMP metadata stream to the PDF catalog."""
    stream = _typed_make_stream(pdf, xmp_text.encode("utf-8"))
    pdf.Root["/Metadata"] = stream


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_document_properties_checks_registry_lists_all_thirteen() -> None:
    """``DOCUMENT_PROPERTIES_CHECKS`` is the phase-5 entry point."""
    assert DOCUMENT_PROPERTIES_CHECKS == [
        check_document_title_set,
        check_pdf_is_tagged,
        check_no_suspect_tags,
        check_pdfua_identifier,
        check_xmp_metadata_stream_present,
        check_xmp_dc_title_present,
        check_metadata_completeness,
        check_page_labels_consistent,
        check_pdf_header_catalog_version_consistent,
        check_pdf20_structure_elements_used_correctly,
        check_pdfua2_accessibility_declarations_in_xmp,
        check_pdf20_namespace_in_structure_tree,
        check_pdfua2_requires_pdf20,
    ]


# ---------------------------------------------------------------------------
# check_document_title_set
# ---------------------------------------------------------------------------


def test_document_title_pass_when_title_and_displaydoctitle() -> None:
    pdf = _new_pdf()
    pdf.docinfo["/Title"] = pikepdf.String("My Document")
    pdf.Root["/ViewerPreferences"] = pikepdf.Dictionary(
        {"/DisplayDocTitle": True}
    )
    res = _only(check_document_title_set(_ctx(pdf)))
    assert res.name == "Document title set"
    assert res.standard == "PDF/UA, WCAG 2.4.2"
    assert res.result == "PASS"
    assert "My Document" in res.details


def test_document_title_warn_when_title_but_no_displaydoctitle() -> None:
    pdf = _new_pdf()
    pdf.docinfo["/Title"] = pikepdf.String("Some Title")
    res = _only(check_document_title_set(_ctx(pdf)))
    assert res.result == "WARN"
    assert "DisplayDocTitle" in res.details


def test_document_title_warn_when_displaydoctitle_false() -> None:
    pdf = _new_pdf()
    pdf.docinfo["/Title"] = pikepdf.String("Some Title")
    pdf.Root["/ViewerPreferences"] = pikepdf.Dictionary(
        {"/DisplayDocTitle": False}
    )
    res = _only(check_document_title_set(_ctx(pdf)))
    assert res.result == "WARN"


def test_document_title_fail_when_no_title() -> None:
    pdf = _new_pdf()
    res = _only(check_document_title_set(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "/Title" in res.details


def test_document_title_fail_when_title_blank() -> None:
    pdf = _new_pdf()
    pdf.docinfo["/Title"] = pikepdf.String("   ")
    res = _only(check_document_title_set(_ctx(pdf)))
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_pdf_is_tagged
# ---------------------------------------------------------------------------


def test_pdf_is_tagged_pass_when_marked_true() -> None:
    pdf = _new_pdf()
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary({"/Marked": True})
    res = _only(check_pdf_is_tagged(_ctx(pdf)))
    assert res.name == "PDF is tagged"
    assert res.standard == "PDF/UA, WCAG 1.3.1"
    assert res.result == "PASS"


def test_pdf_is_tagged_fail_when_marked_false() -> None:
    pdf = _new_pdf()
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary({"/Marked": False})
    res = _only(check_pdf_is_tagged(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "/Marked" in res.details


def test_pdf_is_tagged_fail_when_no_markinfo() -> None:
    pdf = _new_pdf()
    res = _only(check_pdf_is_tagged(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "/MarkInfo" in res.details


# ---------------------------------------------------------------------------
# check_no_suspect_tags
# ---------------------------------------------------------------------------


def test_no_suspect_tags_is_not_applicable_when_no_markinfo() -> None:
    pdf = _new_pdf()
    res = _only(check_no_suspect_tags(_ctx(pdf)))
    assert res.name == "No suspect tags"
    assert res.standard == "Matterhorn 09-004"
    assert res.result == "NA"


def test_no_suspect_tags_pass_when_suspects_false() -> None:
    pdf = _new_pdf()
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary(
        {"/Marked": True, "/Suspects": False}
    )
    res = _only(check_no_suspect_tags(_ctx(pdf)))
    assert res.result == "PASS"


def test_no_suspect_tags_warn_when_suspects_true() -> None:
    pdf = _new_pdf()
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary(
        {"/Marked": True, "/Suspects": True}
    )
    res = _only(check_no_suspect_tags(_ctx(pdf)))
    assert res.result == "WARN"
    assert "Suspects" in res.details


# ---------------------------------------------------------------------------
# check_pdfua_identifier
# ---------------------------------------------------------------------------


def test_pdfua_identifier_pass_when_pdfuaid_present() -> None:
    pdf = _new_pdf()
    _set_metadata(pdf, '<x:xmpmeta xmlns:pdfuaid="..."><pdfuaid:part>1</pdfuaid:part></x:xmpmeta>')
    res = _only(check_pdfua_identifier(_ctx(pdf)))
    assert res.name == "PDF/UA identifier"
    assert res.standard == "Matterhorn 06-002"
    assert res.result == "PASS"


def test_pdfua_identifier_warn_when_no_metadata() -> None:
    pdf = _new_pdf()
    res = _only(check_pdfua_identifier(_ctx(pdf)))
    assert res.result == "WARN"
    assert "PDF/UA" in res.details


def test_pdfua_identifier_warn_when_metadata_lacks_pdfuaid() -> None:
    pdf = _new_pdf()
    _set_metadata(pdf, "<x:xmpmeta>No accessibility identifier here</x:xmpmeta>")
    res = _only(check_pdfua_identifier(_ctx(pdf)))
    assert res.result == "WARN"


# ---------------------------------------------------------------------------
# check_xmp_metadata_stream_present
# ---------------------------------------------------------------------------


def test_xmp_metadata_stream_pass_when_present() -> None:
    pdf = _new_pdf()
    _set_metadata(pdf, "<x:xmpmeta/>")
    res = _only(check_xmp_metadata_stream_present(_ctx(pdf)))
    assert res.name == "XMP metadata stream present"
    assert res.standard == "Matterhorn 06-001"
    assert res.result == "PASS"


def test_xmp_metadata_stream_fail_when_absent() -> None:
    pdf = _new_pdf()
    res = _only(check_xmp_metadata_stream_present(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "/Metadata" in res.details


# ---------------------------------------------------------------------------
# check_xmp_dc_title_present
# ---------------------------------------------------------------------------


def test_xmp_dc_title_pass_when_present() -> None:
    pdf = _new_pdf()
    xmp = (
        '<x:xmpmeta xmlns:dc="..." xmlns:rdf="...">'
        '<rdf:RDF><dc:title>'
        '<rdf:Alt><rdf:li xml:lang="x-default">My Document</rdf:li>'
        "</rdf:Alt></dc:title></rdf:RDF></x:xmpmeta>"
    )
    _set_metadata(pdf, xmp)
    res = _only(check_xmp_dc_title_present(_ctx(pdf)))
    assert res.name == "XMP dc:title present"
    assert res.standard == "Matterhorn 06-003"
    assert res.result == "PASS"
    assert "My Document" in res.details


def test_xmp_dc_title_fail_when_no_metadata() -> None:
    pdf = _new_pdf()
    res = _only(check_xmp_dc_title_present(_ctx(pdf)))
    assert res.result == "FAIL"


def test_xmp_dc_title_fail_when_dc_title_missing() -> None:
    pdf = _new_pdf()
    _set_metadata(pdf, "<x:xmpmeta>no dc:title here</x:xmpmeta>")
    res = _only(check_xmp_dc_title_present(_ctx(pdf)))
    assert res.result == "FAIL"


def test_xmp_dc_title_fail_when_dc_title_empty() -> None:
    pdf = _new_pdf()
    xmp = (
        '<x:xmpmeta xmlns:dc="..." xmlns:rdf="...">'
        '<dc:title><rdf:Alt><rdf:li>   </rdf:li></rdf:Alt></dc:title>'
        "</x:xmpmeta>"
    )
    _set_metadata(pdf, xmp)
    res = _only(check_xmp_dc_title_present(_ctx(pdf)))
    assert res.result == "FAIL"


# ---------------------------------------------------------------------------
# check_metadata_completeness
# ---------------------------------------------------------------------------


def test_metadata_completeness_pass_when_all_fields_set() -> None:
    pdf = _new_pdf()
    di = pdf.docinfo
    di["/Title"] = pikepdf.String("T")
    di["/Author"] = pikepdf.String("A")
    di["/Creator"] = pikepdf.String("C")
    di["/Producer"] = pikepdf.String("P")
    di["/CreationDate"] = pikepdf.String("D:20260101000000Z")
    res = _only(check_metadata_completeness(_ctx(pdf)))
    assert res.name == "Metadata completeness"
    assert res.standard == "PDF/UA-1 7.20"
    assert res.result == "PASS"


def test_metadata_completeness_warn_when_some_missing() -> None:
    pdf = _new_pdf()
    pdf.docinfo["/Title"] = pikepdf.String("T")
    res = _only(check_metadata_completeness(_ctx(pdf)))
    assert res.result == "WARN"
    assert "/Author" in res.details
    assert "/Title" in res.details  # in "Present" group


def test_metadata_completeness_warn_when_no_info() -> None:
    pdf = _new_pdf()
    res = _only(check_metadata_completeness(_ctx(pdf)))
    assert res.result == "WARN"


# ---------------------------------------------------------------------------
# check_page_labels_consistent
# ---------------------------------------------------------------------------


def test_page_labels_is_not_applicable_when_no_pagelabels() -> None:
    pdf = _new_pdf()
    res = _only(check_page_labels_consistent(_ctx(pdf)))
    assert res.name == "Page labels consistent"
    assert res.standard == "WCAG (PDF17)"
    assert res.result == "NA"
    assert "optional" in res.details.lower()


def test_page_labels_pass_when_nums_starts_at_zero() -> None:
    pdf = _new_pdf()
    pdf.Root["/PageLabels"] = pikepdf.Dictionary(
        {
            "/Nums": pikepdf.Array(
                [0, pikepdf.Dictionary({"/S": pikepdf.Name("/D")})]
            )
        }
    )
    res = _only(check_page_labels_consistent(_ctx(pdf)))
    assert res.result == "PASS"
    assert "1 range" in res.details


def test_page_labels_fail_when_nums_empty() -> None:
    pdf = _new_pdf()
    pdf.Root["/PageLabels"] = pikepdf.Dictionary(
        {"/Nums": pikepdf.Array([])}
    )
    res = _only(check_page_labels_consistent(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "missing or empty" in res.details


def test_page_labels_fail_when_nums_missing() -> None:
    pdf = _new_pdf()
    pdf.Root["/PageLabels"] = pikepdf.Dictionary({})
    res = _only(check_page_labels_consistent(_ctx(pdf)))
    assert res.result == "FAIL"


def test_page_labels_fail_when_starts_at_nonzero() -> None:
    pdf = _new_pdf()
    pdf.Root["/PageLabels"] = pikepdf.Dictionary(
        {
            "/Nums": pikepdf.Array(
                [5, pikepdf.Dictionary({"/S": pikepdf.Name("/D")})]
            )
        }
    )
    res = _only(check_page_labels_consistent(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "5" in res.details


# ---------------------------------------------------------------------------
# check_pdf_header_catalog_version_consistent
# ---------------------------------------------------------------------------


def test_header_catalog_version_is_not_applicable_when_no_catalog_version() -> None:
    pdf = _new_pdf()
    res = _only(check_pdf_header_catalog_version_consistent(_ctx(pdf)))
    assert res.name == "PDF header and catalog version consistent"
    assert res.standard == "WCAG 2.2"
    assert res.result == "NA"
    assert "header version" in res.details.lower()


def test_header_catalog_version_pass_when_match() -> None:
    pdf = _new_pdf()
    header = str(pdf.pdf_version)
    pdf.Root["/Version"] = pikepdf.Name(f"/{header}")
    res = _only(check_pdf_header_catalog_version_consistent(_ctx(pdf)))
    assert res.result == "PASS"


def test_header_catalog_version_fail_when_mismatch() -> None:
    pdf = _new_pdf()
    pdf.Root["/Version"] = pikepdf.Name("/2.0")
    res = _only(check_pdf_header_catalog_version_consistent(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "2.0" in res.details


# ---------------------------------------------------------------------------
# check_pdf20_structure_elements_used_correctly
# ---------------------------------------------------------------------------


def test_pdf20_structure_elements_is_not_applicable_when_none_used() -> None:
    pdf = _new_pdf()
    res = _only(check_pdf20_structure_elements_used_correctly(_ctx(pdf)))
    assert res.name == "New PDF 2.0 structure elements used correctly"
    assert res.standard == "PDF/UA-2"
    assert res.result == "NA"


def test_pdf20_structure_elements_pass_when_used_natively() -> None:
    pdf = _new_pdf()
    elems = [_struct_elem(0, "Aside"), _struct_elem(1, "Strong")]
    res = _only(
        check_pdf20_structure_elements_used_correctly(_ctx(pdf, elems))
    )
    assert res.result == "PASS"
    assert "Aside" in res.details


def test_pdf20_structure_elements_warn_when_remapped() -> None:
    pdf = _new_pdf()
    pdf.Root["/StructTreeRoot"] = pikepdf.Dictionary(
        {
            "/RoleMap": pikepdf.Dictionary(
                {"/Aside": pikepdf.Name("/Sect")}
            )
        }
    )
    res = _only(check_pdf20_structure_elements_used_correctly(_ctx(pdf)))
    assert res.result == "WARN"
    assert "Aside" in res.details


# ---------------------------------------------------------------------------
# check_pdfua2_accessibility_declarations_in_xmp
# ---------------------------------------------------------------------------


def test_pdfua2_xmp_pass_when_part2_and_rev() -> None:
    pdf = _new_pdf()
    _set_metadata(
        pdf,
        "<x:xmpmeta><pdfuaid:part>2</pdfuaid:part>"
        + "<pdfuaid:rev>2024</pdfuaid:rev></x:xmpmeta>",
    )
    res = _only(check_pdfua2_accessibility_declarations_in_xmp(_ctx(pdf)))
    assert res.name == "PDF/UA-2 accessibility declarations in XMP"
    assert res.standard == "PDF/UA-2"
    assert res.result == "PASS"


def test_pdfua2_xmp_warn_when_part2_but_no_rev() -> None:
    pdf = _new_pdf()
    _set_metadata(
        pdf, '<x:xmpmeta><pdfuaid:part>2</pdfuaid:part></x:xmpmeta>'
    )
    res = _only(check_pdfua2_accessibility_declarations_in_xmp(_ctx(pdf)))
    assert res.result == "WARN"
    assert "rev" in res.details


def test_pdfua2_xmp_warn_when_no_part2() -> None:
    pdf = _new_pdf()
    res = _only(check_pdfua2_accessibility_declarations_in_xmp(_ctx(pdf)))
    assert res.result == "WARN"


# ---------------------------------------------------------------------------
# check_pdf20_namespace_in_structure_tree
# ---------------------------------------------------------------------------


def test_pdf20_namespace_is_not_applicable_when_pdf20_not_used() -> None:
    pdf = _new_pdf()
    res = _only(check_pdf20_namespace_in_structure_tree(_ctx(pdf)))
    assert res.name == "PDF 2.0 namespace in structure tree"
    assert res.standard == "PDF/UA-2"
    assert res.result == "NA"
    assert "not required" in res.details.lower()


def test_pdf20_namespace_pass_when_pdf20_used_with_namespaces() -> None:
    pdf = _new_pdf()
    pdf.Root["/StructTreeRoot"] = pikepdf.Dictionary(
        {"/Namespaces": pikepdf.Array([])}
    )
    elems = [_struct_elem(0, "Aside")]
    res = _only(check_pdf20_namespace_in_structure_tree(_ctx(pdf, elems)))
    assert res.result == "PASS"


def test_pdf20_namespace_fail_when_pdf20_used_without_namespaces() -> None:
    pdf = _new_pdf()
    pdf.Root["/StructTreeRoot"] = pikepdf.Dictionary({})
    elems = [_struct_elem(0, "Aside")]
    res = _only(check_pdf20_namespace_in_structure_tree(_ctx(pdf, elems)))
    assert res.result == "FAIL"
    assert "Namespaces" in res.details


# ---------------------------------------------------------------------------
# check_pdfua2_requires_pdf20
# ---------------------------------------------------------------------------


def test_pdfua2_requires_pdf20_is_not_applicable_when_no_pdfua2_id() -> None:
    pdf = _new_pdf()
    res = _only(check_pdfua2_requires_pdf20(_ctx(pdf)))
    assert res.name == "PDF/UA-2 requires PDF 2.0"
    assert res.standard == "PDF/UA-2"
    assert res.result == "NA"


def test_pdfua2_requires_pdf20_pass_when_pdfua2_and_pdf2() -> None:
    pdf = _new_pdf()
    _set_metadata(pdf, '<x:xmpmeta><pdfuaid:part>2</pdfuaid:part></x:xmpmeta>')
    pdf.Root["/Version"] = pikepdf.Name("/2.0")
    res = _only(check_pdfua2_requires_pdf20(_ctx(pdf)))
    assert res.result == "PASS"
    assert "2.0" in res.details


def test_pdfua2_requires_pdf20_fail_when_pdfua2_but_old_version() -> None:
    pdf = _new_pdf()
    _set_metadata(pdf, '<x:xmpmeta><pdfuaid:part>2</pdfuaid:part></x:xmpmeta>')
    res = _only(check_pdfua2_requires_pdf20(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "2.0" in res.details
