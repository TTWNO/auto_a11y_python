"""Link- and navigation-related accessibility checks.

Three checks ported from pdfMax's
``python/checker/pdf_accessibility_audit.py``:

* :func:`check_link_alt_text_descriptive` — Link tags whose alt text is
  a raw URL or protocol (``https://``, ``www.``, ``tel:``, ``mailto:``)
  are flagged so authors can replace the URL string with descriptive
  link text (WCAG 2.4.4 best practice; pdfMax line ~5212).
* :func:`check_bookmarks_present` — long documents (more than four
  pages) are expected to expose an ``/Outlines`` bookmark tree so
  screen-reader users can navigate by section (PDF/UA, WCAG 2.4.5;
  pdfMax line ~6238).
* :func:`check_cross_language_link_targets_identified` — for documents
  whose declared ``/Lang`` is English or French, ``Link`` structure
  elements pointing to a URL in the other language must warn the user
  about the language change in the link text or alt text (WCAG 3.1.2
  best practice; pdfMax line ~5265).

Annotation-side ``Link`` checks (``Link annotations have content`` /
``Link annotations have Contents key`` / ``Link annotations inside Link
tags``) live in :mod:`auto_a11y.pdf.audit.checks.annotations`. The
intra-document destination check (``Structure destinations for
intra-document links``) lives in
:mod:`auto_a11y.pdf.audit.checks.tagging_structure`. ``Page labels
consistent`` lives in :mod:`auto_a11y.pdf.audit.checks.document_properties`.

The cross-language check is a deliberate constrained port of pdfMax's
implementation: pdfMax falls back to text-content language inference
(Claude or word-frequency) when the catalog ``/Lang`` is missing or
non-en/fr. Those helpers aren't available on :class:`AuditContext`, so
this module follows pdfMax's "language not set and could not be
inferred" branch (line ~5378-5381) and emits a FAIL when the catalog
``/Lang`` is absent or outside ``{en, fr}``. TODO(phase 4 followup):
once the text language-detection helper deferred from
:mod:`auto_a11y.pdf.audit.checks.language` lands, swap that helper in
here so non-English/French documents can also be inspected.

Mirrors the convention established in
:mod:`auto_a11y.pdf.audit.checks.headings`: each check is a plain
function ``(ctx) -> list[CheckResult]`` and the module exposes a
:data:`LINKS_NAVIGATION_CHECKS` registry list. ``CheckResult.name`` and
``CheckResult.standard`` strings match pdfMax's verbatim so Phase 6's
catalogue can map them.
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


#: Threshold (page count) above which :func:`check_bookmarks_present`
#: WARNs when bookmarks are absent. Mirrors pdfMax line ~6246.
_BOOKMARKS_PAGE_THRESHOLD: int = 4


#: Regex for raw URL / protocol prefixes that indicate the alt text is
#: not descriptive. Mirrors pdfMax line ~5219 verbatim, including the
#: leading ``^`` anchor and the case-insensitive flag.
_RAW_URL_PROTOCOL_RE: re.Pattern[str] = re.compile(
    r"^(https?://|www\.|tel:|mailto:)", re.IGNORECASE,
)


#: URL-substring patterns that flag a link as English. Mirrors pdfMax
#: line ~5285-5293 verbatim.
_EN_URL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"/charities-giving",
        r"/en/",
        r"/en-",
        r"[-_]EN[-_\.]",
        r"[-_]English[-_\.]",
        r"\.en\.",
        r"/english/",
    )
)


#: URL-substring patterns that flag a link as French. Mirrors pdfMax
#: line ~5294-5303 verbatim.
_FR_URL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"/organismes-bienfaisance",
        r"/fr/",
        r"/fr-",
        r"[-_]FR[-_\.]",
        r"[-_]French[-_\.]",
        r"\.fr\.",
        r"/french/",
        r"/francais/",
    )
)


#: Substrings (lowercased) inside the link's text/alt content that
#: indicate the author has warned the user about a language change.
#: Mirrors pdfMax line ~5354-5358 verbatim.
_LANG_WARNINGS: tuple[str, ...] = (
    "english", "anglais", "french", "fran",
    "(en)", "(fr)", "[en]", "[fr]",
    "in english", "en anglais", "in french", "en fran",
)


#: URI scheme prefixes that are skipped by the cross-language check —
#: phone and email links don't carry resource language. Mirrors pdfMax
#: line ~5317.
_NON_HTTP_SCHEMES: tuple[str, ...] = ("tel:", "mailto:")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc_lang_code(pdf: pikepdf.Pdf) -> str | None:
    """Return the catalog ``/Lang`` lowercased and truncated to two chars.

    Returns ``None`` when ``/Lang`` is absent or empty after stripping.
    Mirrors pdfMax line ~5269-5270 ("doc_lang_code = str(lang).lower()[:2]")
    while preserving the language module's "/Lang missing" semantics.
    """
    try:
        lang_obj = pdf.Root["/Lang"]
    except KeyError:
        return None
    rendered = str(lang_obj)
    if not rendered.strip():
        return None
    return rendered.strip().lower()[:2]


def _link_uri_from_objr(elem: StructElement) -> str | None:
    """Resolve the ``/A/URI`` string for a ``Link`` structure element.

    Walks ``elem.obj["/K"]`` looking for an ``OBJR`` child that
    references an annotation with an action dictionary; returns that
    action's ``/URI`` as a Python string. Returns ``None`` if no URI
    is found. Mirrors pdfMax line ~5321-5337.
    """
    k_array = pikepdf_helpers.get_array(elem.obj, "/K")
    items: list[pikepdf.Object]
    if k_array is not None:
        items = [k_array[i] for i in range(len(k_array))]
    else:
        try:
            single = elem.obj["/K"]
        except KeyError:
            return None
        items = [single]
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
        if not isinstance(obj_ref, pikepdf.Dictionary):
            continue
        action = pikepdf_helpers.get_dict(obj_ref, "/A")
        if action is None:
            continue
        uri = pikepdf_helpers.get_string(action, "/URI")
        if uri is not None and uri:
            return uri
    return None


def _classify_url_lang(uri: str) -> str | None:
    """Return ``"en"``, ``"fr"``, or ``None`` for the URI's language hint.

    Checks English patterns first (matching pdfMax's order at line
    ~5340-5348), then French. Returns ``None`` if no pattern matches.
    """
    for pat in _EN_URL_PATTERNS:
        if pat.search(uri):
            return "en"
    for pat in _FR_URL_PATTERNS:
        if pat.search(uri):
            return "fr"
    return None


def _link_warns_about_language(elem: StructElement) -> bool:
    """Return ``True`` if the link's text/alt content mentions a language.

    Joins ``elem.text_content`` and ``elem.alt_text`` and looks for any
    of :data:`_LANG_WARNINGS`. Mirrors pdfMax line ~5352-5359.
    """
    parts = (elem.text_content or "") + " " + (elem.alt_text or "")
    parts_lower = parts.lower()
    return any(w in parts_lower for w in _LANG_WARNINGS)


# ---------------------------------------------------------------------------
# check_link_alt_text_descriptive
# ---------------------------------------------------------------------------


def check_link_alt_text_descriptive(ctx: AuditContext) -> list[CheckResult]:
    """WCAG 2.4.4 best practice: link alt text isn't a raw URL/protocol.

    Reads :attr:`AuditContext.elements`. Every ``Link`` element whose
    ``alt_text`` matches :data:`_RAW_URL_PROTOCOL_RE` is flagged. When
    the link also has a more readable ``text_content`` distinct from
    the alt text, the detail message highlights that the alt text
    overrides the visible text. Mirrors pdfMax line ~5212-5239 verbatim.
    """
    issues: list[str] = []
    for elem in ctx.elements:
        if elem.resolved_tag != "Link" or not elem.alt_text:
            continue
        alt = elem.alt_text.strip()
        if not _RAW_URL_PROTOCOL_RE.match(alt):
            continue
        visible_text = elem.text_content or ""
        if visible_text and visible_text.strip() != alt:
            issues.append(
                f'[{elem.index + 1}] Link alt text is URL/protocol "{alt[:60]}"'
                + f' but visible text is "{visible_text.strip()[:60]}" — '
                + "alt text overrides the more readable visible text"
            )
        else:
            issues.append(
                f'[{elem.index + 1}] Link alt text is URL/protocol "{alt[:60]}"'
                + " — consider using descriptive text instead"
            )

    if not issues:
        return [
            CheckResult(
                name="Link alt text is descriptive",
                standard="WCAG 2.4.4 (best practice)",
                result="PASS",
                details="No links use raw URLs or protocols as alt text",
            )
        ]
    return [
        CheckResult(
            name="Link alt text is descriptive",
            standard="WCAG 2.4.4 (best practice)",
            result="WARN",
            details="; ".join(issues),
        )
    ]


# ---------------------------------------------------------------------------
# check_bookmarks_present
# ---------------------------------------------------------------------------


def check_bookmarks_present(ctx: AuditContext) -> list[CheckResult]:
    """PDF/UA, WCAG 2.4.5: long documents expose a bookmark tree.

    Reads :attr:`AuditContext.pdf`. The document catalog's ``/Outlines``
    dictionary must have a non-zero ``/Count`` for the tree to count as
    populated. Documents with at most four pages PASS unconditionally
    because bookmarks aren't required there; longer documents without
    bookmarks WARN. Mirrors pdfMax line ~6238-6254 verbatim, including
    the four-page threshold.
    """
    num_pages = len(ctx.pdf.pages)
    outlines = pikepdf_helpers.get_dict(ctx.pdf.Root, "/Outlines")
    has_bookmarks = (
        outlines is not None
        and (pikepdf_helpers.get_int(outlines, "/Count") or 0) != 0
    )
    if has_bookmarks:
        return [
            CheckResult(
                name="Bookmarks present",
                standard="PDF/UA, WCAG 2.4.5",
                result="PASS",
                details="Document has bookmarks (outlines) for navigation",
            )
        ]
    if num_pages > _BOOKMARKS_PAGE_THRESHOLD:
        return [
            CheckResult(
                name="Bookmarks present",
                standard="PDF/UA, WCAG 2.4.5",
                result="WARN",
                details=(
                    f"Document has {num_pages} pages but no bookmarks — "
                    "bookmarks help users navigate longer documents"
                ),
            )
        ]
    return [
        CheckResult(
            name="Bookmarks present",
            standard="PDF/UA, WCAG 2.4.5",
            result="PASS",
            details=(
                f"Document has {num_pages} page(s) — bookmarks not required"
                " for short documents"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# check_cross_language_link_targets_identified
# ---------------------------------------------------------------------------


def check_cross_language_link_targets_identified(
    ctx: AuditContext,
) -> list[CheckResult]:
    """WCAG 3.1.2 best practice: cross-language links warn about lang change.

    Reads :attr:`AuditContext.pdf` and :attr:`AuditContext.elements`.
    For documents whose declared ``/Lang`` is English (``en``) or
    French (``fr``), every ``Link`` structure element whose URL matches
    the *other* language's URL patterns must warn the user via its text
    or alt content (one of :data:`_LANG_WARNINGS`). Mirrors pdfMax line
    ~5265-5381 verbatim, restricted to the catalog-``/Lang`` branch
    (the text-inference fallback hasn't been ported yet).

    Verdicts:

    * PASS when no foreign-language links exist or all are properly
      flagged.
    * WARN when one or more cross-language links lack a warning.
    * FAIL when ``/Lang`` is absent or outside ``{en, fr}`` and so the
      document language can't be assessed (mirrors pdfMax line
      ~5378-5381).
    """
    doc_lang_code = _doc_lang_code(ctx.pdf)
    if doc_lang_code not in ("en", "fr"):
        return [
            CheckResult(
                name="Cross-language link targets identified",
                standard="WCAG 3.1.2 (best practice)",
                result="FAIL",
                details=(
                    "Cannot assess — document language not set and could"
                    " not be inferred from content"
                ),
            )
        ]
    doc_lang_name = "English" if doc_lang_code == "en" else "French"

    issues: list[str] = []
    for elem in ctx.elements:
        if elem.resolved_tag != "Link":
            continue
        uri = _link_uri_from_objr(elem)
        if uri is None:
            continue
        if uri.startswith(_NON_HTTP_SCHEMES):
            continue
        url_lang = _classify_url_lang(uri)
        if url_lang is None or url_lang == doc_lang_code:
            continue
        if _link_warns_about_language(elem):
            continue
        lang_name = "English" if url_lang == "en" else "French"
        issues.append(
            f'[{elem.index + 1}] Link to {lang_name} resource'
            + f' "{uri[:70]}" in {doc_lang_name} document — '
            + "link text does not warn users about language change"
        )

    if not issues:
        return [
            CheckResult(
                name="Cross-language link targets identified",
                standard="WCAG 3.1.2 (best practice)",
                result="PASS",
                details=(
                    "No links to foreign-language resources found, or all"
                    " are properly indicated"
                ),
            )
        ]
    return [
        CheckResult(
            name="Cross-language link targets identified",
            standard="WCAG 3.1.2 (best practice)",
            result="WARN",
            details="; ".join(issues),
        )
    ]


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------


#: Phase 5.3's pipeline iterates this list in order. Phase 6's check
#: catalogue iterates the same list to enumerate every check name.
LINKS_NAVIGATION_CHECKS: list[Callable[[AuditContext], list[CheckResult]]] = [
    check_link_alt_text_descriptive,
    check_bookmarks_present,
    check_cross_language_link_targets_identified,
]


__all__ = [
    "LINKS_NAVIGATION_CHECKS",
    "check_bookmarks_present",
    "check_cross_language_link_targets_identified",
    "check_link_alt_text_descriptive",
]
