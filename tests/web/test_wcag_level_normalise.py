"""Tests for the WCAG-level allow-list normaliser in the projects routes."""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import cast

os.environ.setdefault("RUN_AI_ANALYSIS", "false")

import pytest

import auto_a11y.web.routes.projects as projects_module

# Module-private helper; reached via getattr to keep pyright's
# reportPrivateUsage clean (same pattern as other audit tests).
_normalise: Callable[[str | None], str] = cast(
    "Callable[[str | None], str]",
    getattr(projects_module, "_normalise_wcag_level"),
)


@pytest.mark.parametrize("value", ["A", "AA", "AAA"])
def test_valid_levels_pass_through(value: str) -> None:
    assert _normalise(value) == value


@pytest.mark.parametrize(
    "value", ["aa", " aaa ", "Aaa"],
)
def test_valid_levels_normalised_case_and_whitespace(value: str) -> None:
    assert _normalise(value) == value.strip().upper()


@pytest.mark.parametrize(
    "value",
    ["", None, "AAAA", "B", "<script>", "AA;DROP", "2.1"],
)
def test_invalid_levels_default_to_aa(value: str | None) -> None:
    assert _normalise(value) == "AA"
