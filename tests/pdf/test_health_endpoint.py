"""Tests for the pure-function PDF health check (auto_a11y.pdf.health)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from auto_a11y.pdf.health import (
    check_pdf_health,
)


def test_health_ok_when_gs_and_storage_present(tmp_path: Path) -> None:
    health = check_pdf_health(gs_override=None, storage_dir=tmp_path / "pdfs")
    # Real /usr/bin/gs is present in this env; storage dir is writable
    assert health.ghostscript.found is True
    assert health.ghostscript.path is not None
    assert health.storage.writable is True
    assert health.ok is True
    assert (tmp_path / "pdfs").is_dir()


def test_health_503_when_gs_missing(tmp_path: Path) -> None:
    from auto_a11y.pdf.audit.ghostscript import invalidate_detection_cache
    invalidate_detection_cache()
    with patch('auto_a11y.pdf.audit.ghostscript.shutil.which', return_value=None):
        health = check_pdf_health(gs_override=None, storage_dir=tmp_path / "pdfs")
    invalidate_detection_cache()  # clean up so other tests aren't affected
    assert health.ghostscript.found is False
    assert health.ghostscript.path is None
    assert health.ok is False


def test_health_503_when_storage_unwritable(tmp_path: Path) -> None:
    # Create a file at the path so .mkdir() raises and we cannot probe.
    blocker = tmp_path / "blocker"
    blocker.write_text("file not directory")
    health = check_pdf_health(gs_override=None, storage_dir=blocker)
    assert health.storage.writable is False
    assert health.ok is False


def test_health_uses_override_path(tmp_path: Path) -> None:
    fake_gs = tmp_path / "gs"
    fake_gs.write_text("#!/bin/sh\necho fake\n")
    fake_gs.chmod(0o755)
    health = check_pdf_health(gs_override=str(fake_gs), storage_dir=tmp_path / "pdfs")
    assert health.ghostscript.path == str(fake_gs)
    assert health.ghostscript.found is True
