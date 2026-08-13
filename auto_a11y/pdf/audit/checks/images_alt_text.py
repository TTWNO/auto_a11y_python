"""Image alt-text accessibility checks.

Four checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py``:

* :func:`check_alt_text_on_figure_art` — every graphical Figure/Art
  structure element carries non-blank alt text. Art containers with
  text-bearing children (``P``, ``Span``, ``L``, ``LI``, ``Table``,
  ``TR``, ``TD``, ``TH``) are excused as structural wrappers (PDF/UA,
  WCAG 1.1.1; pdfMax line ~4761).
* :func:`check_alt_text_free_of_redundant_role_text` — alt text does
  not contain role words like ``link``, ``image of``, ``image``,
  ``button``, or their French equivalents ``lien`` / ``image de``;
  screen readers already announce the element role
  (WCAG 1.1.1 best practice; pdfMax line ~5206).
* :func:`check_alt_text_does_not_hide_interactive_elements` — a
  structure element with alt text must not have any ``Link`` / ``Form``
  / ``Annot`` descendant whose accessible name would be hidden by the
  parent's alt text (PDF/UA, WCAG 4.1.2; pdfMax line ~5259).
* :func:`check_figure_elements_have_bbox` — every ``Figure`` structure
  element carries a ``/BBox`` attribute, either directly on the element
  or inside the ``/A`` attribute dictionary (or any entry of an ``/A``
  Array). Required by PAC 2024 to fix Figure positions on the page
  (PDF/UA PAC 2024; pdfMax line ~7012).

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes an
:data:`IMAGES_ALT_TEXT_CHECKS` registry list. ``CheckResult`` ``name``
and ``standard`` strings match pdfMax verbatim so Phase 6's check
catalogue can map them.

Deferred to Phase 5.1 (semantic AI module):

* ``Alt text adequacy`` (WCAG 1.1.1; pdfMax line ~4068) — Claude
  vision rates each Figure's alt text on accuracy, completeness,
  context-relevance, and conciseness against the rendered image bytes
  plus surrounding document context. Requires the AI infrastructure
  not yet built.
* ``Images of text have matching alt text`` (WCAG 1.4.5; pdfMax line
  ~3878) — Claude vision detects text rendered inside extracted images
  *and* across full-page renderings, then cross-references the detected
  text against Figure/Art alt-text strings. Requires both the image
  bytes ``ctx.images`` provides and the AI infrastructure not yet
  built. TODO(phase 5.1): port together with ``Alt text adequacy``.
"""
from __future__ import annotations

import re
from collections.abc import Callable

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------


#: Tags whose presence as a child of an Art / Figure makes the parent a
#: structural container (whose accessible content comes from the
#: children themselves) rather than a graphic. Mirrors pdfMax's tuple
#: at line 4745 verbatim.
_TEXT_CHILD_TAGS: frozenset[str] = frozenset(
    {"P", "Span", "L", "LI", "Table", "TR", "TD", "TH"}
)

#: Interactive descendant tags reported by
#: :func:`check_alt_text_does_not_hide_interactive_elements`. Mirrors
#: pdfMax's :func:`_find_interactive_descendants` tuple at line 7857
#: verbatim.
_INTERACTIVE_TAGS: frozenset[str] = frozenset({"Link", "Form", "Annot"})

#: Patterns that duplicate what screen readers already announce. Each
#: entry is ``(compiled_regex, label)`` — the label is the human-readable
#: token surfaced in the FAIL detail. Mirrors pdfMax's tuple at line
#: 5185 verbatim, including the order (more-specific phrases come before
#: their substrings, e.g. ``"image of"`` before ``"image"``).
_ROLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\blink\b"), "link"),
    (re.compile(r"\bimage of\b"), "image of"),
    (re.compile(r"\bimage\b"), "image"),
    (re.compile(r"\bgraphic of\b"), "graphic of"),
    (re.compile(r"\bbutton\b"), "button"),
    (re.compile(r"\blien\b"), "lien"),
    (re.compile(r"\bimage de\b"), "image de"),
)


# ---------------------------------------------------------------------------
# check_alt_text_on_figure_art
# ---------------------------------------------------------------------------


def check_alt_text_on_figure_art(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.1.1: every graphical Figure/Art has alt text.

    Reads :attr:`AuditContext.elements`. An ``Art`` element with a child
    in :data:`_TEXT_CHILD_TAGS` is treated as a structural container
    (its text comes from the children) and is excluded from the
    requirement. Empty / whitespace-only alt text counts as missing.
    """
    elements = ctx.elements
    figures_without_alt: list[StructElement] = []
    figures_with_alt: list[StructElement] = []
    art_structural: list[StructElement] = []

    for elem in elements:
        if elem.resolved_tag not in ("Figure", "Art"):
            continue
        has_text_children = any(
            elements[ci].resolved_tag in _TEXT_CHILD_TAGS
            for ci in elem.children_indices
            if 0 <= ci < len(elements)
        )
        if elem.alt_text and elem.alt_text.strip():
            figures_with_alt.append(elem)
        elif has_text_children:
            art_structural.append(elem)
        else:
            figures_without_alt.append(elem)

    if not figures_without_alt:
        count = len(figures_with_alt)
        if count == 0 and not art_structural:
            return [
                CheckResult(
                    name="Alt text on all Figure/Art tags",
                    standard="PDF/UA, WCAG 1.1.1",
                    result="PASS",
                    details="0 Figure/Art elements with alt text",
                )
            ]
        detail = f"{count} Figure/Art elements with alt text"
        if art_structural:
            detail += (
                f"; {len(art_structural)} Art containers with text"
                + " children (alt text not required)"
            )
        return [
            CheckResult(
                name="Alt text on all Figure/Art tags",
                standard="PDF/UA, WCAG 1.1.1",
                result="PASS",
                details=detail,
            )
        ]

    missing = "; ".join(
        f"[{e.index + 1}] {e.custom_tag}" for e in figures_without_alt
    )
    detail = (
        f"{len(figures_without_alt)} Figure/Art elements missing alt"
        + f" text: {missing}"
    )
    if art_structural:
        detail += (
            f" ({len(art_structural)} Art containers with text children"
            + " excluded)"
        )
    return [
        CheckResult(
            name="Alt text on all Figure/Art tags",
            standard="PDF/UA, WCAG 1.1.1",
            result="FAIL",
            details=detail,
            # Naming the figures makes this finding locatable: the viewer
            # joins each index to the element's position and draws the
            # overlay there.
            elements=tuple(e.index for e in figures_without_alt),
        )
    ]


# ---------------------------------------------------------------------------
# check_alt_text_free_of_redundant_role_text
# ---------------------------------------------------------------------------


def check_alt_text_free_of_redundant_role_text(
    ctx: AuditContext,
) -> list[CheckResult]:
    """WCAG 1.1.1 (best practice): alt text omits role words.

    For every element with an ``alt_text``, the lower-cased text is
    checked against the patterns in :data:`_ROLE_PATTERNS`. The first
    matching pattern produces one issue line and the loop moves on to
    the next element (mirrors pdfMax's ``break`` at line 5203).
    WARNs when any element matched; PASSes otherwise.
    """
    redundant_role_issues: list[str] = []
    for elem in ctx.elements:
        if not elem.alt_text:
            continue
        alt_lower = elem.alt_text.lower()
        for pattern, label in _ROLE_PATTERNS:
            if pattern.search(alt_lower):
                redundant_role_issues.append(
                    f'[{elem.index + 1}] {elem.resolved_tag}: alt text'
                    + f' contains "{label}" —'
                    + " screen readers already announce element roles"
                )
                break

    if not redundant_role_issues:
        return [
            CheckResult(
                name="Alt text free of redundant role text",
                standard="WCAG 1.1.1 (best practice)",
                result="PASS",
                details=(
                    "No alt text contains role words like 'link',"
                    + " 'image', 'button'"
                ),
            )
        ]
    return [
        CheckResult(
            name="Alt text free of redundant role text",
            standard="WCAG 1.1.1 (best practice)",
            result="WARN",
            details="; ".join(redundant_role_issues),
        )
    ]


# ---------------------------------------------------------------------------
# check_alt_text_does_not_hide_interactive_elements
# ---------------------------------------------------------------------------


def _find_interactive_descendants(
    elem: StructElement,
    elements: list[StructElement],
    index_map: dict[int, StructElement],
    results: list[StructElement],
    visited: set[int] | None = None,
) -> None:
    """Recursively collect Link / Form / Annot descendants.

    Mirrors pdfMax's :func:`_find_interactive_descendants` at line 7852
    verbatim. Mutates ``results`` in place rather than returning, so
    repeated calls accumulate findings across siblings.

    A ``visited`` set of node indices guards against malformed structure
    trees (self-referential or duplicated ``children_indices``) that would
    otherwise recurse unboundedly — mirroring the duplicate-protection in
    the table/list BFS helpers (see ``_table_descendants`` in
    :mod:`auto_a11y.pdf.audit.checks.tables`).
    """
    if visited is None:
        visited = set()
    visited.add(elem.index)
    for ci in elem.children_indices:
        if ci not in index_map or ci in visited:
            continue
        visited.add(ci)
        child = index_map[ci]
        if child.resolved_tag in _INTERACTIVE_TAGS:
            results.append(child)
        _find_interactive_descendants(
            child, elements, index_map, results, visited
        )


def check_alt_text_does_not_hide_interactive_elements(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA, WCAG 4.1.2: alt text doesn't hide interactive descendants.

    For every element with an ``alt_text`` and at least one child,
    walks the descendant chain looking for ``Link`` / ``Form`` /
    ``Annot`` tags. Any such descendant means the parent's alt text
    will override the descendant's accessible name in screen readers
    — a 4.1.2 failure. Reports up to 5 interactive descendants per
    parent in the detail.
    """
    elements = ctx.elements
    index_map = {e.index: e for e in elements}
    interactive_hidden_issues: list[str] = []

    for elem in elements:
        if not elem.alt_text or not elem.children_indices:
            continue
        interactive_children: list[StructElement] = []
        _find_interactive_descendants(
            elem, elements, index_map, interactive_children
        )
        if not interactive_children:
            continue
        child_desc = ", ".join(
            f"[{c.index + 1}] {c.resolved_tag}"
            for c in interactive_children[:5]
        )
        interactive_hidden_issues.append(
            f"[{elem.index + 1}] {elem.resolved_tag} has alt text which"
            + f" hides {len(interactive_children)} interactive"
            + f" child(ren): {child_desc}."
            + " Screen readers read the alt text and skip the links."
        )

    if not interactive_hidden_issues:
        return [
            CheckResult(
                name="Alt text does not hide interactive elements",
                standard="PDF/UA, WCAG 4.1.2",
                result="PASS",
                details=(
                    "No alt text parent elements contain interactive"
                    + " children"
                ),
            )
        ]
    return [
        CheckResult(
            name="Alt text does not hide interactive elements",
            standard="PDF/UA, WCAG 4.1.2",
            result="FAIL",
            details="; ".join(interactive_hidden_issues),
        )
    ]


# ---------------------------------------------------------------------------
# check_figure_elements_have_bbox
# ---------------------------------------------------------------------------


def _figure_has_bbox(node: pikepdf.Dictionary) -> bool:
    """Return ``True`` if a Figure structure element carries ``/BBox``.

    pdfMax accepts the BBox either as a direct ``/BBox`` Array on the
    structure element, or as ``/BBox`` inside an entry of the ``/A``
    attribute dictionary (which may itself be a single Dictionary or an
    Array of Dictionaries). Mirrors the helper at line 7016 verbatim.
    """
    if pikepdf_helpers.get_array(node, "/BBox") is not None:
        return True
    try:
        a_val = node["/A"]
    except KeyError:
        return False

    a_items: list[pikepdf.Object]
    if isinstance(a_val, pikepdf.Array):
        a_items = [a_val[i] for i in range(len(a_val))]
    else:
        a_items = [a_val]
    for attr in a_items:
        if isinstance(attr, pikepdf.Dictionary):
            if pikepdf_helpers.get_array(attr, "/BBox") is not None:
                return True
    return False


def check_figure_elements_have_bbox(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA (PAC 2024): every Figure carries a ``/BBox`` attribute.

    Reads :attr:`AuditContext.elements`. PAC 2024 introduced the
    requirement so that assistive technology can position Figures
    correctly on the page. PASSes when there are no Figures at all,
    when every Figure has a ``/BBox`` (directly or via ``/A``), or
    FAILs with the indices of those that don't.
    """
    figure_elements = [e for e in ctx.elements if e.resolved_tag == "Figure"]
    if not figure_elements:
        return [
            CheckResult(
                name="Figure elements have BBox attribute",
                standard="PDF/UA (PAC 2024)",
                result="NA",
                details="No Figure elements in document",
            )
        ]

    figures_without_bbox: list[int] = []
    for elem in figure_elements:
        if not _figure_has_bbox(elem.obj):
            figures_without_bbox.append(elem.index)

    if not figures_without_bbox:
        return [
            CheckResult(
                name="Figure elements have BBox attribute",
                standard="PDF/UA (PAC 2024)",
                result="PASS",
                details=(
                    f"All {len(figure_elements)} Figure element(s)"
                    + " have /BBox attribute"
                ),
            )
        ]
    refs = " ".join(f"[{i + 1}]" for i in figures_without_bbox[:5])
    return [
        CheckResult(
            name="Figure elements have BBox attribute",
            standard="PDF/UA (PAC 2024)",
            result="FAIL",
            details=(
                f"{len(figures_without_bbox)} of {len(figure_elements)}"
                + f" Figure element(s) missing /BBox attribute: {refs}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
IMAGES_ALT_TEXT_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_alt_text_on_figure_art,
    check_alt_text_free_of_redundant_role_text,
    check_alt_text_does_not_hide_interactive_elements,
    check_figure_elements_have_bbox,
]


__all__ = [
    "IMAGES_ALT_TEXT_CHECKS",
    "check_alt_text_does_not_hide_interactive_elements",
    "check_alt_text_free_of_redundant_role_text",
    "check_alt_text_on_figure_art",
    "check_figure_elements_have_bbox",
]
