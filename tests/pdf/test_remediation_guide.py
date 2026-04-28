"""Tests for the ported pdfMax remediation guide.

Verifies that:

* Every :data:`auto_a11y.pdf.translation.check_mapper.CHECK_CATALOGUE`
  ``stable_id`` has an entry in
  :data:`auto_a11y.pdf.translation.remediation_guide.STABLE_ID_TO_REMEDIATION_KEY`.
* Every Fluent message id in the mapping appears in
  :file:`auto_a11y/web/translations/en/pdf-remediation.ftl`.
* The French file lists the same Fluent ids as the English file and
  carries the ``TODO_FR`` placeholder marker.
* A small smoke-test sample of EN messages contains the canonical
  section headers we render.
"""
from __future__ import annotations

import random
from pathlib import Path

from auto_a11y.pdf.translation.check_mapper import CHECK_CATALOGUE
from auto_a11y.pdf.translation.remediation_guide import (
    STABLE_ID_TO_REMEDIATION_KEY,
    STABLE_IDS_WITHOUT_REMEDIATION,
)

# tests/pdf/test_remediation_guide.py -> repo root
_TRANSLATIONS = Path(__file__).resolve().parents[2] / "auto_a11y" / "web" / "translations"
_EN_FTL = _TRANSLATIONS / "en" / "pdf-remediation.ftl"
_FR_FTL = _TRANSLATIONS / "fr" / "pdf-remediation.ftl"


def _extract_ids(path: Path) -> set[str]:
    """Return the set of top-level Fluent message ids declared in *path*.

    A Fluent message id starts at column 0 (no leading whitespace) and
    is followed by ``=``. Lines starting with ``#`` are comments;
    indented lines are continuation/value/attribute lines.
    """
    ids: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        if raw_line[0].isspace():
            continue
        if "=" not in raw_line:
            continue
        # Strip an inline comment off the LHS just in case (shouldn't happen
        # with the generator, but defensive).
        head = raw_line.split("=", 1)[0].strip()
        if head and not head.startswith("."):
            ids.add(head)
    return ids


def test_every_catalogue_stable_id_appears_in_mapping() -> None:
    """Every CHECK_CATALOGUE row's stable_id is in STABLE_ID_TO_REMEDIATION_KEY."""
    catalogue_ids = {row["stable_id"] for row in CHECK_CATALOGUE}
    mapping_ids = set(STABLE_ID_TO_REMEDIATION_KEY)
    missing = catalogue_ids - mapping_ids
    assert not missing, f"Stable IDs missing from mapping: {sorted(missing)}"


def test_mapping_only_covers_catalogue_stable_ids() -> None:
    """The mapping has no entries beyond the catalogue's stable_ids."""
    catalogue_ids = {row["stable_id"] for row in CHECK_CATALOGUE}
    extra = set(STABLE_ID_TO_REMEDIATION_KEY) - catalogue_ids
    assert not extra, f"Mapping has stable IDs not in catalogue: {sorted(extra)}"


def test_remediation_keys_follow_pdf_remediation_prefix() -> None:
    """Every Fluent id in the mapping uses the ``pdf-remediation-`` prefix."""
    bad = {sid: key for sid, key in STABLE_ID_TO_REMEDIATION_KEY.items()
           if key != f"pdf-remediation-{sid}"}
    assert not bad, f"Mapping has malformed Fluent ids: {bad}"


def test_every_remediation_key_appears_in_en_ftl() -> None:
    """Every Fluent id in STABLE_ID_TO_REMEDIATION_KEY is declared in en/pdf-remediation.ftl."""
    en_ids = _extract_ids(_EN_FTL)
    missing = sorted(set(STABLE_ID_TO_REMEDIATION_KEY.values()) - en_ids)
    assert not missing, (
        f"Fluent IDs missing from en/pdf-remediation.ftl: {missing[:5]} "
        f"(total missing: {len(missing)})"
    )


def test_fr_ftl_has_same_ids_as_en() -> None:
    """fr/pdf-remediation.ftl declares the same Fluent ids as en/."""
    en_ids = _extract_ids(_EN_FTL)
    fr_ids = _extract_ids(_FR_FTL)
    assert en_ids == fr_ids, (
        f"FR is missing {sorted(en_ids - fr_ids)[:5]}; "
        f"FR has extra {sorted(fr_ids - en_ids)[:5]}"
    )


def test_fr_ftl_has_todo_marker() -> None:
    """fr/pdf-remediation.ftl declares its placeholder status."""
    fr_text = _FR_FTL.read_text(encoding="utf-8")
    assert "TODO_FR" in fr_text, (
        "fr/pdf-remediation.ftl is missing the TODO_FR header — "
        "the placeholder marker is what blocks release until human "
        "translation lands."
    )


def test_gap_set_matches_todo_remediation_messages() -> None:
    """Every stable_id in STABLE_IDS_WITHOUT_REMEDIATION has a TODO_REMEDIATION value."""
    en_text = _EN_FTL.read_text(encoding="utf-8")
    for stable_id in STABLE_IDS_WITHOUT_REMEDIATION:
        marker = f"pdf-remediation-{stable_id} ="
        idx = en_text.find(marker)
        assert idx != -1, f"Gap stable_id {stable_id!r} not found in EN ftl"
        # Look at the next ~400 chars for TODO_REMEDIATION
        snippet = en_text[idx:idx + 400]
        assert "TODO_REMEDIATION" in snippet, (
            f"Stable id {stable_id!r} is in STABLE_IDS_WITHOUT_REMEDIATION "
            f"but its EN message doesn't contain TODO_REMEDIATION."
        )


def test_smoke_random_messages_contain_section_headers() -> None:
    """A random sample of non-gap EN messages contains the canonical headers.

    Picks 3 stable ids that DO have remediation guidance (i.e. not in
    :data:`STABLE_IDS_WITHOUT_REMEDIATION`) and asserts each message
    contains either ``Why it matters`` or ``How to fix`` — both of
    which the generator emits for every non-placeholder entry.
    """
    en_text = _EN_FTL.read_text(encoding="utf-8")
    candidates = [
        sid for sid in STABLE_ID_TO_REMEDIATION_KEY
        if sid not in STABLE_IDS_WITHOUT_REMEDIATION
    ]
    # Deterministic seed so the test is reproducible across runs.
    rng = random.Random(0xC0FFEE)
    sample = rng.sample(candidates, 3)
    for stable_id in sample:
        message_id = STABLE_ID_TO_REMEDIATION_KEY[stable_id]
        idx = en_text.find(f"{message_id} =")
        assert idx != -1, f"Message {message_id} not found in EN ftl"
        # Read a generous window of text after the message id.
        block = en_text[idx:idx + 4000]
        assert ("Why it matters" in block) or ("How to fix" in block), (
            f"Message {message_id} contains neither 'Why it matters' nor "
            f"'How to fix' — looks like a placeholder, but it's not in the "
            f"gap set."
        )


def test_en_ftl_loads_with_fluent_bundle() -> None:
    """The generated EN file parses cleanly under FluentBundle.

    Picks a known message and asserts ``format()`` returns no errors —
    catches malformed ``{`` placeables, bad indentation, etc.
    """
    from fluent_compiler.bundle import FluentBundle

    bundle = FluentBundle.from_files("en", [str(_EN_FTL)], use_isolating=False)
    # Pick the canonical first entry — it has the full structured shape.
    value, errors = bundle.format("pdf-remediation-PdfErrDocumentTitleNotSet")
    assert errors == [], f"Fluent reported parse/format errors: {errors}"
    assert value is not None and "Why it matters" in value
