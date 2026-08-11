"""Building a document outline (bookmarks) from heading structure.

Bookmarks are the navigation panel a reader opens to see a document's
shape and jump around it. For a long report they are often the difference
between usable and not — and unlike heading navigation, they work for
someone using a plain PDF viewer with no assistive technology at all.

They can be generated because the headings already say where the sections
are. What matters is that the generated outline reproduces the document's
hierarchy rather than flattening it, and that each entry is labelled with
the heading's own words.
"""
from __future__ import annotations

from dataclasses import dataclass

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.audit.content_streams import (
    extract_mcid_text_map_from_content_streams,
)
from auto_a11y.pdf.audit.structure import (
    StructElement,
    populate_element_text,
    walk_structure_tree,
)
from auto_a11y.pdf.fix.models import FixOptions, FixResult

_HEADING_LEVELS = {f"H{n}": n for n in range(1, 7)}

# Bookmark panels are narrow; a title longer than this is truncated rather
# than left to be clipped mid-word by the viewer.
_MAX_TITLE = 80
_ELLIPSIS = "…"


@dataclass
class _Heading:
    level: int
    title: str
    page: pikepdf.Object | None


def _heading_title(element: StructElement, ordinal: int) -> str:
    """The best available label for a heading.

    Preference order is the heading's own text, then its alternative or
    actual text, then a positional placeholder. The text is what a reader
    saw on the page, so it is what they will look for in the panel.
    """
    for candidate in (element.text_content, element.alt_text, element.actual_text):
        text = (candidate or "").strip()
        if text:
            if len(text) > _MAX_TITLE:
                return text[: _MAX_TITLE - 1].rstrip() + _ELLIPSIS
            return text
    return f"Heading {ordinal}"


def _page_for(element: StructElement) -> pikepdf.Object | None:
    """The page an element sits on, via ``/Pg`` on it or on a child MCR."""
    page = element.obj.get(Name("/Pg"))
    if page is not None:
        return page
    kids = element.obj.get(Name("/K"))
    if kids is None:
        return None
    children = (
        [kids[i] for i in range(len(kids))] if isinstance(kids, Array) else [kids]
    )
    for child in children:
        if not isinstance(child, pikepdf.Dictionary):
            continue
        child_type = child.get(Name("/Type"))
        if child_type is not None and str(child_type) == "/MCR":
            page = child.get(Name("/Pg"))
            if page is not None:
                return page
    return None


def _collect_headings(pdf: pikepdf.Pdf) -> list[_Heading]:
    """Every heading in document order, with its level, title and page."""
    elements, _role_map = walk_structure_tree(pdf)
    if not elements:
        return []
    # Heading text lives in marked content, not as a child string, so the
    # MCID map has to be resolved before titles are readable. Without this
    # nearly every bookmark would come out as "Heading 3".
    try:
        populate_element_text(
            elements, extract_mcid_text_map_from_content_streams(pdf)
        )
    except (pikepdf.PdfError, ValueError):
        # A content stream that will not parse costs us the titles, not
        # the outline: the placeholders below still give working jumps.
        pass

    headings: list[_Heading] = []
    for element in elements:
        level = _HEADING_LEVELS.get(element.resolved_tag)
        if level is None:
            continue
        headings.append(
            _Heading(
                level=level,
                title=_heading_title(element, len(headings) + 1),
                page=_page_for(element),
            )
        )
    return headings


def _build_entries(
    pdf: pikepdf.Pdf, headings: list[_Heading], fallback_page: pikepdf.Object
) -> pikepdf.Object:
    """Create the ``/Outlines`` tree, nesting by heading level.

    Each heading becomes a child of the nearest preceding heading of a
    lower level, which is what makes the panel collapsible. A document
    that opens at H2, or jumps H1 → H3, still nests sensibly: the stack
    is unwound to the closest enclosing level rather than assuming a
    well-formed sequence.
    """
    outlines = pdf.make_indirect(Dictionary(Type=Name("/Outlines")))

    # (level, node) for the chain of currently-open ancestors.
    open_chain: list[tuple[int, pikepdf.Object]] = []
    # node → its children, in order.
    children: dict[tuple[int, int], list[pikepdf.Object]] = {}
    roots: list[pikepdf.Object] = []

    for heading in headings:
        entry = pdf.make_indirect(
            Dictionary(
                Title=String(heading.title),
                Dest=Array([heading.page or fallback_page, Name("/Fit")]),
            )
        )
        while open_chain and open_chain[-1][0] >= heading.level:
            open_chain.pop()

        if open_chain:
            parent = open_chain[-1][1]
            entry[Name("/Parent")] = parent
            children.setdefault(parent.objgen, []).append(entry)
        else:
            entry[Name("/Parent")] = outlines
            roots.append(entry)

        open_chain.append((heading.level, entry))

    def link(siblings: list[pikepdf.Object], parent: pikepdf.Object) -> int:
        """Chain siblings with /Prev and /Next; return the visible count."""
        total = 0
        for position, node in enumerate(siblings):
            if position:
                node[Name("/Prev")] = siblings[position - 1]
            if position < len(siblings) - 1:
                node[Name("/Next")] = siblings[position + 1]
            own = children.get(node.objgen, [])
            total += 1 + (link(own, node) if own else 0)
        if siblings:
            parent[Name("/First")] = siblings[0]
            parent[Name("/Last")] = siblings[-1]
        return total

    # A positive /Count means the entry is open; the spec uses the sign to
    # carry that, so the top level opens and shows its descendants.
    outlines[Name("/Count")] = link(roots, outlines)
    return outlines


def fix_bookmarks(pdf: pikepdf.Pdf, opts: FixOptions) -> FixResult:
    """Generate ``/Outlines`` from the document's headings.

    Two improvements on the original, both of which change what the reader
    actually gets.

    The outline is nested by heading level. The original computed each
    heading's level and then never used it, producing a flat list of
    siblings — so a hundred-heading report gave a hundred top-level
    bookmarks with no structure to collapse, which is barely more useful
    than none.

    Titles come from the heading's text. The original looked only for
    ``/Alt``, ``/ActualText`` or a direct string child of the heading —
    but heading text lives in marked content, so in practice almost every
    bookmark came out as "Heading 1", "Heading 2". Resolving the marked
    content gives the words the reader saw.

    Declines when bookmarks already exist: an existing outline was
    authored deliberately and may not follow the headings at all.
    """
    outlines = pdf.Root.get(Name("/Outlines"))
    if outlines is not None:
        count = outlines.get(Name("/Count"))
        if count is not None and int(count) != 0:
            return FixResult("fix_bookmarks", True, "Bookmarks already exist")

    if not len(pdf.pages):
        return FixResult("fix_bookmarks", False, "Document has no pages")

    headings = _collect_headings(pdf)
    if not headings:
        return FixResult(
            "fix_bookmarks", False,
            "No headings (H1-H6) in the structure tree to build an outline"
            + " from",
        )

    pdf.Root[Name("/Outlines")] = _build_entries(
        pdf, headings, pdf.pages[0].obj
    )
    # Opens the bookmarks panel when the document is opened, which is the
    # point of generating them.
    pdf.Root[Name("/PageMode")] = Name("/UseOutlines")

    depth = len({h.level for h in headings})
    return FixResult(
        "fix_bookmarks", True,
        f"Created {len(headings)} bookmark(s) from the heading structure"
        + f", nested across {depth} level(s)",
    )
