"""PDF audit engine.

Phase 5.3 of the pdfMax → auto_a11y integration. Re-exports the public
:func:`run_audit` orchestrator so callers can write
``from auto_a11y.pdf.audit import run_audit`` without reaching into the
implementation module.
"""
from __future__ import annotations

from auto_a11y.pdf.audit.pipeline import run_audit

__all__ = ["run_audit"]
