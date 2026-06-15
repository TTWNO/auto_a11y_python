"""Registration completeness for AI_* issue codes.

The 2026-06-15 fixture audit found that most ``AI_*`` fixtures referenced detection
codes the AI analyzer could never emit, and that the gap was invisible because AI
fixtures run ``ai-skipped`` without an API key. This test makes that class of drift a
hard failure: every ``AI_*`` code that a fixture expects (except the documented
DOM-redundant set) must be wired across *all* surfaces a code needs to flow cleanly
from the analyzer to bilingual reports and UI.

See ``docs/superpowers/specs/2026-06-15-ai-code-engine-implementation-design.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "Fixtures"

# Codes whose detection is reliably DOM-determinable; per the spec these are NOT wired
# as AI prompts (they are remapped to a DOM test or quarantined). They are therefore
# exempt from the AI-registration requirement below.
DOM_REDUNDANT: frozenset[str] = frozenset(
    {
        "AI_ErrMissingPageTitle",
        "AI_ErrIframeWithoutTitle",
        "AI_ErrZoomDisabled",
        "AI_ErrOrientationLocked",
        "AI_ErrTargetSizeTooSmall",
        "AI_ErrTextSpacingIssue",
    }
)


def _derive_code(stem: str) -> str:
    """Filename stem -> expected code (segment up to the first all-digit part)."""
    parts: list[str] = []
    for part in stem.split("_"):
        if part.isdigit():
            break
        parts.append(part)
    return "_".join(parts) if parts else stem


def _expected_ai_codes() -> list[str]:
    """AI_* codes referenced by fixtures that MUST be fully wired (DOM-redundant excluded)."""
    codes: set[str] = set()
    for html in FIXTURES_DIR.rglob("*.html"):
        code = _derive_code(html.stem)
        if code.startswith("AI_") and code not in DOM_REDUNDANT:
            codes.add(code)
    return sorted(codes)


EXPECTED_AI_CODES: list[str] = _expected_ai_codes()


def test_there_are_ai_codes_to_check() -> None:
    """Guard against the discovery silently finding nothing (e.g. moved fixtures)."""
    assert len(EXPECTED_AI_CODES) >= 50, EXPECTED_AI_CODES


@pytest.mark.parametrize("code", EXPECTED_AI_CODES)
def test_ai_code_emittable_by_a_prompt(code: str) -> None:
    """The analyzer can only emit a code if some prompt module names it."""
    text = (REPO_ROOT / "auto_a11y" / "ai" / "analysis_modules.py").read_text(encoding="utf-8")
    assert code in text, f"{code} is not named in any analysis_modules.py prompt -> unemittable"


@pytest.mark.parametrize("code", EXPECTED_AI_CODES)
def test_ai_code_has_touchpoint(code: str) -> None:
    """Every code must resolve to a touchpoint via the per-code map."""
    from auto_a11y.core.touchpoints import TouchpointMapper

    assert TouchpointMapper.get_touchpoint_for_error_code(code) is not None, (
        f"{code} missing from ERROR_CODE_TO_TOUCHPOINT (core/touchpoints.py)"
    )


@pytest.mark.parametrize("code", EXPECTED_AI_CODES)
def test_ai_code_in_issue_catalog(code: str) -> None:
    text = (REPO_ROOT / "ISSUE_CATALOG.md").read_text(encoding="utf-8")
    assert f"ID: {code}" in text, f"{code} has no ISSUE_CATALOG.md entry"


@pytest.mark.parametrize("code", EXPECTED_AI_CODES)
def test_ai_code_has_english_description(code: str) -> None:
    """Report-side English source of truth."""
    text = (
        REPO_ROOT / "auto_a11y" / "reporting" / "issue_descriptions_enhanced.py"
    ).read_text(encoding="utf-8")
    assert f"'{code}'" in text or f'"{code}"' in text, (
        f"{code} has no issue_descriptions_enhanced.py entry"
    )


@pytest.mark.parametrize("code", EXPECTED_AI_CODES)
def test_ai_code_has_french_report_translation(code: str) -> None:
    """Report-side French: the load-bearing surface validate_translations.py does NOT cover."""
    data: dict[str, object] = json.loads(
        (REPO_ROOT / "auto_a11y" / "reporting" / "issue_translations_fr.json").read_text(
            encoding="utf-8"
        )
    )
    assert code in data, (
        f"{code} missing from issue_translations_fr.json -> French reports leak English"
    )


@pytest.mark.parametrize("code", EXPECTED_AI_CODES)
def test_ai_code_has_ui_translations(code: str) -> None:
    """UI-side EN and FR Fluent message ids."""
    for locale in ("en", "fr"):
        ftl = (
            REPO_ROOT / "auto_a11y" / "web" / "translations" / locale / "issues.ftl"
        ).read_text(encoding="utf-8")
        assert f"{code} =" in ftl, f"{code} missing from {locale}/issues.ftl"
