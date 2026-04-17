"""Minimal stub for PyPDF2 — only symbols used by auto_a11y.core.scraper."""

from io import BytesIO

class PdfReader:
    metadata: dict[str, str] | None
    pages: list[_Page]
    def __init__(self, stream: BytesIO | str) -> None: ...

class _Page:
    def extract_text(self) -> str: ...
