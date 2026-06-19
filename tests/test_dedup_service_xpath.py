"""Tests for xpath extraction in the deduplication service.

Regression test for a bug where dict-shaped violations carrying an xpath
(top-level or under ``metadata``) were never matched because the lookup used
``getattr`` exclusively, which returns ``None`` for dicts.
"""
from __future__ import annotations

from types import SimpleNamespace

import importlib

# Importing the web app first resolves a pre-existing circular import between
# auto_a11y.reporting.__init__ and auto_a11y.web.routes that otherwise fires
# when deduplication_service is the first module imported.
importlib.import_module("auto_a11y.web.app")
from auto_a11y.reporting.deduplication_service import (
    extract_violation_xpath,
)


def test_get_violation_xpath_dict_top_level() -> None:
    """A dict violation with a top-level xpath is found."""
    violation = {"xpath": "/html/body/div[1]"}
    assert (
        extract_violation_xpath(violation)
        == "/html/body/div[1]"
    )


def test_get_violation_xpath_dict_under_metadata() -> None:
    """A dict violation with xpath under metadata is found."""
    violation = {"metadata": {"xpath": "/html/body/div[2]"}}
    assert (
        extract_violation_xpath(violation)
        == "/html/body/div[2]"
    )


def test_get_violation_xpath_dict_top_level_preferred() -> None:
    """Top-level xpath takes precedence over metadata xpath."""
    violation = {"xpath": "/top", "metadata": {"xpath": "/meta"}}
    assert (
        extract_violation_xpath(violation) == "/top"
    )


def test_get_violation_xpath_dict_missing() -> None:
    """A dict violation with no xpath returns None."""
    assert extract_violation_xpath({}) is None


def test_get_violation_xpath_object_top_level() -> None:
    """An object violation with an xpath attribute is found (unchanged behavior)."""
    violation = SimpleNamespace(xpath="/html/body/span")
    assert (
        extract_violation_xpath(violation)
        == "/html/body/span"
    )


def test_get_violation_xpath_object_under_metadata() -> None:
    """An object violation with xpath under a metadata dict is found."""
    violation = SimpleNamespace(xpath=None, metadata={"xpath": "/html/body/a"})
    assert (
        extract_violation_xpath(violation)
        == "/html/body/a"
    )


def test_get_violation_xpath_object_missing() -> None:
    """An object violation with no xpath returns None."""
    violation = SimpleNamespace(code="X")
    assert extract_violation_xpath(violation) is None
