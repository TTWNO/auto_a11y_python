"""Regenerate ``auto_a11y/web/translations/inline_issue_ids.json`` + matching
EN/FR FTL inline-issue entries so they cover every static description the
issue catalog emits.

When `auto_a11y/reporting/issue_descriptions_enhanced.py`'s
``ISSUE_DESCRIPTIONS`` dict grows new entries (or the placeholder-fallback
substitution in ``issue_descriptions_translated.py`` produces NEW concrete
strings from previously-parameterised ones), the test
``tests/test_fluent.py::TestInlineIssueIdsCoverage::test_static_issue_descriptions_in_json``
flags every uncovered string. This script reconciles by:

1. Iterating every error code declared in
   ``get_detailed_issue_description``'s local ``ISSUE_DESCRIPTIONS`` dict
   (extracted from source via regex — the dict is function-local).
2. Calling ``get_detailed_issue_description(code, {})`` for each, applying
   the same filtering rules the test uses (skip parameterised, skip
   fallback strings).
3. For every previously-unmapped ``what`` / ``what_generic`` text:
   - Generate a stable FTL ID (slug of the text + 6-hex truncated SHA-1
     for collision-avoidance).
   - Append a ``text -> ftl_id`` entry to ``inline_issue_ids.json``.
   - Append a ``ftl_id = text`` line to ``en/inline-issues.ftl``.
   - Append a ``ftl_id = <fr-translation>`` line to ``fr/inline-issues.ftl``.

For French, the script looks up the translation in
``build/staging/app/auto_a11y/reporting/issue_translations_inline.py`` if
available (that file is a frozen build artefact carrying the legacy
inline FR dictionary). If a string is not in the staging map, the
script falls back to using the EN text — the FTL ID exists in the FR
file (satisfying ``test_all_json_keys_have_fr_ftl``) and the EN
fallback is replaced when a human translator updates it later.

Idempotent: re-running adds nothing when the catalog hasn't changed.

Usage::

    .venv/bin/python scripts/regenerate_inline_issue_ids.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "auto_a11y" / "web" / "translations" / "inline_issue_ids.json"
EN_FTL = ROOT / "auto_a11y" / "web" / "translations" / "en" / "inline-issues.ftl"
FR_FTL = ROOT / "auto_a11y" / "web" / "translations" / "fr" / "inline-issues.ftl"
STAGING_FR_TRANSLATIONS = (
    ROOT / "build" / "staging" / "app" / "auto_a11y"
    / "reporting" / "issue_translations_inline.py"
)

# Make ``auto_a11y`` importable when running this script standalone, and
# import ``issue_descriptions_enhanced`` directly via ``importlib`` to
# bypass ``auto_a11y/reporting/__init__.py`` — that module triggers a
# circular import chain into the web app (ReportGenerator → fluent →
# routes → reports) which isn't relevant to catalog inspection.
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

_ENHANCED_SPEC = importlib.util.spec_from_file_location(
    "_issue_descriptions_enhanced_standalone",
    ROOT / "auto_a11y" / "reporting" / "issue_descriptions_enhanced.py",
)
assert _ENHANCED_SPEC is not None and _ENHANCED_SPEC.loader is not None
_ENHANCED = importlib.util.module_from_spec(_ENHANCED_SPEC)
_ENHANCED_SPEC.loader.exec_module(_ENHANCED)
get_detailed_issue_description: Any = _ENHANCED.get_detailed_issue_description


def _slugify(text: str) -> str:
    """Lower-case, hyphenate, strip punctuation; truncate to 80 chars.

    The output is used as the prefix of the FTL ID. Hash is appended
    separately so two strings that slugify to the same prefix still
    produce distinct IDs.
    """
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned[:80].rstrip("-")


def _ftl_id(text: str) -> str:
    """Stable FTL ID for ``text``: ``issue-<slug>-<6hex-sha1>``.

    The SHA-1 truncation suffix is purely a collision break; the
    full string identity remains the JSON-map key. Same value on every
    re-run for the same input.
    """
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
    slug = _slugify(text)
    return f"issue-{slug}-{digest}" if slug else f"issue-{digest}"


def _catalog_codes() -> list[str]:
    """Pull every ``'Err…'`` / ``'Warn…'`` / ``'Info…'`` / ``'Disco…'`` /
    ``'AI_…'`` key out of ``get_detailed_issue_description``'s body.

    The ``ISSUE_DESCRIPTIONS`` dict is local to the function so the
    only way to enumerate is via source-string regex — same approach
    ``test_static_issue_descriptions_in_json`` uses. Reads the .py
    source directly to dodge the ``auto_a11y.reporting`` package's
    circular import chain.
    """
    source_path = ROOT / "auto_a11y" / "reporting" / "issue_descriptions_enhanced.py"
    fn_source = source_path.read_text(encoding="utf-8")
    err_warn_disco_ai = re.compile(
        r"'((?:Err|Warn|Disco|AI_)[A-Za-z0-9_]+)'\s*:\s*\{"
    )
    info_only = re.compile(r"'(Info[A-Z][A-Za-z0-9_]+)'\s*:\s*\{")
    codes = err_warn_disco_ai.findall(fn_source)
    codes.extend(info_only.findall(fn_source))
    return codes


def _load_staging_fr() -> dict[str, str]:
    """Read the frozen FR translations dictionary from staging.

    Returns an empty dict if the staging copy is absent so the script
    still works on a fresh clone — French fallback will degrade to EN.
    """
    if not STAGING_FR_TRANSLATIONS.is_file():
        return {}
    namespace: dict[str, Any] = {}
    code = STAGING_FR_TRANSLATIONS.read_text(encoding="utf-8")
    exec(compile(code, str(STAGING_FR_TRANSLATIONS), "exec"), namespace)  # noqa: S102
    raw = namespace.get("ISSUE_DESCRIPTION_TRANSLATIONS_FR", {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


def _missing_texts(json_map: dict[str, str]) -> list[str]:
    """Compute the de-duplicated list of catalog texts not in the JSON map.

    Filters mirror the test:
    - drop anything containing a ``{`` (still-parameterised, post-fallback).
    - drop the generic "An accessibility issue of type ..." fallback.
    """
    found: list[str] = []
    seen: set[str] = set()
    for code in _catalog_codes():
        desc = get_detailed_issue_description(code, {})
        for field in ("what", "what_generic"):
            text = desc.get(field)
            if not isinstance(text, str) or not text:
                continue
            if "{" in text:
                continue
            if text.startswith("An accessibility issue of type"):
                continue
            if text in json_map or text in seen:
                continue
            seen.add(text)
            found.append(text)
    return found


def main() -> int:
    json_map: dict[str, str] = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    fr_dict = _load_staging_fr()
    new_texts = _missing_texts(json_map)
    if not new_texts:
        print("nothing to regenerate — JSON is in sync with the catalog")
        return 0

    # Append to FTL files (sorted by FTL ID so the additions are
    # contiguous and review-friendly).
    additions_en: list[str] = []
    additions_fr: list[str] = []
    for text in sorted(new_texts):
        ftl_id = _ftl_id(text)
        # Guard against an ftl_id collision with an existing JSON entry —
        # if someone hand-edited and produced a clash, surface it loudly
        # rather than silently overwrite.
        if ftl_id in json_map.values():
            existing_text = next(t for t, fid in json_map.items() if fid == ftl_id)
            print(
                f"ftl_id {ftl_id!r} already maps to {existing_text!r}; "
                f"refusing to alias it to {text!r}",
                file=sys.stderr,
            )
            return 1
        json_map[text] = ftl_id
        additions_en.append(f"{ftl_id} = {text}")
        fr_translation = fr_dict.get(text, text)
        additions_fr.append(f"{ftl_id} = {fr_translation}")

    JSON_PATH.write_text(
        json.dumps(_sorted_json(json_map), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    _append_to_ftl(EN_FTL, additions_en)
    _append_to_ftl(FR_FTL, additions_fr)

    print(f"added {len(new_texts)} inline-issue entries")
    print(f"  JSON: {JSON_PATH}")
    print(f"  EN  : {EN_FTL}")
    print(f"  FR  : {FR_FTL}")
    print(
        "FR fallbacks (EN text reused) need human review — search the FR file "
        "for the new ftl IDs and replace with translated text."
    )
    return 0


def _sorted_json(json_map: dict[str, str]) -> dict[str, str]:
    """Return ``json_map`` with keys in case-insensitive sorted order."""
    return {key: json_map[key] for key in sorted(json_map.keys(), key=str.lower)}


def _append_to_ftl(path: Path, additions: list[str]) -> None:
    """Append additions to an FTL file, ensuring there's a separator
    comment block above the new entries so reviewers can see where the
    regenerated block starts."""
    existing = path.read_text(encoding="utf-8")
    block = (
        "\n# Regenerated by scripts/regenerate_inline_issue_ids.py — entries\n"
        "# below cover newly-added catalog descriptions. Sort order is by FTL\n"
        "# ID for review-friendliness; do not hand-edit unless you're also\n"
        "# updating the JSON map.\n"
    )
    new = existing.rstrip() + "\n" + block + "\n".join(additions) + "\n"
    path.write_text(new, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
