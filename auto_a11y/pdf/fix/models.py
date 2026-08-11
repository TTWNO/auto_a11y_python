"""Types shared by every PDF fix.

The pdfMax original passes fix inputs as ``**kwargs`` and lets each fix
reach in with ``kwargs.get("title")``. That is untypeable: nothing
declares which keys exist, what they hold, or which fix reads which.

Here the same inputs are a single frozen :class:`FixOptions` record. Each
fix takes ``(pdf, opts)`` and reads named attributes, so a typo is a type
error rather than a silently-missing input, and the set of things a user
can supply is discoverable from one place — which is also what the
fix-selection UI needs in order to know which fixes require input.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FixResult:
    """Outcome of applying one fix.

    ``success`` is False both for a fix that raised and for one that
    declined to act; ``description`` carries the human-readable reason
    either way and is surfaced verbatim in the report.
    """

    fix_id: str
    success: bool
    description: str


@dataclass(frozen=True)
class ListConversionGroup:
    """One run of sibling paragraphs to convert into a list.

    ``element_keys`` are :class:`~auto_a11y.pdf.models.TagElement` keys in
    document order; ``ordered`` picks ``<L>`` numbering over bullets.
    """

    element_keys: Sequence[str]
    ordered: bool = False


@dataclass(frozen=True)
class FixOptions:
    """Everything a fix may need beyond the PDF itself.

    Every field is optional except :attr:`pdf_path`, which fixes use to
    derive a fallback document title. Fixes that need input the user did
    not supply return an unsuccessful :class:`FixResult` explaining what
    was missing rather than guessing.

    The ``*_map`` fields are keyed by structure-element key so the UI can
    collect per-element input (alt text for *this* figure, a level for
    *that* heading) and hand back one flat mapping.
    """

    pdf_path: Path

    # Document properties
    title: str | None = None
    lang: str | None = None
    author: str | None = None
    creator: str | None = None
    producer: str | None = None
    creation_date: str | None = None

    # Per-element input, keyed by structure-element key
    alt_text_map: Mapping[str, str] = field(default_factory=dict[str, str])
    heading_levels_map: Mapping[str, str] = field(default_factory=dict[str, str])
    table_headers_map: Mapping[str, str] = field(default_factory=dict[str, str])
    formula_alt_map: Mapping[str, str] = field(default_factory=dict[str, str])
    annot_descriptions_map: Mapping[str, str] = field(default_factory=dict[str, str])
    link_content_map: Mapping[str, str] = field(default_factory=dict[str, str])
    table_captions_map: Mapping[str, str] = field(default_factory=dict[str, str])

    # Paragraph-run → list conversion
    list_conversion_groups: Sequence[ListConversionGroup] = ()

    # How :func:`~auto_a11y.pdf.fix.lists.fix_list_labels` fills a label:
    # "none" (structure only), "bullet", or "numbered". Defaults to the
    # structural form because the fix cannot see the page, and announcing
    # a bullet on a numbered list — or renumbering one that starts at 5 —
    # contradicts what the reader is looking at.
    list_label_style: str = "none"
