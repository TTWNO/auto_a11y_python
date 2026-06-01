"""Tests that ``Model.from_dict`` falls back gracefully on unknown enum strings.

A legacy or corrupt MongoDB document may carry an enum value that is no longer
a member of the corresponding Python ``Enum``. Previously, an un-guarded
``Enum(value)`` construction raised ``ValueError`` and the entire object failed
to deserialize. Each ``from_dict`` should instead fall back to a sensible
default member so the document still loads.
"""

from __future__ import annotations

from auto_a11y.models.project import Project, ProjectStatus, ProjectType
from auto_a11y.models.page import Page, PageStatus, DrupalSyncStatus
from auto_a11y.models.recording import Recording, RecordingType
from auto_a11y.models.issue import Issue
from auto_a11y.models.recording_issue import RecordingIssue
from auto_a11y.models.page_setup_script import (
    PageSetupScript,
    ScriptScope,
    ExecutionTrigger,
)


def test_project_status_unknown_falls_back_to_active() -> None:
    project = Project.from_dict({'name': 'p', 'status': 'bogus-status'})
    assert project.status == ProjectStatus.ACTIVE


def test_project_type_unknown_falls_back_to_website() -> None:
    # Already guarded; included to confirm behaviour is preserved.
    project = Project.from_dict({'name': 'p', 'project_type': 'bogus-type'})
    assert project.project_type == ProjectType.WEBSITE


def test_page_status_unknown_falls_back_to_discovered() -> None:
    page = Page.from_dict({
        'website_id': 'w',
        'url': 'https://example.com',
        'status': 'bogus-status',
    })
    assert page.status == PageStatus.DISCOVERED


def test_page_drupal_sync_status_unknown_falls_back_to_not_synced() -> None:
    page = Page.from_dict({
        'website_id': 'w',
        'url': 'https://example.com',
        'drupal_sync_status': 'bogus-sync',
    })
    assert page.drupal_sync_status == DrupalSyncStatus.NOT_SYNCED


def test_recording_type_unknown_falls_back_to_audit() -> None:
    recording = Recording.from_dict({
        'recording_id': 'r',
        'title': 't',
        'recording_type': 'bogus-type',
    })
    assert recording.recording_type == RecordingType.AUDIT


def test_recording_drupal_sync_status_unknown_falls_back_to_not_synced() -> None:
    recording = Recording.from_dict({
        'recording_id': 'r',
        'title': 't',
        'drupal_sync_status': 'bogus-sync',
    })
    assert recording.drupal_sync_status == DrupalSyncStatus.NOT_SYNCED


def test_issue_drupal_sync_status_unknown_falls_back_to_not_synced() -> None:
    issue = Issue.from_dict({
        'title': 't',
        'description': 'd',
        'drupal_sync_status': 'bogus-sync',
    })
    assert issue.drupal_sync_status == DrupalSyncStatus.NOT_SYNCED


def test_recording_issue_drupal_sync_status_unknown_falls_back_to_not_synced() -> None:
    issue = RecordingIssue.from_dict({
        'recording_id': 'r',
        'title': 't',
        'drupal_sync_status': 'bogus-sync',
    })
    assert issue.drupal_sync_status == DrupalSyncStatus.NOT_SYNCED


def test_page_setup_script_scope_unknown_falls_back_to_page() -> None:
    script = PageSetupScript.from_dict({
        'name': 'n',
        'description': 'd',
        'scope': 'bogus-scope',
    })
    assert script.scope == ScriptScope.PAGE


def test_page_setup_script_trigger_unknown_falls_back_to_once_per_page() -> None:
    script = PageSetupScript.from_dict({
        'name': 'n',
        'description': 'd',
        'trigger': 'bogus-trigger',
    })
    assert script.trigger == ExecutionTrigger.ONCE_PER_PAGE
