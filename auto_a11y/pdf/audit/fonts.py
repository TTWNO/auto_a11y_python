"""PDF font analysis collector.

Ports :func:`extract_font_analysis` and its four pdfminer-based helpers
from pdfMax's ``python/checker/pdf_accessibility_audit.py`` (around
line 2650). The original returned an anonymous 5-tuple of dicts/lists
typed loosely; we replace each inner shape with a dataclass and wrap
the result in :class:`FontAnalysis` so the zero-escape-hatch policy
holds end to end.

Design notes:

* :class:`FontInfo` keys the public :attr:`FontAnalysis.fonts` dict by
  font name (de-duped), aggregating per-font sizes/pages/character
  count. pdfMax keyed by ``(name, size)``; consolidating to name-only
  with a ``set[float]`` of sizes is more useful for downstream checks
  while preserving the same raw data.
* :attr:`FontInfo.is_italic` and :attr:`FontInfo.is_bold` derive from
  the font name via the same pattern lists pdfMax uses
  (:func:`is_italic_font`, :func:`is_bold_font`).
* :class:`RotationInfo` is emitted once per *unique angle* — pdfMax
  aggregates rotations by angle into a dict; we materialise that
  aggregation as a list with one entry per angle, carrying the first
  sample and page seen.
* The four ``_collect_*`` helpers walk the pdfminer layout tree using
  the same ``LTContainer``-narrowing :class:`_LayoutContainerLike`
  Protocol pattern used by :mod:`auto_a11y.pdf.audit.colors`. The
  Protocol is duplicated here rather than factored into a shared
  module because the LT-walker pattern is small and the two callers
  diverge in their leaf handling — a shared helper would have to be
  parameterised over ``(visit_leaf, recurse_into)`` and would obscure
  more than it dedupes.
* On parse error the public :func:`extract_font_analysis` returns an
  empty :class:`FontAnalysis` so the audit pipeline degrades
  gracefully — the same defensive swallow pdfMax used.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar, LTItem, LTTextBox
from pdfminer.pdftypes import PDFException
from pdfminer.psparser import PSException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class FontInfo:
    """Per-font usage statistics aggregated across the whole document."""

    name: str
    sample: str = ""
    sizes: set[float] = field(default_factory=set[float])
    pages: set[int] = field(default_factory=set[int])
    char_count: int = 0
    is_italic: bool = False
    is_bold: bool = False


@dataclass
class RotationInfo:
    """One unique text rotation angle, with the first sample/page seen."""

    angle: float
    sample: str
    page: int


@dataclass
class ItalicRun:
    """A contiguous run of italic-styled characters."""

    text: str
    font: str
    size: float
    page: int


@dataclass
class LineSpacing:
    """Inter-line leading measurement from a multi-line text block."""

    font_size: float
    leading: float
    ratio: float
    text: str


@dataclass
class AlignmentReport:
    """Detected paragraph alignment for a multi-line text block."""

    page: int
    alignment: str  # "left" | "right" | "center" | "justified" | "mixed"
    line_count: int


@dataclass(frozen=True)
class FontAnalysis:
    """Aggregate output of :func:`extract_font_analysis`.

    The five collections mirror pdfMax's anonymous 5-tuple return
    (``fonts, rotations, italic_runs, line_spacings, alignments``).
    """

    fonts: dict[str, FontInfo]
    rotations: list[RotationInfo]
    italic_runs: list[ItalicRun]
    line_spacings: list[LineSpacing]
    alignments: list[AlignmentReport]


# ---------------------------------------------------------------------------
# Italic / bold detection (font-name heuristics)
# ---------------------------------------------------------------------------


_ITALIC_PATTERNS: tuple[str, ...] = ("italic", "oblique", "ital", "it", "slanted")
_BOLD_PATTERNS: tuple[str, ...] = (
    "bold", "heavy", "black", "semibold", "demibold", "extrabold", "ultrabold",
)
# "it" needs to be at a word boundary so "Semibold" doesn't match.
_IT_BOUNDARY_RE = re.compile(r"(?:^|[^a-z])it(?:[^a-z]|$)")
# "bd" abbreviation, similarly word-boundary-anchored.
_BD_BOUNDARY_RE = re.compile(r"(?:^|[^a-z])bd(?:[^a-z]|$)")


def is_italic_font(fontname: str) -> bool:
    """Return ``True`` if a font name indicates an italic / oblique style.

    Mirrors pdfMax's ``is_italic_font`` exactly: tokens are matched as
    substrings on a normalised (lowercased, ``-``/``_`` stripped) form,
    except for the bare token ``it`` which is anchored at word
    boundaries to avoid false positives like ``StagSans-Semibold``.
    """
    fname_lower = fontname.lower().replace("-", "").replace("_", "")
    for pattern in _ITALIC_PATTERNS:
        if pattern == "it":
            if _IT_BOUNDARY_RE.search(fname_lower):
                return True
        elif pattern in fname_lower:
            return True
    return False


def is_bold_font(fontname: str) -> bool:
    """Return ``True`` if a font name indicates a bold / heavy weight.

    Mirrors pdfMax's ``is_bold_font``.
    """
    fname_lower = fontname.lower().replace("-", "").replace("_", "")
    for pattern in _BOLD_PATTERNS:
        if pattern in fname_lower:
            return True
    if _BD_BOUNDARY_RE.search(fname_lower):
        return True
    return False


# ---------------------------------------------------------------------------
# Layout container narrowing (see colors.py for full rationale)
# ---------------------------------------------------------------------------


@runtime_checkable
class _LayoutContainerLike(Protocol):
    """Structural view of a pdfminer.layout container.

    Duplicated from :mod:`auto_a11y.pdf.audit.colors`. Factoring it out
    would require parameterising every walker over its leaf-handler
    callback, which obscures more than it dedupes for two call sites.
    See ``colors.py`` for the full rationale on why ``runtime_checkable``
    + Protocol narrowing avoids the ``LTContainer[Unknown]`` leak under
    pyright strict mode.
    """

    def __iter__(self) -> Iterator[LTItem]: ...


# ---------------------------------------------------------------------------
# Mutable accumulators threaded through the recursive walkers
# ---------------------------------------------------------------------------


@dataclass
class _FontAccum:
    """Aggregate keyed by font name; backs :class:`FontInfo`."""

    fonts: dict[str, FontInfo] = field(default_factory=dict[str, FontInfo])
    # Rotation aggregate: angle → (first sample, first page seen).
    rotations_by_angle: dict[float, RotationInfo] = field(
        default_factory=dict[float, RotationInfo]
    )


def collect_fonts(
    element: object,
    accum: _FontAccum,
    page_num: int,
) -> None:
    """Recursively collect per-font stats and rotation data.

    Public (no leading underscore) so tests can import it; pyright's
    ``reportPrivateUsage`` would complain otherwise. The walker matches
    pdfMax's ``_collect_fonts`` byte for byte except that:

    * the per-font key is ``fontname`` (not ``(fontname, size)``) — the
      full size set is preserved on :attr:`FontInfo.sizes`, and the
      original pdfMax tuple-keyed shape is recoverable from
      ``(name, size)`` cross-products downstream;
    * rotation data is keyed by angle in :attr:`_FontAccum.rotations_by_angle`
      (deduped, first-seen sample) and materialised as a list at the end.
    """
    if isinstance(element, LTChar):
        fname = element.fontname or "unknown"
        try:
            fsize = round(float(element.size), 1)
        except (AttributeError, TypeError, ValueError):
            return

        info = accum.fonts.get(fname)
        if info is None:
            info = FontInfo(
                name=fname,
                is_italic=is_italic_font(fname),
                is_bold=is_bold_font(fname),
            )
            accum.fonts[fname] = info
        info.sizes.add(fsize)
        info.pages.add(page_num)
        try:
            text = element.get_text()
        except (AttributeError, TypeError, ValueError):
            text = ""
        info.char_count += len(text)
        if len(info.sample) < 40:
            info.sample += text

        # Rotation tracking: matrix is a Tuple[float, ...]; m[0]/m[1]
        # encode the rotation angle. pdfMax wraps this in a broad
        # try/except — we narrow to the specific exceptions that can
        # arise when matrix is malformed or unindexable.
        try:
            m = element.matrix
            angle = float(round(math.degrees(math.atan2(m[1], m[0]))))
        except (AttributeError, TypeError, IndexError, ValueError):
            return

        existing = accum.rotations_by_angle.get(angle)
        if existing is None:
            accum.rotations_by_angle[angle] = RotationInfo(
                angle=angle,
                sample=text[:40],
                page=page_num,
            )
        elif len(existing.sample) < 40:
            existing.sample = (existing.sample + text)[:40]
        return

    if isinstance(element, _LayoutContainerLike):
        for child in element:
            collect_fonts(child, accum, page_num)


def collect_italic_runs(
    element: object,
    italic_runs: list[ItalicRun],
    page_num: int,
) -> None:
    """Track contiguous runs of italic-named characters.

    A "run" is a maximal stretch of consecutive ``LTChar``s in the
    iteration order whose fontname matches :func:`is_italic_font`.
    Descending into a non-LTChar container *flushes* any pending run
    before recursion (mirrors pdfMax's behaviour exactly), which means
    runs do not span container boundaries.
    """
    if not isinstance(element, _LayoutContainerLike):
        return

    current_run: list[str] = []
    current_font: str | None = None
    current_size: float = 0.0

    def _flush() -> None:
        if not current_run:
            return
        text = "".join(current_run).strip()
        if text and current_font is not None:
            italic_runs.append(
                ItalicRun(
                    text=text,
                    font=current_font,
                    size=current_size,
                    page=page_num,
                )
            )
        current_run.clear()

    for child in element:
        if isinstance(child, LTChar):
            fname = child.fontname or ""
            if is_italic_font(fname):
                try:
                    current_run.append(child.get_text())
                except (AttributeError, TypeError, ValueError):
                    continue
                current_font = fname
                try:
                    current_size = float(child.size)
                except (AttributeError, TypeError, ValueError):
                    current_size = 0.0
            else:
                _flush()
                current_font = None
        elif isinstance(child, _LayoutContainerLike):
            _flush()
            current_font = None
            collect_italic_runs(child, italic_runs, page_num)
    _flush()


def collect_line_spacing(
    element: object,
    line_spacings: list[LineSpacing],
) -> None:
    """Measure inter-line leading inside :class:`LTTextBox` elements.

    For each ``LTTextBox`` with two or more ``LTTextLine`` children
    sorted top-to-bottom, emit one :class:`LineSpacing` per adjacent
    pair whose ``leading / font_size`` ratio falls in ``(0.5, 5.0)`` —
    the band pdfMax considers plausibly meaningful (filters out
    overlapping lines and column gaps).
    """
    if isinstance(element, LTTextBox):
        # Sort top-to-bottom by upper edge (PDF y is bottom-origin, so
        # higher y1 = higher on the page). LTTextBox is parameterised
        # as ``LTTextContainer[LTTextLine]`` so the iteration is
        # already narrowed — no isinstance check needed.
        lines = sorted(element, key=lambda line: -line.y1)
        if len(lines) >= 2:
            fsize: float | None = None
            for char in lines[0]:
                if isinstance(char, LTChar):
                    try:
                        fsize = float(char.size)
                    except (AttributeError, TypeError, ValueError):
                        fsize = None
                    break
            if fsize is not None and fsize > 0:
                for i in range(len(lines) - 1):
                    leading = float(lines[i].y1) - float(lines[i + 1].y1)
                    ratio = leading / fsize
                    if 0.5 < ratio < 5.0:
                        text = lines[i].get_text().strip()[:40]
                        line_spacings.append(
                            LineSpacing(
                                font_size=round(fsize, 1),
                                leading=round(leading, 1),
                                ratio=round(ratio, 2),
                                text=text,
                            )
                        )
        return

    if isinstance(element, _LayoutContainerLike):
        for child in element:
            if isinstance(child, LTTextBox):
                collect_line_spacing(child, line_spacings)


def collect_alignment(
    element: object,
    alignments: list[AlignmentReport],
    page_num: int,
) -> None:
    """Detect paragraph alignment for :class:`LTTextBox` blocks of 3+ lines.

    Mirrors pdfMax's ``_collect_alignment``: classifies based on the
    range of left edges (``x0``) and right edges (``x1``) across all
    lines, with a 3-point tolerance. Blocks shorter than 3 lines are
    skipped (the signal is too noisy).
    """
    tolerance = 3.0

    if isinstance(element, LTTextBox):
        # LTTextBox is parameterised as ``LTTextContainer[LTTextLine]``
        # so iteration is already narrowed.
        lines = list(element)
        if len(lines) >= 3:
            x0s = [float(line.x0) for line in lines]
            x1s = [float(line.x1) for line in lines]
            x0_range = max(x0s) - min(x0s)
            x1_range = max(x1s) - min(x1s)

            if x0_range < tolerance and x1_range < tolerance:
                alignment = "justified"
            elif x0_range < tolerance:
                alignment = "left"
            elif x1_range < tolerance:
                alignment = "right"
            else:
                centers = [(float(line.x0) + float(line.x1)) / 2 for line in lines]
                center_range = max(centers) - min(centers)
                if center_range < 5.0:
                    alignment = "center"
                else:
                    alignment = "mixed"

            alignments.append(
                AlignmentReport(
                    page=page_num,
                    alignment=alignment,
                    line_count=len(lines),
                )
            )
        return

    if isinstance(element, _LayoutContainerLike):
        for child in element:
            if isinstance(child, LTTextBox):
                collect_alignment(child, alignments, page_num)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_font_analysis(pdf_path: Path) -> FontAnalysis:
    """Extract fonts, rotations, italics, line spacing, and alignment.

    Wraps the four pdfminer-based collectors into a single typed
    result. On parse error (malformed PDF, I/O failure, garbage layout
    operands) returns an empty :class:`FontAnalysis` so the audit
    pipeline can degrade gracefully — the same defensive swallow
    pdfMax used.
    """
    accum = _FontAccum()
    italic_runs: list[ItalicRun] = []
    line_spacings: list[LineSpacing] = []
    alignments: list[AlignmentReport] = []

    laparams = LAParams(
        line_margin=0.5,
        word_margin=0.1,
        char_margin=2.0,
        boxes_flow=0.5,
    )

    try:
        for page_num, page_layout in enumerate(
            extract_pages(pdf_path, laparams=laparams)
        ):
            collect_fonts(page_layout, accum, page_num)
            collect_italic_runs(page_layout, italic_runs, page_num)
            collect_line_spacing(page_layout, line_spacings)
            collect_alignment(page_layout, alignments, page_num)
    # pdfminer raises PSException / PDFException for malformed PDFs;
    # OSError covers I/O failures, the rest are defensive against
    # garbage operands inside the layout walker. Mirrors the catch-all
    # broad swallow in pdfMax's original.
    except (PSException, PDFException, OSError, ValueError, TypeError, AssertionError) as exc:
        logger.warning("pdfminer failed to parse %s: %s", pdf_path, exc)
        return FontAnalysis(
            fonts={}, rotations=[], italic_runs=[],
            line_spacings=[], alignments=[],
        )

    rotations = list(accum.rotations_by_angle.values())
    return FontAnalysis(
        fonts=accum.fonts,
        rotations=rotations,
        italic_runs=italic_runs,
        line_spacings=line_spacings,
        alignments=alignments,
    )
