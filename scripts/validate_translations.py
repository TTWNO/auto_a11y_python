#!/usr/bin/env python3
"""
Translation Validator for Auto A11y Python

Validates that ALL translation files are complete and correct.
Every user-visible string must have a non-fuzzy French translation.

Checks:
  1. messages.po  — no fuzzy entries, no empty translations
  2. issue_translations_fr.json — every English error code has a complete French entry
  3. wcag_translations_fr.py — every WCAG criterion has a French translation
  4. messages.mo — compiled and not stale relative to .po

Exit code 0 = all translations valid
Exit code 1 = one or more translation issues found

Usage:
  python scripts/validate_translations.py          # full validation
  python scripts/validate_translations.py --po     # .po file only (fast, for pre-commit)
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (relative to repo root)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

PO_FILE = REPO_ROOT / "auto_a11y" / "web" / "translations" / "fr" / "LC_MESSAGES" / "messages.po"
MO_FILE = REPO_ROOT / "auto_a11y" / "web" / "translations" / "fr" / "LC_MESSAGES" / "messages.mo"

ISSUE_FR_JSON = REPO_ROOT / "auto_a11y" / "reporting" / "issue_translations_fr.json"
ISSUE_EN_PY = REPO_ROOT / "auto_a11y" / "reporting" / "issue_descriptions_enhanced.py"

WCAG_FR_PY = REPO_ROOT / "auto_a11y" / "reporting" / "wcag_translations_fr.py"

REQUIRED_ISSUE_FIELDS = {"title", "what", "why", "who", "remediation"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ValidationResult:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str):
        self.errors.append(msg)

    def warning(self, msg: str):
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def _load_module_from_file(path: Path, module_name: str):
    """Import a Python module directly from file path, bypassing package __init__."""
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. Validate messages.po
# ---------------------------------------------------------------------------

def validate_po_file(result: ValidationResult):
    """Check that every entry in messages.po has a non-empty, non-fuzzy translation."""

    if not PO_FILE.exists():
        result.error(f"PO file not found: {PO_FILE}")
        return

    try:
        from babel.messages.pofile import read_po
    except ImportError:
        result.error("babel is not installed — cannot parse .po file (pip install babel)")
        return

    with open(PO_FILE, "r", encoding="utf-8") as f:
        catalog = read_po(f)

    fuzzy_entries: list[str] = []
    empty_entries: list[str] = []

    for message in catalog:
        # Skip the header entry
        if message.id == "":
            continue

        # Check fuzzy
        if message.fuzzy:
            fuzzy_entries.append(str(message.id)[:80])

        # Check empty translation
        if isinstance(message.string, str) and not message.string.strip():
            empty_entries.append(str(message.id)[:80])
        elif isinstance(message.string, tuple):
            # Plural forms — every form must be translated
            for i, form in enumerate(message.string):
                if not form.strip():
                    empty_entries.append(f"{str(message.id)[:60]} (plural form {i})")

    if fuzzy_entries:
        result.error(
            f"messages.po: {len(fuzzy_entries)} FUZZY entries found — "
            f"these will NOT be translated at runtime!\n"
            + "\n".join(f"  - {e}" for e in fuzzy_entries)
        )

    if empty_entries:
        result.error(
            f"messages.po: {len(empty_entries)} UNTRANSLATED entries found\n"
            + "\n".join(f"  - {e}" for e in empty_entries)
        )

    if not fuzzy_entries and not empty_entries:
        total = sum(1 for m in catalog if m.id)
        print(f"  [PASS] messages.po — {total} entries, all translated, 0 fuzzy")


# ---------------------------------------------------------------------------
# 2. Validate .mo compilation
# ---------------------------------------------------------------------------

def validate_mo_file(result: ValidationResult):
    """Check that messages.mo exists and matches the current messages.po content."""

    if not PO_FILE.exists():
        return  # Already reported in validate_po_file

    if not MO_FILE.exists():
        result.error(
            f"messages.mo not found — translations are not compiled.\n"
            f"  Run: pybabel compile -f -d auto_a11y/web/translations"
        )
        return

    # Compile .po to a temp .mo and compare with the committed .mo.
    # This is reliable regardless of filesystem mtime (git checkout
    # does not preserve original timestamps, so mtime comparison
    # breaks in CI).
    import tempfile
    try:
        from babel.messages.pofile import read_po
        from babel.messages.mofile import write_mo
    except ImportError:
        result.error("babel is not installed — cannot validate .mo file (pip install babel)")
        return

    with open(PO_FILE, "r", encoding="utf-8") as f:
        catalog = read_po(f)

    with tempfile.NamedTemporaryFile(suffix=".mo", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        write_mo(tmp, catalog)

    try:
        expected = tmp_path.read_bytes()
        actual = MO_FILE.read_bytes()

        if expected != actual:
            result.error(
                f"messages.mo is STALE — compiled output does not match .po content.\n"
                f"  Run: pybabel compile -f -d auto_a11y/web/translations"
            )
        else:
            print("  [PASS] messages.mo — compiled and up to date")
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 3. Validate issue_translations_fr.json
# ---------------------------------------------------------------------------

def _extract_english_error_codes() -> set[str]:
    """Extract all error codes defined in issue_descriptions_enhanced.py."""

    if not ISSUE_EN_PY.exists():
        return set()

    source = ISSUE_EN_PY.read_text(encoding="utf-8")

    # Error codes are dictionary keys in the descriptions dict.
    # Pattern: 'ErrSomething': { or 'AI_ErrSomething': { or 'WarnSomething': { etc.
    codes = re.findall(
        r"'((?:Err|Warn|Info|Disco|AI_Err|AI_Warn|AI_Info)[A-Za-z0-9_]+)'\s*:",
        source,
    )
    return set(codes)


def validate_issue_translations(result: ValidationResult):
    """Check that every English error code has a complete French translation."""

    if not ISSUE_FR_JSON.exists():
        result.error(f"Issue translations file not found: {ISSUE_FR_JSON}")
        return

    with open(ISSUE_FR_JSON, "r", encoding="utf-8") as f:
        fr_data = json.load(f)

    # Get French main codes (exclude _what suffix entries)
    fr_codes = {k for k in fr_data if not k.endswith("_what")}

    # Get English codes
    en_codes = _extract_english_error_codes()

    if not en_codes:
        result.warning(
            "Could not extract English error codes from "
            f"{ISSUE_EN_PY} — skipping cross-reference check"
        )
    else:
        missing = en_codes - fr_codes
        if missing:
            result.error(
                f"issue_translations_fr.json: {len(missing)} error codes "
                f"have no French translation\n"
                + "\n".join(f"  - {c}" for c in sorted(missing))
            )

    # Check that each French entry has all required fields
    incomplete: list[str] = []
    empty_fields: list[str] = []
    for code, entry in fr_data.items():
        if code.endswith("_what"):
            continue
        if not isinstance(entry, dict):
            incomplete.append(f"{code} (not a dict)")
            continue
        missing_fields = REQUIRED_ISSUE_FIELDS - set(entry.keys())
        if missing_fields:
            incomplete.append(f"{code} missing: {', '.join(sorted(missing_fields))}")
        # Check for empty string values
        for field in REQUIRED_ISSUE_FIELDS:
            if field in entry and isinstance(entry[field], str) and not entry[field].strip():
                empty_fields.append(f"{code}.{field}")

    if incomplete:
        result.error(
            f"issue_translations_fr.json: {len(incomplete)} entries with missing fields\n"
            + "\n".join(f"  - {e}" for e in incomplete)
        )

    if empty_fields:
        result.error(
            f"issue_translations_fr.json: {len(empty_fields)} empty field values\n"
            + "\n".join(f"  - {e}" for e in empty_fields)
        )

    if not missing and not incomplete and not empty_fields:
        print(
            f"  [PASS] issue_translations_fr.json — "
            f"{len(fr_codes)} codes, all complete "
            f"({len(en_codes)} English codes covered)"
        )
    elif not en_codes and not incomplete and not empty_fields:
        print(f"  [PASS] issue_translations_fr.json — {len(fr_codes)} codes, all fields present")


# ---------------------------------------------------------------------------
# 4. Validate wcag_translations_fr.py
# ---------------------------------------------------------------------------

def validate_wcag_translations(result: ValidationResult):
    """Check that WCAG French translations have no empty values."""

    if not WCAG_FR_PY.exists():
        result.error(f"WCAG translations file not found: {WCAG_FR_PY}")
        return

    mod = _load_module_from_file(WCAG_FR_PY, "wcag_translations_fr")

    if not hasattr(mod, "WCAG_TRANSLATIONS_FR"):
        result.error("wcag_translations_fr.py: WCAG_TRANSLATIONS_FR dict not found")
        return

    translations = mod.WCAG_TRANSLATIONS_FR
    empty = [k for k, v in translations.items() if not v or not v.strip()]

    if empty:
        result.error(
            f"wcag_translations_fr.py: {len(empty)} entries with empty French translation\n"
            + "\n".join(f"  - {k}" for k in empty)
        )
    else:
        print(f"  [PASS] wcag_translations_fr.py — {len(translations)} criteria translated")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Validate all translation files")
    parser.add_argument(
        "--po",
        action="store_true",
        help="Only validate messages.po (fast check for pre-commit)",
    )
    args = parser.parse_args()

    result = ValidationResult()

    print()
    print("=" * 60)
    print("  Translation Validation")
    print("=" * 60)
    print()

    # Always check .po
    validate_po_file(result)
    validate_mo_file(result)

    if not args.po:
        validate_issue_translations(result)
        validate_wcag_translations(result)

    print()

    if result.warnings:
        print("WARNINGS:")
        for w in result.warnings:
            print(f"  ⚠ {w}")
        print()

    if result.ok:
        print("=" * 60)
        print("  ALL TRANSLATIONS VALID")
        print("=" * 60)
        print()
        return 0
    else:
        print("ERRORS:")
        for e in result.errors:
            print(f"  ✗ {e}")
        print()
        print("=" * 60)
        print(f"  VALIDATION FAILED — {len(result.errors)} error(s)")
        print("=" * 60)
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
