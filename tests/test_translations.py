"""Tests for Fluent FTL translation quality."""
from __future__ import annotations

import os
from pathlib import Path

from fluent.syntax import parse, ast as fluent_ast

TRANSLATIONS_DIR = Path(
    os.path.dirname(__file__), os.pardir,
    'auto_a11y', 'web', 'translations',
)
EN_DIR = TRANSLATIONS_DIR / 'en'
FR_DIR = TRANSLATIONS_DIR / 'fr'


def _collect_message_ids(ftl_dir: Path) -> set[str]:
    """Collect all message IDs from .ftl files in a directory."""
    ids: set[str] = set()
    for ftl_file in sorted(ftl_dir.glob('*.ftl')):
        source = ftl_file.read_text(encoding='utf-8')
        resource = parse(source)
        for entry in resource.body:
            if isinstance(entry, fluent_ast.Message):
                ids.add(entry.id.name)
    return ids


def _pattern_has_content(pattern: fluent_ast.Pattern | None) -> bool:
    """Return True if a Fluent Pattern has any real content (text or placeables)."""
    if pattern is None:
        return False
    for elem in pattern.elements:
        if isinstance(elem, fluent_ast.Placeable):
            return True
        if elem.value.strip():
            return True
    return False


def _collect_message_values(ftl_dir: Path) -> dict[str, str | None]:
    """Collect all message IDs and their text values from .ftl files.

    A message is considered non-empty if its main value OR any of its
    attributes contain real content (text or placeables).  This correctly
    handles attribute-only messages such as issue descriptions.
    """
    entries: dict[str, str | None] = {}
    for ftl_file in sorted(ftl_dir.glob('*.ftl')):
        source = ftl_file.read_text(encoding='utf-8')
        resource = parse(source)
        for entry in resource.body:
            if isinstance(entry, fluent_ast.Message):
                # Check main value
                if entry.value is not None and _pattern_has_content(entry.value):
                    parts: list[str] = []
                    for elem in entry.value.elements:
                        if isinstance(elem, fluent_ast.TextElement):
                            parts.append(elem.value)
                        else:
                            parts.append('{…}')
                    entries[entry.id.name] = ''.join(parts)
                    continue

                # Check attributes (e.g. .title, .what, .why, …)
                has_attr_content = any(
                    _pattern_has_content(attr.value)
                    for attr in (entry.attributes or [])
                )
                if has_attr_content:
                    entries[entry.id.name] = '<attributes>'
                    continue

                # Truly empty
                entries[entry.id.name] = None
    return entries


class TestTranslationQuality:
    """Checks that catch common FTL translation problems before they reach users."""

    def test_every_en_message_has_fr_translation(self) -> None:
        """Every EN message ID must have a corresponding FR message ID."""
        en_ids = _collect_message_ids(EN_DIR)
        fr_ids = _collect_message_ids(FR_DIR)

        assert en_ids, "No EN message IDs found -- check EN FTL directory"
        assert fr_ids, "No FR message IDs found -- check FR FTL directory"

        missing = sorted(en_ids - fr_ids)
        assert not missing, (
            f"{len(missing)} EN message(s) missing from FR:\n"
            + "\n".join(f"  - {m}" for m in missing[:50])
            + (f"\n  ... and {len(missing) - 50} more" if len(missing) > 50 else "")
        )

    def test_no_empty_fr_messages(self) -> None:
        """No FR message should have a None or empty value."""
        fr_values = _collect_message_values(FR_DIR)
        assert fr_values, "No FR messages found"

        empty = [
            msg_id for msg_id, value in fr_values.items()
            if value is None or not value.strip()
        ]
        assert not empty, (
            f"{len(empty)} FR message(s) have empty values:\n"
            + "\n".join(f"  - {m}" for m in empty[:50])
        )

    def test_ftl_files_parse_without_errors(self) -> None:
        """All FTL files should parse without Junk entries."""
        failures: list[str] = []

        for label, ftl_dir in [("EN", EN_DIR), ("FR", FR_DIR)]:
            for ftl_file in sorted(ftl_dir.glob("*.ftl")):
                source = ftl_file.read_text(encoding="utf-8")
                resource = parse(source)
                junk_count = sum(
                    1 for entry in resource.body
                    if isinstance(entry, fluent_ast.Junk)
                )
                if junk_count > 0:
                    failures.append(f"{label}/{ftl_file.name}: {junk_count} Junk entry(ies)")

        assert not failures, (
            "FTL parse errors found:\n"
            + "\n".join(f"  - {fail}" for fail in failures)
        )
