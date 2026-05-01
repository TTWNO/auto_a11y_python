"""Tests for the additional PDF Fluent files (Task 7.2).

Covers ``pdf.ftl``, ``pdf-touchpoints.ftl``, ``pdf-progress.ftl``,
``pdf-errors.ftl``, and ``pdf-status.ftl`` in both ``en/`` and ``fr/``.

These files are authored by hand (unlike ``pdf-checks.ftl`` which is
generator-driven), so the tests assert the same parity invariants the
generator gives us for free: identical ID set across locales, the
French file carries the ``TODO_FR`` placeholder marker, and every
runtime stage / status / touchpoint that needs a Fluent ID has one.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TRANSLATIONS = Path(__file__).resolve().parents[2] / "auto_a11y" / "web" / "translations"

PDF_FTL_FILES: tuple[str, ...] = (
    "pdf.ftl",
    "pdf-touchpoints.ftl",
    "pdf-progress.ftl",
    "pdf-errors.ftl",
    "pdf-status.ftl",
)


def _ids(path: Path) -> set[str]:
    """Extract every top-level Fluent message ID declared in *path*.

    Skips comment lines, attribute lines, and selector branches. A
    message line looks like ``identifier = value`` at column 0.
    """
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped or "=" not in stripped:
            continue
        if stripped.startswith((".", "*", "[")):
            continue
        # Indented continuation lines are not message IDs.
        if line[:1] in (" ", "\t"):
            continue
        ident = stripped.split("=", 1)[0].strip()
        # Fluent identifiers: letters, digits, underscore, hyphen.
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", ident):
            out.add(ident)
    return out


@pytest.mark.parametrize("filename", PDF_FTL_FILES)
def test_en_and_fr_have_same_id_set(filename: str) -> None:
    """The EN and FR variants of each file declare the same IDs."""
    en = _ids(TRANSLATIONS / "en" / filename)
    fr = _ids(TRANSLATIONS / "fr" / filename)
    assert en == fr, (
        f"FR missing {sorted(en - fr)}, FR extra {sorted(fr - en)}"
    )


@pytest.mark.parametrize("filename", PDF_FTL_FILES)
def test_fr_has_todo_marker(filename: str) -> None:
    """Each FR file carries the ``TODO_FR`` placeholder marker."""
    text = (TRANSLATIONS / "fr" / filename).read_text(encoding="utf-8")
    assert "TODO_FR" in text, (
        f"fr/{filename} must include the TODO_FR header to flag that"
        " every entry is still placeholder English text."
    )


def test_pdf_progress_stages_match_pipeline() -> None:
    """Every stage emitted by ``pipeline.py`` has a Fluent ID."""
    pipeline_src = (
        Path(__file__).resolve().parents[2]
        / "auto_a11y"
        / "pdf"
        / "audit"
        / "pipeline.py"
    ).read_text(encoding="utf-8")
    stages = set(re.findall(r'_emit\(progress, "([^"]+)"', pipeline_src))
    assert stages, "Failed to extract stages from pipeline.py — regex broke?"

    en_ids = _ids(TRANSLATIONS / "en" / "pdf-progress.ftl")
    missing: list[tuple[str, str]] = []
    for stage in stages:
        ftl_id = "pdf-progress-stage-" + stage.lower().replace(" ", "-")
        if ftl_id not in en_ids:
            missing.append((stage, ftl_id))
    assert not missing, f"Stages without Fluent IDs: {missing}"


def test_pdf_status_covers_all_enum_values() -> None:
    """Every ``PdfDocumentStatus`` enum value has a Fluent ID."""
    from auto_a11y.models.pdf_document import PdfDocumentStatus

    en_ids = _ids(TRANSLATIONS / "en" / "pdf-status.ftl")
    missing: list[str] = []
    for st in PdfDocumentStatus:
        ftl_id = f"status-pdf-{st.value.replace('_', '-')}"
        if ftl_id not in en_ids:
            missing.append(ftl_id)
    assert not missing, (
        f"PdfDocumentStatus values without Fluent IDs: {missing}"
    )


def test_pdf_status_covers_page_is_pdf() -> None:
    """``PageStatus.IS_PDF`` has a dedicated Fluent ID."""
    en_ids = _ids(TRANSLATIONS / "en" / "pdf-status.ftl")
    assert "status-page-is-pdf" in en_ids


def test_pdf_touchpoints_covers_all_pdf_members() -> None:
    """All three PDF ``TouchpointID`` members have name + description."""
    en_ids = _ids(TRANSLATIONS / "en" / "pdf-touchpoints.ftl")
    expected = {
        "touchpoint-pdf-tagging-name",
        "touchpoint-pdf-tagging-description",
        "touchpoint-pdf-document-properties-name",
        "touchpoint-pdf-document-properties-description",
        "touchpoint-pdf-annotations-name",
        "touchpoint-pdf-annotations-description",
    }
    missing = expected - en_ids
    assert not missing, f"Missing touchpoint IDs: {missing}"
