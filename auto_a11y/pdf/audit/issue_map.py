"""Locating findings on the page, for the viewer's overlays.

The PDF viewer draws each finding as a box over the page and a line from
that box to its card in the sidebar. To do that it needs, per finding, a
page number and a rectangle in PDF coordinates.

Neither is something a check computes. A check knows *which structure
element* is at fault; the reading-order collector separately knows where
every element sits on the page. This module joins the two, so a check
only has to name an element to become locatable.

The payload deliberately matches the shape the viewer and
:mod:`auto_a11y.pdf.issue_map_counts` already read — it previously came
from a file written by an external pdfMax subprocess, which no packaged
build ever contained.
"""
from __future__ import annotations

from auto_a11y.pdf.audit.non_text_contrast import NonTextContrast
from auto_a11y.pdf.audit.reading_order import ElementPosition, PageDimensions

#: Shape version, carried in the payload the viewer reads.
ISSUE_MAP_VERSION = 1

#: Verdicts that become an overlay. A pass has nothing to point at, and
#: an informational note is not a fault the reader needs locating.
_LOCATABLE_VERDICTS = ("FAIL", "WARN")


def build_issue_map(
    check_results: list[object],
    element_positions: dict[int, ElementPosition] | None,
    page_dimensions: list[PageDimensions] | None = None,
    non_text_contrast: NonTextContrast | None = None,
) -> dict[str, object]:
    """Build the viewer's issue map from check verdicts and element geometry.

    Findings with no element reference, or whose element has no known
    position, are still listed — with ``page`` and ``bbox`` null. The
    viewer shows them as sidebar cards without an overlay, which is
    honest: the finding is real, we just cannot say where on the page it
    is. Dropping them would hide faults from the sidebar entirely.

    ``non_text_contrast`` is the one source of geometry that does not
    come through the structure tree. A form field's widget rectangle is
    stated outright in the PDF, so a field whose border fails 3:1 can be
    drawn exactly, on the field, without needing the field to be tagged
    — which the failing ones frequently are not.
    """
    positions = element_positions or {}
    issues: list[dict[str, object]] = []

    for index, check in enumerate(check_results):
        name = getattr(check, "name", None)
        verdict = getattr(check, "result", None)
        if not isinstance(name, str) or verdict not in _LOCATABLE_VERDICTS:
            continue

        details = getattr(check, "details", "")
        referenced = getattr(check, "elements", ())

        if not referenced:
            issues.append(_issue(index, name, verdict, details, None, None))
            continue

        for element_index in referenced:
            position = positions.get(element_index)
            issues.append(
                _issue(
                    index,
                    name,
                    verdict,
                    details,
                    element_index,
                    position,
                )
            )

    issues.extend(_field_issues(non_text_contrast, len(check_results)))

    # Keyed by 1-based page number, each [width, height]. The viewer needs
    # them to map PDF coordinates onto the rendered canvas, which is at a
    # different scale.
    dimensions: dict[str, list[float]] = {}
    for number, page in enumerate(page_dimensions or [], start=1):
        dimensions[str(number)] = [page.width, page.height]

    return {
        "version": ISSUE_MAP_VERSION,
        "page_dimensions": dimensions,
        "issues": issues,
    }


def _issue(
    ordinal: int,
    name: str,
    verdict: object,
    details: object,
    element_index: int | None,
    position: ElementPosition | None,
) -> dict[str, object]:
    """One issue entry, with geometry where it is known."""
    # The id has to survive a round trip through JSON and back into the
    # DOM, and be stable across a re-render so the sidebar and the
    # overlay agree on which card is selected.
    identifier = f"issue-{ordinal}"
    if element_index is not None:
        identifier = f"{identifier}-{element_index}"

    return {
        "id": identifier,
        "check_name": name,
        "check_result": verdict,
        "detail": details if isinstance(details, str) else "",
        # 1-based, matching every other element reference the user sees.
        "element_index": (
            element_index + 1 if element_index is not None else None
        ),
        "page": position.page + 1 if position is not None else None,
        # [x0, y0, x1, y1] in PDF coordinates, which is the shape the
        # viewer already reads — it converts to an object on receipt.
        "bbox": (
            [position.x0, position.y0, position.x1, position.y1]
            if position is not None
            else None
        ),
    }


def _field_issues(
    data: NonTextContrast | None, ordinal_base: int
) -> list[dict[str, object]]:
    """Overlays for form fields whose contrast fails, drawn on the field.

    Only the failures. A field that passes, or one exempted because it
    draws no border of its own, has nothing for the reader to look at.
    """
    if data is None or data.fields is None:
        return []

    entries: list[dict[str, object]] = []
    for offset, finding in enumerate(data.fields.findings):
        if finding.exempt_reason is not None:
            continue
        border_failed = finding.border_pass is False
        boundary_failed = (
            finding.boundary_pass is False and finding.border_pass is not True
        )
        if not (border_failed or boundary_failed):
            continue

        ratio = (
            finding.border_contrast if border_failed
            else finding.boundary_contrast
        )
        measured = f"{ratio:.2f}:1" if ratio is not None else "unmeasurable"
        reason = (
            "border contrast" if border_failed else "no visible boundary"
        )
        x0, y0, x1, y1 = finding.rect
        entries.append({
            "id": f"issue-{ordinal_base + offset}-field",
            "check_name": "Non-text contrast sufficient",
            "check_result": "FAIL",
            "detail": (
                f"{finding.field_type} field \u201c{finding.field_name}\u201d:"
                f" {reason} {measured}, below the 3:1 minimum"
            ),
            "element_index": None,
            "page": finding.page + 1,
            "bbox": [x0, y0, x1, y1],
        })
    return entries
