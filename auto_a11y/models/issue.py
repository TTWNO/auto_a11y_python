"""
Issue model for Drupal-synced accessibility findings

This model represents issues that can be synced with Drupal,
supporting both automated test results and manual audit findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING
from bson import ObjectId

from auto_a11y.models.test_result import ImpactLevel
from auto_a11y.models.page import DrupalSyncStatus

if TYPE_CHECKING:
    from auto_a11y.models.recording_issue import RecordingIssue


@dataclass
class Issue:
    """
    Accessibility issue that can be synced with Drupal.

    Supports both automated test findings and manual audit findings,
    with bidirectional sync to Drupal issue nodes.
    """

    # Core identification
    title: str
    description: str  # Maps to body in Drupal

    # Classification
    impact: ImpactLevel = ImpactLevel.MEDIUM  # Maps to field_impact (high/med/low)
    issue_type: str | None = None  # Maps to field_issue_type taxonomy
    location_on_page: str | None = None  # Maps to field_location_on_page taxonomy
    issue_code: str | None = None  # Issue code for enhanced descriptions (e.g., "headings_ErrEmptyHeading")

    # WCAG references
    wcag_criteria: list[str] = field(default_factory=lambda: [])  # Maps to field_wcag_chapter

    # Technical details
    xpath: str | None = None  # Maps to field_xpath
    element: str | None = None
    html: str | None = None
    url: str | None = None  # Maps to field_url

    # Recording context (for manual findings)
    recording_id: str | None = None  # Link to Recording document
    video_timecode: str | None = None  # Maps to field_video_timecode

    # Detailed issue information (Dictaphone-style)
    what: str | None = None  # What the issue is
    why: str | None = None  # Why it matters
    who: str | None = None  # Who is affected
    remediation: str | None = None  # How to fix it

    # Relationships
    project_id: str | None = None
    page_urls: list[str] = field(default_factory=lambda: [])
    page_ids: list[str] = field(default_factory=lambda: [])
    discovered_page_ids: list[str] = field(default_factory=lambda: [])
    component_names: list[str] = field(default_factory=lambda: [])

    # Source tracking
    source_type: str = "manual"  # "automated", "manual", "hybrid"
    detection_method: str | None = None  # "axe", "pa11y", "dictaphone", "expert"

    # Status
    status: str = "open"  # open, in_progress, resolved, verified

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    tags: list[str] = field(default_factory=lambda: [])

    # Drupal sync
    drupal_issue_id: int | None = None  # Maps to field_id
    drupal_uuid: str | None = None  # Drupal node UUID
    drupal_nid: int | None = None  # Drupal node ID
    drupal_sync_status: DrupalSyncStatus = DrupalSyncStatus.NOT_SYNCED
    drupal_last_synced: datetime | None = None
    drupal_error_message: str | None = None

    _id: ObjectId | None = None

    @property
    def id(self) -> str | None:
        """Get issue ID as string"""
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
        """Check if issue is synced with Drupal"""
        return self.drupal_sync_status == DrupalSyncStatus.SYNCED and self.drupal_uuid is not None

    @property
    def needs_sync(self) -> bool:
        """Check if issue needs to be synced to Drupal"""
        return self.drupal_sync_status in [DrupalSyncStatus.NOT_SYNCED, DrupalSyncStatus.SYNC_FAILED]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for MongoDB"""
        data: dict[str, Any] = {
            'title': self.title,
            'description': self.description,
            'impact': self.impact.value,
            'issue_type': self.issue_type,
            'location_on_page': self.location_on_page,
            'issue_code': self.issue_code,
            'wcag_criteria': self.wcag_criteria,
            'xpath': self.xpath,
            'element': self.element,
            'html': self.html,
            'url': self.url,
            'recording_id': self.recording_id,
            'video_timecode': self.video_timecode,
            'what': self.what,
            'why': self.why,
            'who': self.who,
            'remediation': self.remediation,
            'project_id': self.project_id,
            'page_urls': self.page_urls,
            'page_ids': self.page_ids,
            'discovered_page_ids': self.discovered_page_ids,
            'component_names': self.component_names,
            'source_type': self.source_type,
            'detection_method': self.detection_method,
            'status': self.status,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'tags': self.tags,
            'drupal_issue_id': self.drupal_issue_id,
            'drupal_uuid': self.drupal_uuid,
            'drupal_nid': self.drupal_nid,
            'drupal_sync_status': self.drupal_sync_status.value,
            'drupal_last_synced': self.drupal_last_synced,
            'drupal_error_message': self.drupal_error_message
        }
        if self._id:
            data['_id'] = self._id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Issue:
        """Create from dictionary"""
        # Handle ObjectId
        obj_id = data.get('_id')

        # Parse impact enum
        impact_value = data.get('impact', 'medium')
        # Map old impact levels for compatibility
        impact_mapping: dict[str, str] = {
            'critical': 'high',
            'serious': 'high',
            'moderate': 'medium',
            'minor': 'low'
        }
        if impact_value in impact_mapping:
            impact_value = impact_mapping[impact_value]
        impact = ImpactLevel(impact_value)

        return cls(
            title=data['title'],
            description=data['description'],
            impact=impact,
            issue_type=data.get('issue_type'),
            location_on_page=data.get('location_on_page'),
            issue_code=data.get('issue_code'),
            wcag_criteria=data.get('wcag_criteria', []),
            xpath=data.get('xpath'),
            element=data.get('element'),
            html=data.get('html'),
            url=data.get('url'),
            recording_id=data.get('recording_id'),
            video_timecode=data.get('video_timecode'),
            what=data.get('what'),
            why=data.get('why'),
            who=data.get('who'),
            remediation=data.get('remediation'),
            project_id=data.get('project_id'),
            page_urls=data.get('page_urls', []),
            page_ids=data.get('page_ids', []),
            discovered_page_ids=data.get('discovered_page_ids', []),
            component_names=data.get('component_names', []),
            source_type=data.get('source_type', 'manual'),
            detection_method=data.get('detection_method'),
            status=data.get('status', 'open'),
            created_at=data.get('created_at', datetime.now()),
            updated_at=data.get('updated_at', datetime.now()),
            tags=data.get('tags', []),
            drupal_issue_id=data.get('drupal_issue_id'),
            drupal_uuid=data.get('drupal_uuid'),
            drupal_nid=data.get('drupal_nid'),
            drupal_sync_status=DrupalSyncStatus(data.get('drupal_sync_status', 'not_synced')),
            drupal_last_synced=data.get('drupal_last_synced'),
            drupal_error_message=data.get('drupal_error_message'),
            _id=obj_id
        )

    @classmethod
    def from_recording_issue(cls, recording_issue: RecordingIssue) -> Issue:
        """
        Convert a RecordingIssue to an Issue for Drupal sync.

        Args:
            recording_issue: RecordingIssue instance

        Returns:
            Issue instance
        """
        # Combine the detailed fields into description
        description_parts: list[str] = []
        if recording_issue.what:
            description_parts.append(f"<h3>What</h3><p>{recording_issue.what}</p>")
        if recording_issue.why:
            description_parts.append(f"<h3>Why</h3><p>{recording_issue.why}</p>")
        if recording_issue.who:
            description_parts.append(f"<h3>Who</h3><p>{recording_issue.who}</p>")
        if recording_issue.remediation:
            description_parts.append(f"<h3>Remediation</h3><p>{recording_issue.remediation}</p>")

        description = "\n".join(description_parts) if description_parts else recording_issue.what or ""

        # Convert timecodes to video_timecode string
        video_timecode: str | None = None
        if recording_issue.timecodes:
            timecode_strs = [f"{tc.start} - {tc.end}" for tc in recording_issue.timecodes]
            video_timecode = "; ".join(timecode_strs)

        # Extract WCAG criteria strings
        wcag_criteria = [w.criteria for w in recording_issue.wcag]

        return cls(
            title=recording_issue.title,
            description=description,
            impact=recording_issue.impact,
            issue_type=recording_issue.touchpoint,
            wcag_criteria=wcag_criteria,
            xpath=recording_issue.xpath,
            element=recording_issue.element,
            html=recording_issue.html,
            recording_id=recording_issue.recording_id,
            video_timecode=video_timecode,
            what=recording_issue.what,
            why=recording_issue.why,
            who=recording_issue.who,
            remediation=recording_issue.remediation,
            project_id=recording_issue.project_id,
            component_names=recording_issue.component_names,
            source_type="manual",
            detection_method="dictaphone",
            status=recording_issue.status,
            created_at=recording_issue.created_at,
            updated_at=recording_issue.updated_at,
            tags=recording_issue.tags
        )
