"""Tests for PDF-specific Config entries."""
from __future__ import annotations

from config import Config


def test_config_exposes_pdf_defaults() -> None:
    cfg = Config()
    assert cfg.PDF_STORAGE_DIR == 'data/pdfs'
    assert cfg.PDF_MAX_SIZE_MB == 100
    assert cfg.PDF_DOWNLOAD_TIMEOUT_SECONDS == 60
    assert cfg.PDF_AUDIT_MAX_PARALLEL == 2
    assert cfg.GHOSTSCRIPT_PATH is None
