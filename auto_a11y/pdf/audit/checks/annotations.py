"""Annotation-related accessibility checks.

Fourteen checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py`` (lines ~4790-6015 and
~7373-7388):

* :func:`check_link_annotations_have_content` — every ``/Link``
  annotation has either ``/Contents``, ``/Alt``, or a ``/StructParent``
  (PDF/UA, WCAG 2.4.4; pdfMax line ~4790).
* :func:`check_link_annotations_have_contents_key` — every ``/Link``
  annotation has a ``/Contents`` description key (Matterhorn 28-012;
  pdfMax line ~5863).
* :func:`check_link_annotations_inside_link_tags` — every ``/Link``
  annotation lives inside a ``Link`` structure element (Matterhorn
  28-014; pdfMax line ~5718).
* :func:`check_visible_annotations_have_alt_descriptions` — every
  visible non-link/widget annotation carries ``/Contents`` (Matterhorn
  28-005; pdfMax line ~5656).
* :func:`check_non_link_widget_annotations_tagged` — every annotation
  except ``/Link`` / ``/Widget`` / ``/Popup`` is tagged in the structure
  tree (Matterhorn 28-004; pdfMax line ~5638).
* :func:`check_multimedia_annotations_tagged` — every multimedia
  annotation (``/Screen`` / ``/RichMedia`` / ``/Sound`` / ``/Movie`` /
  ``/3D``) is tagged in the structure tree (WCAG 1.2; pdfMax line
  ~7373).
* :func:`check_media_clip_annotations_have_alt_text` — every media
  clip annotation (``/Screen`` / ``/RichMedia`` / ``/Sound`` /
  ``/Movie``) has ``/Contents`` (Matterhorn 28-016; pdfMax line
  ~5816).
* :func:`check_media_clip_alt_text_present` — every media clip has
  an ``/Alt`` key on the rendition's media clip dict or the annotation
  itself (Matterhorn 28-015; pdfMax line ~5950).
* :func:`check_media_clip_content_type_present` — every media clip
  has a ``/CT`` content-type key on the rendition's media clip data or
  the annotation itself (Matterhorn 28-014; pdfMax line ~5910).
* :func:`check_annotation_tab_order_on_all_annotated_pages` — every
  page that has annotations has a ``/Tabs`` entry (Matterhorn 28-002;
  pdfMax line ~5799).
* :func:`check_no_nonstandard_annotation_subtypes` — every annotation's
  ``/Subtype`` is one of the ISO 32000 subtypes (Matterhorn 28-006;
  pdfMax line ~5841).
* :func:`check_no_trapnet_annotations` — there are no ``/TrapNet``
  annotations in the document (Matterhorn 28-006; pdfMax line ~5685).
* :func:`check_printermark_annotations_not_in_structure` — no
  ``/PrinterMark`` annotation appears in the structure tree (Matterhorn
  28-018; pdfMax line ~5782).
* :func:`check_file_attachment_annotations_valid` — every
  ``/FileAttachment`` carries a ``/FS`` file specification with ``/F``,
  ``/UF``, and ``/Desc`` keys (Matterhorn 28-016; pdfMax line ~5985).

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes an
:data:`ANNOTATIONS_CHECKS` registry list. ``CheckResult`` ``name`` and
``standard`` strings match pdfMax verbatim so Phase 6's check catalogue
can map them.
"""
from __future__ import annotations

from collections.abc import Callable

import pikepdf

from auto_a11y.pdf.audit import pikepdf_helpers
from auto_a11y.pdf.audit.structure import StructElement
from auto_a11y.pdf.models import AuditContext, CheckResult


# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------


#: Subtypes whose absence from the structure tree is acceptable for
#: :func:`check_non_link_widget_annotations_tagged`. Mirrors pdfMax's
#: ``ANNOT_EXCLUDE`` set verbatim.
_ANNOT_EXCLUDE: frozenset[str] = frozenset({"/Link", "/Widget", "/Popup"})

#: Media clip subtypes used by the three media checks. Mirrors pdfMax's
#: ``MEDIA_SUBTYPES`` constant verbatim.
_MEDIA_SUBTYPES: frozenset[str] = frozenset(
    {"/Screen", "/RichMedia", "/Sound", "/Movie"}
)

#: Multimedia subtypes for :func:`check_multimedia_annotations_tagged`.
#: Mirrors pdfMax's ``MULTIMEDIA_SUBTYPES`` verbatim — a superset of
#: :data:`_MEDIA_SUBTYPES` that adds ``/3D``.
_MULTIMEDIA_SUBTYPES: frozenset[str] = frozenset(
    {"/Screen", "/RichMedia", "/Sound", "/Movie", "/3D"}
)

#: ISO 32000 standard annotation subtypes. Mirrors pdfMax's
#: ``ISO_32000_SUBTYPES`` set verbatim (same insertion order, same
#: contents).
_ISO_32000_SUBTYPES: frozenset[str] = frozenset(
    {
        "/Text", "/Link", "/FreeText", "/Line", "/Square", "/Circle",
        "/Polygon", "/PolyLine", "/Highlight", "/Underline", "/Squiggly",
        "/StrikeOut", "/Stamp", "/Caret", "/Ink", "/Popup", "/FileAttachment",
        "/Sound", "/Movie", "/Widget", "/Screen", "/PrinterMark", "/TrapNet",
        "/Watermark", "/3D", "/Redact", "/Projection", "/RichMedia",
    }
)

#: Bit mask for the ``/F`` annotation-flag bit that marks an annotation
#: as Hidden (PDF 1.7 §12.5.3 Table 165). Mirrors pdfMax's ``0x2``
#: literal at line 5665.
_HIDDEN_FLAG_MASK: int = 0x2


# ---------------------------------------------------------------------------
# Helpers shared across the annotation-iterating checks
# ---------------------------------------------------------------------------


def _iter_page_annot_dicts(
    page_obj: pikepdf.Dictionary,
) -> list[pikepdf.Dictionary]:
    """Yield every dictionary-shaped annotation on a page.

    Annotation arrays may legitimately contain non-dictionary entries
    (e.g. dangling indirect references) — those are skipped rather than
    raising. Mirrors the helper in
    :mod:`auto_a11y.pdf.audit.checks.interactive`.
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


def _build_annot_struct_map(
    elements: list[StructElement],
) -> dict[int, tuple[int, str]]:
    """Map ``id(annotation)`` to the parent structure element index/tag.

    Mirrors pdfMax's :func:`build_annotation_structure_map`. Walks each
    element's ``/K`` array, picks out ``OBJR`` children, and records the
    ``(element_index, resolved_tag)`` of the parent for each annotation
    object referenced. Mirrors the helper in
    :mod:`auto_a11y.pdf.audit.checks.interactive`.
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


def _collect_all_annots(
    pdf: pikepdf.Pdf,
) -> list[tuple[int, pikepdf.Dictionary, str]]:
    """Walk every page and return ``(page_num, annot_dict, subtype_str)``.

    Mirrors the iteration at pdfMax line ~5624. ``page_num`` is 1-based
    so it matches the human-facing page numbers in the FAIL details.
    """
    out: list[tuple[int, pikepdf.Dictionary, str]] = []
    for page_idx, page in enumerate(pdf.pages):
        page_num = page_idx + 1
        for annot in _iter_page_annot_dicts(page.obj):
            out.append((page_num, annot, _annot_subtype_str(annot)))
    return out


def _annot_has_uri(annot: pikepdf.Dictionary) -> str:
    """Read the URI string from an annotation's ``/A`` dictionary.

    Returns the URI as a Python string when ``/A/URI`` is set, otherwise
    the empty string. Mirrors the helper used inline at pdfMax line ~4807.
    """
    action = pikepdf_helpers.get_dict(annot, "/A")
    if action is None:
        return ""
    uri = pikepdf_helpers.get_string(action, "/URI")
    return uri if uri is not None else ""


def _is_truthy_pdf_value(obj: pikepdf.Object | None) -> bool:
    """Return ``True`` for a non-empty PDF value (string, name, dict).

    pdfMax used Python's truthiness on the raw ``annot.get(...)``
    result. We replicate that here: a value is "present" if it isn't
    None and isn't an empty pikepdf.String. Names and Dictionaries
    always count as present; arrays count as present when non-empty.
    """
    if obj is None:
        return False
    if isinstance(obj, pikepdf.String):
        return bool(str(obj))
    if isinstance(obj, pikepdf.Array):
        return len(obj) > 0
    return True


# ---------------------------------------------------------------------------
# check_link_annotations_have_content
# ---------------------------------------------------------------------------


def check_link_annotations_have_content(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA, WCAG 2.4.4: every link annotation has associated text.

    A ``/Link`` annotation needs at least one of: ``/Contents``,
    ``/Alt``, or ``/StructParent`` (so the link is reachable from the
    structure tree). Annotations missing all three are reported with
    their URI for context. Mirrors pdfMax line ~4790 verbatim.
    """
    link_issues: list[str] = []
    link_ok_count = 0
    for page_num, annot, subtype_str in _collect_all_annots(ctx.pdf):
        if subtype_str != "/Link":
            continue
        try:
            contents_obj: pikepdf.Object | None = annot["/Contents"]
        except KeyError:
            contents_obj = None
        try:
            alt_obj: pikepdf.Object | None = annot["/Alt"]
        except KeyError:
            alt_obj = None
        try:
            struct_parent_obj: pikepdf.Object | None = annot["/StructParent"]
        except KeyError:
            struct_parent_obj = None

        has_content = (
            _is_truthy_pdf_value(contents_obj)
            or _is_truthy_pdf_value(alt_obj)
            or struct_parent_obj is not None
        )
        if has_content:
            link_ok_count += 1
        else:
            uri = _annot_has_uri(annot)
            link_issues.append(
                f"Page {page_num}: Link to '{uri}' has no associated"
                + " text content"
            )

    if not link_issues:
        return [
            CheckResult(
                name="Link annotations have content",
                standard="PDF/UA, WCAG 2.4.4",
                result="PASS",
                details=f"{link_ok_count} links checked",
            )
        ]
    return [
        CheckResult(
            name="Link annotations have content",
            standard="PDF/UA, WCAG 2.4.4",
            result="FAIL",
            details="; ".join(link_issues),
        )
    ]


# ---------------------------------------------------------------------------
# check_link_annotations_have_contents_key
# ---------------------------------------------------------------------------


def check_link_annotations_have_contents_key(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-012: every link annotation has a ``/Contents`` key.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Each ``/Link`` annotation must carry ``/Contents`` (a description
    string for assistive technology). Untagged annotations are matched
    to a structure element by URI overlap. Mirrors pdfMax line ~5863.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    links_missing_contents: list[str] = []
    link_contents_total = 0
    for page_num, annot, subtype_str in _collect_all_annots(ctx.pdf):
        if subtype_str != "/Link":
            continue
        link_contents_total += 1
        contents = pikepdf_helpers.get_string(annot, "/Contents")
        if contents:
            continue
        uri = _annot_has_uri(annot)[:50]
        mapping = annot_struct_map.get(id(annot))
        elem_idx = mapping[0] if mapping else None
        if elem_idx is None and uri:
            elem_idx = _find_link_element_by_uri(ctx.elements, uri)
        ref = f"[{elem_idx}] " if elem_idx is not None else ""
        if uri:
            links_missing_contents.append(f"{ref}p.{page_num}: '{uri}'")
        else:
            links_missing_contents.append(f"{ref}p.{page_num}")

    if link_contents_total == 0:
        return [
            CheckResult(
                name="Link annotations have Contents key",
                standard="Matterhorn 28-012",
                result="PASS",
                details="No link annotations in document",
            )
        ]
    if not links_missing_contents:
        return [
            CheckResult(
                name="Link annotations have Contents key",
                standard="Matterhorn 28-012",
                result="PASS",
                details=(
                    f"All {link_contents_total} link annotation(s) have"
                    + " /Contents key"
                ),
            )
        ]
    return [
        CheckResult(
            name="Link annotations have Contents key",
            standard="Matterhorn 28-012",
            result="FAIL",
            details=(
                f"{len(links_missing_contents)} link annotation(s)"
                + " missing /Contents: "
                + "; ".join(links_missing_contents[:5])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_link_annotations_inside_link_tags
# ---------------------------------------------------------------------------


def check_link_annotations_inside_link_tags(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-014: link annotations live inside ``Link`` tags.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Every ``/Link`` annotation must be a child of a ``Link`` structure
    element. The fallback from pdfMax — match untagged annotations to
    the nearest text element by URI — is preserved so the FAIL detail
    still shows the user where to look. Mirrors pdfMax line ~5718.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    links_not_in_link: list[tuple[int, str, int | None]] = []
    link_total = 0
    for page_num, annot, subtype_str in _collect_all_annots(ctx.pdf):
        if subtype_str != "/Link":
            continue
        link_total += 1
        mapping = annot_struct_map.get(id(annot))
        if mapping is not None and mapping[1] == "Link":
            continue
        # Extract URI or destination for context.
        uri = _annot_has_uri(annot)
        if not uri:
            action = pikepdf_helpers.get_dict(annot, "/A")
            if action is not None:
                try:
                    dest = action["/D"]
                    uri = f"dest:{dest}"
                except KeyError:
                    uri = ""
        elem_idx = mapping[0] if mapping else None
        if elem_idx is None and uri:
            elem_idx = _find_link_element_by_uri(ctx.elements, uri)
        links_not_in_link.append((page_num, uri, elem_idx))

    if link_total == 0:
        return [
            CheckResult(
                name="Link annotations inside Link tags",
                standard="Matterhorn 28-014",
                result="PASS",
                details="No link annotations in document",
            )
        ]
    if not links_not_in_link:
        return [
            CheckResult(
                name="Link annotations inside Link tags",
                standard="Matterhorn 28-014",
                result="PASS",
                details=(
                    f"All {link_total} link annotations are inside Link"
                    + " structure elements"
                ),
            )
        ]
    pages = sorted({p for p, _, _ in links_not_in_link})
    uri_details: list[str] = []
    for pg, uri, eidx in links_not_in_link[:6]:
        ref = f"[{eidx}] " if eidx is not None else ""
        if uri:
            short_uri = uri[:60] + ("…" if len(uri) > 60 else "")
            uri_details.append(f"{ref}p.{pg}: '{short_uri}'")
        else:
            uri_details.append(f"{ref}p.{pg}: (no URI)")
    pages_str = ", ".join(str(p) for p in pages[:10])
    detail = (
        f"{len(links_not_in_link)} link annotation(s) not inside Link"
        + f" structure element (pages {pages_str})"
    )
    if uri_details:
        detail += ": " + "; ".join(uri_details)
    return [
        CheckResult(
            name="Link annotations inside Link tags",
            standard="Matterhorn 28-014",
            result="FAIL",
            details=detail,
        )
    ]


# ---------------------------------------------------------------------------
# check_visible_annotations_have_alt_descriptions
# ---------------------------------------------------------------------------


def check_visible_annotations_have_alt_descriptions(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-005: visible non-link/widget annotations have alt text.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Every annotation outside :data:`_ANNOT_EXCLUDE` (Link / Widget /
    Popup) must carry ``/Contents``, unless its ``/F`` flag bit 1
    (Hidden) is set. Mirrors pdfMax line ~5656.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    missing_contents: list[tuple[int, str, str | None]] = []
    for page_num, annot, subtype_str in _collect_all_annots(ctx.pdf):
        if subtype_str in _ANNOT_EXCLUDE:
            continue
        f_flags = pikepdf_helpers.get_int(annot, "/F")
        if f_flags is not None and f_flags & _HIDDEN_FLAG_MASK:
            continue
        contents = pikepdf_helpers.get_string(annot, "/Contents")
        if contents:
            continue
        mapping = annot_struct_map.get(id(annot))
        elem_ref = f"[{mapping[0]}]" if mapping else None
        missing_contents.append(
            (page_num, subtype_str.lstrip("/"), elem_ref)
        )

    if not missing_contents:
        return [
            CheckResult(
                name="Visible annotations have alt descriptions",
                standard="Matterhorn 28-005",
                result="PASS",
                details="All visible annotations have /Contents descriptions",
            )
        ]
    details = "; ".join(
        f"{ref} p.{pg} {st}" if ref else f"p.{pg} {st}"
        for pg, st, ref in missing_contents[:6]
    )
    return [
        CheckResult(
            name="Visible annotations have alt descriptions",
            standard="Matterhorn 28-005",
            result="FAIL",
            details=(
                f"{len(missing_contents)} visible annotation(s) missing"
                + f" /Contents: {details}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_non_link_widget_annotations_tagged
# ---------------------------------------------------------------------------


def check_non_link_widget_annotations_tagged(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-004: non-link/widget annotations tagged in tree.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Every annotation outside :data:`_ANNOT_EXCLUDE` (Link / Widget /
    Popup) must appear in the structure tree. Mirrors pdfMax line
    ~5638.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    all_annots = _collect_all_annots(ctx.pdf)
    untagged_annots: list[tuple[int, str]] = []
    for page_num, annot, subtype_str in all_annots:
        if subtype_str in _ANNOT_EXCLUDE:
            continue
        if id(annot) not in annot_struct_map:
            untagged_annots.append((page_num, subtype_str.lstrip("/")))

    if not untagged_annots:
        non_excluded = [
            (p, a, s) for p, a, s in all_annots if s not in _ANNOT_EXCLUDE
        ]
        if non_excluded:
            return [
                CheckResult(
                    name="Non-link/widget annotations tagged",
                    standard="Matterhorn 28-004",
                    result="PASS",
                    details=(
                        f"All {len(non_excluded)} non-link/widget"
                        + " annotations are tagged in structure tree"
                    ),
                )
            ]
        return [
            CheckResult(
                name="Non-link/widget annotations tagged",
                standard="Matterhorn 28-004",
                result="PASS",
                details="No non-link/widget annotations found",
            )
        ]

    pages = sorted({p for p, _ in untagged_annots})
    pages_str = ", ".join(str(p) for p in pages[:10])
    return [
        CheckResult(
            name="Non-link/widget annotations tagged",
            standard="Matterhorn 28-004",
            result="FAIL",
            details=(
                f"{len(untagged_annots)} annotation(s) not tagged in"
                + f" structure tree (pages {pages_str})"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_multimedia_annotations_tagged
# ---------------------------------------------------------------------------


def check_multimedia_annotations_tagged(
    ctx: AuditContext,
) -> list[CheckResult]:
    """WCAG 1.2: every multimedia annotation is in the structure tree.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Multimedia covers ``/Screen``, ``/RichMedia``, ``/Sound``,
    ``/Movie``, and ``/3D`` (broader than ``_MEDIA_SUBTYPES`` because
    it includes ``/3D``). Mirrors pdfMax line ~7373 verbatim.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    multimedia_annots = [
        (pn, a, s)
        for pn, a, s in _collect_all_annots(ctx.pdf)
        if s in _MULTIMEDIA_SUBTYPES
    ]
    if not multimedia_annots:
        return [
            CheckResult(
                name="Multimedia annotations tagged",
                standard="WCAG 1.2",
                result="PASS",
                details="No multimedia annotations in document",
            )
        ]
    untagged_media = [
        (pn, s.lstrip("/"))
        for pn, a, s in multimedia_annots
        if id(a) not in annot_struct_map
    ]
    if not untagged_media:
        return [
            CheckResult(
                name="Multimedia annotations tagged",
                standard="WCAG 1.2",
                result="PASS",
                details=(
                    f"All {len(multimedia_annots)} multimedia"
                    + " annotation(s) are tagged in structure"
                ),
            )
        ]
    details = "; ".join(f"page {pn}: {st}" for pn, st in untagged_media[:5])
    return [
        CheckResult(
            name="Multimedia annotations tagged",
            standard="WCAG 1.2",
            result="FAIL",
            details=(
                f"{len(untagged_media)} of {len(multimedia_annots)}"
                + " multimedia annotation(s) not in structure tree:"
                + f" {details}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_media_clip_annotations_have_alt_text
# ---------------------------------------------------------------------------


def check_media_clip_annotations_have_alt_text(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-016: media clip annotations have ``/Contents``.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Every annotation in :data:`_MEDIA_SUBTYPES` (Screen / RichMedia /
    Sound / Movie) must carry ``/Contents``. Mirrors pdfMax line ~5816.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    media_missing_alt: list[tuple[int, str, str | None]] = []
    media_total = 0
    for page_num, annot, subtype_str in _collect_all_annots(ctx.pdf):
        if subtype_str not in _MEDIA_SUBTYPES:
            continue
        media_total += 1
        contents = pikepdf_helpers.get_string(annot, "/Contents")
        if contents:
            continue
        mapping = annot_struct_map.get(id(annot))
        elem_ref = f"[{mapping[0]}]" if mapping else None
        media_missing_alt.append(
            (page_num, subtype_str.lstrip("/"), elem_ref)
        )

    if media_total == 0:
        return [
            CheckResult(
                name="Media clip annotations have alt text",
                standard="Matterhorn 28-016",
                result="PASS",
                details="No media clip annotations found",
            )
        ]
    if not media_missing_alt:
        return [
            CheckResult(
                name="Media clip annotations have alt text",
                standard="Matterhorn 28-016",
                result="PASS",
                details=(
                    f"All {media_total} media clip annotations have"
                    + " /Contents"
                ),
            )
        ]
    details = "; ".join(
        f"{ref} p.{pg} {st}" if ref else f"p.{pg} {st}"
        for pg, st, ref in media_missing_alt[:6]
    )
    return [
        CheckResult(
            name="Media clip annotations have alt text",
            standard="Matterhorn 28-016",
            result="FAIL",
            details=(
                f"{len(media_missing_alt)} media annotation(s) missing"
                + f" /Contents alt text: {details}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_media_clip_alt_text_present
# ---------------------------------------------------------------------------


def _media_clip_has_key(annot: pikepdf.Dictionary, key: str) -> bool:
    """Return ``True`` if ``key`` appears on the annotation or its clip.

    pdfMax accepts the key either at ``annot[key]`` (direct on the
    annotation) or via the rendition action chain
    ``annot/A/R/C[key]`` — and for the content-type check, one level
    further down at ``annot/A/R/C/D[key]``. This helper covers the
    direct case plus the ``annot/A/R/C/<key>`` variant; callers needing
    the deeper path inspect ``D`` themselves.
    """
    direct = pikepdf_helpers.get_string(annot, key)
    if direct is not None:
        return True
    action = pikepdf_helpers.get_dict(annot, "/A")
    if action is None:
        return False
    rendition = pikepdf_helpers.get_dict(action, "/R")
    if rendition is None:
        return False
    clip = pikepdf_helpers.get_dict(rendition, "/C")
    if clip is None:
        return False
    return pikepdf_helpers.get_string(clip, key) is not None


def check_media_clip_alt_text_present(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-015: media clip carries ``/Alt`` on rendition or annot.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    The rendition's media clip dict (``annot/A/R/C/Alt``) or the
    annotation itself (``annot/Alt``) must carry an ``/Alt`` string.
    Mirrors pdfMax line ~5950.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    media_missing_alt_key: list[str] = []
    media_alt_total = 0
    for page_num, annot, subtype_str in _collect_all_annots(ctx.pdf):
        if subtype_str not in _MEDIA_SUBTYPES:
            continue
        media_alt_total += 1
        if _media_clip_has_key(annot, "/Alt"):
            continue
        mapping = annot_struct_map.get(id(annot))
        ref = f"[{mapping[0]}] " if mapping else ""
        media_missing_alt_key.append(
            f"{ref}p.{page_num} {subtype_str.lstrip('/')}"
        )

    if media_alt_total == 0:
        return [
            CheckResult(
                name="Media clip alt text present",
                standard="Matterhorn 28-015",
                result="PASS",
                details="No media clip annotations found",
            )
        ]
    if not media_missing_alt_key:
        return [
            CheckResult(
                name="Media clip alt text present",
                standard="Matterhorn 28-015",
                result="PASS",
                details=(
                    f"All {media_alt_total} media clip annotation(s)"
                    + " have /Alt text"
                ),
            )
        ]
    return [
        CheckResult(
            name="Media clip alt text present",
            standard="Matterhorn 28-015",
            result="FAIL",
            details=(
                f"{len(media_missing_alt_key)} media annotation(s)"
                + " missing /Alt text: "
                + ", ".join(media_missing_alt_key[:5])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_media_clip_content_type_present
# ---------------------------------------------------------------------------


def _media_has_ct(annot: pikepdf.Dictionary) -> bool:
    """Return ``True`` if a media annotation declares its content type.

    Mirrors pdfMax's check at line ~5917-5932: the ``/CT`` key may be
    direct on the annotation or buried at
    ``annot/A/R/C/D/CT`` (rendition action → rendition → media clip →
    media clip data → content type).
    """
    if pikepdf_helpers.get_string(annot, "/CT") is not None:
        return True
    action = pikepdf_helpers.get_dict(annot, "/A")
    if action is None:
        return False
    rendition = pikepdf_helpers.get_dict(action, "/R")
    if rendition is None:
        return False
    clip = pikepdf_helpers.get_dict(rendition, "/C")
    if clip is None:
        return False
    data = pikepdf_helpers.get_dict(clip, "/D")
    if data is None:
        return False
    return pikepdf_helpers.get_string(data, "/CT") is not None


def check_media_clip_content_type_present(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-014: media clip carries a ``/CT`` content type.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Mirrors pdfMax line ~5910 — note the standard string is the same
    as :func:`check_link_annotations_inside_link_tags` (Matterhorn
    28-014); pdfMax uses the same Matterhorn ID for both checks.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    media_missing_ct: list[str] = []
    media_ct_total = 0
    for page_num, annot, subtype_str in _collect_all_annots(ctx.pdf):
        if subtype_str not in _MEDIA_SUBTYPES:
            continue
        media_ct_total += 1
        if _media_has_ct(annot):
            continue
        mapping = annot_struct_map.get(id(annot))
        ref = f"[{mapping[0]}] " if mapping else ""
        media_missing_ct.append(
            f"{ref}p.{page_num} {subtype_str.lstrip('/')}"
        )

    if media_ct_total == 0:
        return [
            CheckResult(
                name="Media clip content type present",
                standard="Matterhorn 28-014",
                result="PASS",
                details="No media clip annotations found",
            )
        ]
    if not media_missing_ct:
        return [
            CheckResult(
                name="Media clip content type present",
                standard="Matterhorn 28-014",
                result="PASS",
                details=(
                    f"All {media_ct_total} media clip annotation(s) have"
                    + " content type"
                ),
            )
        ]
    return [
        CheckResult(
            name="Media clip content type present",
            standard="Matterhorn 28-014",
            result="FAIL",
            details=(
                f"{len(media_missing_ct)} media annotation(s) missing"
                + f" /CT content type: {', '.join(media_missing_ct[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_annotation_tab_order_on_all_annotated_pages
# ---------------------------------------------------------------------------


def check_annotation_tab_order_on_all_annotated_pages(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-002: every annotated page has a ``/Tabs`` entry.

    Reads :attr:`AuditContext.pdf`. Pages with no annotations don't
    need ``/Tabs`` (pdfMax line ~5803 explicitly skips them). Pages
    that have annotations must declare ``/Tabs`` so keyboard tab order
    is deterministic. Mirrors pdfMax line ~5799.
    """
    pages_missing_tabs: list[int] = []
    for page_idx, page in enumerate(ctx.pdf.pages):
        annots = pikepdf_helpers.get_array(page.obj, "/Annots")
        if annots is None or len(annots) == 0:
            continue
        tabs = pikepdf_helpers.get_name(page.obj, "/Tabs")
        if tabs is None:
            pages_missing_tabs.append(page_idx + 1)

    if not pages_missing_tabs:
        return [
            CheckResult(
                name="Annotation tab order on all annotated pages",
                standard="Matterhorn 28-002",
                result="PASS",
                details="All annotated pages have /Tabs entry set",
            )
        ]
    return [
        CheckResult(
            name="Annotation tab order on all annotated pages",
            standard="Matterhorn 28-002",
            result="FAIL",
            details=(
                f"{len(pages_missing_tabs)} annotated page(s) missing"
                + " /Tabs entry: pages "
                + ", ".join(str(p) for p in pages_missing_tabs[:10])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_no_nonstandard_annotation_subtypes
# ---------------------------------------------------------------------------


def check_no_nonstandard_annotation_subtypes(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-006: every annotation uses a standard subtype.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    A subtype that is non-empty and not in :data:`_ISO_32000_SUBTYPES`
    is reported. Annotations without a ``/Subtype`` (empty string) are
    out of scope. Mirrors pdfMax line ~5841.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    nonstandard_subtypes: list[str] = []
    all_annots = _collect_all_annots(ctx.pdf)
    for page_num, annot, subtype_str in all_annots:
        if not subtype_str or subtype_str in _ISO_32000_SUBTYPES:
            continue
        mapping = annot_struct_map.get(id(annot))
        ref = f"[{mapping[0]}] " if mapping else ""
        nonstandard_subtypes.append(
            f"{ref}p.{page_num}: {subtype_str.lstrip('/')}"
        )

    if not nonstandard_subtypes:
        return [
            CheckResult(
                name="No non-standard annotation subtypes",
                standard="Matterhorn 28-006",
                result="PASS",
                details=(
                    f"All {len(all_annots)} annotation(s) use standard"
                    + " ISO 32000 subtypes"
                ),
            )
        ]
    return [
        CheckResult(
            name="No non-standard annotation subtypes",
            standard="Matterhorn 28-006",
            result="FAIL",
            details=(
                f"{len(nonstandard_subtypes)} annotation(s) with"
                + " non-standard subtypes: "
                + ", ".join(nonstandard_subtypes[:5])
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_no_trapnet_annotations
# ---------------------------------------------------------------------------


def check_no_trapnet_annotations(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 28-006: no ``/TrapNet`` annotations in the document.

    Reads :attr:`AuditContext.pdf`. ``/TrapNet`` is a printing-process
    artifact prohibited by PDF/UA. Mirrors pdfMax line ~5685.
    """
    trapnet_pages = [
        p for p, _, s in _collect_all_annots(ctx.pdf) if s == "/TrapNet"
    ]
    if not trapnet_pages:
        return [
            CheckResult(
                name="No TrapNet annotations",
                standard="Matterhorn 28-006",
                result="PASS",
                details="No TrapNet annotations found",
            )
        ]
    pages_str = ", ".join(str(p) for p in sorted(set(trapnet_pages)))
    return [
        CheckResult(
            name="No TrapNet annotations",
            standard="Matterhorn 28-006",
            result="FAIL",
            details=(
                f"TrapNet annotation(s) found on page(s) {pages_str}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_printermark_annotations_not_in_structure
# ---------------------------------------------------------------------------


def check_printermark_annotations_not_in_structure(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-018: ``/PrinterMark`` annotations are not tagged.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    PrinterMark annotations are production artifacts; placing them in
    the structure tree pollutes the assistive-technology view of the
    document. Mirrors pdfMax line ~5782.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    printermark_in_struct: list[tuple[int, int]] = []
    for page_num, annot, subtype_str in _collect_all_annots(ctx.pdf):
        if subtype_str != "/PrinterMark":
            continue
        mapping = annot_struct_map.get(id(annot))
        if mapping is not None:
            printermark_in_struct.append((page_num, mapping[0]))

    if not printermark_in_struct:
        return [
            CheckResult(
                name="PrinterMark annotations not in structure",
                standard="Matterhorn 28-018",
                result="PASS",
                details=(
                    "No PrinterMark annotations found in structure tree"
                ),
            )
        ]
    details = "; ".join(
        f"[{eidx}] p.{pg}" for pg, eidx in printermark_in_struct[:6]
    )
    return [
        CheckResult(
            name="PrinterMark annotations not in structure",
            standard="Matterhorn 28-018",
            result="FAIL",
            details=(
                f"{len(printermark_in_struct)} PrinterMark annotation(s)"
                + f" incorrectly in structure tree: {details}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_file_attachment_annotations_valid
# ---------------------------------------------------------------------------


def check_file_attachment_annotations_valid(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 28-016: ``/FileAttachment`` carries a complete file spec.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    Each ``/FileAttachment`` annotation must carry a ``/FS`` file-spec
    dictionary with ``/F``, ``/UF``, and ``/Desc`` keys (PDF/UA §7.11).
    Mirrors pdfMax line ~5985.
    """
    annot_struct_map = _build_annot_struct_map(ctx.elements)
    fa_issues: list[str] = []
    fa_total = 0
    for page_num, annot, subtype_str in _collect_all_annots(ctx.pdf):
        if subtype_str != "/FileAttachment":
            continue
        fa_total += 1
        mapping = annot_struct_map.get(id(annot))
        ref = f"[{mapping[0]}] " if mapping else ""
        fs = pikepdf_helpers.get_dict(annot, "/FS")
        if fs is None:
            fa_issues.append(
                f"{ref}p.{page_num}: missing /FS file specification"
            )
            continue
        missing_keys: list[str] = []
        if pikepdf_helpers.get_string(fs, "/F") is None:
            missing_keys.append("/F")
        if pikepdf_helpers.get_string(fs, "/UF") is None:
            missing_keys.append("/UF")
        if pikepdf_helpers.get_string(fs, "/Desc") is None:
            missing_keys.append("/Desc")
        if missing_keys:
            fa_issues.append(
                f"{ref}p.{page_num}: file spec missing"
                + f" {', '.join(missing_keys)}"
            )

    if fa_total == 0:
        return [
            CheckResult(
                name="File attachment annotations valid",
                standard="Matterhorn 28-016",
                result="PASS",
                details="No file attachment annotations found",
            )
        ]
    if not fa_issues:
        return [
            CheckResult(
                name="File attachment annotations valid",
                standard="Matterhorn 28-016",
                result="PASS",
                details=(
                    f"All {fa_total} file attachment annotation(s)"
                    + " conform to 7.11"
                ),
            )
        ]
    return [
        CheckResult(
            name="File attachment annotations valid",
            standard="Matterhorn 28-016",
            result="FAIL",
            details=(
                f"{len(fa_issues)} file attachment issue(s):"
                + f" {'; '.join(fa_issues[:5])}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
ANNOTATIONS_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_link_annotations_have_content,
    check_link_annotations_have_contents_key,
    check_link_annotations_inside_link_tags,
    check_visible_annotations_have_alt_descriptions,
    check_non_link_widget_annotations_tagged,
    check_multimedia_annotations_tagged,
    check_media_clip_annotations_have_alt_text,
    check_media_clip_alt_text_present,
    check_media_clip_content_type_present,
    check_annotation_tab_order_on_all_annotated_pages,
    check_no_nonstandard_annotation_subtypes,
    check_no_trapnet_annotations,
    check_printermark_annotations_not_in_structure,
    check_file_attachment_annotations_valid,
]


__all__ = [
    "ANNOTATIONS_CHECKS",
    "check_annotation_tab_order_on_all_annotated_pages",
    "check_file_attachment_annotations_valid",
    "check_link_annotations_have_content",
    "check_link_annotations_have_contents_key",
    "check_link_annotations_inside_link_tags",
    "check_media_clip_alt_text_present",
    "check_media_clip_annotations_have_alt_text",
    "check_media_clip_content_type_present",
    "check_multimedia_annotations_tagged",
    "check_no_nonstandard_annotation_subtypes",
    "check_no_trapnet_annotations",
    "check_non_link_widget_annotations_tagged",
    "check_printermark_annotations_not_in_structure",
    "check_visible_annotations_have_alt_descriptions",
]
