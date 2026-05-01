"""Smoke tests for :mod:`auto_a11y.pdf.pdfmax_runner`.

The runner shells out to pdfMax's
``pdf_accessibility_audit.py``. We don't re-test pdfMax — these tests
verify our wrapper's contract:

* Missing pdfMax checkout → :class:`PdfMaxRunError` with a clear message
* Subprocess returncode != 0 → :class:`PdfMaxRunError` carrying stderr
* Cache hit when the on-disk ``.md`` post-dates the source PDF
* Cache miss invalidates and re-runs

A live integration test (actually invoking the pdfMax CLI) sits
behind an env-var gate because (a) it costs ~10s on a typical PDF and
(b) the pdfMax checkout isn't available in CI.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from auto_a11y.pdf.pdfmax_runner import (
    PdfMaxReport,
    PdfMaxRunError,
    cached_or_run_pdfmax,
    run_pdfmax,
)


def _write_fake_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\n%fake\n%%EOF\n")


# ---------------------------------------------------------------------------
# run_pdfmax — error paths
# ---------------------------------------------------------------------------


class TestRunPdfMaxErrors:

    def test_missing_pdfmax_dir_raises(self, tmp_path: Path) -> None:
        pdf = tmp_path / "in.pdf"
        _write_fake_pdf(pdf)
        out = tmp_path / "out"
        with pytest.raises(PdfMaxRunError, match="pdfMax checker directory"):
            run_pdfmax(
                pdf_path=pdf,
                output_dir=out,
                pdfmax_dir=tmp_path / "does-not-exist",
            )

    def test_missing_audit_script_raises(self, tmp_path: Path) -> None:
        pdf = tmp_path / "in.pdf"
        _write_fake_pdf(pdf)
        empty_pdfmax = tmp_path / "empty-pdfmax"
        empty_pdfmax.mkdir()
        with pytest.raises(PdfMaxRunError, match="audit script missing"):
            run_pdfmax(
                pdf_path=pdf,
                output_dir=tmp_path / "out",
                pdfmax_dir=empty_pdfmax,
            )

    def test_subprocess_failure_raises_with_stderr(
        self, tmp_path: Path
    ) -> None:
        # Build a faux pdfmax dir whose audit script always exits 1.
        pdfmax = tmp_path / "fake-pdfmax"
        pdfmax.mkdir()
        (pdfmax / "pdf_accessibility_audit.py").write_text("exit code")
        pdf = tmp_path / "in.pdf"
        _write_fake_pdf(pdf)

        # Mock subprocess.run so we don't actually invoke python3.
        fake_result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="some pdfMax error",
        )
        with patch("auto_a11y.pdf.pdfmax_runner.subprocess.run",
                   return_value=fake_result):
            with pytest.raises(PdfMaxRunError, match="some pdfMax error"):
                run_pdfmax(
                    pdf_path=pdf,
                    output_dir=tmp_path / "out",
                    pdfmax_dir=pdfmax,
                )

    def test_zero_exit_but_no_md_file_raises(
        self, tmp_path: Path
    ) -> None:
        pdfmax = tmp_path / "fake-pdfmax"
        pdfmax.mkdir()
        (pdfmax / "pdf_accessibility_audit.py").write_text("# no-op")
        pdf = tmp_path / "in.pdf"
        _write_fake_pdf(pdf)
        out = tmp_path / "out"
        out.mkdir()

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        with patch("auto_a11y.pdf.pdfmax_runner.subprocess.run",
                   return_value=fake_result):
            with pytest.raises(PdfMaxRunError,
                               match="did not produce a Markdown report"):
                run_pdfmax(
                    pdf_path=pdf, output_dir=out, pdfmax_dir=pdfmax,
                )


# ---------------------------------------------------------------------------
# run_pdfmax — happy path (mocked subprocess)
# ---------------------------------------------------------------------------


class TestRunPdfMaxHappyPath:

    def test_returns_markdown_when_subprocess_writes_report(
        self, tmp_path: Path
    ) -> None:
        pdfmax = tmp_path / "fake-pdfmax"
        pdfmax.mkdir()
        (pdfmax / "pdf_accessibility_audit.py").write_text("# stub")
        pdf = tmp_path / "doc.pdf"
        _write_fake_pdf(pdf)
        out = tmp_path / "out"
        out.mkdir()

        # The fake subprocess: write a markdown file matching pdfMax's
        # naming convention, then return rc=0.
        def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            _ = cmd, kwargs
            (out / "doc_accessibility_report.md").write_text(
                "# fake pdfMax report"
            )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr="",
            )

        with patch("auto_a11y.pdf.pdfmax_runner.subprocess.run",
                   side_effect=_fake_run):
            report = run_pdfmax(
                pdf_path=pdf, output_dir=out, pdfmax_dir=pdfmax,
            )
        assert isinstance(report, PdfMaxReport)
        assert report.markdown.startswith("# fake pdfMax report")


# ---------------------------------------------------------------------------
# cached_or_run_pdfmax
# ---------------------------------------------------------------------------


class TestCacheBehaviour:

    def test_cache_hit_skips_subprocess(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        _write_fake_pdf(pdf)
        cache = tmp_path / "cache"
        cache.mkdir()
        cached = cache / "doc_accessibility_report.md"
        cached.write_text("# cached content")
        # Push the cache mtime forward so it post-dates the source PDF.
        future = pdf.stat().st_mtime + 60
        os.utime(cached, (future, future))

        with patch("auto_a11y.pdf.pdfmax_runner.run_pdfmax") as mocked:
            report = cached_or_run_pdfmax(pdf_path=pdf, cache_dir=cache)
        mocked.assert_not_called()
        assert report.markdown == "# cached content"

    def test_cache_stale_triggers_rerun(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        _write_fake_pdf(pdf)
        cache = tmp_path / "cache"
        cache.mkdir()
        cached = cache / "doc_accessibility_report.md"
        cached.write_text("# stale content")
        # Pull the cache mtime backward to be older than the PDF.
        past = pdf.stat().st_mtime - 60
        os.utime(cached, (past, past))

        fresh = PdfMaxReport(markdown="# fresh content", output_dir=cache)
        with patch("auto_a11y.pdf.pdfmax_runner.run_pdfmax",
                   return_value=fresh) as mocked:
            report = cached_or_run_pdfmax(pdf_path=pdf, cache_dir=cache)
        mocked.assert_called_once()
        assert report.markdown == "# fresh content"

    def test_empty_cache_dir_runs(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        _write_fake_pdf(pdf)
        cache = tmp_path / "cache"  # Doesn't exist yet.

        fresh = PdfMaxReport(markdown="# from a real run", output_dir=cache)
        with patch("auto_a11y.pdf.pdfmax_runner.run_pdfmax",
                   return_value=fresh) as mocked:
            report = cached_or_run_pdfmax(pdf_path=pdf, cache_dir=cache)
        mocked.assert_called_once()
        assert report.markdown == "# from a real run"
