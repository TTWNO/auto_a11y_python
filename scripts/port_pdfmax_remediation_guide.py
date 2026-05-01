"""Port pdfMax's REMEDIATION_GUIDE dict to auto_a11y Fluent files + mapping.

One-shot script. Re-run when pdfMax adds checks; commit the generated files.

Usage::

    python scripts/port_pdfmax_remediation_guide.py \\
        --source ~/Documents/cnib/code/pdfMax/python/checker

The script reads ``remediation_guide.py`` and ``reference_urls.py`` from the
given source directory (using :mod:`importlib.util` to avoid sys.path
pollution), then walks :data:`auto_a11y.pdf.translation.check_mapper.CHECK_CATALOGUE`
to emit:

* ``auto_a11y/web/translations/en/pdf-remediation.ftl`` — one Fluent message
  per ``stable_id`` containing the structured remediation guidance flattened
  into a single multi-line value with section headers.
* ``auto_a11y/web/translations/fr/pdf-remediation.ftl`` — placeholder English
  text under the same IDs, with a stark ``TODO_FR`` header so the file is
  obviously incomplete until a francophone translates it.
* ``auto_a11y/pdf/translation/remediation_guide.py`` — typed mapping table
  ``STABLE_ID_TO_REMEDIATION_KEY`` plus ``STABLE_IDS_WITHOUT_REMEDIATION`` for
  audit-engine checks that don't yet have pdfMax remediation entries.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Mapping, cast

from auto_a11y.pdf.translation.check_mapper import CHECK_CATALOGUE

logger = logging.getLogger(__name__)

# Repo root computed from this file's location: scripts/port_…/.. == repo
_REPO_ROOT = Path(__file__).resolve().parents[1]

_EN_FTL_PATH = _REPO_ROOT / "auto_a11y" / "web" / "translations" / "en" / "pdf-remediation.ftl"
_FR_FTL_PATH = _REPO_ROOT / "auto_a11y" / "web" / "translations" / "fr" / "pdf-remediation.ftl"
_MAPPING_PY_PATH = _REPO_ROOT / "auto_a11y" / "pdf" / "translation" / "remediation_guide.py"


# ---------------------------------------------------------------------------
# pdfMax source loading
# ---------------------------------------------------------------------------

def _load_module_from_path(name: str, path: Path) -> types.ModuleType:
    """Load a module from a filesystem path without polluting ``sys.path``.

    Uses :func:`importlib.util.spec_from_file_location` so the module is
    available under *name* in :data:`sys.modules` (some sibling pdfMax
    modules expect to find each other via ``import``-by-name).
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        msg = f"Could not load module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_pdfmax_remediation(source_dir: Path) -> Mapping[str, Mapping[str, object]]:
    """Load pdfMax's ``REMEDIATION_GUIDE`` dict from *source_dir*.

    pdfMax's :file:`remediation_guide.py` does ``from reference_urls import
    REFERENCE_URLS`` at import time, so we must register
    :mod:`reference_urls` in :data:`sys.modules` before loading the
    remediation module. Both modules are loaded by file path, not via
    ``sys.path`` mutation.
    """
    refs_path = source_dir / "reference_urls.py"
    rem_path = source_dir / "remediation_guide.py"
    if not refs_path.exists():
        msg = f"reference_urls.py not found in {source_dir}"
        raise FileNotFoundError(msg)
    if not rem_path.exists():
        msg = f"remediation_guide.py not found in {source_dir}"
        raise FileNotFoundError(msg)

    # Load reference_urls first under its bare name (the remediation
    # module's `from reference_urls import ...` will look it up in
    # sys.modules).
    _load_module_from_path("reference_urls", refs_path)
    rem_module = _load_module_from_path("pdfmax_remediation_guide", rem_path)

    raw = getattr(rem_module, "REMEDIATION_GUIDE", None)
    if not isinstance(raw, dict):
        msg = "pdfMax remediation_guide.py missing REMEDIATION_GUIDE dict"
        raise RuntimeError(msg)
    # Cast: we trust the upstream shape; it's a dict[str, dict[str, object]].
    return cast(Mapping[str, Mapping[str, object]], raw)


# ---------------------------------------------------------------------------
# Fluent rendering
# ---------------------------------------------------------------------------

#: Fluent indent (4 spaces) for continuation lines inside a multi-line value.
_INDENT = "    "


def _fluent_escape(text: str) -> str:
    """Escape ``{`` so Fluent doesn't try to parse it as a placeable.

    Uses the ``{"{"}`` literal-string trick — Fluent's only special
    character inside a value is the opening brace. ``}`` outside a
    placeable is literal in most positions and needs no escaping
    *except* at the start of a value line, where the line-level
    workaround in :func:`_fluent_indent_block` handles it.
    """
    return text.replace("{", '{"{"}')


#: Characters that, when they appear at the start of an indented value
#: line, trip Fluent's parser:
#:
#: * ``.`` — would start an attribute declaration (``.attr = ...``);
#: * ``[`` — would start a variant key (``[selector]``);
#: * ``*`` — would start the default variant marker (``*[default]``);
#: * ``}`` — orphaned placeable close.
#:
#: ``{`` is already escaped via :func:`_fluent_escape`. The standard
#: Fluent workaround for any of these is to prepend an empty-placeable
#: ``{""}``, which forces the line to be parsed as a continuation of
#: the message value rather than as a structural element.
_LEADING_FENCE_CHARS = (".", "[", "*", "}")


def _fluent_indent_block(text: str) -> str:
    """Indent every line of *text* with the Fluent continuation indent.

    Lines whose first non-whitespace character is one of
    :data:`_LEADING_FENCE_CHARS` get a ``{""}`` (empty-placeable) prefix
    inserted just before that character so Fluent's parser doesn't
    mistake the line for an attribute declaration, variant key, or
    orphaned closing brace.

    Empty lines are emitted as the bare continuation indent (Fluent
    requires non-empty whitespace on otherwise-empty value lines).
    """
    out: list[str] = []
    for line in text.splitlines():
        if line == "":
            out.append(_INDENT)
            continue
        # Find the first non-whitespace char — if it's one of the fence
        # chars, splice in a {""} placeable before it.
        stripped = line.lstrip()
        if stripped and stripped[0] in _LEADING_FENCE_CHARS:
            leading_ws = line[: len(line) - len(stripped)]
            line = leading_ws + '{""}' + stripped
        out.append(_INDENT + line)
    return "\n".join(out)


def _as_step_list(field: object) -> list[str]:
    """Normalize a ``fix_*`` field that may be ``str`` or ``list[str]``."""
    if isinstance(field, str):
        # Single-string entries are full sentences; treat as one step.
        return [field]
    if isinstance(field, list):
        items_list: list[object] = cast(list[object], field)
        out: list[str] = []
        for item in items_list:
            if isinstance(item, str):
                out.append(item)
            else:
                msg = f"Unexpected step entry type: {type(item).__name__}"
                raise TypeError(msg)
        return out
    msg = f"Unexpected fix-field type: {type(field).__name__}"
    raise TypeError(msg)


def _section(header: str, body: str) -> str:
    """Build a ``Header\\n\\nBody`` chunk for a remediation section."""
    return f"{header}\n\n{body}"


def _format_steps(steps: list[str]) -> str:
    """Format numbered steps as ``  1. ...`` / ``  2. ...`` lines."""
    return "\n".join(f"  {i}. {step}" for i, step in enumerate(steps, start=1))


def _render_remediation_value(entry: Mapping[str, object]) -> str:
    """Render a pdfMax ``REMEDIATION_GUIDE`` entry as a flat multi-paragraph string.

    The result is intended to be the *value* portion of a Fluent message
    (i.e. the text after ``message-id =``). It contains literal newlines
    and blank lines; the caller indents it for inclusion in a ``.ftl``
    file.
    """
    parts: list[str] = []

    why = entry.get("why_it_matters")
    if isinstance(why, str) and why:
        parts.append(_section("Why it matters", why))

    principle = entry.get("general_principle")
    if isinstance(principle, str) and principle:
        parts.append(_section("Principle", principle))

    fix_acrobat_raw = entry.get("fix_acrobat")
    if fix_acrobat_raw is not None:
        steps = _as_step_list(fix_acrobat_raw)
        if steps:
            parts.append(_section("How to fix in Adobe Acrobat Pro", _format_steps(steps)))

    fix_word_raw = entry.get("fix_word")
    if fix_word_raw is not None:
        steps = _as_step_list(fix_word_raw)
        if steps:
            parts.append(_section("How to fix in Microsoft Word", _format_steps(steps)))

    fix_indesign_raw = entry.get("fix_indesign")
    if fix_indesign_raw is not None:
        steps = _as_step_list(fix_indesign_raw)
        if steps:
            parts.append(_section("How to fix in Adobe InDesign", _format_steps(steps)))

    before_after_raw = entry.get("before_after")
    if isinstance(before_after_raw, dict):
        before_after = cast(Mapping[str, object], before_after_raw)
        before_label = before_after.get("before_label")
        before_content = before_after.get("before_content")
        after_label = before_after.get("after_label")
        after_content = before_after.get("after_content")
        explanation = before_after.get("explanation")
        if isinstance(before_label, str) and isinstance(before_content, str):
            parts.append(_section("Before", f"{before_label}\n{before_content}"))
        if isinstance(after_label, str) and isinstance(after_content, str):
            parts.append(_section("After", f"{after_label}\n{after_content}"))
        if isinstance(explanation, str) and explanation:
            parts.append(explanation)

    learn_more = entry.get("learn_more_url")
    if isinstance(learn_more, str) and learn_more:
        parts.append(f"Learn more: {learn_more}")

    return "\n\n".join(parts)


def _render_placeholder_value(stable_id: str, pdfmax_check_name: str) -> str:
    """Render a ``TODO_REMEDIATION`` placeholder value for unmapped checks."""
    return (
        f"TODO_REMEDIATION\n\n"
        f"No remediation entry found in pdfMax's REMEDIATION_GUIDE for the "
        f"check '{pdfmax_check_name}' (stable id: {stable_id}). When pdfMax "
        f"adds an entry, re-run scripts/port_pdfmax_remediation_guide.py and "
        f"commit the regenerated translation files."
    )


def _format_fluent_message(message_id: str, value: str) -> str:
    """Render a single Fluent message as ``id =\\n    value-line-1\\n    …``."""
    indented = _fluent_indent_block(_fluent_escape(value))
    return f"{message_id} =\n{indented}\n"


# ---------------------------------------------------------------------------
# Mapping module rendering
# ---------------------------------------------------------------------------

_MAPPING_TEMPLATE = '''"""Generated by scripts/port_pdfmax_remediation_guide.py.

DO NOT edit by hand. Re-run the script and commit the regenerated output.

Maps each ``CHECK_CATALOGUE`` stable id to its Fluent message id in
``auto_a11y/web/translations/{{en,fr}}/pdf-remediation.ftl``.

Templates resolve the message via :func:`auto_a11y.web.fluent.ftl`. The
mapping is enumerated explicitly (rather than computed on the fly) so
type-checkers and ``grep`` can find references statically.
"""
from __future__ import annotations

# Map stable_id -> Fluent message id. The Fluent id is always
# ``f"pdf-remediation-{{stable_id}}"`` but enumerating explicitly lets
# tests assert one-for-one correspondence with the catalogue.
STABLE_ID_TO_REMEDIATION_KEY: dict[str, str] = {{
{mapping_rows}
}}


# Stable ids whose pdfMax check name was NOT present in pdfMax's
# REMEDIATION_GUIDE at port time. The corresponding Fluent message
# carries a ``TODO_REMEDIATION`` placeholder; downstream consumers
# should treat these as audit-engine gaps to fill upstream.
STABLE_IDS_WITHOUT_REMEDIATION: frozenset[str] = frozenset({{
{gap_rows}
}})


__all__ = [
    "STABLE_ID_TO_REMEDIATION_KEY",
    "STABLE_IDS_WITHOUT_REMEDIATION",
]
'''


def _render_mapping_module(
    stable_id_to_key: dict[str, str],
    gaps: set[str],
) -> str:
    mapping_rows = "\n".join(
        f"    {sid!r}: {key!r},"
        for sid, key in sorted(stable_id_to_key.items())
    )
    if gaps:
        gap_rows = "\n".join(f"    {sid!r}," for sid in sorted(gaps))
    else:
        # Empty frozenset literal — Python doesn't have one; use the
        # "no rows" comment so the resulting frozenset({}) call is empty.
        gap_rows = "    # (empty: every catalogue stable_id has a remediation entry)"
    return _MAPPING_TEMPLATE.format(mapping_rows=mapping_rows, gap_rows=gap_rows)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _build_artifacts(
    pdfmax_remediation: Mapping[str, Mapping[str, object]],
) -> tuple[str, str, str, list[tuple[str, str]], set[str]]:
    """Return (en_ftl, fr_ftl, mapping_py, unmapped_pdfmax_keys, gaps)."""
    en_messages: list[str] = []
    stable_id_to_key: dict[str, str] = {}
    gaps: set[str] = set()
    used_pdfmax_keys: set[str] = set()

    seen_stable_ids: set[str] = set()
    for row in CHECK_CATALOGUE:
        stable_id = row["stable_id"]
        if stable_id in seen_stable_ids:
            # Catalogue already enforces uniqueness, but defend anyway
            continue
        seen_stable_ids.add(stable_id)

        pdfmax_name = row["pdfmax_check_name"]
        message_id = f"pdf-remediation-{stable_id}"
        stable_id_to_key[stable_id] = message_id

        entry = pdfmax_remediation.get(pdfmax_name)
        if entry is None:
            gaps.add(stable_id)
            value = _render_placeholder_value(stable_id, pdfmax_name)
            logger.warning(
                "No pdfMax remediation entry for %s (stable id: %s) — emitting TODO_REMEDIATION placeholder.",
                pdfmax_name,
                stable_id,
            )
        else:
            used_pdfmax_keys.add(pdfmax_name)
            value = _render_remediation_value(entry)

        en_messages.append(_format_fluent_message(message_id, value))

    en_header = (
        "# PDF remediation guidance — English\n"
        "# Auto-generated by scripts/port_pdfmax_remediation_guide.py from\n"
        "# pdfMax's REMEDIATION_GUIDE. DO NOT edit by hand — re-run the porter\n"
        "# and commit the regenerated file.\n"
        "\n"
    )
    en_ftl = en_header + "\n".join(en_messages)

    timestamp = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    fr_header = (
        "### TODO_FR ###\n"
        "### This file contains placeholder ENGLISH text for every Fluent ID.\n"
        "### Each entry MUST be translated by a human francophone before this\n"
        "### file is considered complete. The Phase 7.4 coverage test (pending)\n"
        "### will refuse to pass while any English placeholder remains.\n"
        "###\n"
        f"### Generated: {timestamp}\n"
        "###\n"
        "\n"
    )
    fr_ftl = fr_header + "\n".join(en_messages)

    mapping_py = _render_mapping_module(stable_id_to_key, gaps)

    unmapped_pdfmax_keys = sorted(set(pdfmax_remediation) - used_pdfmax_keys)
    unmapped_pairs = [(k, "pdfmax-only") for k in unmapped_pdfmax_keys]

    return en_ftl, fr_ftl, mapping_py, unmapped_pairs, gaps


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote %s (%d bytes)", path, len(content))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help=(
            "Path to pdfMax's python/checker directory (containing "
            + "remediation_guide.py and reference_urls.py)."
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    source_dir = args.source.expanduser().resolve()
    logger.info("Loading pdfMax remediation guide from %s", source_dir)
    pdfmax_remediation = _load_pdfmax_remediation(source_dir)
    logger.info("Loaded %d pdfMax remediation entries", len(pdfmax_remediation))

    en_ftl, fr_ftl, mapping_py, unmapped_pdfmax, gaps = _build_artifacts(pdfmax_remediation)

    _write(_EN_FTL_PATH, en_ftl)
    _write(_FR_FTL_PATH, fr_ftl)
    _write(_MAPPING_PY_PATH, mapping_py)

    logger.info(
        "Catalogue stable ids covered: %d (gaps: %d)",
        len({row["stable_id"] for row in CHECK_CATALOGUE}),
        len(gaps),
    )
    if gaps:
        logger.warning(
            "Stable ids without pdfMax remediation entries (%d): %s",
            len(gaps),
            ", ".join(sorted(gaps)),
        )
    if unmapped_pdfmax:
        logger.info(
            "pdfMax remediation entries with no matching CHECK_CATALOGUE row (%d): %s",
            len(unmapped_pdfmax),
            ", ".join(k for k, _ in unmapped_pdfmax),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
