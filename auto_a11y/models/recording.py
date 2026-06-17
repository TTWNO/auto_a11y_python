"""
Recording model for Dictaphone audio/video audit sessions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast
from enum import Enum
from bson import ObjectId
from .page import DrupalSyncStatus


class RecordingType(Enum):
    """Type of recording"""
    AUDIT = "audit"
    LIVED_EXPERIENCE_WEBSITE = "lived_experience_website"
    LIVED_EXPERIENCE_APP = "lived_experience_app"
    LIVED_EXPERIENCE_TANGIBLE_DEVICE = "lived_experience_tangible_device"
    LIVED_EXPERIENCE_NAV_AND_WAYFINDING = "lived_experience_nav_and_wayfinding"


# Literal type aliases for the audio pipeline state fields. Defined at
# module scope so ``from_dict`` can refer to them when narrowing the
# Mongo-stored strings back into Literals via ``match`` statements.
AuditContext = Literal["audit", "livedExperience", "navilens"]
AnalysisLanguage = Literal["en", "fr"]
CalloutsStatus = Literal["not-requested", "pending", "complete", "failed"]
RecordingStatus = Literal[
    "uploaded", "processing", "complete", "failed", "cancelling", "cancelled"
]


# Per-Literal value tuples are repeated below so each ``in`` check
# returns a narrowed type. ty's match-statement narrowing is weaker
# than mypy / pyright; explicit per-value returns keep all three
# checkers happy without ``cast``.

def _narrow_audit_context(raw: str) -> AuditContext:
    """Narrow a stored string to ``AuditContext``; fall back to ``audit``."""
    if raw == "audit":
        return "audit"
    if raw == "livedExperience":
        return "livedExperience"
    if raw == "navilens":
        return "navilens"
    return "audit"


def _narrow_analysis_language(item: str) -> AnalysisLanguage | None:
    """Narrow one Mongo-stored string to an ``AnalysisLanguage``."""
    if item == "en":
        return "en"
    if item == "fr":
        return "fr"
    return None


def _narrow_analysis_languages(raw: list[object]) -> list[AnalysisLanguage]:
    """Narrow a list of Mongo-stored strings to ``list[AnalysisLanguage]``.

    Unknown values are dropped. If nothing valid remains, fall back to
    ``["en"]`` so the runner always has at least one language to process.
    """
    out: list[AnalysisLanguage] = []
    for item in raw:
        if isinstance(item, str):
            narrowed = _narrow_analysis_language(item)
            if narrowed is not None:
                out.append(narrowed)
    if not out:
        return ["en"]
    return out


def _narrow_callouts_status(raw: str) -> CalloutsStatus:
    """Narrow a stored string to ``CalloutsStatus``; default to ``not-requested``."""
    if raw == "not-requested":
        return "not-requested"
    if raw == "pending":
        return "pending"
    if raw == "complete":
        return "complete"
    if raw == "failed":
        return "failed"
    return "not-requested"


def _narrow_recording_status(raw: str) -> RecordingStatus:
    """Narrow a stored string to ``RecordingStatus``; default to ``uploaded``.

    A Mongo document that pre-dates the audio pipeline migration won't
    have ``status`` at all; ``from_dict`` substitutes ``"uploaded"`` in
    that case, and this helper handles the case where some legacy or
    hand-edited value sneaks in.
    """
    if raw == "uploaded":
        return "uploaded"
    if raw == "processing":
        return "processing"
    if raw == "complete":
        return "complete"
    if raw == "failed":
        return "failed"
    if raw == "cancelling":
        return "cancelling"
    if raw == "cancelled":
        return "cancelled"
    return "uploaded"


@dataclass
class Recording:
    """Audio/video recording session from Dictaphone or manual audits"""

    # Core identifiers
    recording_id: str  # e.g., "NED-A"
    title: str
    description: str | None = None

    # Media information
    media_file_path: str | None = None  # Path to MP4 file
    duration: str | None = None  # Total recording duration (HH:MM:SS)
    recorded_date: datetime | None = None

    # Audit context
    auditor_name: str | None = None
    auditor_role: str | None = None  # e.g., "Screen Reader User", "Expert Auditor"
    test_user_account: str | None = None  # Test account used (e.g., "Premium User", "Admin")
    recording_type: RecordingType = RecordingType.AUDIT

    # Lived experience testing participants (IDs referencing project testers/supervisors)
    lived_experience_tester_id: str | None = None  # ID of tester from project.lived_experience_testers
    test_supervisor_id: str | None = None  # ID of supervisor from project.test_supervisors

    # Testing scope - what content/features were tested
    testing_scope: dict[str, bool] = field(default_factory=lambda: {})  # e.g., {"forms": True, "video": False, ...}

    # Relationships
    project_id: str | None = None  # Link to AutoA11y project

    # Component/Section references (context-specific based on project type)
    website_ids: list[str] = field(default_factory=lambda: [])  # Specific websites (for WEBSITE projects)
    component_names: list[str] = field(default_factory=lambda: [])  # Common components (header, nav, footer, etc.)
    app_screens: list[str] = field(default_factory=lambda: [])  # Specific screens/views (for APP projects)
    device_sections: list[str] = field(default_factory=lambda: [])  # Device sections (for TANGIBLE_DEVICE projects)

    # Task context
    task_description: str | None = None  # Specific task performed (e.g., "Complete checkout process", "Navigate to settings")

    # Statistics (computed from issues)
    total_issues: int = 0
    high_impact_count: int = 0
    medium_impact_count: int = 0
    low_impact_count: int = 0

    # Recording Content (Structured data) - Multi-language support
    # Each can contain 'en' and/or 'fr' keys with the content in that language
    key_takeaways: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {})  # {'en': [...], 'fr': [...]}
    user_painpoints: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {})  # {'en': [...], 'fr': [...]}
    user_assertions: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {})  # {'en': [...], 'fr': [...]}

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    tags: list[str] = field(default_factory=lambda: [])
    notes: str | None = None

    # Drupal sync (audit_video export)
    drupal_video_uuid: str | None = None  # UUID of audit_video node in Drupal
    drupal_video_nid: int | None = None  # Drupal node ID
    drupal_sync_status: DrupalSyncStatus = DrupalSyncStatus.NOT_SYNCED
    drupal_last_synced: datetime | None = None
    drupal_error_message: str | None = None

    # === Audio pipeline state (added by Phase 6 of audioA11y integration) ===
    source_video_path: str | None = None
    audit_context: AuditContext = "audit"
    analysis_languages: list[AnalysisLanguage] = field(default_factory=lambda: ["en"])
    extended_context: bool = False
    speaker_remap_enabled: bool = True
    callouts_requested: bool = False
    callouts_status: CalloutsStatus = "not-requested"
    # When callouts/branding rendering fails (Stage F), the audit still
    # completes but this carries the reason (ffmpeg stderr / CalloutsError
    # message) so the UI can show it instead of a bare "failed" badge.
    callouts_error: str | None = None
    # Per-analysis failures (one entry per failed context×language×kind,
    # e.g. a truncated Claude JSON). The recording still completes with the
    # analyses that succeeded; these record what was lost so the UI/log can
    # show it instead of failing the whole recording.
    analysis_errors: list[str] = field(default_factory=lambda: [])
    status: RecordingStatus = "uploaded"
    progress: dict[str, object] | None = None
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None
    cost_breakdown: dict[str, object] | None = None
    error_message: str | None = None
    manifest_version: str = ""  # populated from ``auto_a11y.audio.__version__``
    started_at: datetime | None = None
    finished_at: datetime | None = None

    _id: ObjectId | None = None

    @property
    def id(self) -> str | None:
        """Get recording ID as string"""
        return str(self._id) if self._id else None

    @property
    def mongo_id(self) -> ObjectId | None:
        """Get the raw MongoDB _id value."""
        return self._id

    @mongo_id.setter
    def mongo_id(self, value: ObjectId | None) -> None:
        """Set the raw MongoDB _id value."""
        self._id = value

    @property
    def is_synced(self) -> bool:
        """Check if recording is synced with Drupal"""
        return self.drupal_sync_status == DrupalSyncStatus.SYNCED and self.drupal_video_uuid is not None

    @property
    def needs_sync(self) -> bool:
        """Check if recording needs to be synced to Drupal"""
        return self.drupal_sync_status in [DrupalSyncStatus.NOT_SYNCED, DrupalSyncStatus.SYNC_FAILED]

    def get_key_takeaways(self, language: str = 'en') -> list[dict[str, Any]]:
        """Get key takeaways in specified language, fallback to English"""
        return self.key_takeaways.get(language, self.key_takeaways.get('en', []))

    def get_user_painpoints(self, language: str = 'en') -> list[dict[str, Any]]:
        """Get user painpoints in specified language, fallback to English"""
        return self.user_painpoints.get(language, self.user_painpoints.get('en', []))

    def get_user_assertions(self, language: str = 'en') -> list[dict[str, Any]]:
        """Get user assertions in specified language, fallback to English"""
        return self.user_assertions.get(language, self.user_assertions.get('en', []))

    @property
    def available_languages(self) -> list[str]:
        """Get list of languages that have content"""
        languages: set[str] = set()
        if self.key_takeaways:
            languages.update(self.key_takeaways.keys())
        if self.user_painpoints:
            languages.update(self.user_painpoints.keys())
        if self.user_assertions:
            languages.update(self.user_assertions.keys())
        # If no languages found, default to 'en' if any content exists
        if not languages:
            if self.key_takeaways or self.user_painpoints or self.user_assertions:
                languages.add('en')
        return sorted(list(languages))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for MongoDB"""
        data: dict[str, Any] = {
            'recording_id': self.recording_id,
            'title': self.title,
            'description': self.description,
            'media_file_path': self.media_file_path,
            'duration': self.duration,
            'recorded_date': self.recorded_date,
            'auditor_name': self.auditor_name,
            'auditor_role': self.auditor_role,
            'test_user_account': self.test_user_account,
            'recording_type': self.recording_type.value,
            'lived_experience_tester_id': self.lived_experience_tester_id,
            'test_supervisor_id': self.test_supervisor_id,
            'testing_scope': self.testing_scope,
            'project_id': self.project_id,
            'website_ids': self.website_ids,
            'component_names': self.component_names,
            'app_screens': self.app_screens,
            'device_sections': self.device_sections,
            'task_description': self.task_description,
            'key_takeaways': self.key_takeaways,
            'user_painpoints': self.user_painpoints,
            'user_assertions': self.user_assertions,
            'total_issues': self.total_issues,
            'high_impact_count': self.high_impact_count,
            'medium_impact_count': self.medium_impact_count,
            'low_impact_count': self.low_impact_count,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'tags': self.tags,
            'notes': self.notes,
            'drupal_video_uuid': self.drupal_video_uuid,
            'drupal_video_nid': self.drupal_video_nid,
            'drupal_sync_status': self.drupal_sync_status.value,
            'drupal_last_synced': self.drupal_last_synced,
            'drupal_error_message': self.drupal_error_message,
            # === Audio pipeline state ===
            'source_video_path': self.source_video_path,
            'audit_context': self.audit_context,
            'analysis_languages': list(self.analysis_languages),
            'extended_context': self.extended_context,
            'speaker_remap_enabled': self.speaker_remap_enabled,
            'callouts_requested': self.callouts_requested,
            'callouts_status': self.callouts_status,
            'callouts_error': self.callouts_error,
            'analysis_errors': list(self.analysis_errors),
            'status': self.status,
            'progress': self.progress,
            'estimated_cost_usd': self.estimated_cost_usd,
            'actual_cost_usd': self.actual_cost_usd,
            'cost_breakdown': self.cost_breakdown,
            'error_message': self.error_message,
            'manifest_version': self.manifest_version,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
        }
        if self._id:
            data['_id'] = self._id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recording:
        """Create from dictionary"""
        # Handle ObjectId
        obj_id = data.get('_id')

        # Parse recording_type enum (with backward compatibility for old audit_type field)
        recording_type_value = data.get('recording_type') or data.get('audit_type', 'audit')
        if recording_type_value:
            try:
                recording_type = RecordingType(recording_type_value)
            except ValueError:
                recording_type = RecordingType.AUDIT
        else:
            recording_type = RecordingType.AUDIT

        # Narrow audio-pipeline Literal-typed fields from their Mongo-
        # stored ``str`` form. Missing fields fall back to dataclass
        # defaults so pre-Phase-6 documents deserialize cleanly.
        audit_context_raw = data.get('audit_context', 'audit')
        if not isinstance(audit_context_raw, str):
            audit_context_raw = 'audit'

        # ``analysis_languages`` may be missing on legacy docs (defaults
        # to ``['en']``) or non-list (treated the same). Cast launders
        # pyright's ``list[Unknown]`` element type to ``list[object]`` —
        # ``_narrow_analysis_languages`` does per-element isinstance
        # checks regardless of the static element type.
        analysis_languages_raw_any: Any = data.get('analysis_languages', ['en'])
        analysis_languages_raw: list[object] = (
            cast(list[object], analysis_languages_raw_any)
            if isinstance(analysis_languages_raw_any, list)
            else ['en']
        )

        callouts_status_raw = data.get('callouts_status', 'not-requested')
        if not isinstance(callouts_status_raw, str):
            callouts_status_raw = 'not-requested'

        status_raw = data.get('status', 'uploaded')
        if not isinstance(status_raw, str):
            status_raw = 'uploaded'

        # ``progress`` / ``cost_breakdown`` are blobs from the pipeline.
        # Mongo round-trips them as JSON-shaped dicts; we coerce missing
        # / non-dict values to ``None``. The ``cast`` launders pyright's
        # ``Unknown`` element type to ``object`` (the canonical shape
        # used elsewhere — see ``_is_str_obj_dict`` in audio/).
        progress_raw: object = data.get('progress')
        progress_val: dict[str, object] | None = (
            cast(dict[str, object], progress_raw)
            if isinstance(progress_raw, dict)
            else None
        )
        cost_breakdown_raw: object = data.get('cost_breakdown')
        cost_breakdown_val: dict[str, object] | None = (
            cast(dict[str, object], cost_breakdown_raw)
            if isinstance(cost_breakdown_raw, dict)
            else None
        )

        # Guard Drupal sync status against legacy/unknown values
        try:
            drupal_sync_status = DrupalSyncStatus(data.get('drupal_sync_status', 'not_synced'))
        except ValueError:
            drupal_sync_status = DrupalSyncStatus.NOT_SYNCED

        return cls(
            recording_id=data['recording_id'],
            title=data['title'],
            description=data.get('description'),
            media_file_path=data.get('media_file_path'),
            duration=data.get('duration'),
            recorded_date=data.get('recorded_date'),
            auditor_name=data.get('auditor_name'),
            auditor_role=data.get('auditor_role'),
            test_user_account=data.get('test_user_account'),
            recording_type=recording_type,
            lived_experience_tester_id=data.get('lived_experience_tester_id'),
            test_supervisor_id=data.get('test_supervisor_id'),
            testing_scope=data.get('testing_scope', {}),
            project_id=data.get('project_id'),
            website_ids=data.get('website_ids', []),
            component_names=data.get('component_names', []),
            app_screens=data.get('app_screens', []),
            device_sections=data.get('device_sections', []),
            task_description=data.get('task_description'),
            key_takeaways=data.get('key_takeaways', {}),
            user_painpoints=data.get('user_painpoints', {}),
            user_assertions=data.get('user_assertions', {}),
            total_issues=data.get('total_issues', 0),
            high_impact_count=data.get('high_impact_count', 0),
            medium_impact_count=data.get('medium_impact_count', 0),
            low_impact_count=data.get('low_impact_count', 0),
            created_at=data.get('created_at', datetime.now()),
            updated_at=data.get('updated_at', datetime.now()),
            tags=data.get('tags', []),
            notes=data.get('notes'),
            drupal_video_uuid=data.get('drupal_video_uuid'),
            drupal_video_nid=data.get('drupal_video_nid'),
            drupal_sync_status=drupal_sync_status,
            drupal_last_synced=data.get('drupal_last_synced'),
            drupal_error_message=data.get('drupal_error_message'),
            # === Audio pipeline state ===
            source_video_path=data.get('source_video_path'),
            audit_context=_narrow_audit_context(audit_context_raw),
            analysis_languages=_narrow_analysis_languages(analysis_languages_raw),
            extended_context=bool(data.get('extended_context', False)),
            speaker_remap_enabled=bool(data.get('speaker_remap_enabled', True)),
            callouts_requested=bool(data.get('callouts_requested', False)),
            callouts_status=_narrow_callouts_status(callouts_status_raw),
            callouts_error=(
                data.get('callouts_error')
                if isinstance(data.get('callouts_error'), str)
                else None
            ),
            analysis_errors=[
                item for item in data.get('analysis_errors', [])
                if isinstance(item, str)
            ] if isinstance(data.get('analysis_errors'), list) else [],
            status=_narrow_recording_status(status_raw),
            progress=progress_val,
            estimated_cost_usd=data.get('estimated_cost_usd'),
            actual_cost_usd=data.get('actual_cost_usd'),
            cost_breakdown=cost_breakdown_val,
            error_message=data.get('error_message'),
            manifest_version=data.get('manifest_version', ''),
            started_at=data.get('started_at'),
            finished_at=data.get('finished_at'),
            _id=obj_id
        )


@dataclass
class Timecode:
    """Timecode reference in a recording"""
    start: str  # HH:MM:SS.mmm format
    end: str  # HH:MM:SS.mmm format
    duration: str  # HH:MM:SS.mmm format

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary"""
        return {
            'start': self.start,
            'end': self.end,
            'duration': self.duration
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Timecode:
        """Create from dictionary"""
        return cls(
            start=data['start'],
            end=data['end'],
            duration=data['duration']
        )

    def to_seconds(self, time_str: str) -> float:
        """Convert HH:MM:SS.mmm to total seconds"""
        try:
            # Split into time and milliseconds
            if '.' in time_str:
                time_part, ms_part = time_str.split('.')
            else:
                time_part = time_str
                ms_part = '0'

            # Parse time components
            parts = time_part.split(':')
            if len(parts) == 3:
                hours, minutes, seconds = map(int, parts)
            elif len(parts) == 2:
                hours = 0
                minutes, seconds = map(int, parts)
            else:
                return 0.0

            # Calculate total seconds
            total = hours * 3600 + minutes * 60 + seconds + float(f"0.{ms_part}")
            return total
        except (ValueError, AttributeError):
            return 0.0

    @property
    def start_seconds(self) -> float:
        """Get start time in seconds"""
        return self.to_seconds(self.start)

    @property
    def end_seconds(self) -> float:
        """Get end time in seconds"""
        return self.to_seconds(self.end)


@dataclass
class WCAGReference:
    """WCAG criteria reference"""
    criteria: str  # e.g., "1.3.1"
    level: str  # "A", "AA", "AAA"
    versions: list[str] = field(default_factory=lambda: [])  # ["2.0", "2.1", "2.2"]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            'criteria': self.criteria,
            'level': self.level,
            'versions': self.versions
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WCAGReference:
        """Create from dictionary"""
        return cls(
            criteria=data['criteria'],
            level=data['level'],
            versions=data.get('versions', [])
        )
