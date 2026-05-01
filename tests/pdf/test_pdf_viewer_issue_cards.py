"""Issue-card detail parity tests (Part 1 of the 2026-05-01 spec)."""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
EN_PDF_FTL = REPO_ROOT / "auto_a11y" / "web" / "translations" / "en" / "pdf.ftl"
FR_PDF_FTL = REPO_ROOT / "auto_a11y" / "web" / "translations" / "fr" / "pdf.ftl"


REQUIRED_NEW_IDS = (
    "pdf-viewer-issue-element",
    "pdf-viewer-issue-document-level",
    "pdf-viewer-issue-group-count",
    "pdf-viewer-issue-view-in-report",
    "pdf-viewer-issue-view-in-report-aria",
    "pdfmax-report-jumped-to-check",
)
REMOVED_IDS = ("pdf-viewer-jump-to-page-template",)


@pytest.mark.parametrize("ftl_path", [EN_PDF_FTL, FR_PDF_FTL])
def test_required_ftl_ids_present(ftl_path: Path) -> None:
    body = ftl_path.read_text(encoding="utf-8")
    for ftl_id in REQUIRED_NEW_IDS:
        assert f"\n{ftl_id}" in f"\n{body}", (
            f"missing Fluent ID {ftl_id!r} in {ftl_path}"
        )


@pytest.mark.parametrize("ftl_path", [EN_PDF_FTL, FR_PDF_FTL])
def test_removed_ftl_ids_absent(ftl_path: Path) -> None:
    body = ftl_path.read_text(encoding="utf-8")
    for ftl_id in REMOVED_IDS:
        assert f"\n{ftl_id} " not in f"\n{body}" and f"\n{ftl_id}\n" not in f"\n{body}", (
            f"dead Fluent ID {ftl_id!r} still present in {ftl_path}"
        )
