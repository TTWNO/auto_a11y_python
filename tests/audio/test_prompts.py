"""Snapshot tests for prompt files.

Each prompt is asserted to end with the ``END_OF_PROMPT`` marker and
its SHA256 must match the hash committed in ``snapshots/prompts.json``.
This catches accidental edits during dependency upgrades or refactors.

Regenerate snapshots with::

    pytest --snapshot-update tests/audio/test_prompts.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


_PROMPT_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "auto_a11y"
    / "audio"
    / "prompts"
)
_SNAPSHOTS = Path(__file__).resolve().parent / "snapshots" / "prompts.json"


# 3 context-specific issues prompts + 3 shared (painpoints/takeaways/
# assertions) + heuristics. Painpoints/takeaways/assertions are
# context-independent in the source pipeline; see
# auto_a11y/audio/prompts/__init__.py for the loader logic.
ISSUES_PROMPTS = (
    "audit_issues.txt",
    "livedExperience_issues.txt",
    "navilens_issues.txt",
)
SHARED_PROMPTS = ("painpoints.txt", "takeaways.txt", "assertions.txt")
ALL_PROMPT_FILES = ISSUES_PROMPTS + SHARED_PROMPTS + ("heuristics.txt",)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("filename", ALL_PROMPT_FILES)
def test_prompt_ends_with_marker(filename: str) -> None:
    text = (_PROMPT_DIR / filename).read_text().rstrip()
    assert text.endswith("END_OF_PROMPT"), f"{filename} missing END_OF_PROMPT"


def test_prompt_hashes_match_snapshots(request: pytest.FixtureRequest) -> None:
    """Every prompt's sha256 matches the value in snapshots/prompts.json.

    Run with --snapshot-update to refresh after intentional edits.
    """
    expected: dict[str, str] = (
        json.loads(_SNAPSHOTS.read_text()) if _SNAPSHOTS.exists() else {}
    )
    actual: dict[str, str] = {
        name: _sha256(_PROMPT_DIR / name) for name in ALL_PROMPT_FILES
    }

    if request.config.getoption("--snapshot-update", default=False):
        _SNAPSHOTS.parent.mkdir(parents=True, exist_ok=True)
        _SNAPSHOTS.write_text(json.dumps(actual, indent=2, sort_keys=True))
        return
    assert actual == expected, (
        "prompt hashes drifted; rerun with --snapshot-update if intentional"
    )
