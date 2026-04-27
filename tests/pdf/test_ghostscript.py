"""Tests for Ghostscript detection + rendering."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from auto_a11y.pdf.audit.ghostscript import (
    detect_ghostscript,
    invalidate_detection_cache,
)
from auto_a11y.pdf.errors import GhostscriptMissing


def test_detect_ghostscript_finds_installed_binary() -> None:
    invalidate_detection_cache()
    path = detect_ghostscript()
    assert path is not None
    assert Path(path).name.startswith(('gs', 'gswin'))


def test_detect_ghostscript_raises_when_missing() -> None:
    invalidate_detection_cache()
    with patch('auto_a11y.pdf.audit.ghostscript.shutil.which', return_value=None):
        with pytest.raises(GhostscriptMissing):
            detect_ghostscript(raise_if_missing=True)


def test_detect_ghostscript_returns_none_when_missing_without_raise() -> None:
    invalidate_detection_cache()
    with patch('auto_a11y.pdf.audit.ghostscript.shutil.which', return_value=None):
        assert detect_ghostscript(raise_if_missing=False) is None


def test_detect_ghostscript_honours_config_override(tmp_path: Path) -> None:
    invalidate_detection_cache()
    fake_gs = tmp_path / "gs"
    fake_gs.write_text("#!/bin/sh\necho fake\n")
    fake_gs.chmod(0o755)
    path = detect_ghostscript(override=str(fake_gs))
    assert path == str(fake_gs)
