"""Tests for :class:`auto_a11y.core.pdf_audit_job.PdfAuditJob`.

The job class wraps a synchronous ``asyncio.run`` of
:meth:`PdfRunner.audit_pdf_document` so the audit doesn't block the
Flask request thread. These tests exercise:

* ``start()`` creates a :class:`JobManager` record with
  :data:`JobType.PDF_AUDIT` and submits to :data:`task_runner`.
* The worker (``_run_audit_in_thread``) flips the job to RUNNING then
  COMPLETED on a successful audit and records ``test_result_id`` in
  the metadata + result.
* On exception the worker flips to FAILED with a typed error message.
* The progress bridge (``_on_progress``) projects the
  ``(stage, fraction)`` PDF callback signature onto the
  :meth:`JobManager.update_job_progress` ``current/total`` shape.

These tests poke at private attributes by name via
``setattr``/``getattr`` (rather than accessing them with a leading
underscore) so the strict checkers don't complain about
``reportPrivateUsage`` — the underscores are only documentation, this
is direct unit-testing of the implementation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

import auto_a11y.core as _core_preload
del _core_preload

from auto_a11y.core.job_manager import JobStatus, JobType
from auto_a11y.core.pdf_audit_job import PdfAuditJob
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus


def _set_jm(job: PdfAuditJob, fake_jm: MagicMock) -> None:
    """Inject a stub :class:`JobManager` onto a :class:`PdfAuditJob`."""
    setattr(job, '_job_manager', fake_jm)


def _run_worker(job: PdfAuditJob) -> None:
    """Invoke the (private) worker entry point under test."""
    worker: Any = getattr(job, '_run_audit_in_thread')
    worker()


def _emit_progress(job: PdfAuditJob, stage: str, fraction: float) -> None:
    """Invoke the (private) progress bridge under test."""
    cb: Any = getattr(job, '_on_progress')
    cb(stage, fraction)


def _make_doc(
    *,
    last_audit_result_id: str | None = None,
) -> PdfDocument:
    oid = ObjectId()
    doc = PdfDocument(
        website_id="w-1",
        project_id="p-1",
        source_url=None,
        source_type="uploaded",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256="a" * 64,
        file_size_bytes=10,
        storage_relpath=f"w-1/{oid}/pdf.pdf",
        images_relpath=f"w-1/{oid}/images/",
        original_filename="doc.pdf",
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=PdfDocumentStatus.AUDITED,
        error_reason=None,
        last_audit_result_id=last_audit_result_id,
        discovered_at=datetime.now(),
        last_audited_at=None,
    )
    doc.mongo_id = oid
    return doc


@pytest.fixture
def mock_db_with_doc() -> MagicMock:
    db = MagicMock()
    db.get_pdf_document.return_value = _make_doc()
    return db


@pytest.fixture
def mock_runner() -> MagicMock:
    return MagicMock()


def _make_job(
    *,
    runner: MagicMock,
    db: MagicMock,
    pdf_id: str = "pdf-1",
) -> PdfAuditJob:
    return PdfAuditJob(
        runner=runner,
        db=db,
        pdf_document_id=pdf_id,
        run_ai=False,
        ai_api_key=None,
        wcag_level='AA',
        locale='en',
        user_id='user-1',
    )


def test_start_creates_job_in_db_and_submits_to_task_runner(
    mock_runner: MagicMock, mock_db_with_doc: MagicMock
) -> None:
    """``start()`` calls ``JobManager.create_job`` with PDF_AUDIT and
    hands the work to ``task_runner.submit_task`` keyed by job_id."""
    job = _make_job(runner=mock_runner, db=mock_db_with_doc)

    fake_jm = MagicMock()
    _set_jm(job, fake_jm)
    with patch(
        'auto_a11y.core.pdf_audit_job.task_runner'
    ) as fake_task_runner:
        returned_id = job.start()

    assert returned_id == job.job_id
    fake_jm.create_job.assert_called_once()
    create_kwargs: dict[str, Any] = fake_jm.create_job.call_args.kwargs
    assert create_kwargs['job_type'] is JobType.PDF_AUDIT
    assert create_kwargs['project_id'] == 'p-1'
    assert create_kwargs['website_id'] == 'w-1'
    assert create_kwargs['user_id'] == 'user-1'
    assert create_kwargs['metadata']['pdf_document_id'] == 'pdf-1'

    fake_task_runner.submit_task.assert_called_once()
    task_kwargs: dict[str, Any] = fake_task_runner.submit_task.call_args.kwargs
    assert task_kwargs['task_id'] == job.job_id
    # The function passed in is the bound _run_audit_in_thread.
    assert task_kwargs['func'].__name__ == '_run_audit_in_thread'


def test_run_audit_in_thread_updates_status_to_running_then_completed(
    mock_runner: MagicMock, mock_db_with_doc: MagicMock
) -> None:
    """Successful audit flips RUNNING → COMPLETED via JobManager."""
    job = _make_job(runner=mock_runner, db=mock_db_with_doc)
    fake_jm = MagicMock()
    _set_jm(job, fake_jm)

    fake_test_result = MagicMock()
    fake_test_result.id = 'tr-99'
    mock_db_with_doc.get_pdf_document.return_value = _make_doc(
        last_audit_result_id='tr-99'
    )

    async def _fake_audit(*_args: Any, **_kwargs: Any) -> Any:
        return fake_test_result

    mock_runner.audit_pdf_document = _fake_audit

    _run_worker(job)

    statuses = [
        c.kwargs['status']
        for c in fake_jm.update_job_status.call_args_list
    ]
    assert JobStatus.RUNNING in statuses
    assert JobStatus.COMPLETED in statuses
    # COMPLETED was the last status.
    assert statuses[-1] is JobStatus.COMPLETED


def test_run_audit_in_thread_marks_failed_on_exception(
    mock_runner: MagicMock, mock_db_with_doc: MagicMock
) -> None:
    """Any audit exception → JobStatus.FAILED with a typed error string."""
    job = _make_job(runner=mock_runner, db=mock_db_with_doc)
    fake_jm = MagicMock()
    _set_jm(job, fake_jm)

    async def _fail(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("boom")

    mock_runner.audit_pdf_document = _fail

    _run_worker(job)

    failed_calls = [
        c for c in fake_jm.update_job_status.call_args_list
        if c.kwargs.get('status') is JobStatus.FAILED
    ]
    assert len(failed_calls) == 1
    assert 'RuntimeError' in failed_calls[0].kwargs['error']
    assert 'boom' in failed_calls[0].kwargs['error']


def test_run_audit_in_thread_records_test_result_id_on_success(
    mock_runner: MagicMock, mock_db_with_doc: MagicMock
) -> None:
    """The COMPLETED job record carries the persisted test_result_id."""
    job = _make_job(runner=mock_runner, db=mock_db_with_doc)
    fake_jm = MagicMock()
    _set_jm(job, fake_jm)

    # The worker re-reads the document after the audit to get the
    # last_audit_result_id that PdfRunner sets there.
    mock_db_with_doc.get_pdf_document.return_value = _make_doc(
        last_audit_result_id='tr-99'
    )

    async def _ok(*_args: Any, **_kwargs: Any) -> Any:
        return MagicMock()

    mock_runner.audit_pdf_document = _ok

    _run_worker(job)

    completed = [
        c for c in fake_jm.update_job_status.call_args_list
        if c.kwargs.get('status') is JobStatus.COMPLETED
    ]
    assert len(completed) == 1
    progress = completed[0].kwargs['progress']
    result = completed[0].kwargs['result']
    assert progress['details']['test_result_id'] == 'tr-99'
    assert result['test_result_id'] == 'tr-99'
    assert result['pdf_document_id'] == 'pdf-1'


def test_progress_callback_emits_job_manager_update_progress(
    mock_runner: MagicMock, mock_db_with_doc: MagicMock
) -> None:
    """``_on_progress`` projects (stage, fraction) onto current/total/message."""
    job = _make_job(runner=mock_runner, db=mock_db_with_doc)
    fake_jm = MagicMock()
    _set_jm(job, fake_jm)

    _emit_progress(job, "extract-tags", 0.42)

    fake_jm.update_job_progress.assert_called_once()
    kwargs: dict[str, Any] = fake_jm.update_job_progress.call_args.kwargs
    assert kwargs['job_id'] == job.job_id
    assert kwargs['total'] == 100
    assert kwargs['current'] == 42
    assert kwargs['message'] == 'extract-tags'
    assert kwargs['details']['stage'] == 'extract-tags'
    assert abs(kwargs['details']['fraction'] - 0.42) < 1e-9


def test_progress_callback_clamps_out_of_range_fractions(
    mock_runner: MagicMock, mock_db_with_doc: MagicMock
) -> None:
    """Fractions outside [0, 1] are clamped before projection."""
    job = _make_job(runner=mock_runner, db=mock_db_with_doc)
    fake_jm = MagicMock()
    _set_jm(job, fake_jm)

    _emit_progress(job, "oops", -1.5)
    _emit_progress(job, "done", 99.0)

    calls = fake_jm.update_job_progress.call_args_list
    assert calls[0].kwargs['current'] == 0
    assert calls[1].kwargs['current'] == 100
