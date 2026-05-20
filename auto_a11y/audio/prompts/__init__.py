"""Prompt-file loader.

Pure read; no template engine; prompts ARE the input to Claude. The
loader maps (context, kind) to a file under ``auto_a11y/audio/prompts/``.

Painpoints / takeaways / assertions are context-independent in the
source pipeline, so they live in a single shared file each. Only the
``issues`` kind varies by audit context.

French output is produced at runtime by prepending a French-output
instruction to the prompt body (handled in
``auto_a11y/audio/analysis.py``); the prompt files themselves are
English-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

_PROMPT_DIR = Path(__file__).resolve().parent


Context = Literal["audit", "livedExperience", "navilens"]
Kind = Literal["issues", "painpoints", "takeaways", "assertions"]


def load(context: Context, kind: Kind) -> str:
    """Load the prompt for (context, kind).

    Issues prompts vary by context. Painpoints / takeaways / assertions
    are context-independent.
    """
    if kind == "issues":
        path = _PROMPT_DIR / f"{context}_issues.txt"
    else:
        path = _PROMPT_DIR / f"{kind}.txt"
    return path.read_text()


def load_heuristics() -> str:
    """Load the shared accessibility-testing heuristics block."""
    return (_PROMPT_DIR / "heuristics.txt").read_text()
