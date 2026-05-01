"""Interactive-element accessibility checks.

Five checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py`` (lines ~5429-5615):

* :func:`check_label_in_name` — visible text must appear in accessible
  name (WCAG 2.5.3).
* :func:`check_interactive_target_size` — link annotations meet the
  WCAG 2.5.5 / 2.5.8 minimum click-target size (24pt).
* :func:`check_focus_indicator_visibility` — link annotations carry
  visible borders so the viewer's focus ring has somewhere to render
  (WCAG 2.4.7).
* :func:`check_focus_not_obscured` — interactive (Widget / Link)
  annotations are not completely covered by other interactive
  annotations on the same page (WCAG 2.4.11).
* :func:`check_dragging_movement_alternatives` — Widget annotations do
  not depend on mouse-event JavaScript that would be impossible to
  trigger without a pointing device (WCAG 2.5.7).

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes an
:data:`INTERACTIVE_CHECKS` registry list that the Phase 5.3 orchestrator
iterates. ``CheckResult`` ``name`` and ``standard`` strings match
pdfMax verbatim so Phase 6's check catalogue can map them.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------


#: WCAG 2.5.8 minimum target size in PDF points (24 CSS px ≈ 18pt;
#: pdfMax used 24pt directly to keep a margin). Mirrors pdfMax's
#: ``MIN_TARGET_HEIGHT`` constant.
_MIN_TARGET_HEIGHT: float = 24.0

#: Tags whose accessible name (``/Alt``) is allowed to override visible
#: text. Mirrors pdfMax's eligibility tuple verbatim.
_LABEL_IN_NAME_TAGS: frozenset[str] = frozenset(
    {"Link", "H1", "H2", "H3", "H4", "H5", "H6", "Art", "Figure", "Span"}
)

#: Word-extraction pattern for the Label-in-Name overlap calculation.
#: Matches sequences of 3+ word characters; mirrors pdfMax's
#: ``r'\w{3,}'`` literal.
_WORD_RE: re.Pattern[str] = re.compile(r"\w{3,}")

#: ``/AA`` keys that fire on mouse activity for a Widget annotation:
#: ``/D`` mouse-down, ``/U`` mouse-up, ``/E`` mouse-enter, ``/X``
#: mouse-exit. Mirrors pdfMax's tuple verbatim.
_MOUSE_AA_KEYS: tuple[str, ...] = ("/D", "/U", "/E", "/X")

#: Substrings (case-folded) in an /AA JavaScript payload that suggest
#: drag-based interaction. Mirrors pdfMax's tuple verbatim.
_DRAG_TERMS: tuple[str, ...] = ("drag", "mouse", "pointer", "move")


# ---------------------------------------------------------------------------
# Helpers shared across the annotation-iterating checks
# ---------------------------------------------------------------------------


def _iter_page_annot_dicts(
    page_obj: pikepdf.Dictionary,
) -> list[pikepdf.Dictionary]:
    """Yield every dictionary-shaped annotation on a page.

    Annotation arrays may legitimately contain non-dictionary entries
    (e.g. dangling indirect references) — those are skipped rather than
    raising. Mirrors the helper used by the language checks.
    """
    annots = pikepdf_helpers.get_array(page_obj, "/Annots")
    if annots is None:
        return []
    out: list[pikepdf.Dictionary] = []
    for ai in range(len(annots)):
        item = annots[ai]
        if isinstance(item, pikepdf.Dictionary):
            out.append(item)
    return out


def _annot_subtype_str(annot: pikepdf.Dictionary) -> str:
    """Render an annotation's ``/Subtype`` as a slash-prefixed string.

    Returns the empty string when ``/Subtype`` is absent or not a Name.
    """
    name = pikepdf_helpers.get_name(annot, "/Subtype")
    return str(name) if name is not None else ""


def _read_rect(
    annot: pikepdf.Dictionary,
) -> tuple[float, float, float, float] | None:
    """Read ``/Rect`` from an annotation as four floats, or ``None``.

    Returns ``None`` when ``/Rect`` is absent, not a four-element Array,
    or any entry fails ``float()`` coercion.
    """
    rect = pikepdf_helpers.get_array(annot, "/Rect")
    if rect is None or len(rect) != 4:
        return None
    try:
        x0 = float(rect[0])
        y0 = float(rect[1])
        x1 = float(rect[2])
        y1 = float(rect[3])
    except (TypeError, ValueError):
        return None
    return (x0, y0, x1, y1)


def _build_annot_struct_map(
    elements: list[StructElement],
) -> dict[int, tuple[int, str]]:
    """Map ``id(annotation)`` to the parent structure element index/tag.

    Mirrors pdfMax's :func:`build_annotation_structure_map`. Walks each
    element's ``/K`` array, picks out ``OBJR`` children, and records the
    ``(element_index, resolved_tag)`` of the parent for each annotation
    object referenced.
    """
    annot_to_elem: dict[int, tuple[int, str]] = {}
    for elem in elements:
        try:
            k_obj = elem.obj["/K"]
        except KeyError:
            continue
        items: list[pikepdf.Object]
        if isinstance(k_obj, pikepdf.Array):
            items = [k_obj[i] for i in range(len(k_obj))]
        else:
            items = [k_obj]
        for child in items:
            if not isinstance(child, pikepdf.Dictionary):
                continue
            child_type = pikepdf_helpers.get_name(child, "/Type")
            if child_type is None or str(child_type) != "/OBJR":
                continue
            try:
                obj_ref = child["/Obj"]
            except KeyError:
                continue
            annot_to_elem[id(obj_ref)] = (elem.index, elem.resolved_tag)
    return annot_to_elem


def _find_link_element_by_uri(
    elements: list[StructElement], uri: str,
) -> int | None:
    """Best-effort fallback for untagged /Link annotations.

    Mirrors pdfMax: when an annotation isn't in ``annot_struct_map``,
    look for an element whose text content contains the URI's first
    30 characters (or vice versa) and return the index of the longest
    such match. Returns ``None`` when nothing plausibly matches.
    """
    if not uri:
        return None
    uri_lower = (
        uri.lower()
        .replace("https://", "")
        .replace("http://", "")
        .rstrip("/")
    )
    if not uri_lower:
        return None
    best_match: int | None = None
    best_len = 0
    needle = uri_lower[:30]
    for elem in elements:
        tc = elem.text_content
        if not tc:
            continue
        tc_lower = tc.lower().strip()
        if needle in tc_lower or tc_lower in uri_lower:
            if len(tc_lower) > best_len:
                best_len = len(tc_lower)
                best_match = elem.index
    return best_match


# ---------------------------------------------------------------------------
# check_label_in_name
# ---------------------------------------------------------------------------


def check_label_in_name(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 2.5.3: accessible name must contain visible text.

    Reads :attr:`AuditContext.elements`. Only Link / H1-H6 / Art /
    Figure / Span elements with both ``alt_text`` and ``text_content``
    set are evaluated. Visible text shorter than 3 characters is
    skipped; word-level overlap is computed via 3+ char word tokens.
    Below 50% overlap FAILs with the visible/accessible quoted; between
    50% and 80% with 4+ visible words FAILs with the missing words.
    """
    label_name_issues: list[str] = []
    for elem in ctx.elements:
        if not elem.alt_text or not elem.text_content:
            continue
        if elem.resolved_tag not in _LABEL_IN_NAME_TAGS:
            continue

        visible = elem.text_content.strip().lower()
        accessible = elem.alt_text.strip().lower()
        if not visible or not accessible or len(visible) < 3:
            continue

        visible_words = set(_WORD_RE.findall(visible))
        accessible_words = set(_WORD_RE.findall(accessible))
        if not visible_words:
            continue

        overlap = visible_words & accessible_words
        coverage = len(overlap) / len(visible_words)

        if coverage < 0.5:
            label_name_issues.append(
                f'[{elem.index + 1}] {elem.resolved_tag}: visible text'
                + f' "{visible[:50]}" not found in accessible name'
                + f' "{accessible[:50]}" ({coverage:.0%} word overlap)'
            )
        elif coverage < 0.8 and len(visible_words) > 3:
            missing = visible_words - accessible_words
            if missing:
                label_name_issues.append(
                    f"[{elem.index + 1}] {elem.resolved_tag}: visible"
                    + " text partially missing from accessible name —"
                    + f" missing words: {', '.join(sorted(missing)[:5])}"
                )

    if not label_name_issues:
        return [
            CheckResult(
                name="Label in Name",
                standard="WCAG 2.5.3",
                result="PASS",
                details="All accessible names contain their visible text",
            )
        ]
    return [
        CheckResult(
            name="Label in Name",
            standard="WCAG 2.5.3",
            result="FAIL",
            details="; ".join(label_name_issues),
        )
    ]


# ---------------------------------------------------------------------------
# Link-annotation walk shared by target-size and focus-visibility
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LinkAnnotInfo:
    """One /Link annotation's measurements collected during the walk."""

    rect: tuple[float, float, float, float] | None
    border_width: float | None  # ``None`` if no /Border or unparseable
    uri: str
    elem_idx: int | None


def _collect_link_annots(ctx: AuditContext) -> list[_LinkAnnotInfo]:
    """Walk every page's annotations once for the target-size + focus checks.

    Returns one :class:`_LinkAnnotInfo` per ``/Link`` annotation, in
    page-then-array order. ``elem_idx`` is the parent structure-element
    index (when the annotation is in the structure tree) or the best
    text-overlap match for an untagged annotation.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    out: list[_LinkAnnotInfo] = []
    for page in ctx.pdf.pages:
        for annot in _iter_page_annot_dicts(page.obj):
            if _annot_subtype_str(annot) != "/Link":
                continue
            rect = _read_rect(annot)

            border_width: float | None = None
            border = pikepdf_helpers.get_array(annot, "/Border")
            if border is not None and len(border) >= 3:
                try:
                    border_width = float(border[2])
                except (TypeError, ValueError):
                    border_width = None

            mapping = annot_struct_map.get(id(annot))
            elem_idx = mapping[0] if mapping else None

            uri = ""
            action = pikepdf_helpers.get_dict(annot, "/A")
            if action is not None:
                uri_str = pikepdf_helpers.get_string(action, "/URI")
                if uri_str is not None:
                    uri = uri_str
            if elem_idx is None and uri:
                elem_idx = _find_link_element_by_uri(ctx.elements, uri)
            out.append(_LinkAnnotInfo(
                rect=rect,
                border_width=border_width,
                uri=uri,
                elem_idx=elem_idx,
            ))
    return out


# ---------------------------------------------------------------------------
# check_interactive_target_size
# ---------------------------------------------------------------------------


def check_interactive_target_size(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 2.5.5 / 2.5.8: link annotations meet the 24pt minimum size.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Each ``/Link`` annotation must be at least
    :data:`_MIN_TARGET_HEIGHT` points in both width and height. WARNs
    when any link is below threshold; PASSes otherwise (including when
    the document has no links).
    """
    links = _collect_link_annots(ctx)
    small_targets: list[str] = []
    for link in links:
        if link.rect is None:
            continue
        x0, y0, x1, y1 = link.rect
        w = x1 - x0
        h = y1 - y0
        if h < _MIN_TARGET_HEIGHT or w < _MIN_TARGET_HEIGHT:
            elem_ref = f"[{link.elem_idx}]" if link.elem_idx is not None else ""
            uri_part = link.uri[:40] if link.uri else ""
            small_targets.append(
                f"{elem_ref} Link '{uri_part}' ({w:.0f}x{h:.0f}pt)"
            )

    if small_targets:
        return [
            CheckResult(
                name="Interactive element target size",
                standard="WCAG 2.5.5, 2.5.8",
                result="WARN",
                details=(
                    f"{len(small_targets)} link(s) have small click"
                    + f" targets (below {int(_MIN_TARGET_HEIGHT)}pt): "
                    + "; ".join(small_targets[:3])
                ),
            )
        ]
    return [
        CheckResult(
            name="Interactive element target size",
            standard="WCAG 2.5.5, 2.5.8",
            result="PASS",
            details=f"All {len(links)} link targets meet minimum size",
        )
    ]


# ---------------------------------------------------------------------------
# check_focus_indicator_visibility
# ---------------------------------------------------------------------------


def check_focus_indicator_visibility(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 2.4.7: link annotations have visible borders.

    A zero-width ``/Border`` array entry forces the PDF viewer to draw
    its own focus ring (which not every viewer does). When every link
    has a zero border WARNs that focus visibility depends on the
    viewer; when only some do WARNs about partial suppression; when
    none do (or there are no links) PASSes.
    """
    links = _collect_link_annots(ctx)
    total_links = len(links)
    suppressed_borders = 0
    suppressed_border_refs: list[str] = []
    for link in links:
        if link.border_width is None:
            continue
        if link.border_width == 0.0:
            suppressed_borders += 1
            if link.elem_idx is not None and len(suppressed_border_refs) < 5:
                suppressed_border_refs.append(f"[{link.elem_idx}]")

    if suppressed_borders == total_links and total_links > 0:
        refs_str = (
            f" ({' '.join(suppressed_border_refs)})"
            if suppressed_border_refs
            else ""
        )
        return [
            CheckResult(
                name="Focus indicator visibility",
                standard="WCAG 2.4.7",
                result="WARN",
                details=(
                    f"All {total_links} link annotations have zero-width"
                    + f" borders{refs_str} — focus visibility depends"
                    + " entirely on PDF viewer"
                ),
            )
        ]
    if suppressed_borders > 0:
        refs_str = (
            f" ({' '.join(suppressed_border_refs)})"
            if suppressed_border_refs
            else ""
        )
        return [
            CheckResult(
                name="Focus indicator visibility",
                standard="WCAG 2.4.7",
                result="WARN",
                details=(
                    f"{suppressed_borders} of {total_links} links have"
                    + f" suppressed borders{refs_str}"
                ),
            )
        ]
    return [
        CheckResult(
            name="Focus indicator visibility",
            standard="WCAG 2.4.7",
            result="PASS",
            details="Link annotations have visible borders",
        )
    ]


# ---------------------------------------------------------------------------
# check_focus_not_obscured
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _InteractiveRect:
    """One interactive (Widget / Link) annotation's normalised geometry."""

    rect: tuple[float, float, float, float]
    subtype: str
    label: str


def _annot_label(annot: pikepdf.Dictionary, subtype: str) -> str:
    """Pick a human-readable label for a Widget / Link annotation.

    Mirrors pdfMax's precedence: ``/T`` (field name) → ``/Contents``
    (truncated to 30 chars) → the subtype name itself.
    """
    t_name = pikepdf_helpers.get_string(annot, "/T")
    if t_name:
        return t_name
    contents = pikepdf_helpers.get_string(annot, "/Contents")
    if contents:
        return contents[:30]
    return subtype


def check_focus_not_obscured(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 2.4.11: interactive annotations are not completely covered.

    For every page, normalises the rectangles of all ``/Widget`` and
    ``/Link`` annotations and reports any annotation whose rectangle is
    fully contained within another's. Zero-area annotations are also
    reported. Annotations with no parseable ``/Rect`` are skipped.
    """
    obscured_issues: list[str] = []
    total_interactive = 0
    for page_idx, page in enumerate(ctx.pdf.pages):
        page_num = page_idx + 1
        interactive_rects: list[_InteractiveRect] = []
        for annot in _iter_page_annot_dicts(page.obj):
            subtype = _annot_subtype_str(annot)
            if subtype not in ("/Widget", "/Link"):
                continue
            raw_rect = _read_rect(annot)
            if raw_rect is None:
                continue
            x0, y0, x1, y1 = raw_rect
            rx0, ry0 = min(x0, x1), min(y0, y1)
            rx1, ry1 = max(x0, x1), max(y0, y1)
            label = _annot_label(annot, subtype)
            w, h = rx1 - rx0, ry1 - ry0
            if w <= 0 or h <= 0:
                obscured_issues.append(
                    f"p{page_num}: zero-area {subtype} '{label}'"
                )
            interactive_rects.append(_InteractiveRect(
                rect=(rx0, ry0, rx1, ry1),
                subtype=subtype,
                label=label,
            ))
        total_interactive += len(interactive_rects)
        for i, a in enumerate(interactive_rects):
            ax0, ay0, ax1, ay1 = a.rect
            for j, b in enumerate(interactive_rects):
                if i == j:
                    continue
                bx0, by0, bx1, by1 = b.rect
                if bx0 <= ax0 and by0 <= ay0 and bx1 >= ax1 and by1 >= ay1:
                    obscured_issues.append(
                        f"p{page_num}: {a.subtype} '{a.label}'"
                        + f" completely obscured by {b.subtype}"
                    )
                    break

    if obscured_issues:
        return [
            CheckResult(
                name="Focus not obscured",
                standard="WCAG 2.4.11",
                result="FAIL",
                details=(
                    f"{len(obscured_issues)} interactive element(s)"
                    + " obscured: "
                    + "; ".join(obscured_issues[:5])
                ),
            )
        ]
    return [
        CheckResult(
            name="Focus not obscured",
            standard="WCAG 2.4.11",
            result="PASS",
            details=(
                "No interactive elements are obscured by overlapping"
                + f" content ({total_interactive} elements checked)"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_dragging_movement_alternatives
# ---------------------------------------------------------------------------


def check_dragging_movement_alternatives(
    ctx: AuditContext,
) -> list[CheckResult]:
    """WCAG 2.5.7: widgets don't depend on mouse-event JavaScript.

    For each ``/Widget`` annotation, inspects ``/AA`` mouse-event keys
    (``/D``, ``/U``, ``/E``, ``/X``). Any JavaScript action whose script
    body contains ``drag``, ``mouse``, ``pointer``, or ``move`` (case-
    insensitive) flags the widget as potentially mouse-dependent.
    Mirrors pdfMax verbatim.
    """
    drag_issues: list[str] = []
    for page_idx, page in enumerate(ctx.pdf.pages):
        page_num = page_idx + 1
        for annot in _iter_page_annot_dicts(page.obj):
            if _annot_subtype_str(annot) != "/Widget":
                continue
            aa_dict = pikepdf_helpers.get_dict(annot, "/AA")
            if aa_dict is None:
                continue
            mouse_actions: list[str] = []
            for key in _MOUSE_AA_KEYS:
                action = pikepdf_helpers.get_dict(aa_dict, key)
                if action is None:
                    continue
                action_subtype = pikepdf_helpers.get_name(action, "/S")
                if action_subtype is None or str(action_subtype) != "/JavaScript":
                    continue
                js = pikepdf_helpers.get_string(action, "/JS") or ""
                if any(term in js.lower() for term in _DRAG_TERMS):
                    mouse_actions.append(key.lstrip("/"))
            if mouse_actions:
                t_name = pikepdf_helpers.get_string(annot, "/T") or "unnamed"
                drag_issues.append(
                    f"p{page_num}: Widget '{t_name}' has mouse-dependent"
                    + f" JS ({', '.join(mouse_actions)})"
                )

    if drag_issues:
        return [
            CheckResult(
                name="Dragging movement alternatives",
                standard="WCAG 2.5.7",
                result="WARN",
                details=(
                    f"{len(drag_issues)} widget(s) may require dragging"
                    + " without alternatives: "
                    + "; ".join(drag_issues[:5])
                ),
            )
        ]
    return [
        CheckResult(
            name="Dragging movement alternatives",
            standard="WCAG 2.5.7",
            result="PASS",
            details="No interactive elements require dragging movements",
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
INTERACTIVE_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_label_in_name,
    check_interactive_target_size,
    check_focus_indicator_visibility,
    check_focus_not_obscured,
    check_dragging_movement_alternatives,
]


__all__ = [
    "INTERACTIVE_CHECKS",
    "check_dragging_movement_alternatives",
    "check_focus_indicator_visibility",
    "check_focus_not_obscured",
    "check_interactive_target_size",
    "check_label_in_name",
]
