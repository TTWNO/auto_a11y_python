"""Minimal stub for wcag-contrast-ratio 0.9.

Upstream package ships no py.typed and there is no types-* on PyPI.
Covers the three public functions consumed by pdfMax and the
auto_a11y port: ``rgb``, ``passes_AA``, and ``passes_AAA``.

Signatures reflect the real library's runtime behaviour:
``rgb`` accepts any 3-element tuple of floats in [0.0, 1.0]; we type
the parameters as ``tuple[float, ...]`` rather than
``tuple[float, float, float]`` so that generator-expression-built
tuples (``tuple(clamp(c) for c in colour)``) type-check at call sites
without forcing a local refactor. The runtime raises ``ValueError``
on a length mismatch, matching what a narrowed static type would catch.
"""

def rgb(
    rgb1: tuple[float, ...],
    rgb2: tuple[float, ...],
) -> float:
    """Return the WCAG 2.x contrast ratio between two sRGB colours.

    Each colour is a ``(r, g, b)`` tuple of floats in [0.0, 1.0].
    Raises ``ValueError`` on out-of-range values or wrong length.
    """
    ...

def passes_AA(contrast: float, large: bool = ...) -> bool:
    """Return True iff the ratio meets WCAG 2.x AA (SC 1.4.3).

    ``large=True`` uses the 3.0 threshold for large text;
    ``large=False`` (the default) uses the 4.5 threshold for normal text.
    """
    ...

def passes_AAA(contrast: float, large: bool = ...) -> bool:
    """Return True iff the ratio meets WCAG 2.x AAA (SC 1.4.6).

    ``large=True`` uses the 4.5 threshold for large text;
    ``large=False`` (the default) uses the 7.0 threshold for normal text.
    """
    ...
