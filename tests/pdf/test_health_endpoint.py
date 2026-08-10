"""Tests for the pure-function PDF health check (auto_a11y.pdf.health)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from auto_a11y.pdf.health import (
    check_pdf_health,
)


def test_health_ok_when_renderer_and_storage_present(tmp_path: Path) -> None:
    health = check_pdf_health(storage_dir=tmp_path / "pdfs")
    # pypdfium2 is a pinned requirement, so the renderer is available in any
    # environment installed from requirements.txt; storage dir is writable.
    assert health.renderer.available is True
    assert health.renderer.engine == "pdfium"
    assert health.storage.writable is True
    assert health.ok is True
    assert (tmp_path / "pdfs").is_dir()


def test_health_503_when_renderer_unavailable(tmp_path: Path) -> None:
    # There is no binary to hide any more — the only way rasterisation can be
    # unavailable is a stripped environment where the wheel is not installed.
    with patch('auto_a11y.pdf.health.renderer_available', return_value=False):
        health = check_pdf_health(storage_dir=tmp_path / "pdfs")
    assert health.renderer.available is False
    assert health.ok is False


def test_health_503_when_storage_unwritable(tmp_path: Path) -> None:
    # Create a file at the path so .mkdir() raises and we cannot probe.
    blocker = tmp_path / "blocker"
    blocker.write_text("file not directory")
    health = check_pdf_health(storage_dir=blocker)
    assert health.storage.writable is False
    assert health.ok is False
