"""Tests for PDF-specific Config entries."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from config import BASE_DIR, Config


def test_config_exposes_pdf_defaults() -> None:
    cfg = Config()
    # PDF_STORAGE_DIR is anchored to BASE_DIR so it doesn't depend on cwd
    # (see commit f1efc27). Default suffix stays 'data/pdfs'.
    assert Path(cfg.PDF_STORAGE_DIR).is_absolute()
    assert Path(cfg.PDF_STORAGE_DIR) == BASE_DIR / 'data' / 'pdfs'
    assert cfg.PDF_MAX_SIZE_MB == 100
    assert cfg.PDF_DOWNLOAD_TIMEOUT_SECONDS == 60
    assert cfg.PDF_AUDIT_MAX_PARALLEL == 2


def test_pdf_storage_dir_absolute_env_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('PDF_STORAGE_DIR', '/var/lib/auto_a11y/pdfs')
    import config as config_mod
    importlib.reload(config_mod)
    try:
        cfg = config_mod.Config()
        assert cfg.PDF_STORAGE_DIR == '/var/lib/auto_a11y/pdfs'
    finally:
        monkeypatch.delenv('PDF_STORAGE_DIR', raising=False)
        importlib.reload(config_mod)


def test_pdf_storage_dir_relative_env_anchors_to_base_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('PDF_STORAGE_DIR', 'custom/pdfs')
    import config as config_mod
    importlib.reload(config_mod)
    try:
        cfg = config_mod.Config()
        assert Path(cfg.PDF_STORAGE_DIR) == BASE_DIR / 'custom' / 'pdfs'
    finally:
        monkeypatch.delenv('PDF_STORAGE_DIR', raising=False)
        importlib.reload(config_mod)
