"""Minimal stub for wcag-contrast-ratio 0.9.

Upstream package ships no py.typed and there is no types-* on PyPI.
Covers only the public API (the ``__all__`` of the package) that
pdfMax and the auto_a11y port consume. Verified against
``.venv/lib/python*/site-packages/wcag_contrast_ratio/contrast.py``.

Note: the package's runtime ``rgb`` accepts any 3-element iterable
whose elements are within [0.0, 1.0]. pdfMax always passes a
``tuple[float, float, float]``, so that is the signature we expose.
"""

from typing import Final

__all__: Final[list[str]]

def rgb(
    rgb1: tuple[float, float, float],
    rgb2: tuple[float, float, float],
) -> float:
    """Return the WCAG 2.x contrast ratio between two sRGB colours.

    Each colour is a ``(r, g, b)`` tuple with each channel in [0.0, 1.0].
    Raises ``ValueError`` if any channel is outside that range.
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
