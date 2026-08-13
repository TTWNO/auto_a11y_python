"""Document-wide inventory builders for the PDF audit report.

Mirrors pdfMax's ``_generate_report_inner`` Sections 2–16. Each
builder reads from an already-populated :class:`AuditContext` (no new
PDF I/O), turns the raw collector output into a JSON-serialisable
dict, and the orchestrator collects all of them into
``AuditResult.report_sections`` keyed by stable section ID.

The shapes here are deliberately simple — lists of small dicts with
str / int / float / bool values — so they can round-trip through
Mongo via :meth:`TestResult.to_dict` without extra serialisers and
through Jinja's templates without extra view models.

Sections covered (Phase A):

* ``tag_tree``        — structure-tree walk (pdfMax §2)
* ``reading_order``   — sequential text in tag order (pdfMax §3)
* ``image_inventory`` — Figure/Formula elements + extracted images (§4)
* ``heading_map``     — H1..H6 outline (§7)
* ``full_alt_text``   — every Figure/Formula element's alt/actual text (§8)
* ``color_contrast``  — per ``(fg, bg)`` color pair stats (§10)
* ``language_analysis`` — declared lang + per-element /Lang spans (§11)
* ``font_analysis``   — full font inventory + size/rotation/italic
                        sub-tables (§13)

Sections covered (Phase B):

* ``link_inventory``           — Link struct elements + /Link annotations (§5)
* ``form_inventory``           — /AcroForm /Fields tree (§6)
* ``wcag_mapping``             — full WCAG 2.2 SC → check verdict (§12)
* ``version_recommendations``  — PDF 2.0 vs 1.7 advice + Sect rec (§16)

Sections covered (Phase C):

* ``executive_summary``        — deterministic counts + top-failing checks
* ``visual_reading_order``     — structure-vs-visual mismatches (§14)
* ``exported_images``          — bitmap gallery aligned with §8 alt text (§9)
* ``images_of_text``           — placeholder (AI-deferred per Phase 5.1) (§15)

The AI-prose flavour of pdfMax's executive summary
(``generate_executive_summary_ai``) and the AI-driven Color Use
analysis remain in Phase 5.1's deferred bucket; the placeholders
above surface a "requires AI" notice so the section is visible and
labelled.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pikepdf

from auto_a11y.pdf.audit.checks.fonts import classify_font

if TYPE_CHECKING:
    from auto_a11y.pdf.audit.colors import ColorPairInfo
    from auto_a11y.pdf.audit.fonts import FontAnalysis
    from auto_a11y.pdf.audit.images import ExtractedImage
    from auto_a11y.pdf.audit.reading_order import ReadingOrderMismatch
    from auto_a11y.pdf.audit.structure import StructElement
    from auto_a11y.pdf.models import AuditContext, CheckResult


# Maximum nesting depth rendered for the tag tree. Beyond this we
# truncate with an ellipsis row to keep the rendered HTML manageable
# for very deep documents (some legal forms reach 20+ levels).
_MAX_TAG_TREE_DEPTH = 30

# Truncate text snippets in inventories to keep the table compact.
_TEXT_PREVIEW_LIMIT = 120


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_report_sections(
    ctx: AuditContext,
    *,
    check_results: list[CheckResult] | None = None,
) -> dict[str, object]:
    """Build the structured report-section payload for an audit.

    Returns a dict keyed by section ID. Each value is itself a small
    dict shaped per the section (e.g. ``{"rows": [...]}``,
    ``{"items": [...], "total": N}``). The shapes are part of the
    contract with ``pdf/_report_sections.html`` — keep keys stable.

    Empty sections are still emitted (with empty lists / counts of 0)
    so the renderer can show "no data" copy uniformly.

    ``check_results`` powers the WCAG mapping section. The pipeline
    passes them in after running every check; callers building report
    sections in isolation (e.g. tests, ad-hoc) can omit it and the
    WCAG mapping will surface every criterion as "Not tested".
    """
    return {
        # Front matter — pdfMax opens its report with the document's own
        # provenance before any verdict, so a reader can tell which file
        # and which producer they are looking at.
        "document_metadata": _build_document_metadata(ctx),
        # Deterministic counterpart to pdfMax's AI exec summary.
        # Surfaced at the top of the inventory area.
        "executive_summary": build_executive_summary(check_results or []),
        # Phase A
        "tag_tree": _build_tag_tree(ctx.elements),
        "reading_order": _build_reading_order(ctx.elements),
        "image_inventory": _build_image_inventory(
            ctx.elements, ctx.images or []
        ),
        "heading_map": _build_heading_map(ctx.elements),
        "full_alt_text": _build_full_alt_text(ctx.elements),
        "color_contrast": _build_color_contrast(ctx.color_pairs or {}),
        "language_analysis": _build_language_analysis(
            ctx.elements, ctx.pdf
        ),
        "font_analysis": _build_font_analysis(ctx.font_analysis),
        # Phase B
        "link_inventory": _build_link_inventory(ctx.elements, ctx.pdf),
        "form_inventory": _build_form_inventory(ctx.pdf),
        "wcag_mapping": _build_wcag_mapping(check_results or []),
        "version_recommendations": _build_version_recommendations(
            ctx.elements, ctx.pdf
        ),
        # Phase C
        "visual_reading_order": _build_visual_reading_order(
            ctx.elements, ctx.reading_order_mismatches or []
        ),
        "exported_images": _build_exported_images(
            ctx.elements, ctx.images or []
        ),
        "images_of_text": _build_images_of_text_placeholder(),
    }


# ---------------------------------------------------------------------------
# §2 Tag Tree
# ---------------------------------------------------------------------------


def _build_tag_tree(
    elements: list[StructElement],
) -> dict[str, object]:
    """Flat list of (depth, tag, text_preview) rows for the structure tree.

    Walks from root elements (parent_index < 0) depth-first. The
    template renders each row indented ``depth * 1ch``. Cyclic
    structure trees (illegal but seen in the wild) are guarded by
    ``visited``.
    """
    if not elements:
        return {"rows": [], "total_elements": 0}

    by_index: dict[int, StructElement] = {e.index: e for e in elements}
    roots = [e.index for e in elements if e.parent_index < 0]
    rows: list[dict[str, object]] = []
    visited: set[int] = set()

    def _walk(idx: int, depth: int) -> None:
        if idx in visited or depth > _MAX_TAG_TREE_DEPTH:
            return
        visited.add(idx)
        elem = by_index.get(idx)
        if elem is None:
            return
        text = (elem.text_content or elem.actual_text or "").strip()
        rows.append({
            "depth": depth,
            "index": elem.index,
            "tag": elem.resolved_tag,
            "custom_tag": elem.custom_tag,
            "text_preview": _truncate(text, _TEXT_PREVIEW_LIMIT),
            "alt_text": elem.alt_text,
            "lang": elem.lang,
        })
        for child_idx in elem.children_indices:
            _walk(child_idx, depth + 1)

    for root_idx in roots:
        _walk(root_idx, 0)

    # Catch any orphans that aren't reachable from a root (defensive —
    # shouldn't happen for valid PDFs, but pdfMax surfaced these too).
    for elem in elements:
        if elem.index not in visited:
            _walk(elem.index, 0)

    return {
        "rows": rows,
        "total_elements": len(elements),
    }


# ---------------------------------------------------------------------------
# §3 Reading Order
# ---------------------------------------------------------------------------


def _build_reading_order(
    elements: list[StructElement],
) -> dict[str, object]:
    """Sequential text in document (tag-tree) order.

    One entry per element with non-empty text. Skips Artifact /
    Decorative tags since those aren't read by AT.
    """
    items: list[dict[str, object]] = []
    skip_tags = {"Artifact", "Background", "Layout"}
    for elem in elements:
        if elem.resolved_tag in skip_tags:
            continue
        text = (elem.text_content or elem.actual_text or elem.alt_text or "").strip()
        if not text:
            continue
        items.append({
            "index": elem.index,
            "tag": elem.resolved_tag,
            "text_preview": _truncate(text, _TEXT_PREVIEW_LIMIT),
        })
    return {"items": items}


# ---------------------------------------------------------------------------
# §4 Image Inventory
# ---------------------------------------------------------------------------


def _build_image_inventory(
    elements: list[StructElement],
    extracted: list[ExtractedImage],
) -> dict[str, object]:
    """Figure / Formula elements with their alt text + extracted images.

    pdfMax shows two parallel views: structure-tree elements that are
    images (Figure / Formula) with their /Alt or /ActualText, and the
    raw extracted bitmap inventory. Both round-trip here so the
    renderer can show alt text against the real bitmap thumbnail.
    """
    figure_tags = {"Figure", "Formula"}
    figures: list[dict[str, object]] = []
    for elem in elements:
        if elem.resolved_tag not in figure_tags:
            continue
        figures.append({
            "index": elem.index,
            "tag": elem.resolved_tag,
            "alt_text": elem.alt_text,
            "actual_text": elem.actual_text,
            # An empty /Alt is itself a finding (decorative figures
            # should be marked as Artifacts; missing /Alt is a fail).
            "has_alt": bool(elem.alt_text),
            "lang": elem.lang,
        })

    extracted_rows: list[dict[str, object]] = []
    for ex in extracted:
        extracted_rows.append({
            "index": ex.index,
            "filename": ex.filename,
            "width": ex.width,
            "height": ex.height,
        })

    return {
        "figures": figures,
        "extracted": extracted_rows,
    }


# ---------------------------------------------------------------------------
# §7 Heading Map
# ---------------------------------------------------------------------------


def _build_heading_map(
    elements: list[StructElement],
) -> dict[str, object]:
    """H1..H6 outline in document order, with hierarchy gap detection."""
    heading_tags = {f"H{i}" for i in range(1, 7)}
    headings: list[dict[str, object]] = []
    for elem in elements:
        if elem.resolved_tag not in heading_tags:
            continue
        text = (elem.text_content or elem.actual_text or "").strip()
        level = int(elem.resolved_tag[1])
        headings.append({
            "index": elem.index,
            "level": level,
            "tag": elem.resolved_tag,
            "custom_tag": elem.custom_tag,
            "text_preview": _truncate(text, _TEXT_PREVIEW_LIMIT),
        })

    # Detect level gaps (e.g., H1 → H3 with no H2). A gap is a violation
    # of WCAG 1.3.1 / PDF/UA structural hierarchy.
    gaps: list[dict[str, object]] = []
    last_level: int | None = None
    for h in headings:
        level = int(h["level"]) if isinstance(h["level"], int) else 0
        if last_level is not None and level > last_level + 1:
            gaps.append({
                "from_level": last_level,
                "to_level": level,
                "at_index": h["index"],
                "at_text": h["text_preview"],
            })
        last_level = level

    return {
        "headings": headings,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# §8 Full Alt Text
# ---------------------------------------------------------------------------


def _build_full_alt_text(
    elements: list[StructElement],
) -> dict[str, object]:
    """Every Figure / Formula element's complete alt + actual text.

    Unlike :func:`_build_image_inventory`, this section does NOT
    truncate — the user is supposed to read every alt to decide if
    it's accurate.
    """
    out: list[dict[str, object]] = []
    for elem in elements:
        if elem.resolved_tag not in ("Figure", "Formula"):
            continue
        out.append({
            "index": elem.index,
            "tag": elem.resolved_tag,
            "alt_text": elem.alt_text or "",
            "actual_text": elem.actual_text or "",
            "has_alt": bool(elem.alt_text),
            "lang": elem.lang,
        })
    return {"items": out}


# ---------------------------------------------------------------------------
# §10 Color and Contrast
# ---------------------------------------------------------------------------


def _build_color_contrast(
    color_pairs: dict[tuple[tuple[float, float, float],
                            tuple[float, float, float]],
                      ColorPairInfo],
) -> dict[str, object]:
    """Per ``(fg, bg)`` color pair: count, ratio, sizes, sample text."""
    rows: list[dict[str, object]] = []
    for (fg, bg), info in color_pairs.items():
        ratio = _contrast_ratio(fg, bg)
        rows.append({
            "fg_hex": _rgb_to_hex(fg),
            "bg_hex": _rgb_to_hex(bg),
            "fg_rgb": list(fg),
            "bg_rgb": list(bg),
            "contrast_ratio": round(ratio, 2),
            "count": info.count,
            "size_min": info.size_min,
            "size_max": info.size_max,
            "page_count": len(info.pages),
            "sample": _truncate(info.sample, _TEXT_PREVIEW_LIMIT),
            # WCAG 1.4.3 thresholds: 4.5:1 normal, 3:1 large (>=18pt
            # or >=14pt bold). We don't track bold here, so the large-
            # text exemption is approximated by size_min >= 18pt.
            "passes_aa_normal": ratio >= 4.5,
            "passes_aa_large": ratio >= 3.0 and info.size_min >= 18.0,
        })
    # Sort worst contrast first — that's what users want to fix first.
    rows.sort(key=lambda r: float(r["contrast_ratio"]) if isinstance(r["contrast_ratio"], (int, float)) else 999.0)
    return {"rows": rows}


# ---------------------------------------------------------------------------
# §11 Language-of-Parts
# ---------------------------------------------------------------------------


def _build_language_analysis(
    elements: list[StructElement],
    pdf: pikepdf.Pdf,
) -> dict[str, object]:
    """Document /Lang plus per-element /Lang spans.

    Re-reads /Lang from the catalog rather than relying on
    ``ctx.declared_lang`` so the section is self-contained.
    """
    declared_lang: str | None = None
    try:
        lang = _dict_get(pdf.Root, "/Lang")
        if lang is not None:
            declared_lang = str(lang).lstrip("/")
    except (AttributeError, KeyError, TypeError):
        declared_lang = None

    spans: list[dict[str, object]] = []
    for elem in elements:
        if not elem.lang:
            continue
        if declared_lang and elem.lang == declared_lang:
            continue
        text = (elem.text_content or elem.actual_text or "").strip()
        if not text:
            continue
        spans.append({
            "index": elem.index,
            "tag": elem.resolved_tag,
            "lang": elem.lang,
            "text_preview": _truncate(text, _TEXT_PREVIEW_LIMIT),
        })

    return {
        "declared_lang": declared_lang,
        "spans": spans,
    }


# ---------------------------------------------------------------------------
# §13 Font Analysis (full)
# ---------------------------------------------------------------------------


def _build_font_analysis(
    fa: FontAnalysis | None,
) -> dict[str, object]:
    """Full font inventory + rotations + italic runs + leading + alignment."""
    if fa is None or not fa.fonts:
        return {
            "inventory": [],
            "total_chars": 0,
            "rotations": [],
            "italic_runs": [],
            "line_spacings": [],
            "alignments": [],
        }

    total_chars = sum(info.char_count for info in fa.fonts.values()) or 1
    inventory: list[dict[str, object]] = []
    for info in fa.fonts.values():
        if not info.sizes:
            continue
        smallest = min(info.sizes)
        largest = max(info.sizes)
        match = classify_font(info.name)
        inventory.append({
            "name": info.name,
            "size_min": smallest,
            "size_max": largest,
            "char_count": info.char_count,
            "char_pct": round(info.char_count / total_chars * 100, 1),
            "page_count": len(info.pages),
            "is_italic": info.is_italic,
            "is_bold": info.is_bold,
            "category": match[0] if match else None,
            "sample": _truncate(info.sample, 60),
        })
    inventory.sort(key=lambda row: (
        -int(row["char_count"]) if isinstance(row["char_count"], int) else 0
    ))

    rotations = [
        {
            "angle": r.angle,
            "page": r.page,
            "sample": _truncate(r.sample, 60),
        }
        for r in fa.rotations
    ]
    italic_runs = [
        {
            "text": _truncate(it.text, _TEXT_PREVIEW_LIMIT),
            "font": it.font,
            "size": it.size,
            "page": it.page,
            "word_count": len(it.text.split()),
        }
        for it in fa.italic_runs
    ]
    line_spacings = [
        {
            "font_size": ls.font_size,
            "leading": ls.leading,
            "ratio": round(ls.ratio, 2),
            "text_preview": _truncate(ls.text, 60),
        }
        for ls in fa.line_spacings
    ]
    alignments = [
        {
            "page": a.page,
            "alignment": a.alignment,
            "line_count": a.line_count,
        }
        for a in fa.alignments
    ]

    return {
        "inventory": inventory,
        "total_chars": total_chars,
        "rotations": rotations,
        "italic_runs": italic_runs,
        "line_spacings": line_spacings,
        "alignments": alignments,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# §5 Link Inventory
# ---------------------------------------------------------------------------


def _build_link_inventory(
    elements: list[StructElement],
    pdf: pikepdf.Pdf,
) -> dict[str, object]:
    """Walk Link struct elements + /Link annotations.

    Combines two views:

    * Tagged links (``Link`` struct elements) — source of truth for
      accessibility: their /Alt or text is what AT announces.
    * Annotation /Link entries — the underlying URI per annot.

    The two are joined where possible by walking each Link element's
    /K children for an OBJR pointing at the annotation.
    """
    # Build a (page, annot_uri) inventory for the second table.
    annotation_links: list[dict[str, object]] = []
    try:
        for page_num, page in enumerate(pdf.pages):
            page_obj = page.obj if hasattr(page, "obj") else None
            if page_obj is None:
                continue
            annots = _dict_get(page_obj, "/Annots")
            if annots is None:
                continue
            for annot in _iterate(annots):
                if not isinstance(annot, pikepdf.Dictionary):
                    continue
                subtype = _dict_get(annot, "/Subtype")
                if subtype is None or str(subtype) != "/Link":
                    continue
                contents = _dict_get(annot, "/Contents")
                annotation_links.append({
                    "page": page_num + 1,
                    "uri": _resolve_annot_target(annot, pdf),
                    "contents": str(contents) if contents is not None else "",
                })
    except (AttributeError, TypeError, KeyError):
        pass

    # Tagged Link struct elements with their /K → OBJR → annot walk.
    tagged_links: list[dict[str, object]] = []
    for elem in elements:
        if elem.resolved_tag != "Link":
            continue
        text = (
            elem.alt_text
            or elem.actual_text
            or elem.text_content
            or ""
        ).strip() or "[no text]"
        url = _resolve_link_uri(elem, pdf)
        tagged_links.append({
            "index": elem.index,
            "text_preview": _truncate(text, _TEXT_PREVIEW_LIMIT),
            "url": url,
            "lang": elem.lang,
            "has_alt": bool(elem.alt_text),
        })

    return {
        "tagged_links": tagged_links,
        "annotation_links": annotation_links,
    }


def _resolve_link_uri(elem: StructElement, pdf: pikepdf.Pdf) -> str:
    """Walk a Link element's /K children for an OBJR → annotation target.

    Returns a human-readable string for the link target. URI actions
    return the bare URL; internal GoTo destinations resolve to a
    ``page N`` reference (or ``named: foo`` if the destination name
    cannot be resolved against the catalog's name tree).
    """
    obj = elem.obj
    k = _dict_get(obj, "/K")
    if k is None:
        return ""
    for child in _iterate(k):
        if not isinstance(child, pikepdf.Dictionary):
            continue
        child_type = _dict_get(child, "/Type")
        if child_type is None or str(child_type) != "/OBJR":
            continue
        annot = _dict_get(child, "/Obj")
        if not isinstance(annot, pikepdf.Dictionary):
            continue
        target = _resolve_annot_target(annot, pdf)
        if target:
            return target
    return ""


def _resolve_annot_target(
    annot: pikepdf.Dictionary,
    pdf: pikepdf.Pdf,
) -> str:
    """Resolve a /Link annotation's target into a readable string.

    Order of preference:

    1. ``/A`` action — URI / GoTo / GoToR / Launch / Named.
    2. ``/Dest`` directly on the annotation (legacy GoTo shorthand).

    Returns ``""`` when nothing resolvable is present.
    """
    action = _dict_get(annot, "/A")
    if isinstance(action, pikepdf.Dictionary):
        rendered = _resolve_action(action, pdf)
        if rendered:
            return rendered

    dest = _dict_get(annot, "/Dest")
    if dest is not None:
        return _format_destination(dest, pdf)

    return ""


def _resolve_action(
    action: pikepdf.Dictionary,
    pdf: pikepdf.Pdf,
) -> str:
    """Render an action dictionary into a user-readable target string."""
    s = _dict_get(action, "/S")
    s_str = str(s) if s is not None else ""

    uri = _dict_get(action, "/URI")
    if uri is not None:
        return str(uri)

    if s_str == "/GoTo":
        d = _dict_get(action, "/D")
        if d is not None:
            return _format_destination(d, pdf)
        return ""

    if s_str == "/GoToR":
        f_spec = _format_filespec(_dict_get(action, "/F"))
        d = _dict_get(action, "/D")
        d_str = _format_destination(d, pdf) if d is not None else ""
        if f_spec and d_str:
            return f"{f_spec} → {d_str}"
        return f_spec or d_str

    if s_str == "/Launch":
        f_spec = _format_filespec(_dict_get(action, "/F"))
        return f"launch: {f_spec}" if f_spec else ""

    if s_str == "/Named":
        n = _dict_get(action, "/N")
        return f"named action: {n}" if n is not None else ""

    return f"action: {s_str}" if s_str else ""


def _format_destination(
    dest: pikepdf.Object,
    pdf: pikepdf.Pdf,
) -> str:
    """Format a destination value (array, name, or string) as a string.

    PDF destination forms (ISO 32000-1 §12.3.2):

    * Explicit array — ``[<page ref> <fit type> ...]``. Rendered as
      ``page N`` (1-based) when the page reference resolves; ``page
      (unresolved)`` otherwise.
    * Name — shorthand for a named destination, looked up via
      ``/Names → /Dests`` (PDF 1.2+) or ``/Dests`` (PDF 1.1).
    * Byte string — UTF-8/PDFDocEncoding form of a named destination;
      same lookup as Name.
    """
    if isinstance(dest, pikepdf.Array):
        return _format_dest_array(dest, pdf)
    if isinstance(dest, (pikepdf.Name, pikepdf.String)):
        name = str(dest).lstrip("/")
        resolved = _lookup_named_dest(pdf, name)
        if resolved is not None and resolved is not dest:
            inner = _format_destination(resolved, pdf)
            if inner:
                return f"{inner} (named: {name})"
        return f"named: {name}"
    return str(dest)


def _format_dest_array(
    arr: pikepdf.Array,
    pdf: pikepdf.Pdf,
) -> str:
    """Format an explicit destination array as ``page N`` (1-based)."""
    if len(arr) == 0:
        return ""
    page_ref = arr[0]
    page_idx = _page_index_for(page_ref, pdf)
    if page_idx is not None:
        return f"page {page_idx + 1}"
    return "page (unresolved)"


def _page_index_for(
    page_ref: pikepdf.Object,
    pdf: pikepdf.Pdf,
) -> int | None:
    """Find a page's 0-based index given a page reference."""
    if not isinstance(page_ref, pikepdf.Dictionary):
        return None
    target_objgen = page_ref.objgen
    for i, page in enumerate(pdf.pages):
        if page.obj.objgen == target_objgen:
            return i
    return None


def _lookup_named_dest(
    pdf: pikepdf.Pdf,
    name: str,
) -> pikepdf.Object | None:
    """Resolve a named destination via ``/Names → /Dests`` or ``/Dests``."""
    catalog = pdf.Root
    names = _dict_get(catalog, "/Names")
    dests_tree = _dict_get(names, "/Dests")
    if isinstance(dests_tree, pikepdf.Dictionary):
        found = _walk_name_tree(dests_tree, name)
        if found is not None:
            return _unwrap_dest(found)
    dests_dict = _dict_get(catalog, "/Dests")
    if isinstance(dests_dict, pikepdf.Dictionary):
        try:
            for key in dests_dict.keys():
                key_str = str(key).lstrip("/")
                if key_str == name:
                    return _unwrap_dest(dests_dict[key])
        except (AttributeError, TypeError, KeyError):
            return None
    return None


def _unwrap_dest(value: pikepdf.Object) -> pikepdf.Object:
    """Unwrap the ``/D`` key of a destination dict (PDF 1.2+ form)."""
    if isinstance(value, pikepdf.Dictionary):
        d = _dict_get(value, "/D")
        if d is not None:
            return d
    return value


def _walk_name_tree(
    node: pikepdf.Dictionary,
    name: str,
) -> pikepdf.Object | None:
    """Walk a PDF name tree (ISO 32000-1 §7.9.6) for ``name``.

    Each node has either ``/Names`` (leaf — flat ``[key value ...]``
    pairs) or ``/Kids`` (intermediate — recurse). ``/Limits`` (if
    present) lets us prune subtrees lexically.
    """
    names_arr = _dict_get(node, "/Names")
    if isinstance(names_arr, pikepdf.Array):
        i = 0
        while i + 1 < len(names_arr):
            try:
                key = str(names_arr[i])
            except (AttributeError, TypeError):
                i += 2
                continue
            if key == name:
                return names_arr[i + 1]
            i += 2

    kids = _dict_get(node, "/Kids")
    if isinstance(kids, pikepdf.Array):
        for ki in range(len(kids)):
            kid = kids[ki]
            if not isinstance(kid, pikepdf.Dictionary):
                continue
            limits = _dict_get(kid, "/Limits")
            if isinstance(limits, pikepdf.Array) and len(limits) >= 2:
                try:
                    lo = str(limits[0])
                    hi = str(limits[1])
                except (AttributeError, TypeError):
                    lo = ""
                    hi = ""
                if lo and hi and not (lo <= name <= hi):
                    continue
            found = _walk_name_tree(kid, name)
            if found is not None:
                return found
    return None


def _format_filespec(f: pikepdf.Object | None) -> str:
    """Render a file specification (string or filespec dict) as a path."""
    if f is None:
        return ""
    if isinstance(f, pikepdf.String):
        return str(f)
    if isinstance(f, pikepdf.Dictionary):
        uf = _dict_get(f, "/UF")
        if uf is not None:
            return str(uf)
        f_key = _dict_get(f, "/F")
        if f_key is not None:
            return str(f_key)
    return str(f)


# ---------------------------------------------------------------------------
# §6 Form Field Inventory
# ---------------------------------------------------------------------------


# Map raw /FT field-type identifiers to friendly labels. pdfMax used
# the same set; keeping in lockstep so both reports speak the same
# vocabulary to the user.
_FT_LABELS: dict[str, str] = {
    "/Tx": "Text",
    "/Btn": "Button/Checkbox",
    "/Ch": "Choice/Dropdown",
    "/Sig": "Signature",
}


def _build_form_inventory(pdf: pikepdf.Pdf) -> dict[str, object]:
    """Walk /AcroForm /Fields tree producing per-field rows.

    Each row carries the field name (/T), type (/FT), accessible
    name (/TU), the /Ff bit flags decoded into ``required`` /
    ``read_only``, and a truncated value preview.
    """
    summary: dict[str, object] = {
        "total": 0,
        "labeled": 0,
        "missing_tu": 0,
        "required_count": 0,
        "fields": [],
    }
    try:
        root = pdf.Root
        acroform = _dict_get(root, "/AcroForm")
        if not isinstance(acroform, pikepdf.Dictionary):
            return summary
        raw_fields = _dict_get(acroform, "/Fields")
        if raw_fields is None:
            return summary

        # Flatten the field tree (terminal-field walker, mirroring
        # pdfMax — fields with /Kids and no /FT are intermediate
        # nodes that group children).
        form_fields: list[pikepdf.Dictionary] = []
        stack: list[pikepdf.Object] = list(_iterate(raw_fields))
        while stack:
            field = stack.pop(0)
            if not isinstance(field, pikepdf.Dictionary):
                continue
            kids = _dict_get(field, "/Kids")
            ft = _dict_get(field, "/FT")
            if kids is not None and ft is None:
                stack.extend(_iterate(kids))
            else:
                form_fields.append(field)
    except (AttributeError, TypeError, KeyError):
        return summary

    fields_out: list[dict[str, object]] = []
    labeled = 0
    missing_tu = 0
    required_count = 0
    for field in form_fields:
        t_raw = _dict_get(field, "/T")
        name = str(t_raw) if t_raw is not None else ""
        ft_raw = _dict_get(field, "/FT")
        ft_str = str(ft_raw) if ft_raw is not None else ""
        ft_label = _FT_LABELS.get(ft_str, ft_str or "—")
        tu_raw = _dict_get(field, "/TU")
        tu = str(tu_raw).strip() if tu_raw is not None else ""
        if tu:
            labeled += 1
        else:
            missing_tu += 1
        ff_raw = _dict_get(field, "/Ff")
        ff = 0
        if ff_raw is not None:
            try:
                ff = int(str(ff_raw))
            except (TypeError, ValueError):
                ff = 0
        is_required = bool(ff & 2)
        is_readonly = bool(ff & 1)
        if is_required:
            required_count += 1
        v_raw = _dict_get(field, "/V")
        dv_raw = _dict_get(field, "/DV")
        if v_raw is not None:
            value = str(v_raw)
        elif dv_raw is not None:
            value = f"(default: {dv_raw})"
        else:
            value = ""
        fields_out.append({
            "name": name,
            "type_raw": ft_str,
            "type_label": ft_label,
            "accessible_name": tu,
            "has_accessible_name": bool(tu),
            "is_required": is_required,
            "is_read_only": is_readonly,
            "value_preview": _truncate(value, 60),
        })

    return {
        "total": len(fields_out),
        "labeled": labeled,
        "missing_tu": missing_tu,
        "required_count": required_count,
        "fields": fields_out,
    }


# ---------------------------------------------------------------------------
# §12 WCAG Mapping
# ---------------------------------------------------------------------------


# Stable WCAG 2.2 SC → check-name list. Mirrors pdfMax's wcag_map at
# line ~8771. Keep these check names in lockstep with the names
# emitted by the audit-engine check functions; the regression test
# `test_every_check_emitted_by_engine_has_catalogue_entry` already
# guarantees every emitted (name, result) has a catalogue row, so
# any drift here surfaces as "Not tested" rather than a crash.
_WCAG_MAP: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("1.1.1 Non-text Content", "A", (
        "Alt text on all Figure/Art tags",
        "Alt text adequacy",
        "Alt text free of redundant role text",
        "Formula elements have alt text or ActualText",
        "Media clip annotations have alt text",
        "Media clip alt text present",
        "Media clip content type present",
        "MathML associated with Formula elements",
    )),
    ("1.2.1 Audio-only and Video-only", "A", (
        "Multimedia annotations tagged",
    )),
    ("1.3.1 Info and Relationships", "A", (
        "PDF is tagged",
        "Structure tree exists",
        "Heading hierarchy valid",
        "No multiple headings per node",
        "No mixed heading tag types",
        "Table headers defined",
        "Table header scope defined",
        "Table structure sections",
        "Table regularity",
        "No empty tables",
        "Table captions",
        "Complex table headers association",
        "List structure valid",
        "No empty lists",
        "List nesting valid",
        "List item labels",
        "Untagged lists detected",
        "Correct nesting",
        "TOC structure valid",
        "Ruby structure valid",
        "Warichu structure valid",
        "All content tagged",
        "Artifact not inside tagged content",
        "Tagged content not inside artifact",
        "All content is tagged or artifact",
        "Note tags have unique IDs",
        "Figure elements have BBox attribute",
    )),
    ("1.3.2 Meaningful Sequence", "A", (
        "Reading order matches visual layout",
    )),
    ("1.4.1 Use of Color", "A", (
        "Use of color not sole indicator",
    )),
    ("1.4.3 Contrast (Minimum)", "AA", (
        "Color contrast sufficient",
    )),
    ("1.4.5 Images of Text", "AA", (
        "Images of text have matching alt text",
    )),
    ("1.4.6 Contrast (Enhanced)", "AAA", (
        "Color contrast enhanced (AAA)",
    )),
    ("1.4.11 Non-text Contrast", "AA", (
        "Non-text contrast sufficient",
    )),
    ("2.1.1 Keyboard", "A", (
        "Tab order follows structure",
        "Annotation tab order on all annotated pages",
    )),
    ("2.4.2 Page Titled", "A", (
        "Document title set",
        "XMP dc:title present",
        "Page labels consistent",
    )),
    ("2.4.4 Link Purpose (In Context)", "A", (
        "Link annotations have content",
        "Link annotations have Contents key",
        "Link alt text is descriptive",
        "Structure destinations for intra-document links",
    )),
    ("2.4.5 Multiple Ways", "AA", ("Bookmarks present",)),
    ("2.4.6 Headings and Labels", "AA", ("Heading hierarchy valid",)),
    ("2.4.7 Focus Visible", "AA", ("Focus indicator visibility",)),
    ("2.4.11 Focus Not Obscured (Minimum)", "AA", ("Focus not obscured",)),
    ("2.4.12 Focus Not Obscured (Enhanced)", "AAA", ("Focus not obscured",)),
    ("2.4.13 Focus Appearance", "AAA", ("Focus indicator visibility",)),
    ("2.5.3 Label in Name", "A", ("Label in Name",)),
    ("2.5.5 Target Size (Enhanced)", "AAA", ("Interactive element target size",)),
    ("2.5.7 Dragging Movements", "AA", ("Dragging movement alternatives",)),
    ("2.5.8 Target Size (Minimum)", "AA", ("Interactive element target size",)),
    ("3.1.1 Language of Page", "A", (
        "Document language set",
        "Document metadata language determinable",
    )),
    ("3.1.2 Language of Parts", "AA", (
        "Language of parts markup",
        "Cross-language link targets identified",
        "Lang values are valid BCP 47",
        "Annotation contents language determinable",
        "Form field tooltip language determinable",
    )),
    ("3.1.4 Abbreviations", "AAA", ("Abbreviations have /E expansion",)),
    ("3.2.6 Consistent Help", "A", ()),
    ("3.3.2 Labels or Instructions", "A", (
        "Form fields labeled",
        "Required fields flagged",
        "Required fields visually indicated",
    )),
    ("3.3.7 Redundant Entry", "A", ("Redundant entry in forms",)),
    ("3.3.8 Accessible Authentication (Minimum)", "AA", ("Accessible authentication",)),
    ("3.3.9 Accessible Authentication (Enhanced)", "AAA", ("Accessible authentication",)),
    ("4.1.2 Name, Role, Value", "A", (
        "Role mapping valid",
        "No circular role mappings",
        "Standard tags not remapped",
        "Alt text does not hide interactive elements",
        "No XFA forms present",
        "Non-link/widget annotations tagged",
        "Visible annotations have alt descriptions",
        "No non-standard annotation subtypes",
        "Widget annotations inside Form tags",
        "Link annotations inside Link tags",
        "File attachment annotations valid",
        "New PDF 2.0 structure elements used correctly",
    )),
)


def _build_wcag_mapping(
    check_results: list[CheckResult],
) -> dict[str, object]:
    """Aggregate per-SC verdicts.

    For each WCAG SC the worst result among matching checks wins
    (``FAIL`` > ``WARN`` > ``INFO`` > ``PASS``). SCs with no related
    check are flagged "Manual review required". SCs whose related
    checks didn't run are flagged "Not tested".
    """
    by_name: dict[str, list[str]] = {}
    for cr in check_results:
        by_name.setdefault(cr.name, []).append(cr.result)

    rows: list[dict[str, object]] = []
    for sc, level, names in _WCAG_MAP:
        if not names:
            rows.append({
                "criterion": sc,
                "level": level,
                "verdict": "MANUAL",
                "checks": [],
            })
            continue
        outcomes: list[str] = []
        matched_names: list[str] = []
        for n in names:
            if n in by_name:
                outcomes.extend(by_name[n])
                matched_names.append(n)
        if not outcomes:
            rows.append({
                "criterion": sc,
                "level": level,
                "verdict": "NOT_TESTED",
                "checks": list(names),
            })
            continue
        if "FAIL" in outcomes:
            verdict = "FAIL"
        elif "WARN" in outcomes:
            verdict = "WARN"
        elif "INFO" in outcomes:
            verdict = "INFO"
        elif all(o == "NA" for o in outcomes):
            # Every check under this criterion had nothing to examine —
            # a document with no tables says nothing about 1.3.1's table
            # requirements. Reporting that as PASS would claim conformance
            # the audit never established.
            verdict = "NOT_TESTED"
        else:
            verdict = "PASS"
        rows.append({
            "criterion": sc,
            "level": level,
            "verdict": verdict,
            "checks": matched_names,
        })

    return {"rows": rows}


# ---------------------------------------------------------------------------
# §16 PDF Version & Structure Recommendations
# ---------------------------------------------------------------------------


_PDF2_TAGS: frozenset[str] = frozenset({
    "Aside", "Em", "Strong", "Title", "FENote", "Sub", "DocumentFragment",
})


def _build_version_recommendations(
    elements: list[StructElement],
    pdf: pikepdf.Pdf,
) -> dict[str, object]:
    """Surface PDF version, PDF 2.0 tag usage, and Sect-structure advice.

    Mirrors pdfMax's ``build_version_and_structure_recommendations``.
    Translates the static markdown advice pdfMax embeds into a
    structured ``recommendations`` payload the template renders as a
    bullet list — the exact wording lives in Fluent so it can be
    translated.
    """
    pdf_version: str | None = None
    try:
        if pdf.pdf_version:
            pdf_version = str(pdf.pdf_version)
        # Catalog /Version overrides the header version when set.
        cv = _dict_get(pdf.Root, "/Version")
        if cv is not None:
            pdf_version = str(cv).lstrip("/")
    except (AttributeError, TypeError):
        pass

    is_pdf2 = bool(pdf_version and pdf_version.startswith("2."))

    used_pdf2: list[str] = []
    used_pdf2_set: set[str] = set()
    sect_count = 0
    for elem in elements:
        if elem.resolved_tag == "Sect":
            sect_count += 1
        if elem.resolved_tag in _PDF2_TAGS and elem.resolved_tag not in used_pdf2_set:
            used_pdf2_set.add(elem.resolved_tag)
            used_pdf2.append(elem.resolved_tag)
    used_pdf2.sort()

    # Suggest top-level Sect groupings from H1/H2/H3 boundaries.
    sect_recs: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for elem in elements:
        if elem.resolved_tag == "Document":
            continue
        if elem.parent_index in (-1, 0):
            if elem.resolved_tag in ("H1", "H2", "H3"):
                if current is not None:
                    sect_recs.append(current)
                heading_text = (
                    elem.text_content
                    or elem.alt_text
                    or "[heading]"
                ).strip()
                current = {
                    "heading": _truncate(heading_text, _TEXT_PREVIEW_LIMIT),
                    "tag": elem.resolved_tag,
                    "child_count": 1,
                }
            elif current is not None:
                count = current.get("child_count", 0)
                if isinstance(count, int):
                    current["child_count"] = count + 1
    if current is not None:
        sect_recs.append(current)

    return {
        "pdf_version": pdf_version,
        "is_pdf2": is_pdf2,
        "used_pdf2_tags": used_pdf2,
        "sect_count": sect_count,
        "section_recommendations": sect_recs,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Document metadata (pdfMax "Document Metadata")
# ---------------------------------------------------------------------------


def _build_document_metadata(ctx: AuditContext) -> dict[str, object]:
    """The document's own provenance: who made it, with what, and when.

    Every field is optional in a PDF, so each is emitted as ``None`` when
    absent rather than omitted — the renderer distinguishes "not set" from
    "not collected", and "no author" is itself worth seeing in an audit.
    """
    info = _docinfo(ctx.pdf)
    return {
        "author": info.get("/Author"),
        "creator": info.get("/Creator"),
        "producer": info.get("/Producer"),
        "title": info.get("/Title"),
        "subject": info.get("/Subject"),
        "keywords": info.get("/Keywords"),
        "creation_date": info.get("/CreationDate"),
        "mod_date": info.get("/ModDate"),
        "pdf_version": ctx.pdf.pdf_version or None,
        "language": _catalog_lang(ctx.pdf),
        "structure_elements": len(ctx.elements),
        "pages": len(ctx.pdf.pages),
    }


def _docinfo(pdf: pikepdf.Pdf) -> dict[str, str]:
    """The /Info dictionary as plain strings, skipping anything unreadable."""
    values: dict[str, str] = {}
    try:
        info = pdf.trailer.get("/Info")
    except Exception:  # noqa: BLE001 — a malformed trailer is not fatal
        return values
    if info is None:
        return values
    for key in (
        "/Author", "/Creator", "/Producer", "/Title",
        "/Subject", "/Keywords", "/CreationDate", "/ModDate",
    ):
        try:
            raw = info.get(key)
        except Exception:  # noqa: BLE001 — one bad entry costs its own row
            continue
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            values[key] = text
    return values


def _catalog_lang(pdf: pikepdf.Pdf) -> str | None:
    try:
        lang = pdf.Root.get("/Lang")
    except Exception:  # noqa: BLE001
        return None
    if lang is None:
        return None
    text = str(lang).strip()
    return text or None


# ---------------------------------------------------------------------------
# Executive summary (Phase C, deterministic — no AI)
# ---------------------------------------------------------------------------


def build_executive_summary(
    check_results: list[CheckResult],
) -> dict[str, object]:
    """Top-of-inventory summary: counts + most-frequent failing checks.

    Mirrors the structure of pdfMax's ``generate_executive_summary_local``
    minus the AI prose. Counts every check by outcome, then picks the
    five check names with the most FAIL+WARN occurrences for a "where
    to focus" panel.
    """
    fail = sum(1 for cr in check_results if cr.result == "FAIL")
    warn = sum(1 for cr in check_results if cr.result == "WARN")
    info = sum(1 for cr in check_results if cr.result == "INFO")
    passed = sum(1 for cr in check_results if cr.result == "PASS")
    not_applicable = sum(1 for cr in check_results if cr.result == "NA")

    issue_counts: dict[str, dict[str, object]] = {}
    for cr in check_results:
        if cr.result not in ("FAIL", "WARN"):
            continue
        bucket = issue_counts.setdefault(cr.name, {
            "name": cr.name,
            "fail": 0,
            "warn": 0,
        })
        if cr.result == "FAIL":
            current = bucket.get("fail", 0)
            if isinstance(current, int):
                bucket["fail"] = current + 1
        else:
            current = bucket.get("warn", 0)
            if isinstance(current, int):
                bucket["warn"] = current + 1

    def _sort_key(row: dict[str, object]) -> tuple[int, int, str]:
        f = row.get("fail", 0)
        w = row.get("warn", 0)
        n = row.get("name", "")
        return (
            -int(f) if isinstance(f, int) else 0,
            -int(w) if isinstance(w, int) else 0,
            str(n),
        )

    top_issues = sorted(issue_counts.values(), key=_sort_key)[:5]

    if fail > 0:
        verdict = "FAIL"
    elif warn > 0:
        verdict = "WARN"
    elif passed > 0:
        verdict = "PASS"
    else:
        verdict = "NOT_TESTED"

    return {
        "fail_count": fail,
        "warn_count": warn,
        "info_count": info,
        "pass_count": passed,
        "na_count": not_applicable,
        "total_checks": len(check_results),
        "top_issues": top_issues,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# §14 Visual Reading Order
# ---------------------------------------------------------------------------


def _build_visual_reading_order(
    elements: list[StructElement],
    mismatches: list[ReadingOrderMismatch],
) -> dict[str, object]:
    """Surface structure-vs-visual reading-order inversions.

    Reads ``ctx.reading_order_mismatches`` populated by
    :func:`auto_a11y.pdf.audit.reading_order.compare_reading_orders`.
    Each mismatch carries the element indices in both orderings; we
    join those back to text previews so the rendered table is
    self-explanatory.
    """
    by_index: dict[int, StructElement] = {e.index: e for e in elements}

    def _preview(idx: int) -> str:
        elem = by_index.get(idx)
        if elem is None:
            return "[#" + str(idx) + "]"
        text = (elem.text_content or elem.actual_text or elem.alt_text or "").strip()
        if not text:
            return f"[{elem.resolved_tag} #{idx}]"
        return _truncate(text, 60)

    rows: list[dict[str, object]] = []
    for m in mismatches:
        rows.append({
            "struct_first_index": m.struct_first,
            "struct_first_text": _preview(m.struct_first),
            "struct_second_index": m.struct_second,
            "struct_second_text": _preview(m.struct_second),
            "visual_first_index": m.visual_first,
            "visual_first_text": _preview(m.visual_first),
            "visual_second_index": m.visual_second,
            "visual_second_text": _preview(m.visual_second),
        })

    return {
        "rows": rows,
        "total": len(rows),
    }


# ---------------------------------------------------------------------------
# §9 Exported Images gallery
# ---------------------------------------------------------------------------


def _build_exported_images(
    elements: list[StructElement],
    extracted: list[ExtractedImage],
) -> dict[str, object]:
    """Pair each extracted bitmap with its likely struct-tree alt text.

    Auto_a11y stores each extracted image as a PNG under
    ``PdfStorage`` (streamed by the ``pdf.image`` route). We surface
    the bitmap filename + dimensions per image plus the alt text from
    the matching Figure/Formula by 1-based index — pdfMax's
    convention. Where no matching element exists the row carries
    ``has_alt=False`` so the renderer can flag it.
    """
    figure_elements = [
        e for e in elements
        if e.resolved_tag in ("Figure", "Formula")
    ]
    rows: list[dict[str, object]] = []
    for ex in extracted:
        # Try to align by 1-based index — i.e. the i-th extracted
        # image with the i-th Figure element. This is approximate
        # (extraction order = page order, not necessarily the same as
        # struct-tree order) but matches pdfMax's pairing UI.
        match: StructElement | None = None
        if 1 <= ex.index <= len(figure_elements):
            match = figure_elements[ex.index - 1]
        rows.append({
            "index": ex.index,
            "filename": ex.filename,
            "width": ex.width,
            "height": ex.height,
            "alt_text": match.alt_text if match else None,
            "has_alt": bool(match and match.alt_text),
            "linked_struct_index": match.index if match else None,
        })
    return {
        "rows": rows,
        "total": len(rows),
    }


# ---------------------------------------------------------------------------
# §15 Images of Text (Phase 5.1 placeholder)
# ---------------------------------------------------------------------------


def _build_images_of_text_placeholder() -> dict[str, object]:
    """Stub for the AI/OCR-driven images-of-text section.

    Renders an "AI required" notice in the template. When Phase 5.1
    lands, replace this with a real OCR/AI pass over the extracted
    images. Returning a structured dict (not omitting the key) keeps
    the section labelled in the inventory so the user knows it
    exists, even if no analysis ran.
    """
    return {
        "available": False,
        "reason": "ai_not_configured",
    }


def _dict_get(
    obj: pikepdf.Dictionary | pikepdf.Object | None,
    key: str,
) -> pikepdf.Object | None:
    """Defensively read a key off a pikepdf object.

    Returns ``None`` if the object isn't a Dictionary, the key isn't
    present, or the underlying pikepdf call raises. The link / form /
    version builders walk pikepdf dictionaries that may be missing
    the keys we expect on malformed PDFs; this wrapper keeps each
    builder's happy path readable.
    """
    if obj is None or not isinstance(obj, pikepdf.Dictionary):
        return None
    try:
        if key in obj:
            return obj[key]
        return None
    except (AttributeError, TypeError, KeyError):
        return None


def _iterate(obj: pikepdf.Object) -> list[pikepdf.Object]:
    """Iterate a pikepdf Array / single Object as a uniform list.

    PDF arrays and single objects are interchangeable in many
    contexts (e.g. /Annots can be either). pdfMax used the same idiom
    inline; this helper centralises it.

    Uses index-based access because pikepdf.Array's ``__iter__`` is
    typed as ``Iterable[Object]`` rather than ``Iterator[Object]``,
    which strict-mode pyright + mypy refuse to feed to ``list()`` —
    see ``audit/colors.py::_flatten_form_fields`` for the same idiom.
    """
    if isinstance(obj, pikepdf.Array):
        out: list[pikepdf.Object] = []
        for i in range(len(obj)):
            out.append(obj[i])
        return out
    if isinstance(obj, pikepdf.Dictionary):
        return [obj]
    return []


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    text = " ".join(text.split())  # collapse whitespace
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r = max(0, min(255, int(round(rgb[0] * 255))))
    g = max(0, min(255, int(round(rgb[1] * 255))))
    b = max(0, min(255, int(round(rgb[2] * 255))))
    return f"#{r:02x}{g:02x}{b:02x}"


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    """Compute WCAG relative luminance for an [0..1] sRGB triple."""
    def _channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast_ratio(
    fg: tuple[float, float, float],
    bg: tuple[float, float, float],
) -> float:
    lf = _relative_luminance(fg)
    lb = _relative_luminance(bg)
    light, dark = (lf, lb) if lf > lb else (lb, lf)
    return (light + 0.05) / (dark + 0.05)
