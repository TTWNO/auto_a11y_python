#!/usr/bin/env python3
"""
Translation Validator for Auto A11y Python (Fluent FTL format)

Validates that ALL Fluent translation files are complete and correct.
Every English message ID must have a corresponding French translation.

Checks:
  1. Every EN message ID has a corresponding FR message ID
  2. No FR messages have empty values
  3. No FTL files contain parse errors (Junk entries)

Exit code 0 = all translations valid
Exit code 1 = one or more translation issues found

Usage:
  python tests/validate_translations.py
"""

import os
import sys
from pathlib import Path

from fluent.syntax import parse, ast as fluent_ast

# ---------------------------------------------------------------------------
# Paths (relative to repo root)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

EN_DIR = REPO_ROOT / "auto_a11y" / "web" / "translations" / "en"
FR_DIR = REPO_ROOT / "auto_a11y" / "web" / "translations" / "fr"


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


def _collect_message_ids(ftl_dir: Path) -> dict[str, set[str]]:
    """Parse all .ftl files in a directory and return {filename: set_of_message_ids}."""
    result = {}
    if not ftl_dir.is_dir():
        return result

    for ftl_file in sorted(ftl_dir.glob("*.ftl")):
        source = ftl_file.read_text(encoding="utf-8")
        resource = parse(source)
        ids = set()
        for entry in resource.body:
            if isinstance(entry, fluent_ast.Message):
                ids.add(entry.id.name)
        result[ftl_file.name] = ids

    return result


def _pattern_has_content(pattern) -> bool:
    """Return True if a Fluent Pattern has any real content (text or placeables)."""
    if pattern is None:
        return False
    for elem in pattern.elements:
        if isinstance(elem, fluent_ast.Placeable):
            return True
        if isinstance(elem, fluent_ast.TextElement) and elem.value.strip():
            return True
    return False


def _collect_message_values(ftl_dir: Path) -> dict[str, dict[str, str | None]]:
    """Parse all .ftl files and return {filename: {msg_id: value_or_None}}.

    A message is considered non-empty if its main value OR any of its
    attributes contain real content (text or placeables).  This correctly
    handles attribute-only messages (e.g. issue descriptions) and values
    that consist solely of placeables (e.g. plural selectors).
    """
    result = {}
    if not ftl_dir.is_dir():
        return result

    for ftl_file in sorted(ftl_dir.glob("*.ftl")):
        source = ftl_file.read_text(encoding="utf-8")
        resource = parse(source)
        entries = {}
        for entry in resource.body:
            if isinstance(entry, fluent_ast.Message):
                # Check main value
                if _pattern_has_content(entry.value):
                    parts = []
                    for elem in entry.value.elements:
                        if isinstance(elem, fluent_ast.TextElement):
                            parts.append(elem.value)
                        elif isinstance(elem, fluent_ast.Placeable):
                            parts.append("{…}")
                    entries[entry.id.name] = "".join(parts)
                    continue

                # Check attributes (e.g. .title, .what, .why, …)
                has_attr_content = any(
                    _pattern_has_content(attr.value)
                    for attr in (entry.attributes or [])
                )
                if has_attr_content:
                    entries[entry.id.name] = "<attributes>"
                    continue

                # Truly empty
                entries[entry.id.name] = None
        result[ftl_file.name] = entries

    return result


def _check_for_junk(ftl_dir: Path) -> list[tuple[str, int]]:
    """Return list of (filename, junk_count) for files with parse errors."""
    junk_files = []
    if not ftl_dir.is_dir():
        return junk_files

    for ftl_file in sorted(ftl_dir.glob("*.ftl")):
        source = ftl_file.read_text(encoding="utf-8")
        resource = parse(source)
        junk_count = sum(
            1 for entry in resource.body
            if isinstance(entry, fluent_ast.Junk)
        )
        if junk_count > 0:
            junk_files.append((ftl_file.name, junk_count))

    return junk_files


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------

def validate_coverage(result: ValidationResult):
    """Check that every EN message ID has a corresponding FR message ID."""
    en_ids_by_file = _collect_message_ids(EN_DIR)
    fr_ids_by_file = _collect_message_ids(FR_DIR)

    if not en_ids_by_file:
        result.error(f"No EN .ftl files found in {EN_DIR}")
        return

    if not fr_ids_by_file:
        result.error(f"No FR .ftl files found in {FR_DIR}")
        return

    # Flatten to global sets
    all_en = set()
    for ids in en_ids_by_file.values():
        all_en.update(ids)

    all_fr = set()
    for ids in fr_ids_by_file.values():
        all_fr.update(ids)

    missing = sorted(all_en - all_fr)

    if missing:
        result.error(
            f"{len(missing)} EN message(s) missing from FR translations:\n"
            + "\n".join(f"  - {m}" for m in missing[:50])
            + (f"\n  ... and {len(missing) - 50} more" if len(missing) > 50 else "")
        )
    else:
        coverage = len(all_fr & all_en) / len(all_en) * 100 if all_en else 100
        print(f"  [PASS] Coverage — {len(all_en)} EN messages, {len(all_fr)} FR messages, {coverage:.1f}% coverage")


def validate_no_empty_values(result: ValidationResult):
    """Check that no FR messages have empty/None values."""
    fr_values = _collect_message_values(FR_DIR)

    empty_entries = []
    for filename, entries in fr_values.items():
        for msg_id, value in entries.items():
            if value is None or not value.strip():
                empty_entries.append(f"{filename}:{msg_id}")

    if empty_entries:
        result.error(
            f"{len(empty_entries)} FR message(s) have empty values:\n"
            + "\n".join(f"  - {e}" for e in empty_entries[:50])
            + (f"\n  ... and {len(empty_entries) - 50} more" if len(empty_entries) > 50 else "")
        )
    else:
        total = sum(len(entries) for entries in fr_values.values())
        print(f"  [PASS] No empty values — {total} FR messages all have content")


def validate_no_parse_errors(result: ValidationResult):
    """Check that no FTL files contain Junk (parse errors)."""
    for label, ftl_dir in [("EN", EN_DIR), ("FR", FR_DIR)]:
        junk_files = _check_for_junk(ftl_dir)
        if junk_files:
            details = "\n".join(f"  - {name}: {count} junk entry(ies)" for name, count in junk_files)
            result.error(f"{label} FTL files have parse errors:\n{details}")
        else:
            file_count = len(list(ftl_dir.glob("*.ftl"))) if ftl_dir.is_dir() else 0
            print(f"  [PASS] {label} FTL files — {file_count} file(s), no parse errors")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    result = ValidationResult()

    print()
    print("=" * 60)
    print("  Translation Validation (Fluent FTL)")
    print("=" * 60)
    print()

    validate_coverage(result)
    validate_no_empty_values(result)
    validate_no_parse_errors(result)

    print()

    if result.warnings:
        print("WARNINGS:")
        for w in result.warnings:
            print(f"  WARNING: {w}")
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
            print(f"  FAIL: {e}")
        print()
        print("=" * 60)
        print(f"  VALIDATION FAILED -- {len(result.errors)} error(s)")
        print("=" * 60)
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
