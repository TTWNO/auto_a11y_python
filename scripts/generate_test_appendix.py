"""Regenerate the tests-by-touchpoint appendix inside docs/USER_GUIDE.md.

Reads every issue in the catalog, groups it by touchpoint, and rewrites the
region between the BEGIN/END GENERATED TEST APPENDIX markers. Run after
adding or renaming tests:

    python scripts/generate_test_appendix.py
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import auto_a11y.core.database  # noqa: F401 — settles package import order
from auto_a11y.core.touchpoints import TOUCHPOINTS, TouchpointID
from auto_a11y.reporting.issue_catalog import IssueCatalog

# Display names for the catalog's category slugs. Built from the touchpoint
# registry where a slug matches a TouchpointID value, with a few extra slugs
# the catalog uses that aren't 1:1 with a touchpoint.
_CATEGORY_DISPLAY: dict[str, str] = {
    tp.value: TOUCHPOINTS[tp].name for tp in TouchpointID if tp in TOUCHPOINTS
}
_CATEGORY_DISPLAY.update({
    "colors_contrast": "Colours & Contrast",
    "colors": "Colours & Contrast",
    "color": "Colours & Contrast",
    "contrast": "Colours & Contrast",
    "aria": "ARIA",
    "focus": "Focus Management",
    "focus_management": "Focus Management",
    "style": "Styles",
    "styles": "Styles",
    "typography": "Fonts & Typography",
    "fonts": "Fonts & Typography",
    "title": "Page Title",
    "title_attributes": "Title Attributes",
    "electronic_documents": "Electronic Documents",
    "event_handling": "Event Handling",
    "accessible_names": "Accessible Names",
    "javascript": "Event Handling",
    "responsive": "Responsive & Reflow",
    "svg": "Images",
    "images": "Images",
    "pdf": "Electronic Documents",
})


def category_display(slug: str) -> str:
    slug = (slug or "other").lower()
    return _CATEGORY_DISPLAY.get(slug, slug.replace("_", " ").title())

GUIDE = REPO / "docs" / "USER_GUIDE.md"
BEGIN = "<!-- BEGIN GENERATED TEST APPENDIX -->"
END = "<!-- END GENERATED TEST APPENDIX -->"

PLACEHOLDER = re.compile(r"\{[^}]+\}")
HTML_TAG = re.compile(r"<(/?[a-zA-Z][^>]*)>")
BARE_URL = re.compile(r"\b(?:https?://|www\.|file://)\S+")


def sanitize(text: str) -> str:
    """Make a catalog description safe inside a Markdown table cell.

    Wraps HTML tags and bare URLs in inline code so they render literally
    (and don't trip Markdown-lint's no-inline-html / no-bare-urls rules),
    and escapes the cell delimiter.
    """
    text = HTML_TAG.sub(lambda m: f"`<{m.group(1)}>`", text)
    text = BARE_URL.sub(lambda m: f"`{m.group(0).rstrip('.,;:)')}`", text)
    return text.replace("|", "\\|")


def first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    text = PLACEHOLDER.sub("…", text)
    match = re.search(r"(?<=[.!?])\s", text)
    return sanitize(text[: match.start()] if match else text)


def build_appendix() -> str:
    by_touchpoint: defaultdict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for code, issue in IssueCatalog.get_all_issues().items():
        touchpoint = category_display(str(issue.get("category") or "other"))
        issue_type = str(issue.get("type") or "")
        desc = first_sentence(str(issue.get("description") or issue.get("title") or ""))
        by_touchpoint[touchpoint].append((code, issue_type, desc))

    total = sum(len(v) for v in by_touchpoint.values())
    out = [
        BEGIN,
        "",
        f"The automated test suite contains **{total} checks** across "
        f"**{len(by_touchpoint)} touchpoints**. Whether each check runs "
        "depends on its fixture-validation status and the project's "
        "touchpoint configuration. Types: **Error** (WCAG violation), "
        "**Warning** (likely barrier), **Info** (informational), "
        "**Discovery** (needs manual review).",
        "",
    ]
    for touchpoint in sorted(by_touchpoint):
        rows = sorted(by_touchpoint[touchpoint])
        out.append(f"### {touchpoint}")
        out.append("")
        out.append("| Check | Type | What it tests |")
        # Proportional dash counts set the column widths in the Word export
        # (pandoc maps them to grid widths): a narrow Type column — just wide
        # enough to fit its longest value, "Discovery" — with the extra space
        # split between Check (long code names) and the What-it-tests text.
        out.append("| " + "-" * 34 + " | " + "-" * 16 + " | " + "-" * 60 + " |")
        for code, issue_type, desc in rows:
            out.append(f"| `{code}` | {issue_type} | {desc} |")
        out.append("")
    out.append(END)
    return "\n".join(out)


def main() -> None:
    source = GUIDE.read_text(encoding="utf-8")
    if BEGIN not in source or END not in source:
        raise SystemExit(
            f"Markers not found in {GUIDE} — add {BEGIN} and {END} where the "
            "appendix belongs."
        )
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.DOTALL)
    updated = pattern.sub(lambda _: build_appendix(), source)
    GUIDE.write_text(updated, encoding="utf-8")
    print(f"appendix regenerated in {GUIDE}")


if __name__ == "__main__":
    main()
