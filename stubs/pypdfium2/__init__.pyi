"""Minimal typed surface for pypdfium2.

The wheel ships no py.typed, and only the page-rasterisation path in
auto_a11y/pdf/audit/rasterize.py uses it, so this declares just that: open a
document, index a page, render it, hand the bitmap to Pillow. No Any.
"""

from types import TracebackType
from PIL.Image import Image

class PdfBitmap:
    def to_pil(self) -> Image: ...

class PdfPage:
    def render(self, *, scale: float = ...) -> PdfBitmap: ...

class PdfDocument:
    def __init__(self, input: str) -> None: ...
    def __len__(self) -> int: ...
    def __getitem__(self, index: int) -> PdfPage: ...
    def __enter__(self) -> PdfDocument: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def close(self) -> None: ...
