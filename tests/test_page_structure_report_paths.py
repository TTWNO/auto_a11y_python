"""Tests for reports-directory resolution in the page-structure report.

Covers the fallback path when REPORTS_DIR is unset and no Flask app
context is available: the resolved directory must be a repo-relative
``reports`` path, never a foreign developer-specific absolute path.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# Load the web app package before importing ``auto_a11y.reporting`` so the
# module-load order matches the running application. ``auto_a11y.reporting``
# imports ``auto_a11y.web.fluent``, which (via ``auto_a11y.web.__init__``)
# pulls in the route blueprints; loading ``web.app`` up front establishes that
# ordering and avoids a partial-init circular import when ``reporting`` is the
# first thing imported.
importlib.import_module("auto_a11y.web.app")

from auto_a11y.reporting.page_structure_report import resolve_reports_dir


def test_fallback_is_repo_relative_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """With REPORTS_DIR unset and no Flask app context, fall back to ``reports``."""
    monkeypatch.delenv("REPORTS_DIR", raising=False)

    resolved = resolve_reports_dir()

    assert isinstance(resolved, Path)
    # Must be the repo-relative default, not a foreign absolute path.
    assert resolved == Path("reports")
    assert not resolved.is_absolute()
    assert "bob3" not in str(resolved)


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """REPORTS_DIR, when set, takes precedence over the default."""
    monkeypatch.setenv("REPORTS_DIR", "/tmp/custom_reports")

    resolved = resolve_reports_dir()

    assert resolved == Path("/tmp/custom_reports")
