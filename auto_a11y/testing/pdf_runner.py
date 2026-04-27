"""Async wrapper around the synchronous PDF audit pipeline.

Phase 8.1 of the pdfMax → auto_a11y integration. :class:`PdfRunner` owns
the lifecycle that surrounds :func:`auto_a11y.pdf.audit.pipeline.run_audit`:

* magic-byte / size validation and SHA-256 dedup on incoming bytes;
* atomic write to the on-disk :class:`~auto_a11y.pdf.storage.PdfStorage`
  layout;
* :class:`~auto_a11y.models.pdf_document.PdfDocumentStatus` transitions
  around every audit attempt (``PENDING/AUDITED → AUDITING →
  AUDITED/AUDIT_FAILED``);
* mapping the pipeline's ``CheckResult`` list to
  :class:`~auto_a11y.models.test_result.Violation` instances via
  :func:`~auto_a11y.pdf.translation.check_mapper.to_violation`;
* persistence of the resulting :class:`~auto_a11y.models.test_result.TestResult`.

The synchronous pipeline runs on a private :class:`ThreadPoolExecutor`
keyed to ``max_parallel`` so PDF audits don't starve the default executor
used by HTML test execution.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Literal

from bson import ObjectId

from auto_a11y.core.database import Database
from auto_a11y.models.pdf_document import (
    PdfDocument,
    PdfDocumentStatus,
    SourceType,
)
from auto_a11y.models.test_result import (
    ImpactLevel,
    TargetType,
    TestResult,
    Violation,
)
from auto_a11y.pdf.audit.pipeline import run_audit
from auto_a11y.pdf.errors import (
    CannotAuditFetchFailedDocument,
    CorruptPdf,
    GhostscriptMissing,
    NotAPdf,
    PdfDocumentNotFound,
    PdfTooLarge,
)
from auto_a11y.pdf.models import AuditResult, ProgressCallback
from auto_a11y.pdf.storage import PdfStorage
from auto_a11y.pdf.translation.check_mapper import to_violation


logger = logging.getLogger(__name__)


class PdfRunner:
    """Async wrapper around the sync :func:`run_audit` pipeline.

    Runs PDF audits in a dedicated thread-pool executor so they don't
    starve the default executor used by HTML test execution. Manages
    :class:`PdfDocument` status transitions and persists
    :class:`TestResult` records.

    Lifecycle:

    1. :meth:`create_or_find_pdf_document` accepts raw bytes, validates
       the magic header and size, computes the dedup key, and either
       returns the existing :class:`PdfDocument` or writes a new one to
       storage and the database with
       :data:`PdfDocumentStatus.PENDING`.
    2. :meth:`audit_pdf_document` flips the document to
       :data:`PdfDocumentStatus.AUDITING`, runs the pipeline on the
       executor, and on completion writes a :class:`TestResult`
       (``target_type=PDF_DOCUMENT``) and updates the document to
       :data:`PdfDocumentStatus.AUDITED`. Any exception flips the
       document to :data:`PdfDocumentStatus.AUDIT_FAILED` with
       ``error_reason`` set, and is then re-raised.
    """

    def __init__(
        self,
        database: Database,
        storage: PdfStorage,
        *,
        max_parallel: int = 2,
        max_size_mb: int = 100,
        ghostscript_path_override: str | None = None,
    ) -> None:
        self._db = database
        self._storage = storage
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._gs_override = ghostscript_path_override
        self._executor = ThreadPoolExecutor(
            max_workers=max_parallel,
            thread_name_prefix="pdf-audit",
        )

    def shutdown(self) -> None:
        """Shut down the executor. Idempotent."""
        self._executor.shutdown(wait=False)

    async def create_or_find_pdf_document(
        self,
        pdf_bytes: bytes,
        *,
        website_id: str,
        project_id: str,
        source_type: SourceType,
        discovered_from_page_id: str | None,
        discovered_from_user_id: str | None,
        original_filename: str,
        source_url: str | None,
    ) -> PdfDocument:
        """Dedupe-or-create a :class:`PdfDocument` from raw PDF bytes.

        1. Magic-byte verify (raises :class:`NotAPdf` otherwise).
        2. Size check (raises :class:`PdfTooLarge` if > configured max).
        3. SHA-256 + ``(website_id, sha256)`` dedup lookup.
        4. If hit: return existing.
        5. If miss: pre-allocate an :class:`~bson.ObjectId`, allocate the
           storage slot, atomically write the bytes, and insert the
           :class:`PdfDocument` with the same id so the on-disk layout
           and the Mongo ``_id`` agree.
        """
        if not pdf_bytes.startswith(b"%PDF-"):
            raise NotAPdf("Bytes do not start with %PDF- magic header")
        if len(pdf_bytes) > self._max_size_bytes:
            raise PdfTooLarge(len(pdf_bytes), self._max_size_bytes)

        sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        existing = self._db.find_pdf_document_by_sha256(website_id, sha256)
        if existing is not None:
            return existing

        # Pre-allocate the ObjectId so the storage layout (which embeds
        # the doc id in its path) matches the Mongo `_id` from the start.
        doc_oid = ObjectId()
        doc_id = str(doc_oid)
        slot = self._storage.allocate_pdf(
            website_id=website_id, pdf_document_id=doc_id
        )
        self._storage.write_pdf_bytes(slot, pdf_bytes)

        doc = PdfDocument(
            website_id=website_id,
            project_id=project_id,
            source_url=source_url,
            source_type=source_type,
            discovered_from_page_id=discovered_from_page_id,
            discovered_from_user_id=discovered_from_user_id,
            sha256=sha256,
            file_size_bytes=len(pdf_bytes),
            storage_relpath=f"{website_id}/{doc_id}/pdf.pdf",
            images_relpath=f"{website_id}/{doc_id}/images/",
            original_filename=original_filename,
            pdf_version=None,
            page_count=None,
            declared_lang=None,
            detected_lang=None,
            lang_confidence=None,
            status=PdfDocumentStatus.PENDING,
            error_reason=None,
            last_audit_result_id=None,
            discovered_at=datetime.now(),
            last_audited_at=None,
        )
        # Reserve the id on the in-memory doc so create_pdf_document honours it.
        doc.mongo_id = doc_oid
        self._db.create_pdf_document(doc)
        return doc

    async def audit_pdf_document(
        self,
        pdf_document_id: str,
        *,
        run_ai: bool = False,
        ai_api_key: str | None = None,
        wcag_level: Literal["AA", "AAA"] = "AA",
        locale: str = "en",
        progress_cb: ProgressCallback | None = None,
    ) -> TestResult:
        """Run an audit on an existing :class:`PdfDocument` and persist a :class:`TestResult`.

        Status transitions:

        * ``PENDING`` / ``AUDITED`` / ``AUDIT_FAILED`` → ``AUDITING``
          immediately before :func:`run_audit`.
        * ``AUDITING`` → ``AUDITED`` on success, with the document's
          PDF metadata back-filled from the :class:`AuditResult`.
        * ``AUDITING`` → ``AUDIT_FAILED`` on any exception, with
          ``error_reason`` populated.

        :data:`PdfDocumentStatus.FETCH_FAILED` documents cannot be
        audited (raises :class:`CannotAuditFetchFailedDocument`) — the
        bytes never reached storage, so there's nothing to audit.

        Returns the persisted :class:`TestResult`. The pipeline's
        ``check_results`` are mapped to violations via
        :func:`to_violation` and routed by :class:`ImpactLevel`:

        * ``HIGH`` → ``test_result.violations``
        * ``MEDIUM`` → ``test_result.warnings``
        * ``LOW`` → ``test_result.info``

        Raises:
            PdfDocumentNotFound: ``pdf_document_id`` doesn't exist.
            CannotAuditFetchFailedDocument: doc is in ``FETCH_FAILED``.
            GhostscriptMissing: bubbled up from :func:`run_audit`.
            CorruptPdf: bubbled up from :func:`run_audit`.
        """
        doc = self._db.get_pdf_document(pdf_document_id)
        if doc is None:
            raise PdfDocumentNotFound(pdf_document_id)
        if doc.status == PdfDocumentStatus.FETCH_FAILED:
            raise CannotAuditFetchFailedDocument(
                f"PdfDocument {pdf_document_id} has status FETCH_FAILED"
                + " — refetch first"
            )

        # Transition to AUDITING.
        doc.status = PdfDocumentStatus.AUDITING
        doc.error_reason = None
        self._db.update_pdf_document(doc)

        pdf_path = self._storage.local_path(doc)
        images_dir = self._storage.images_dir_for(doc)
        loop = asyncio.get_running_loop()

        started_at = datetime.now()
        try:
            audit_result = await loop.run_in_executor(
                self._executor,
                _run_audit_call(
                    pdf_path=pdf_path,
                    wcag_level=wcag_level,
                    run_ai=run_ai,
                    ai_api_key=ai_api_key,
                    locale=locale,
                    images_out_dir=images_dir,
                    progress_cb=progress_cb,
                    gs_override=self._gs_override,
                ),
            )
        except GhostscriptMissing:
            doc.status = PdfDocumentStatus.AUDIT_FAILED
            doc.error_reason = "Ghostscript not installed"
            self._db.update_pdf_document(doc)
            raise
        except CorruptPdf as exc:
            doc.status = PdfDocumentStatus.AUDIT_FAILED
            doc.error_reason = f"Corrupt PDF: {exc}"
            self._db.update_pdf_document(doc)
            raise
        except Exception as exc:  # noqa: BLE001 — we re-raise after persisting state
            doc.status = PdfDocumentStatus.AUDIT_FAILED
            doc.error_reason = f"{type(exc).__name__}: {exc}"
            self._db.update_pdf_document(doc)
            raise

        duration_ms = max(
            int((datetime.now() - started_at).total_seconds() * 1000), 0
        )
        test_result = self._build_test_result(
            doc=doc,
            audit_result=audit_result,
            duration_ms=duration_ms,
            wcag_level=wcag_level,
            locale=locale,
        )
        result_id = self._db.create_test_result(test_result)

        # Update doc post-audit.
        doc.status = PdfDocumentStatus.AUDITED
        doc.error_reason = None
        doc.last_audit_result_id = result_id
        doc.last_audited_at = datetime.now()
        doc.pdf_version = audit_result.pdf_version
        doc.page_count = audit_result.page_count
        # Preserve the document's declared language if the pipeline didn't
        # surface one (it currently always does, but be defensive).
        if audit_result.declared_lang is not None:
            doc.declared_lang = audit_result.declared_lang
        if audit_result.detected_lang is not None:
            doc.detected_lang = audit_result.detected_lang
        self._db.update_pdf_document(doc)

        return test_result

    @staticmethod
    def _build_test_result(
        *,
        doc: PdfDocument,
        audit_result: AuditResult,
        duration_ms: int,
        wcag_level: Literal["AA", "AAA"],
        locale: str,
    ) -> TestResult:
        """Construct a :class:`TestResult` from a finished audit.

        Routes mapped :class:`Violation` instances into
        ``violations`` / ``warnings`` / ``info`` by impact level —
        ``HIGH``, ``MEDIUM``, ``LOW`` respectively. Catalogue-unknown
        check results would have raised inside :func:`to_violation`; if
        we get here, every check has a mapping.
        """
        violations: list[Violation] = []
        warnings: list[Violation] = []
        info: list[Violation] = []
        pdf_doc_id = doc.id or ""
        for cr in audit_result.check_results:
            v = to_violation(cr, pdf_doc_id=pdf_doc_id)
            if v is None:
                continue
            # ImpactLevel is set inside to_violation: HIGH for FAIL,
            # MEDIUM for WARN, LOW for INFO (modulo per-row overrides).
            if v.impact == ImpactLevel.HIGH:
                violations.append(v)
            elif v.impact == ImpactLevel.MEDIUM:
                warnings.append(v)
            else:
                info.append(v)

        metadata: dict[str, object] = {
            'pdf_document_id': pdf_doc_id,
            'pdf_version': audit_result.pdf_version,
            'page_count': audit_result.page_count,
            'declared_lang': audit_result.declared_lang,
            'detected_lang': audit_result.detected_lang,
            'fail_count': audit_result.fail_count,
            'warn_count': audit_result.warn_count,
            'info_count': audit_result.info_count,
            'pass_count': audit_result.pass_count,
            'wcag_level': wcag_level,
            'locale': locale,
        }

        return TestResult(
            page_id=None,
            website_id=doc.website_id,
            target_type=TargetType.PDF_DOCUMENT,
            target_id=pdf_doc_id,
            test_date=datetime.now(),
            duration_ms=duration_ms,
            violations=violations,
            warnings=warnings,
            info=info,
            discovery=[],
            passes=[],
            ai_findings=[],
            screenshot_path=None,
            metadata=metadata,
        )


def _run_audit_call(
    *,
    pdf_path: Path,
    wcag_level: Literal["AA", "AAA"],
    run_ai: bool,
    ai_api_key: str | None,
    locale: str,
    images_out_dir: Path,
    progress_cb: ProgressCallback | None,
    gs_override: str | None,
) -> Callable[[], AuditResult]:
    """Build a zero-arg callable for :meth:`asyncio.AbstractEventLoop.run_in_executor`.

    Pulled out as a tiny factory so the call site stays type-clean (no
    inline ``lambda`` whose return type pyright/mypy struggle to
    propagate from the keyword-rich :func:`run_audit` signature) and so
    the test suite can target the ``run_audit`` symbol via
    :mod:`unittest.mock` without intercepting an inline lambda.
    """
    def _call() -> AuditResult:
        return run_audit(
            pdf_path,
            wcag_level=wcag_level,
            run_ai=run_ai,
            ai_api_key=ai_api_key,
            locale=locale,
            images_out_dir=images_out_dir,
            progress=progress_cb,
            gs_path_override=gs_override,
        )

    return _call


__all__ = ["PdfRunner"]
