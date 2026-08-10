"""In-repo PDF remediation engine.

Ported from pdfMax's ``pdf_fix.py``, which auto_a11y previously could not
reach at all: the fixer lived in a separate checkout driven by a
subprocess, so no auto_a11y build could correct a document it had just
found faults in.

The port keeps pdfMax's fix IDs — they are the contract the fix-selection
UI and the stored record of applied fixes both depend on — while replacing
its ``**kwargs`` input convention with the typed :class:`FixOptions`
record, so that which fixes need which inputs is declared rather than
discovered at runtime.
"""
from auto_a11y.pdf.fix.models import (
    FixOptions,
    FixResult,
    ListConversionGroup,
)
from auto_a11y.pdf.fix.registry import (
    FIX_REGISTRY,
    FixFn,
    get_fix,
    known_fix_ids,
)
from auto_a11y.pdf.fix.runner import (
    FixRun,
    ProgressCallback,
    UnknownFixError,
    apply_fixes,
    default_output_path,
)

__all__ = [
    "FIX_REGISTRY",
    "FixFn",
    "FixOptions",
    "FixResult",
    "FixRun",
    "ListConversionGroup",
    "ProgressCallback",
    "UnknownFixError",
    "apply_fixes",
    "default_output_path",
    "get_fix",
    "known_fix_ids",
]
