"""Phase 4 per-touchpoint check modules.

Each submodule of this package exposes:

* one function per individual check, with signature
  ``(ctx: AuditContext) -> list[CheckResult]``;
* a module-level registry list (e.g. ``HEADING_CHECKS``) listing those
  functions in the order the orchestrator should call them.

Phase 5.3 ``run_audit`` iterates every registry list and invokes each
check function with the populated :class:`auto_a11y.pdf.models.AuditContext`.
Phase 6's check catalogue iterates the same lists to enumerate all
known check names.

Submodules are *not* re-exported from this package marker — the
orchestrator imports the registry list it needs directly. That wiring
is added in a single dedicated commit after every Phase 4 module has
landed.
"""
