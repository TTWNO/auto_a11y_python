"""Tests for :func:`auto_a11y.core.pdf_audit_job.queue_audits_for_website`.

Called from the "Test All Documents" route to fan out one
:class:`PdfAuditJob` per auditable PDF on a website. Verifies:

* PDFs in :data:`PdfDocumentStatus.AUDITING` / ``FETCH_FAILED`` are skipped.
* PDFs in every other status are (re-)queued on each click.
* A failure to construct/start one job doesn't block the others.
* The returned list of job IDs matches the jobs successfully queued.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

# Same import-order dance as test_pdf_runner.py.
import auto_a11y.core as _core_preload
del _core_preload
from auto_a11y.core import pdf_audit_job as paj_module
from auto_a11y.core.pdf_audit_job import queue_audits_for_website
from auto_a11y.models.pdf_document import PdfDocumentStatus


def _make_pdf(
    pdf_id: str, status: PdfDocumentStatus = PdfDocumentStatus.PENDING
) -> Any:
    pdf = MagicMock()
    pdf.id = pdf_id
    pdf.status = status
    return pdf


@pytest.fixture
def stub_audit_job(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    job_class = MagicMock()
    counter = {'n': 0}

    def make_instance(*_args: Any, **_kwargs: Any) -> MagicMock:
        counter['n'] += 1
        inst = MagicMock()
        inst.start = MagicMock(return_value=f"job-id-{counter['n']}")
        return inst

    job_class.side_effect = make_instance
    monkeypatch.setattr(paj_module, "PdfAuditJob", job_class)
    return job_class


def test_queues_audits_for_all_eligible_statuses(
    stub_audit_job: MagicMock,
) -> None:
    """PENDING / AUDITED / AUDIT_FAILED all re-audit on test-all click."""
    db = MagicMock()
    db.get_pdf_documents.return_value = [
        _make_pdf("pdf-1", PdfDocumentStatus.PENDING),
        _make_pdf("pdf-2", PdfDocumentStatus.AUDITED),
        _make_pdf("pdf-3", PdfDocumentStatus.AUDIT_FAILED),
    ]
    runner = MagicMock()

    queued = queue_audits_for_website(
        runner=runner,
        db=db,
        website_id="wid-123",
        user_id="u-1",
    )

    assert queued == ["job-id-1", "job-id-2", "job-id-3"]
    assert stub_audit_job.call_count == 3
    pdf_ids = [
        c.kwargs["pdf_document_id"] for c in stub_audit_job.call_args_list
    ]
    assert pdf_ids == ["pdf-1", "pdf-2", "pdf-3"]


def test_skips_already_auditing_or_fetch_failed(
    stub_audit_job: MagicMock,
) -> None:
    db = MagicMock()
    db.get_pdf_documents.return_value = [
        _make_pdf("pdf-1", PdfDocumentStatus.AUDITING),
        _make_pdf("pdf-2", PdfDocumentStatus.FETCH_FAILED),
        _make_pdf("pdf-3", PdfDocumentStatus.AUDITED),
    ]

    queued = queue_audits_for_website(
        runner=MagicMock(),
        db=db,
        website_id="wid-123",
        user_id="u-1",
    )

    assert len(queued) == 1
    assert stub_audit_job.call_count == 1
    assert (
        stub_audit_job.call_args.kwargs["pdf_document_id"] == "pdf-3"
    )


def test_one_failure_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    db.get_pdf_documents.return_value = [
        _make_pdf("pdf-1", PdfDocumentStatus.PENDING),
        _make_pdf("pdf-2", PdfDocumentStatus.PENDING),
        _make_pdf("pdf-3", PdfDocumentStatus.PENDING),
    ]

    bad = MagicMock()
    bad.start.side_effect = RuntimeError("boom")
    good_a = MagicMock()
    good_a.start.return_value = "job-a"
    good_b = MagicMock()
    good_b.start.return_value = "job-b"

    job_class = MagicMock(side_effect=[good_a, bad, good_b])
    monkeypatch.setattr(paj_module, "PdfAuditJob", job_class)

    queued = queue_audits_for_website(
        runner=MagicMock(),
        db=db,
        website_id="wid-123",
        user_id="u-1",
    )

    assert queued == ["job-a", "job-b"]
    assert job_class.call_count == 3


def test_skips_pdfs_with_missing_id(stub_audit_job: MagicMock) -> None:
    db = MagicMock()
    nullified = _make_pdf("pdf-no-id", PdfDocumentStatus.PENDING)
    nullified.id = None
    db.get_pdf_documents.return_value = [
        nullified,
        _make_pdf("pdf-2", PdfDocumentStatus.PENDING),
    ]

    queued = queue_audits_for_website(
        runner=MagicMock(),
        db=db,
        website_id="wid-123",
        user_id="u-1",
    )

    assert len(queued) == 1
    assert stub_audit_job.call_args.kwargs["pdf_document_id"] == "pdf-2"
