"""Apply a selected set of fixes to a PDF and write a corrected copy.

The original is never modified. Every run writes a new file, so a fix that
turns out to be wrong costs the user nothing — which matters because
several fixes rewrite the structure tree and cannot be undone in place.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from auto_a11y.pdf.fix.models import FixOptions, FixResult
from auto_a11y.pdf.fix.registry import FIX_REGISTRY

logger = logging.getLogger(__name__)

# Called with (human-readable step, percent complete 0-100).
ProgressCallback = Callable[[str, int], None]

# Percent reserved for opening the file before the first fix runs, and for
# saving after the last one. Fixes share the span between them.
_OPEN_PCT = 5
_FIRST_FIX_PCT = 10
_SAVE_PCT = 90


class UnknownFixError(ValueError):
    """Raised when a requested fix ID is not in the registry."""


@dataclass(frozen=True)
class FixRun:
    """Outcome of one apply-fixes run."""

    output_path: Path
    results: Sequence[FixResult]

    @property
    def success_count(self) -> int:
        """How many fixes reported success."""
        return sum(1 for r in self.results if r.success)

    @property
    def failure_count(self) -> int:
        """How many fixes declined or errored."""
        return len(self.results) - self.success_count


def default_output_path(pdf_path: Path) -> Path:
    """Where a fixed copy of ``pdf_path`` is written by default."""
    return pdf_path.with_name(f"{pdf_path.stem}_fixed{pdf_path.suffix}")


def apply_fixes(
    *,
    pdf_path: Path,
    fix_ids: Sequence[str],
    options: FixOptions | None = None,
    output_path: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> FixRun:
    """Apply ``fix_ids`` to ``pdf_path`` and save the result.

    Args:
        pdf_path: The PDF to fix. Opened read-only; never overwritten.
        fix_ids: Registry IDs to apply, in order. Order matters — several
            fixes read state an earlier one establishes (``fix_mark_info``
            after tagging, ``fix_xmp_title`` after ``fix_title``).
        options: User-supplied inputs. Defaults to an empty set, which
            leaves input-requiring fixes to decline with an explanation.
        output_path: Destination. Defaults to ``<name>_fixed.pdf``.
        on_progress: Optional progress sink.

    Returns:
        A :class:`FixRun` carrying the output path and one
        :class:`FixResult` per requested fix, in request order.

    Raises:
        FileNotFoundError: ``pdf_path`` does not exist.
        UnknownFixError: a requested ID is not in the registry.
        ValueError: ``fix_ids`` is empty.
    """
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not fix_ids:
        raise ValueError("No fixes requested")

    unknown = [f for f in fix_ids if f not in FIX_REGISTRY]
    if unknown:
        raise UnknownFixError(f"Unknown fix IDs: {', '.join(sorted(unknown))}")

    destination = output_path or default_output_path(pdf_path)
    opts = options or FixOptions(pdf_path=pdf_path)

    def progress(message: str, percent: int) -> None:
        if on_progress is not None:
            on_progress(message, percent)

    progress(f"Opening {pdf_path.name}", _OPEN_PCT)

    results: list[FixResult] = []
    span = _SAVE_PCT - _FIRST_FIX_PCT
    with pikepdf.open(pdf_path, allow_overwriting_input=False) as pdf:
        for index, fix_id in enumerate(fix_ids):
            progress(
                f"Applying {fix_id}",
                _FIRST_FIX_PCT + (span * index // len(fix_ids)),
            )
            try:
                results.append(FIX_REGISTRY[fix_id](pdf, opts))
            except Exception as exc:  # noqa: BLE001
                # One failing fix must not abandon the rest, nor lose the
                # fixes already applied to the in-memory document.
                logger.warning("Fix %s failed: %s", fix_id, exc)
                results.append(FixResult(fix_id, False, f"Error: {exc}"))

        progress("Saving fixed PDF", _SAVE_PCT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(destination)

    progress("Complete", 100)
    return FixRun(output_path=destination, results=results)
