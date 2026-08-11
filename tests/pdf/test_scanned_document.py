"""What the audit says about a purely scanned PDF.

A scan is a page-sized bitmap with no text layer, no tags and no declared
language: a screen reader can read nothing from it at all. It is also the
document most likely to be flattered by a naive audit, because almost
every structural check has nothing to examine and so has nothing to
complain about.

These tests pin the two things that must be true of such a document: the
faults that genuinely apply are reported as failures, and the checks that
had nothing to look at are not counted as passes.
"""
from __future__ import annotations

import zlib
from pathlib import Path
from typing import cast

import pikepdf
import pytest

from auto_a11y.pdf.audit.pipeline import run_audit
from auto_a11y.pdf.models import AuditResult

_PAGE_W, _PAGE_H = 300, 400


@pytest.fixture(scope="module")
def scanned_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A one-page PDF containing only a bitmap — no text, tags or /Lang."""
    out = tmp_path_factory.mktemp("scanned") / "scanned.pdf"
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(_PAGE_W, _PAGE_H))

    # Grey noise stands in for scanned ink; the content is irrelevant, the
    # point is that the page's only content is an image XObject.
    raw = bytes((x * 7 + y * 13) % 256 for y in range(_PAGE_H) for x in range(_PAGE_W))
    image = pikepdf.Stream(pdf, zlib.compress(raw))
    image.Type = pikepdf.Name("/XObject")
    image.Subtype = pikepdf.Name("/Image")
    image.Width = _PAGE_W
    image.Height = _PAGE_H
    image.ColorSpace = pikepdf.Name("/DeviceGray")
    image.BitsPerComponent = 8
    image.Filter = pikepdf.Name("/FlateDecode")

    page[pikepdf.Name("/Resources")] = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(image))
    )
    page[pikepdf.Name("/Contents")] = pikepdf.Stream(
        pdf, f"q {_PAGE_W} 0 0 {_PAGE_H} 0 0 cm /Im0 Do Q".encode("ascii")
    )
    pdf.save(out)
    return out


@pytest.fixture(scope="module")
def scanned_audit(scanned_pdf: Path) -> AuditResult:
    return run_audit(scanned_pdf)


def _verdict(audit: AuditResult, name: str) -> str | None:
    for check in audit.check_results:
        if check.name == name:
            return check.result
    return None


def test_scanned_pdf_fails_tagging(scanned_audit: AuditResult) -> None:
    assert _verdict(scanned_audit, "PDF is tagged") == "FAIL"
    assert _verdict(scanned_audit, "Structure tree exists") == "FAIL"


def test_scanned_pdf_fails_language_when_undeclared(
    scanned_audit: AuditResult,
) -> None:
    # No /Lang in the catalog and no text to derive one from.
    assert _verdict(scanned_audit, "Document language set") == "FAIL"
    assert scanned_audit.declared_lang is None


def test_scanned_pdf_is_not_mostly_passes(scanned_audit: AuditResult) -> None:
    """The regression this file exists for.

    Before N/A existed this document reported 88 passes out of 105 — every
    verdict defensible, the total a lie. Passes must stay a minority of the
    checks that ran, or the report reads as a clean bill of health for a
    document no screen reader can read.
    """
    total = len(scanned_audit.check_results)
    assert scanned_audit.na_count > 0, "vacuous checks must be reported as N/A"
    assert scanned_audit.pass_count < total / 2, (
        f"{scanned_audit.pass_count} of {total} checks passed on a document "
        "with no text, tags or language"
    )


def test_counts_account_for_every_check(scanned_audit: AuditResult) -> None:
    assert (
        scanned_audit.fail_count
        + scanned_audit.warn_count
        + scanned_audit.pass_count
        + scanned_audit.info_count
        + scanned_audit.na_count
    ) == len(scanned_audit.check_results)


def test_not_applicable_checks_never_become_violations(
    scanned_audit: AuditResult,
) -> None:
    """N/A is not a fault, so it must not reach the user's issue list."""
    from auto_a11y.pdf.translation.check_mapper import to_violation

    for check in scanned_audit.check_results:
        if check.result == "NA":
            assert to_violation(check, pdf_doc_id="doc123") is None


def test_criteria_with_only_inapplicable_checks_are_not_claimed_as_pass(
    scanned_audit: AuditResult,
) -> None:
    """A WCAG criterion whose every check was N/A is untested, not passed.

    Rolling those up to PASS would assert conformance the audit never
    established — the same overstatement one level up.
    """
    # report_sections is a JSON-shaped dict[str, object] by contract, so
    # the element types are narrowed with concrete casts — matching how
    # auto_a11y.web.routes.pdf reads the same payload.
    mapping = cast(
        "dict[str, object]", scanned_audit.report_sections["wcag_mapping"]
    )
    rows = cast("list[dict[str, object]]", mapping["rows"])

    by_name = {c.name: c.result for c in scanned_audit.check_results}
    for row in rows:
        if row.get("verdict") != "PASS":
            continue
        checks = cast("list[object]", row.get("checks", []))
        outcomes = [by_name.get(str(name)) for name in checks]
        assert any(outcome != "NA" for outcome in outcomes), (
            f"criterion {row.get('criterion')} claimed PASS on N/A checks only"
        )


def test_a_scanned_page_is_reported_as_untagged_content(
    scanned_audit: AuditResult,
) -> None:
    """The check that finally names what is wrong with a scan.

    Every other failure on this document describes a missing declaration
    — no title, no language, no structure tree. This one describes the
    content itself: there is an image on the page that belongs to neither
    category, and no text anywhere.

    pdfMax's equivalent counts only text operators, so a page with one
    untagged image and no text satisfies it. That is every page of every
    scanned document, which is the class of file this check exists for.
    """
    assert _verdict(scanned_audit, "All content is tagged or artifact") == "FAIL"


def test_the_scan_reports_an_image_rather_than_text(
    scanned_audit: AuditResult,
) -> None:
    detail = next(
        c.details for c in scanned_audit.check_results
        if c.name == "All content is tagged or artifact"
    )

    assert "image" in detail
    assert "page(s) 1" in detail


def test_the_scan_is_named_as_a_scan(scanned_audit: AuditResult) -> None:
    """The finding neither tool made before.

    Every other failure describes something missing from the document.
    This one says what the document *is* — and that OCR has to happen
    before any of the others can be corrected, so a reader working the
    report top to bottom does not tag a picture.
    """
    assert _verdict(scanned_audit, "Document has a text layer") == "FAIL"

    detail = next(
        c.details for c in scanned_audit.check_results
        if c.name == "Document has a text layer"
    )
    assert "scan" in detail
    assert "optical character recognition" in detail
