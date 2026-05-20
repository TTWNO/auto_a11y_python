"""tests/audio/ pytest configuration."""
from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="Update snapshot files instead of asserting against them",
    )
