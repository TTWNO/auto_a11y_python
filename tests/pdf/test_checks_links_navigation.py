"""Tests for ``auto_a11y.pdf.audit.checks.links_navigation``.

Three link/navigation checks ported from pdfMax's
``pdf_accessibility_audit.py``:

* ``check_link_alt_text_descriptive`` (WCAG 2.4.4 best practice).
* ``check_bookmarks_present`` (PDF/UA, WCAG 2.4.5).
* ``check_cross_language_link_targets_identified`` (WCAG 3.1.2 best
  practice).
"""
from __future__ import annotations

from pathlib import Path

import pikepdf

from auto_a11y.pdf.audit.checks.links_navigation import (
    LINKS_NAVIGATION_CHECKS,
    check_bookmarks_present,
    check_cross_language_link_targets_identified,
    check_link_alt_text_descriptive,
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


def _new_pdf_with_pages(num_pages: int = 1) -> pikepdf.Pdf:
    """Return a fresh ``pikepdf.Pdf`` with ``num_pages`` blank pages."""
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(612, 792))
    return pdf


def _set_lang(pdf: pikepdf.Pdf, lang: str | None) -> pikepdf.Pdf:
    """Set ``/Lang`` on the catalog if ``lang`` is provided."""
    if lang is not None:
        pdf.Root["/Lang"] = pikepdf.String(lang)
    return pdf


def _set_outlines(pdf: pikepdf.Pdf, count: int | None) -> pikepdf.Pdf:
    """Attach an ``/Outlines`` dictionary with the given ``/Count``.

    ``count = None`` removes the outlines key entirely (no /Outlines);
    ``count = 0`` writes an explicit empty outline tree.
    """
    if count is None:
        return pdf
    pdf.Root["/Outlines"] = pikepdf.Dictionary({
        "/Count": pikepdf.Object.parse(str(count).encode("ascii")),
    })
    return pdf


def _link_elem(
    index: int,
    *,
    alt_text: str | None = None,
    text_content: str = "",
    uri: str | None = None,
) -> StructElement:
    """Build a ``Link`` structure element.

    When ``uri`` is provided, ``elem.obj`` carries a ``/K`` array with
    one OBJR child whose ``/Obj`` is an annotation dict with ``/A/URI``
    set — that's what
    :func:`auto_a11y.pdf.audit.checks.links_navigation._link_uri_from_objr`
    walks.
    """
    obj_dict: dict[str, pikepdf.Object] = {}
    if uri is not None:
        annot = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/Annot"),
            "/Subtype": pikepdf.Name("/Link"),
            "/A": pikepdf.Dictionary({
                "/Type": pikepdf.Name("/Action"),
                "/S": pikepdf.Name("/URI"),
                "/URI": pikepdf.String(uri),
            }),
        })
        objr = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/OBJR"),
            "/Obj": annot,
        })
        obj_dict["/K"] = pikepdf.Array([objr])
    elem = StructElement(
        index=index,
        custom_tag="/Link",
        resolved_tag="Link",
        alt_text=alt_text,
        actual_text=None,
        lang=None,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=pikepdf.Dictionary(obj_dict),
    )
    elem.text_content = text_content
    return elem


def _only(results: list[CheckResult]) -> CheckResult:
    """Assert single-element list and return its sole entry."""
    assert len(results) == 1, f"expected one CheckResult, got {len(results)}"
    return results[0]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


def test_links_navigation_checks_registry_lists_all_three_functions() -> None:
    """``LINKS_NAVIGATION_CHECKS`` is the phase-5 entry point."""
    assert LINKS_NAVIGATION_CHECKS == [
        check_link_alt_text_descriptive,
        check_bookmarks_present,
        check_cross_language_link_targets_identified,
    ]


# ---------------------------------------------------------------------------
# check_link_alt_text_descriptive
# ---------------------------------------------------------------------------


def test_link_alt_descriptive_passes_when_no_links() -> None:
    res = _only(check_link_alt_text_descriptive(_ctx(_new_pdf_with_pages())))
    assert res.name == "Link alt text is descriptive"
    assert res.standard == "WCAG 2.4.4 (best practice)"
    assert res.result == "PASS"


def test_link_alt_descriptive_passes_when_link_has_no_alt_text() -> None:
    elem = _link_elem(0, alt_text=None, text_content="Read more")
    res = _only(
        check_link_alt_text_descriptive(_ctx(_new_pdf_with_pages(), [elem]))
    )
    assert res.result == "PASS"


def test_link_alt_descriptive_passes_for_descriptive_alt_text() -> None:
    elem = _link_elem(0, alt_text="Annual report 2024", text_content="here")
    res = _only(
        check_link_alt_text_descriptive(_ctx(_new_pdf_with_pages(), [elem]))
    )
    assert res.result == "PASS"
    assert "No links" in res.details


def test_link_alt_descriptive_warns_for_https_alt_text() -> None:
    elem = _link_elem(0, alt_text="https://example.com/doc.pdf")
    res = _only(
        check_link_alt_text_descriptive(_ctx(_new_pdf_with_pages(), [elem]))
    )
    assert res.result == "WARN"
    # The element's diagnostic uses 1-based index.
    assert "[1]" in res.details
    assert "https://example.com/doc.pdf" in res.details


def test_link_alt_descriptive_warns_for_mailto_alt_text() -> None:
    elem = _link_elem(0, alt_text="mailto:foo@example.org")
    res = _only(
        check_link_alt_text_descriptive(_ctx(_new_pdf_with_pages(), [elem]))
    )
    assert res.result == "WARN"
    assert "mailto" in res.details


def test_link_alt_descriptive_warns_for_www_alt_text_case_insensitive() -> None:
    elem = _link_elem(0, alt_text="WWW.Example.Com")
    res = _only(
        check_link_alt_text_descriptive(_ctx(_new_pdf_with_pages(), [elem]))
    )
    assert res.result == "WARN"


def test_link_alt_descriptive_calls_out_overriding_visible_text() -> None:
    """When alt overrides a different visible text, the message says so."""
    elem = _link_elem(
        0,
        alt_text="https://example.com",
        text_content="Read the report",
    )
    res = _only(
        check_link_alt_text_descriptive(_ctx(_new_pdf_with_pages(), [elem]))
    )
    assert res.result == "WARN"
    assert "overrides" in res.details
    assert "Read the report" in res.details


def test_link_alt_descriptive_skips_non_link_elements() -> None:
    """Only ``Link`` resolved tags are inspected."""
    elem = StructElement(
        index=0,
        custom_tag="/P",
        resolved_tag="P",
        alt_text="https://example.com",
        actual_text=None,
        lang=None,
        children_indices=[],
        mcids=[],
        parent_index=-1,
        obj=pikepdf.Dictionary(),
    )
    res = _only(
        check_link_alt_text_descriptive(_ctx(_new_pdf_with_pages(), [elem]))
    )
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_bookmarks_present
# ---------------------------------------------------------------------------


def test_bookmarks_passes_with_populated_outlines() -> None:
    pdf = _set_outlines(_new_pdf_with_pages(20), count=5)
    res = _only(check_bookmarks_present(_ctx(pdf)))
    assert res.name == "Bookmarks present"
    assert res.standard == "PDF/UA, WCAG 2.4.5"
    assert res.result == "PASS"
    assert "outlines" in res.details


def test_bookmarks_passes_for_short_doc_without_outlines() -> None:
    """4 pages or fewer don't need bookmarks."""
    pdf = _new_pdf_with_pages(4)
    res = _only(check_bookmarks_present(_ctx(pdf)))
    assert res.result == "PASS"
    assert "not required" in res.details


def test_bookmarks_warns_for_long_doc_without_outlines() -> None:
    """5 pages or more without bookmarks WARN."""
    pdf = _new_pdf_with_pages(5)
    res = _only(check_bookmarks_present(_ctx(pdf)))
    assert res.result == "WARN"
    assert "5" in res.details


def test_bookmarks_warns_for_long_doc_with_zero_count_outlines() -> None:
    """Empty outline tree is treated identically to missing /Outlines."""
    pdf = _set_outlines(_new_pdf_with_pages(10), count=0)
    res = _only(check_bookmarks_present(_ctx(pdf)))
    assert res.result == "WARN"


def test_bookmarks_passes_for_short_doc_with_zero_count_outlines() -> None:
    pdf = _set_outlines(_new_pdf_with_pages(2), count=0)
    res = _only(check_bookmarks_present(_ctx(pdf)))
    assert res.result == "PASS"


# ---------------------------------------------------------------------------
# check_cross_language_link_targets_identified
# ---------------------------------------------------------------------------


def test_cross_lang_fails_when_lang_missing() -> None:
    """No /Lang means we can't assess — emit FAIL."""
    pdf = _new_pdf_with_pages()
    res = _only(check_cross_language_link_targets_identified(_ctx(pdf)))
    assert res.name == "Cross-language link targets identified"
    assert res.standard == "WCAG 3.1.2 (best practice)"
    assert res.result == "FAIL"
    assert "Cannot assess" in res.details


def test_cross_lang_fails_for_non_en_fr_doc_lang() -> None:
    """Outside en/fr: FAIL (text-content inference not yet ported)."""
    pdf = _set_lang(_new_pdf_with_pages(), "de")
    res = _only(check_cross_language_link_targets_identified(_ctx(pdf)))
    assert res.result == "FAIL"


def test_cross_lang_passes_for_en_doc_with_no_links() -> None:
    pdf = _set_lang(_new_pdf_with_pages(), "en")
    res = _only(check_cross_language_link_targets_identified(_ctx(pdf)))
    assert res.result == "PASS"


def test_cross_lang_passes_for_en_doc_with_same_lang_link() -> None:
    """An English link in an English doc is fine."""
    pdf = _set_lang(_new_pdf_with_pages(), "en")
    elem = _link_elem(
        0, text_content="More info", uri="https://example.com/en/about"
    )
    res = _only(
        check_cross_language_link_targets_identified(_ctx(pdf, [elem]))
    )
    assert res.result == "PASS"


def test_cross_lang_warns_for_unflagged_french_link_in_english_doc() -> None:
    pdf = _set_lang(_new_pdf_with_pages(), "en")
    elem = _link_elem(
        0,
        text_content="More info",
        uri="https://example.com/fr/apropos",
    )
    res = _only(
        check_cross_language_link_targets_identified(_ctx(pdf, [elem]))
    )
    assert res.result == "WARN"
    assert "French" in res.details
    assert "English" in res.details
    assert "[1]" in res.details


def test_cross_lang_warns_for_unflagged_english_link_in_french_doc() -> None:
    pdf = _set_lang(_new_pdf_with_pages(), "fr")
    elem = _link_elem(
        0,
        text_content="Plus d'info",
        uri="https://example.com/en/about",
    )
    res = _only(
        check_cross_language_link_targets_identified(_ctx(pdf, [elem]))
    )
    assert res.result == "WARN"


def test_cross_lang_passes_when_link_text_warns_about_language() -> None:
    """Mentioning 'in English' in the link text counts as a warning."""
    pdf = _set_lang(_new_pdf_with_pages(), "fr")
    elem = _link_elem(
        0,
        text_content="Read more (in English)",
        uri="https://example.com/en/about",
    )
    res = _only(
        check_cross_language_link_targets_identified(_ctx(pdf, [elem]))
    )
    assert res.result == "PASS"


def test_cross_lang_passes_when_alt_text_warns_about_language() -> None:
    """Warning copy in alt text is also accepted."""
    pdf = _set_lang(_new_pdf_with_pages(), "en")
    elem = _link_elem(
        0,
        alt_text="French version",
        text_content="Plus d'info",
        uri="https://example.com/fr/about",
    )
    res = _only(
        check_cross_language_link_targets_identified(_ctx(pdf, [elem]))
    )
    assert res.result == "PASS"


def test_cross_lang_skips_mailto_and_tel_links() -> None:
    """Mail/phone schemes never carry resource language."""
    pdf = _set_lang(_new_pdf_with_pages(), "en")
    elems = [
        _link_elem(0, text_content="Email", uri="mailto:foo@example.com"),
        _link_elem(1, text_content="Call", uri="tel:+1-555-1234"),
    ]
    res = _only(
        check_cross_language_link_targets_identified(_ctx(pdf, elems))
    )
    assert res.result == "PASS"


def test_cross_lang_skips_links_with_no_uri() -> None:
    """Links without an action URI are silently skipped."""
    pdf = _set_lang(_new_pdf_with_pages(), "en")
    elem = _link_elem(0, text_content="link with no URI")  # no uri kwarg
    res = _only(
        check_cross_language_link_targets_identified(_ctx(pdf, [elem]))
    )
    assert res.result == "PASS"


def test_cross_lang_skips_urls_without_language_marker() -> None:
    """A URL with no /en/ or /fr/ pattern doesn't trigger the warn path."""
    pdf = _set_lang(_new_pdf_with_pages(), "en")
    elem = _link_elem(
        0, text_content="Other site", uri="https://example.com/about"
    )
    res = _only(
        check_cross_language_link_targets_identified(_ctx(pdf, [elem]))
    )
    assert res.result == "PASS"


def test_cross_lang_collapses_lang_tag_to_two_chars() -> None:
    """``en-CA`` is treated identically to ``en``."""
    pdf = _set_lang(_new_pdf_with_pages(), "en-CA")
    elem = _link_elem(
        0,
        text_content="More info",
        uri="https://example.com/fr/apropos",
    )
    res = _only(
        check_cross_language_link_targets_identified(_ctx(pdf, [elem]))
    )
    assert res.result == "WARN"


def test_cross_lang_treats_blank_lang_as_missing() -> None:
    pdf = _set_lang(_new_pdf_with_pages(), "   ")
    res = _only(check_cross_language_link_targets_identified(_ctx(pdf)))
    assert res.result == "FAIL"
    assert "Cannot assess" in res.details
