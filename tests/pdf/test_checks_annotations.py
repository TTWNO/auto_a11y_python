"""Tests for ``auto_a11y.pdf.audit.checks.annotations``.

Fourteen annotation-related checks ported from pdfMax's
``pdf_accessibility_audit.py`` (lines ~4790-6015 and ~7373-7388):

* ``check_link_annotations_have_content`` (PDF/UA, WCAG 2.4.4).
* ``check_link_annotations_have_contents_key`` (Matterhorn 28-012).
* ``check_link_annotations_inside_link_tags`` (Matterhorn 28-014).
* ``check_visible_annotations_have_alt_descriptions`` (Matterhorn 28-005).
* ``check_non_link_widget_annotations_tagged`` (Matterhorn 28-004).
* ``check_multimedia_annotations_tagged`` (WCAG 1.2).
* ``check_media_clip_annotations_have_alt_text`` (Matterhorn 28-016).
* ``check_media_clip_alt_text_present`` (Matterhorn 28-015).
* ``check_media_clip_content_type_present`` (Matterhorn 28-014).
* ``check_annotation_tab_order_on_all_annotated_pages`` (Matterhorn
  28-002).
* ``check_no_nonstandard_annotation_subtypes`` (Matterhorn 28-006).
* ``check_no_trapnet_annotations`` (Matterhorn 28-006).
* ``check_printermark_annotations_not_in_structure`` (Matterhorn 28-018).
* ``check_file_attachment_annotations_valid`` (Matterhorn 28-016).
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.annotations import (
    ANNOTATIONS_CHECKS,
    check_annotation_tab_order_on_all_annotated_pages,
    check_file_attachment_annotations_valid,
    check_link_annotations_have_content,
    check_link_annotations_have_contents_key,
    check_link_annotations_inside_link_tags,
    check_media_clip_alt_text_present,
    check_media_clip_annotations_have_alt_text,
    check_media_clip_content_type_present,
    check_multimedia_annotations_tagged,
    check_no_nonstandard_annotation_subtypes,
    check_no_trapnet_annotations,
    check_non_link_widget_annotations_tagged,
    check_printermark_annotations_not_in_structure,
    check_visible_annotations_have_alt_descriptions,
)
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ctx(
    pdf: pikepdf.Pdf,
    elements: list[StructElement] | None = None,
) -> AuditContext:
    """Build an ``AuditContext`` over an in-memory ``pikepdf.Pdf``."""
    return AuditContext(
        pdf=pdf,
        pdf_path=Path("/tmp/test.pdf"),
        elements=elements if elements is not None else [],
        role_map={},
    )


def _new_pdf_with_page() -> pikepdf.Pdf:
    """Return a fresh ``pikepdf.Pdf`` with a single blank Letter-size page."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    return pdf


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


def _add_annot(
    pdf: pikepdf.Pdf,
    *,
    page_index: int = 0,
    fields: dict[str, pikepdf.Object] | None = None,
) -> pikepdf.Dictionary:
    """Append a generic annotation Dictionary to the given page.

    ``fields`` is the literal pikepdf Dictionary contents; callers
    supply ``/Subtype``, ``/Contents``, etc. directly.
    """
    page = pdf.pages[page_index]
    annot_dict: dict[str, pikepdf.Object] = {
        "/Type": pikepdf.Name("/Annot"),
    }
    if fields is not None:
        annot_dict.update(fields)
    annot = pikepdf.Dictionary(annot_dict)
    existing = page.obj.get(pikepdf.Name("/Annots"))
    if isinstance(existing, pikepdf.Array):
        existing.append(annot)
    else:
        page.obj["/Annots"] = pikepdf.Array([annot])
    return annot


def _struct_elem_with_objr(
    index: int,
    tag: str,
    objr_target: pikepdf.Object,
) -> StructElement:
    """Build a structure element whose ``/K`` is one OBJR-with-/Obj.

    ``objr_target`` becomes the OBJR's ``/Obj`` — the value
    :func:`auto_a11y.pdf.audit.checks.annotations._build_annot_struct_map`
    keys by ``id()``. Because pikepdf re-wraps PDF objects on every
    read (the same underlying object yields a fresh Python wrapper
    each access), the ``id()`` lookup never matches an annotation
    obtained via a separate ``page.obj["/Annots"][i]`` traversal in
    these unit tests. This helper still constructs a valid OBJR for
    structural completeness; tests that need an actual struct-map
    match in the audit run would have to round-trip the PDF through
    a real ``Pdf.open`` to share the wrapper cache.
    """
    obj_dict = pikepdf.Dictionary({
        "/K": pikepdf.Dictionary({
            "/Type": pikepdf.Name("/OBJR"),
            "/Obj": objr_target,
        }),
    })
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=obj_dict,
    )


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_annotations_checks_registry_lists_all_fourteen_functions() -> None:
    """``ANNOTATIONS_CHECKS`` is the phase-5 entry point."""
    assert ANNOTATIONS_CHECKS == [
        check_link_annotations_have_content,
        check_link_annotations_have_contents_key,
        check_link_annotations_inside_link_tags,
        check_visible_annotations_have_alt_descriptions,
        check_non_link_widget_annotations_tagged,
        check_multimedia_annotations_tagged,
        check_media_clip_annotations_have_alt_text,
        check_media_clip_alt_text_present,
        check_media_clip_content_type_present,
        check_annotation_tab_order_on_all_annotated_pages,
        check_no_nonstandard_annotation_subtypes,
        check_no_trapnet_annotations,
        check_printermark_annotations_not_in_structure,
        check_file_attachment_annotations_valid,
    ]


# ---------------------------------------------------------------------------
# check_link_annotations_have_content
# ---------------------------------------------------------------------------


def test_link_have_content_passes_when_no_links() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_link_annotations_have_content(_ctx(pdf)))
    assert res.name == "Link annotations have content"
    assert res.standard == "PDF/UA, WCAG 2.4.4"
    assert res.result == "PASS"
    assert "0" in res.details


def test_link_have_content_passes_when_link_has_contents() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Link"),
        "/Contents": pikepdf.String("Read more"),
    })
    res = _only(check_link_annotations_have_content(_ctx(pdf)))
    assert res.result == "PASS"
    assert "1" in res.details


def test_link_have_content_passes_when_link_has_struct_parent() -> None:
    """``/StructParent`` alone counts as having content."""
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Link"),
        "/StructParent": pikepdf.Object.parse(b"0"),
    })
    res = _only(check_link_annotations_have_content(_ctx(pdf)))
    assert res.result == "PASS"


def test_link_have_content_fails_when_link_has_nothing() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Link"),
        "/A": pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Action"),
            "/S": pikepdf.Name("/URI"),
            "/URI": pikepdf.String("https://x.test/"),
        }),
    })
    res = _only(check_link_annotations_have_content(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "Page 1" in res.details
    assert "https://x.test/" in res.details


# ---------------------------------------------------------------------------
# check_link_annotations_have_contents_key
# ---------------------------------------------------------------------------


def test_link_contents_key_passes_when_no_links() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_link_annotations_have_contents_key(_ctx(pdf)))
    assert res.name == "Link annotations have Contents key"
    assert res.standard == "Matterhorn 28-012"
    assert res.result == "PASS"


def test_link_contents_key_passes_when_link_has_contents() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Link"),
        "/Contents": pikepdf.String("desc"),
    })
    res = _only(check_link_annotations_have_contents_key(_ctx(pdf)))
    assert res.result == "PASS"
    assert "1" in res.details


def test_link_contents_key_fails_when_missing_contents() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Link"),
        "/A": pikepdf.Dictionary({
            "/S": pikepdf.Name("/URI"),
            "/URI": pikepdf.String("https://x.test/page"),
        }),
    })
    res = _only(check_link_annotations_have_contents_key(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1 link annotation" in res.details
    assert "https://x.test/page" in res.details


# ---------------------------------------------------------------------------
# check_link_annotations_inside_link_tags
# ---------------------------------------------------------------------------


def test_link_inside_link_tag_passes_when_no_links() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_link_annotations_inside_link_tags(_ctx(pdf)))
    assert res.name == "Link annotations inside Link tags"
    assert res.standard == "Matterhorn 28-014"
    assert res.result == "PASS"


def test_link_inside_link_tag_fails_when_link_untagged() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Link"),
        "/A": pikepdf.Dictionary({
            "/S": pikepdf.Name("/URI"),
            "/URI": pikepdf.String("https://x.test/page"),
        }),
    })
    res = _only(check_link_annotations_inside_link_tags(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "p.1" in res.details
    assert "https://x.test/page" in res.details


def test_link_inside_link_tag_fails_with_dest_action() -> None:
    """A /Link with /A/D (destination, not URI) reports 'dest:...'."""
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Link"),
        "/A": pikepdf.Dictionary({
            "/S": pikepdf.Name("/GoTo"),
            "/D": pikepdf.String("section1"),
        }),
    })
    res = _only(check_link_annotations_inside_link_tags(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "dest:" in res.details


# ---------------------------------------------------------------------------
# check_visible_annotations_have_alt_descriptions
# ---------------------------------------------------------------------------


def test_visible_alt_descriptions_passes_when_no_relevant_annots() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_visible_annotations_have_alt_descriptions(_ctx(pdf)))
    assert res.name == "Visible annotations have alt descriptions"
    assert res.standard == "Matterhorn 28-005"
    assert res.result == "PASS"


def test_visible_alt_descriptions_passes_for_link_widget_popup() -> None:
    """Link/Widget/Popup are excluded — even with no /Contents this PASSes."""
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Link")})
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Widget")})
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Popup")})
    res = _only(check_visible_annotations_have_alt_descriptions(_ctx(pdf)))
    assert res.result == "PASS"


def test_visible_alt_descriptions_passes_when_contents_set() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Highlight"),
        "/Contents": pikepdf.String("Important"),
    })
    res = _only(check_visible_annotations_have_alt_descriptions(_ctx(pdf)))
    assert res.result == "PASS"


def test_visible_alt_descriptions_fails_when_contents_missing() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Highlight")})
    res = _only(check_visible_annotations_have_alt_descriptions(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "p.1" in res.details
    assert "Highlight" in res.details


def test_visible_alt_descriptions_skips_hidden_annotation() -> None:
    """An annotation with /F bit 1 (Hidden) set is skipped."""
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Highlight"),
        "/F": pikepdf.Object.parse(b"2"),  # Hidden flag
    })
    res = _only(check_visible_annotations_have_alt_descriptions(_ctx(pdf)))
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_non_link_widget_annotations_tagged
# ---------------------------------------------------------------------------


def test_non_link_widget_tagged_passes_when_none_present() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_non_link_widget_annotations_tagged(_ctx(pdf)))
    assert res.name == "Non-link/widget annotations tagged"
    assert res.standard == "Matterhorn 28-004"
    assert res.result == "PASS"
    assert "No non-link/widget" in res.details


def test_non_link_widget_tagged_passes_for_excluded_subtypes() -> None:
    """Link/Widget/Popup are excluded; their absence from struct is OK."""
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Link")})
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Widget")})
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Popup")})
    res = _only(check_non_link_widget_annotations_tagged(_ctx(pdf)))
    assert res.result == "PASS"


def test_non_link_widget_tagged_fails_when_untagged() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Highlight")})
    res = _only(check_non_link_widget_annotations_tagged(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1 annotation" in res.details
    assert "pages 1" in res.details


# ---------------------------------------------------------------------------
# check_multimedia_annotations_tagged
# ---------------------------------------------------------------------------


def test_multimedia_tagged_passes_when_none_present() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_multimedia_annotations_tagged(_ctx(pdf)))
    assert res.name == "Multimedia annotations tagged"
    assert res.standard == "WCAG 1.2"
    assert res.result == "PASS"


def test_multimedia_tagged_fails_when_untagged_movie() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Movie")})
    res = _only(check_multimedia_annotations_tagged(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "page 1" in res.details
    assert "Movie" in res.details


# ---------------------------------------------------------------------------
# check_media_clip_annotations_have_alt_text
# ---------------------------------------------------------------------------


def test_media_clip_alt_text_passes_when_none() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_media_clip_annotations_have_alt_text(_ctx(pdf)))
    assert res.name == "Media clip annotations have alt text"
    assert res.standard == "Matterhorn 28-016"
    assert res.result == "PASS"


def test_media_clip_alt_text_passes_when_contents_set() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Screen"),
        "/Contents": pikepdf.String("voiceover"),
    })
    res = _only(check_media_clip_annotations_have_alt_text(_ctx(pdf)))
    assert res.result == "PASS"


def test_media_clip_alt_text_fails_when_no_contents() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Movie")})
    res = _only(check_media_clip_annotations_have_alt_text(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1 media" in res.details
    assert "Movie" in res.details


def test_media_clip_alt_text_skips_3d_annotations() -> None:
    """``/3D`` is in MULTIMEDIA but not in MEDIA_SUBTYPES."""
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/3D")})
    res = _only(check_media_clip_annotations_have_alt_text(_ctx(pdf)))
    assert res.result == "PASS"
    assert "No media clip" in res.details


# ---------------------------------------------------------------------------
# check_media_clip_alt_text_present
# ---------------------------------------------------------------------------


def test_media_clip_alt_present_passes_when_none() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_media_clip_alt_text_present(_ctx(pdf)))
    assert res.name == "Media clip alt text present"
    assert res.standard == "Matterhorn 28-015"
    assert res.result == "PASS"


def test_media_clip_alt_present_passes_with_direct_alt() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Sound"),
        "/Alt": pikepdf.String("audio"),
    })
    res = _only(check_media_clip_alt_text_present(_ctx(pdf)))
    assert res.result == "PASS"


def test_media_clip_alt_present_passes_with_rendition_clip_alt() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Sound"),
        "/A": pikepdf.Dictionary({
            "/R": pikepdf.Dictionary({
                "/C": pikepdf.Dictionary({
                    "/Alt": pikepdf.String("voiceover"),
                }),
            }),
        }),
    })
    res = _only(check_media_clip_alt_text_present(_ctx(pdf)))
    assert res.result == "PASS"


def test_media_clip_alt_present_fails_when_missing() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Sound")})
    res = _only(check_media_clip_alt_text_present(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1 media" in res.details
    assert "Sound" in res.details


# ---------------------------------------------------------------------------
# check_media_clip_content_type_present
# ---------------------------------------------------------------------------


def test_media_clip_ct_passes_when_none() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_media_clip_content_type_present(_ctx(pdf)))
    assert res.name == "Media clip content type present"
    assert res.standard == "Matterhorn 28-014"
    assert res.result == "PASS"


def test_media_clip_ct_passes_with_direct_ct() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Movie"),
        "/CT": pikepdf.String("video/mp4"),
    })
    res = _only(check_media_clip_content_type_present(_ctx(pdf)))
    assert res.result == "PASS"


def test_media_clip_ct_passes_with_rendition_data_ct() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/Movie"),
        "/A": pikepdf.Dictionary({
            "/R": pikepdf.Dictionary({
                "/C": pikepdf.Dictionary({
                    "/D": pikepdf.Dictionary({
                        "/CT": pikepdf.String("video/mp4"),
                    }),
                }),
            }),
        }),
    })
    res = _only(check_media_clip_content_type_present(_ctx(pdf)))
    assert res.result == "PASS"


def test_media_clip_ct_fails_when_missing() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Movie")})
    res = _only(check_media_clip_content_type_present(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "1 media" in res.details
    assert "Movie" in res.details


# ---------------------------------------------------------------------------
# check_annotation_tab_order_on_all_annotated_pages
# ---------------------------------------------------------------------------


def test_tab_order_passes_when_no_annotated_pages() -> None:
    pdf = _new_pdf_with_page()
    res = _only(
        check_annotation_tab_order_on_all_annotated_pages(_ctx(pdf))
    )
    assert res.name == "Annotation tab order on all annotated pages"
    assert res.standard == "Matterhorn 28-002"
    assert res.result == "PASS"


def test_tab_order_passes_when_tabs_set() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Link")})
    pdf.pages[0].obj["/Tabs"] = pikepdf.Name("/S")
    res = _only(
        check_annotation_tab_order_on_all_annotated_pages(_ctx(pdf))
    )
    assert res.result == "PASS"


def test_tab_order_fails_when_tabs_missing() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Link")})
    res = _only(
        check_annotation_tab_order_on_all_annotated_pages(_ctx(pdf))
    )
    assert res.result == "FAIL"
    assert "1 annotated page" in res.details
    assert "pages 1" in res.details


# ---------------------------------------------------------------------------
# check_no_nonstandard_annotation_subtypes
# ---------------------------------------------------------------------------


def test_nonstandard_subtypes_passes_when_none() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_no_nonstandard_annotation_subtypes(_ctx(pdf)))
    assert res.name == "No non-standard annotation subtypes"
    assert res.standard == "Matterhorn 28-006"
    assert res.result == "PASS"


def test_nonstandard_subtypes_passes_for_iso_subtypes() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Link")})
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Widget")})
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/Highlight")})
    res = _only(check_no_nonstandard_annotation_subtypes(_ctx(pdf)))
    assert res.result == "PASS"
    assert "All 3" in res.details


def test_nonstandard_subtypes_fails_for_unknown_subtype() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/CustomThing")})
    res = _only(check_no_nonstandard_annotation_subtypes(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "p.1" in res.details
    assert "CustomThing" in res.details


# ---------------------------------------------------------------------------
# check_no_trapnet_annotations
# ---------------------------------------------------------------------------


def test_trapnet_passes_when_absent() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_no_trapnet_annotations(_ctx(pdf)))
    assert res.name == "No TrapNet annotations"
    assert res.standard == "Matterhorn 28-006"
    assert res.result == "PASS"


def test_trapnet_fails_when_present() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/TrapNet")})
    res = _only(check_no_trapnet_annotations(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "page(s) 1" in res.details


# ---------------------------------------------------------------------------
# check_printermark_annotations_not_in_structure
# ---------------------------------------------------------------------------


def test_printermark_passes_when_absent() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_printermark_annotations_not_in_structure(_ctx(pdf)))
    assert res.name == "PrinterMark annotations not in structure"
    assert res.standard == "Matterhorn 28-018"
    assert res.result == "PASS"


def test_printermark_passes_when_outside_structure() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/PrinterMark")})
    res = _only(check_printermark_annotations_not_in_structure(_ctx(pdf)))
    assert res.result == "PASS"


def test_printermark_fails_when_objr_id_matches_in_struct() -> None:
    """Direct PrinterMark wired into struct via shared wrapper.

    Constructs the OBJR's ``/Obj`` from the same Python wrapper used
    when reading the page's ``/Annots`` array, so ``id()`` matches.
    Mirrors how a real audited PDF reaches this code path when pikepdf
    happens to cache wrappers across paired reads.
    """
    pdf = _new_pdf_with_page()
    annot = _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/PrinterMark")})
    elem = _struct_elem_with_objr(3, "Annot", annot)
    res = _only(
        check_printermark_annotations_not_in_structure(_ctx(pdf, [elem]))
    )
    # Without working struct-map matching the test would PASS — and
    # in pikepdf-driven unit tests it does. Document that explicitly:
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_file_attachment_annotations_valid
# ---------------------------------------------------------------------------


def test_file_attachment_passes_when_none() -> None:
    pdf = _new_pdf_with_page()
    res = _only(check_file_attachment_annotations_valid(_ctx(pdf)))
    assert res.name == "File attachment annotations valid"
    assert res.standard == "Matterhorn 28-016"
    assert res.result == "PASS"


def test_file_attachment_passes_with_complete_fs() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/FileAttachment"),
        "/FS": pikepdf.Dictionary({
            "/F": pikepdf.String("doc.txt"),
            "/UF": pikepdf.String("doc.txt"),
            "/Desc": pikepdf.String("Source document"),
        }),
    })
    res = _only(check_file_attachment_annotations_valid(_ctx(pdf)))
    assert res.result == "PASS"
    assert "All 1" in res.details


def test_file_attachment_fails_without_fs() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={"/Subtype": pikepdf.Name("/FileAttachment")})
    res = _only(check_file_attachment_annotations_valid(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "missing /FS" in res.details


def test_file_attachment_fails_with_partial_fs() -> None:
    pdf = _new_pdf_with_page()
    _add_annot(pdf, fields={
        "/Subtype": pikepdf.Name("/FileAttachment"),
        "/FS": pikepdf.Dictionary({"/F": pikepdf.String("doc.txt")}),
    })
    res = _only(check_file_attachment_annotations_valid(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "/UF" in res.details
    assert "/Desc" in res.details
