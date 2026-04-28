"""Phase 7.4: consolidated translation coverage tests.

Asserts every CHECK_CATALOGUE row has all six required Fluent IDs in both
locales, and that no orphan IDs exist in the per-check files.

The release-gating "FR has been human-translated" check is implemented
via test_fr_translations_are_genuinely_translated. It is currently marked
xfail because the FR pdf-* files are placeholder English text under a
TODO_FR header. The xfail flips to a real failure when the placeholder
header is removed — which a human translator MUST do as part of any FR
translation pass. CI will then enforce that the file is no longer
placeholder.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_a11y.pdf.translation.check_mapper import CHECK_CATALOGUE

TRANSLATIONS = Path(__file__).parents[2] / "auto_a11y" / "web" / "translations"

_REQUIRED_CHECKS_SUFFIXES = ("name", "short-title", "what", "why", "who")


def _ids(path: Path) -> set[str]:
    """Extract Fluent message IDs from a .ftl file (top-level, not attributes)."""
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped or "=" not in stripped:
            continue
        if stripped.startswith((".", "*", "[", "{")):
            continue
        out.add(stripped.split("=", 1)[0].strip())
    return out


@pytest.mark.parametrize("locale", ["en", "fr"])
@pytest.mark.parametrize("suffix", _REQUIRED_CHECKS_SUFFIXES)
def test_every_catalogue_row_has_pdf_checks_id(locale: str, suffix: str) -> None:
    ftl_path = TRANSLATIONS / locale / "pdf-checks.ftl"
    ids = _ids(ftl_path)
    missing = [
        f"pdf-check-{row['stable_id']}-{suffix}"
        for row in CHECK_CATALOGUE
        if f"pdf-check-{row['stable_id']}-{suffix}" not in ids
    ]
    assert not missing, (
        f"Missing {len(missing)} IDs in {locale}/pdf-checks.ftl: "
        f"{missing[:5]}"
    )


@pytest.mark.parametrize("locale", ["en", "fr"])
def test_every_catalogue_row_has_remediation_id(locale: str) -> None:
    ftl_path = TRANSLATIONS / locale / "pdf-remediation.ftl"
    ids = _ids(ftl_path)
    missing = [
        f"pdf-remediation-{row['stable_id']}"
        for row in CHECK_CATALOGUE
        if f"pdf-remediation-{row['stable_id']}" not in ids
    ]
    assert not missing, (
        f"Missing {len(missing)} IDs in {locale}/pdf-remediation.ftl: "
        f"{missing[:5]}"
    )


def test_pdf_checks_en_ftl_has_no_orphans() -> None:
    """Every pdf-check-* ID in en/pdf-checks.ftl maps to a CHECK_CATALOGUE row."""
    expected = {
        f"pdf-check-{r['stable_id']}-{s}"
        for r in CHECK_CATALOGUE
        for s in _REQUIRED_CHECKS_SUFFIXES
    }
    en_ids = {
        i for i in _ids(TRANSLATIONS / "en" / "pdf-checks.ftl")
        if i.startswith("pdf-check-")
    }
    orphans = en_ids - expected
    assert not orphans, f"Orphan IDs not in catalogue: {sorted(orphans)[:5]}"


def test_pdf_remediation_en_ftl_has_no_orphans() -> None:
    expected = {
        f"pdf-remediation-{r['stable_id']}" for r in CHECK_CATALOGUE
    }
    en_ids = {
        i for i in _ids(TRANSLATIONS / "en" / "pdf-remediation.ftl")
        if i.startswith("pdf-remediation-")
    }
    orphans = en_ids - expected
    assert not orphans, f"Orphan remediation IDs: {sorted(orphans)[:5]}"


@pytest.mark.xfail(
    reason=(
        "FR Fluent files are placeholder English text under a TODO_FR header "
        "pending human translation. This test fails on purpose until a "
        "francophone translator removes the TODO_FR markers from every "
        "fr/pdf-*.ftl file. Untranslated FR is a release blocker per the "
        "spec — do NOT remove the xfail by skipping the assertion; remove "
        "it only when FR has been genuinely translated."
    ),
    strict=True,
)
def test_fr_translations_are_genuinely_translated() -> None:
    """When FR has been translated, none of the FR pdf-*.ftl files should
    carry the TODO_FR placeholder header.
    """
    fr_dir = TRANSLATIONS / "fr"
    fr_files = sorted(fr_dir.glob("pdf*.ftl"))
    assert fr_files, "No FR PDF Fluent files found"
    untranslated = [
        f.name
        for f in fr_files
        if "TODO_FR" in f.read_text(encoding="utf-8")
    ]
    assert not untranslated, (
        f"FR files still have TODO_FR placeholder: {untranslated}"
    )
