"""Tests for the typed PDF structure tree walker."""
from __future__ import annotations

import pikepdf

from auto_a11y.pdf.audit.structure import (
    STANDARD_PDF_TAGS,
    StructElement,
    populate_element_text,
    resolve_tag,
    walk_structure_tree,
)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_pdf_with_blank_page() -> pikepdf.Pdf:
    """Return a Pdf with a single blank letter-sized page."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    return pdf


def _attach_struct_root(
    pdf: pikepdf.Pdf,
    root_dict: pikepdf.Dictionary,
) -> None:
    """Attach a structure tree root onto pdf.Root."""
    indirect = pdf.make_indirect(root_dict)
    pdf.Root["/StructTreeRoot"] = indirect


# ─── resolve_tag ─────────────────────────────────────────────────────────────


def test_resolve_tag_returns_mapped_value() -> None:
    role_map: dict[str, str] = {"/MyHeading": "/H1"}
    assert resolve_tag("/MyHeading", role_map) == "/H1"


def test_resolve_tag_handles_cycle() -> None:
    """A cyclic role map must terminate, returning the last seen value."""
    role_map: dict[str, str] = {"/A": "/B", "/B": "/A"}
    # Either value is acceptable — what matters is we don't infinite-loop.
    result = resolve_tag("/A", role_map)
    assert result in {"/A", "/B"}


def test_resolve_tag_returns_input_when_missing() -> None:
    role_map: dict[str, str] = {}
    assert resolve_tag("/Unknown", role_map) == "/Unknown"


def test_resolve_tag_chains_through_intermediate_aliases() -> None:
    role_map: dict[str, str] = {"/A": "/B", "/B": "/C"}
    assert resolve_tag("/A", role_map) == "/C"


def test_resolve_tag_respects_max_depth() -> None:
    """An over-long chain should bail out instead of looping forever."""
    role_map: dict[str, str] = {f"/T{i}": f"/T{i+1}" for i in range(20)}
    # max_depth=3: /T0 → /T1 → /T2 → stops at /T2 (3 visits including starting node)
    result = resolve_tag("/T0", role_map, max_depth=3)
    assert result.startswith("/T")


# ─── STANDARD_PDF_TAGS ───────────────────────────────────────────────────────


def test_standard_pdf_tags_is_frozenset() -> None:
    assert isinstance(STANDARD_PDF_TAGS, frozenset)


def test_standard_pdf_tags_contains_common_tags() -> None:
    for expected in ("Document", "P", "H1", "H6", "Figure", "Table", "Span"):
        assert expected in STANDARD_PDF_TAGS


# ─── walk_structure_tree (no struct tree) ────────────────────────────────────


def test_walk_returns_empty_when_no_struct_tree() -> None:
    pdf = _make_pdf_with_blank_page()
    elements, role_map = walk_structure_tree(pdf)
    assert elements == []
    assert role_map == {}


# ─── walk_structure_tree (minimal Document → P) ──────────────────────────────


def test_walk_minimal_document_with_paragraph() -> None:
    pdf = _make_pdf_with_blank_page()
    paragraph = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/P"),
    )
    document = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/Document"),
        K=pikepdf.Array([paragraph]),
    )
    struct_root = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructTreeRoot"),
        K=pikepdf.Array([document]),
    )
    _attach_struct_root(pdf, struct_root)

    elements, role_map = walk_structure_tree(pdf)
    assert role_map == {}
    assert len(elements) == 2

    doc_elem = elements[0]
    assert doc_elem.resolved_tag == "Document"
    assert doc_elem.parent_index == -1
    assert doc_elem.children_indices == [1]

    p_elem = elements[1]
    assert p_elem.resolved_tag == "P"
    assert p_elem.parent_index == 0


# ─── walk_structure_tree extracts /Lang ──────────────────────────────────────


def test_walk_extracts_lang_attribute() -> None:
    pdf = _make_pdf_with_blank_page()
    paragraph = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/P"),
        Lang=pikepdf.String("fr-CA"),
    )
    struct_root = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructTreeRoot"),
        K=pikepdf.Array([paragraph]),
    )
    _attach_struct_root(pdf, struct_root)

    elements, _ = walk_structure_tree(pdf)
    assert len(elements) == 1
    assert elements[0].lang == "fr-CA"


# ─── walk_structure_tree extracts /Alt and /ActualText ───────────────────────


def test_walk_extracts_alt_and_actual_text() -> None:
    pdf = _make_pdf_with_blank_page()
    figure = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/Figure"),
        Alt=pikepdf.String("A red square"),
        ActualText=pikepdf.String("Square"),
    )
    struct_root = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructTreeRoot"),
        K=pikepdf.Array([figure]),
    )
    _attach_struct_root(pdf, struct_root)

    elements, _ = walk_structure_tree(pdf)
    assert len(elements) == 1
    assert elements[0].alt_text == "A red square"
    assert elements[0].actual_text == "Square"


# ─── walk_structure_tree resolves custom tags via /RoleMap ───────────────────


def test_walk_resolves_custom_tag_via_role_map() -> None:
    pdf = _make_pdf_with_blank_page()
    custom = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/MyDiv"),
    )
    struct_root = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructTreeRoot"),
        K=pikepdf.Array([custom]),
        RoleMap=pikepdf.Dictionary({"/MyDiv": pikepdf.Name("/Div")}),
    )
    _attach_struct_root(pdf, struct_root)

    elements, role_map = walk_structure_tree(pdf)
    assert len(elements) == 1
    assert elements[0].custom_tag == "/MyDiv"
    assert elements[0].resolved_tag == "Div"
    assert role_map.get("/MyDiv") == "/Div"


# ─── walk_structure_tree extracts MCIDs from integer leaves ──────────────────


def test_walk_extracts_mcids_from_integer_leaves() -> None:
    pdf = _make_pdf_with_blank_page()
    paragraph = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/P"),
        K=pikepdf.Array([0, 1, 2]),
    )
    struct_root = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructTreeRoot"),
        K=pikepdf.Array([paragraph]),
    )
    _attach_struct_root(pdf, struct_root)

    elements, _ = walk_structure_tree(pdf)
    assert len(elements) == 1
    assert elements[0].mcids == [0, 1, 2]


# ─── populate_element_text ───────────────────────────────────────────────────


def _make_elem(
    index: int,
    tag: str = "P",
    *,
    parent: int = -1,
    children: list[int] | None = None,
    mcids: list[int] | None = None,
    page_map: dict[int, int] | None = None,
) -> StructElement:
    """Build a StructElement without exercising walk_structure_tree."""
    placeholder = pikepdf.Dictionary(Type=pikepdf.Name("/StructElem"))
    return StructElement(
        index=index,
        custom_tag=f"/{tag}",
        resolved_tag=tag,
        alt_text=None,
        actual_text=None,
        lang=None,
        children_indices=children if children is not None else [],
        mcids=mcids if mcids is not None else [],
        parent_index=parent,
        obj=placeholder,
        mcid_page_map=page_map if page_map is not None else {},
    )


def test_populate_element_text_single_page() -> None:
    elem = _make_elem(0, mcids=[0, 1], page_map={0: 0, 1: 0})
    elements = [elem]
    populate_element_text(elements, {0: {0: "Hello", 1: "World"}})
    assert elem.text_content == "Hello World"


def test_populate_element_text_aggregates_children() -> None:
    parent = _make_elem(0, "Document", children=[1])
    child = _make_elem(1, "P", parent=0, mcids=[0], page_map={0: 0})
    elements = [parent, child]
    populate_element_text(elements, {0: {0: "kid"}})
    assert child.text_content == "kid"
    assert parent.text_content == "kid"


def test_populate_element_text_no_mcids_or_kids() -> None:
    elem = _make_elem(0)
    populate_element_text([elem], {})
    assert elem.text_content == ""


def test_populate_element_text_falls_back_when_page_unknown() -> None:
    """Element with mcids but empty mcid_page_map searches all pages."""
    elem = _make_elem(0, mcids=[5])
    elements = [elem]
    populate_element_text(elements, {0: {}, 1: {5: "found"}})
    assert elem.text_content == "found"


def test_populate_element_text_uses_alt_when_child_has_no_text() -> None:
    parent = _make_elem(0, "Figure", children=[1])
    child = _make_elem(1, "Figure", parent=0)
    child.alt_text = "alt description"
    populate_element_text([parent, child], {})
    assert parent.text_content == "alt description"
