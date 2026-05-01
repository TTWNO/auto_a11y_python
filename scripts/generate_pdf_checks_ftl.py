"""Generate ``pdf-checks.ftl`` files from CHECK_CATALOGUE + pdfMax's REMEDIATION_GUIDE.

One-shot script. Re-run when the catalogue or pdfMax remediation guide
changes; commit the regenerated translation files.

For every row in :data:`auto_a11y.pdf.translation.check_mapper.CHECK_CATALOGUE`,
emits five Fluent message IDs:

* ``pdf-check-{stable_id}-name`` — short label used in result lists.
* ``pdf-check-{stable_id}-short-title`` — even-shorter alias for cards/badges.
* ``pdf-check-{stable_id}-what`` — 1–2 sentences describing the issue.
* ``pdf-check-{stable_id}-why`` — 1–2 sentences on user impact.
* ``pdf-check-{stable_id}-who`` — 1 sentence identifying affected users.

Sources for the message bodies:

* The pdfMax check name from the catalogue row → ``-name`` and ``-short-title``.
* The pdfMax ``REMEDIATION_GUIDE[name]['why_it_matters']`` text, split
  on sentence boundaries → ``-what`` and ``-why``.
* A small static table mapping WCAG criteria to affected-user phrases →
  ``-who``.

Catalogue rows whose pdfMax check name has no remediation entry get a
``TODO_AUTHOR`` placeholder and are listed in
:data:`STABLE_IDS_WITHOUT_FULL_AUTHORING` for manual follow-up.

Usage::

    python scripts/generate_pdf_checks_ftl.py \\
        --source ~/Documents/cnib/code/pdfMax/python/checker

The script writes:

* ``auto_a11y/web/translations/en/pdf-checks.ftl``
* ``auto_a11y/web/translations/fr/pdf-checks.ftl`` (placeholder English text
  under a ``TODO_FR`` header pending human translation).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import logging
import re
import sys
import types
from pathlib import Path
from typing import Mapping, cast

from auto_a11y.pdf.translation.check_mapper import CHECK_CATALOGUE, CatalogueRow

logger = logging.getLogger(__name__)

# Repo root computed from this file's location: scripts/generate_…/.. == repo
_REPO_ROOT = Path(__file__).resolve().parents[1]

_EN_FTL_PATH = _REPO_ROOT / "auto_a11y" / "web" / "translations" / "en" / "pdf-checks.ftl"
_FR_FTL_PATH = _REPO_ROOT / "auto_a11y" / "web" / "translations" / "fr" / "pdf-checks.ftl"


# ---------------------------------------------------------------------------
# pdfMax source loading (mirrors scripts/port_pdfmax_remediation_guide.py)
# ---------------------------------------------------------------------------


def _load_module_from_path(name: str, path: Path) -> types.ModuleType:
    """Load a module from a filesystem path without polluting ``sys.path``."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        msg = f"Could not load module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_pdfmax_remediation(source_dir: Path) -> Mapping[str, Mapping[str, object]]:
    """Load pdfMax's ``REMEDIATION_GUIDE`` dict from *source_dir*."""
    refs_path = source_dir / "reference_urls.py"
    rem_path = source_dir / "remediation_guide.py"
    if not refs_path.exists():
        msg = f"reference_urls.py not found in {source_dir}"
        raise FileNotFoundError(msg)
    if not rem_path.exists():
        msg = f"remediation_guide.py not found in {source_dir}"
        raise FileNotFoundError(msg)

    _load_module_from_path("reference_urls", refs_path)
    rem_module = _load_module_from_path("pdfmax_remediation_guide", rem_path)

    raw = getattr(rem_module, "REMEDIATION_GUIDE", None)
    if not isinstance(raw, dict):
        msg = "pdfMax remediation_guide.py missing REMEDIATION_GUIDE dict"
        raise RuntimeError(msg)
    return cast(Mapping[str, Mapping[str, object]], raw)


# ---------------------------------------------------------------------------
# Fluent rendering — same fence-escaping pattern as Task 7.1.
# ---------------------------------------------------------------------------


_INDENT = "    "

_LEADING_FENCE_CHARS = (".", "[", "*", "}")


def _fluent_escape(text: str) -> str:
    """Escape ``{`` so Fluent doesn't try to parse it as a placeable."""
    return text.replace("{", '{"{"}')


def _fluent_indent_block(text: str) -> str:
    """Indent every line of *text* with the Fluent continuation indent.

    Lines beginning with one of :data:`_LEADING_FENCE_CHARS` get a
    ``{""}`` (empty-placeable) prefix to defeat Fluent's parser
    confusing them with attribute or variant declarations.
    """
    out: list[str] = []
    for line in text.splitlines():
        if line == "":
            out.append(_INDENT)
            continue
        stripped = line.lstrip()
        if stripped and stripped[0] in _LEADING_FENCE_CHARS:
            leading_ws = line[: len(line) - len(stripped)]
            line = leading_ws + '{""}' + stripped
        out.append(_INDENT + line)
    return "\n".join(out)


def _format_ftl_message(message_id: str, value: str) -> str:
    """Render a single Fluent message as ``id =\\n    value-line-1\\n    …``.

    For single-line values that fit on one physical line and don't start
    with a fence char, we still use the indented multi-line form so the
    file format is uniform. (Fluent permits both.)
    """
    indented = _fluent_indent_block(_fluent_escape(value))
    return f"{message_id} =\n{indented}\n"


# ---------------------------------------------------------------------------
# WCAG → affected-users mapping
# ---------------------------------------------------------------------------


#: Map a WCAG criterion (X.Y or X.Y.Z) to an affected-users phrase used
#: as the ``-who`` body when no explicit "Affected users" text is in the
#: pdfMax remediation entry. The first criterion in a row's
#: ``wcag_criteria`` list that matches a key here is used; rows whose
#: criteria match nothing here get the default fallback below.
_WCAG_TO_WHO: dict[str, str] = {
    "1.1.1": "Blind, low-vision, and colour-blind users.",
    "1.4.3": "Blind, low-vision, and colour-blind users.",
    "1.4.6": "Blind, low-vision, and colour-blind users.",
    "1.4.11": "Blind, low-vision, and colour-blind users.",
    "1.3.1": "Screen-reader and assistive-technology users.",
    "1.3.2": "Screen-reader and assistive-technology users.",
    "2.4.6": "Screen-reader and assistive-technology users.",
    "2.4.10": "Screen-reader and assistive-technology users.",
    "2.4.2": "All users navigating the document.",
    "2.4.5": "All users navigating the document.",
    "2.4.4": "All users navigating the document.",
    "2.1.1": "Users with motor disabilities.",
    "2.1.2": "Users with motor disabilities.",
    "2.5.5": "Users with motor disabilities.",
    "2.5.7": "Users with motor disabilities.",
    "2.5.8": "Users with motor disabilities.",
    "3.1.1": "Screen-reader users and translation tools.",
    "3.1.2": "Screen-reader users and translation tools.",
    "4.1.2": "Assistive-technology users.",
}

_WHO_DEFAULT = "Users of assistive technology."


def _who_from_wcag(criteria: list[str]) -> str:
    """Pick a ``-who`` phrase from the row's WCAG criteria list."""
    for crit in criteria:
        if crit in _WCAG_TO_WHO:
            return _WCAG_TO_WHO[crit]
    return _WHO_DEFAULT


# ---------------------------------------------------------------------------
# Sentence splitting for what/why
# ---------------------------------------------------------------------------


#: Split *text* into sentences on a period followed by whitespace. Greedy
#: enough for the prose pdfMax uses (no embedded "e.g." in the
#: ``why_it_matters`` field — verified by manual inspection of the
#: source). If the text is one long sentence, we get a single chunk.
_SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def _split_sentences(text: str) -> list[str]:
    """Split *text* into sentences. Returns a list of trimmed strings."""
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _split_what_why(why_it_matters: str) -> tuple[str, str]:
    """Split ``why_it_matters`` into a ``-what`` and ``-why`` body.

    Heuristic: take the first sentence as ``-what`` (describes the
    deficiency / what's wrong in the document) and concatenate the
    remaining sentences as ``-why`` (the user-impact framing). When the
    text doesn't split cleanly, both fields get the entire text — the
    UI surfaces them under different headings, so duplication is
    survivable.
    """
    sentences = _split_sentences(why_it_matters)
    if len(sentences) == 0:
        return why_it_matters, why_it_matters
    if len(sentences) == 1:
        only = sentences[0]
        return only, only
    what = sentences[0]
    why = " ".join(sentences[1:])
    return what, why


# ---------------------------------------------------------------------------
# Name / short-title heuristics
# ---------------------------------------------------------------------------


_ARTICLES = {"the", "a", "an"}


def _format_name(pdfmax_name: str, result: str) -> str:
    """Build the ``-name`` body for a row.

    The pdfMax check names are written affirmatively
    ("Document title set", "All fonts embedded"). The UI's FAIL/WARN/
    INFO badge already conveys the verdict, so we use the name as-is
    for FAIL rows and add a parenthetical hint for WARN/INFO rows so
    they're not visually identical to a FAIL row in result lists.
    """
    if result == "WARN":
        return f"{pdfmax_name} (warning)"
    if result == "INFO":
        return f"{pdfmax_name} (no data)"
    return pdfmax_name


def _format_short_title(pdfmax_name: str) -> str:
    """Build the ``-short-title`` body — a compact alias for cards/badges.

    If the pdfMax name is already short (≤30 chars), return as-is.
    Otherwise, drop articles ("the", "a", "an") and trim. We don't try
    to preserve grammaticality — short labels in cards tolerate
    abbreviation.
    """
    if len(pdfmax_name) <= 30:
        return pdfmax_name
    tokens = pdfmax_name.split()
    kept = [t for t in tokens if t.lower() not in _ARTICLES]
    return " ".join(kept) if kept else pdfmax_name


# ---------------------------------------------------------------------------
# Per-row message construction
# ---------------------------------------------------------------------------


_SUFFIXES: tuple[str, str, str, str, str] = (
    "name",
    "short-title",
    "what",
    "why",
    "who",
)


def _placeholder_messages(
    stable_id: str,
    pdfmax_name: str,
    result: str,
    criteria: list[str],
) -> dict[str, str]:
    """Render TODO_AUTHOR placeholders for rows without pdfMax remediation entries.

    The ``-name`` and ``-short-title`` slots still get sensible content
    derived from the catalogue; only ``-what``/``-why``/``-who`` use
    the TODO_AUTHOR marker so the UI displays a clear authoring gap
    rather than a generic placeholder.
    """
    todo = (
        f"TODO_AUTHOR: pdfMax has no remediation entry for "
        f"'{pdfmax_name}' (stable id: {stable_id}); fill in by hand."
    )
    return {
        "name": _format_name(pdfmax_name, result),
        "short-title": _format_short_title(pdfmax_name),
        "what": todo,
        "why": todo,
        "who": _who_from_wcag(criteria),
    }


def _author_messages(
    stable_id: str,
    pdfmax_name: str,
    result: str,
    rem: Mapping[str, object],
    criteria: list[str],
) -> tuple[dict[str, str], bool]:
    """Render the five message bodies for a fully-authored row.

    Returns the messages dict plus a flag indicating whether the
    ``why_it_matters`` text split cleanly into ≥2 sentences (False
    means the heuristic produced identical ``-what`` / ``-why`` bodies
    — the row goes on the manual-review list).
    """
    why_raw = rem.get("why_it_matters")
    if not isinstance(why_raw, str) or not why_raw:
        # pdfMax entry exists but has no why_it_matters; treat like a
        # placeholder for the prose fields.
        msgs = _placeholder_messages(stable_id, pdfmax_name, result, criteria)
        return msgs, False

    what, why = _split_what_why(why_raw)
    clean_split = what != why
    return (
        {
            "name": _format_name(pdfmax_name, result),
            "short-title": _format_short_title(pdfmax_name),
            "what": what,
            "why": why,
            "who": _who_from_wcag(criteria),
        },
        clean_split,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _build_artifacts(
    pdfmax_remediation: Mapping[str, Mapping[str, object]],
) -> tuple[str, str, list[str], list[str]]:
    """Return (en_ftl, fr_ftl, todo_ids, single_sentence_ids).

    * ``todo_ids`` — stable ids that got TODO_AUTHOR placeholders.
    * ``single_sentence_ids`` — stable ids whose ``why_it_matters`` was
      a single sentence; ``-what`` and ``-why`` are duplicated and need
      manual editorial review.
    """
    en_messages: list[str] = []
    todo_ids: list[str] = []
    single_sentence_ids: list[str] = []
    seen_message_ids: set[str] = set()

    for row in CHECK_CATALOGUE:
        stable_id = row["stable_id"]
        pdfmax_name = row["pdfmax_check_name"]
        result = row["pdfmax_result"]
        wcag = row["wcag_criteria"]

        rem = pdfmax_remediation.get(pdfmax_name)
        if rem is None:
            todo_ids.append(stable_id)
            messages = _placeholder_messages(stable_id, pdfmax_name, result, wcag)
        else:
            messages, clean_split = _author_messages(
                stable_id, pdfmax_name, result, rem, wcag
            )
            if not clean_split:
                single_sentence_ids.append(stable_id)

        for suffix in _SUFFIXES:
            ftl_id = f"pdf-check-{stable_id}-{suffix}"
            if ftl_id in seen_message_ids:
                msg = (
                    f"Duplicate Fluent message id {ftl_id!r} — catalogue"
                    f" has duplicate stable_id {stable_id!r}"
                )
                raise RuntimeError(msg)
            seen_message_ids.add(ftl_id)
            en_messages.append(_format_ftl_message(ftl_id, messages[suffix]))

    en_header = (
        "# PDF check labels and descriptions — English\n"
        "# Auto-generated by scripts/generate_pdf_checks_ftl.py from\n"
        "# CHECK_CATALOGUE + pdfMax's REMEDIATION_GUIDE. DO NOT edit by\n"
        "# hand — re-run the generator and commit the regenerated file.\n"
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

    return en_ftl, fr_ftl, todo_ids, single_sentence_ids


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

    en_ftl, fr_ftl, todo_ids, single_sentence_ids = _build_artifacts(
        pdfmax_remediation
    )

    _write(_EN_FTL_PATH, en_ftl)
    _write(_FR_FTL_PATH, fr_ftl)

    catalogue_rows: list[CatalogueRow] = list(CHECK_CATALOGUE)
    logger.info(
        "Catalogue rows: %d; messages emitted per locale: %d",
        len(catalogue_rows),
        len(catalogue_rows) * len(_SUFFIXES),
    )
    if todo_ids:
        logger.warning(
            "Stable ids with TODO_AUTHOR placeholders (%d): %s",
            len(todo_ids),
            ", ".join(sorted(todo_ids)),
        )
    if single_sentence_ids:
        logger.info(
            "Stable ids whose why_it_matters is one sentence (-what == -why) (%d): %s",
            len(single_sentence_ids),
            ", ".join(sorted(single_sentence_ids)),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
