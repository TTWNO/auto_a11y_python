"""Filesystem storage for PDF artefacts."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from auto_a11y.models.pdf_document import PdfDocument


@dataclass(frozen=True)
class AllocatedSlot:
    """Allocation result: where to write the PDF + images."""

    pdf_path: Path
    images_dir: Path


class PdfStorage:
    """Filesystem-backed PDF storage.

    Layout: <base_dir>/<website_id>/<pdf_document_id>/pdf.pdf
            <base_dir>/<website_id>/<pdf_document_id>/images/

    Atomicity: write-temp-then-rename with fsync'd parent directory.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def allocate_pdf(self, *, website_id: str, pdf_document_id: str) -> AllocatedSlot:
        parent = self.base_dir / website_id / pdf_document_id
        parent.mkdir(parents=True, exist_ok=True)
        images_dir = parent / "images"
        images_dir.mkdir(exist_ok=True)
        return AllocatedSlot(pdf_path=parent / "pdf.pdf", images_dir=images_dir)

    def write_pdf_bytes(self, slot: AllocatedSlot, data: bytes) -> None:
        tmp_path = slot.pdf_path.with_suffix('.pdf.tmp')
        with open(tmp_path, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, slot.pdf_path)
        # Best-effort fsync of the parent directory for rename durability;
        # Windows and some other platforms disallow directory fsync, so swallow OSError.
        try:
            dir_fd = os.open(slot.pdf_path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    def local_path(self, doc: PdfDocument) -> Path:
        return self.base_dir / doc.storage_relpath

    def images_dir_for(self, doc: PdfDocument) -> Path:
        return self.base_dir / doc.images_relpath

    def delete(self, doc: PdfDocument) -> None:
        doc_dir = self.local_path(doc).parent
        if doc_dir.is_dir():
            shutil.rmtree(doc_dir, ignore_errors=True)

    def delete_audit_cache(self, doc: PdfDocument) -> None:
        """Remove the cached pdfMax outputs for one PDF.

        The audit job writes ``*_issue_map.json`` and
        ``*_accessibility_report.md`` into ``<pdf_dir>/pdfmax-report/``.
        Both the website-detail rollup
        (:func:`auto_a11y.pdf.issue_map_counts.count_issues`) and the
        ``/pdfs/<id>/pdfmax-report`` and ``/issue-map`` viewer routes
        read directly from this directory, so a stale cache survives a
        DB-only reset and the user keeps seeing old issues. The PDF
        bytes and images are kept; only the audit outputs are removed.
        """
        cache_dir = self.local_path(doc).parent / "pdfmax-report"
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)

    def delete_website(self, website_id: str) -> None:
        website_dir = self.base_dir / website_id
        if website_dir.is_dir():
            shutil.rmtree(website_dir, ignore_errors=True)
