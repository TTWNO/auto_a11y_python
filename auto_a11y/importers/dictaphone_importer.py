"""
Dictaphone JSON importer for AutoA11y

Imports manual accessibility audit data from Dictaphone format into AutoA11y's
recording and issue tracking system.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from auto_a11y.models import (
    Recording, RecordingIssue, RecordingType,
    Timecode, ImpactLevel
)

if TYPE_CHECKING:
    # Imported lazily for type-checking only to avoid an import cycle:
    # ``auto_a11y.audio.runner`` imports this module, which would otherwise
    # need to import the audio storage helper to type the function below.
    from auto_a11y.audio.storage import AllocatedSlot
    from auto_a11y.core.database import Database

logger = logging.getLogger(__name__)


class DictaphoneImporter:
    """
    Import Dictaphone JSON data into AutoA11y.

    Dictaphone generates accessibility findings from audio/video recordings
    of manual audits and lived experience testing. This importer converts
    that data into AutoA11y's Recording and RecordingIssue models.
    """

    def __init__(self) -> None:
        """Initialize the importer"""
        pass

    def import_from_file(
        self,
        json_file_path: str,
        project_id: str,
        website_ids: list[str] | None = None,
        component_names: list[str] | None = None,
        app_screens: list[str] | None = None,
        device_sections: list[str] | None = None,
        task_description: str | None = None,
        auditor_info: dict[str, Any] | None = None,
        recording_type: str = "audit",
        testing_scope: dict[str, bool] | None = None,
        language: str = "en"
    ) -> tuple[Recording, list[RecordingIssue]]:
        """
        Import a Dictaphone JSON file.

        Args:
            json_file_path: Path to dictaphone JSON file
            project_id: AutoA11y project ID to attach to
            website_ids: Optional website IDs this recording covers
            component_names: Optional common component names (header, nav, footer, etc.)
            app_screens: Optional app screens/views covered
            device_sections: Optional device sections covered
            task_description: Optional task being performed
            auditor_info: Optional dict with auditor_name, auditor_role, etc.
            recording_type: Type of recording (audit, lived_experience_website, etc.)

        Returns:
            Tuple of (Recording object, List of RecordingIssue objects)

        Raises:
            FileNotFoundError: If JSON file doesn't exist
            ValueError: If JSON is invalid or missing required fields
            json.JSONDecodeError: If file is not valid JSON
        """
        # Read and parse JSON file
        file_path = Path(json_file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Dictaphone JSON file not found: {json_file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            data: dict[str, Any] | list[dict[str, Any]] = json.load(f)

        logger.info(f"Parsing Dictaphone JSON from {json_file_path}")

        # Parse the data
        recording, issues = self.parse_dictaphone_json(
            data,
            project_id=project_id,
            website_ids=website_ids or [],
            component_names=component_names or [],
            app_screens=app_screens or [],
            device_sections=device_sections or [],
            task_description=task_description,
            auditor_info=auditor_info or {},
            recording_type=recording_type,
            testing_scope=testing_scope or {},
            language=language
        )

        logger.info(f"Successfully parsed recording '{recording.recording_id}' with {len(issues)} issues")

        return recording, issues

    def parse_dictaphone_json(
        self,
        data: dict[str, Any] | list[dict[str, Any]],
        project_id: str,
        website_ids: list[str],
        component_names: list[str],
        app_screens: list[str],
        device_sections: list[str],
        task_description: str | None,
        auditor_info: dict[str, Any],
        recording_type: str,
        testing_scope: dict[str, bool],
        language: str = 'en'
    ) -> tuple[Recording, list[RecordingIssue]]:
        """
        Parse Dictaphone JSON structure into Recording and RecordingIssue objects.

        Args:
            data: Parsed JSON dict from Dictaphone (or list for Claude-generated format)
            project_id: Project ID to link to
            website_ids: Website IDs
            auditor_info: Auditor information
            recording_type: Type of recording

        Returns:
            Tuple of (Recording, List[RecordingIssue])

        Raises:
            ValueError: If required fields are missing
        """
        # Handle both formats:
        # 1. Standard format: {"recording": "ID", "issues": [...]}
        # 2. Claude format: [{"recording": "ID", "title": "...", ...}, ...]

        recording_id: str
        issues_data: list[dict[str, Any]]

        if isinstance(data, list):
            # Claude format - array of issues with recording field in each
            if not data:
                raise ValueError("Empty issues array")

            # Extract recording ID from first item
            if 'recording' not in data[0]:
                raise ValueError("Missing 'recording' field in first issue")

            recording_id = data[0]['recording']

            # Convert to issues format (remove 'recording' field from each issue)
            issues_data = []
            for item in data:
                issue = {k: v for k, v in item.items() if k != 'recording'}
                issues_data.append(issue)
        else:
            # Standard format - object with recording and issues fields
            if 'recording' not in data:
                raise ValueError("Missing required field: 'recording'")
            if 'issues' not in data:
                raise ValueError("Missing required field: 'issues'")

            recording_id = data['recording']
            issues_data = data['issues']

        # Parse recording type
        try:
            recording_type_enum = RecordingType(recording_type)
        except ValueError:
            logger.warning(f"Invalid recording_type '{recording_type}', defaulting to 'audit'")
            recording_type_enum = RecordingType.AUDIT

        # Count issues by impact
        high_count = sum(1 for issue in issues_data if issue.get('impact', '').lower() in ['high', 'critical'])
        medium_count = sum(1 for issue in issues_data if issue.get('impact', '').lower() in ['medium', 'moderate'])
        low_count = sum(1 for issue in issues_data if issue.get('impact', '').lower() == 'low')

        # Calculate total duration if we can extract it from timecodes
        total_duration = self._calculate_total_duration(issues_data)

        # Create Recording object
        recording = Recording(
            recording_id=recording_id,
            title=auditor_info.get('title', f"Recording {recording_id}"),
            description=auditor_info.get('description'),
            media_file_path=auditor_info.get('media_file_path'),
            duration=total_duration,
            recorded_date=auditor_info.get('recorded_date'),
            auditor_name=auditor_info.get('auditor_name'),
            auditor_role=auditor_info.get('auditor_role'),
            test_user_account=auditor_info.get('test_user_account'),
            recording_type=recording_type_enum,
            lived_experience_tester_id=auditor_info.get('lived_experience_tester_id'),
            test_supervisor_id=auditor_info.get('test_supervisor_id'),
            project_id=project_id,
            website_ids=website_ids,
            component_names=component_names,
            app_screens=app_screens,
            device_sections=device_sections,
            task_description=task_description,
            testing_scope=testing_scope,
            key_takeaways=auditor_info.get('key_takeaways', {}),
            user_painpoints=auditor_info.get('user_painpoints', {}),
            user_assertions=auditor_info.get('user_assertions', {}),
            total_issues=len(issues_data),
            high_impact_count=high_count,
            medium_impact_count=medium_count,
            low_impact_count=low_count,
            tags=auditor_info.get('tags', []),
            notes=auditor_info.get('notes')
        )

        # Parse issues
        issues: list[RecordingIssue] = []
        for issue_data in issues_data:
            try:
                parsed_issue = RecordingIssue.from_dictaphone_issue(
                    issue_data,
                    recording_id=recording_id,
                    project_id=project_id,
                    language=language
                )
                # Add component references from recording
                parsed_issue.website_ids = website_ids
                parsed_issue.component_names = component_names
                parsed_issue.app_screens = app_screens
                parsed_issue.device_sections = device_sections
                parsed_issue.task_description = task_description

                # Infer touchpoint from WCAG criteria if not present
                if not parsed_issue.touchpoint:
                    parsed_issue.touchpoint = self._infer_touchpoint(issue_data)

                issues.append(parsed_issue)
            except Exception as e:
                logger.error(f"Error parsing issue '{issue_data.get('title', 'unknown')}': {e}")
                # Continue with other issues

        return recording, issues

    def _calculate_total_duration(self, issues_data: list[dict[str, Any]]) -> str | None:
        """
        Calculate total recording duration from issue timecodes.
        Returns the maximum end time found across all timecodes.

        Args:
            issues_data: List of issue dicts from Dictaphone JSON

        Returns:
            Duration string in HH:MM:SS format, or None if cannot determine
        """
        max_seconds = 0.0

        for issue in issues_data:
            timecodes = issue.get('timecodes', [])
            for tc in timecodes:
                try:
                    # Create Timecode object to parse time
                    timecode = Timecode(
                        start=tc.get('start', '00:00:00'),
                        end=tc.get('end', '00:00:00'),
                        duration=tc.get('duration', '00:00:00')
                    )
                    end_seconds = timecode.end_seconds
                    if end_seconds > max_seconds:
                        max_seconds = end_seconds
                except Exception as e:
                    logger.debug(f"Could not parse timecode: {e}")
                    continue

        if max_seconds > 0:
            # Convert back to HH:MM:SS format
            hours = int(max_seconds // 3600)
            minutes = int((max_seconds % 3600) // 60)
            seconds = int(max_seconds % 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        return None

    def _infer_touchpoint(self, issue_data: dict[str, Any]) -> str:
        """
        Infer accessibility touchpoint/category from issue details.

        Args:
            issue_data: Issue dict from Dictaphone JSON

        Returns:
            Touchpoint string (e.g., "Landmarks", "Navigation", "Forms")
        """
        title = issue_data.get('title', '').lower()
        short_title = issue_data.get('short_title', '').lower()

        # Mapping of keywords to touchpoints
        keyword_mapping: dict[str, str] = {
            'landmark': 'Landmarks',
            'aside': 'Landmarks',
            'navigation': 'Navigation',
            'nav': 'Navigation',
            'menu': 'Navigation',
            'dropdown': 'Navigation',
            'search': 'Forms',
            'input': 'Forms',
            'form': 'Forms',
            'field': 'Forms',
            'label': 'Forms',
            'button': 'Forms',
            'contrast': 'Color Contrast',
            'color': 'Color Contrast',
            'focus': 'Focus Management',
            'heading': 'Headings',
            'h1': 'Headings',
            'h2': 'Headings',
            'h3': 'Headings',
            'image': 'Images',
            'img': 'Images',
            'svg': 'Images',
            'logo': 'Images',
            'alt': 'Images',
            'link': 'Links',
            'aria': 'ARIA',
            'role': 'ARIA',
            'list': 'Lists',
            'table': 'Tables',
            'font': 'Typography',
            'text': 'Typography',
            'skip': 'Page Structure',
            'title': 'Page Structure'
        }

        # Check title and short_title for keywords
        search_text = f"{title} {short_title}"
        for keyword, touchpoint in keyword_mapping.items():
            if keyword in search_text:
                return touchpoint

        # Check WCAG criteria for hints
        wcag_list: list[dict[str, Any]] = issue_data.get('wcag', [])
        if wcag_list:
            first_criteria: str = wcag_list[0].get('criteria', '')
            if first_criteria.startswith('1.1'):
                return 'Images'
            elif first_criteria.startswith('1.3'):
                return 'Page Structure'
            elif first_criteria.startswith('1.4'):
                return 'Color Contrast'
            elif first_criteria.startswith('2.1'):
                return 'Keyboard'
            elif first_criteria.startswith('2.4'):
                return 'Navigation'
            elif first_criteria.startswith('3.3'):
                return 'Forms'
            elif first_criteria.startswith('4.1'):
                return 'ARIA'

        # Default fallback
        return 'General'

    def map_dictaphone_impact(self, impact_str: str) -> ImpactLevel:
        """
        Map Dictaphone impact levels to AutoA11y ImpactLevel enum.

        Args:
            impact_str: Impact string from Dictaphone (e.g., "high", "medium", "low")

        Returns:
            ImpactLevel enum value
        """
        impact_lower = impact_str.lower() if impact_str else 'medium'

        mapping: dict[str, ImpactLevel] = {
            'low': ImpactLevel.LOW,
            'minor': ImpactLevel.LOW,
            'medium': ImpactLevel.MEDIUM,
            'moderate': ImpactLevel.MEDIUM,
            'high': ImpactLevel.HIGH,
            'critical': ImpactLevel.HIGH,
            'serious': ImpactLevel.HIGH
        }

        return mapping.get(impact_lower, ImpactLevel.MEDIUM)

    def validate_dictaphone_json(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """
        Validate Dictaphone JSON structure.

        Args:
            data: Parsed JSON dict

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check required top-level fields
        if 'recording' not in data:
            return False, "Missing required field: 'recording'"

        if 'issues' not in data:
            return False, "Missing required field: 'issues'"

        if not isinstance(data['issues'], list):
            return False, "'issues' must be an array"

        # Validate each issue has required fields
        issues_list: list[dict[str, Any]] = cast(list[dict[str, Any]], data['issues'])
        for i, issue in enumerate(issues_list):
            if 'title' not in issue:
                return False, f"Issue {i} missing required field: 'title'"

            if 'impact' not in issue:
                return False, f"Issue {i} missing required field: 'impact'"

            if 'timecodes' in issue and not isinstance(issue['timecodes'], list):
                return False, f"Issue {i}: 'timecodes' must be an array"

            if 'wcag' in issue and not isinstance(issue['wcag'], list):
                return False, f"Issue {i}: 'wcag' must be an array"

        return True, None


# === Phase 6 (audioA11y) — pipeline-output ingestion =====================
#
# ``import_pipeline_output`` is the bridge between the on-disk JSON
# produced by ``auto_a11y.audio.pipeline.run_pipeline`` and the existing
# Mongo collections (``recordings`` + ``recording_issues``). It's a free
# function (not a method) so :class:`auto_a11y.audio.runner.VideoRunner`
# can call it without instantiating ``DictaphoneImporter``.
#
# Output layout (set by ``AllocatedSlot.json_path``):
#
#   <slot>/json/<recording_id>.issues.json            (English issues)
#   <slot>/json/<recording_id>.issues.fr.json         (French issues)
#   <slot>/json/<recording_id>.painpoints.json        (etc.)
#   <slot>/json/<recording_id>.painpoints.fr.json
#   <slot>/json/<recording_id>.takeaways[.fr].json
#   <slot>/json/<recording_id>.assertions[.fr].json
#
# A missing file is treated as "nothing to import for this slot" — a
# warning is logged but the runner does not fail. A file that exists
# but fails to parse logs an error and is skipped likewise; the runner
# completes the rest of the languages / kinds rather than aborting
# everything because Claude returned malformed JSON for one combo.


def _read_pipeline_json(path: Path) -> dict[str, object] | None:
    """Read one Claude-emitted JSON file. Returns ``None`` for missing / bad.

    Catches parse and IO errors so a single mangled output doesn't
    bring down the import for the other (kind, lang) combos.
    """
    if not path.exists():
        logger.info("import_pipeline_output: missing %s; skipping", path)
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            payload: object = json.load(f)
    except OSError as exc:
        logger.error("import_pipeline_output: read failed for %s: %s", path, exc)
        return None
    except json.JSONDecodeError as exc:
        logger.error("import_pipeline_output: malformed JSON in %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        logger.error(
            "import_pipeline_output: %s payload is %s, not an object; skipping",
            path,
            type(payload).__name__,
        )
        return None
    # ``json.loads`` always produces ``str``-keyed dicts for JSON objects;
    # the runtime check above is ``isinstance(payload, dict)``. Cast to
    # the canonical ``dict[str, object]`` shape so downstream typing is
    # explicit (matches the ``_is_str_obj_dict`` pattern in audio/).
    return cast(dict[str, object], payload)


def _ingest_issues_file(
    db: "Database",
    recording: Recording,
    path: Path,
    language: str,
) -> list[RecordingIssue]:
    """Parse an ``issues.json`` file → RecordingIssues → DB.

    Reuses :meth:`RecordingIssue.from_dictaphone_issue` so we get the
    same parsing semantics as the manual JSON-upload route. Returns the
    list of issues created (empty on missing file or parse failure).
    """
    payload = _read_pipeline_json(path)
    if payload is None:
        return []
    issues_data: object = payload.get("issues")
    if not isinstance(issues_data, list):
        logger.error(
            "import_pipeline_output: %s 'issues' is not a list; skipping",
            path,
        )
        return []

    created: list[RecordingIssue] = []
    # ``issues_data`` is ``list[object]`` after the isinstance check; we
    # narrow each element to ``dict[str, Any]`` (the shape
    # ``RecordingIssue.from_dictaphone_issue`` expects) before passing it on.
    issues_list: list[object] = cast(list[object], issues_data)
    for issue_data in issues_list:
        if not isinstance(issue_data, dict):
            logger.warning(
                "import_pipeline_output: skipping non-object issue in %s", path
            )
            continue
        issue_dict: dict[str, Any] = cast(dict[str, Any], issue_data)
        try:
            parsed = RecordingIssue.from_dictaphone_issue(
                issue_dict,
                recording_id=recording.recording_id,
                project_id=recording.project_id or "",
                language=language,
            )
            parsed.website_ids = list(recording.website_ids)
            parsed.component_names = list(recording.component_names)
            parsed.app_screens = list(recording.app_screens)
            parsed.device_sections = list(recording.device_sections)
            parsed.task_description = recording.task_description
            created.append(parsed)
        except Exception as exc:  # noqa: BLE001 — keep going on one bad issue
            logger.error(
                "import_pipeline_output: failed to parse one issue in %s: %s",
                path, exc,
            )
    if created:
        db.create_recording_issues_bulk(created)
        logger.info(
            "import_pipeline_output: inserted %d %s issues from %s",
            len(created), language, path.name,
        )
    return created


def _ingest_content_kind(
    payload_key: str,
    recording_attr: dict[str, list[dict[str, Any]]],
    path: Path,
    language: str,
) -> bool:
    """Read painpoints / takeaways / assertions into the Recording dict.

    ``payload_key`` is the top-level JSON key Claude emits
    (``"pain_points"``, ``"takeaways"``, ``"assertions"``);
    ``recording_attr`` is the per-language dict on the Recording that
    gets mutated in place. Returns True on a successful read so the
    caller knows to persist the Recording.
    """
    payload = _read_pipeline_json(path)
    if payload is None:
        return False
    items: object = payload.get(payload_key)
    if not isinstance(items, list):
        logger.error(
            "import_pipeline_output: %s '%s' is not a list; skipping",
            path, payload_key,
        )
        return False
    # Coerce to ``list[dict[str, Any]]`` — Claude is instructed to emit
    # objects only; defensively drop anything else rather than letting
    # a stray scalar poison the in-memory shape.
    items_list: list[object] = cast(list[object], items)
    typed_items: list[dict[str, Any]] = []
    for item in items_list:
        if isinstance(item, dict):
            typed_items.append(cast(dict[str, Any], item))
    recording_attr[language] = typed_items
    return True


def import_pipeline_output(
    db: "Database",
    slot: "AllocatedSlot",
    recording: Recording,
) -> list[RecordingIssue]:
    """Import the audio pipeline's JSON outputs into Mongo.

    Walks ``recording.analysis_languages`` × the four analysis kinds
    (``issues``, ``painpoints``, ``takeaways``, ``assertions``):

    - **issues**: inserts into the ``recording_issues`` collection via
      :meth:`Database.create_recording_issues_bulk`. The existing
      :meth:`RecordingIssue.from_dictaphone_issue` parser handles
      timecodes / WCAG / impact mapping.
    - **painpoints / takeaways / assertions**: written onto the
      Recording itself (``user_painpoints[lang]`` / ``key_takeaways[lang]``
      / ``user_assertions[lang]``). The Recording is persisted via
      :meth:`Database.update_recording` once at the end if any content
      kind was populated.

    Returns the list of :class:`RecordingIssue` objects created (empty
    if no issues files were present).

    Missing files: logged at INFO level and skipped. Files that fail to
    parse: logged at ERROR level and skipped; **does not raise** —
    runner-level failure handling is reserved for unrecoverable errors.
    """
    all_issues: list[RecordingIssue] = []
    any_content_updated = False

    for lang in recording.analysis_languages:
        # Issues — into the recording_issues collection.
        issues_path = slot.json_path(kind="issues", lang=lang)
        all_issues.extend(_ingest_issues_file(db, recording, issues_path, lang))

        # Content kinds — onto the Recording's per-language dicts.
        painpoints_path = slot.json_path(kind="painpoints", lang=lang)
        if _ingest_content_kind(
            payload_key="pain_points",
            recording_attr=recording.user_painpoints,
            path=painpoints_path,
            language=lang,
        ):
            any_content_updated = True

        takeaways_path = slot.json_path(kind="takeaways", lang=lang)
        if _ingest_content_kind(
            payload_key="takeaways",
            recording_attr=recording.key_takeaways,
            path=takeaways_path,
            language=lang,
        ):
            any_content_updated = True

        assertions_path = slot.json_path(kind="assertions", lang=lang)
        if _ingest_content_kind(
            payload_key="assertions",
            recording_attr=recording.user_assertions,
            path=assertions_path,
            language=lang,
        ):
            any_content_updated = True

    # Recompute the impact tallies from the freshly inserted issues so
    # the recording-list view shows correct counts.
    if all_issues:
        recording.total_issues = len(all_issues)
        recording.high_impact_count = sum(
            1 for i in all_issues if i.impact == ImpactLevel.HIGH
        )
        recording.medium_impact_count = sum(
            1 for i in all_issues if i.impact == ImpactLevel.MEDIUM
        )
        recording.low_impact_count = sum(
            1 for i in all_issues if i.impact == ImpactLevel.LOW
        )
        any_content_updated = True

    if any_content_updated:
        db.update_recording(recording)

    return all_issues
