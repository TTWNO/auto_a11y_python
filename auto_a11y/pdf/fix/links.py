"""Fixes that give link annotations a description.

``/Contents`` on a link annotation is what a screen reader announces when
the user reaches it. Without one they hear "link" and nothing else — or,
in some readers, the raw destination URL, which for a tracking link is
several lines of unreadable parameters.

The text almost always already exists: it is the wording the author made
into a link, sitting in the ``<Link>`` structure element that wraps the
annotation. These fixes copy it across rather than asking for it.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Name, String

from auto_a11y.pdf.audit.content_streams import (
    extract_mcid_text_map_from_content_streams,
)
from auto_a11y.pdf.audit.structure import (
    StructElement,
    populate_element_text,
    walk_structure_tree,
)
from auto_a11y.pdf.fix._pdf_objects import items, kids
from auto_a11y.pdf.fix.models import FixOptions, FixResult

# A description longer than this is almost certainly the whole paragraph
# the link sits in rather than the link's own wording.
_MAX_DESCRIPTION = 300


def _linked_annotation(element: StructElement) -> pikepdf.Object | None:
    """The annotation a ``<Link>`` element points at, through its ``/OBJR``."""
    for child in kids(element.obj) or []:
        if not isinstance(child, pikepdf.Dictionary):
            continue
        if str(child.get(Name("/Type")) or "") != "/OBJR":
            continue
        target = child.get(Name("/Obj"))
        if target is not None:
            return target
    return None


def _describe(element: StructElement) -> str:
    """The best description available for a link element.

    Explicit alternative text wins, since an author who wrote it meant it
    to be the announcement. Otherwise the link's own text is used — which
    is why the marked-content map has to be resolved first: link wording
    lives in page content, not as a child of the structure element.
    """
    for candidate in (element.alt_text, element.actual_text, element.text_content):
        text = " ".join((candidate or "").split())
        if text:
            return text[:_MAX_DESCRIPTION]
    return ""


def _populate_from_structure(pdf: pikepdf.Pdf) -> tuple[int, bool]:
    """Copy link text into ``/Contents``. Returns ``(count, had_tree)``."""
    elements, _role_map = walk_structure_tree(pdf)
    if not elements:
        return 0, False

    try:
        populate_element_text(
            elements, extract_mcid_text_map_from_content_streams(pdf)
        )
    except (pikepdf.PdfError, ValueError):
        # Without the text map only alt text is available, which is worse
        # but not wrong; the fix degrades rather than failing.
        pass

    written = 0
    for element in elements:
        if element.resolved_tag != "Link":
            continue
        annotation = _linked_annotation(element)
        if annotation is None:
            continue
        existing = annotation.get(Name("/Contents"))
        if existing is not None and str(existing).strip():
            continue
        description = _describe(element)
        if not description:
            continue
        annotation[Name("/Contents")] = String(description)
        written += 1
    return written, True


def fix_link_annot_contents(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Describe links using the text that was made into the link.

    Takes no input: everything it needs is already in the document.
    """
    written, had_tree = _populate_from_structure(pdf)
    if not had_tree:
        return FixResult(
            "fix_link_annot_contents", False, "No structure tree found",
        )
    if written:
        return FixResult(
            "fix_link_annot_contents", True,
            f"Described {written} link(s) using their own text",
        )
    return FixResult(
        "fix_link_annot_contents", True,
        "No link needed a description, or none had text to use",
    )


def fix_link_content(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Describe links from their own text, then from supplied descriptions.

    The first pass is :func:`fix_link_annot_contents`. The second applies
    ``opts.link_content_map``, keyed ``"page:position"`` with both parts
    counting from 1 to match the report — the original counted pages from
    0 while the report showed them from 1, so every supplied description
    landed on the page before the one it was written for.

    Supplied descriptions do not overwrite one the first pass just wrote:
    a link with usable text needed no help, and the report offering it for
    input was working from before that pass ran.
    """
    written, had_tree = _populate_from_structure(pdf)
    if not had_tree and not opts.link_content_map:
        return FixResult("fix_link_content", False, "No structure tree found")

    supplied = 0
    problems: list[str] = []
    for key, description in sorted(opts.link_content_map.items()):
        page_part, _, position_part = key.partition(":")
        try:
            page_number = int(page_part)
            position = int(position_part)
        except (TypeError, ValueError):
            problems.append(f'"{key}" is not page:position')
            continue
        if page_number < 1 or page_number > len(pdf.pages):
            problems.append(f"page {page_number} does not exist")
            continue

        annots = items(pdf.pages[page_number - 1].obj.get(Name("/Annots")))
        if position < 1 or position > len(annots):
            problems.append(f"page {page_number} has no annotation {position}")
            continue

        annotation = annots[position - 1]
        existing = annotation.get(Name("/Contents"))
        if existing is not None and str(existing).strip():
            continue
        annotation[Name("/Contents")] = String(description)
        supplied += 1

    total = written + supplied
    parts: list[str] = []
    if written:
        parts.append(f"{written} from the document's own text")
    if supplied:
        parts.append(f"{supplied} from supplied descriptions")

    if total and not problems:
        return FixResult(
            "fix_link_content", True,
            f"Described {total} link(s): " + ", ".join(parts),
        )
    if total:
        return FixResult(
            "fix_link_content", True,
            f"Described {total} link(s): " + ", ".join(parts)
            + " — " + "; ".join(problems),
        )
    if problems:
        return FixResult(
            "fix_link_content", False,
            "Described no links — " + "; ".join(problems),
        )
    return FixResult(
        "fix_link_content", True,
        "No link needed a description, or none had text to use",
    )

