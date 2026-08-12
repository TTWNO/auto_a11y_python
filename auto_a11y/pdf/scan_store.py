"""Filesystem store for standalone PDF scans.

A **standalone scan** is the "Scan a PDF file" flow: a user drops a PDF in,
the shared audit engine runs over it, and the result comes back. It is
pdfMax's own workflow, carried across whole.

It is deliberately *not* a :class:`~auto_a11y.models.pdf_document.PdfDocument`.
A scan belongs to no website and no project, so it has no business in the
``pdf_documents`` collection, in a project's rollup, or in anybody's
dashboard totals. Only the engine underneath is shared — the same
:func:`~auto_a11y.pdf.audit.pipeline.run_audit` and the same fixes as the
project-scoped path. Everything above the engine is separate on purpose:
adding a standalone scan can never perturb a project's numbers, because no
project query can reach one.

Layout, under ``<PDF_STORAGE_DIR>/_scans/``::

    _scans/<owner_user_id>/<scan_id>/pdf.pdf     the uploaded bytes
    _scans/<owner_user_id>/<scan_id>/images/     extracted page images
    _scans/<owner_user_id>/<scan_id>/scan.json   manifest + audit result

The owner id is part of the path so a scan is authorised by construction:
a request that resolves to a different user's directory simply does not
find the scan. ``scan.json`` also carries ``owner_user_id`` so the check
does not rest on path shape alone.

The stored result mirrors the key names in ``TestResult.metadata`` that the
project-scoped path already uses (``check_results``, ``report_sections``,
``page_count`` and friends), so the report renderer and the viewer read a
scan and an audited ``PdfDocument`` through the same accessors.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from auto_a11y.pdf.models import AuditResult

# A scan id is generated here and then arrives back from the URL. Anchor
# the pattern so a crafted id can never climb out of the scans directory.
_SCAN_ID_RE = re.compile(r'\A[0-9a-f]{32}\Z')

ScanStatus = Literal["auditing", "audited", "audit_failed"]

_STATUSES: dict[str, ScanStatus] = {
    "auditing": "auditing",
    "audited": "audited",
    "audit_failed": "audit_failed",
}


@dataclass(frozen=True)
class ScanRecord:
    """One standalone scan: the manifest, plus where its bytes live."""

    scan_id: str
    owner_user_id: str
    original_filename: str
    file_size_bytes: int
    scanned_at: datetime
    status: ScanStatus
    error_reason: str | None
    wcag_level: Literal["AA", "AAA"]
    locale: str
    run_ai: bool
    # The audit payload, keyed as TestResult.metadata keys it. Empty until
    # the audit finishes.
    result: dict[str, Any]

    @property
    def pdf_version(self) -> str | None:
        value = self.result.get('pdf_version')
        return value if isinstance(value, str) else None

    @property
    def page_count(self) -> int | None:
        value = self.result.get('page_count')
        return value if isinstance(value, int) else None

    @property
    def declared_lang(self) -> str | None:
        value = self.result.get('declared_lang')
        return value if isinstance(value, str) else None

    @property
    def detected_lang(self) -> str | None:
        value = self.result.get('detected_lang')
        return value if isinstance(value, str) else None

    @property
    def report_sections(self) -> dict[str, object]:
        value = self.result.get('report_sections')
        if isinstance(value, dict):
            return cast("dict[str, object]", value)
        return {}

    @property
    def ai_analysis(self) -> dict[str, Any] | None:
        """The AI pass's own summary block, or ``None`` if AI did not run."""
        value = self.result.get('ai_analysis')
        if isinstance(value, dict):
            return cast("dict[str, Any]", value)
        return None

    @property
    def ai_ran(self) -> bool:
        """Whether AI analysis actually produced a result.

        Distinct from :attr:`run_ai`, which records only that it was asked
        for — a missing key or a failed call leaves this ``False`` so the
        UI can say "AI did not run" rather than "AI found nothing".
        """
        analysis = self.ai_analysis
        if analysis is None:
            return False
        model = analysis.get('model')
        return isinstance(model, str) and not model.startswith('(')

    @property
    def issue_map(self) -> dict[str, object] | None:
        """The viewer's overlay payload, or ``None`` if this audit has none."""
        payload = self.report_sections.get('issue_map')
        if isinstance(payload, dict):
            return cast("dict[str, object]", payload)
        return None

    def count(self, key: str) -> int:
        """Read one of the outcome counts, defaulting to zero."""
        value = self.result.get(key)
        return value if isinstance(value, int) else 0


class ScanStore:
    """Filesystem-backed store for standalone scans."""

    def __init__(self, base_dir: Path) -> None:
        # `_scans` is bucketed away from the per-website directories so a
        # website id can never collide with the standalone tree.
        self.base_dir = Path(base_dir) / "_scans"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # -- paths ---------------------------------------------------------

    def _scan_dir(self, owner_user_id: str, scan_id: str) -> Path:
        return self.base_dir / _safe_segment(owner_user_id) / scan_id

    def pdf_path(self, owner_user_id: str, scan_id: str) -> Path:
        return self._scan_dir(owner_user_id, scan_id) / "pdf.pdf"

    def images_dir(self, owner_user_id: str, scan_id: str) -> Path:
        return self._scan_dir(owner_user_id, scan_id) / "images"

    # -- lifecycle -----------------------------------------------------

    def create(
        self,
        *,
        owner_user_id: str,
        pdf_bytes: bytes,
        original_filename: str,
        wcag_level: Literal["AA", "AAA"] = "AA",
        locale: str = "en",
        run_ai: bool = False,
    ) -> ScanRecord:
        """Write the uploaded bytes and open a manifest in ``auditing``.

        The caller is expected to run the audit next and hand the result
        to :meth:`finish` (or the failure to :meth:`fail`).
        """
        scan_id = uuid.uuid4().hex
        scan_dir = self._scan_dir(owner_user_id, scan_id)
        scan_dir.mkdir(parents=True, exist_ok=True)
        (scan_dir / "images").mkdir(exist_ok=True)

        _write_atomic(scan_dir / "pdf.pdf", pdf_bytes)

        record = ScanRecord(
            scan_id=scan_id,
            owner_user_id=owner_user_id,
            original_filename=original_filename,
            file_size_bytes=len(pdf_bytes),
            scanned_at=datetime.now(),
            status="auditing",
            error_reason=None,
            wcag_level=wcag_level,
            locale=locale,
            run_ai=run_ai,
            result={},
        )
        self._write_manifest(record)
        return record

    def finish(self, record: ScanRecord, audit: AuditResult) -> ScanRecord:
        """Record a completed audit against a scan."""
        updated = ScanRecord(
            scan_id=record.scan_id,
            owner_user_id=record.owner_user_id,
            original_filename=record.original_filename,
            file_size_bytes=record.file_size_bytes,
            scanned_at=record.scanned_at,
            status="audited",
            error_reason=None,
            wcag_level=record.wcag_level,
            locale=record.locale,
            run_ai=record.run_ai,
            result=result_payload(audit),
        )
        self._write_manifest(updated)
        return updated

    def fail(self, record: ScanRecord, reason: str) -> ScanRecord:
        """Record that the audit raised, and why."""
        updated = ScanRecord(
            scan_id=record.scan_id,
            owner_user_id=record.owner_user_id,
            original_filename=record.original_filename,
            file_size_bytes=record.file_size_bytes,
            scanned_at=record.scanned_at,
            status="audit_failed",
            error_reason=reason,
            wcag_level=record.wcag_level,
            locale=record.locale,
            run_ai=record.run_ai,
            result={},
        )
        self._write_manifest(updated)
        return updated

    def get(self, owner_user_id: str, scan_id: str) -> ScanRecord | None:
        """Load one scan, or ``None`` if this user has no such scan.

        Rejects a malformed id before touching the filesystem, so a
        traversal attempt never becomes a path.
        """
        if not _SCAN_ID_RE.match(scan_id):
            return None
        manifest = self._scan_dir(owner_user_id, scan_id) / "scan.json"
        if not manifest.is_file():
            return None
        try:
            with open(manifest, encoding='utf-8') as handle:
                raw: object = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return _record_from_dict(cast("dict[str, Any]", raw))

    def list_for_user(self, owner_user_id: str, *, limit: int = 50) -> list[ScanRecord]:
        """List a user's scans, newest first."""
        user_dir = self.base_dir / _safe_segment(owner_user_id)
        if not user_dir.is_dir():
            return []
        records: list[ScanRecord] = []
        for child in user_dir.iterdir():
            if not child.is_dir():
                continue
            record = self.get(owner_user_id, child.name)
            if record is not None:
                records.append(record)
        records.sort(key=lambda r: r.scanned_at, reverse=True)
        return records[:limit]

    def delete(self, owner_user_id: str, scan_id: str) -> bool:
        """Remove a scan and everything under it."""
        if not _SCAN_ID_RE.match(scan_id):
            return False
        scan_dir = self._scan_dir(owner_user_id, scan_id)
        if not scan_dir.is_dir():
            return False
        shutil.rmtree(scan_dir, ignore_errors=True)
        return True

    # -- internals -----------------------------------------------------

    def _write_manifest(self, record: ScanRecord) -> None:
        path = self._scan_dir(record.owner_user_id, record.scan_id) / "scan.json"
        payload = {
            'scan_id': record.scan_id,
            'owner_user_id': record.owner_user_id,
            'original_filename': record.original_filename,
            'file_size_bytes': record.file_size_bytes,
            'scanned_at': record.scanned_at.isoformat(),
            'status': record.status,
            'error_reason': record.error_reason,
            'wcag_level': record.wcag_level,
            'locale': record.locale,
            'run_ai': record.run_ai,
            'result': record.result,
        }
        _write_atomic(path, json.dumps(payload, default=str).encode('utf-8'))


def result_payload(audit: AuditResult) -> dict[str, Any]:
    """Flatten an :class:`AuditResult` into the stored/JSON shape.

    Deliberately the same key names the project-scoped path writes into
    ``TestResult.metadata``, so ``checks_from_metadata`` and the report and
    viewer templates work against a scan without a second code path.
    """
    ai = audit.ai_analysis
    return {
        'pdf_version': audit.pdf_version,
        'page_count': audit.page_count,
        # Present only when AI was asked for. `model` is the honest signal:
        # "(unavailable)" / "(failed)" mean the pass could not run, which is
        # a different thing from running and finding nothing.
        'ai_analysis': None if ai is None else {
            'model': ai.model,
            'executive_summary': ai.executive_summary,
            'overall_severity': ai.overall_severity,
            'cached_input_tokens': ai.cached_input_tokens,
            'uncached_input_tokens': ai.uncached_input_tokens,
            'output_tokens': ai.output_tokens,
            'findings': [
                {
                    'category': f.category,
                    'severity': f.severity,
                    'title': f.title,
                    'description': f.description,
                    'page': f.page,
                    'element_index': f.element_index,
                }
                for f in ai.findings
            ],
        },
        'declared_lang': audit.declared_lang,
        'detected_lang': audit.detected_lang,
        'fail_count': audit.fail_count,
        'warn_count': audit.warn_count,
        'info_count': audit.info_count,
        'pass_count': audit.pass_count,
        'na_count': audit.na_count,
        # Every verdict, including PASS and NA. A report that lists only
        # faults cannot say what was checked.
        'check_results': [
            {
                'name': c.name,
                'standard': c.standard,
                'result': c.result,
                'details': c.details,
            }
            for c in audit.check_results
        ],
        'report_sections': audit.report_sections,
    }


def _record_from_dict(data: dict[str, Any]) -> ScanRecord | None:
    """Rebuild a :class:`ScanRecord` from its manifest, or ``None`` if malformed."""
    scan_id = data.get('scan_id')
    owner_user_id = data.get('owner_user_id')
    if not isinstance(scan_id, str) or not isinstance(owner_user_id, str):
        return None
    if not _SCAN_ID_RE.match(scan_id):
        return None

    scanned_raw = data.get('scanned_at')
    scanned_at: datetime
    if isinstance(scanned_raw, str):
        try:
            scanned_at = datetime.fromisoformat(scanned_raw)
        except ValueError:
            return None
    else:
        return None

    status = _STATUSES.get(str(data.get('status')))
    if status is None:
        return None

    filename = data.get('original_filename')
    size = data.get('file_size_bytes')
    error = data.get('error_reason')
    locale = data.get('locale')
    result = data.get('result')

    return ScanRecord(
        scan_id=scan_id,
        owner_user_id=owner_user_id,
        original_filename=filename if isinstance(filename, str) else 'document.pdf',
        file_size_bytes=size if isinstance(size, int) else 0,
        scanned_at=scanned_at,
        status=status,
        error_reason=error if isinstance(error, str) else None,
        wcag_level="AAA" if data.get('wcag_level') == "AAA" else "AA",
        locale=locale if isinstance(locale, str) else 'en',
        run_ai=bool(data.get('run_ai')),
        result=cast("dict[str, Any]", result) if isinstance(result, dict) else {},
    )


def _safe_segment(value: str) -> str:
    """Reduce an id to a single safe path segment.

    User ids come from Mongo and are hex already, but this store builds
    paths out of them, so nothing untrusted reaches the filesystem
    unfiltered.
    """
    cleaned = re.sub(r'[^A-Za-z0-9_-]', '', value)
    return cleaned or 'anonymous'


def _write_atomic(path: Path, data: bytes) -> None:
    """Write bytes via write-temp-then-rename, fsyncing before the swap."""
    tmp_path = path.with_name(path.name + '.tmp')
    with open(tmp_path, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)
