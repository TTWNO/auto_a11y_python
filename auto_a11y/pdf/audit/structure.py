"""Typed PDF structure tree walker.

Ports the structure-tree extraction from pdfMax's
``pdf_accessibility_audit.py`` into the auto_a11y codebase. The walker
yields a flat list of :class:`StructElement` plus the resolved RoleMap;
:func:`populate_element_text` then back-fills text content using a
per-page MCID → text map produced elsewhere in Phase 3.

The original pdfMax module typed every PDF object as ``pikepdf.Object``
and leaned on ``isinstance`` checks scattered throughout. Here we use
:mod:`auto_a11y.pdf.audit.pikepdf_helpers` to perform the narrowing once
at each access boundary, so callers (and the type-checkers) see concrete
``Dictionary``/``Array``/``Name``/``String`` types.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: The set of standard structure tags defined in PDF 1.7 / ISO 32000-1.
#: A custom tag in a PDF's structure tree should resolve, via the document's
#: RoleMap, into one of these values.
STANDARD_PDF_TAGS: frozenset[str] = frozenset(
    {
        "Document", "Part", "Art", "Sect", "Div", "BlockQuote", "Caption",
        "TOC", "TOCI", "Index", "NonStruct", "Private",
        "H", "H1", "H2", "H3", "H4", "H5", "H6",
        "P", "L", "LI", "Lbl", "LBody",
        "Table", "TR", "TH", "TD", "THead", "TBody", "TFoot",
        "Span", "Quote", "Note", "Reference", "BibEntry", "Code",
        "Link", "Annot", "Ruby", "RB", "RT", "RP", "Warichu", "WT", "WP",
        "Figure", "Formula", "Form",
    }
)


# ---------------------------------------------------------------------------
# resolve_tag
# ---------------------------------------------------------------------------


def resolve_tag(tag_name: str, role_map: dict[str, str], max_depth: int = 10) -> str:
    """Resolve a custom tag name through the RoleMap to a standard PDF tag.

    Walks the RoleMap chain at most ``max_depth`` hops and stops if a cycle
    is detected, returning whichever value was current at the cycle.
    Unmapped names pass through unchanged.
    """
    current = tag_name
    seen: set[str] = set()
    for _ in range(max_depth):
        if current in seen:
            break
        seen.add(current)
        mapped = role_map.get(current)
        if mapped is None:
            break
        current = mapped
    return current


# ---------------------------------------------------------------------------
# StructElement
# ---------------------------------------------------------------------------


@dataclass
class StructElement:
    """A single node in the PDF structure tree.

    ``obj`` is narrowed from pikepdf's untyped ``Object`` to
    ``Dictionary``: every node visited by :func:`walk_structure_tree` is
    asserted to be a Dictionary at walk time, so downstream consumers can
    rely on the narrower type without re-checking.

    ``text_content`` and ``mcid_page_map`` start empty and are filled in
    after the walk by other Phase 3 collectors (page text extraction,
    then :func:`populate_element_text`).
    """

    index: int
    custom_tag: str
    resolved_tag: str
    alt_text: str | None
    actual_text: str | None
    lang: str | None
    children_indices: list[int]
    mcids: list[int]
    parent_index: int
    obj: pikepdf.Dictionary
    text_content: str = ""
    mcid_page_map: dict[int, int] = field(default_factory=dict[int, int])


# ---------------------------------------------------------------------------
# walk_structure_tree
# ---------------------------------------------------------------------------


def _name_to_str(name: pikepdf.Name) -> str:
    """Render a pikepdf.Name as its source string (with leading ``/``)."""
    return str(name)


def _read_role_map(struct_root: pikepdf.Dictionary) -> dict[str, str]:
    """Materialise the RoleMap dict-of-strings from the StructTreeRoot."""
    role_map_raw = pikepdf_helpers.get_dict(struct_root, "/RoleMap")
    if role_map_raw is None:
        return {}
    role_map: dict[str, str] = {}
    for key in role_map_raw.keys():
        value = role_map_raw[key]
        # Both keys and values are PDF Names; render via str() so the
        # leading "/" is preserved (matches how callers compose lookups).
        if isinstance(value, pikepdf.Name):
            role_map[key] = _name_to_str(value)
        elif isinstance(value, pikepdf.String):
            role_map[key] = str(value)
        else:
            role_map[key] = str(value)
    return role_map


def _read_string_attr(node: pikepdf.Dictionary, key: str) -> str | None:
    """Read a /Alt-style attribute that is conventionally a String.

    Falls back to ``str()`` for the rare case where a producer wrote the
    value as some other PDF object — the original pdfMax code accepted
    anything stringifiable, so we preserve that liberality.
    """
    try:
        val = node[key]
    except KeyError:
        return None
    if isinstance(val, pikepdf.String):
        return str(val)
    return str(val)


def _resolve_custom_tag(node: pikepdf.Dictionary) -> tuple[str, str]:
    """Read /S off a struct node, returning ``(custom_tag, key_for_role_map)``.

    ``custom_tag`` is the as-stored tag with its leading slash, e.g.
    ``"/H1"`` or ``"/MyHeading"``. The bare key (no slash) is used when
    composing role-map lookups; the original pdfMax code did
    ``"/" + tag_key`` because it normalised both directions.
    """
    name = pikepdf_helpers.get_name(node, "/S")
    if name is None:
        return "", ""
    rendered = _name_to_str(name)
    return rendered, rendered.lstrip("/")


def walk_structure_tree(
    pdf: pikepdf.Pdf,
) -> tuple[list[StructElement], dict[str, str]]:
    """Walk the PDF structure tree.

    Returns:
        ``(elements, role_map)`` where ``role_map`` maps custom tag names
        (with leading ``/``) to their standard PDF tag equivalents (also
        with leading ``/``). Elements are returned in pre-order traversal.
        If the document has no /StructTreeRoot, returns ``([], {})``.
    """
    struct_root = pikepdf_helpers.get_dict(pdf.Root, "/StructTreeRoot")
    if struct_root is None:
        return [], {}

    role_map = _read_role_map(struct_root)

    # Build page object identity → page index lookup for /Pg resolution.
    # Page.obj is typed as Dictionary; id() on it is stable for the life
    # of the Pdf, so this is safe across the walk.
    page_obj_to_idx: dict[int, int] = {}
    for i, page in enumerate(pdf.pages):
        page_obj_to_idx[id(page.obj)] = i

    elements: list[StructElement] = []
    # Mutable cell so the inner walker can advance the counter without
    # nonlocal gymnastics — matches the original pdfMax pattern.
    index_counter: list[int] = [0]

    def _resolve_page_idx(pg_ref: pikepdf.Object | None) -> int | None:
        if pg_ref is None:
            return None
        return page_obj_to_idx.get(id(pg_ref))

    def _node_pg(node: pikepdf.Dictionary) -> pikepdf.Object | None:
        try:
            return node["/Pg"]
        except KeyError:
            return None

    def walk(node: pikepdf.Dictionary, parent_idx: int, depth: int) -> None:
        idx = index_counter[0]
        index_counter[0] += 1

        custom_tag, tag_key = _resolve_custom_tag(node)
        if tag_key:
            resolved = resolve_tag("/" + tag_key, role_map)
        else:
            resolved = custom_tag
        resolved_clean = resolved.lstrip("/")

        alt_text = _read_string_attr(node, "/Alt")
        actual_text = _read_string_attr(node, "/ActualText")
        lang = _read_string_attr(node, "/Lang")

        elem_page_idx = _resolve_page_idx(_node_pg(node))

        mcids: list[int] = []
        mcid_page_map: dict[int, int] = {}
        children_indices: list[int] = []

        elem = StructElement(
            index=idx,
            custom_tag=custom_tag,
            resolved_tag=resolved_clean,
            alt_text=alt_text,
            actual_text=actual_text,
            lang=lang,
            children_indices=children_indices,
            mcids=mcids,
            parent_index=parent_idx,
            obj=node,
            mcid_page_map=mcid_page_map,
        )
        elements.append(elem)

        try:
            k = node["/K"]
        except KeyError:
            return

        items: list[pikepdf.Object]
        if isinstance(k, pikepdf.Array):
            # pikepdf's Object.__iter__ stub returns ``Iterable[Object]``
            # rather than ``Iterator[Object]``, which trips ``list()``.
            # Index access is always typed correctly, so collect by hand.
            items = [k[i] for i in range(len(k))]
        else:
            items = [k]

        for child in items:
            # PDF integer leaves arrive as bare Python ints (pikepdf
            # unwraps numeric primitives at the array boundary). Check
            # this *before* Dictionary, since Integer is not a Dictionary
            # but also not visible as its own type at runtime.
            if isinstance(child, int):
                mcids.append(child)
                if elem_page_idx is not None:
                    mcid_page_map[child] = elem_page_idx
                continue

            if isinstance(child, pikepdf.Dictionary):
                child_type = pikepdf_helpers.get_name(child, "/Type")
                if child_type is not None and _name_to_str(child_type) == "/MCR":
                    mcid_int = pikepdf_helpers.get_int(child, "/MCID")
                    if mcid_int is not None:
                        mcids.append(mcid_int)
                        # The MCR may carry its own /Pg; otherwise fall
                        # back to the enclosing element's /Pg.
                        mcr_page = _resolve_page_idx(_node_pg(child))
                        pg = mcr_page if mcr_page is not None else elem_page_idx
                        if pg is not None:
                            mcid_page_map[mcid_int] = pg
                elif child_type is not None and _name_to_str(child_type) == "/OBJR":
                    # Object reference — not a content marker, skip.
                    continue
                else:
                    # Nested struct element. Reserve its index *before*
                    # recursing so children_indices lines up with the
                    # actual order of insertion.
                    children_indices.append(index_counter[0])
                    walk(child, idx, depth + 1)
                continue

            # Anything else: try to coerce to int (covers indirect
            # numeric refs that pikepdf didn't unwrap). Failure is fine
            # — the original pdfMax code silently skipped these too.
            try:
                mcid_int = int(child)
            except (TypeError, ValueError):
                continue
            mcids.append(mcid_int)
            if elem_page_idx is not None:
                mcid_page_map[mcid_int] = elem_page_idx

    root_k_obj: pikepdf.Object | None
    try:
        root_k_obj = struct_root["/K"]
    except KeyError:
        root_k_obj = None
    if root_k_obj is not None:
        root_items: list[pikepdf.Object]
        if isinstance(root_k_obj, pikepdf.Array):
            # See note above: iterate by index to satisfy strict typing.
            root_items = [root_k_obj[i] for i in range(len(root_k_obj))]
        else:
            root_items = [root_k_obj]
        for item in root_items:
            if isinstance(item, pikepdf.Dictionary):
                walk(item, -1, 0)

    _populate_mcid_page_map_from_parent_tree(pdf, struct_root, elements)

    return elements, role_map


def _populate_mcid_page_map_from_parent_tree(
    pdf: pikepdf.Pdf,
    struct_root: pikepdf.Dictionary,
    elements: list[StructElement],
) -> None:
    """Fill ``mcid_page_map`` for elements lacking explicit /Pg attributes.

    The /ParentTree on the StructTreeRoot is a number tree mapping each
    page's /StructParents key to an array whose i-th entry is the parent
    structure element of the i-th MCID on that page. We invert that
    mapping (per parent element objgen, build {mcid: page}) and apply it
    to elements whose direct walk yielded no page assignments.
    """
    parent_tree = pikepdf_helpers.get_dict(struct_root, "/ParentTree")
    if parent_tree is None:
        return

    # Build /StructParents → page index. Page.get() is typed as
    # ``Object | T | None``; narrow numerics through int().
    sp_to_page: dict[int, int] = {}
    for pi, page in enumerate(pdf.pages):
        sp = page.get(pikepdf.Name("/StructParents"))
        if sp is None:
            continue
        try:
            sp_to_page[int(sp)] = pi
        except (TypeError, ValueError):
            # /StructParents must be numeric per spec; tolerate junk.
            continue

    pt_nums = pikepdf_helpers.get_array(parent_tree, "/Nums")
    if pt_nums is None:
        return

    # objgen → {mcid: page_idx}. Built by walking the flat /Nums array
    # in (key, value) pairs.
    objgen_mcid_pages: dict[tuple[int, int], dict[int, int]] = {}
    for ni in range(0, len(pt_nums), 2):
        try:
            pt_key = int(pt_nums[ni])
        except (TypeError, ValueError, IndexError):
            continue
        pi_idx = sp_to_page.get(pt_key)
        if pi_idx is None:
            continue
        try:
            arr = pt_nums[ni + 1]
        except IndexError:
            continue
        if not isinstance(arr, pikepdf.Array):
            continue
        for mcid_val in range(len(arr)):
            try:
                # AttributeError: the array entry is not an indirect ref
                #   (e.g. a null leaf) — no objgen attribute.
                # TypeError: pikepdf raised on the access path itself.
                # IndexError: array length lied; access went past end.
                og = arr[mcid_val].objgen
            except (AttributeError, TypeError, IndexError):
                continue
            objgen_mcid_pages.setdefault(og, {})[mcid_val] = pi_idx

    for elem in elements:
        if elem.mcids and not elem.mcid_page_map:
            pt_map = objgen_mcid_pages.get(elem.obj.objgen)
            if pt_map:
                elem.mcid_page_map = pt_map


# ---------------------------------------------------------------------------
# populate_element_text
# ---------------------------------------------------------------------------


def populate_element_text(
    elements: list[StructElement],
    mcid_map: dict[int, dict[int, str]],
) -> None:
    """Populate ``text_content`` on structure elements from a MCID→text map.

    Args:
        elements: Output of :func:`walk_structure_tree`. Mutated in place.
        mcid_map: Per-page lookup ``{page_index: {mcid: text}}``.

    For elements with explicit MCIDs, joins the per-mcid text with " "
    (matching pdfMax's behaviour). For parent elements with no direct
    MCIDs, aggregates child ``text_content`` (or alt/actual fallback)
    bottom-up so a Document tag picks up the concatenation of its
    descendants.
    """
    for elem in elements:
        if not elem.mcids:
            continue
        text_parts: list[str] = []
        for mcid in elem.mcids:
            page_idx = elem.mcid_page_map.get(mcid)
            if page_idx is not None:
                page_mcids = mcid_map.get(page_idx, {})
                if mcid in page_mcids:
                    text_parts.append(page_mcids[mcid])
            else:
                # No page assignment recorded; fall back to a global
                # search across every page's MCID map.
                for page_mcids in mcid_map.values():
                    if mcid in page_mcids:
                        text_parts.append(page_mcids[mcid])
                        break
        if text_parts:
            elem.text_content = " ".join(text_parts).strip()

    # Bottom-up aggregation: walk reversed so child text exists before a
    # parent tries to consume it.
    index_map = {e.index: e for e in elements}
    for elem in reversed(elements):
        if elem.text_content or not elem.children_indices:
            continue
        child_texts: list[str] = []
        for ci in elem.children_indices:
            child = index_map.get(ci)
            if child is None:
                continue
            t = child.text_content or child.alt_text or child.actual_text or ""
            if t:
                child_texts.append(t)
        if child_texts:
            elem.text_content = " ".join(child_texts)
