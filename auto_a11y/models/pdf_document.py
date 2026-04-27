"""PdfDocument model for downloaded, auditable PDF artefacts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from bson import ObjectId


class PdfDocumentStatus(Enum):
    """Lifecycle states for a PdfDocument."""

    PENDING = "pending"           # created; no audit has run yet
    FETCHING = "fetching"         # downloading the bytes
    FETCH_FAILED = "fetch_failed"  # download or auth error
    AUDITING = "auditing"         # audit in progress
    AUDITED = "audited"           # at least one successful audit
    AUDIT_FAILED = "audit_failed"  # last audit raised before completion


SourceType = Literal["uploaded", "manual_url", "opportunistic"]


@dataclass
class PdfDocument:
    """A downloaded, audit-ready PDF artefact."""

    website_id: str
    project_id: str                         # denormalized for project-level queries
    source_url: str | None
    source_type: SourceType
    discovered_from_page_id: str | None
    discovered_from_user_id: str | None

    sha256: str                             # 64-char hex; dedup key per website
    file_size_bytes: int
    storage_relpath: str                    # relative to Config.PDF_STORAGE_DIR
    images_relpath: str                     # relative; trailing slash

    original_filename: str | None
    pdf_version: str | None
    page_count: int | None
    declared_lang: str | None
    detected_lang: str | None
    lang_confidence: float | None

    status: PdfDocumentStatus
    error_reason: str | None
    last_audit_result_id: str | None

    discovered_at: datetime
    last_audited_at: datetime | None

    _id: ObjectId | None = None

    def __post_init__(self) -> None:
        if '\\' in self.storage_relpath:
            raise ValueError(
                f"PdfDocument.storage_relpath must use POSIX '/' separators, got: {self.storage_relpath!r}"
            )
        if '\\' in self.images_relpath:
            raise ValueError(
                f"PdfDocument.images_relpath must use POSIX '/' separators, got: {self.images_relpath!r}"
            )

    @property
    def id(self) -> str | None:
        """Get PDF document ID as string."""
        return str(self._id) if self._id else None

    @property
    def mongo_id(self) -> ObjectId | None:
        """Get the raw MongoDB _id value."""
        return self._id

    @mongo_id.setter
    def mongo_id(self, value: ObjectId | None) -> None:
        """Set the raw MongoDB _id value."""
        self._id = value

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for MongoDB."""
        data: dict[str, Any] = {
            'website_id': self.website_id,
            'project_id': self.project_id,
            'source_url': self.source_url,
            'source_type': self.source_type,
            'discovered_from_page_id': self.discovered_from_page_id,
            'discovered_from_user_id': self.discovered_from_user_id,
            'sha256': self.sha256,
            'file_size_bytes': self.file_size_bytes,
            'storage_relpath': self.storage_relpath,
            'images_relpath': self.images_relpath,
            'original_filename': self.original_filename,
            'pdf_version': self.pdf_version,
            'page_count': self.page_count,
            'declared_lang': self.declared_lang,
            'detected_lang': self.detected_lang,
            'lang_confidence': self.lang_confidence,
            'status': self.status.value,
            'error_reason': self.error_reason,
            'last_audit_result_id': self.last_audit_result_id,
            'discovered_at': self.discovered_at,
            'last_audited_at': self.last_audited_at,
        }
        if self._id:
            data['_id'] = self._id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PdfDocument:
        """Create from MongoDB document."""
        source_type_raw: str = data['source_type']
        # Use `match` to narrow to the Literal type without cast/ignore.
        source_type: SourceType
        match source_type_raw:
            case "uploaded" | "manual_url" | "opportunistic":
                source_type = source_type_raw
            case _:
                raise ValueError(f"Unknown source_type: {source_type_raw}")
        # `discovered_at` is a historical timestamp — silently substituting
        # `datetime.now()` would misrepresent when the artefact was found.
        discovered_at = data.get('discovered_at')
        if discovered_at is None:
            raise ValueError(
                "PdfDocument record missing required 'discovered_at' field"
            )
        return cls(
            website_id=data['website_id'],
            project_id=data['project_id'],
            source_url=data.get('source_url'),
            source_type=source_type,
            discovered_from_page_id=data.get('discovered_from_page_id'),
            discovered_from_user_id=data.get('discovered_from_user_id'),
            sha256=data['sha256'],
            file_size_bytes=data['file_size_bytes'],
            storage_relpath=data['storage_relpath'],
            images_relpath=data['images_relpath'],
            original_filename=data.get('original_filename'),
            pdf_version=data.get('pdf_version'),
            page_count=data.get('page_count'),
            declared_lang=data.get('declared_lang'),
            detected_lang=data.get('detected_lang'),
            lang_confidence=data.get('lang_confidence'),
            status=PdfDocumentStatus(data['status']),
            error_reason=data.get('error_reason'),
            last_audit_result_id=data.get('last_audit_result_id'),
            discovered_at=discovered_at,
            last_audited_at=data.get('last_audited_at'),
            _id=data.get('_id'),
        )
