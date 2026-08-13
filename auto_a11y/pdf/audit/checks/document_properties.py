"""Document-level (catalog/metadata/version) accessibility checks.

Thirteen checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py`` (lines ~4476-4555,
~5174-5180, ~7048-7063, ~7470-7502, ~7593-7600, ~7706-7732):

* :func:`check_document_title_set` — document info dict ``/Title`` set
  and ``/ViewerPreferences /DisplayDocTitle`` true (PDF/UA, WCAG 2.4.2;
  pdfMax line ~4476).
* :func:`check_pdf_is_tagged` — ``/MarkInfo /Marked = true`` in catalog
  (PDF/UA, WCAG 1.3.1; pdfMax line ~4493).
* :func:`check_no_suspect_tags` — ``/MarkInfo /Suspects`` is not true
  (Matterhorn 09-004; pdfMax line ~4505).
* :func:`check_pdfua_identifier` — XMP metadata stream contains
  ``pdfuaid:part`` (Matterhorn 06-002; pdfMax line ~4527).
* :func:`check_xmp_metadata_stream_present` — catalog has a
  ``/Metadata`` stream (Matterhorn 06-001; pdfMax line ~4535).
* :func:`check_xmp_dc_title_present` — XMP carries a non-empty
  ``dc:title`` element (Matterhorn 06-003; pdfMax line ~4548).
* :func:`check_metadata_completeness` — info dict carries ``/Title``,
  ``/Author``, ``/Creator``, ``/Producer``, ``/CreationDate`` (PDF/UA-1
  7.20; pdfMax line ~5175).
* :func:`check_page_labels_consistent` — ``/PageLabels /Nums`` (when
  present) starts at index 0 (WCAG (PDF17); pdfMax line ~7048).
* :func:`check_pdf_header_catalog_version_consistent` — header version
  matches catalog ``/Version`` (WCAG 2.2; pdfMax line ~7723).
* :func:`check_pdf20_structure_elements_used_correctly` — PDF 2.0 tags
  used natively rather than remapped (PDF/UA-2; pdfMax line ~7471).
* :func:`check_pdfua2_accessibility_declarations_in_xmp` — XMP carries
  ``pdfuaid:part=2`` and ``pdfuaid:rev`` (PDF/UA-2; pdfMax line ~7495).
* :func:`check_pdf20_namespace_in_structure_tree` — when PDF 2.0 tags
  are used, ``/StructTreeRoot/Namespaces`` is declared (PDF/UA-2;
  pdfMax line ~7593).
* :func:`check_pdfua2_requires_pdf20` — when PDF/UA-2 is declared, the
  effective PDF version is 2.0 or later (PDF/UA-2; pdfMax line ~7706).

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes a
:data:`DOCUMENT_PROPERTIES_CHECKS` registry list. ``CheckResult``
``name`` and ``standard`` strings match pdfMax verbatim so Phase 6's
check catalogue can map them.

Deferred from this commit:

* ``Artifact classification subtypes`` (PDF/UA-2; pdfMax line ~7569).
  Requires regex parsing of decoded content streams to correlate
  artifact markers with MCID positions; this is significantly more
  involved than the other document-level checks and overlaps with the
  reading-order pipeline that Phase 4.4 / 4.5 will address.
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


#: Native PDF 2.0 structure tags. Mirrors pdfMax's ``PDF20_TAGS`` set
#: verbatim (line ~7455).
_PDF20_TAGS: frozenset[str] = frozenset(
    {"DocumentFragment", "Aside", "Title", "Sub", "FENote", "Em", "Strong"}
)

#: Document-info fields required by PDF/UA-1 7.20. Mirrors pdfMax's
#: ``meta_fields`` dict keys at line ~5167.
_REQUIRED_INFO_FIELDS: tuple[str, ...] = (
    "/Title", "/Author", "/Creator", "/Producer", "/CreationDate",
)

#: pdfMax matches ``dc:title`` content via this regex against the raw
#: XMP text (line ~4544). We reuse it byte-for-byte so the captured
#: group survives the port.
_DC_TITLE_RE: re.Pattern[str] = re.compile(
    r"<dc:title[^>]*>\s*<rdf:Alt[^>]*>\s*<rdf:li[^>]*>([^<]+)</rdf:li>",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Helpers shared across the metadata-touching checks
# ---------------------------------------------------------------------------


def _read_xmp_text(pdf: pikepdf.Pdf) -> str:
    """Return the decoded XMP metadata stream text, or empty string.

    Mirrors pdfMax's repeated ``read_bytes()`` + UTF-8 decode at lines
    ~4520 and ~7487. The catalog ``/Metadata`` entry is a Stream rather
    than a Dictionary, so we narrow via :class:`pikepdf.Stream` directly.
    Returns ``""`` for both "no metadata stream" and "stream present
    but unreadable", matching pdfMax's behaviour.
    """
    try:
        metadata = pdf.Root["/Metadata"]
    except KeyError:
        return ""
    if not isinstance(metadata, pikepdf.Stream):
        return ""
    try:
        xmp_bytes = bytes(metadata.read_bytes())
    except (pikepdf.PdfError, AttributeError):
        return ""
    try:
        return xmp_bytes.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return ""


def _xmp_has_pdfuaid_part2(xmp_text: str) -> bool:
    """Return ``True`` when the XMP payload declares ``pdfuaid:part=2``.

    Mirrors pdfMax's whitespace-stripping substring check at line ~7491:
    ``"pdfuaid:part" in xmp_text and ">2<" in xmp_text.replace(" ", "")``.
    """
    if "pdfuaid:part" not in xmp_text:
        return False
    return ">2<" in xmp_text.replace(" ", "")


def _has_metadata_stream(pdf: pikepdf.Pdf) -> bool:
    """Return ``True`` if the catalog carries a ``/Metadata`` Stream."""
    try:
        metadata = pdf.Root["/Metadata"]
    except KeyError:
        return False
    return isinstance(metadata, pikepdf.Stream)


def _read_bool(node: pikepdf.Dictionary, key: str) -> bool:
    """Read ``node[key]`` and coerce to Python ``bool``.

    pikepdf decodes PDF Booleans into Python ``bool`` at the dictionary
    boundary; ``bool(True) == 1`` so :func:`pikepdf_helpers.get_int`
    returns 1 / 0 for true / false. Names, Strings, and other PDF
    object kinds aren't expected in the PDF/UA boolean fields this
    helper covers (``/Marked``, ``/Suspects``, ``/DisplayDocTitle``);
    pdfMax just used Python's ``bool()`` on the raw value (lines ~4470,
    ~4494, ~4504), so any non-numeric oddity falls through to ``False``.
    """
    int_val = pikepdf_helpers.get_int(node, key)
    if int_val is not None:
        return int_val != 0
    # Names like /true / /false aren't conventional but render truthy
    # under pdfMax's bool() — preserve the spirit by treating any
    # non-empty Name as True.
    name_val = pikepdf_helpers.get_name(node, key)
    if name_val is not None:
        rendered = str(name_val).lstrip("/")
        return rendered.lower() not in {"", "false"}
    return False


def _effective_pdf_version(pdf: pikepdf.Pdf) -> tuple[str, str | None]:
    """Return ``(header_version, catalog_version_or_None)``.

    Mirrors pdfMax line ~7452-7454 and ~7718-7720. ``catalog_version``
    is the value of ``/Version`` rendered without the leading ``/``;
    ``None`` when absent. ``header_version`` is the always-set
    ``pdf.pdf_version`` string.
    """
    header = str(pdf.pdf_version)
    catalog_name = pikepdf_helpers.get_name(pdf.Root, "/Version")
    catalog = (
        str(catalog_name).lstrip("/") if catalog_name is not None else None
    )
    return header, catalog


def _used_pdf20_tags(elements: list[StructElement]) -> set[str]:
    """Return the set of PDF 2.0 tags actually used in the structure tree.

    Mirrors pdfMax line ~7466-7468: an element matches if either its
    ``custom_tag`` or its ``resolved_tag`` is in :data:`_PDF20_TAGS`.
    """
    out: set[str] = set()
    for elem in elements:
        custom_clean = elem.custom_tag.lstrip("/")
        if custom_clean in _PDF20_TAGS:
            out.add(custom_clean)
        elif elem.resolved_tag in _PDF20_TAGS:
            out.add(elem.resolved_tag)
    return out


# ---------------------------------------------------------------------------
# check_document_title_set
# ---------------------------------------------------------------------------


def check_document_title_set(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 2.4.2: document info dict ``/Title`` is set.

    PASSes when ``/Title`` is set *and*
    ``/ViewerPreferences/DisplayDocTitle`` is true; WARNs when ``/Title``
    is set but ``DisplayDocTitle`` is missing or false (the title would
    not actually be shown to the user); FAILs when no ``/Title`` is
    present. Mirrors pdfMax line ~4476.
    """
    info = ctx.pdf.docinfo
    title = pikepdf_helpers.get_string(info, "/Title")

    display_title = False
    vp = pikepdf_helpers.get_dict(ctx.pdf.Root, "/ViewerPreferences")
    if vp is not None:
        display_title = _read_bool(vp, "/DisplayDocTitle")

    if title and title.strip():
        detail = f'Title: "{title}"'
        if not display_title:
            detail += (
                " (WARNING: DisplayDocTitle not set in ViewerPreferences)"
            )
            return [
                CheckResult(
                    name="Document title set",
                    standard="PDF/UA, WCAG 2.4.2",
                    result="WARN",
                    details=detail,
                )
            ]
        return [
            CheckResult(
                name="Document title set",
                standard="PDF/UA, WCAG 2.4.2",
                result="PASS",
                details=detail,
            )
        ]
    return [
        CheckResult(
            name="Document title set",
            standard="PDF/UA, WCAG 2.4.2",
            result="FAIL",
            details="No /Title in document info",
        )
    ]


# ---------------------------------------------------------------------------
# check_pdf_is_tagged
# ---------------------------------------------------------------------------


def check_pdf_is_tagged(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 1.3.1: document carries the ``/MarkInfo /Marked`` flag.

    Mirrors pdfMax line ~4493: PASS when ``/MarkInfo`` exists and
    ``/Marked`` is true; FAIL when ``/MarkInfo`` exists but ``/Marked``
    is false or missing; FAIL when ``/MarkInfo`` itself is absent.
    """
    mark_info = pikepdf_helpers.get_dict(ctx.pdf.Root, "/MarkInfo")
    if mark_info is None:
        return [
            CheckResult(
                name="PDF is tagged",
                standard="PDF/UA, WCAG 1.3.1",
                result="FAIL",
                details="No /MarkInfo dictionary",
            )
        ]
    if _read_bool(mark_info, "/Marked"):
        return [
            CheckResult(
                name="PDF is tagged",
                standard="PDF/UA, WCAG 1.3.1",
                result="PASS",
                details="/MarkInfo/Marked = true",
            )
        ]
    return [
        CheckResult(
            name="PDF is tagged",
            standard="PDF/UA, WCAG 1.3.1",
            result="FAIL",
            details="/MarkInfo exists but /Marked is not true",
        )
    ]


# ---------------------------------------------------------------------------
# check_no_suspect_tags
# ---------------------------------------------------------------------------


def check_no_suspect_tags(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 09-004: ``/MarkInfo /Suspects`` is not set to true.

    Mirrors pdfMax line ~4505. PASS when ``/MarkInfo`` is absent (the
    ``PDF is tagged`` check covers that gap) or when ``/Suspects`` is
    false / missing. WARN when ``/Suspects`` is explicitly true.
    """
    mark_info = pikepdf_helpers.get_dict(ctx.pdf.Root, "/MarkInfo")
    if mark_info is None:
        return [
            CheckResult(
                name="No suspect tags",
                standard="Matterhorn 09-004",
                result="NA",
                details="No /MarkInfo (checked separately)",
            )
        ]
    if _read_bool(mark_info, "/Suspects"):
        return [
            CheckResult(
                name="No suspect tags",
                standard="Matterhorn 09-004",
                result="WARN",
                details=(
                    "/MarkInfo/Suspects is true — tags may have been"
                    " auto-generated and not reviewed"
                ),
            )
        ]
    return [
        CheckResult(
            name="No suspect tags",
            standard="Matterhorn 09-004",
            result="PASS",
            details="/Suspects not set or false",
        )
    ]


# ---------------------------------------------------------------------------
# check_pdfua_identifier
# ---------------------------------------------------------------------------


def check_pdfua_identifier(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 06-002: XMP metadata declares ``pdfuaid:part``.

    Mirrors pdfMax line ~4527. The check is case-insensitive and looks
    for the ``pdfuaid:part`` substring anywhere in the decoded XMP
    payload (the same loose match pdfMax uses).
    """
    xmp_text = _read_xmp_text(ctx.pdf)
    if "pdfuaid:part" in xmp_text.lower():
        return [
            CheckResult(
                name="PDF/UA identifier",
                standard="Matterhorn 06-002",
                result="PASS",
                details="PDF/UA identifier (pdfuaid:part) found in XMP metadata",
            )
        ]
    return [
        CheckResult(
            name="PDF/UA identifier",
            standard="Matterhorn 06-002",
            result="WARN",
            details=(
                "No PDF/UA identifier in XMP metadata — assistive"
                " technology may not recognize this as a PDF/UA document"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_xmp_metadata_stream_present
# ---------------------------------------------------------------------------


def check_xmp_metadata_stream_present(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 06-001: catalog carries an XMP ``/Metadata`` stream.

    Mirrors pdfMax line ~4535.
    """
    if _has_metadata_stream(ctx.pdf):
        return [
            CheckResult(
                name="XMP metadata stream present",
                standard="Matterhorn 06-001",
                result="PASS",
                details="/Metadata stream found in document catalog",
            )
        ]
    return [
        CheckResult(
            name="XMP metadata stream present",
            standard="Matterhorn 06-001",
            result="FAIL",
            details=(
                "No /Metadata stream in document catalog — PDF/UA"
                " requires XMP metadata"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_xmp_dc_title_present
# ---------------------------------------------------------------------------


def check_xmp_dc_title_present(ctx: AuditContext) -> list[CheckResult]:
    """Matterhorn 06-003: XMP metadata carries a non-empty ``dc:title``.

    Mirrors pdfMax line ~4548. The same regex (``_DC_TITLE_RE``) is
    re-used so the captured group survives byte-for-byte. FAILs when
    the metadata stream is missing, when ``dc:title`` is absent, or
    when the captured text strips to empty.
    """
    xmp_text = _read_xmp_text(ctx.pdf)
    if not xmp_text:
        return [
            CheckResult(
                name="XMP dc:title present",
                standard="Matterhorn 06-003",
                result="FAIL",
                details="No XMP metadata to check for dc:title",
            )
        ]
    match = _DC_TITLE_RE.search(xmp_text)
    if match is None or not match.group(1).strip():
        return [
            CheckResult(
                name="XMP dc:title present",
                standard="Matterhorn 06-003",
                result="FAIL",
                details="dc:title is missing or empty in XMP metadata",
            )
        ]
    captured = match.group(1).strip()[:80]
    return [
        CheckResult(
            name="XMP dc:title present",
            standard="Matterhorn 06-003",
            result="PASS",
            details=f'dc:title found in XMP: "{captured}"',
        )
    ]


# ---------------------------------------------------------------------------
# check_metadata_completeness
# ---------------------------------------------------------------------------


def check_metadata_completeness(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA-1 7.20: info dict carries the five conventional fields.

    Mirrors pdfMax line ~5175. PASS when all of ``/Title``, ``/Author``,
    ``/Creator``, ``/Producer``, ``/CreationDate`` are present; WARN
    otherwise, listing the missing and present fields.
    """
    info = ctx.pdf.docinfo
    field_values: dict[str, str | None] = {
        f: pikepdf_helpers.get_string(info, f) for f in _REQUIRED_INFO_FIELDS
    }

    missing = [f for f, v in field_values.items() if not v]
    if not missing:
        return [
            CheckResult(
                name="Metadata completeness",
                standard="PDF/UA-1 7.20",
                result="PASS",
                details=(
                    "All metadata fields present: Title, Author, Creator,"
                    " Producer, CreationDate"
                ),
            )
        ]
    present = [f for f, v in field_values.items() if v]
    return [
        CheckResult(
            name="Metadata completeness",
            standard="PDF/UA-1 7.20",
            result="WARN",
            details=(
                f"Missing: {', '.join(missing)};"
                f" Present: {', '.join(present)}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_page_labels_consistent
# ---------------------------------------------------------------------------


def check_page_labels_consistent(ctx: AuditContext) -> list[CheckResult]:
    """WCAG (PDF17): ``/PageLabels /Nums`` (when present) starts at 0.

    Mirrors pdfMax line ~7048. The ``/PageLabels`` entry is optional;
    when absent, this check passes. When present, ``/Nums`` must be a
    non-empty array starting with the integer ``0`` (the standard
    convention for "page labels begin at the first page").
    """
    page_labels = pikepdf_helpers.get_dict(ctx.pdf.Root, "/PageLabels")
    if page_labels is None:
        return [
            CheckResult(
                name="Page labels consistent",
                standard="WCAG (PDF17)",
                result="NA",
                details="No /PageLabels dictionary — page labels are optional",
            )
        ]
    nums = pikepdf_helpers.get_array(page_labels, "/Nums")
    if nums is None or len(nums) == 0:
        return [
            CheckResult(
                name="Page labels consistent",
                standard="WCAG (PDF17)",
                result="FAIL",
                details=(
                    "/PageLabels present but /Nums array is missing or empty"
                ),
            )
        ]
    # /Nums alternates int-key, dict-value pairs. pdfMax requires len>=2
    # before reading the first int (line ~7057); shorter arrays fall
    # back to ``None`` and FAIL with the same message format.
    first_idx: int | None
    if len(nums) >= 2:
        first_raw = nums[0]
        try:
            first_idx = int(first_raw)
        except (TypeError, ValueError):
            first_idx = None
    else:
        first_idx = None
    if first_idx != 0:
        return [
            CheckResult(
                name="Page labels consistent",
                standard="WCAG (PDF17)",
                result="FAIL",
                details=(
                    f"/PageLabels /Nums starts at index {first_idx}"
                    " instead of 0"
                ),
            )
        ]
    return [
        CheckResult(
            name="Page labels consistent",
            standard="WCAG (PDF17)",
            result="PASS",
            details=(
                f"Page labels defined with {len(nums) // 2} range(s),"
                " starting at page 0"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_pdf_header_catalog_version_consistent
# ---------------------------------------------------------------------------


def check_pdf_header_catalog_version_consistent(
    ctx: AuditContext,
) -> list[CheckResult]:
    """WCAG 2.2: PDF header version agrees with catalog ``/Version``.

    Mirrors pdfMax line ~7723. PASS when no catalog ``/Version``
    override is present (header is authoritative) or when both values
    match; FAIL when they disagree.
    """
    header, catalog = _effective_pdf_version(ctx.pdf)
    if catalog is None:
        return [
            CheckResult(
                name="PDF header and catalog version consistent",
                standard="WCAG 2.2",
                result="NA",
                details=(
                    f"No catalog /Version override — header version"
                    f" {header} is authoritative"
                ),
            )
        ]
    if catalog == header:
        return [
            CheckResult(
                name="PDF header and catalog version consistent",
                standard="WCAG 2.2",
                result="PASS",
                details=(
                    f"Header version ({header}) and catalog /Version"
                    f" ({catalog}) match"
                ),
            )
        ]
    return [
        CheckResult(
            name="PDF header and catalog version consistent",
            standard="WCAG 2.2",
            result="FAIL",
            details=(
                f"Header version ({header}) disagrees with catalog"
                f" /Version ({catalog}) — assistive technology may"
                " interpret document capabilities inconsistently"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_pdf20_structure_elements_used_correctly
# ---------------------------------------------------------------------------


def check_pdf20_structure_elements_used_correctly(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA-2: PDF 2.0 tags are used natively, not remapped.

    Mirrors pdfMax line ~7471. Builds a list of PDF 2.0 tags that the
    RoleMap remaps to legacy equivalents (``Aside→Sect`` etc.) and a
    list of PDF 2.0 tags actually used by structure elements. WARN
    when any PDF 2.0 tag is remapped; PASS otherwise (no PDF 2.0 tags
    found *or* every PDF 2.0 tag is used natively).
    """
    struct_root = pikepdf_helpers.get_dict(ctx.pdf.Root, "/StructTreeRoot")
    pdf20_remapped: list[str] = []
    if struct_root is not None:
        role_map_raw = pikepdf_helpers.get_dict(struct_root, "/RoleMap")
        if role_map_raw is not None:
            for key in role_map_raw.keys():
                tag_name = str(key).lstrip("/")
                if tag_name in _PDF20_TAGS:
                    target = str(role_map_raw[key]).lstrip("/")
                    pdf20_remapped.append(f"{tag_name}→{target}")

    pdf20_used = _used_pdf20_tags(ctx.elements)

    if not pdf20_used and not pdf20_remapped:
        return [
            CheckResult(
                name="New PDF 2.0 structure elements used correctly",
                standard="PDF/UA-2",
                result="NA",
                details="No PDF 2.0 specific structure elements found",
            )
        ]
    if pdf20_remapped:
        return [
            CheckResult(
                name="New PDF 2.0 structure elements used correctly",
                standard="PDF/UA-2",
                result="WARN",
                details=(
                    f"PDF 2.0 tags remapped in /RoleMap:"
                    f" {', '.join(pdf20_remapped[:5])} — these should use"
                    " native PDF 2.0 semantics"
                ),
            )
        ]
    return [
        CheckResult(
            name="New PDF 2.0 structure elements used correctly",
            standard="PDF/UA-2",
            result="PASS",
            details=(
                f"PDF 2.0 structure elements used:"
                f" {', '.join(sorted(pdf20_used))}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_pdfua2_accessibility_declarations_in_xmp
# ---------------------------------------------------------------------------


def check_pdfua2_accessibility_declarations_in_xmp(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA-2: XMP carries ``pdfuaid:part=2`` and ``pdfuaid:rev``.

    Mirrors pdfMax line ~7495. PASS when both are present; WARN when
    ``part=2`` is present but ``rev`` is missing; WARN when ``part=2``
    itself is absent.
    """
    xmp_text = _read_xmp_text(ctx.pdf)
    has_part2 = _xmp_has_pdfuaid_part2(xmp_text)
    has_rev = "pdfuaid:rev" in xmp_text

    if has_part2 and has_rev:
        return [
            CheckResult(
                name="PDF/UA-2 accessibility declarations in XMP",
                standard="PDF/UA-2",
                result="PASS",
                details="XMP contains pdfuaid:part=2 and pdfuaid:rev",
            )
        ]
    if has_part2:
        return [
            CheckResult(
                name="PDF/UA-2 accessibility declarations in XMP",
                standard="PDF/UA-2",
                result="WARN",
                details=(
                    "XMP has pdfuaid:part=2 but missing pdfuaid:rev year"
                ),
            )
        ]
    return [
        CheckResult(
            name="PDF/UA-2 accessibility declarations in XMP",
            standard="PDF/UA-2",
            result="WARN",
            details=(
                "No PDF/UA-2 identification found in XMP metadata"
                " (pdfuaid:part=2 missing)"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_pdf20_namespace_in_structure_tree
# ---------------------------------------------------------------------------


def check_pdf20_namespace_in_structure_tree(
    ctx: AuditContext,
) -> list[CheckResult]:
    """PDF/UA-2: when PDF 2.0 tags are in use, ``/Namespaces`` is declared.

    Mirrors pdfMax line ~7593. When no PDF 2.0 tags are used, the check
    PASSes regardless of whether ``/Namespaces`` is set. When PDF 2.0
    tags are in use, ``/StructTreeRoot/Namespaces`` must be present.
    """
    struct_root = pikepdf_helpers.get_dict(ctx.pdf.Root, "/StructTreeRoot")
    has_namespaces = False
    if struct_root is not None:
        ns = pikepdf_helpers.get_array(struct_root, "/Namespaces")
        if ns is not None:
            has_namespaces = True

    pdf20_used = _used_pdf20_tags(ctx.elements)
    if not pdf20_used:
        return [
            CheckResult(
                name="PDF 2.0 namespace in structure tree",
                standard="PDF/UA-2",
                result="NA",
                details=(
                    "No PDF 2.0 specific tags in use — namespace not"
                    " required"
                ),
            )
        ]
    if has_namespaces:
        return [
            CheckResult(
                name="PDF 2.0 namespace in structure tree",
                standard="PDF/UA-2",
                result="PASS",
                details=(
                    "Structure tree has /Namespaces entry for PDF 2.0 tags"
                ),
            )
        ]
    return [
        CheckResult(
            name="PDF 2.0 namespace in structure tree",
            standard="PDF/UA-2",
            result="FAIL",
            details=(
                "PDF 2.0 structure tags are used but"
                " /StructTreeRoot/Namespaces is missing"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_pdfua2_requires_pdf20
# ---------------------------------------------------------------------------


def check_pdfua2_requires_pdf20(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA-2: when ``pdfuaid:part=2`` is declared, version is 2.0+.

    Mirrors pdfMax line ~7706. The "effective" version is the catalog
    ``/Version`` if set, otherwise the header version (line ~7454).
    """
    xmp_text = _read_xmp_text(ctx.pdf)
    has_part2 = _xmp_has_pdfuaid_part2(xmp_text)
    if not has_part2:
        return [
            CheckResult(
                name="PDF/UA-2 requires PDF 2.0",
                standard="PDF/UA-2",
                result="NA",
                details=(
                    "No PDF/UA-2 identification — PDF 2.0 version check"
                    " not required"
                ),
            )
        ]
    header, catalog = _effective_pdf_version(ctx.pdf)
    effective = catalog if catalog is not None else header
    if effective.startswith("2."):
        return [
            CheckResult(
                name="PDF/UA-2 requires PDF 2.0",
                standard="PDF/UA-2",
                result="PASS",
                details=(
                    f"PDF version is {effective} (PDF 2.0 or later)"
                ),
            )
        ]
    return [
        CheckResult(
            name="PDF/UA-2 requires PDF 2.0",
            standard="PDF/UA-2",
            result="FAIL",
            details=(
                f"PDF/UA-2 declared but PDF version is {effective}"
                " (must be 2.0 or later)"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.


# ---------------------------------------------------------------------------
# check_document_has_text_layer
# ---------------------------------------------------------------------------

#: A page carrying at least this many images and no text is a scanned page.
#: One is enough: a scan is one photograph of a sheet of paper.
_SCAN_IMAGE_THRESHOLD = 1


def check_document_has_text_layer(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 1.4.5: the document's words exist as text, not only as pictures.

    Neither this engine nor pdfMax previously said this outright, and it
    is the single most important thing to know about a document that has
    it wrong. A scan is a photograph of a page: a screen reader finds
    nothing to read, text cannot be searched, selected, resized or
    reflowed, and no amount of tagging will change that — the words are
    not in the file as words.

    Reported when every page carries an image and no page carries any
    text. Partial coverage is deliberately not flagged here: a report with
    one scanned appendix is a different problem from a document that is
    entirely a scan, and the per-page failures already describe it.

    The remedy is optical character recognition, which has to happen
    before any other fix in this report can apply.
    """
    name = "Document has a text layer"
    standard = "WCAG 1.4.5, PDF/UA"

    if ctx.content_classification is None:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="Page content was not classified",
        )]
    pages = ctx.content_classification
    if not pages:
        return [CheckResult(
            name=name, standard=standard, result="NA",
            details="Document has no pages",
        )]

    if any(page.text_operators for page in pages):
        return [CheckResult(
            name=name, standard=standard, result="PASS",
            details="The document contains real text",
        )]

    imaged = [
        page.page_number for page in pages
        if page.image_operators >= _SCAN_IMAGE_THRESHOLD
    ]
    if len(imaged) == len(pages):
        return [CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                f"No page contains any text; all {len(pages)} page(s) are"
                " images. This document is a scan and needs optical"
                " character recognition before any other fault in this"
                " report can be corrected."
            ),
        )]

    return [CheckResult(
        name=name, standard=standard, result="FAIL",
        details=(
            f"No page contains any text across {len(pages)} page(s), so"
            " there is nothing for a screen reader to read"
        ),
    )]


def check_accessibility_permission_not_restricted(
    ctx: AuditContext,
) -> list[CheckResult]:
    """Matterhorn 26-001: encryption must not block accessibility extraction.

    Mirrors pdfMax line ~4565. A PDF can be encrypted such that text
    extraction is forbidden, which stops assistive technology reading it at
    all — the document may be perfectly tagged and still be unusable.

    Reads pikepdf's ``allow.accessibility`` and falls back to bit 10
    (``0x200``) of ``/P``, which is the flag that accessor derives. An
    unencrypted document passes: there is no restriction to violate.

    (pdfMax read ``allow.extract_for_accessibility``, which pikepdf does
    not define — its ``AttributeError`` fallback meant this check silently
    ran on the ``/P`` bit every time.)
    """
    name = "Accessibility permission not restricted"
    standard = "Matterhorn 26-001"

    try:
        encrypt = ctx.pdf.Root.get("/Encrypt")
    except Exception:  # noqa: BLE001 — a malformed catalog is not this check
        encrypt = None

    if encrypt is None:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details="PDF is not encrypted — no permission restrictions",
            )
        ]

    permitted: bool
    basis = ""
    try:
        permitted = bool(ctx.pdf.allow.accessibility)
    except (AttributeError, TypeError):
        p_value = pikepdf_helpers.get_int(encrypt, "/P") or 0
        permitted = bool(p_value & 0x200)
        basis = " (P bit 10 set)" if permitted else " (P bit 10 not set)"

    if permitted:
        return [
            CheckResult(
                name=name, standard=standard, result="PASS",
                details=(
                    "PDF is encrypted but accessibility extraction is "
                    f"permitted{basis}"
                ),
            )
        ]
    return [
        CheckResult(
            name=name, standard=standard, result="FAIL",
            details=(
                "PDF encryption restricts content extraction for "
                f"accessibility{basis} — assistive technology cannot read "
                "this document"
            ),
        )
    ]


DOCUMENT_PROPERTIES_CHECKS: list[
    Callable[[AuditContext], list[CheckResult]]
] = [
    check_accessibility_permission_not_restricted,
    check_document_has_text_layer,
    check_document_title_set,
    check_pdf_is_tagged,
    check_no_suspect_tags,
    check_pdfua_identifier,
    check_xmp_metadata_stream_present,
    check_xmp_dc_title_present,
    check_metadata_completeness,
    check_page_labels_consistent,
    check_pdf_header_catalog_version_consistent,
    check_pdf20_structure_elements_used_correctly,
    check_pdfua2_accessibility_declarations_in_xmp,
    check_pdf20_namespace_in_structure_tree,
    check_pdfua2_requires_pdf20,
]


__all__ = [
    "DOCUMENT_PROPERTIES_CHECKS",
    "check_document_has_text_layer",
    "check_document_title_set",
    "check_metadata_completeness",
    "check_no_suspect_tags",
    "check_page_labels_consistent",
    "check_pdf20_namespace_in_structure_tree",
    "check_pdf20_structure_elements_used_correctly",
    "check_pdf_header_catalog_version_consistent",
    "check_pdf_is_tagged",
    "check_pdfua2_accessibility_declarations_in_xmp",
    "check_pdfua2_requires_pdf20",
    "check_pdfua_identifier",
    "check_xmp_dc_title_present",
    "check_xmp_metadata_stream_present",
]
