"""The element-reference convention, and what the viewer draws from it.

A check that blames particular elements says so in its details —
``2 table(s) missing THead/TBody: [12] Table; [30] Table``. Those numbers
are 1-based, and :func:`auto_a11y.pdf.audit.pipeline.with_referenced_elements`
turns them into the element indices the viewer needs to draw an overlay.

That makes the convention load-bearing rather than cosmetic. A check that
prints a raw 0-based index puts the overlay on the element *before* the
one at fault, which is worse than drawing nothing: it is confidently
wrong, and nothing in the report reveals it. A third of the reference
sites in the engine printed 0-based before this landed, so the same
document showed ``[5]`` meaning two different elements depending on which
section you were reading.

These tests pin both halves — the printed convention, and the parse.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import pikepdf
import pytest

from auto_a11y.pdf.audit import structure
from auto_a11y.pdf.audit.issue_map import build_issue_map
from auto_a11y.pdf.audit.non_text_contrast import (
    FieldContrast,
    FieldContrastFinding,
    GraphicContrast,
    NonTextContrast,
    summarise_fields,
    summarise_graphics,
)
from auto_a11y.pdf.audit.pipeline import with_referenced_elements, run_audit
from auto_a11y.pdf.models import CheckResult

REPO_ROOT = Path(__file__).resolve().parents[2]

#: A reference followed by the tag name the check printed beside it.
_REF_WITH_TAG = re.compile(r"\[(\d+)\]\s+([A-Za-z][A-Za-z0-9]*)")

#: Tag names a check might print after a reference. Anything outside this
#: set is prose ("[3] before [7]") rather than a tag claim.
_TAG_NAMES = frozenset({
    "Annot", "Art", "Caption", "Document", "Figure", "Form", "Formula",
    "H", "H1", "H2", "H3", "H4", "H5", "H6", "L", "LBody", "LI", "Lbl",
    "Link", "Note", "P", "Ruby", "Sect", "Span", "TD", "TH", "TOC",
    "TOCI", "TR", "Table", "Warichu",
})

#: Tagged documents in the repo, used as the corpus for the convention
#: guard. Untagged PDFs have no elements to reference and prove nothing.
_TAGGED_PDF = REPO_ROOT / "docs" / "Auto_A11y_API_Guide.pdf"


def _issues(payload: dict[str, object]) -> list[dict[str, object]]:
    """The issue list out of an issue-map payload, typed for the checkers."""
    raw = payload.get("issues")
    assert isinstance(raw, list)
    issues: list[dict[str, object]] = []
    for entry in cast("list[object]", raw):
        assert isinstance(entry, dict)
        issues.append(cast("dict[str, object]", entry))
    return issues


def _result(details: str, elements: tuple[int, ...] = ()) -> CheckResult:
    return CheckResult(
        name="Example", standard="WCAG 1.3.1", result="FAIL",
        details=details, elements=elements,
    )


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------


def test_a_printed_reference_becomes_a_zero_based_element_index() -> None:
    """``[1]`` is the first element, index 0."""
    filled = with_referenced_elements(_result("bad: [1] P"), element_count=5)

    assert filled.elements == (0,)


def test_several_references_are_kept_in_the_order_printed() -> None:
    filled = with_referenced_elements(
        _result("bad: [3] Table; [1] P; [7] TH"), element_count=10
    )

    assert filled.elements == (2, 0, 6)


def test_a_repeated_reference_is_recorded_once() -> None:
    filled = with_referenced_elements(
        _result("[2] TD and [2] TD again"), element_count=5
    )

    assert filled.elements == (1,)


def test_a_reference_past_the_end_of_the_document_is_dropped() -> None:
    """A number that names no element is not a location."""
    filled = with_referenced_elements(
        _result("[3] P and [99] P"), element_count=5
    )

    assert filled.elements == (2,)


def test_zero_is_dropped_because_references_are_one_based() -> None:
    filled = with_referenced_elements(_result("[0] P"), element_count=5)

    assert filled.elements == ()


def test_a_check_that_set_its_own_elements_is_left_alone() -> None:
    """Explicit beats parsed — the check knows more than its prose does."""
    original = _result("mentions [1] and [2]", elements=(7,))

    assert with_referenced_elements(original, element_count=10) is original


def test_details_with_no_references_yield_no_elements() -> None:
    filled = with_referenced_elements(
        _result("No /Lang attribute in document catalog."), element_count=5
    )

    assert filled.elements == ()


def test_a_page_number_is_not_mistaken_for_an_element() -> None:
    """Pages are printed as ``p.3`` / ``page(s) 3``, never bracketed."""
    filled = with_referenced_elements(
        _result("untagged text on page(s) 3, 4"), element_count=99
    )

    assert filled.elements == ()


# ---------------------------------------------------------------------------
# The convention, across a real document
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _TAGGED_PDF.is_file(), reason="corpus PDF absent")
def test_every_printed_reference_names_the_tag_it_resolves_to() -> None:
    """The strongest available check that the references are 1-based.

    Where a check prints ``[12] Table`` we can verify the claim: element
    12 (1-based) really is a Table. An off-by-one would land on its
    neighbour and almost always disagree.
    """
    with pikepdf.open(_TAGGED_PDF) as pdf:
        elements, _ = structure.walk_structure_tree(pdf)
    tags = {element.index: element.resolved_tag for element in elements}

    audit = run_audit(_TAGGED_PDF)

    mismatches: list[str] = []
    for check in audit.check_results:
        for match in _REF_WITH_TAG.finditer(check.details):
            printed, named = int(match.group(1)), match.group(2)
            if named not in _TAG_NAMES:
                continue
            actual = tags.get(printed - 1)
            if actual is not None and actual != named:
                mismatches.append(
                    f"{check.name}: printed {match.group(0)!r} but element"
                    + f" {printed} is a {actual}"
                )

    assert not mismatches, "\n".join(mismatches)


@pytest.mark.skipif(not _TAGGED_PDF.is_file(), reason="corpus PDF absent")
def test_findings_on_a_tagged_document_are_locatable() -> None:
    """The regression this exists to prevent.

    Before the parse landed, exactly one check in the engine populated
    ``elements``, so the viewer had nothing to draw and every finding
    appeared as a sidebar card with no overlay.
    """
    audit = run_audit(_TAGGED_PDF)
    faults = [c for c in audit.check_results if c.result in ("FAIL", "WARN")]
    locatable = [c for c in faults if c.elements]

    assert faults, "corpus document should have findings"
    assert len(locatable) >= len(faults) // 3, (
        f"only {len(locatable)} of {len(faults)} findings carry element"
        " references — the viewer can draw almost nothing"
    )


@pytest.mark.skipif(not _TAGGED_PDF.is_file(), reason="corpus PDF absent")
def test_the_issue_map_carries_boxes_for_located_findings() -> None:
    audit = run_audit(_TAGGED_PDF)
    issue_map = audit.report_sections.get("issue_map")

    assert isinstance(issue_map, dict)
    issues = _issues(cast("dict[str, object]", issue_map))

    with_box = [issue for issue in issues if issue.get("bbox")]
    assert with_box, "no issue has a bounding box; the viewer draws nothing"


# ---------------------------------------------------------------------------
# Form-field geometry
# ---------------------------------------------------------------------------


def _contrast_data(
    *,
    border_pass: bool | None,
    boundary_pass: bool | None,
    exempt: str | None = None,
) -> NonTextContrast:
    finding = FieldContrastFinding(
        field_name="surname",
        field_type="Text",
        page=2,
        is_readonly=exempt == "readonly",
        border_color=(0.9, 0.9, 0.9),
        border_source="/MK/BC",
        field_bg_color=(1.0, 1.0, 1.0),
        field_bg_source="/MK/BG",
        page_bg_color=(1.0, 1.0, 1.0),
        border_contrast=1.24,
        boundary_contrast=1.0,
        border_pass=border_pass,
        boundary_pass=boundary_pass,
        rect=(72.0, 640.0, 300.0, 660.0),
        exempt_reason=exempt,
    )
    return NonTextContrast(
        fields=FieldContrast(
            findings=[finding], summary=summarise_fields([finding]),
        ),
        graphics=GraphicContrast(findings=[], summary=summarise_graphics([])),
    )


def test_a_failing_field_is_drawn_on_its_own_rectangle() -> None:
    """Field geometry is stated in the PDF, so it needs no structure tag.

    The fields that fail this check are frequently the untagged ones,
    which is exactly when the structure tree cannot locate them.
    """
    payload = build_issue_map(
        [], {}, None,
        non_text_contrast=_contrast_data(
            border_pass=False, boundary_pass=False,
        ),
    )

    issues = _issues(payload)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["page"] == 3  # 0-based internally, 1-based for the viewer
    assert issue["bbox"] == [72.0, 640.0, 300.0, 660.0]
    assert "surname" in str(issue["detail"])


def test_a_passing_field_is_not_drawn() -> None:
    payload = build_issue_map(
        [], {}, None,
        non_text_contrast=_contrast_data(border_pass=True, boundary_pass=True),
    )

    assert payload["issues"] == []


def test_an_exempt_field_is_not_drawn() -> None:
    """Nothing for a reader to look at where nothing is being claimed."""
    payload = build_issue_map(
        [], {}, None,
        non_text_contrast=_contrast_data(
            border_pass=False, boundary_pass=False, exempt="readonly",
        ),
    )

    assert payload["issues"] == []


def test_a_field_saved_by_its_border_is_not_drawn() -> None:
    """A fill matching the page is fine when the border is visible."""
    payload = build_issue_map(
        [], {}, None,
        non_text_contrast=_contrast_data(
            border_pass=True, boundary_pass=False,
        ),
    )

    assert payload["issues"] == []
