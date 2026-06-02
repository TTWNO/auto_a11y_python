"""Tests for the content-hash-aware pdfMax report cache.

:func:`cached_or_run_pdfmax` previously keyed its cache purely on the
on-disk ``.md`` mtime vs. the source PDF's mtime. That is unsound: if a
PDF is overwritten in place within the same filesystem-mtime
granularity (or an atomic rename preserves a near-identical mtime), the
stale ``.md`` report — whose mtime is ``>=`` the new PDF's mtime — gets
reused even though the PDF content changed.

These tests pin the corrected contract: the cache is valid only when the
*content hash* of the current PDF matches the hash captured when the
report was produced. ``os.utime`` is used to force identical mtimes on
the PDF and report so the old mtime-only logic would (wrongly) reuse the
cache; the content hash is what must drive the decision.

``run_pdfmax`` is monkeypatched so no real pdfMax subprocess is shelled
out; the stub records its invocations and writes a fake report.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from auto_a11y.pdf import pdfmax_runner
from auto_a11y.pdf.pdfmax_runner import (
    PdfMaxReport,
    cached_or_run_pdfmax,
)


def _force_same_mtime(*paths: Path, mtime: float) -> None:
    """Pin identical access/modify times on every path."""
    for p in paths:
        os.utime(p, (mtime, mtime))


class _RunRecorder:
    """Stand-in for ``run_pdfmax`` that writes a fake report.

    Each call writes ``<stem>_accessibility_report.md`` into the
    requested ``output_dir`` with body derived from the run index, then
    returns a :class:`PdfMaxReport`. The call count and the markdown it
    wrote let tests assert whether a (re-)run happened.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        *,
        pdf_path: Path,
        output_dir: Path,
        wcag_level: Any = "AA",
        skip_claude: bool = True,
        pdfmax_dir: Path | None = None,
        timeout_seconds: float = 600.0,
    ) -> PdfMaxReport:
        self.calls += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"{pdf_path.stem}_accessibility_report.md"
        markdown = f"# report run {self.calls}\n"
        report_path.write_text(markdown, encoding="utf-8")
        return PdfMaxReport(markdown=markdown, output_dir=output_dir)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _RunRecorder:
    rec = _RunRecorder()
    monkeypatch.setattr(pdfmax_runner, "run_pdfmax", rec)
    return rec


def test_same_mtime_different_content_busts_cache(
    tmp_path: Path, recorder: _RunRecorder
) -> None:
    """Overwriting the PDF with different content (but identical mtime)
    must NOT reuse the stale cached report."""
    pdf_path = tmp_path / "doc.pdf"
    cache_dir = tmp_path / "cache"
    pdf_path.write_bytes(b"%PDF-1.4 original content\n")

    # First call: cache miss -> runs.
    first = cached_or_run_pdfmax(pdf_path=pdf_path, cache_dir=cache_dir)
    assert recorder.calls == 1
    assert "run 1" in first.markdown

    # Overwrite the PDF with DIFFERENT content, then force the report and
    # PDF to share an identical mtime. The old mtime-only logic would see
    # report.mtime >= pdf.mtime and wrongly reuse the stale report.
    pdf_path.write_bytes(b"%PDF-1.4 COMPLETELY DIFFERENT content\n")
    report_path = next(cache_dir.glob("*_accessibility_report.md"))
    shared = pdf_path.stat().st_mtime
    _force_same_mtime(pdf_path, report_path, mtime=shared)

    # Content hash differs -> must re-run.
    second = cached_or_run_pdfmax(pdf_path=pdf_path, cache_dir=cache_dir)
    assert recorder.calls == 2, "cache must be busted on content change"
    assert "run 2" in second.markdown


def test_same_content_reuses_cache(
    tmp_path: Path, recorder: _RunRecorder
) -> None:
    """Identical content (same hash) reuses the cached report even if the
    PDF's mtime moves forward."""
    pdf_path = tmp_path / "doc.pdf"
    cache_dir = tmp_path / "cache"
    pdf_path.write_bytes(b"%PDF-1.4 stable content\n")

    first = cached_or_run_pdfmax(pdf_path=pdf_path, cache_dir=cache_dir)
    assert recorder.calls == 1

    # Touch the PDF to a NEWER mtime without changing its bytes. Under a
    # mtime-only cache this would force a needless re-run; under a
    # content-hash cache the matching hash short-circuits it.
    report_path = next(cache_dir.glob("*_accessibility_report.md"))
    newer = pdf_path.stat().st_mtime + 1000.0
    os.utime(pdf_path, (newer, newer))

    second = cached_or_run_pdfmax(pdf_path=pdf_path, cache_dir=cache_dir)
    assert recorder.calls == 1, "matching content hash must reuse cache"
    assert second.markdown == first.markdown
    assert report_path.is_file()


def test_first_run_persists_hash_sidecar(
    tmp_path: Path, recorder: _RunRecorder
) -> None:
    """A run records the PDF's content hash so later calls can compare."""
    pdf_path = tmp_path / "doc.pdf"
    cache_dir = tmp_path / "cache"
    pdf_path.write_bytes(b"%PDF-1.4 hashable\n")

    cached_or_run_pdfmax(pdf_path=pdf_path, cache_dir=cache_dir)
    assert recorder.calls == 1

    # A re-call with the unchanged file must reuse the cache (the hash was
    # persisted by the first run), proving the hash survives across calls.
    cached_or_run_pdfmax(pdf_path=pdf_path, cache_dir=cache_dir)
    assert recorder.calls == 1
