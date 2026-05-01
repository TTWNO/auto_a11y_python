"""Typed exceptions for the PDF audit subsystem."""
from __future__ import annotations


class PdfError(Exception):
    """Base class for PDF subsystem errors."""


class GhostscriptMissing(PdfError):
    """Ghostscript binary could not be located."""

    def __init__(self, searched: list[str]) -> None:
        self.searched = searched
        super().__init__(
            f"Ghostscript not found on PATH (searched: {', '.join(searched)}). "
            + "Install Ghostscript or set GHOSTSCRIPT_PATH in config."
        )


class CorruptPdf(PdfError):
    """pikepdf could not open the PDF."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Cannot open PDF at {path}: {reason}")


class FetchFailed(PdfError):
    """Downloading the PDF from a URL failed."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"Failed to fetch {url}: {reason}")


class PdfTooLarge(PdfError):
    """PDF exceeds configured maximum size."""

    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"PDF size {size_bytes} bytes exceeds limit {limit_bytes} bytes."
        )


class NotAPdf(PdfError):
    """Bytes don't start with the %PDF- magic header."""


class PdfDocumentNotFound(PdfError):
    """No PdfDocument exists with the given id."""

    def __init__(self, pdf_document_id: str) -> None:
        self.pdf_document_id = pdf_document_id
        super().__init__(f"PdfDocument not found: {pdf_document_id}")


class CannotAuditFetchFailedDocument(PdfError):
    """Attempted to audit a document whose initial fetch failed."""
