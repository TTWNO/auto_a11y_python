"""PDF audit engine.

Re-exports the public :func:`run_audit` orchestrator so callers can write
``from auto_a11y.pdf.audit import run_audit`` without reaching into the
implementation module.

The re-export is lazy, and that matters. Importing it eagerly meant that
importing *any* module in this package — ``audit.colors``,
``audit.structure`` — ran the whole pipeline import chain, which reaches
``auto_a11y.pdf.models``, which imports back into this package. The cycle
resolved only when something happened to import the pipeline first, so
``import auto_a11y.pdf.models`` failed on its own while the application
and the test suite worked. Deferring the import to first attribute access
(PEP 562) breaks the loop without changing how callers import it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_a11y.pdf.audit.pipeline import run_audit

__all__ = ["run_audit"]


def __getattr__(name: str) -> object:
    """Resolve :func:`run_audit` on first access."""
    if name == "run_audit":
        from auto_a11y.pdf.audit.pipeline import run_audit as _run_audit

        return _run_audit
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
