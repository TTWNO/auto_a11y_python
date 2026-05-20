"""
Recording management routes for manual audits
"""
from __future__ import annotations

import asyncio
import secrets
from datetime import datetime
from typing import Any, cast

from flask import (
    Blueprint, Response, abort, make_response, render_template, request, redirect,
    send_file, url_for, flash, jsonify, session
)
from auto_a11y.web.api.deprecation import deprecated
from auto_a11y.web.fluent import ftl
from auto_a11y.web.typed_app import (
    get_app_config, get_audio_storage, get_db, get_video_runner,
)
from werkzeug.datastructures import FileStorage
from werkzeug.wrappers import Response as WerkzeugResponse
import logging
import json
from pathlib import Path

from auto_a11y.audio import cost as audio_cost
from auto_a11y.core.job_manager import JobManager, JobType, JobStatus
from auto_a11y.core.task_runner import task_runner
from auto_a11y.models import Recording, RecordingIssue, RecordingType
from auto_a11y.models.recording import AnalysisLanguage, AuditContext
from auto_a11y.importers import DictaphoneImporter

logger = logging.getLogger(__name__)
recordings_bp = Blueprint('recordings', __name__)


@recordings_bp.route('/')
def list_recordings() -> str | Response:
    """List all recordings"""
    try:
        project_id = request.args.get('project_id')
        recording_type = request.args.get('recording_type')

        # Convert recording_type string to enum if provided
        recording_type_enum = None
        if recording_type:
            try:
                recording_type_enum = RecordingType(recording_type)
            except ValueError:
                pass

        recordings = get_db().get_recordings(
            project_id=project_id,
            recording_type=recording_type_enum
        )

        # Get projects for filter dropdown
        projects = get_db().get_all_projects()

        return render_template(
            'recordings/list.html',
            recordings=recordings,
            projects=projects,
            selected_project_id=project_id,
            selected_recording_type=recording_type
        )
    except Exception as e:
        logger.error(f"Error listing recordings: {e}", exc_info=True)
        flash(ftl('recordings-error-loading-recordings-error', error=str(e)), "danger")
        return render_template('recordings/list.html', recordings=[], projects=[])


@recordings_bp.route('/<recording_id>')
def view_recording(recording_id: str) -> str | Response | WerkzeugResponse:
    """View recording details"""
    try:
        recording = get_db().get_recording(recording_id)
        if not recording:
            flash(ftl('recordings-recording-not-found'), "danger")
            return redirect(url_for('recordings.list_recordings'))

        # Get language preference - use page language from session/locale
        language = request.args.get('lang')
        if not language:
            # Use the current page language (from session or browser)
            language = session.get('language', request.accept_languages.best_match(['en', 'fr']) or 'en')
        if language not in ['en', 'fr']:
            language = 'en'

        logger.info(f"Viewing recording {recording.recording_id} with language: {language}")

        # Get ALL issues for this recording
        all_issues = get_db().get_recording_issues_for_recording(recording.recording_id)

        logger.info(f"Total issues found: {len(all_issues)}")

        # Filter issues by selected language
        issues = [issue for issue in all_issues if issue.language == language]

        logger.info(f"Filtered to {len(issues)} issues for language '{language}'")

        # Get available issue languages for this recording
        issue_languages = sorted(list(set(issue.language for issue in all_issues)))

        logger.info(f"Available issue languages: {issue_languages}")
        logger.info(f"Recording available_languages: {recording.available_languages}")

        # Calculate manual scores
        from auto_a11y.scoring import ManualAccessibilityScorer
        from auto_a11y.wcag_parser import get_wcag_parser
        scorer = ManualAccessibilityScorer()
        scores = scorer.calculate_scores(recording, issues, target_level='AA')

        # Get detailed criteria breakdown for the modal
        wcag_parser = get_wcag_parser()
        all_criteria = wcag_parser.get_criteria_for_level('AA')
        applicable_criteria = scorer.scope_mapper.get_applicable_criteria(
            recording.testing_scope or {},
            target_level='AA'
        )

        # Determine which criteria were removed
        all_criteria_ids = set(c.id for c in all_criteria)
        applicable_criteria_ids = set(c.id for c in applicable_criteria)
        removed_criteria_ids = all_criteria_ids - applicable_criteria_ids

        removed_criteria = [c for c in all_criteria if c.id in removed_criteria_ids]

        # Group issues by touchpoint
        issues_by_touchpoint: dict[str, list[RecordingIssue]] = {}
        for issue in issues:
            touchpoint = issue.touchpoint or "General"
            if touchpoint not in issues_by_touchpoint:
                issues_by_touchpoint[touchpoint] = []
            issues_by_touchpoint[touchpoint].append(issue)

        # Get project if linked
        project = None
        if recording.project_id:
            project = get_db().get_project(recording.project_id)

        return render_template(
            'recordings/detail.html',
            recording=recording,
            issues=issues,
            issues_by_touchpoint=issues_by_touchpoint,
            project=project,
            scores=scores,
            all_criteria=all_criteria,
            applicable_criteria=applicable_criteria,
            removed_criteria=removed_criteria,
            current_language=language,
            issue_languages=issue_languages
        )
    except Exception as e:
        logger.error(f"Error viewing recording: {e}", exc_info=True)
        flash(ftl('recordings-error-loading-recording-error', error=str(e)), "danger")
        return redirect(url_for('recordings.list_recordings'))


@recordings_bp.route('/combined/<project_id>')
def view_combined_recordings(project_id: str) -> str | Response | WerkzeugResponse:
    """View all recordings for a project combined into a single issue list"""
    try:
        # Get project
        project = get_db().get_project(project_id)
        if not project:
            flash(ftl('common-project-not-found'), "danger")
            return redirect(url_for('recordings.list_recordings'))

        # Get all recordings for this project
        recordings = get_db().get_recordings(project_id=project_id)

        if not recordings:
            flash(ftl('recordings-no-recordings-found-for-this-project'), "info")
            return redirect(url_for('projects.view_project', project_id=project_id))

        # Collect all issues from all recordings
        all_issues: list[RecordingIssue] = []
        for recording in recordings:
            issues = get_db().get_recording_issues_for_recording(recording.recording_id)
            # Add recording reference to each issue for display
            for issue in issues:
                setattr(issue, 'recording_ref', recording)
            all_issues.extend(issues)

        # Group all issues by touchpoint
        issues_by_touchpoint: dict[str, list[RecordingIssue]] = {}
        for issue in all_issues:
            touchpoint = issue.touchpoint or "General"
            if touchpoint not in issues_by_touchpoint:
                issues_by_touchpoint[touchpoint] = []
            issues_by_touchpoint[touchpoint].append(issue)

        # Calculate combined statistics
        total_issues = len(all_issues)
        high_count = sum(1 for i in all_issues if i.impact.value.lower() in ['high', 'critical'])
        medium_count = sum(1 for i in all_issues if i.impact.value.lower() in ['medium', 'moderate'])
        low_count = sum(1 for i in all_issues if i.impact.value.lower() == 'low')

        return render_template(
            'recordings/combined.html',
            project=project,
            recordings=recordings,
            all_issues=all_issues,
            issues_by_touchpoint=issues_by_touchpoint,
            total_issues=total_issues,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count
        )
    except Exception as e:
        logger.error(f"Error viewing combined recordings: {e}", exc_info=True)
        flash(ftl('recordings-error-loading-combined-view-error', error=str(e)), "danger")
        return redirect(url_for('projects.view_project', project_id=project_id))


def _is_mp4_upload(file: FileStorage) -> bool:
    """Return True iff ``file`` looks like an MP4 upload.

    Checks both the MIME type the browser declared and the filename
    extension; either is sufficient. Empty filename or zero-byte uploads
    return False so we never branch into the video flow for the empty
    placeholder files that browsers attach to unused ``<input type=file>``
    elements.
    """
    filename = file.filename or ''
    if not filename:
        return False
    if file.mimetype == 'video/mp4':
        return True
    if filename.lower().endswith('.mp4'):
        return True
    return False


def _narrow_audit_context(raw: str) -> AuditContext:
    """Narrow a form-submitted audit-context string to ``AuditContext``."""
    if raw == 'audit':
        return 'audit'
    if raw == 'livedExperience':
        return 'livedExperience'
    if raw == 'navilens':
        return 'navilens'
    return 'audit'


def _narrow_language(raw: str) -> AnalysisLanguage | None:
    if raw == 'en':
        return 'en'
    if raw == 'fr':
        return 'fr'
    return None


def _error_response(message_id: str, status: int, **kwargs: object) -> Response:
    """Render the upload form with a flash + the given HTTP status.

    The existing JSON-import branch redirects on error (302), which is
    fine for synchronous form submissions. The MP4 branch uses real
    status codes (400 for validation, 413 for size cap) so the Flask
    test client can assert against them.
    """
    flash(ftl(message_id, **kwargs), 'danger')
    projects = get_db().get_all_projects()
    rendered = render_template('recordings/upload.html', projects=projects)
    response = make_response(rendered, status)
    return response


def _handle_video_upload(file: FileStorage) -> str | Response | WerkzeugResponse:
    """Handle an MP4 upload: validate, allocate storage, estimate cost.

    Splits the video flow out of :func:`upload_recording` to keep the
    existing JSON / HTML import path readable. On success renders
    ``recordings/upload_confirm.html`` with the freshly-created
    :class:`Recording`; on validation failure renders the upload form
    with a flash + an appropriate HTTP status (400 / 413).
    """
    cfg = get_app_config()
    max_mb = cfg.AUDIO_MAX_SIZE_MB
    max_bytes = max_mb * 1024 * 1024

    # ---- Validate inputs ---------------------------------------------------
    project_id = request.form.get('project_id')
    if not project_id:
        return _error_response('audio-error-no-project', 400)

    project = get_db().get_project(project_id)
    if project is None:
        return _error_response('audio-error-no-project-access', 403)

    # Language selection — at least one required.
    raw_languages = request.form.getlist('languages')
    selected: list[AnalysisLanguage] = []
    for raw in raw_languages:
        narrowed = _narrow_language(raw)
        if narrowed is not None and narrowed not in selected:
            selected.append(narrowed)
    if not selected:
        return _error_response('audio-error-no-language', 400)

    # Size cap — Content-Length is unreliable, so probe the stream.
    file.stream.seek(0, 2)  # SEEK_END
    size_bytes = file.stream.tell()
    file.stream.seek(0)
    if size_bytes > max_bytes:
        return _error_response('audio-error-file-too-large', 413, max=max_mb)

    # ---- Allocate storage + persist source ---------------------------------
    storage = get_audio_storage()
    if storage is None:
        return _error_response('audio-error-runner-not-configured', 503)

    recording_id = (
        'REC-'
        + datetime.now().strftime('%Y%m%d%H%M%S')
        + '-'
        + secrets.token_hex(3)
    )
    slot = storage.allocate(recording_id)
    file.save(str(slot.source_mp4))

    # ---- Probe duration + estimate cost ------------------------------------
    # Local import: ``auto_a11y.audio.segmenter`` transitively pulls
    # ``auto_a11y.audio.ffmpeg`` which registers preflight checks at
    # import time. Loading it at module scope here triggers a circular
    # import with ``auto_a11y.core`` (preflight → core → testing →
    # web.fluent → web.app → web.routes ↻). Deferring to the call site
    # breaks the cycle without changing observable behaviour.
    from auto_a11y.audio import segmenter as audio_segmenter
    try:
        duration_s = audio_segmenter.probe_duration(slot.source_mp4)
    except Exception as exc:  # noqa: BLE001 — surface as a 400 + flash
        logger.error("probe_duration failed for %s: %s", recording_id, exc)
        # The slot has been allocated but we leave it on disk — the next
        # successful upload re-uses a fresh id; cleanup is the operator's.
        return _error_response('audio-error-invalid-mp4', 400)

    audit_context = _narrow_audit_context(request.form.get('audit_context', 'audit'))
    extended_context = request.form.get('extended_context') == 'on'
    speaker_remap_enabled = request.form.get('speaker_remap_enabled') == 'on'
    callouts_requested = request.form.get('callouts_requested') == 'on'

    estimate = audio_cost.estimate_cost(
        duration_s=duration_s,
        contexts=[audit_context],
        languages=selected,
        extended_context=extended_context,
        callouts=callouts_requested,
    )

    # ---- Build + persist the Recording row ---------------------------------
    title = (request.form.get('title') or '').strip() or f'Recording {recording_id}'
    recording = Recording(
        recording_id=recording_id,
        title=title,
        project_id=project_id,
        source_video_path=str(slot.source_mp4),
        audit_context=audit_context,
        analysis_languages=selected,
        extended_context=extended_context,
        speaker_remap_enabled=speaker_remap_enabled,
        callouts_requested=callouts_requested,
        callouts_status='pending' if callouts_requested else 'not-requested',
        status='uploaded',
        estimated_cost_usd=estimate.total_usd,
        cost_breakdown=cast(dict[str, object], dict(estimate.breakdown)),
    )
    mongo_id = get_db().create_recording(recording)

    # Link recording into the project's recording_ids list (matches the
    # JSON-import path so the project sidebar surfaces it consistently).
    if mongo_id not in project.recording_ids:
        project.recording_ids.append(mongo_id)
        get_db().update_project(project)

    return render_template(
        'recordings/upload_confirm.html',
        recording=recording,
        estimate=estimate,
        duration_s=duration_s,
        pricing_as_of=audio_cost.AS_OF.isoformat(),
        max_size_mb=max_mb,
    )


@recordings_bp.route('/upload', methods=['GET', 'POST'])
def upload_recording() -> str | Response | WerkzeugResponse:
    """Upload Dictaphone JSON file or an MP4 audit video.

    The MP4 branch (Phase 7 of the audioA11y integration) auto-generates
    a ``REC-YYYYMMDDHHMMSS-{6hex}`` id, allocates the per-recording
    directory tree under ``AUDIO_STORAGE_DIR``, probes the duration via
    ffprobe, computes a pre-flight cost estimate, and renders the
    confirm-step template. The user kicks off the actual pipeline by
    POSTing to ``/recordings/<id>/process``.

    The JSON / HTML branch is unchanged: it imports a Dictaphone export
    directly into the recording_issues collection.
    """
    if request.method == 'GET':
        projects = get_db().get_all_projects()
        return render_template('recordings/upload.html', projects=projects)

    # MP4 branch: any uploaded file whose extension / MIME identifies it
    # as an MP4 dispatches to the video flow before the JSON-import
    # validation below.
    for upload in request.files.values():
        if _is_mp4_upload(upload):
            return _handle_video_upload(upload)

    try:
        # Validate file uploads - at least English required
        if 'json_file_en' not in request.files:
            flash(ftl('recordings-english-json-file-is-required'), "danger")
            return redirect(url_for('recordings.upload_recording'))

        file_en = request.files['json_file_en']
        if file_en.filename == '':
            flash(ftl('recordings-english-json-file-is-required'), "danger")
            return redirect(url_for('recordings.upload_recording'))

        if not file_en.filename or not file_en.filename.endswith('.json'):
            flash(ftl('recordings-file-must-be-a-json-file'), "danger")
            return redirect(url_for('recordings.upload_recording'))

        # Optional French file
        file_fr = request.files.get('json_file_fr')
        has_french = file_fr and file_fr.filename and file_fr.filename.endswith('.json')

        # Get form data
        project_id = request.form.get('project_id')
        if not project_id:
            flash(ftl('recordings-project-is-required'), "danger")
            return redirect(url_for('recordings.upload_recording'))

        # Optional fields
        title = request.form.get('title', '')
        description = request.form.get('description', '')
        auditor_name = request.form.get('auditor_name', '')
        auditor_role = request.form.get('auditor_role', '')
        test_user_account = request.form.get('test_user_account', '').strip() or None
        recording_type = request.form.get('recording_type', 'audit')
        lived_experience_tester_id = request.form.get('lived_experience_tester_id', '').strip() or None
        test_supervisor_id = request.form.get('test_supervisor_id', '').strip() or None
        component_names_str = request.form.get('component_names', '')
        app_screens_str = request.form.get('app_screens', '')
        device_sections_str = request.form.get('device_sections', '')
        task_description = request.form.get('task_description', '').strip() or None
        media_file_path = request.form.get('media_file_path', '')

        # Capture testing scope
        testing_scope = {
            'forms': request.form.get('scope_forms') == 'on',
            'video': request.form.get('scope_video') == 'on',
            'live_multimedia': request.form.get('scope_live_multimedia') == 'on',
            'multilingual': request.form.get('scope_multilingual') == 'on',
            'orientation': request.form.get('scope_orientation') == 'on',
            'zoom': request.form.get('scope_zoom') == 'on',
            'timeouts': request.form.get('scope_timeouts') == 'on',
            'motion_actuation': request.form.get('scope_motion_actuation') == 'on',
            'drag_drop': request.form.get('scope_drag_drop') == 'on',
        }

        # Parse multi-line fields
        component_names = [c.strip() for c in component_names_str.split('\n') if c.strip()]
        app_screens = [s.strip() for s in app_screens_str.split('\n') if s.strip()]
        device_sections = [d.strip() for d in device_sections_str.split('\n') if d.strip()]

        # Process HTML or JSON content files (key takeaways, painpoints, assertions) - Multi-language
        from auto_a11y.parsers import (
            parse_key_takeaways_html,
            parse_user_painpoints_html,
            parse_user_assertions_html,
            parse_key_takeaways_json,
            parse_user_painpoints_json,
            parse_user_assertions_json
        )

        # Store content by language: {'en': [...], 'fr': [...]}
        key_takeaways_data: dict[str, list[dict[str, Any]]] = {}
        user_painpoints_data: dict[str, list[dict[str, Any]]] = {}
        user_assertions_data: dict[str, list[dict[str, Any]]] = {}

        # Process English content files
        for lang_suffix, lang_code in [('_en', 'en'), ('_fr', 'fr')]:
            # Key Takeaways
            file_key = f'key_takeaways_file{lang_suffix}'
            if file_key in request.files:
                takeaways_file = request.files[file_key]
                if takeaways_file.filename:
                    content = takeaways_file.read().decode('utf-8')
                    try:
                        if takeaways_file.filename.endswith('.json'):
                            json_data = json.loads(content)
                            key_takeaways_data[lang_code] = parse_key_takeaways_json(json_data)
                            logger.info(f"✓ Parsed {len(key_takeaways_data[lang_code])} key takeaways ({lang_code.upper()}) from JSON")
                        elif takeaways_file.filename.endswith('.html') or takeaways_file.filename.endswith('.htm'):
                            key_takeaways_data[lang_code] = parse_key_takeaways_html(content)
                            logger.info(f"✓ Parsed {len(key_takeaways_data[lang_code])} key takeaways ({lang_code.upper()}) from HTML")
                    except Exception as e:
                        logger.error(f"Error parsing key takeaways ({lang_code}): {e}", exc_info=True)
                        flash(ftl('recordings-error-parsing-key-takeaways-lang-error', lang=lang_code, error=str(e)), "warning")

            # User Painpoints
            file_key = f'user_painpoints_file{lang_suffix}'
            if file_key in request.files:
                painpoints_file = request.files[file_key]
                if painpoints_file.filename:
                    content = painpoints_file.read().decode('utf-8')
                    try:
                        if painpoints_file.filename.endswith('.json'):
                            json_data = json.loads(content)
                            user_painpoints_data[lang_code] = parse_user_painpoints_json(json_data)
                            logger.info(f"✓ Parsed {len(user_painpoints_data[lang_code])} painpoints ({lang_code.upper()}) from JSON")
                        elif painpoints_file.filename.endswith('.html') or painpoints_file.filename.endswith('.htm'):
                            user_painpoints_data[lang_code] = parse_user_painpoints_html(content)
                            logger.info(f"✓ Parsed {len(user_painpoints_data[lang_code])} painpoints ({lang_code.upper()}) from HTML")
                    except Exception as e:
                        logger.error(f"Error parsing user painpoints ({lang_code}): {e}", exc_info=True)
                        flash(ftl('recordings-error-parsing-user-painpoints-lang-error', lang=lang_code, error=str(e)), "warning")

            # User Assertions
            file_key = f'user_assertions_file{lang_suffix}'
            if file_key in request.files:
                assertions_file = request.files[file_key]
                if assertions_file.filename:
                    content = assertions_file.read().decode('utf-8')
                    try:
                        if assertions_file.filename.endswith('.json'):
                            json_data = json.loads(content)
                            user_assertions_data[lang_code] = parse_user_assertions_json(json_data)
                            logger.info(f"✓ Parsed user assertions ({lang_code.upper()}) from JSON")
                        elif assertions_file.filename.endswith('.html') or assertions_file.filename.endswith('.htm'):
                            user_assertions_data[lang_code] = parse_user_assertions_html(content)
                            logger.info(f"✓ Parsed user assertions ({lang_code.upper()}) from HTML")
                    except Exception as e:
                        logger.error(f"Error parsing user assertions ({lang_code}): {e}", exc_info=True)
                        flash(ftl('recordings-error-parsing-user-assertions-lang-error', lang=lang_code, error=str(e)), "warning")

        # Process JSON files for both languages
        import tempfile

        # Read English file content
        content_en = file_en.read().decode('utf-8')
        data_en = json.loads(content_en)
        recording_id_value = data_en.get('recording', 'Unknown')

        # Read French file content if provided
        content_fr = None
        data_fr = None
        if has_french and file_fr is not None:
            content_fr = file_fr.read().decode('utf-8')
            data_fr = json.loads(content_fr)
            # Verify both files have the same recording_id
            recording_id_fr = data_fr.get('recording', '')
            if recording_id_fr != recording_id_value:
                flash(ftl('recordings-recording-ids-don-t-match-en-en_id-fr-fr_id', en_id=recording_id_value, fr_id=recording_id_fr), "danger")
                return redirect(url_for('recordings.upload_recording'))

        # If lived experience tester selected but no auditor name, look up tester name
        if lived_experience_tester_id and not auditor_name:
            project = get_db().get_project(project_id)
            if project and project.lived_experience_testers:
                for tester in project.lived_experience_testers:
                    if tester.id == lived_experience_tester_id:
                        # Format: "Name (Disability)"
                        name = tester.name or 'Unknown'
                        disability = tester.disability_type or ''
                        auditor_name = f"{name} ({disability})" if disability else name
                        break

        # Prepare auditor info
        auditor_info: dict[str, Any] = {
            'title': title or f"Recording {recording_id_value}",
            'description': description,
            'auditor_name': auditor_name,
            'auditor_role': auditor_role,
            'test_user_account': test_user_account,
            'lived_experience_tester_id': lived_experience_tester_id,
            'test_supervisor_id': test_supervisor_id,
            'media_file_path': media_file_path,
            'key_takeaways': key_takeaways_data,
            'user_painpoints': user_painpoints_data,
            'user_assertions': user_assertions_data
        }

        # Check if recording with this recording_id already exists
        existing = get_db().get_recording_by_recording_id(recording_id_value)
        if existing:
            flash(ftl('recordings-recording-id-already-exists-please-use-a', id=recording_id_value), "danger")
            return redirect(url_for('recordings.upload_recording'))

        # Process English issues
        tmp_file_en = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='_en.json', delete=False) as tmp_file:
                tmp_file.write(content_en)
                tmp_file_en = tmp_file.name

            importer = DictaphoneImporter()
            recording, issues_en = importer.import_from_file(
                tmp_file_en,
                project_id=project_id,
                component_names=component_names,
                app_screens=app_screens,
                device_sections=device_sections,
                task_description=task_description,
                auditor_info=auditor_info,
                recording_type=recording_type,
                testing_scope=testing_scope,
                language='en'
            )

            all_issues = issues_en

            # Process French issues if provided
            issues_fr: list[RecordingIssue] = []
            if has_french:
                assert content_fr is not None
                tmp_file_fr = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', suffix='_fr.json', delete=False) as tmp_file:
                        tmp_file.write(content_fr)
                        tmp_file_fr = tmp_file.name

                    _, issues_fr = importer.import_from_file(
                        tmp_file_fr,
                        project_id=project_id,
                        component_names=component_names,
                        app_screens=app_screens,
                        device_sections=device_sections,
                        task_description=task_description,
                        auditor_info=auditor_info,
                        recording_type=recording_type,
                        testing_scope=testing_scope,
                        language='fr'
                    )
                    all_issues.extend(issues_fr)
                finally:
                    if tmp_file_fr:
                        Path(tmp_file_fr).unlink(missing_ok=True)

            # Save to database
            recording_id = get_db().create_recording(recording)
            _issue_ids = get_db().create_recording_issues_bulk(all_issues)

            # Update project's recording_ids
            project = get_db().get_project(project_id)
            if project:
                if recording_id not in project.recording_ids:
                    project.recording_ids.append(recording_id)
                    get_db().update_project(project)

            lang_detail = f"{len(issues_en)} EN" + (f", {len(issues_fr)} FR" if has_french else "")
            flash(ftl('recordings-successfully-imported-recording-id-with-count', id=recording.recording_id, count=len(all_issues), detail=lang_detail), "success")
            return redirect(url_for('recordings.view_recording', recording_id=recording_id))

        finally:
            # Clean up temp files
            if tmp_file_en:
                Path(tmp_file_en).unlink(missing_ok=True)

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON file: {e}")
        flash(ftl('recordings-invalid-json-file-error', error=str(e)), "danger")
        return redirect(url_for('recordings.upload_recording'))
    except Exception as e:
        logger.error(f"Error uploading recording: {e}", exc_info=True)
        flash(ftl('recordings-error-uploading-recording-error', error=str(e)), "danger")
        return redirect(url_for('recordings.upload_recording'))


def _run_video_pipeline(
    *,
    runner: object,
    db: object,
    recording_id: str,
    job_id: str,
) -> None:
    """Thread-pool worker: drive ``VideoRunner.run`` in a fresh event loop.

    The runner exposes an async ``run(recording_id)`` coroutine; the
    JobManager record was already created by the route, so this worker
    flips it to RUNNING / COMPLETED / FAILED in lockstep with the
    Recording's own status field (which the runner manages).

    Mirrors the PdfAuditJob loop pattern: each task_runner worker
    re-uses the same OS thread across many jobs and ``nest_asyncio``
    monkey-patches ``asyncio.run`` in some flows; build a fresh loop
    here so we never reuse a closed one. We take ``runner`` + ``db``
    as parameters because we're outside the Flask request context.
    """
    from auto_a11y.audio.runner import VideoRunner as _VideoRunner
    from auto_a11y.core.database import Database as _Database

    assert isinstance(runner, _VideoRunner)
    assert isinstance(db, _Database)

    job_manager = JobManager.get_instance(db)
    job_manager.update_job_status(
        job_id=job_id,
        status=JobStatus.RUNNING,
        progress={
            'current': 0,
            'total': 0,
            'message': 'Video pipeline started',
            'details': {'recording_id': recording_id},
        },
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        try:
            loop.run_until_complete(runner.run(recording_id))
            job_manager.update_job_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    except Exception as exc:  # noqa: BLE001 — log + record on job
        logger.exception("Video pipeline failed for %s", recording_id)
        job_manager.update_job_status(
            job_id=job_id,
            status=JobStatus.FAILED,
            error=f'{type(exc).__name__}: {exc}',
        )


@recordings_bp.route('/<recording_id>/process', methods=['POST'])
def process_recording(recording_id: str) -> Response | WerkzeugResponse:
    """Kick off the audioA11y pipeline for an ``uploaded`` Recording.

    Phase 7 of the audioA11y integration. Loads the Recording (by Mongo
    id, matching the existing ``/recordings/<recording_id>`` detail
    route), asserts ``status == "uploaded"``, flips it to ``"processing"``,
    creates a ``JobType.VIDEO_PROCESSING`` JobManager record, submits the
    work to the global :data:`task_runner`, and redirects to the detail
    page (where Phase 8 will render the live progress card).
    """
    recording = get_db().get_recording(recording_id)
    if recording is None:
        flash(ftl('recordings-recording-not-found'), 'danger')
        return redirect(url_for('recordings.list_recordings'))

    if recording.status != 'uploaded':
        flash(ftl('audio-error-already-processing'), 'warning')
        return redirect(url_for('recordings.view_recording', recording_id=recording_id))

    runner = get_video_runner()
    if runner is None:
        flash(ftl('audio-error-runner-not-configured'), 'danger')
        return redirect(url_for('recordings.view_recording', recording_id=recording_id))

    recording.status = 'processing'
    recording.started_at = datetime.now()
    get_db().update_recording(recording)

    job_manager = JobManager.get_instance(get_db())
    job_id = f"video_processing_{recording.recording_id}_{secrets.token_hex(4)}"
    job_manager.create_job(
        job_id=job_id,
        job_type=JobType.VIDEO_PROCESSING,
        project_id=recording.project_id,
        metadata={'recording_id': recording.recording_id},
    )
    task_runner.submit_task(
        func=_run_video_pipeline,
        kwargs={
            'runner': runner,
            'db': get_db(),
            'recording_id': recording.recording_id,
            'job_id': job_id,
        },
        task_id=job_id,
    )

    return redirect(url_for('recordings.view_recording', recording_id=recording_id))


@recordings_bp.route('/<recording_id>/callouts.mp4')
def download_callouts_video(recording_id: str) -> Response:
    """Stream the rendered callouts MP4.

    Phase 9 of the audioA11y integration. Only available when
    ``Recording.callouts_status == "complete"``; any other state
    returns 404. The Recording itself is keyed by Mongo id (matching
    the rest of this blueprint's routes); the on-disk slot is looked
    up by the recording's ``recording_id`` field.
    """
    rec = get_db().get_recording(recording_id)
    if rec is None or rec.callouts_status != "complete":
        abort(404)
    audio_storage = get_audio_storage()
    if audio_storage is None:
        abort(404)
    slot = audio_storage.get(rec.recording_id)
    if not slot.callouts_mp4.exists():
        abort(404)
    download_name = f"{rec.recording_id}.callouts.mp4"
    return send_file(
        slot.callouts_mp4,
        mimetype="video/mp4",
        as_attachment=False,
        download_name=download_name,
    )


@recordings_bp.route('/<recording_id>/cancel', methods=['POST'])
def cancel_recording(recording_id: str) -> WerkzeugResponse:
    """Flip a ``processing`` Recording's status to ``cancelling``.

    Phase 8 of the audioA11y integration. The running ``VideoRunner``
    polls ``Recording.status`` between pipeline stages and raises
    ``_Cancelled`` (which it maps to a final ``cancelled`` state) when
    it sees the flag flipped. This endpoint is idempotent — a second
    POST (e.g. from a double-clicked button) is a no-op, and POSTing
    against any non-``processing`` Recording is also a safe no-op so
    the UI never 4xx's on a stale page.
    """
    recording = get_db().get_recording(recording_id)
    if recording is None:
        flash(ftl('recordings-recording-not-found'), 'danger')
        return redirect(url_for('recordings.list_recordings'))

    if recording.status == 'processing':
        recording.status = 'cancelling'
        get_db().update_recording(recording)

    return redirect(url_for('recordings.view_recording', recording_id=recording_id))


@recordings_bp.route('/<recording_id>/delete', methods=['POST'])
def delete_recording(recording_id: str) -> WerkzeugResponse:
    """Delete a recording"""
    try:
        recording = get_db().get_recording(recording_id)
        if not recording:
            flash(ftl('recordings-recording-not-found'), "danger")
            return redirect(url_for('recordings.list_recordings'))

        # Remove from project's recording_ids
        if recording.project_id:
            project = get_db().get_project(recording.project_id)
            if project and recording_id in project.recording_ids:
                project.recording_ids.remove(recording_id)
                get_db().update_project(project)

        # Delete recording (will also delete related issues)
        get_db().delete_recording(recording_id)

        flash(ftl('recordings-recording-id-deleted-successfully', id=recording.recording_id), "success")
        return redirect(url_for('recordings.list_recordings'))
    except Exception as e:
        logger.error(f"Error deleting recording: {e}", exc_info=True)
        flash(ftl('recordings-error-deleting-recording-error', error=str(e)), "danger")
        return redirect(url_for('recordings.list_recordings'))


# API endpoints

@recordings_bp.route('/api/list')
@deprecated(successor="/api/v1/recordings", sunset="2026-09-01")
def api_list_recordings() -> Response | tuple[Response, int]:
    """API endpoint to list recordings"""
    try:
        project_id = request.args.get('project_id')
        recordings = get_db().get_recordings(project_id=project_id)

        return jsonify({
            'success': True,
            'recordings': [
                {
                    'id': r.id,
                    'recording_id': r.recording_id,
                    'title': r.title,
                    'auditor_name': r.auditor_name,
                    'auditor_role': r.auditor_role,
                    'recording_type': r.recording_type.value,
                    'total_issues': r.total_issues,
                    'high_impact_count': r.high_impact_count,
                    'medium_impact_count': r.medium_impact_count,
                    'low_impact_count': r.low_impact_count,
                    'duration': r.duration,
                    'recorded_date': r.recorded_date.isoformat() if r.recorded_date else None
                } for r in recordings
            ]
        })
    except Exception as e:
        logger.error(f"Error in API list recordings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@recordings_bp.route('/api/<recording_id>/issues')
@deprecated(successor="/api/v1/recordings/<recording_id>/issues", sunset="2026-09-01")
def api_recording_issues(recording_id: str) -> Response | tuple[Response, int]:
    """API endpoint to get issues for a recording"""
    try:
        recording = get_db().get_recording(recording_id)
        if not recording:
            return jsonify({'success': False, 'error': ftl('recordings-recording-not-found')}), 404

        issues = get_db().get_recording_issues_for_recording(recording.recording_id)

        return jsonify({
            'success': True,
            'issues': [
                {
                    'id': i.id,
                    'title': i.title,
                    'short_title': i.short_title,
                    'impact': i.impact.value,
                    'touchpoint': i.touchpoint,
                    'what': i.what,
                    'why': i.why,
                    'who': i.who,
                    'remediation': i.remediation,
                    'timecodes': [tc.to_dict() for tc in i.timecodes],
                    'wcag': [w.to_dict() for w in i.wcag],
                    'status': i.status
                } for i in issues
            ]
        })
    except Exception as e:
        logger.error(f"Error in API recording issues: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@recordings_bp.route('/api/issue/<issue_id>/status', methods=['POST'])
@deprecated(successor="/api/v1/recording-issues/<issue_id>", sunset="2026-09-01")
def api_update_issue_status(issue_id: str) -> Response | tuple[Response, int]:
    """API endpoint to update issue status"""
    try:
        status = request.json.get('status')
        if not status:
            return jsonify({'success': False, 'error': ftl('recordings-status-is-required')}), 400

        success = get_db().update_recording_issue_status(issue_id, status)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': ftl('recordings-failed-to-update-status')}), 500
    except Exception as e:
        logger.error(f"Error updating issue status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
