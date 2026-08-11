"""Tests for the /RoleMap fixes.

A role map is what gives a custom tag a standard meaning, so the tests
turn on resolution: an entry survives if following it reaches a real PDF
structure tag, and goes if it loops, redefines a standard tag, or lands
somewhere that means nothing.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Dictionary, Name

from auto_a11y.pdf.fix.models import FixOptions
from auto_a11y.pdf.fix.role_mapping import (
    fix_circular_roles,
    fix_role_mapping,
    fix_standard_remap,
)


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "doc.pdf")


def _pdf_with_role_map(mapping: dict[str, str] | None) -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    if mapping is not None:
        struct_root[Name("/RoleMap")] = Dictionary(
            {f"/{k}": Name(f"/{v}") for k, v in mapping.items()}
        )
    pdf.Root[Name("/StructTreeRoot")] = struct_root
    return pdf


def _role_map(pdf: pikepdf.Pdf) -> dict[str, str]:
    struct_root = pdf.Root[Name("/StructTreeRoot")]
    raw = struct_root.get(Name("/RoleMap"))
    if raw is None:
        return {}
    return {
        str(k).lstrip("/"): str(raw[k]).lstrip("/") for k in raw.keys()
    }


# ---------------------------------------------------------------------------
# fix_circular_roles
# ---------------------------------------------------------------------------

def test_a_two_tag_loop_is_removed_entirely(opts: FixOptions) -> None:
    """Both entries go.

    Breaking one edge would leave the survivor pointing at a name that is
    no longer defined — the same problem with fewer entries.
    """
    pdf = _pdf_with_role_map({"Heading": "Title", "Title": "Heading"})

    result = fix_circular_roles(pdf, opts)

    assert result.success
    assert _role_map(pdf) == {}


def test_a_tag_leading_into_a_loop_is_removed(opts: FixOptions) -> None:
    # Sidebar resolves no better than the loop it feeds into.
    pdf = _pdf_with_role_map(
        {"Sidebar": "Heading", "Heading": "Title", "Title": "Heading"}
    )

    fix_circular_roles(pdf, opts)

    assert _role_map(pdf) == {}


def test_valid_mappings_survive_alongside_a_loop(opts: FixOptions) -> None:
    pdf = _pdf_with_role_map(
        {"Heading1": "H1", "A": "B", "B": "A"}
    )

    result = fix_circular_roles(pdf, opts)

    assert result.success
    assert _role_map(pdf) == {"Heading1": "H1"}


def test_no_loops_reports_cleanly(opts: FixOptions) -> None:
    pdf = _pdf_with_role_map({"Heading1": "H1"})

    result = fix_circular_roles(pdf, opts)

    assert result.success
    assert "No circular role mappings" in result.description
    assert _role_map(pdf) == {"Heading1": "H1"}


def test_no_role_map_is_not_a_failure(opts: FixOptions) -> None:
    pdf = _pdf_with_role_map(None)

    result = fix_circular_roles(pdf, opts)

    assert result.success


# ---------------------------------------------------------------------------
# fix_standard_remap
# ---------------------------------------------------------------------------

def test_redefining_a_standard_tag_is_removed(opts: FixOptions) -> None:
    """Mapping P to Div makes every paragraph in the document a lie."""
    pdf = _pdf_with_role_map({"P": "Div", "Heading1": "H1"})

    result = fix_standard_remap(pdf, opts)

    assert result.success
    assert _role_map(pdf) == {"Heading1": "H1"}


def test_mapping_a_custom_tag_onto_a_standard_one_is_kept(
    opts: FixOptions,
) -> None:
    # This is what a role map is *for* — only the source side matters.
    pdf = _pdf_with_role_map({"MyHeading": "H2"})

    result = fix_standard_remap(pdf, opts)

    assert result.success
    assert _role_map(pdf) == {"MyHeading": "H2"}


# ---------------------------------------------------------------------------
# fix_role_mapping
# ---------------------------------------------------------------------------

def test_a_mapping_to_a_nonstandard_tag_is_removed(opts: FixOptions) -> None:
    pdf = _pdf_with_role_map({"MyTag": "SomethingInvented"})

    result = fix_role_mapping(pdf, opts)

    assert result.success
    assert _role_map(pdf) == {}


def test_a_chain_that_reaches_a_standard_tag_survives(
    opts: FixOptions,
) -> None:
    # Resolution follows the chain, so an indirect route is still valid.
    pdf = _pdf_with_role_map({"Outer": "Inner", "Inner": "H1"})

    result = fix_role_mapping(pdf, opts)

    assert result.success
    assert _role_map(pdf) == {"Outer": "Inner", "Inner": "H1"}


def test_a_chain_ending_outside_the_standard_set_is_removed(
    opts: FixOptions,
) -> None:
    pdf = _pdf_with_role_map({"Outer": "Inner", "Inner": "Invented"})

    fix_role_mapping(pdf, opts)

    assert _role_map(pdf) == {}


def test_a_looping_chain_is_removed(opts: FixOptions) -> None:
    # A loop never reaches a standard tag, so this is a superset of
    # fix_circular_roles.
    pdf = _pdf_with_role_map({"A": "B", "B": "A", "Good": "P"})

    result = fix_role_mapping(pdf, opts)

    assert result.success
    assert _role_map(pdf) == {"Good": "P"}


def test_all_valid_reports_cleanly(opts: FixOptions) -> None:
    pdf = _pdf_with_role_map({"MyHeading": "H1", "MyList": "L"})

    result = fix_role_mapping(pdf, opts)

    assert result.success
    assert "All role mappings resolve" in result.description
    assert len(_role_map(pdf)) == 2
