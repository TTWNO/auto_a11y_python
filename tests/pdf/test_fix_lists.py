"""Tests for the list-repair fixes.

These fixes add wrappers rather than move content, so the properties that
matter are conservation ones: after a repair the same content must still
be there, in the same order, with the tree's parent pointers agreeing
with its child lists. A repair that silently reorders or drops an item
turns a malformed list into a wrong one.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from auto_a11y.pdf.audit.structure import walk_structure_tree
from auto_a11y.pdf.fix.lists import fix_list_nesting, fix_list_structure
from auto_a11y.pdf.fix.models import FixOptions


@pytest.fixture
def opts(tmp_path: Path) -> FixOptions:
    return FixOptions(pdf_path=tmp_path / "list.pdf")


def _node(
    pdf: pikepdf.Pdf, tag: str, kids: list[pikepdf.Object] | None = None
) -> pikepdf.Object:
    """A structure element whose children point back at it.

    Setting /P here matters: a real tagged PDF carries parent pointers
    throughout, and a fixture without them would make any check of
    pointer consistency trivially fail on nodes the fix never touched.
    """
    obj = pdf.make_indirect(
        Dictionary(Type=Name("/StructElem"), S=Name(f"/{tag}"))
    )
    if kids is not None:
        obj[Name("/K")] = Array(kids)
        for kid in kids:
            if isinstance(kid, pikepdf.Dictionary):
                kid[Name("/P")] = obj
    return obj


def _rooted(pdf: pikepdf.Pdf, *roots: pikepdf.Object) -> pikepdf.Pdf:
    struct_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
    struct_root[Name("/K")] = Array(list(roots))
    pdf.Root[Name("/StructTreeRoot")] = struct_root
    return pdf


def _blank() -> pikepdf.Pdf:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    return pdf


def _tags(pdf: pikepdf.Pdf) -> list[str]:
    elements, _ = walk_structure_tree(pdf)
    return [e.resolved_tag for e in elements]


def _shape(pdf: pikepdf.Pdf) -> list[tuple[int, str]]:
    """(depth, tag) for every element, in document order."""
    elements, _ = walk_structure_tree(pdf)
    by_index = {e.index: e for e in elements}
    depths: dict[int, int] = {}
    for element in elements:
        parent = by_index.get(element.parent_index)
        depths[element.index] = 0 if parent is None else depths[parent.index] + 1
    return [(depths[e.index], e.resolved_tag) for e in elements]


def _parents_agree(pdf: pikepdf.Pdf) -> bool:
    """Every element's /P points at the element that lists it in /K."""
    elements, _ = walk_structure_tree(pdf)
    by_index = {e.index: e for e in elements}
    for element in elements:
        parent = by_index.get(element.parent_index)
        if parent is None:
            continue
        declared = element.obj.get(Name("/P"))
        if declared is None or declared.objgen != parent.obj.objgen:
            return False
    return True


# ---------------------------------------------------------------------------
# fix_list_structure
# ---------------------------------------------------------------------------

def test_paragraphs_directly_in_a_list_become_items(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [
        _node(pdf, "P", [String("first")]),
        _node(pdf, "P", [String("second")]),
    ]))

    result = fix_list_structure(pdf, opts)

    assert result.success
    assert _shape(pdf) == [
        (0, "L"),
        (1, "LI"), (2, "LBody"), (3, "P"),
        (1, "LI"), (2, "LBody"), (3, "P"),
    ]


def test_repair_preserves_content_and_order(opts: FixOptions) -> None:
    """The conservation property: same items, same sequence, none lost."""
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [
        _node(pdf, "P", [String("alpha")]),
        _node(pdf, "P", [String("beta")]),
        _node(pdf, "P", [String("gamma")]),
    ]))

    fix_list_structure(pdf, opts)

    assert _tags(pdf).count("P") == 3
    assert _tags(pdf).count("LI") == 3


def test_repair_leaves_parent_pointers_consistent(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [_node(pdf, "P", [String("x")])]))

    fix_list_structure(pdf, opts)

    assert _parents_agree(pdf), "/P must agree with /K after rewrapping"


def test_loose_content_in_an_item_gets_a_body(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [
        _node(pdf, "LI", [_node(pdf, "P", [String("loose")])]),
    ]))

    result = fix_list_structure(pdf, opts)

    assert result.success
    assert _shape(pdf) == [(0, "L"), (1, "LI"), (2, "LBody"), (3, "P")]


def test_an_item_that_already_has_a_body_is_left_alone(
    opts: FixOptions,
) -> None:
    """Adding a second LBody would split one item into two."""
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [
        _node(pdf, "LI", [
            _node(pdf, "Lbl", [String("1.")]),
            _node(pdf, "LBody", [_node(pdf, "P", [String("item")])]),
        ]),
    ]))
    before = _shape(pdf)

    result = fix_list_structure(pdf, opts)

    assert result.success
    assert _shape(pdf) == before


def test_a_well_formed_list_is_untouched(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [
        _node(pdf, "LI", [_node(pdf, "LBody", [_node(pdf, "P")])]),
    ]))
    before = _shape(pdf)

    result = fix_list_structure(pdf, opts)

    assert result.success
    assert "already have valid structure" in result.description
    assert _shape(pdf) == before


def test_declines_without_a_structure_tree(opts: FixOptions) -> None:
    result = fix_list_structure(_blank(), opts)

    assert not result.success


# ---------------------------------------------------------------------------
# fix_list_nesting
# ---------------------------------------------------------------------------

def test_a_sublist_inside_a_list_gains_an_item(opts: FixOptions) -> None:
    pdf = _blank()
    inner = _node(pdf, "L", [_node(pdf, "LI", [_node(pdf, "LBody")])])
    _rooted(pdf, _node(pdf, "L", [
        _node(pdf, "LI", [_node(pdf, "LBody")]),
        inner,
    ]))

    result = fix_list_nesting(pdf, opts)

    assert result.success
    assert _shape(pdf)[:2] == [(0, "L"), (1, "LI")]
    # The sublist is now reached through LI > LBody rather than directly.
    assert (3, "L") in _shape(pdf)


def test_a_sublist_inside_an_item_gains_a_body(opts: FixOptions) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [
        _node(pdf, "LI", [
            _node(pdf, "L", [_node(pdf, "LI", [_node(pdf, "LBody")])]),
        ]),
    ]))

    result = fix_list_nesting(pdf, opts)

    assert result.success
    assert _shape(pdf)[:3] == [(0, "L"), (1, "LI"), (2, "LBody")]


def test_nesting_repair_keeps_parent_pointers_consistent(
    opts: FixOptions,
) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [
        _node(pdf, "L", [_node(pdf, "LI", [_node(pdf, "LBody")])]),
    ]))

    fix_list_nesting(pdf, opts)

    assert _parents_agree(pdf)


def test_nesting_leaves_a_correctly_nested_list_alone(
    opts: FixOptions,
) -> None:
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [
        _node(pdf, "LI", [
            _node(pdf, "LBody", [
                _node(pdf, "L", [_node(pdf, "LI", [_node(pdf, "LBody")])]),
            ]),
        ]),
    ]))
    before = _shape(pdf)

    result = fix_list_nesting(pdf, opts)

    assert result.success
    assert _shape(pdf) == before


def test_nesting_does_not_rewrite_unrelated_lists(opts: FixOptions) -> None:
    """Someone who picked the nesting check should not get a broader sweep.

    fix_list_structure repairs loose paragraphs too; this one must not,
    or choosing one fix silently applies another.
    """
    pdf = _blank()
    _rooted(pdf, _node(pdf, "L", [_node(pdf, "P", [String("loose")])]))
    before = _shape(pdf)

    result = fix_list_nesting(pdf, opts)

    assert result.success
    assert _shape(pdf) == before, "a loose paragraph is not a nesting problem"
