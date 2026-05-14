"""
RESTful API routes
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user
from werkzeug.datastructures import FileStorage
from auto_a11y.models import (
    Page,
    PageStatus,
    Project,
    ProjectStatus,
    ScriptStateDefinition,
    TestStateMatrix,
    Violation,
)
from auto_a11y.models.app_user import UserRole
from auto_a11y.pdf.storage import PdfStorage
from auto_a11y.web.routes.auth import project_role_required
from auto_a11y.core.job_manager import JobManager, JobStatus, JobType
from auto_a11y.web.api.openapi.document import document
from auto_a11y.web.api.schemas.common import Empty
from auto_a11y.web.api.schemas.projects import (
    MessageOut,
    ProjectCreatedOut,
    ProjectDictOut,
    ProjectIn,
    ProjectListOut,
    ProjectPatch,
)
from auto_a11y.web.api.schemas.websites import (
    ScrapingConfigModel,
    WebsiteIn,
    WebsiteListOut,
    WebsiteOut,
    WebsitePatch,
    WebsitePut,
)
from auto_a11y.web.api.schemas.pages import (
    DiscoveredPageIn,
    DiscoveredPageListOut,
    DiscoveredPageOut,
    DiscoveredPagePatch,
    DiscoveredPagePut,
    PageIn,
    PageListOut,
    PageMatrixIn,
    PageMatrixOut,
    PageOut,
    PagePatch,
    PagePut,
    PageViolationsOut,
    ScriptStateDefinitionOut,
)
from auto_a11y.web.api.schemas.recordings import (
    RecordingContentOut,
    RecordingContentPatch,
    RecordingIssueListOut,
    RecordingIssueOut,
    RecordingListOut,
    RecordingOut,
    RecordingPatch,
    RecordingUploadIn,
)
from auto_a11y.web.api.schemas.reports import (
    JobRestartOut,
    PageReportIn,
    ProjectReportIn,
    ReportCreatedOut,
    ReportIn,
    WebsiteReportIn,
)
from auto_a11y.web.api.schemas.schedules import (
    PresetConfigOut,
    ScheduleIn,
    ScheduleListOut,
    ScheduleOut,
    SchedulePatch,
    SchedulePreviewOut,
    ScheduleRunOut,
    ScheduleTestConfigOut,
)
from auto_a11y.web.api.schemas.scripts import (
    ExecutionStatsOut,
    ScriptIn,
    ScriptListOut,
    ScriptOut,
    ScriptPatch,
    ScriptPut,
    ScriptStepOut,
    ScriptTestRunIn,
    ScriptTestRunOut,
    ScriptValidationOut,
)
from auto_a11y.web.api.schemas.test_runs import (
    PageTestRunCancelOut,
    PageTestRunIn,
    PageTestRunLatestOut,
    PageTestRunStartedOut,
    PageTestSessionOut,
    PageTestSessionStateOut,
    PageTestSessionsOut,
    PageTestStatesOut,
    ProjectTestRunIn,
    ProjectTestRunStartedOut,
    ProjectTestRunWebsiteHandleOut,
    TestResultCompareIn,
    TestResultCompareOut,
    TestResultListOut,
    TestResultOut,
    TestResultStatesOut,
    TestStateEntryOut,
    WebsiteDiscoveryIn,
    WebsiteDiscoveryStartedOut,
    WebsiteTestRunIn,
    WebsiteTestRunStartedOut,
)
from auto_a11y.web.typed_app import get_db, get_app_config, get_test_config
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
api_bp = Blueprint('api', __name__)


# Fixture Test Status API

@api_bp.route('/fixture-tests/status', methods=['GET'])
def get_fixture_test_status() -> tuple[Response, int] | Response:
    """Get fixture test status for all tests"""
    try:
        # Get test configuration
        test_config = get_test_config()

        # Get all test statuses
        statuses = test_config.get_all_test_statuses()
        
        # Get fixture run summary
        summary = None
        if test_config.fixture_validator:
            summary = test_config.fixture_validator.get_fixture_run_summary()
        
        # Get passing tests
        passing_tests: set[str] = set()
        if test_config.fixture_validator:
            passing_tests = test_config.fixture_validator.get_passing_tests()

        # Calculate category counts
        all_pass_count = 0
        partial_pass_count = 0
        all_fail_count = 0

        for _error_code, status in statuses.items():
            category = status.get('status_category', 'all_fail')
            if category == 'all_pass':
                all_pass_count += 1
            elif category == 'partial_pass':
                partial_pass_count += 1
            else:
                all_fail_count += 1

        return jsonify({
            'success': True,
            'debug_mode': test_config.debug_mode,
            'fixture_run_summary': summary,
            'passing_tests': list(passing_tests),
            'test_statuses': statuses,
            'total_tests': len(statuses),
            'passing_count': len(passing_tests),
            'all_pass_count': all_pass_count,
            'partial_pass_count': partial_pass_count,
            'all_fail_count': all_fail_count
        })
    except Exception as e:
        logger.error(f"Error getting fixture test status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/fixture-tests/check/<error_code>', methods=['GET'])
def check_test_availability(error_code: str) -> tuple[Response, int] | Response:
    """Check if a specific test is available based on fixture status"""
    try:
        test_config = get_test_config()

        # Get fixture status for this test
        status = test_config.get_test_fixture_status(error_code)
        
        return jsonify({
            'success': True,
            'error_code': error_code,
            'available': status.get('available', False),
            'passed_fixture': status.get('passed_fixture', False),
            'debug_override': status.get('debug_override', False),
            'fixture_path': status.get('fixture_path', ''),
            'tested_at': status.get('tested_at')
        })
    except Exception as e:
        logger.error(f"Error checking test availability for {error_code}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Projects API

def _project_to_jsonable_dict(project: Project) -> dict[str, object]:
    """Convert ``project.to_dict()`` into a JSON-serializable dict.

    ``Project.to_dict()`` returns an ``ObjectId`` for the ``_id`` key and
    ``datetime`` objects for timestamps. ``ObjectId`` is not natively
    JSON-serialisable (neither in Pydantic's ``model_dump(mode='json')``
    nor in stdlib ``json``); the legacy handler relied on whatever the
    frontend tolerated. We stringify the ``ObjectId`` here so the
    Pydantic models can serialise the payload without ad-hoc encoders.
    Timestamps stay as ``datetime`` -- Pydantic emits them as ISO 8601
    strings on dump.
    """
    raw = project.to_dict()
    if '_id' in raw and raw['_id'] is not None:
        raw['_id'] = str(raw['_id'])
    return raw


@api_bp.route('/projects', methods=['GET'])
@document(
    response_200=ProjectListOut,
    errors=[400, 401],
    tags=["Projects"],
    summary="List projects",
    description=(
        "Returns the projects visible to the caller (member-of-project "
        "filtering for non-superadmins). Pagination uses page/limit; "
        "``total`` reflects the unfiltered project count."
    ),
)
def get_projects() -> tuple[ProjectListOut, int] | tuple[Response, int] | Response:
    """Get all projects"""
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 20))
    status = request.args.get('status')

    skip = (page - 1) * limit

    if current_user.is_authenticated and not getattr(current_user, 'is_superadmin', False):
        projects = get_db().get_projects_for_user(str(current_user.get_id()))
        if status:
            try:
                status_enum = ProjectStatus(status)
                projects = [p for p in projects if p.status == status_enum]
            except ValueError:
                return jsonify({'error': 'Invalid status value'}), 400
    elif status:
        try:
            status_enum = ProjectStatus(status)
            projects = get_db().get_projects(status=status_enum, limit=limit, skip=skip)
        except ValueError:
            return jsonify({'error': 'Invalid status value'}), 400
    else:
        projects = get_db().get_projects(limit=limit, skip=skip)

    return ProjectListOut.model_validate({
        'projects': [_project_to_jsonable_dict(p) for p in projects],
        'pagination': {
            'page': page,
            'limit': limit,
            'total': get_db().projects.count_documents({}),
        },
    }), 200


@api_bp.route('/projects', methods=['POST'])
@document(
    request=ProjectIn,
    response_201=ProjectCreatedOut,
    errors=[400, 401, 409],
    tags=["Projects"],
    summary="Create a project",
    description=(
        "Creates a new project. The caller is auto-added as a project "
        "admin if authenticated. Returns ``{id, message}`` -- not the "
        "full project resource (wire-compat with the legacy handler)."
    ),
)
def create_project(body: ProjectIn) -> tuple[ProjectCreatedOut, int] | tuple[Response, int]:
    """Create new project"""
    # Check if project exists
    existing = get_db().projects.find_one({'name': body.name})
    if existing:
        return jsonify({'error': f'Project {body.name} already exists'}), 409

    project = Project(
        name=body.name,
        description=body.description if body.description is not None else '',
        status=ProjectStatus.ACTIVE,
        config=dict(body.config) if body.config is not None else {},
    )

    project_id = get_db().create_project(project)
    # Auto-add creator as project admin
    if current_user.is_authenticated:
        admin_group = get_db().get_group_by_name('Admin')
        get_db().add_project_member(
            project_id, str(current_user.get_id()), [admin_group.id] if admin_group and admin_group.id else []
        )

    return ProjectCreatedOut(
        id=project_id,
        message='Project created successfully',
    ), 201


@api_bp.route('/projects/<project_id>', methods=['GET'])
@document(
    response_200=ProjectDictOut,
    errors=[401, 403, 404],
    tags=["Projects"],
    summary="Get a project by ID",
    description=(
        "Returns the project payload (mirroring ``Project.to_dict()``) "
        "with a top-level ``statistics`` block from "
        "``Database.get_project_stats``."
    ),
)
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def get_project(project_id: str) -> tuple[ProjectDictOut, int] | tuple[Response, int]:
    """Get project by ID"""
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    storage = PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))
    stats = get_db().get_project_stats(project_id, pdf_storage=storage)

    payload: dict[str, object] = _project_to_jsonable_dict(project)
    payload['statistics'] = stats

    return ProjectDictOut.model_validate(payload), 200


@api_bp.route('/projects/<project_id>', methods=['PUT'])
@document(
    request=ProjectPatch,
    response_200=MessageOut,
    errors=[400, 401, 403, 404, 500],
    tags=["Projects"],
    summary="Update a project",
    description=(
        "Partial update -- only fields present in the body are applied. "
        "Returns ``{message}`` (wire-compat with the legacy handler)."
    ),
)
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def update_project(
    project_id: str, body: ProjectPatch
) -> tuple[MessageOut, int] | tuple[Response, int]:
    """Update project"""
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.status is not None:
        try:
            project.status = ProjectStatus(body.status)
        except ValueError:
            return jsonify({'error': 'Invalid status value'}), 400
    if body.config is not None:
        project.config.update(body.config)

    if get_db().update_project(project):
        return MessageOut(message='Project updated successfully'), 200
    return jsonify({'error': 'Failed to update project'}), 500


@api_bp.route('/projects/<project_id>', methods=['DELETE'])
@document(
    response_204=Empty,
    errors=[401, 403, 404, 500],
    tags=["Projects"],
    summary="Delete a project",
    description=(
        "Deletes the project. The legacy handler returned status 204 "
        "with a message body; per HTTP semantics 204 has no body so "
        "the message was silently dropped. This handler returns "
        "``204 No Content`` with an empty body -- byte-identical on the "
        "wire."
    ),
)
@project_role_required(UserRole.ADMIN)
def delete_project(project_id: str) -> tuple[Empty, int] | tuple[Response, int]:
    """Delete project"""
    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    if get_db().delete_project(project_id):
        return Empty(), 204
    return jsonify({'error': 'Failed to delete project'}), 500


# Websites API

# Website CRUD endpoints moved to the REST block at the bottom of this
# file (search for "Websites (REST shape — uses the @api_endpoint
# scaffolding)"). The legacy stubs at this position were never wired
# into the frontend and used a different error envelope from the rest of
# /api/v1; consolidating into the proper RFC 7807 shape keeps the
# /api/v1/websites surface consistent.


# Pages API

# Page CRUD endpoints moved to the REST block at the bottom of this file
# (search for "Pages (REST shape — uses the @api_endpoint scaffolding)").
# The legacy stubs at this position were never wired into the frontend
# and lacked auth guards on list/create — consolidating into the
# `@api_endpoint` shape closes that gap and adds PUT/PATCH/DELETE.


@api_bp.route('/pages/<page_id>/test', methods=['POST'])
@api_bp.route('/pages/<page_id>/test-runs', methods=['POST'])
@document(
    request=PageTestRunIn,
    response_202=PageTestRunStartedOut,
    errors=[401, 403, 404, 503],
    tags=["Test runs"],
    summary="Queue a single-page test run",
    description=(
        "Queues an accessibility test run for a single page. Both "
        "``POST /api/v1/pages/<id>/test`` (legacy alias) and the "
        "canonical ``POST /api/v1/pages/<id>/test-runs`` route here. "
        "Returns 202 with the queued task's id; the worker runs the "
        "test in the background. 503 is returned when "
        "``BROWSER_MODE='disabled'`` or ``'remote'`` rejects the "
        "request at the server."
    ),
)
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def test_page(
    page_id: str, body: PageTestRunIn
) -> tuple[PageTestRunStartedOut, int] | tuple[Response, int]:
    """Queue an accessibility test run for a page."""
    from auto_a11y.core.test_run_service import (
        BrowserDisabledError,
        BrowserRemoteError,
        PageNotFoundError,
        start_page_test_run,
    )

    enable_multi_state: bool = (
        body.enable_multi_state if body.enable_multi_state is not None else True
    )

    try:
        handle = start_page_test_run(
            get_db(),
            get_app_config(),
            page_id,
            enable_multi_state=enable_multi_state,
            website_user_id=body.website_user_id,
        )
    except BrowserDisabledError as exc:
        return jsonify({'error': str(exc)}), 503
    except BrowserRemoteError as exc:
        return jsonify({'error': str(exc)}), 503
    except PageNotFoundError:
        return jsonify({'error': 'Page not found'}), 404

    return PageTestRunStartedOut(
        job_id=handle.job_id,
        page_id=handle.page_id,
        multi_state=handle.multi_state,
        status='queued',
        message='Test job queued successfully',
    ), 202


# Test Results API

@api_bp.route('/test-results/<result_id>', methods=['GET'])
@document(
    response_200=TestResultOut,
    errors=[404],
    tags=["Test results"],
    summary="Get a single test result by ID",
    description=(
        "Returns the test result document for ``result_id``. The body "
        "is the full :meth:`TestResult.to_dict` payload — nested "
        "violation / AI-finding / page-state objects are emitted "
        "as the dataclass dumps them; downstream §5.x work will model "
        "those nested types."
    ),
)
def get_test_result(
    result_id: str,
) -> tuple[Response, int] | Response:
    """Get test result by ID"""
    result = get_db().get_test_result(result_id)
    if not result:
        return jsonify({'error': 'Test result not found'}), 404

    # The @document decorator serialises BaseModel returns via jsonify;
    # this endpoint returns the raw dict directly (the payload IS the
    # body, not nested under a "payload" key) to preserve the legacy
    # wire shape byte-for-byte.
    return jsonify(result.to_dict())


@api_bp.route('/pages/<page_id>/test-results', methods=['GET'])
@document(
    response_200=TestResultListOut,
    errors=[401, 403, 404],
    tags=["Test results"],
    summary="List a page's test results",
    description=(
        "Returns every stored test result for ``page_id`` in a "
        "``{results: [...]}`` envelope. Each item is the full "
        ":meth:`TestResult.to_dict` payload."
    ),
)
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT)
def get_page_test_results(
    page_id: str,
) -> tuple[TestResultListOut, int] | tuple[Response, int] | Response:
    """Get test results for page"""
    page = get_db().get_page(page_id)
    if not page:
        return jsonify({'error': 'Page not found'}), 404

    results = get_db().get_test_results(page_id=page_id)

    return TestResultListOut(
        results=[r.to_dict() for r in results],
    ), 200


# Batch Operations

@api_bp.route('/websites/<website_id>/discover', methods=['POST'])
@api_bp.route('/websites/<website_id>/discoveries', methods=['POST'])
@document(
    request=WebsiteDiscoveryIn,
    response_202=WebsiteDiscoveryStartedOut,
    errors=[401, 403, 404],
    tags=["Test runs"],
    summary="Queue a website discovery crawl",
    description=(
        "Queues a page-discovery crawl for a website. Both the legacy "
        "``/discover`` URL and the canonical ``/discoveries`` URL "
        "route here. ``max_pages`` is coerced to a positive integer "
        "by the server (non-positive or non-int values silently "
        "degrade to unbounded). Both ``project_user_ids`` and "
        "``website_user_ids`` are accepted for backwards-compat; the "
        "handler reads ``project_user_ids`` first."
    ),
)
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def discover_pages(
    website_id: str, body: WebsiteDiscoveryIn
) -> tuple[WebsiteDiscoveryStartedOut, int] | tuple[Response, int]:
    """Queue a page-discovery crawl for a website."""
    from auto_a11y.core.test_run_service import (
        WebsiteNotFoundError,
        start_website_discovery,
    )
    from auto_a11y.web.typed_app import get_pdf_runner as _get_pdf_runner

    max_pages: int | None = body.max_pages
    if max_pages is not None and max_pages <= 0:
        max_pages = None

    # ``project_user_ids`` wins over ``website_user_ids`` (legacy
    # behavior). ``None`` flows through to ``start_website_discovery``
    # which means "guest crawl only".
    user_ids_raw: list[str] | str | None = (
        body.project_user_ids if body.project_user_ids is not None else body.website_user_ids
    )

    try:
        handle = start_website_discovery(
            get_db(),
            get_app_config(),
            website_id,
            max_pages=max_pages,
            project_user_ids=user_ids_raw,
            pdf_runner=_get_pdf_runner(),
        )
    except WebsiteNotFoundError:
        return jsonify({'error': 'Website not found'}), 404

    return WebsiteDiscoveryStartedOut(
        job_id=handle.job_id,
        website_id=handle.website_id,
        max_pages=handle.max_pages,
        user_count=handle.user_count,
        status='started',
        message='Page discovery started',
    ), 202


@api_bp.route('/websites/<website_id>/test', methods=['POST'])
@api_bp.route('/websites/<website_id>/test-runs', methods=['POST'])
@document(
    request=WebsiteTestRunIn,
    response_202=WebsiteTestRunStartedOut,
    errors=[400, 401, 403, 404],
    tags=["Test runs"],
    summary="Queue a website-wide batch test",
    description=(
        "Queues a batch accessibility test run for every page on a "
        "website. Both the legacy ``/test`` URL and the canonical "
        "``/test-runs`` URL route here. Returns 400 when no eligible "
        "pages exist (``untested_only`` filters to DISCOVERED-status "
        "pages only)."
    ),
)
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def test_website(
    website_id: str, body: WebsiteTestRunIn
) -> tuple[WebsiteTestRunStartedOut, int] | tuple[Response, int]:
    """Queue a batch test run for every page on a website."""
    from auto_a11y.core.test_run_service import (
        NoPagesToTestError,
        WebsiteNotFoundError,
        start_website_test_run,
    )
    from auto_a11y.web.typed_app import get_pdf_runner as _get_pdf_runner

    max_pages: int | None = body.max_pages
    if max_pages is not None and max_pages <= 0:
        max_pages = None

    untested_only: bool = bool(body.untested_only)

    user_ids_raw: list[str] | str | None = (
        body.project_user_ids if body.project_user_ids is not None else body.website_user_ids
    )

    try:
        handle = start_website_test_run(
            get_db(),
            get_app_config(),
            website_id,
            project_user_ids=user_ids_raw,
            max_pages=max_pages,
            untested_only=untested_only,
            pdf_runner=_get_pdf_runner(),
        )
    except WebsiteNotFoundError:
        return jsonify({'error': 'Website not found'}), 404
    except NoPagesToTestError:
        return jsonify({'error': 'No pages to test'}), 400

    return WebsiteTestRunStartedOut(
        job_id=handle.job_id,
        website_id=handle.website_id,
        pages_queued=handle.pages_queued,
        user_count=handle.user_count,
        total_tests=handle.total_tests,
        status='queued',
        message=f'Batch testing queued for {handle.pages_queued} pages',
    ), 202


@api_bp.route('/projects/<project_id>/test-runs', methods=['POST'])
@document(
    request=ProjectTestRunIn,
    response_202=ProjectTestRunStartedOut,
    errors=[400, 401, 403, 404],
    tags=["Test runs"],
    summary="Queue a project-wide batch test",
    description=(
        "Fan-out batch test across every website in a project. Each "
        "website is queued as its own test run via the same code path "
        "as ``POST /websites/<id>/test-runs``. Websites with no "
        "eligible pages are silently skipped — they don't appear in "
        "``test_runs`` and ``websites_queued`` reflects only the "
        "websites that actually queued work. Returns 400 when no "
        "website in the project has eligible pages."
    ),
)
@project_role_required(UserRole.ADMIN, UserRole.AUDITOR)
def test_project(
    project_id: str, body: ProjectTestRunIn
) -> tuple[ProjectTestRunStartedOut, int] | tuple[Response, int]:
    """Queue a batch test run for every website in a project.

    Each website is queued as its own test run via
    ``start_website_test_run`` — the project-level endpoint is a thin
    fan-out over the website-level batch endpoint. This gives clients
    a single REST entry point for "test the whole project" without
    forcing them to iterate over websites client-side, while keeping
    the per-website queueing semantics (per-user sequential execution,
    PDF audit folding) identical to the existing
    ``POST /websites/<id>/test-runs``.

    Body (all optional, applied uniformly to every website):

    - ``project_user_ids`` / ``website_user_ids``: project test-users
      to run as (forwarded to each website's queue)
    - ``max_pages``: cap per website
    - ``untested_only``: skip pages already TESTED

    Returns 202 with a list of handles, one per website that had at
    least one testable page. Websites with no eligible pages are
    silently skipped — the response's ``websites_queued`` reflects
    only the websites that actually queued a job. If *no* website in
    the project has eligible pages, returns 400.

    Errors:

    - **404** — project does not exist
    - **400** — project has no websites or no eligible pages anywhere
    """
    from auto_a11y.core.test_run_service import (
        NoPagesToTestError,
        WebsiteNotFoundError,
        start_website_test_run,
    )
    from auto_a11y.web.typed_app import get_pdf_runner as _get_pdf_runner

    project = get_db().get_project(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    max_pages: int | None = body.max_pages
    if max_pages is not None and max_pages <= 0:
        max_pages = None

    untested_only: bool = bool(body.untested_only)

    user_ids_raw: list[str] | str | None = (
        body.project_user_ids if body.project_user_ids is not None else body.website_user_ids
    )

    websites = get_db().get_websites(project_id)
    if not websites:
        return jsonify({'error': 'Project has no websites'}), 400

    pdf_runner = _get_pdf_runner()
    test_runs: list[ProjectTestRunWebsiteHandleOut] = []
    for website in websites:
        if website.id is None:
            continue
        try:
            handle = start_website_test_run(
                get_db(),
                get_app_config(),
                website.id,
                project_user_ids=user_ids_raw,
                max_pages=max_pages,
                untested_only=untested_only,
                pdf_runner=pdf_runner,
            )
        except WebsiteNotFoundError:
            # Race: website disappeared between list and start —
            # skip it rather than fail the whole batch.
            continue
        except NoPagesToTestError:
            # No eligible pages for this website; carry on with the rest.
            continue
        test_runs.append(ProjectTestRunWebsiteHandleOut(
            job_id=handle.job_id,
            website_id=handle.website_id,
            pages_queued=handle.pages_queued,
            user_count=handle.user_count,
            total_tests=handle.total_tests,
        ))

    if not test_runs:
        return jsonify({'error': 'No pages to test in any website'}), 400

    pages_queued_total = sum(t.pages_queued for t in test_runs)
    total_tests_sum = sum(t.total_tests for t in test_runs)

    return ProjectTestRunStartedOut(
        project_id=project_id,
        websites_queued=len(test_runs),
        pages_queued=pages_queued_total,
        total_tests=total_tests_sum,
        test_runs=test_runs,
        status='queued',
    ), 202


# Health Check

@api_bp.route('/health', methods=['GET'])
def health_check() -> Response:
    """API health check"""
    try:
        # Check database connection
        get_db().client.server_info()
        db_status = 'healthy'
    except:
        db_status = 'unhealthy'
    
    return jsonify({
        'status': 'healthy' if db_status == 'healthy' else 'degraded',
        'database': db_status,
        'timestamp': datetime.now().isoformat()
    })


# Jobs API

@api_bp.route('/jobs/stats', methods=['GET'])
def get_job_stats() -> tuple[Response, int] | Response:
    """Get job statistics"""
    try:
        job_manager = JobManager(get_db())
        
        # Get overall statistics
        stats = job_manager.get_job_statistics(hours=24)
        
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting job stats: {e}")
        return jsonify({'error': 'Failed to get job statistics'}), 500


@api_bp.route('/jobs/clear-all', methods=['POST'])
def clear_all_jobs() -> tuple[Response, int] | Response:
    """Clear all running and pending jobs - emergency reset"""
    try:
        job_manager = JobManager(get_db())
        
        # Clear all running jobs
        running_result = job_manager.collection.update_many(
            {'status': {'$in': [JobStatus.RUNNING.value, JobStatus.CANCELLING.value]}},
            {
                '$set': {
                    'status': JobStatus.CANCELLED.value,
                    'completed_at': datetime.now(),
                    'error': 'Job cleared by administrator'
                }
            }
        )
        
        # Clear all pending jobs
        pending_result = job_manager.collection.update_many(
            {'status': JobStatus.PENDING.value},
            {
                '$set': {
                    'status': JobStatus.CANCELLED.value,
                    'completed_at': datetime.now(),
                    'error': 'Job cleared by administrator'
                }
            }
        )
        
        # Also reset page statuses that are stuck in QUEUED or TESTING states
        pages_result = get_db().pages.update_many(
            {'status': {'$in': [PageStatus.QUEUED.value, PageStatus.TESTING.value]}},
            {
                '$set': {
                    'status': PageStatus.DISCOVERED.value,
                    'error_reason': 'Job cleared by administrator'
                }
            }
        )
        
        total_cleared = running_result.modified_count + pending_result.modified_count
        
        logger.info(f"Cleared {total_cleared} jobs (running: {running_result.modified_count}, pending: {pending_result.modified_count})")
        logger.info(f"Reset {pages_result.modified_count} pages from queued/testing to discovered status")
        
        return jsonify({
            'success': True,
            'cleared_count': total_cleared,
            'running_cleared': running_result.modified_count,
            'pending_cleared': pending_result.modified_count,
            'pages_reset': pages_result.modified_count,
            'message': f'Successfully cleared {total_cleared} jobs and reset {pages_result.modified_count} pages'
        })
        
    except Exception as e:
        logger.error(f"Error clearing all jobs: {e}")
        return jsonify({'error': f'Failed to clear jobs: {str(e)}'}), 500


@api_bp.route('/jobs/clear-stale', methods=['POST'])
def clear_stale_jobs() -> tuple[Response, int] | Response:
    """Clear stale jobs that have been running for too long"""
    try:
        job_manager = JobManager(get_db())
        
        # Clear jobs running for more than 24 hours
        cleared_count = job_manager.cleanup_stale_jobs(stale_after_hours=24)
        
        # Also reset old pages stuck in QUEUED or TESTING states for more than 24 hours
        from datetime import timedelta
        stale_time = datetime.now() - timedelta(hours=24)
        pages_result = get_db().pages.update_many(
            {
                'status': {'$in': [PageStatus.QUEUED.value, PageStatus.TESTING.value]},
                '$or': [
                    {'last_tested': {'$lt': stale_time}},
                    {'last_tested': None, 'discovered_at': {'$lt': stale_time}}
                ]
            },
            {
                '$set': {
                    'status': PageStatus.DISCOVERED.value,
                    'error_reason': 'Stale job cleared by administrator'
                }
            }
        )
        
        logger.info(f"Cleared {cleared_count} stale jobs")
        logger.info(f"Reset {pages_result.modified_count} stale pages")
        
        return jsonify({
            'success': True,
            'cleared_count': cleared_count,
            'pages_reset': pages_result.modified_count,
            'message': f'Successfully cleared {cleared_count} stale jobs and reset {pages_result.modified_count} pages'
        })
        
    except Exception as e:
        logger.error(f"Error clearing stale jobs: {e}")
        return jsonify({'error': f'Failed to clear stale jobs: {str(e)}'}), 500


@api_bp.route('/jobs/active', methods=['GET'])
def get_active_jobs() -> tuple[Response, int] | Response:
    """Get list of active jobs"""
    try:
        job_manager = JobManager(get_db())
        
        # Get active jobs
        active_jobs = list(job_manager.collection.find(
            {'status': {'$in': [JobStatus.RUNNING.value, JobStatus.PENDING.value, JobStatus.CANCELLING.value]}},
            {'_id': 0}  # Exclude MongoDB _id from response
        ).sort('created_at', -1).limit(100))
        
        return jsonify({
            'jobs': active_jobs,
            'count': len(active_jobs)
        })
        
    except Exception as e:
        logger.error(f"Error getting active jobs: {e}")
        return jsonify({'error': 'Failed to get active jobs'}), 500


@api_bp.route('/jobs/cleanup-page-counts', methods=['POST'])
def cleanup_page_counts() -> tuple[Response, int] | Response:
    """Clean up violation counts for pages that haven't been tested"""
    try:
        # Reset violation/warning/info counts for all pages that aren't in TESTED status
        result = get_db().pages.update_many(
            {'status': {'$ne': PageStatus.TESTED.value}},
            {
                '$set': {
                    'violation_count': 0,
                    'warning_count': 0,
                    'info_count': 0,
                    'discovery_count': 0,
                    'pass_count': 0,
                    'test_duration_ms': None
                }
            }
        )
        
        logger.info(f"Cleaned up counts for {result.modified_count} untested pages")
        
        return jsonify({
            'success': True,
            'pages_cleaned': result.modified_count,
            'message': f'Reset counts for {result.modified_count} untested pages'
        })
        
    except Exception as e:
        logger.error(f"Error cleaning up page counts: {e}")
        return jsonify({'error': f'Failed to clean up page counts: {str(e)}'}), 500


# Multi-State Testing API Endpoints

@api_bp.route('/test-results/<result_id>/states', methods=['GET'])
@document(
    response_200=TestResultStatesOut,
    errors=[404, 500],
    tags=["Test results"],
    summary="List sibling state test results",
    description=(
        "Returns every test result from the same multi-state testing "
        "session as ``result_id``, sorted by ``state_sequence`` "
        "ascending. The seed result is included in the list. "
        "``page_state`` is the producer-supplied state metadata (button "
        "name, script name, etc.) — free-form because the runner emits "
        "records whose keys depend on the trigger."
    ),
)
def get_test_result_states(
    result_id: str,
) -> tuple[TestResultStatesOut, int] | tuple[Response, int] | Response:
    """Get all related state test results for a given result."""
    try:
        # Get the result
        result = get_db().get_test_result(result_id)
        if not result:
            return jsonify({'error': 'Test result not found'}), 404

        # Get related results
        related_results = get_db().get_related_test_results(result_id)

        # Include the original result
        all_results = [result] + related_results

        # Sort by state_sequence
        all_results.sort(key=lambda r: r.state_sequence)

        # Serialize results
        results_data: list[TestStateEntryOut] = []
        for r in all_results:
            results_data.append(TestStateEntryOut(
                result_id=str(r.mongo_id) if r.mongo_id else None,
                state_sequence=r.state_sequence,
                page_state=r.page_state,
                session_id=r.session_id,
                test_date=r.test_date.isoformat() if r.test_date else None,
                violation_count=r.violation_count,
                warning_count=r.warning_count,
                info_count=r.info_count,
                pass_count=r.pass_count,
                duration_ms=r.duration_ms,
            ))

        return TestResultStatesOut(
            success=True,
            result_id=result_id,
            total_states=len(results_data),
            states=results_data,
        ), 200

    except Exception as e:
        logger.error(f"Error getting test result states: {e}")
        return jsonify({'error': f'Failed to get test result states: {str(e)}'}), 500


@api_bp.route('/pages/<page_id>/test-states', methods=['GET'])
@document(
    response_200=PageTestStatesOut,
    errors=[404, 500],
    tags=["Test results"],
    summary="Get latest test results per page state",
    description=(
        "Returns the most recent test result for each multi-state "
        "test position (initial, after script, after button A, etc.) "
        "as a dict keyed by stringified ``state_sequence`` integers."
    ),
)
def get_page_test_states(
    page_id: str,
) -> tuple[PageTestStatesOut, int] | tuple[Response, int] | Response:
    """Get latest test results per state for a page."""
    try:
        # Check page exists
        page = get_db().get_page(page_id)
        if not page:
            return jsonify({'error': 'Page not found'}), 404

        # Get latest results per state
        state_results = get_db().get_latest_test_results_per_state(page_id)

        # Serialize results. Flask's JSON encoder coerces int keys to
        # strings on the wire, so the model uses str keys here too —
        # the actual emitted body matches byte-for-byte.
        states_data: dict[str, TestStateEntryOut] = {}
        for state_seq, result in state_results.items():
            states_data[str(state_seq)] = TestStateEntryOut(
                result_id=str(result.mongo_id) if result.mongo_id else None,
                state_sequence=state_seq,
                page_state=result.page_state,
                session_id=result.session_id,
                test_date=result.test_date.isoformat() if result.test_date else None,
                violation_count=result.violation_count,
                warning_count=result.warning_count,
                info_count=result.info_count,
                pass_count=result.pass_count,
                duration_ms=result.duration_ms,
            )

        return PageTestStatesOut(
            success=True,
            page_id=page_id,
            page_url=page.url,
            total_states=len(states_data),
            states=states_data,
        ), 200

    except Exception as e:
        logger.error(f"Error getting page test states: {e}")
        return jsonify({'error': f'Failed to get page test states: {str(e)}'}), 500


@api_bp.route('/pages/<page_id>/test-sessions', methods=['GET'])
@document(
    response_200=PageTestSessionsOut,
    errors=[404, 500],
    tags=["Test results"],
    summary="List a page's test sessions",
    description=(
        "Groups every stored test result for the page by ``session_id`` "
        "(``single_state`` is the bucket for results with no session) "
        "and returns one entry per session, sorted by ``test_date`` "
        "descending. Each session lists its states and rolls up the "
        "violation/warning totals."
    ),
)
def get_page_test_sessions(
    page_id: str,
) -> tuple[PageTestSessionsOut, int] | tuple[Response, int] | Response:
    """Get all test sessions for a page with their state counts."""
    try:
        # Check page exists
        page = get_db().get_page(page_id)
        if not page:
            return jsonify({'error': 'Page not found'}), 404

        # Get all test results for page
        all_results = get_db().get_test_results(page_id=page_id)

        # Group by session. We accumulate in a typed-builder dict and
        # assemble the response models at the end so per-iteration
        # mutation paths remain straightforward.
        session_test_dates: dict[str, datetime | None] = {}
        session_states: dict[str, list[PageTestSessionStateOut]] = {}
        session_totals_violations: dict[str, int] = {}
        session_totals_warnings: dict[str, int] = {}

        for result in all_results:
            session_id = result.session_id or 'single_state'

            if session_id not in session_test_dates:
                session_test_dates[session_id] = result.test_date
                session_states[session_id] = []
                session_totals_violations[session_id] = 0
                session_totals_warnings[session_id] = 0

            page_state = result.page_state
            state_description: str | None = None
            if page_state is not None:
                desc_value = page_state.get('description')
                if isinstance(desc_value, str):
                    state_description = desc_value

            session_states[session_id].append(PageTestSessionStateOut(
                result_id=str(result.mongo_id) if result.mongo_id else None,
                state_sequence=result.state_sequence,
                state_description=state_description,
                violation_count=result.violation_count,
                warning_count=result.warning_count,
            ))

            session_totals_violations[session_id] += result.violation_count
            session_totals_warnings[session_id] += result.warning_count

        # Sort sessions by date (descending), then states inside each
        # session by state_sequence ascending.
        ordered_session_ids = sorted(
            session_test_dates.keys(),
            key=lambda sid: session_test_dates[sid] or datetime.min,
            reverse=True,
        )

        sessions_list: list[PageTestSessionOut] = []
        for session_id in ordered_session_ids:
            sorted_states = sorted(session_states[session_id], key=lambda s: s.state_sequence)
            test_date_dt = session_test_dates[session_id]
            sessions_list.append(PageTestSessionOut(
                session_id=session_id,
                test_date=test_date_dt.isoformat() if test_date_dt else None,
                states=sorted_states,
                total_violations=session_totals_violations[session_id],
                total_warnings=session_totals_warnings[session_id],
                state_count=len(sorted_states),
            ))

        return PageTestSessionsOut(
            success=True,
            page_id=page_id,
            page_url=page.url,
            total_sessions=len(sessions_list),
            sessions=sessions_list,
        ), 200

    except Exception as e:
        logger.error(f"Error getting page test sessions: {e}")
        return jsonify({'error': f'Failed to get page test sessions: {str(e)}'}), 500


@api_bp.route('/test-results/compare', methods=['POST'])
@document(
    request=TestResultCompareIn,
    response_200=TestResultCompareOut,
    errors=[400, 404, 500],
    tags=["Test results"],
    summary="Compare two test results",
    description=(
        "Returns a diff of two test results, typically from different "
        "states or runs: violations newly introduced in ``result_2``, "
        "violations that disappeared from ``result_1``, and violations "
        "present in both. The ``comparison`` payload is deeply nested "
        "and emitted as a free-form object (the legacy handler builds "
        "it inline; modelling each leaf would inflate this surface "
        "without changing the wire — §5.x downstream work will revisit)."
    ),
)
def compare_test_results(
    body: TestResultCompareIn,
) -> tuple[TestResultCompareOut, int] | tuple[Response, int] | Response:
    """Compare two test results (typically from different states)."""
    try:
        result_id_1 = body.result_id_1
        result_id_2 = body.result_id_2

        if not result_id_1 or not result_id_2:
            return jsonify({'error': 'Both result_id_1 and result_id_2 are required'}), 400

        # Get results
        result1 = get_db().get_test_result(result_id_1)
        result2 = get_db().get_test_result(result_id_2)

        if not result1 or not result2:
            return jsonify({'error': 'One or both test results not found'}), 404

        # Get violation IDs (using issue_id for comparison)
        violations1_ids = {v.id for v in result1.violations}
        violations2_ids = {v.id for v in result2.violations}

        # Calculate differences
        new_violations = [v for v in result2.violations if v.id not in violations1_ids]
        fixed_violations = [v for v in result1.violations if v.id not in violations2_ids]
        persistent_violations = [v for v in result2.violations if v.id in violations1_ids]

        # Serialize violations. ``Violation.impact`` is typed
        # :class:`ImpactLevel`; the legacy handler had a
        # ``hasattr(v.impact, 'value')`` guard for old records that
        # stored a raw string, but the dataclass always coerces on
        # ``from_dict``, so the guard is dead code.
        def serialize_violation(v: Violation) -> dict[str, object]:
            return {
                'id': v.id,
                'impact': v.impact.value,
                'touchpoint': v.touchpoint,
                'description': v.description,
                'element': v.element,
            }

        comparison: dict[str, object] = {
            'result_1': {
                'id': result_id_1,
                'state_sequence': result1.state_sequence,
                'state_description': result1.page_state.get('description') if result1.page_state else None,
                'violation_count': result1.violation_count,
                'test_date': result1.test_date.isoformat() if result1.test_date else None,
            },
            'result_2': {
                'id': result_id_2,
                'state_sequence': result2.state_sequence,
                'state_description': result2.page_state.get('description') if result2.page_state else None,
                'violation_count': result2.violation_count,
                'test_date': result2.test_date.isoformat() if result2.test_date else None,
            },
            'new_violations': [serialize_violation(v) for v in new_violations],
            'fixed_violations': [serialize_violation(v) for v in fixed_violations],
            'persistent_violations': [serialize_violation(v) for v in persistent_violations],
            'summary': {
                'new_count': len(new_violations),
                'fixed_count': len(fixed_violations),
                'persistent_count': len(persistent_violations),
                'net_change': result2.violation_count - result1.violation_count,
            },
        }

        return TestResultCompareOut(
            success=True,
            comparison=comparison,
        ), 200

    except Exception as e:
        logger.error(f"Error comparing test results: {e}")
        return jsonify({'error': f'Failed to compare test results: {str(e)}'}), 500


@api_bp.route('/health/pdf', methods=['GET'])
def pdf_health() -> tuple[Response, int]:
    """Report PDF-audit subsystem health."""
    from pathlib import Path
    from auto_a11y.pdf.health import check_pdf_health
    from auto_a11y.web.typed_app import get_app_config

    cfg = get_app_config()
    health = check_pdf_health(
        gs_override=cfg.GHOSTSCRIPT_PATH,
        storage_dir=Path(cfg.PDF_STORAGE_DIR),
    )
    payload = {
        "ghostscript": {"found": health.ghostscript.found, "path": health.ghostscript.path},
        "storage": {"dir": health.storage.dir, "writable": health.storage.writable},
    }
    status = 200 if health.ok else 503
    return jsonify(payload), status


# ---------------------------------------------------------------------------
# Scheduled tests (REST shape — uses the @api_endpoint scaffolding).
#
# These endpoints follow the conventions in ``docs/REST_API_ROADMAP.md``:
# bare-body JSON responses (no ``success`` envelope), Problem Details on
# error, cursor pagination on list, RFC 7807 ``Idempotency-Key`` support
# on the action endpoint. They live alongside the legacy ``success``-
# wrapped endpoints above; both shapes coexist until the frontend-
# migration phase per the locked-in §4.4 deprecation plan.
# ---------------------------------------------------------------------------

from auto_a11y.models.schedule import (  # noqa: E402
    AITestMode,
    PresetConfig,
    ScheduleTestConfig,
    ScheduleType,
    TestSchedule,
)
from auto_a11y.web.api import (  # noqa: E402
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
    api_endpoint,
    paginate,
    require_project_role,
    require_superadmin,
)
from auto_a11y.web.api.errors import FieldError as _FieldError  # noqa: E402
from auto_a11y.web.api.pagination import Cursor as _Cursor  # noqa: E402
from auto_a11y.web.api.pagination import parse_limit  # noqa: E402
from auto_a11y.web.typed_app import get_idempotency_store  # noqa: E402


def _schedule_to_out(schedule: TestSchedule) -> ScheduleOut:
    """Project a :class:`TestSchedule` to its :class:`ScheduleOut` model.

    Mirrors the legacy ``_serialize_schedule`` shape byte-for-byte:
    datetimes are emitted as ISO 8601 strings, enum values are
    stringified, and the Mongo ``_id`` is dropped in favor of the
    ``id`` property.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return ScheduleOut(
        id=schedule.id,
        website_id=schedule.website_id,
        name=schedule.name,
        description=schedule.description,
        schedule_type=schedule.schedule_type.value,
        scheduled_datetime=_iso(schedule.scheduled_datetime),
        cron_expression=schedule.cron_expression,
        preset_config=PresetConfigOut(
            time=schedule.preset_config.time,
            day_of_week=schedule.preset_config.day_of_week,
            day_of_month=schedule.preset_config.day_of_month,
            timezone=schedule.preset_config.timezone,
        ),
        test_config=ScheduleTestConfigOut(
            run_ai_tests=schedule.test_config.run_ai_tests,
            run_javascript_tests=schedule.test_config.run_javascript_tests,
            run_python_tests=schedule.test_config.run_python_tests,
            enabled_touchpoints=list(schedule.test_config.enabled_touchpoints),
            ai_pages_mode=schedule.test_config.ai_pages_mode.value,
            ai_page_ids=list(schedule.test_config.ai_page_ids),
            take_screenshots=schedule.test_config.take_screenshots,
        ),
        project_user_ids=list(schedule.project_user_ids),
        enabled=schedule.enabled,
        created_by=schedule.created_by,
        last_run_at=_iso(schedule.last_run_at),
        last_run_job_id=schedule.last_run_job_id,
        last_run_status=(
            schedule.last_run_status.value
            if schedule.last_run_status is not None
            else None
        ),
        next_run_at=_iso(schedule.next_run_at),
        run_count=schedule.run_count,
        created_at=_iso(schedule.created_at),
        updated_at=_iso(schedule.updated_at),
    )


def _coerce_str_list(value: Any) -> list[str]:
    """Coerce an arbitrary iterable into a ``list[str]``.

    Centralizes the ``Any → list[str]`` conversion so callers don't have
    to wrestle with pyright's Unknown propagation on every comprehension.
    """
    items: list[Any] = []
    for item in value:
        items.append(item)
    return [str(item) for item in items]


def _require_dict_body() -> dict[str, Any]:
    """Return the parsed JSON body or raise ValidationError."""
    body_any: Any = request.get_json(silent=True)
    if not isinstance(body_any, dict):
        raise ValidationError(
            "request body must be a JSON object",
            errors=(
                _FieldError(field="<root>", code="invalid_type", message="must be object"),
            ),
        )
    return cast(dict[str, Any], body_any)


def _parse_schedule_type(raw: Any, *, field: str) -> ScheduleType:
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(
                _FieldError(field=field, code="invalid_type", message="must be string"),
            ),
        )
    try:
        return ScheduleType(raw)
    except ValueError as exc:
        raise ValidationError(
            f"{field} is not a recognized schedule type",
            errors=(
                _FieldError(field=field, code="invalid_value", message=str(exc)),
            ),
        ) from exc


def _parse_iso_datetime(raw: Any, *, field: str) -> datetime:
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be an ISO 8601 datetime string",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"{field} is not a valid ISO 8601 datetime",
            errors=(_FieldError(field=field, code="invalid_format", message=str(exc)),),
        ) from exc
    return parsed


def _parse_preset_config(raw: Any, *, field: str) -> PresetConfig:
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    return PresetConfig(
        time=str(raw_dict.get("time", "02:00")),
        day_of_week=int(raw_dict.get("day_of_week", 0)),
        day_of_month=int(raw_dict.get("day_of_month", 1)),
        timezone=str(raw_dict.get("timezone", "America/Toronto")),
    )


def _parse_test_config(raw: Any, *, field: str) -> ScheduleTestConfig:
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    ai_pages_mode_raw: Any = raw_dict.get("ai_pages_mode", "all")
    try:
        ai_pages_mode = (
            AITestMode(ai_pages_mode_raw)
            if isinstance(ai_pages_mode_raw, str)
            else AITestMode.ALL
        )
    except ValueError as exc:
        raise ValidationError(
            "test_config.ai_pages_mode is not recognized",
            errors=(
                _FieldError(
                    field=f"{field}.ai_pages_mode",
                    code="invalid_value",
                    message=str(exc),
                ),
            ),
        ) from exc
    return ScheduleTestConfig(
        run_ai_tests=bool(raw_dict.get("run_ai_tests", False)),
        run_javascript_tests=bool(raw_dict.get("run_javascript_tests", True)),
        run_python_tests=bool(raw_dict.get("run_python_tests", True)),
        enabled_touchpoints=list(raw_dict.get("enabled_touchpoints", [])),
        ai_pages_mode=ai_pages_mode,
        ai_page_ids=list(raw_dict.get("ai_page_ids", [])),
        take_screenshots=bool(raw_dict.get("take_screenshots", True)),
    )


def _validate_schedule_invariants(schedule: TestSchedule) -> None:
    """Cross-field checks not enforceable from a single field's parser."""
    if not schedule.name.strip():
        raise ValidationError(
            "name is required",
            errors=(_FieldError(field="name", code="required", message="required"),),
        )
    if (
        schedule.schedule_type is ScheduleType.CRON
        and not (schedule.cron_expression or "").strip()
    ):
        raise ValidationError(
            "cron_expression is required when schedule_type=cron",
            errors=(
                _FieldError(
                    field="cron_expression",
                    code="required",
                    message="required when schedule_type=cron",
                ),
            ),
        )
    if (
        schedule.schedule_type is ScheduleType.ONE_TIME
        and schedule.scheduled_datetime is None
    ):
        raise ValidationError(
            "scheduled_datetime is required when schedule_type=one_time",
            errors=(
                _FieldError(
                    field="scheduled_datetime",
                    code="required",
                    message="required when schedule_type=one_time",
                ),
            ),
        )


def _build_schedule_from_body(
    website_id: str, body: dict[str, Any]
) -> TestSchedule:
    schedule_type = _parse_schedule_type(
        body.get("schedule_type", "daily"), field="schedule_type"
    )
    scheduled_datetime: datetime | None = None
    if "scheduled_datetime" in body and body["scheduled_datetime"] is not None:
        scheduled_datetime = _parse_iso_datetime(
            body["scheduled_datetime"], field="scheduled_datetime"
        )
    preset_config = _parse_preset_config(
        body.get("preset_config", {}), field="preset_config"
    )
    test_config = _parse_test_config(body.get("test_config", {}), field="test_config")
    cron_raw = body.get("cron_expression")
    cron_expression = cron_raw.strip() if isinstance(cron_raw, str) and cron_raw.strip() else None
    project_user_ids_raw: Any = body.get("project_user_ids", [])
    if not isinstance(project_user_ids_raw, list):
        raise ValidationError(
            "project_user_ids must be an array",
            errors=(
                _FieldError(
                    field="project_user_ids",
                    code="invalid_type",
                    message="must be array",
                ),
            ),
        )
    project_user_ids = _coerce_str_list(project_user_ids_raw)
    schedule = TestSchedule(
        website_id=website_id,
        name=str(body.get("name", "")).strip(),
        description=(
            str(body["description"]).strip()
            if isinstance(body.get("description"), str)
            else None
        ),
        schedule_type=schedule_type,
        scheduled_datetime=scheduled_datetime,
        cron_expression=cron_expression,
        preset_config=preset_config,
        test_config=test_config,
        project_user_ids=project_user_ids,
        enabled=bool(body.get("enabled", True)),
        created_by=(
            str(current_user.get_id()) if current_user.is_authenticated else None
        ),
    )
    _validate_schedule_invariants(schedule)
    return schedule


def _apply_patch_to_schedule(
    schedule: TestSchedule, body: dict[str, Any]
) -> TestSchedule:
    """Apply only the keys present in ``body`` to ``schedule``."""
    if "name" in body:
        if not isinstance(body["name"], str):
            raise ValidationError(
                "name must be a string",
                errors=(
                    _FieldError(field="name", code="invalid_type", message="must be string"),
                ),
            )
        schedule.name = body["name"].strip()
    if "description" in body:
        desc = body["description"]
        schedule.description = desc.strip() if isinstance(desc, str) else None
    if "schedule_type" in body:
        schedule.schedule_type = _parse_schedule_type(
            body["schedule_type"], field="schedule_type"
        )
    if "scheduled_datetime" in body:
        schedule.scheduled_datetime = (
            _parse_iso_datetime(body["scheduled_datetime"], field="scheduled_datetime")
            if body["scheduled_datetime"] is not None
            else None
        )
    if "cron_expression" in body:
        cron_raw = body["cron_expression"]
        schedule.cron_expression = (
            cron_raw.strip() if isinstance(cron_raw, str) and cron_raw.strip() else None
        )
    if "preset_config" in body:
        schedule.preset_config = _parse_preset_config(
            body["preset_config"], field="preset_config"
        )
    if "test_config" in body:
        schedule.test_config = _parse_test_config(
            body["test_config"], field="test_config"
        )
    if "project_user_ids" in body:
        ids_raw: Any = body["project_user_ids"]
        if not isinstance(ids_raw, list):
            raise ValidationError(
                "project_user_ids must be an array",
                errors=(
                    _FieldError(
                        field="project_user_ids",
                        code="invalid_type",
                        message="must be array",
                    ),
                ),
            )
        schedule.project_user_ids = _coerce_str_list(ids_raw)
    if "enabled" in body:
        schedule.enabled = bool(body["enabled"])
    schedule.update_timestamp()
    _validate_schedule_invariants(schedule)
    return schedule


def _schedule_body_to_dict(body: ScheduleIn | SchedulePatch) -> dict[str, Any]:
    """Convert a Pydantic schedule body to the legacy ``dict[str, Any]`` shape.

    The legacy parsers ``_build_schedule_from_body`` and
    ``_apply_patch_to_schedule`` distinguish "absent" from "explicit
    ``null``" using ``key in body`` checks against a raw request dict.
    Pydantic's ``model_dump(exclude_unset=True)`` preserves that exact
    distinction: only keys the client explicitly sent appear in the
    output dict.

    Nested config blocks (``preset_config``, ``test_config``) are
    dumped as nested dicts so the legacy ``_parse_preset_config`` /
    ``_parse_test_config`` parsers receive the shapes they expect.
    """
    return body.model_dump(exclude_unset=True, by_alias=False)


def _resync_with_scheduler(schedule: TestSchedule) -> None:
    """Mirror the legacy register/remove dance against the scheduler.

    Imported lazily so endpoints can run in tests where the APScheduler
    service is not configured.
    """
    from auto_a11y.core.scheduler import get_scheduler_service

    scheduler = get_scheduler_service()
    if scheduler is None:
        return
    if schedule.enabled:
        scheduler.register_schedule_with_apscheduler(schedule)
    elif schedule.id:
        scheduler.remove_from_apscheduler(schedule.id)


@api_bp.route("/websites/<website_id>/scheduled-tests", methods=["GET"])
@api_endpoint
@document(
    response_200=ScheduleListOut,
    errors=[400, 401, 403, 404],
    tags=["Schedules"],
    summary="List scheduled tests for a website",
    description=(
        "Returns the scheduled tests configured on the given website. "
        "Cursor-paginated using the legacy ``{items, next_cursor}`` "
        "shape shared by every v1 list endpoint."
    ),
)
def list_scheduled_tests(
    website_id: str,
) -> tuple[ScheduleListOut, int] | tuple[Response, int] | Response:
    """List scheduled tests for a website with cursor pagination."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"website_id": website_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(
                    _FieldError(
                        field="cursor.last_id",
                        code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc

    docs = list(
        get_db().test_schedules.find(query).sort("_id", -1).limit(limit + 1)
    )
    schedules = [TestSchedule.from_dict(doc) for doc in docs]
    page = paginate(
        schedules, limit=limit, get_id=lambda s: str(s.mongo_id) if s.mongo_id else ""
    )
    return ScheduleListOut(
        items=[_schedule_to_out(s) for s in page["items"]],
        next_cursor=page["next_cursor"],
    ), 200


@api_bp.route("/websites/<website_id>/scheduled-tests", methods=["POST"])
@api_endpoint
@document(
    request=ScheduleIn,
    response_201=ScheduleOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Schedules"],
    summary="Create a scheduled test on a website",
    description=(
        "Creates a scheduled test and registers it with the APScheduler "
        "service when ``enabled`` is true. Returns 201 with the persisted "
        "``ScheduleOut`` resource plus a ``Location`` header pointing at "
        "``/api/v1/scheduled-tests/<id>``."
    ),
)
def create_scheduled_test(
    website_id: str, body: ScheduleIn,
) -> tuple[Response, int]:
    """Create a scheduled test on a website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    legacy_body = _schedule_body_to_dict(body)
    schedule = _build_schedule_from_body(website_id, legacy_body)
    schedule_id = get_db().create_test_schedule(schedule)
    refreshed = get_db().get_test_schedule(schedule_id)
    if refreshed is None:
        raise ConflictError("schedule failed to persist")
    if refreshed.enabled:
        _resync_with_scheduler(refreshed)
    # The @document decorator serialises BaseModel returns through
    # ``jsonify``, but we need a ``Location`` header on the new resource,
    # so we pre-build the Response here and attach the header.
    payload = _schedule_to_out(refreshed)
    response = jsonify(
        payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    response.headers["Location"] = f"/api/v1/scheduled-tests/{schedule_id}"
    return response, 201


@api_bp.route("/scheduled-tests/<schedule_id>", methods=["GET"])
@api_endpoint
@document(
    response_200=ScheduleOut,
    errors=[401, 403, 404],
    tags=["Schedules"],
    summary="Get a scheduled test by ID",
    description="Returns the scheduled-test resource (``ScheduleOut``).",
)
def get_scheduled_test(
    schedule_id: str,
) -> tuple[ScheduleOut, int] | tuple[Response, int] | Response:
    """Get a scheduled test by id."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    return _schedule_to_out(schedule), 200


@api_bp.route("/scheduled-tests/<schedule_id>", methods=["PUT"])
@api_endpoint
@document(
    request=ScheduleIn,
    response_200=ScheduleOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Schedules"],
    summary="Replace a scheduled test",
    description=(
        "Full replacement of the user-editable fields. Server-managed "
        "bookkeeping (``run_count``, ``last_run_*``, ``next_run_at``, "
        "``apscheduler_job_id``, ``created_at``, ``created_by``) is "
        "preserved from the prior version."
    ),
)
def replace_scheduled_test(
    schedule_id: str, body: ScheduleIn,
) -> tuple[ScheduleOut, int] | tuple[Response, int] | Response:
    """Full replace of a scheduled test."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    legacy_body = _schedule_body_to_dict(body)
    replaced = _build_schedule_from_body(schedule.website_id, legacy_body)
    replaced.mongo_id = schedule.mongo_id
    replaced.created_at = schedule.created_at
    replaced.created_by = schedule.created_by
    replaced.last_run_at = schedule.last_run_at
    replaced.last_run_job_id = schedule.last_run_job_id
    replaced.last_run_status = schedule.last_run_status
    replaced.next_run_at = schedule.next_run_at
    replaced.run_count = schedule.run_count
    replaced.apscheduler_job_id = schedule.apscheduler_job_id
    replaced.update_timestamp()
    if not get_db().update_test_schedule(replaced):
        raise ConflictError("schedule could not be updated")
    _resync_with_scheduler(replaced)
    return _schedule_to_out(replaced), 200


@api_bp.route("/scheduled-tests/<schedule_id>", methods=["PATCH"])
@api_endpoint
@document(
    request=SchedulePatch,
    response_200=ScheduleOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Schedules"],
    summary="Partially update a scheduled test",
    description=(
        "Partial update -- only fields present in the request body are "
        "applied. Commonly used for the enable/disable toggle "
        "(``{\"enabled\": true}``) and similar narrow edits."
    ),
)
def patch_scheduled_test(
    schedule_id: str, body: SchedulePatch,
) -> tuple[ScheduleOut, int] | tuple[Response, int] | Response:
    """Partial update -- used for toggle (``{"enabled": true}``) and similar edits."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    legacy_body = _schedule_body_to_dict(body)
    patched = _apply_patch_to_schedule(schedule, legacy_body)
    if not get_db().update_test_schedule(patched):
        raise ConflictError("schedule could not be updated")
    _resync_with_scheduler(patched)
    return _schedule_to_out(patched), 200


@api_bp.route("/scheduled-tests/<schedule_id>", methods=["DELETE"])
@api_endpoint
@document(
    response_204=Empty,
    errors=[401, 403, 404],
    tags=["Schedules"],
    summary="Delete a scheduled test",
    description=(
        "Removes the schedule from the APScheduler service and deletes "
        "the persisted resource. Returns ``204 No Content`` with an "
        "empty body."
    ),
)
def delete_scheduled_test(
    schedule_id: str,
) -> tuple[Empty, int] | tuple[Response, int]:
    """Delete a scheduled test."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    from auto_a11y.core.scheduler import get_scheduler_service
    scheduler = get_scheduler_service()
    if scheduler is not None:
        scheduler.remove_from_apscheduler(schedule_id)
    get_db().delete_test_schedule(schedule_id)
    return Empty(), 204


@api_bp.route("/scheduled-tests/<schedule_id>/runs", methods=["POST"])
@api_endpoint
@document(
    response_202=ScheduleRunOut,
    errors=[401, 403, 404, 409],
    tags=["Schedules"],
    summary="Trigger an immediate run of a scheduled test",
    description=(
        "Honors the ``Idempotency-Key`` header per §4.9 of the "
        "REST API roadmap. A repeated POST with the same key returns "
        "the recorded ``job_id`` without enqueuing a second job."
    ),
)
def run_scheduled_test_now(
    schedule_id: str,
) -> tuple[Response, int] | Response:
    """Trigger an immediate run of the schedule.

    Honors the ``Idempotency-Key`` header per §4.9 of the roadmap. A
    repeated POST with the same key returns the recorded ``job_id``
    without enqueuing a second job.
    """
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )

    from auto_a11y.core.scheduler import get_scheduler_service
    scheduler = get_scheduler_service()
    if scheduler is None:
        raise ConflictError("scheduler service is not available")

    def _run() -> tuple[int, dict[str, Any]]:
        job_id = scheduler.run_now(schedule_id)
        if not job_id:
            raise ConflictError("scheduler refused to start the run")
        return 202, {"job_id": job_id, "schedule_id": schedule_id}

    idempotency_key = request.headers.get("Idempotency-Key")
    body_any: Any = request.get_json(silent=True)
    request_body: dict[str, Any] | None = (
        cast(dict[str, Any], body_any) if isinstance(body_any, dict) else None
    )

    if idempotency_key:
        status_code, body = get_idempotency_store().get_or_record(
            idempotency_key, request_body=request_body, compute=_run
        )
    else:
        status_code, body = _run()

    # The idempotency store caches the legacy ``dict`` shape; re-validate
    # through ``ScheduleRunOut`` so the wire output exactly matches the
    # advertised schema (and so a cached miss-shaped value would surface
    # as a 500 rather than silently drifting from the spec).
    payload = ScheduleRunOut.model_validate(body)
    return jsonify(
        payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    ), status_code


@api_bp.route("/scheduled-tests/<schedule_id>/preview", methods=["GET"])
@api_endpoint
@document(
    response_200=SchedulePreviewOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Schedules"],
    summary="Preview the next upcoming run times for a scheduled test",
    description=(
        "Returns the next ``?count=N`` upcoming run times (default 5, "
        "clamped to ``[1, 50]``) as ISO 8601 datetime strings."
    ),
)
def preview_scheduled_test(
    schedule_id: str,
) -> tuple[SchedulePreviewOut, int] | tuple[Response, int] | Response:
    """Return the next N upcoming run times for a schedule."""
    schedule = get_db().get_test_schedule(schedule_id)
    if schedule is None:
        raise NotFoundError(f"scheduled test {schedule_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=schedule.website_id
    )
    from auto_a11y.core.scheduler import get_scheduler_service
    scheduler = get_scheduler_service()
    if scheduler is None:
        raise ConflictError("scheduler service is not available")

    count_raw = request.args.get("count", "5")
    try:
        count = max(1, min(int(count_raw), 50))
    except ValueError as exc:
        raise ValidationError(
            "count must be an integer",
            errors=(_FieldError(field="count", code="invalid_type", message=str(exc)),),
        ) from exc

    next_runs = scheduler.get_next_run_times(schedule_id, count)
    return SchedulePreviewOut(
        schedule_id=schedule_id,
        next_runs=[dt.isoformat() for dt in next_runs],
    ), 200


# ---------------------------------------------------------------------------
# Websites (REST shape — uses the @api_endpoint scaffolding).
#
# Mirrors the conventions from the scheduled-tests block above. Action
# endpoints (discoveries, test-runs, cancels, clear-test-results) are
# scoped out of this PR — they involve async-job tracking and idempotency
# concerns that are best handled in a follow-up alongside their respective
# resources, per ``docs/REST_API_ROADMAP.md`` §5.2.
# ---------------------------------------------------------------------------

from auto_a11y.models.website import ScrapingConfig, Website  # noqa: E402


def _validate_url(raw: Any, *, field: str) -> str:
    """Require ``raw`` to be a non-empty http(s) URL string."""
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    value = raw.strip()
    if not value:
        raise ValidationError(
            f"{field} is required",
            errors=(_FieldError(field=field, code="required", message="required"),),
        )
    if not (value.startswith("http://") or value.startswith("https://")):
        raise ValidationError(
            f"{field} must be an http(s) URL",
            errors=(_FieldError(field=field, code="invalid_format", message="must start with http:// or https://"),),
        )
    return value


def _website_to_out(website: Website) -> WebsiteOut:
    """Project a :class:`Website` to a :class:`WebsiteOut` model.

    Mirrors the legacy ``_serialize_website()`` shape byte-for-byte:
    datetimes are emitted as ISO 8601 strings, the Mongo ``_id`` surfaces
    via the ``id`` property (already stringified), and ``members`` is
    intentionally dropped (the project-members API is the authoritative
    surface for that data).
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return WebsiteOut(
        id=website.id,
        project_id=website.project_id,
        url=website.url,
        name=website.name,
        display_name=website.display_name,
        page_count=website.page_count,
        scraping_config=ScrapingConfigModel.model_validate(
            website.scraping_config.to_dict()
        ),
        created_at=_iso(website.created_at),
        last_scraped=_iso(website.last_scraped),
        last_tested=_iso(website.last_tested),
    )


def _scraping_config_from_model(
    model: ScrapingConfigModel | None,
) -> ScrapingConfig:
    """Build a :class:`ScrapingConfig` dataclass from a Pydantic model.

    None-valued fields fall back to the dataclass's defaults, matching
    the legacy ``ScrapingConfig.from_dict`` behaviour (unknown keys are
    impossible here because ``StrictModel`` already rejected them).
    """
    if model is None:
        return ScrapingConfig()
    return ScrapingConfig.from_dict(model.model_dump(exclude_none=True))


def _website_from_in(project_id: str, body: WebsiteIn | WebsitePut) -> Website:
    """Construct a new :class:`Website` from a POST or PUT body.

    The URL gets the same ``http(s)://`` prefix check the legacy handler
    applied (Pydantic's built-in ``HttpUrl`` is intentionally not used
    here so the v1 error envelope stays identical for invalid URLs).
    ``name`` is normalized: whitespace-only strings collapse to ``None``.
    """
    url = _validate_url(body.url, field="url")
    name = body.name.strip() if body.name is not None and body.name.strip() else None
    return Website(
        project_id=project_id,
        url=url,
        name=name,
        scraping_config=_scraping_config_from_model(body.scraping_config),
    )


def _apply_patch_pyd(website: Website, body: WebsitePatch) -> Website:
    """Apply a :class:`WebsitePatch` to ``website`` in place.

    Only fields that were *present* on the wire are applied. Pydantic
    distinguishes "field omitted" from "field set to null" via
    ``model_fields_set``; this preserves the legacy "patch only the
    keys the client sent" contract.
    """
    fields_set = body.model_fields_set
    if "url" in fields_set and body.url is not None:
        website.url = _validate_url(body.url, field="url")
    if "name" in fields_set:
        website.name = (
            body.name.strip()
            if body.name is not None and body.name.strip()
            else None
        )
    if "scraping_config" in fields_set:
        website.scraping_config = _scraping_config_from_model(body.scraping_config)
    return website


@api_bp.route("/projects/<project_id>/websites", methods=["GET"])
@api_endpoint
@document(
    response_200=WebsiteListOut,
    errors=[400, 401, 403, 404],
    tags=["Websites"],
    summary="List websites in a project",
    description=(
        "Returns the websites belonging to ``project_id`` with cursor "
        "pagination. The response shape is ``{items, next_cursor}`` -- "
        "NOT the page/limit/total envelope used by ``GET /projects``."
    ),
)
def list_websites_for_project(
    project_id: str,
) -> tuple[WebsiteListOut, int] | tuple[Response, int] | Response:
    """List websites in a project with cursor pagination."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"project_id": project_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(
                    _FieldError(
                        field="cursor.last_id",
                        code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc

    docs = list(get_db().websites.find(query).sort("_id", -1).limit(limit + 1))
    websites = [Website.from_dict(doc) for doc in docs]
    page = paginate(
        websites, limit=limit, get_id=lambda w: str(w.mongo_id) if w.mongo_id else ""
    )
    return WebsiteListOut(
        items=[_website_to_out(w) for w in page["items"]],
        next_cursor=page["next_cursor"],
    ), 200


@api_bp.route("/projects/<project_id>/websites", methods=["POST"])
@api_endpoint
@document(
    request=WebsiteIn,
    response_201=WebsiteOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Websites"],
    summary="Create a website",
    description=(
        "Creates a website inside ``project_id``. Returns the full "
        "website resource (``WebsiteOut``) plus a ``Location`` header "
        "pointing at ``/api/v1/websites/<id>``. The ``url`` must start "
        "with ``http://`` or ``https://``; unknown JSON keys are "
        "rejected with 400."
    ),
)
def create_website(
    project_id: str, body: WebsiteIn
) -> tuple[WebsiteOut, int] | tuple[Response, int]:
    """Create a website inside a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    website = _website_from_in(project_id, body)
    website_id = get_db().create_website(website)
    refreshed = get_db().get_website(website_id)
    if refreshed is None:
        raise ConflictError("website failed to persist")

    # The legacy handler attached a ``Location`` header to the response.
    # ``@document`` serialises BaseModel returns via ``jsonify``, which
    # builds a fresh Response object, so the header must be applied to
    # *that* response -- not the model. We pre-build a Response here
    # to preserve the header.
    payload = _website_to_out(refreshed)
    response = jsonify(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
    response.headers["Location"] = f"/api/v1/websites/{website_id}"
    return response, 201


@api_bp.route("/websites/<website_id>", methods=["GET"])
@api_endpoint
@document(
    response_200=WebsiteOut,
    errors=[401, 403, 404],
    tags=["Websites"],
    summary="Get a website by ID",
    description="Returns the website resource (``WebsiteOut``).",
)
def get_website(
    website_id: str,
) -> tuple[WebsiteOut, int] | tuple[Response, int] | Response:
    """Get a website by id."""
    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    return _website_to_out(website), 200


@api_bp.route("/websites/<website_id>", methods=["PUT"])
@api_endpoint
@document(
    request=WebsitePut,
    response_200=WebsiteOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Websites"],
    summary="Replace a website",
    description=(
        "Full replace of a website's editable fields. Server-managed "
        "fields (``created_at``, ``last_scraped``, ``last_tested``, "
        "``page_count``, ``project_id``, ``members``, "
        "``discovery_history``) are preserved from the existing record; "
        "clients cannot reassign a website to a different project via "
        "PUT."
    ),
)
def replace_website(
    website_id: str, body: WebsitePut
) -> tuple[WebsiteOut, int] | tuple[Response, int] | Response:
    """Full replace of a website's editable fields.

    Server-managed fields (created_at, last_scraped, last_tested,
    page_count, project_id, members) are preserved from the existing
    record — clients cannot reassign a website to a different project
    via PUT.
    """
    existing = get_db().get_website(website_id)
    if existing is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    replaced = _website_from_in(existing.project_id, body)
    replaced.mongo_id = existing.mongo_id
    replaced.created_at = existing.created_at
    replaced.last_scraped = existing.last_scraped
    replaced.last_tested = existing.last_tested
    replaced.page_count = existing.page_count
    replaced.discovery_history = list(existing.discovery_history)
    replaced.members = list(existing.members)
    if not get_db().update_website(replaced):
        raise ConflictError("website could not be updated")
    return _website_to_out(replaced), 200


@api_bp.route("/websites/<website_id>", methods=["PATCH"])
@api_endpoint
@document(
    request=WebsitePatch,
    response_200=WebsiteOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Websites"],
    summary="Partially update a website",
    description=(
        "Partial update -- only fields present in the request body are "
        "applied. Returns the full updated ``WebsiteOut``."
    ),
)
def patch_website(
    website_id: str, body: WebsitePatch
) -> tuple[WebsiteOut, int] | tuple[Response, int] | Response:
    """Partial update — only fields present in the request body are changed."""
    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    patched = _apply_patch_pyd(website, body)
    if not get_db().update_website(patched):
        raise ConflictError("website could not be updated")
    return _website_to_out(patched), 200


@api_bp.route("/websites/<website_id>", methods=["DELETE"])
@api_endpoint
@document(
    response_204=Empty,
    errors=[401, 403, 404],
    tags=["Websites"],
    summary="Delete a website",
    description=(
        "Deletes the website and cascades to its pages, PDFs, and test "
        "results. Returns ``204 No Content`` with an empty body."
    ),
)
def delete_website(website_id: str) -> tuple[Empty, int] | tuple[Response, int]:
    """Delete a website. Cascades to pages, PDFs, and test results."""
    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, website_id=website_id
    )
    get_db().delete_website(website_id)
    return Empty(), 204


# ---------------------------------------------------------------------------
# Pages (REST shape — uses the @api_endpoint scaffolding).
#
# Mirrors the conventions from the websites and scheduled-tests blocks
# above. Action endpoints (`/test`, `/test-results`, `/test-states`,
# `/test-sessions`) remain on the legacy URL surface for now — they
# share async-job tracking with the schedules `/runs` endpoint and are
# scoped out of this PR per ``docs/REST_API_ROADMAP.md`` §5.3.
# ---------------------------------------------------------------------------


def _page_to_out(page: Page) -> PageOut:
    """Project a :class:`Page` to a :class:`PageOut` model.

    Mirrors the legacy ``_serialize_page()`` shape byte-for-byte:
    datetimes emit as ISO 8601 strings, the Mongo ``_id`` surfaces via
    the ``id`` property (already stringified), and Drupal-sync fields
    are intentionally omitted (the legacy serializer dropped them too).
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return PageOut(
        id=page.id,
        website_id=page.website_id,
        url=page.url,
        title=page.title,
        status=page.status.value,
        priority=page.priority,
        depth=page.depth,
        discovered_at=_iso(page.discovered_at),
        discovered_from=page.discovered_from,
        discovery_run_id=page.discovery_run_id,
        last_tested=_iso(page.last_tested),
        violation_count=page.violation_count,
        warning_count=page.warning_count,
        info_count=page.info_count,
        discovery_count=page.discovery_count,
        pass_count=page.pass_count,
        test_duration_ms=page.test_duration_ms,
        error_reason=page.error_reason,
        is_in_latest_discovery=page.is_in_latest_discovery,
        screenshot_path=page.screenshot_path,
        setup_script_id=page.setup_script_id,
        linked_pdf_document_id=page.linked_pdf_document_id,
    )


def _page_from_in(website_id: str, body: PageIn | PagePut) -> Page:
    """Construct a new :class:`Page` from a POST or PUT body.

    URL validation matches the legacy ``_validate_url`` rule (the
    ``http(s)://`` prefix check). ``title`` is normalised: whitespace-only
    strings collapse to ``None``. ``priority`` defaults to ``"normal"``
    when the client omits it (matching the legacy default).
    """
    url = _validate_url(body.url, field="url")
    title = (
        body.title.strip()
        if body.title is not None and body.title.strip()
        else None
    )
    priority = body.priority if body.priority is not None else "normal"
    return Page(
        website_id=website_id,
        url=url,
        title=title,
        priority=priority,
        setup_script_id=body.setup_script_id,
    )


def _apply_page_patch(page: Page, body: PagePatch) -> Page:
    """Apply a :class:`PagePatch` to ``page`` in place.

    Only fields the client *sent* are applied; ``model_fields_set`` is
    the distinguishing signal between "not in body" and "explicitly null".
    Matches the legacy "patch only present keys" contract.
    """
    fields_set = body.model_fields_set
    if "title" in fields_set:
        page.title = (
            body.title.strip()
            if body.title is not None and body.title.strip()
            else None
        )
    if "priority" in fields_set and body.priority is not None:
        page.priority = body.priority
    if "setup_script_id" in fields_set:
        page.setup_script_id = body.setup_script_id
    return page


@api_bp.route("/websites/<website_id>/pages", methods=["GET"])
@api_endpoint
@document(
    response_200=PageListOut,
    errors=[400, 401, 403, 404],
    tags=["Pages"],
    summary="List pages in a website",
    description=(
        "Returns pages belonging to ``website_id`` with cursor "
        "pagination. The response shape is ``{items, next_cursor}``. "
        "Supports an optional ``status`` filter that must match a "
        "``PageStatus`` enum value."
    ),
)
def list_pages_for_website(
    website_id: str,
) -> tuple[PageListOut, int] | tuple[Response, int] | Response:
    """List pages for a website with cursor pagination."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"website_id": website_id}
    status_raw = request.args.get("status")
    if status_raw is not None:
        try:
            query["status"] = PageStatus(status_raw).value
        except ValueError as exc:
            raise ValidationError(
                "status is not a recognized PageStatus value",
                errors=(_FieldError(field="status", code="invalid_value", message=str(exc)),),
            ) from exc
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(
                    _FieldError(
                        field="cursor.last_id",
                        code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc

    docs = list(get_db().pages.find(query).sort("_id", -1).limit(limit + 1))
    pages = [Page.from_dict(doc) for doc in docs]
    page = paginate(
        pages, limit=limit, get_id=lambda p: str(p.mongo_id) if p.mongo_id else ""
    )
    return PageListOut(
        items=[_page_to_out(p) for p in page["items"]],
        next_cursor=page["next_cursor"],
    ), 200


@api_bp.route("/websites/<website_id>/pages", methods=["POST"])
@api_endpoint
@document(
    request=PageIn,
    response_201=PageOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Pages"],
    summary="Create a page",
    description=(
        "Creates a page on ``website_id``. Returns the full page "
        "resource plus a ``Location`` header pointing at "
        "``/api/v1/pages/<id>``. Note: the underlying database call is "
        "upsert-by-(website_id, url) -- posting a duplicate URL returns "
        "the existing page rather than creating a new one. The 201 "
        "status reflects the resulting resource either way."
    ),
)
def create_page(
    website_id: str, body: PageIn
) -> tuple[Response, int]:
    """Create a page on a website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    page = _page_from_in(website_id, body)
    page_id = get_db().create_page(page)
    refreshed = get_db().get_page(page_id)
    if refreshed is None:
        raise ConflictError("page failed to persist")

    # ``@document`` serialises a BaseModel return through jsonify(), but
    # the Location header has to live on the same response. Build the
    # response explicitly so we can attach the header.
    payload = _page_to_out(refreshed)
    response = jsonify(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
    response.headers["Location"] = f"/api/v1/pages/{page_id}"
    return response, 201


@api_bp.route("/pages/<page_id>", methods=["GET"])
@api_endpoint
@document(
    response_200=PageOut,
    errors=[401, 403, 404],
    tags=["Pages"],
    summary="Get a page by ID",
    description="Returns the page resource (``PageOut``).",
)
def get_page_resource(
    page_id: str,
) -> tuple[PageOut, int] | tuple[Response, int] | Response:
    """Get a page by id."""
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=page.website_id
    )
    return _page_to_out(page), 200


@api_bp.route("/pages/<page_id>", methods=["PUT"])
@api_endpoint
@document(
    request=PagePut,
    response_200=PageOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Pages"],
    summary="Replace a page",
    description=(
        "Full replace of a page's editable fields. Server-managed "
        "fields (status, counts, dates, screenshot, drupal sync, "
        "discovery metadata) are preserved. ``website_id`` and ``url`` "
        "are locked -- the (website_id, url) pair is the page's "
        "identity. A PUT that changes ``url`` is rejected with 400."
    ),
)
def replace_page(
    page_id: str, body: PagePut
) -> tuple[PageOut, int] | tuple[Response, int] | Response:
    """Full replace of a page's editable fields."""
    existing = get_db().get_page(page_id)
    if existing is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=existing.website_id
    )
    replaced = _page_from_in(existing.website_id, body)
    if replaced.url != existing.url:
        raise ValidationError(
            "url cannot be changed; (website_id, url) is the page identity",
            errors=(_FieldError(field="url", code="immutable", message="immutable"),),
        )
    replaced.mongo_id = existing.mongo_id
    replaced.discovered_at = existing.discovered_at
    replaced.discovered_from = existing.discovered_from
    replaced.discovery_run_id = existing.discovery_run_id
    replaced.last_tested = existing.last_tested
    replaced.status = existing.status
    replaced.violation_count = existing.violation_count
    replaced.warning_count = existing.warning_count
    replaced.info_count = existing.info_count
    replaced.discovery_count = existing.discovery_count
    replaced.pass_count = existing.pass_count
    replaced.test_duration_ms = existing.test_duration_ms
    replaced.depth = existing.depth
    replaced.error_reason = existing.error_reason
    replaced.is_in_latest_discovery = existing.is_in_latest_discovery
    replaced.screenshot_path = existing.screenshot_path
    replaced.visible_to_users = list(existing.visible_to_users)
    replaced.is_flagged_for_discovery = existing.is_flagged_for_discovery
    replaced.discovery_reasons = list(existing.discovery_reasons)
    replaced.discovery_areas = list(existing.discovery_areas)
    replaced.discovery_notes_private = existing.discovery_notes_private
    replaced.discovery_notes_public = existing.discovery_notes_public
    replaced.drupal_discovered_page_uuid = existing.drupal_discovered_page_uuid
    replaced.drupal_sync_status = existing.drupal_sync_status
    replaced.drupal_last_synced = existing.drupal_last_synced
    replaced.drupal_error_message = existing.drupal_error_message
    replaced.linked_pdf_document_id = existing.linked_pdf_document_id
    if not get_db().update_page(replaced):
        raise ConflictError("page could not be updated")
    return _page_to_out(replaced), 200


@api_bp.route("/pages/<page_id>", methods=["PATCH"])
@api_endpoint
@document(
    request=PagePatch,
    response_200=PageOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Pages"],
    summary="Partially update a page",
    description=(
        "Partial update -- only fields present in the request body are "
        "applied. Returns the full updated ``PageOut``."
    ),
)
def patch_page(
    page_id: str, body: PagePatch
) -> tuple[PageOut, int] | tuple[Response, int] | Response:
    """Partial update — only fields present in the request body are changed."""
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )
    patched = _apply_page_patch(page, body)
    if not get_db().update_page(patched):
        raise ConflictError("page could not be updated")
    return _page_to_out(patched), 200


@api_bp.route("/pages/<page_id>", methods=["DELETE"])
@api_endpoint
@document(
    response_204=Empty,
    errors=[401, 403, 404],
    tags=["Pages"],
    summary="Delete a page",
    description=(
        "Deletes the page and cascades to its test results. Returns "
        "``204 No Content`` with an empty body."
    ),
)
def delete_page_resource(page_id: str) -> tuple[Empty, int] | tuple[Response, int]:
    """Delete a page and its test results."""
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )
    get_db().delete_page(page_id)
    return Empty(), 204


# ---------------------------------------------------------------------------
# Page sub-resources (REST shape — §5.3 of docs/REST_API_ROADMAP.md).
#
# Replaces the legacy HTML routes on `pages_bp`:
#   - GET /pages/<id>/violations  → just redirected to the page view
#   - GET/POST /pages/<id>/matrix → server-rendered form
#   - POST /pages/<id>/cancel-test → ad-hoc `success` envelope
#
# The HTML routes stay alive until the issue #21 frontend migration —
# they keep their old shapes; this surface returns RFC 7807 problems.
# ---------------------------------------------------------------------------


@api_bp.route("/pages/<page_id>/violations", methods=["GET"])
@api_endpoint
@document(
    response_200=PageViolationsOut,
    errors=[401, 403, 404],
    tags=["Pages"],
    summary="Get the latest violations for a page",
    description=(
        "Returns the latest test result's issue buckets for a page "
        "(``violations``, ``warnings``, ``info``, ``discovery``, "
        "``ai_findings``) so a UI can render a violations table "
        "without paging through the full result history. When the page "
        "has never been tested, the buckets are empty and "
        "``test_result_id`` / ``tested_at`` are ``null``."
    ),
)
def get_page_violations(
    page_id: str,
) -> tuple[PageViolationsOut, int] | tuple[Response, int] | Response:
    """Return the latest test result's issue buckets for a page."""
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=page.website_id
    )

    result = get_db().get_latest_test_result(page_id)

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    if result is None:
        return PageViolationsOut(
            page_id=page_id,
            test_result_id=None,
            tested_at=None,
            violations=[],
            warnings=[],
            info=[],
            discovery=[],
            ai_findings=[],
        ), 200

    return PageViolationsOut(
        page_id=page_id,
        test_result_id=result.id,
        tested_at=_iso(result.test_date),
        violations=[v.to_dict() for v in result.violations],
        warnings=[v.to_dict() for v in result.warnings],
        info=[v.to_dict() for v in result.info],
        discovery=[v.to_dict() for v in result.discovery],
        ai_findings=[f.to_dict() for f in result.ai_findings],
    ), 200


def _matrix_to_out(
    matrix: TestStateMatrix, *, page_id: str, website_id: str
) -> PageMatrixOut:
    """Project a :class:`TestStateMatrix` to a :class:`PageMatrixOut`.

    Datetimes are emitted as ISO 8601; the legacy ``matrix`` (row/column
    boolean grid) is intentionally dropped because the canonical
    storage is ``combinations``. ``id`` is ``None`` for an unsaved
    default matrix.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return PageMatrixOut(
        id=matrix.id,
        page_id=page_id,
        website_id=website_id,
        scripts=[
            ScriptStateDefinitionOut(
                script_id=s.script_id,
                script_name=s.script_name,
                test_before=s.test_before,
                test_after=s.test_after,
                execution_order=s.execution_order,
            )
            for s in matrix.scripts
        ],
        combinations=[dict(c) for c in matrix.combinations],
        created_date=_iso(matrix.created_date),
        last_modified=_iso(matrix.last_modified),
        created_by=matrix.created_by,
    )


@api_bp.route("/pages/<page_id>/matrix", methods=["GET"])
@api_endpoint
@document(
    response_200=PageMatrixOut,
    errors=[401, 403, 404],
    tags=["Pages"],
    summary="Get the test-state matrix for a page",
    description=(
        "Returns the test-state matrix for a page. A page has at most "
        "one matrix; if none has been saved yet, returns a default "
        "in-memory matrix derived from the page's currently-enabled "
        "multi-state scripts with sequential combinations. ``id`` is "
        "``null`` in that case so clients can detect a not-yet-"
        "persisted matrix."
    ),
)
def get_page_matrix(
    page_id: str,
) -> tuple[PageMatrixOut, int] | tuple[Response, int] | Response:
    """Read the test-state matrix for a page."""
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=page.website_id
    )

    matrix = get_db().get_test_state_matrix_by_page(page_id)
    if matrix is None:
        matrix = TestStateMatrix(page_id=page_id, website_id=page.website_id)
        scripts = get_db().get_scripts_for_page_v2(
            page_id=page_id, website_id=page.website_id, enabled_only=False
        )
        testable_scripts = [
            s for s in scripts
            if s.enabled and (s.test_before_execution or s.test_after_execution)
        ]
        for script in testable_scripts:
            if not script.id:
                continue
            matrix.scripts.append(ScriptStateDefinition(
                script_id=script.id,
                script_name=script.name,
                test_before=script.test_before_execution,
                test_after=script.test_after_execution,
                execution_order=len(matrix.scripts),
            ))
        if matrix.scripts:
            matrix.initialize_matrix()

    return _matrix_to_out(
        matrix, page_id=page_id, website_id=page.website_id
    ), 200


def _validate_matrix_combinations(
    combinations: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Validate that each combination value is ``before|after|none``.

    Pydantic constrains the outer types (a list of ``dict[str, str]``)
    but cannot enumerate values in a free-form dict where the keys are
    arbitrary script IDs. This helper performs the value-level check
    the legacy ``_parse_matrix_combinations`` did, raising a
    ``ValidationError`` with the same field path so the wire shape of
    the error envelope stays identical.
    """
    valid: tuple[str, ...] = ("before", "after", "none")
    for idx, combo in enumerate(combinations):
        for sid, state in combo.items():
            if state not in valid:
                raise ValidationError(
                    f"combinations[{idx}].{sid} must be one of before|after|none",
                    errors=(
                        _FieldError(
                            field=f"combinations[{idx}].{sid}",
                            code="invalid_value",
                            message="must be before|after|none",
                        ),
                    ),
                )
    return combinations


@api_bp.route("/pages/<page_id>/matrix", methods=["PUT"])
@api_endpoint
@document(
    request=PageMatrixIn,
    response_200=PageMatrixOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Pages"],
    summary="Replace the test-state matrix for a page",
    description=(
        "Full replace of the page's test-state matrix. The ``scripts`` "
        "array on the persisted matrix is always rebuilt from the "
        "page's currently-enabled multi-state scripts at save time -- "
        "clients don't submit it. ``combinations`` is required and "
        "each value must be ``\"before\"``, ``\"after\"``, or "
        "``\"none\"``. ``script_order`` is optional and only "
        "repositions scripts already present on the page. The verb is "
        "PUT because the matrix is an idempotent 1:1 sub-resource of "
        "the page -- there is no PATCH or DELETE; clearing it means "
        "PUTing an empty ``combinations`` array."
    ),
)
def replace_page_matrix(
    page_id: str, body: PageMatrixIn
) -> tuple[PageMatrixOut, int] | tuple[Response, int] | Response:
    """Full replace of the page's test-state matrix."""
    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )

    combinations = _validate_matrix_combinations(body.combinations)
    order_map: dict[str, int] = {
        entry.script_id: entry.execution_order
        for entry in (body.script_order or ())
    }

    scripts = get_db().get_scripts_for_page_v2(
        page_id=page_id, website_id=page.website_id, enabled_only=False
    )
    testable_scripts = [
        s for s in scripts
        if s.enabled and (s.test_before_execution or s.test_after_execution)
    ]

    matrix = get_db().get_test_state_matrix_by_page(page_id)
    if matrix is None:
        matrix = TestStateMatrix(page_id=page_id, website_id=page.website_id)

    matrix.scripts = []
    for idx, script in enumerate(testable_scripts):
        if not script.id:
            continue
        matrix.scripts.append(ScriptStateDefinition(
            script_id=script.id,
            script_name=script.name,
            test_before=script.test_before_execution,
            test_after=script.test_after_execution,
            execution_order=order_map.get(script.id, idx),
        ))
    matrix.scripts.sort(key=lambda s: s.execution_order)
    matrix.combinations = combinations
    matrix.matrix = {}

    if matrix.mongo_id:
        get_db().update_test_state_matrix(matrix)
    else:
        new_id = get_db().create_test_state_matrix(matrix)
        refreshed = get_db().get_test_state_matrix(new_id)
        if refreshed is None:
            raise ConflictError("matrix failed to persist")
        matrix = refreshed

    return _matrix_to_out(
        matrix, page_id=page_id, website_id=page.website_id
    ), 200


@api_bp.route("/pages/<page_id>/test-runs/latest", methods=["GET"])
@api_endpoint
@document(
    response_200=PageTestRunLatestOut,
    errors=[401, 403, 404],
    tags=["Test runs"],
    summary="Get the page's current/most-recent test-run status",
    description=(
        "Surfaces just the run-level state so a UI can drive a 'test "
        "in flight' indicator without paging the whole result history. "
        "``active_task_id`` is the task-runner id when the worker is "
        "still running, otherwise null. ``last_test_result_id`` points "
        "at the most recent stored result regardless of outcome, so "
        "clients can deep-link to the historical view."
    ),
)
def get_page_test_run_latest(
    page_id: str,
) -> tuple[PageTestRunLatestOut, int] | tuple[Response, int] | Response:
    """Read the page's current/most-recent test-run status."""
    from auto_a11y.core.task_runner import task_runner

    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        website_id=page.website_id,
    )

    task_pattern = f"test_page_{page_id}_"
    active_task_id: str | None = None
    for active in task_runner.get_active_tasks():
        if active.startswith(task_pattern):
            active_task_id = active
            break

    # Latest TestResult — limit=1 so we don't pay to hydrate a long
    # history just to read the id.
    latest_results = get_db().get_test_results(page_id=page_id, limit=1)
    last_test_result_id = (
        latest_results[0].id if latest_results else None
    )

    return PageTestRunLatestOut(
        page_id=page_id,
        status=page.status.value,
        last_tested=(
            page.last_tested.isoformat() if page.last_tested else None
        ),
        last_test_result_id=last_test_result_id,
        active_task_id=active_task_id,
    ), 200


@api_bp.route("/pages/<page_id>/test-runs/latest/cancel", methods=["POST"])
@api_endpoint
@document(
    response_202=PageTestRunCancelOut,
    errors=[401, 403, 404, 409],
    tags=["Test runs"],
    summary="Cancel the page's in-flight test run",
    description=(
        "Cancellation is asynchronous: the worker polls the task's "
        "cancellation flag and exits at its next checkpoint. The "
        "page status flips immediately to ``DISCOVERED`` (never "
        "tested) or ``TESTED`` (has prior results) so the UI does "
        "not lock up on a phantom run. Returns 409 when the page is "
        "not in QUEUED or TESTING (legacy ``/cancel-test`` returned "
        "200 with ``success: false`` for this; the REST surface uses "
        "409 so callers can branch on status_code alone)."
    ),
)
def cancel_page_test_run_latest(
    page_id: str,
) -> tuple[PageTestRunCancelOut, int] | tuple[Response, int] | Response:
    """Request cancellation of the page's in-flight test run."""
    from auto_a11y.core.task_runner import task_runner

    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )

    if page.status not in (PageStatus.QUEUED, PageStatus.TESTING):
        raise ConflictError(
            f"page {page_id} is not being tested (current status: {page.status.value})"
        )

    task_pattern = f"test_page_{page_id}_"
    target_task_id: str | None = None
    for active_task_id in task_runner.get_active_tasks():
        if active_task_id.startswith(task_pattern):
            target_task_id = active_task_id
            break

    cancelled = False
    if target_task_id is not None:
        try:
            cancelled = task_runner.cancel_task(target_task_id)
        except Exception as exc:
            logger.error(f"Error cancelling page task {target_task_id}: {exc}")

    # Even if the task was already past the cancel checkpoint or had
    # never been spawned (status set to QUEUED by an earlier failed
    # path), flip the page back to a non-running state so the UI
    # doesn't lock up on a phantom in-flight run.
    if cancelled or page.status == PageStatus.QUEUED:
        test_history = get_db().get_test_results(page_id=page_id, limit=1)
        page.status = PageStatus.TESTED if test_history else PageStatus.DISCOVERED
        get_db().update_page(page)

    refreshed = get_db().get_page(page_id)
    if refreshed is None:
        raise ConflictError(f"page {page_id} disappeared after cancel")

    return PageTestRunCancelOut(
        page_id=page_id,
        task_id=target_task_id,
        cancellation_requested=cancelled,
        status=refreshed.status.value,
    ), 202


# ---------------------------------------------------------------------------
# Top-level test-runs + testing config (REST shape — §5.4 of
# docs/REST_API_ROADMAP.md).
#
# Replaces the hollow legacy routes on `testing_bp`:
#   - POST /testing/run-test    → only updated page.status (no real queue)
#   - POST /testing/batch-test  → only returned a fake batch_id
#   - GET/POST /testing/configure → HTML form
#
# The new routes delegate to :mod:`auto_a11y.core.test_run_service`
# (the same helper the per-page and per-website routes call) so the
# generic top-level URL actually queues work instead of just flipping
# status flags.
# ---------------------------------------------------------------------------


def _parse_optional_bool(raw: Any, *, field: str) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    raise ValidationError(
        f"{field} must be a boolean",
        errors=(
            _FieldError(field=field, code="invalid_type", message="must be bool"),
        ),
    )


def _parse_optional_str(raw: Any, *, field: str) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    raise ValidationError(
        f"{field} must be a string",
        errors=(
            _FieldError(field=field, code="invalid_type", message="must be string"),
        ),
    )


def _parse_required_str(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValidationError(
            f"{field} is required",
            errors=(
                _FieldError(field=field, code="required", message="required"),
            ),
        )
    return raw


def _parse_str_list(raw: Any, *, field: str) -> list[str]:
    if not isinstance(raw, list):
        raise ValidationError(
            f"{field} must be a list of strings",
            errors=(
                _FieldError(field=field, code="invalid_type", message="must be list"),
            ),
        )
    raw_list = _iter_to_any_list(raw)
    result: list[str] = []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, str):
            raise ValidationError(
                f"{field}[{idx}] must be a string",
                errors=(
                    _FieldError(
                        field=f"{field}[{idx}]",
                        code="invalid_type",
                        message="must be string",
                    ),
                ),
            )
        result.append(item)
    return result


@api_bp.route("/test-runs", methods=["POST"])
@api_endpoint
def create_test_run(
) -> tuple[Response, int] | Response:
    """Queue a single-page accessibility test run.

    Body:

        {
          "page_id": "...",                // required
          "enable_multi_state": bool,      // optional, default true
          "website_user_id": "..."         // optional
        }

    Returns 202 with the same handle shape as
    ``POST /api/v1/pages/<id>/test-runs`` — both call
    :func:`auto_a11y.core.test_run_service.start_page_test_run`. The
    per-page URL stays alive for clients that already know the page id
    in the path; this top-level form is for callers who already have
    the page id in their request body (e.g. a "test this page"
    bookmark service or a queue worker).

    Use :func:`create_test_runs_batch` for multiple pages — looping
    this endpoint client-side is fine but the batch shape is more
    convenient.
    """
    from auto_a11y.core.test_run_service import (
        BrowserDisabledError,
        BrowserRemoteError,
        PageNotFoundError,
        start_page_test_run,
    )

    body = _require_dict_body()
    page_id = _parse_required_str(body.get("page_id"), field="page_id")
    enable_multi_state = _parse_optional_bool(
        body.get("enable_multi_state"), field="enable_multi_state"
    )
    if enable_multi_state is None:
        enable_multi_state = True
    website_user_id = _parse_optional_str(
        body.get("website_user_id"), field="website_user_id"
    )

    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
    )

    try:
        handle = start_page_test_run(
            get_db(),
            get_app_config(),
            page_id,
            enable_multi_state=enable_multi_state,
            website_user_id=website_user_id,
        )
    except BrowserDisabledError as exc:
        raise ConflictError(str(exc)) from exc
    except BrowserRemoteError as exc:
        raise ConflictError(str(exc)) from exc
    except PageNotFoundError as exc:
        # Race: the page existed at the auth check but disappeared
        # between then and the service call. Treat as a fresh 404.
        raise NotFoundError(str(exc)) from exc

    return jsonify({
        "job_id": handle.job_id,
        "page_id": handle.page_id,
        "multi_state": handle.multi_state,
        "status": "queued",
    }), 202


@api_bp.route("/test-runs/batch", methods=["POST"])
@api_endpoint
def create_test_runs_batch(
) -> tuple[Response, int] | Response:
    """Queue accessibility test runs for several pages.

    Body:

        {
          "page_ids": ["...", "..."],      // required, 1..N entries
          "enable_multi_state": bool,      // optional, default true
          "website_user_id": "..."         // optional, applied to every run
        }

    Each page is queued through
    :func:`auto_a11y.core.test_run_service.start_page_test_run`, the
    same helper :func:`create_test_run` and the per-page endpoint use.
    Pages from different websites can be mixed — auth is enforced
    per-page so a non-admin caller will fail on the first page they
    don't have access to.

    On success returns 202 with a list of handles, one per queued
    page, in the same order as the input. If ``page_ids`` contains an
    unknown id, the request fails with 404 *before* anything is queued,
    so the operation is all-or-nothing.

    Use :func:`test_website` (``POST /websites/<id>/test-runs``) when
    you want to test every page on a website — it handles the
    per-user sequential execution and PDF audit folding that this
    endpoint deliberately doesn't.
    """
    from auto_a11y.core.test_run_service import (
        BrowserDisabledError,
        BrowserRemoteError,
        PageNotFoundError,
        start_page_test_run,
    )

    body = _require_dict_body()
    page_ids = _parse_str_list(body.get("page_ids"), field="page_ids")
    if not page_ids:
        raise ValidationError(
            "page_ids must contain at least one entry",
            errors=(
                _FieldError(
                    field="page_ids", code="too_short", message="min 1 entry"
                ),
            ),
        )
    enable_multi_state = _parse_optional_bool(
        body.get("enable_multi_state"), field="enable_multi_state"
    )
    if enable_multi_state is None:
        enable_multi_state = True
    website_user_id = _parse_optional_str(
        body.get("website_user_id"), field="website_user_id"
    )

    # Validate every page up front so we don't half-queue on a typo.
    pages: list[Page] = []
    for page_id in page_ids:
        page = get_db().get_page(page_id)
        if page is None:
            raise NotFoundError(f"page {page_id} not found")
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id
        )
        pages.append(page)

    handles: list[dict[str, Any]] = []
    for page in pages:
        assert page.id is not None
        try:
            handle = start_page_test_run(
                get_db(),
                get_app_config(),
                page.id,
                enable_multi_state=enable_multi_state,
                website_user_id=website_user_id,
            )
        except BrowserDisabledError as exc:
            raise ConflictError(str(exc)) from exc
        except BrowserRemoteError as exc:
            raise ConflictError(str(exc)) from exc
        except PageNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
        handles.append({
            "job_id": handle.job_id,
            "page_id": handle.page_id,
            "multi_state": handle.multi_state,
        })

    return jsonify({
        "status": "queued",
        "pages_queued": len(handles),
        "runs": handles,
    }), 202


# Runtime config keys the testing config endpoint exposes.
# Tuple form keeps ordering deterministic across GET/PUT.
_TESTING_CONFIG_FIELDS: tuple[tuple[str, str, type[Any]], ...] = (
    # (json_field, Config attribute name, expected python type)
    ("parallel_tests", "PARALLEL_TESTS", int),
    ("test_timeout", "TEST_TIMEOUT", int),
    ("run_ai_analysis", "RUN_AI_ANALYSIS", bool),
    ("browser_headless", "BROWSER_HEADLESS", bool),
    ("viewport_width", "BROWSER_VIEWPORT_WIDTH", int),
    ("viewport_height", "BROWSER_VIEWPORT_HEIGHT", int),
    ("pages_per_page", "PAGES_PER_PAGE", int),
    ("max_pages_per_page", "MAX_PAGES_PER_PAGE", int),
    ("show_error_codes", "SHOW_ERROR_CODES", bool),
)


def _read_testing_config_field(
    cfg: Any, *, attr: str, py_type: type[Any]
) -> Any:
    """Read a single config attribute with a sensible per-type default.

    Several fields are optional on the Config dataclass (``getattr`` in
    the legacy route uses defaults like ``100`` / ``500`` / ``False``).
    Returning a typed default — instead of letting ``None`` leak into
    the JSON — keeps the response schema stable across deployments
    that haven't set every flag in ``.env``.
    """
    if py_type is bool:
        return bool(getattr(cfg, attr, False))
    if py_type is int:
        return int(getattr(cfg, attr, 0))
    return getattr(cfg, attr)


def _serialize_testing_config(cfg: Any) -> dict[str, Any]:
    return {
        field: _read_testing_config_field(cfg, attr=attr, py_type=py_type)
        for field, attr, py_type in _TESTING_CONFIG_FIELDS
    }


@api_bp.route("/testing/config", methods=["GET"])
@api_endpoint
def get_testing_config() -> tuple[Response, int] | Response:
    """Read the runtime testing config.

    Mirrors the legacy GET ``/testing/configure`` form view, but emits
    JSON only (the HTML form has been retained on ``testing_bp`` for
    the admin UI). Fields:

    - ``parallel_tests`` (int)
    - ``test_timeout`` (int, ms)
    - ``run_ai_analysis`` (bool)
    - ``browser_headless`` (bool)
    - ``viewport_width`` / ``viewport_height`` (int)
    - ``pages_per_page`` / ``max_pages_per_page`` (int — pagination defaults)
    - ``show_error_codes`` (bool — developer/debug toggle)

    Superadmin-only because this surface also gates the PUT writer
    and we don't want two role checks to drift.
    """
    require_superadmin()
    return jsonify(_serialize_testing_config(get_app_config()))


@api_bp.route("/testing/config", methods=["PUT"])
@api_endpoint
def replace_testing_config() -> tuple[Response, int] | Response:
    """Update runtime testing config keys.

    Body shape:

        {
          "parallel_tests": 4,
          "browser_headless": false,
          ...
        }

    Any subset of :data:`_TESTING_CONFIG_FIELDS` is accepted; missing
    keys are left untouched (PUT here is a *full-update-or-no-change*,
    matching the legacy POST handler that only wrote the keys present
    in the body). Unknown keys raise 400 so typos surface immediately
    instead of silently dropping.

    Type-validates each present key — pyright/mypy strict mode wants
    real ``bool`` / ``int`` values, not the JSON-coerced ``Any`` the
    legacy route happily passed straight into the Config attributes.

    Returns the full post-update config so callers can confirm the
    effective values without a follow-up GET.

    **In-process only:** writes go to the live :class:`Config` object,
    not to ``.env`` or a database — the changes survive until the
    process restarts. Persisting these settings is out of scope for
    #27; an admin-settings PR (#36) covers the persistent equivalents.
    """
    require_superadmin()
    body = _require_dict_body()

    known_fields = {f for f, _, _ in _TESTING_CONFIG_FIELDS}
    unknown = set(body) - known_fields
    if unknown:
        sorted_unknown = sorted(unknown)
        raise ValidationError(
            f"unknown config keys: {sorted_unknown}",
            errors=tuple(
                _FieldError(
                    field=key, code="unknown_field", message="unknown config key"
                )
                for key in sorted_unknown
            ),
        )

    cfg = get_app_config()
    for field, attr, py_type in _TESTING_CONFIG_FIELDS:
        if field not in body:
            continue
        value: Any = body[field]
        if py_type is bool:
            if not isinstance(value, bool):
                raise ValidationError(
                    f"{field} must be a boolean",
                    errors=(
                        _FieldError(
                            field=field, code="invalid_type", message="must be bool"
                        ),
                    ),
                )
            setattr(cfg, attr, value)
        elif py_type is int:
            # ``isinstance(True, int)`` is True in Python — exclude bools
            # so a stray ``true`` in the JSON isn't coerced to 1.
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValidationError(
                    f"{field} must be an integer",
                    errors=(
                        _FieldError(
                            field=field, code="invalid_type", message="must be int"
                        ),
                    ),
                )
            setattr(cfg, attr, value)
        else:
            setattr(cfg, attr, value)

    return jsonify(_serialize_testing_config(cfg))


# Reports API (REST shape — §5.5 of docs/REST_API_ROADMAP.md).
#
# Replaces the hollow ``POST /projects/<id>/reports`` placeholder that
# used to live here. The new routes delegate to
# :mod:`auto_a11y.core.report_run_service` so the legacy
# ``reports_bp`` blueprint and these endpoints submit jobs through the
# same code path.
#
# Out of scope for this commit (deferred):
#   - GET /api/v1/reports/<id>/file (download)
#   - DELETE /api/v1/reports/<id>   (delete)
#   - GET /api/v1/projects/<id>/report-summary
# These need a separate report-record collection so the opaque report
# id can map to a filename/path independent of the JobManager TTL —
# follow-up PR.


def _capture_report_runtime() -> tuple[Any, dict[str, Any], str, Path]:
    """Snapshot the per-request Flask state the service needs.

    Read once at the start of each report route so the background
    thread sees a consistent view of (app, config, language,
    output_dir) — the request context is gone by the time the worker
    runs.
    """
    from flask import current_app
    from auto_a11y.web.fluent import get_current_locale as _get_locale

    app = getattr(current_app, "_get_current_object")()
    config_snapshot: dict[str, Any] = get_app_config().__dict__.copy()
    locale_obj = _get_locale()
    language = str(locale_obj) if locale_obj else "en"
    output_dir = Path(get_app_config().REPORTS_DIR)
    return app, config_snapshot, language, output_dir


def _handle_report_service_errors(exc: Exception) -> tuple[Response, int]:
    """Map :mod:`report_run_service` exceptions to RFC 7807 responses.

    Called inside the route handlers' ``try`` blocks. The exceptions
    are imported lazily inside the route bodies (matching the test-run
    service routes) so this helper takes ``Exception`` and tests
    ``type(exc).__name__``; this avoids a top-level import that would
    pull report-generator code into the request path.
    """
    name = type(exc).__name__
    if name in ("ProjectNotFoundError", "WebsiteNotFoundError", "PageNotFoundError"):
        raise NotFoundError(str(exc)) from exc
    if name == "JobNotFoundError":
        raise NotFoundError(str(exc)) from exc
    if name in ("ReportScopeError", "NoTestedPagesError"):
        raise ValidationError(str(exc)) from exc
    raise exc


def _serialize_report_handle(handle: Any) -> ReportCreatedOut:
    """Shape :class:`ReportRunHandle` into the 202 response body."""
    return ReportCreatedOut(
        job_id=handle.job_id,
        scope=handle.scope,
        display_name=handle.display_name,
        status="queued",
    )


@api_bp.route('/pages/<page_id>/reports', methods=['POST'])
@api_endpoint
@document(
    request=PageReportIn,
    response_202=ReportCreatedOut,
    errors=[400, 401, 403, 404],
    tags=["Reports"],
    summary="Queue a single-page accessibility report",
    description=(
        "Queues a background job to generate an accessibility report "
        "for a single page. Returns 202 with the job handle. "
        "``format`` defaults to ``xlsx``; ``include_ai`` defaults to "
        "true. The page's existence is validated up front so the "
        "response is a clean 404 (Problem Details) rather than a 202 "
        "followed by a job that fails on the first generator call."
    ),
)
def generate_page_report(
    page_id: str, body: PageReportIn
) -> tuple[ReportCreatedOut, int] | tuple[Response, int] | Response:
    """Queue a single-page accessibility report."""
    from auto_a11y.core.report_run_service import (
        start_report_generation,
    )

    page = get_db().get_page(page_id)
    if page is None:
        raise NotFoundError(f"page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        website_id=page.website_id,
    )

    report_format = body.format if body.format is not None else "xlsx"
    include_ai = body.include_ai if body.include_ai is not None else True

    app, config_snapshot, language, output_dir = _capture_report_runtime()

    try:
        handle = start_report_generation(
            get_db(),
            scope="page",
            report_format=report_format,
            page_id=page_id,
            include_ai=include_ai,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return _serialize_report_handle(handle), 202


_WEBSITE_REPORT_TYPE_TO_SCOPE: dict[str, str] = {
    "accessibility": "website",
    "page-structure": "page_structure",
    "discovery": "discovery_website",
}


@api_bp.route('/websites/<website_id>/reports', methods=['POST'])
@api_endpoint
@document(
    request=WebsiteReportIn,
    response_202=ReportCreatedOut,
    errors=[400, 401, 403, 404],
    tags=["Reports"],
    summary="Queue a website-scoped report",
    description=(
        "Queues a background job to generate one of three website-"
        "scoped reports, selected by the ``type`` discriminator. "
        "``type`` defaults to ``accessibility``; ``format`` defaults "
        "to ``xlsx``; ``include_ai`` defaults to true and is only "
        "honoured for ``accessibility`` (the other types ignore it). "
        "The ``type`` discriminator collapses the three legacy URLs "
        "(``/generate/website/<id>``, ``/generate/page-structure/<id>``, "
        "``/generate/discovery/website/<id>``) into one endpoint."
    ),
)
def generate_website_report(
    website_id: str, body: WebsiteReportIn,
) -> tuple[ReportCreatedOut, int] | tuple[Response, int] | Response:
    """Queue a website-scoped report."""
    from auto_a11y.core.report_run_service import (
        start_report_generation,
    )

    website = get_db().get_website(website_id)
    if website is None:
        raise NotFoundError(f"website {website_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        website_id=website_id,
    )

    report_format = body.format if body.format is not None else "xlsx"
    report_type = body.type if body.type is not None else "accessibility"
    include_ai = body.include_ai if body.include_ai is not None else True

    app, config_snapshot, language, output_dir = _capture_report_runtime()
    scope = _WEBSITE_REPORT_TYPE_TO_SCOPE[report_type]

    try:
        handle = start_report_generation(
            get_db(),
            scope=scope,
            report_format=report_format,
            website_id=website_id,
            include_ai=include_ai,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return _serialize_report_handle(handle), 202


_PROJECT_REPORT_TYPE_TO_SCOPE: dict[str, str] = {
    "accessibility": "project",
    "discovery": "discovery_project",
    "recordings": "recordings",
    "deduplicated": "deduplicated",
}


@api_bp.route('/projects/<project_id>/reports', methods=['POST'])
@api_endpoint
@document(
    request=ProjectReportIn,
    response_202=ReportCreatedOut,
    errors=[400, 401, 403, 404],
    tags=["Reports"],
    summary="Queue a project-scoped report",
    description=(
        "Queues a background job to generate one of four project-"
        "scoped reports, selected by the ``type`` discriminator. "
        "``type`` defaults to ``accessibility``; ``format`` defaults "
        "to ``xlsx``. The ``type`` discriminator collapses four "
        "legacy URLs (``/generate/project/<id>`` -> ``accessibility``, "
        "``/generate/discovery/project/<id>`` -> ``discovery``, "
        "``/generate/recordings/<id>`` -> ``recordings``, "
        "``/generate/deduplicated`` -> ``deduplicated``)."
    ),
)
def generate_project_report(
    project_id: str, body: ProjectReportIn,
) -> tuple[ReportCreatedOut, int] | tuple[Response, int] | Response:
    """Queue a project-scoped report."""
    from auto_a11y.core.report_run_service import (
        start_report_generation,
    )

    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=project_id,
    )

    report_format = body.format if body.format is not None else "xlsx"
    report_type = body.type if body.type is not None else "accessibility"

    app, config_snapshot, language, output_dir = _capture_report_runtime()
    scope = _PROJECT_REPORT_TYPE_TO_SCOPE[report_type]

    try:
        handle = start_report_generation(
            get_db(),
            scope=scope,
            report_format=report_format,
            project_id=project_id,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return _serialize_report_handle(handle), 202


def _resolve_generic_scope(
    *,
    report_type: str,
    project_id: str | None,
    website_id: str | None,
    page_id: str | None,
) -> str:
    """Pick the right scope for ``POST /reports`` from (type, ids).

    The generic top-level endpoint is the catch-all that lets clients
    request any report shape with one body — the legacy
    ``/generate``, ``/generate/static-html``, and
    ``/generate/deduplicated`` routes collapse here. The scope
    inferred from ``type`` plus the supplied ids dictates which
    generator runs:

    - ``accessibility`` with project_id → ``project``
    - ``accessibility`` with website_id → ``website``
    - ``accessibility`` with page_id    → ``page``
    - ``accessibility`` with none       → ``all`` (all-projects roll-up)
    - ``page-structure``                → ``page_structure`` (requires website_id)
    - ``discovery`` with project_id     → ``discovery_project``
    - ``discovery`` with website_id     → ``discovery_website``
    - ``static-html``                   → ``static_html`` (accepts any/none)
    - ``deduplicated``                  → ``deduplicated`` (accepts any/none)
    - ``recordings``                    → ``recordings`` (requires project_id)
    """
    if report_type == "accessibility":
        if page_id:
            return "page"
        if website_id:
            return "website"
        if project_id:
            return "project"
        return "all"
    if report_type == "page-structure":
        return "page_structure"
    if report_type == "discovery":
        return "discovery_website" if website_id else "discovery_project"
    if report_type == "static-html":
        return "static_html"
    if report_type == "deduplicated":
        return "deduplicated"
    if report_type == "recordings":
        return "recordings"
    # Defensive — should be unreachable given the Pydantic
    # ``GenericReportType`` literal validates ``type`` upstream.
    raise ValidationError(
        f"unsupported type: {report_type}",
        errors=(
            _FieldError(
                field="type", code="invalid_value", message="unsupported type"
            ),
        ),
    )


@api_bp.route('/reports', methods=['POST'])
@api_endpoint
@document(
    request=ReportIn,
    response_202=ReportCreatedOut,
    errors=[400, 401, 403, 404],
    tags=["Reports"],
    summary="Queue a report (generic entry point)",
    description=(
        "Generic top-level report-generation endpoint. Picks the scope "
        "from ``(type, ids)`` -- e.g. ``type=accessibility`` with "
        "``page_id`` queues a page-scoped run, with ``website_id`` a "
        "website-scoped run, etc. Most clients should prefer the "
        "scope-specific routes (``/pages/<id>/reports``, "
        "``/websites/<id>/reports``, ``/projects/<id>/reports``); this "
        "top-level form is for clients that need to switch report type "
        "at runtime without remapping URLs. With no scope id supplied, "
        "asks for an all-projects roll-up (superadmin only)."
    ),
)
def generate_generic_report(
    body: ReportIn,
) -> tuple[ReportCreatedOut, int] | tuple[Response, int] | Response:
    """Generic top-level report-generation endpoint."""
    from auto_a11y.core.report_run_service import (
        start_report_generation,
    )

    report_format = body.format if body.format is not None else "xlsx"
    report_type = body.type if body.type is not None else "accessibility"
    project_id = body.project_id
    website_id = body.website_id
    page_id = body.page_id
    include_ai = body.include_ai if body.include_ai is not None else True

    # Validate target existence + enforce role before we read any
    # request-context machinery. This way a 404/403 doesn't waste a
    # config-snapshot copy.
    if page_id:
        page = get_db().get_page(page_id)
        if page is None:
            raise NotFoundError(f"page {page_id} not found")
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            website_id=page.website_id,
        )
    elif website_id:
        website = get_db().get_website(website_id)
        if website is None:
            raise NotFoundError(f"website {website_id} not found")
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            website_id=website_id,
        )
    elif project_id:
        if get_db().get_project(project_id) is None:
            raise NotFoundError(f"project {project_id} not found")
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            project_id=project_id,
        )
    else:
        # No scope id supplied — only superadmins can request a
        # cross-project ("all") roll-up.
        require_superadmin()

    scope = _resolve_generic_scope(
        report_type=report_type,
        project_id=project_id,
        website_id=website_id,
        page_id=page_id,
    )

    app, config_snapshot, language, output_dir = _capture_report_runtime()

    try:
        handle = start_report_generation(
            get_db(),
            scope=scope,
            report_format=report_format,
            project_id=project_id,
            website_id=website_id,
            page_id=page_id,
            include_ai=include_ai,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return _serialize_report_handle(handle), 202


@api_bp.route('/jobs/<job_id>/restart', methods=['POST'])
@api_endpoint
@document(
    response_202=JobRestartOut,
    errors=[400, 401, 403, 404],
    tags=["Reports"],
    summary="Restart a report-generation job",
    description=(
        "Re-queues a (typically failed or cancelled) report-generation "
        "job. Reads the old job's metadata, files a cancellation "
        "request if it is still pending/running (the worker thread "
        "reads the flag and exits cleanly), and submits a fresh job "
        "with the same scope/type/format. Currently only "
        "``REPORT_GENERATION`` jobs are restartable -- other job types "
        "either auto-restart (discovery) or have side effects that do "
        "not make sense to replay (test runs, PDF audits); restart of "
        "a non-report job returns 400."
    ),
)
def restart_job_rest(
    job_id: str,
) -> tuple[JobRestartOut, int] | tuple[Response, int] | Response:
    """Re-queue a (typically failed/cancelled) report job."""
    from auto_a11y.core.report_run_service import (
        restart_report_generation,
    )

    job_manager = JobManager(get_db())
    old_job = job_manager.get_job(job_id)
    if old_job is None:
        raise NotFoundError(f"job {job_id} not found")
    if old_job.get("job_type") != JobType.REPORT_GENERATION.value:
        raise ValidationError(
            f"job {job_id} is not a report job; restart only supports report_generation",
            errors=(
                _FieldError(
                    field="job_id", code="invalid_value",
                    message="not a report job",
                ),
            ),
        )

    require_authenticated()
    app, config_snapshot, language, output_dir = _capture_report_runtime()

    try:
        handle = restart_report_generation(
            get_db(),
            old_job_id=job_id,
            config=config_snapshot,
            language=language,
            output_dir=output_dir,
            app=app,
        )
    except Exception as exc:
        return _handle_report_service_errors(exc)

    return JobRestartOut(
        old_job_id=job_id,
        job_id=handle.job_id,
        scope=handle.scope,
        display_name=handle.display_name,
        status="queued",
    ), 202


# ---------------------------------------------------------------------------
# Reports retrieval (§5.5 of REST_API_ROADMAP.md — the three routes
# deferred from the generation slice).
#
# Report id = the JobManager job_id of the completed report job. The
# job record already carries (project_id, website_id, created_at,
# metadata.scope, metadata.report_type, result.filename, result.path),
# so we don't need a separate `reports` collection — the opaque id
# maps back to the on-disk filename via job.result.filename.
#
# - GET    /api/v1/reports/<id>/file          download (200 + Content-Disposition)
# - DELETE /api/v1/reports/<id>               delete file + job record (204)
# - GET    /api/v1/projects/<id>/report-summary
# ---------------------------------------------------------------------------


def _resolve_report_job(job_id: str) -> dict[str, Any]:
    """Return the JobManager record for a report job, or raise 404.

    A job is considered a "report" for this surface iff its
    ``job_type`` is ``REPORT_GENERATION``. Other job types living
    under the same JobManager (testing / discovery / pdf_audit) are
    intentionally hidden from this URL space so callers can't use a
    test-run id with the report endpoints.
    """
    job_manager = JobManager(get_db())
    record = job_manager.get_job(job_id)
    if record is None:
        raise NotFoundError(f"report {job_id} not found")
    if record.get("job_type") != JobType.REPORT_GENERATION.value:
        raise NotFoundError(f"report {job_id} not found")
    return record


def _authorize_report_record(record: dict[str, Any]) -> None:
    """Run the project-role auth check that matches the report's scope.

    Reports inherit their access policy from whichever resource the
    underlying job was scoped to:

    - ``project_id`` set → check the project
    - ``website_id`` set → check the website
    - metadata.``page_id`` set → check the page's website
    - none of the above → "all projects" roll-up → must be superadmin

    The check covers ADMIN, AUDITOR, and CLIENT for reads. Mutations
    (DELETE) raise the bar to ADMIN/AUDITOR — see
    :func:`_authorize_report_mutation`.
    """
    project_id_any: Any = record.get("project_id")
    website_id_any: Any = record.get("website_id")
    metadata_any: Any = record.get("metadata") or {}
    metadata = cast(dict[str, Any], metadata_any) if isinstance(metadata_any, dict) else {}
    page_id_any: Any = metadata.get("page_id")

    project_id = project_id_any if isinstance(project_id_any, str) else None
    website_id = website_id_any if isinstance(website_id_any, str) else None
    page_id = page_id_any if isinstance(page_id_any, str) else None

    if page_id:
        page = get_db().get_page(page_id)
        if page is None:
            # Page was deleted after the report was generated — fall
            # back to the website that's still on the job record.
            if website_id is None:
                require_superadmin()
                return
        else:
            require_project_role(
                UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
                website_id=page.website_id,
            )
            return

    if website_id:
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            website_id=website_id,
        )
        return
    if project_id:
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
            project_id=project_id,
        )
        return

    # "all projects" roll-up — the only scope with no id on the job.
    require_superadmin()


def _authorize_report_mutation(record: dict[str, Any]) -> None:
    """ADMIN/AUDITOR variant of :func:`_authorize_report_record` for DELETE."""
    project_id_any: Any = record.get("project_id")
    website_id_any: Any = record.get("website_id")
    metadata_any: Any = record.get("metadata") or {}
    metadata = cast(dict[str, Any], metadata_any) if isinstance(metadata_any, dict) else {}
    page_id_any: Any = metadata.get("page_id")

    project_id = project_id_any if isinstance(project_id_any, str) else None
    website_id = website_id_any if isinstance(website_id_any, str) else None
    page_id = page_id_any if isinstance(page_id_any, str) else None

    if page_id:
        page = get_db().get_page(page_id)
        if page is not None:
            require_project_role(
                UserRole.ADMIN, UserRole.AUDITOR, website_id=page.website_id,
            )
            return

    if website_id:
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id,
        )
        return
    if project_id:
        require_project_role(
            UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id,
        )
        return

    require_superadmin()


def _resolve_report_file_path(record: dict[str, Any]) -> Path:
    """Return the on-disk path of a completed report, raising 404 if the
    job isn't completed yet or the file is missing.

    The reports directory is enforced to be the configured
    ``REPORTS_DIR``: even though the job's ``result.path`` is
    server-controlled (not user input), we re-resolve under
    ``REPORTS_DIR`` and check ``is_relative_to`` so a stray absolute
    path can't escape the directory.
    """
    status = record.get("status")
    if status != JobStatus.COMPLETED.value:
        raise NotFoundError(
            f"report not ready (status: {status})"
        )

    result_any: Any = record.get("result") or {}
    if not isinstance(result_any, dict):
        raise NotFoundError("report result missing")
    result = cast(dict[str, Any], result_any)
    filename_any: Any = result.get("filename")
    if not isinstance(filename_any, str) or not filename_any:
        raise NotFoundError("report filename missing")

    reports_dir = Path(get_app_config().REPORTS_DIR).resolve()
    candidate = (reports_dir / filename_any).resolve()
    # Guard against absolute or traversing filenames just in case the
    # generator ever wrote one — the report record is not user input
    # but defence-in-depth is cheap here.
    try:
        candidate.relative_to(reports_dir)
    except ValueError as exc:
        raise NotFoundError("report path outside reports dir") from exc
    if not candidate.exists():
        raise NotFoundError("report file no longer on disk")
    return candidate


@api_bp.route("/reports/<report_id>/file", methods=["GET"])
@api_endpoint
@document(
    errors=[401, 403, 404],
    tags=["Reports"],
    summary="Download a completed report file",
    description=(
        "Streams the completed report bytes with "
        "``Content-Disposition: attachment``. The response body is "
        "the raw report file (xlsx, html, csv, pdf, ...) and is not "
        "modelled as JSON -- ``@document`` passes the Flask Response "
        "through unchanged. Returns 404 for any of: job missing, job "
        "not a report-generation job, job not yet COMPLETED, "
        "result/filename missing, or file removed from disk after "
        "generation. Authorization mirrors the report's scope "
        "(project/website/page -> ADMIN/AUDITOR/CLIENT on that scope; "
        "cross-project rollups -> superadmin)."
    ),
)
def download_report_file(report_id: str) -> Response | tuple[Response, int]:
    """Stream the completed report file with Content-Disposition."""
    from flask import send_file

    record = _resolve_report_job(report_id)
    _authorize_report_record(record)
    file_path = _resolve_report_file_path(record)

    download_name = file_path.name
    return send_file(
        str(file_path), as_attachment=True, download_name=download_name,
    )


@api_bp.route("/reports/<report_id>", methods=["DELETE"])
@api_endpoint
@document(
    response_204=Empty,
    errors=[401, 403, 404],
    tags=["Reports"],
    summary="Delete a report",
    description=(
        "Removes the report file from disk and deletes the underlying "
        "job record. Idempotent at the file level -- a missing on-disk "
        "file still yields 204 as long as the job record exists and "
        "gets deleted; a missing job record raises 404. Requires ADMIN "
        "or AUDITOR on the report's scope (or superadmin for "
        "cross-project rollups)."
    ),
)
def delete_report(report_id: str) -> tuple[Response, int]:
    """Delete the report file and the underlying job record."""
    record = _resolve_report_job(report_id)
    _authorize_report_mutation(record)

    # Best-effort file removal — proceed to the job-record delete
    # even if the file is gone or unreadable. Log so an oncall can
    # spot a leaked file later.
    result_any: Any = record.get("result") or {}
    if isinstance(result_any, dict):
        result_dict = cast(dict[str, Any], result_any)
        filename_any: Any = result_dict.get("filename")
        if isinstance(filename_any, str) and filename_any:
            reports_dir = Path(get_app_config().REPORTS_DIR).resolve()
            try:
                candidate = (reports_dir / filename_any).resolve()
                candidate.relative_to(reports_dir)
                if candidate.exists():
                    candidate.unlink()
            except (ValueError, OSError) as exc:
                logger.warning(
                    "report %s: file removal skipped (%s)", report_id, exc,
                )

    JobManager(get_db()).collection.delete_one({"job_id": report_id})
    return Response(status=204), 204


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _summarize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Shape a completed-report job into the summary list-item form."""
    metadata_any: Any = record.get("metadata") or {}
    metadata = cast(dict[str, Any], metadata_any) if isinstance(metadata_any, dict) else {}
    result_any: Any = record.get("result") or {}
    result = cast(dict[str, Any], result_any) if isinstance(result_any, dict) else {}
    return {
        "id": record.get("job_id"),
        "scope": metadata.get("scope"),
        "report_type": metadata.get("report_type"),
        "display_name": metadata.get("display_name"),
        "filename": result.get("filename"),
        "created_at": _iso_or_none(record.get("created_at")),
        "completed_at": _iso_or_none(record.get("completed_at")),
    }


@api_bp.route(
    "/projects/<project_id>/report-summary", methods=["GET"]
)
@api_endpoint
def get_project_report_summary(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Aggregate the project's completed report-generation jobs.

    Returns:

        {
          "project_id": "...",
          "total_completed": 7,
          "by_scope":      {"project": 4, "discovery_project": 3},
          "by_report_type": {"html": 2, "xlsx": 4, "csv": 1},
          "recent": [<latest 10, newest first, summary shape>]
        }

    Counts and the ``recent`` list both filter to
    ``status=COMPLETED`` — failed/cancelled jobs are intentionally
    omitted because the summary is a "what reports are available to
    download right now" view. Use ``GET /jobs?status=...`` (and the
    forthcoming :doc:`/jobs` filters from #54) for the broader job
    history.
    """
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=project_id,
    )

    job_manager = JobManager(get_db())
    cursor = job_manager.collection.find({
        "job_type": JobType.REPORT_GENERATION.value,
        "project_id": project_id,
        "status": JobStatus.COMPLETED.value,
    }).sort("completed_at", -1)

    records = list(cursor)
    by_scope: dict[str, int] = {}
    by_report_type: dict[str, int] = {}
    for record in records:
        metadata_any: Any = record.get("metadata") or {}
        metadata = (
            cast(dict[str, Any], metadata_any)
            if isinstance(metadata_any, dict) else {}
        )
        scope = metadata.get("scope")
        report_type = metadata.get("report_type")
        if isinstance(scope, str):
            by_scope[scope] = by_scope.get(scope, 0) + 1
        if isinstance(report_type, str):
            by_report_type[report_type] = by_report_type.get(report_type, 0) + 1

    return jsonify({
        "project_id": project_id,
        "total_completed": len(records),
        "by_scope": by_scope,
        "by_report_type": by_report_type,
        "recent": [_summarize_record(r) for r in records[:10]],
    })


# ---------------------------------------------------------------------------
# Share tokens (REST shape — uses the @api_endpoint scaffolding).
#
# These endpoints sit alongside the existing `share_tokens_bp` HTML
# routes at /share-tokens/... — those still serve the admin frontend
# until the issue #21 stage-2 migration. The new REST surface adds:
#   - JSON body input (legacy used multipart form)
#   - RFC 7807 errors
#   - DELETE-as-revoke (idempotent, 204)
#   - Cursor pagination on list
#   - Single-resource GET (legacy never had this)
#
# Per docs/REST_API_ROADMAP.md §5.10.
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402
import uuid  # noqa: E402

from itsdangerous import URLSafeSerializer  # noqa: E402

from auto_a11y.models.share_token import ShareToken, TokenScope  # noqa: E402


_SHARE_TOKEN_SALT = "public-share-token"


def _share_token_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_app_config().SECRET_KEY, salt=_SHARE_TOKEN_SALT)


def _share_token_hash(token_string: str) -> str:
    return hashlib.sha256(token_string.encode("utf-8")).hexdigest()


def _build_public_url(token_string: str) -> str:
    """Build the public share URL.

    Uses ``request.host_url`` directly so the result is correct even in
    test contexts where the public blueprint is not registered (and so
    ``url_for('public.token_landing', ...)`` would raise BuildError).
    """
    host = request.host_url.rstrip("/")
    return f"{host}/t/{token_string}/"


def _serialize_share_token(token: ShareToken) -> dict[str, Any]:
    """Project a :class:`ShareToken` to a JSON-safe metadata dict.

    Never includes the raw token or the SHA-256 hash. The raw token is
    only ever returned by the create handler in the same response.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": token.id,
        "scope": token.scope.value,
        "scope_id": token.scope_id,
        "label": token.label,
        "created_by": token.created_by,
        "created_at": _iso(token.created_at),
        "expires_at": _iso(token.expires_at),
        "revoked": token.revoked,
        "revoked_at": _iso(token.revoked_at),
        "last_used": _iso(token.last_used),
        "use_count": token.use_count,
        "is_valid": token.is_valid,
    }


def _parse_share_token_body(body: dict[str, Any]) -> tuple[str, datetime | None]:
    """Validate and extract ``label`` / ``expires_at`` from a create body."""
    label_raw = body.get("label")
    if not isinstance(label_raw, str) or not label_raw.strip():
        raise ValidationError(
            "label is required",
            errors=(_FieldError(field="label", code="required", message="required"),),
        )
    label = label_raw.strip()
    expires_at: datetime | None = None
    if "expires_at" in body and body["expires_at"] is not None:
        expires_at = _parse_iso_datetime(body["expires_at"], field="expires_at")
    return label, expires_at


def _create_share_token(scope: TokenScope, scope_id: str) -> tuple[Response, int]:
    """Shared logic for project- and website-scoped token creation."""
    body = _require_dict_body()
    label, expires_at = _parse_share_token_body(body)

    serializer = _share_token_serializer()
    # ``nonce`` ensures every dump produces a distinct signed string —
    # without it, ``URLSafeSerializer.dumps`` is deterministic on
    # (scope, scope_id) and a second token for the same scope collides
    # on the unique ``token_hash`` index. validate_token() looks up by
    # hash, so the nonce is never inspected on the public side.
    token_string = serializer.dumps(
        {"scope": scope.value, "scope_id": scope_id, "nonce": uuid.uuid4().hex}
    )

    token = ShareToken(
        scope=scope,
        scope_id=scope_id,
        created_by=str(current_user.get_id()) if current_user.is_authenticated else "",
        label=label,
        token_hash=_share_token_hash(token_string),
        expires_at=expires_at,
    )
    token_id = get_db().create_share_token(token)
    refreshed = get_db().get_share_token(token_id)
    if refreshed is None:
        raise ConflictError("share token failed to persist")

    payload = _serialize_share_token(refreshed)
    payload["token"] = token_string  # raw token — only ever in the create response
    payload["public_url"] = _build_public_url(token_string)
    response = jsonify(payload)
    response.headers["Location"] = f"/api/v1/share-tokens/{token_id}"
    return response, 201


@api_bp.route("/projects/<project_id>/share-tokens", methods=["POST"])
@api_endpoint
def create_project_share_token(project_id: str) -> tuple[Response, int]:
    """Create a project-scoped share token."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    return _create_share_token(TokenScope.PROJECT, project_id)


@api_bp.route("/websites/<website_id>/share-tokens", methods=["POST"])
@api_endpoint
def create_website_share_token(website_id: str) -> tuple[Response, int]:
    """Create a website-scoped share token."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    return _create_share_token(TokenScope.WEBSITE, website_id)


def _list_share_tokens(scope: TokenScope, scope_id: str) -> Response:
    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"scope": scope.value, "scope_id": scope_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(
                    _FieldError(
                        field="cursor.last_id",
                        code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc

    docs = list(get_db().share_tokens.find(query).sort("_id", -1).limit(limit + 1))
    tokens = [ShareToken.from_dict(doc) for doc in docs]
    page = paginate(
        tokens, limit=limit, get_id=lambda t: str(t.mongo_id) if t.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_share_token(t) for t in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/projects/<project_id>/share-tokens", methods=["GET"])
@api_endpoint
def list_project_share_tokens(project_id: str) -> tuple[Response, int] | Response:
    """List share tokens scoped to a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    return _list_share_tokens(TokenScope.PROJECT, project_id)


@api_bp.route("/websites/<website_id>/share-tokens", methods=["GET"])
@api_endpoint
def list_website_share_tokens(website_id: str) -> tuple[Response, int] | Response:
    """List share tokens scoped to a website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    return _list_share_tokens(TokenScope.WEBSITE, website_id)


def _resolve_token_project_id(token: ShareToken) -> str | None:
    """Resolve a token's scope to its owning project_id (for auth checks)."""
    if token.scope is TokenScope.WEBSITE:
        website = get_db().get_website(token.scope_id)
        return website.project_id if website is not None else None
    return token.scope_id


@api_bp.route("/share-tokens/<token_id>", methods=["GET"])
@api_endpoint
def get_share_token(token_id: str) -> tuple[Response, int] | Response:
    """Get share token metadata by id."""
    token = get_db().get_share_token(token_id)
    if token is None:
        raise NotFoundError(f"share token {token_id} not found")
    project_id = _resolve_token_project_id(token)
    if project_id is None:
        # Token references a deleted scope — treat as not found rather
        # than expose its existence to anyone with the id.
        raise NotFoundError(f"share token {token_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    return jsonify(_serialize_share_token(token))


@api_bp.route("/share-tokens/<token_id>", methods=["DELETE"])
@api_endpoint
def revoke_share_token(token_id: str) -> tuple[Response, int]:
    """Revoke a share token (idempotent — repeated DELETE returns 204)."""
    token = get_db().get_share_token(token_id)
    if token is None:
        raise NotFoundError(f"share token {token_id} not found")
    project_id = _resolve_token_project_id(token)
    if project_id is None:
        raise NotFoundError(f"share token {token_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    get_db().revoke_share_token(token_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Page-setup scripts (REST shape — uses the @api_endpoint scaffolding).
#
# Sits alongside the existing scripts_bp HTML routes at /scripts/... —
# those still serve the admin frontend (templates call /scripts/<id>/test,
# /toggle, /delete) until issue #21 stage 2 migrates the frontend. The
# new REST surface adds:
#   - JSON body input (legacy used multipart form)
#   - RFC 7807 errors
#   - PUT replace + PATCH partial update (legacy had only "edit" + "toggle")
#   - DELETE returns 204 (legacy returned JSON)
#   - Cursor pagination on list
#
# Scripts have a ``scope`` discriminator: PAGE-scoped scripts attach to a
# single page, WEBSITE-scoped scripts run for every page on a website.
# The TEST_RUN scope is internal/runtime-injected and is not exposed via
# REST. Per docs/REST_API_ROADMAP.md §5.8.
#
# Action endpoint POST /api/v1/scripts/<id>/test-runs is deferred to a
# follow-up alongside other test-run action endpoints.
# ---------------------------------------------------------------------------

from auto_a11y.models.page_setup_script import (  # noqa: E402
    ActionType,
    ExecutionTrigger,
    PageSetupScript,
    ScriptScope,
    ScriptStep,
    ScriptValidation,
)


def _script_step_to_out(step: ScriptStep) -> ScriptStepOut:
    return ScriptStepOut(
        step_number=step.step_number,
        action_type=step.action_type.value,
        description=step.description,
        selector=step.selector,
        value=step.value,
        timeout=step.timeout,
        wait_after=step.wait_after,
        screenshot_after=step.screenshot_after,
    )


def _script_validation_to_out(
    validation: ScriptValidation | None,
) -> ScriptValidationOut | None:
    if validation is None:
        return None
    return ScriptValidationOut(
        success_selector=validation.success_selector,
        success_text=validation.success_text,
        failure_selectors=list(validation.failure_selectors),
    )


def _script_to_out(script: PageSetupScript) -> ScriptOut:
    """Project a :class:`PageSetupScript` to its :class:`ScriptOut` model.

    Mirrors the legacy ``_serialize_script`` shape byte-for-byte:
    datetimes are emitted as ISO 8601 strings, enum values are
    stringified, and the Mongo ``_id`` is dropped in favor of the
    ``id`` property.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    stats = script.execution_stats
    return ScriptOut(
        id=script.id,
        name=script.name,
        description=script.description,
        scope=script.scope.value,
        website_id=script.website_id,
        page_id=script.page_id,
        trigger=script.trigger.value,
        condition_selector=script.condition_selector,
        report_violation_if_condition_met=script.report_violation_if_condition_met,
        violation_message=script.violation_message,
        violation_code=script.violation_code,
        test_before_execution=script.test_before_execution,
        test_after_execution=script.test_after_execution,
        expect_visible_after=list(script.expect_visible_after),
        expect_hidden_after=list(script.expect_hidden_after),
        clear_cookies_before=script.clear_cookies_before,
        clear_local_storage_before=script.clear_local_storage_before,
        wait_for_selector=script.wait_for_selector,
        wait_timeout=script.wait_timeout,
        enabled=script.enabled,
        steps=[_script_step_to_out(s) for s in script.steps],
        validation=_script_validation_to_out(script.validation),
        created_by=script.created_by,
        created_date=_iso(script.created_date),
        last_modified=_iso(script.last_modified),
        execution_stats=ExecutionStatsOut(
            last_executed=_iso(stats.last_executed),
            success_count=stats.success_count,
            failure_count=stats.failure_count,
            average_duration_ms=stats.average_duration_ms,
        ),
    )


def _script_body_to_dict(
    body: ScriptIn | ScriptPut | ScriptPatch,
) -> dict[str, Any]:
    """Convert a Pydantic script body to the legacy ``dict[str, Any]`` shape.

    The legacy parsers ``_build_script_from_body`` and
    ``_apply_patch_to_script`` distinguish "absent" from "explicit
    ``null``" using ``key in body`` checks against a raw request dict.
    Pydantic's ``model_dump(exclude_unset=True)`` preserves that exact
    distinction: only keys the client explicitly sent appear in the
    output dict.

    Nested ``steps`` and ``validation`` blocks are dumped as nested
    dicts so the legacy ``_parse_script_steps`` and
    ``_parse_script_validation`` parsers receive the shapes they
    expect.
    """
    return body.model_dump(exclude_unset=True, by_alias=False)


def _parse_action_type(raw: Any, *, field: str) -> ActionType:
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    try:
        return ActionType(raw)
    except ValueError as exc:
        raise ValidationError(
            f"{field} is not a recognized action type",
            errors=(_FieldError(field=field, code="invalid_value", message=str(exc)),),
        ) from exc


def _parse_execution_trigger(raw: Any, *, field: str) -> ExecutionTrigger:
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field} must be a string",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    try:
        return ExecutionTrigger(raw)
    except ValueError as exc:
        raise ValidationError(
            f"{field} is not a recognized trigger",
            errors=(_FieldError(field=field, code="invalid_value", message=str(exc)),),
        ) from exc


def _parse_script_step(raw: Any, *, index: int) -> ScriptStep:
    field_prefix = f"steps[{index}]"
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field_prefix} must be an object",
            errors=(_FieldError(field=field_prefix, code="invalid_type", message="must be object"),),
        )
    step_dict = cast(dict[str, Any], raw)

    description_raw = step_dict.get("description", "")
    if not isinstance(description_raw, str):
        raise ValidationError(
            f"{field_prefix}.description must be a string",
            errors=(_FieldError(field=f"{field_prefix}.description", code="invalid_type", message="must be string"),),
        )

    selector_raw = step_dict.get("selector")
    if selector_raw is not None and not isinstance(selector_raw, str):
        raise ValidationError(
            f"{field_prefix}.selector must be a string or null",
            errors=(_FieldError(field=f"{field_prefix}.selector", code="invalid_type", message="must be string"),),
        )

    value_raw = step_dict.get("value")
    if value_raw is not None and not isinstance(value_raw, str):
        raise ValidationError(
            f"{field_prefix}.value must be a string or null",
            errors=(_FieldError(field=f"{field_prefix}.value", code="invalid_type", message="must be string"),),
        )

    timeout_raw = step_dict.get("timeout", 5000)
    if isinstance(timeout_raw, bool) or not isinstance(timeout_raw, int):
        raise ValidationError(
            f"{field_prefix}.timeout must be an integer",
            errors=(_FieldError(field=f"{field_prefix}.timeout", code="invalid_type", message="must be integer"),),
        )

    wait_after_raw = step_dict.get("wait_after", 0)
    if isinstance(wait_after_raw, bool) or not isinstance(wait_after_raw, int):
        raise ValidationError(
            f"{field_prefix}.wait_after must be an integer",
            errors=(_FieldError(field=f"{field_prefix}.wait_after", code="invalid_type", message="must be integer"),),
        )

    screenshot_after_raw = step_dict.get("screenshot_after", False)
    if not isinstance(screenshot_after_raw, bool):
        raise ValidationError(
            f"{field_prefix}.screenshot_after must be a boolean",
            errors=(_FieldError(field=f"{field_prefix}.screenshot_after", code="invalid_type", message="must be boolean"),),
        )

    return ScriptStep(
        step_number=index + 1,
        action_type=_parse_action_type(step_dict.get("action_type"), field=f"{field_prefix}.action_type"),
        description=description_raw,
        selector=selector_raw,
        value=value_raw,
        timeout=int(timeout_raw),
        wait_after=int(wait_after_raw),
        screenshot_after=screenshot_after_raw,
    )


def _iter_to_any_list(value: Any) -> list[Any]:
    """Re-widen a narrowed iterable to ``list[Any]``.

    Same trick as :func:`_coerce_str_list`: by routing the value through
    a parameter typed ``Any``, pyright drops the ``list[Unknown]``
    narrowing that an outer ``isinstance(_, list)`` check imposes, and
    iteration inside this body yields properly-typed ``Any`` items.
    """
    items: list[Any] = []
    for item in value:
        items.append(item)
    return items


def _parse_script_steps(raw: Any, *, field: str) -> list[ScriptStep]:
    if not isinstance(raw, list):
        raise ValidationError(
            f"{field} must be an array",
            errors=(_FieldError(field=field, code="invalid_type", message="must be array"),),
        )
    items = _iter_to_any_list(raw)
    return [_parse_script_step(item, index=i) for i, item in enumerate(items)]


def _parse_script_validation(raw: Any, *, field: str) -> ScriptValidation | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object or null",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    success_selector = raw_dict.get("success_selector")
    success_text = raw_dict.get("success_text")
    failure_selectors_raw = raw_dict.get("failure_selectors", [])
    if success_selector is not None and not isinstance(success_selector, str):
        raise ValidationError(
            f"{field}.success_selector must be a string or null",
            errors=(_FieldError(field=f"{field}.success_selector", code="invalid_type", message="must be string"),),
        )
    if success_text is not None and not isinstance(success_text, str):
        raise ValidationError(
            f"{field}.success_text must be a string or null",
            errors=(_FieldError(field=f"{field}.success_text", code="invalid_type", message="must be string"),),
        )
    if not isinstance(failure_selectors_raw, list):
        raise ValidationError(
            f"{field}.failure_selectors must be an array",
            errors=(_FieldError(field=f"{field}.failure_selectors", code="invalid_type", message="must be array"),),
        )
    return ScriptValidation(
        success_selector=success_selector,
        success_text=success_text,
        failure_selectors=_coerce_str_list(failure_selectors_raw),
    )


def _build_script_from_body(
    body: dict[str, Any], *, scope: ScriptScope, scope_id: str
) -> PageSetupScript:
    """Construct a :class:`PageSetupScript` from a POST/PUT body."""
    name_raw = body.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        raise ValidationError(
            "name is required",
            errors=(_FieldError(field="name", code="required", message="required"),),
        )
    description_raw = body.get("description", "")
    if not isinstance(description_raw, str):
        raise ValidationError(
            "description must be a string",
            errors=(_FieldError(field="description", code="invalid_type", message="must be string"),),
        )

    trigger = (
        _parse_execution_trigger(body["trigger"], field="trigger")
        if "trigger" in body
        else ExecutionTrigger.ONCE_PER_PAGE
    )
    steps = _parse_script_steps(body["steps"], field="steps") if "steps" in body else []
    validation = (
        _parse_script_validation(body["validation"], field="validation")
        if "validation" in body
        else None
    )

    script = PageSetupScript(
        name=name_raw.strip(),
        description=description_raw,
        scope=scope,
        page_id=scope_id if scope is ScriptScope.PAGE else None,
        website_id=scope_id if scope is ScriptScope.WEBSITE else None,
        trigger=trigger,
        steps=steps,
        validation=validation,
        enabled=bool(body.get("enabled", True)),
        condition_selector=(
            body["condition_selector"] if isinstance(body.get("condition_selector"), str) else None
        ),
        report_violation_if_condition_met=bool(body.get("report_violation_if_condition_met", False)),
        violation_message=(
            body["violation_message"] if isinstance(body.get("violation_message"), str) else None
        ),
        violation_code=(
            body["violation_code"] if isinstance(body.get("violation_code"), str) else None
        ),
        test_before_execution=bool(body.get("test_before_execution", False)),
        test_after_execution=bool(body.get("test_after_execution", True)),
        expect_visible_after=_coerce_str_list(body.get("expect_visible_after", [])),
        expect_hidden_after=_coerce_str_list(body.get("expect_hidden_after", [])),
        clear_cookies_before=bool(body.get("clear_cookies_before", False)),
        clear_local_storage_before=bool(body.get("clear_local_storage_before", False)),
        wait_for_selector=bool(body.get("wait_for_selector", False)),
        wait_timeout=int(body.get("wait_timeout", 5000)) if not isinstance(body.get("wait_timeout"), bool) else 5000,
        created_by=str(current_user.get_id()) if current_user.is_authenticated else None,
    )
    if script.trigger is ExecutionTrigger.CONDITIONAL and not (script.condition_selector or "").strip():
        raise ValidationError(
            "condition_selector is required when trigger=conditional",
            errors=(_FieldError(field="condition_selector", code="required", message="required when trigger=conditional"),),
        )
    return script


def _apply_patch_to_script(script: PageSetupScript, body: dict[str, Any]) -> PageSetupScript:
    """Apply only the keys present in ``body`` to ``script`` in place."""
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise ValidationError(
                "name must be a non-empty string",
                errors=(_FieldError(field="name", code="invalid_value", message="must be non-empty string"),),
            )
        script.name = body["name"].strip()
    if "description" in body:
        desc = body["description"]
        if not isinstance(desc, str):
            raise ValidationError(
                "description must be a string",
                errors=(_FieldError(field="description", code="invalid_type", message="must be string"),),
            )
        script.description = desc
    if "trigger" in body:
        script.trigger = _parse_execution_trigger(body["trigger"], field="trigger")
    if "condition_selector" in body:
        cs = body["condition_selector"]
        script.condition_selector = cs if isinstance(cs, str) else None
    if "report_violation_if_condition_met" in body:
        script.report_violation_if_condition_met = bool(body["report_violation_if_condition_met"])
    if "violation_message" in body:
        vm = body["violation_message"]
        script.violation_message = vm if isinstance(vm, str) else None
    if "violation_code" in body:
        vc = body["violation_code"]
        script.violation_code = vc if isinstance(vc, str) else None
    if "test_before_execution" in body:
        script.test_before_execution = bool(body["test_before_execution"])
    if "test_after_execution" in body:
        script.test_after_execution = bool(body["test_after_execution"])
    if "expect_visible_after" in body:
        if not isinstance(body["expect_visible_after"], list):
            raise ValidationError(
                "expect_visible_after must be an array",
                errors=(_FieldError(field="expect_visible_after", code="invalid_type", message="must be array"),),
            )
        script.expect_visible_after = _coerce_str_list(body["expect_visible_after"])
    if "expect_hidden_after" in body:
        if not isinstance(body["expect_hidden_after"], list):
            raise ValidationError(
                "expect_hidden_after must be an array",
                errors=(_FieldError(field="expect_hidden_after", code="invalid_type", message="must be array"),),
            )
        script.expect_hidden_after = _coerce_str_list(body["expect_hidden_after"])
    if "clear_cookies_before" in body:
        script.clear_cookies_before = bool(body["clear_cookies_before"])
    if "clear_local_storage_before" in body:
        script.clear_local_storage_before = bool(body["clear_local_storage_before"])
    if "wait_for_selector" in body:
        script.wait_for_selector = bool(body["wait_for_selector"])
    if "wait_timeout" in body:
        wt = body["wait_timeout"]
        if isinstance(wt, bool) or not isinstance(wt, int):
            raise ValidationError(
                "wait_timeout must be an integer",
                errors=(_FieldError(field="wait_timeout", code="invalid_type", message="must be integer"),),
            )
        script.wait_timeout = int(wt)
    if "enabled" in body:
        script.enabled = bool(body["enabled"])
    if "steps" in body:
        script.steps = _parse_script_steps(body["steps"], field="steps")
    if "validation" in body:
        script.validation = _parse_script_validation(body["validation"], field="validation")
    if script.trigger is ExecutionTrigger.CONDITIONAL and not (script.condition_selector or "").strip():
        raise ValidationError(
            "condition_selector is required when trigger=conditional",
            errors=(_FieldError(field="condition_selector", code="required", message="required when trigger=conditional"),),
        )
    script.update_timestamp()
    return script


def _resolve_script_auth_context(script: PageSetupScript) -> tuple[str | None, str | None]:
    """Return ``(website_id, page_id)`` for ``require_project_role`` lookup.

    REST surfaces only PAGE- and WEBSITE-scoped scripts; TEST_RUN-scoped
    scripts are runtime-internal and not exposed.
    """
    if script.scope is ScriptScope.PAGE:
        return None, script.page_id
    if script.scope is ScriptScope.WEBSITE:
        return script.website_id, None
    return None, None


def _list_scripts_for_scope(
    scope: ScriptScope, scope_id: str, *, scope_field: str
) -> ScriptListOut:
    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"scope": scope.value, scope_field: scope_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().page_setup_scripts.find(query).sort("_id", -1).limit(limit + 1))
    scripts = [PageSetupScript.from_dict(doc) for doc in docs]
    page = paginate(
        scripts, limit=limit, get_id=lambda s: str(s.mongo_id) if s.mongo_id else ""
    )
    return ScriptListOut(
        items=[_script_to_out(s) for s in page["items"]],
        next_cursor=page["next_cursor"],
    )


@api_bp.route("/pages/<page_id>/scripts", methods=["GET"])
@api_endpoint
@document(
    response_200=ScriptListOut,
    errors=[400, 401, 403, 404],
    tags=["Scripts"],
    summary="List page-scoped setup scripts",
    description=(
        "Returns the setup scripts configured against the given page. "
        "Cursor-paginated using the legacy ``{items, next_cursor}`` "
        "shape shared by every v1 list endpoint."
    ),
)
def list_page_scripts_rest(
    page_id: str,
) -> tuple[ScriptListOut, int] | tuple[Response, int] | Response:
    """List page-scoped setup scripts."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, page_id=page_id
    )
    if get_db().get_page(page_id) is None:
        raise NotFoundError(f"page {page_id} not found")
    return _list_scripts_for_scope(
        ScriptScope.PAGE, page_id, scope_field="page_id",
    ), 200


@api_bp.route("/pages/<page_id>/scripts", methods=["POST"])
@api_endpoint
@document(
    request=ScriptIn,
    response_201=ScriptOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Scripts"],
    summary="Create a page-scoped setup script",
    description=(
        "Creates a setup script bound to the given page. Returns 201 "
        "with the persisted ``ScriptOut`` resource plus a ``Location`` "
        "header pointing at ``/api/v1/scripts/<id>``."
    ),
)
def create_page_script_rest(
    page_id: str, body: ScriptIn,
) -> tuple[Response, int]:
    """Create a page-scoped setup script."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, page_id=page_id
    )
    if get_db().get_page(page_id) is None:
        raise NotFoundError(f"page {page_id} not found")

    legacy_body = _script_body_to_dict(body)
    script = _build_script_from_body(
        legacy_body, scope=ScriptScope.PAGE, scope_id=page_id,
    )
    script_id = get_db().create_page_setup_script(script)
    refreshed = get_db().get_page_setup_script(script_id)
    if refreshed is None:
        raise ConflictError("script failed to persist")
    # The @document decorator serialises BaseModel returns through
    # ``jsonify``, but we need a ``Location`` header on the new resource,
    # so we pre-build the Response here and attach the header.
    payload = _script_to_out(refreshed)
    response = jsonify(
        payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    response.headers["Location"] = f"/api/v1/scripts/{script_id}"
    return response, 201


@api_bp.route("/websites/<website_id>/scripts", methods=["GET"])
@api_endpoint
@document(
    response_200=ScriptListOut,
    errors=[400, 401, 403, 404],
    tags=["Scripts"],
    summary="List website-scoped setup scripts",
    description=(
        "Returns the setup scripts configured against the given website. "
        "Cursor-paginated using the legacy ``{items, next_cursor}`` "
        "shape shared by every v1 list endpoint."
    ),
)
def list_website_scripts_rest(
    website_id: str,
) -> tuple[ScriptListOut, int] | tuple[Response, int] | Response:
    """List website-scoped setup scripts."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    return _list_scripts_for_scope(
        ScriptScope.WEBSITE, website_id, scope_field="website_id",
    ), 200


@api_bp.route("/websites/<website_id>/scripts", methods=["POST"])
@api_endpoint
@document(
    request=ScriptIn,
    response_201=ScriptOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Scripts"],
    summary="Create a website-scoped setup script",
    description=(
        "Creates a setup script bound to the given website. Returns 201 "
        "with the persisted ``ScriptOut`` resource plus a ``Location`` "
        "header pointing at ``/api/v1/scripts/<id>``."
    ),
)
def create_website_script_rest(
    website_id: str, body: ScriptIn,
) -> tuple[Response, int]:
    """Create a website-scoped setup script."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    legacy_body = _script_body_to_dict(body)
    script = _build_script_from_body(
        legacy_body, scope=ScriptScope.WEBSITE, scope_id=website_id,
    )
    script_id = get_db().create_page_setup_script(script)
    refreshed = get_db().get_page_setup_script(script_id)
    if refreshed is None:
        raise ConflictError("script failed to persist")
    payload = _script_to_out(refreshed)
    response = jsonify(
        payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    response.headers["Location"] = f"/api/v1/scripts/{script_id}"
    return response, 201


@api_bp.route("/scripts/<script_id>", methods=["GET"])
@api_endpoint
@document(
    response_200=ScriptOut,
    errors=[401, 403, 404],
    tags=["Scripts"],
    summary="Get a setup script by ID",
    description=(
        "Returns the setup-script resource (``ScriptOut``). "
        "``TEST_RUN``-scoped scripts are runtime-internal and surface "
        "as 404."
    ),
)
def get_script_rest(
    script_id: str,
) -> tuple[ScriptOut, int] | tuple[Response, int] | Response:
    """Get a setup script by id."""
    script = get_db().get_page_setup_script(script_id)
    if script is None:
        raise NotFoundError(f"script {script_id} not found")
    if script.scope is ScriptScope.TEST_RUN:
        # TEST_RUN-scoped scripts are runtime-internal; they are not part
        # of the REST surface, so return 404 rather than expose them.
        raise NotFoundError(f"script {script_id} not found")
    website_id, page_id = _resolve_script_auth_context(script)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        website_id=website_id, page_id=page_id,
    )
    return _script_to_out(script), 200


@api_bp.route("/scripts/<script_id>", methods=["PUT"])
@api_endpoint
@document(
    request=ScriptPut,
    response_200=ScriptOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Scripts"],
    summary="Replace a setup script",
    description=(
        "Full replacement of the user-editable fields. Server-managed "
        "bookkeeping (``created_date``, ``created_by``, "
        "``execution_stats``, ``scope`` and the matching ``page_id`` / "
        "``website_id``) is preserved from the prior version. "
        "``TEST_RUN``-scoped scripts surface as 404."
    ),
)
def replace_script_rest(
    script_id: str, body: ScriptPut,
) -> tuple[ScriptOut, int] | tuple[Response, int] | Response:
    """Full replace of a setup script's editable fields.

    Server-managed fields (created_date, created_by, execution_stats,
    scope/page_id/website_id) are preserved.
    """
    existing = get_db().get_page_setup_script(script_id)
    if existing is None:
        raise NotFoundError(f"script {script_id} not found")
    if existing.scope is ScriptScope.TEST_RUN:
        raise NotFoundError(f"script {script_id} not found")
    website_id, page_id = _resolve_script_auth_context(existing)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR,
        website_id=website_id, page_id=page_id,
    )
    legacy_body = _script_body_to_dict(body)
    scope_id = (
        existing.page_id if existing.scope is ScriptScope.PAGE else existing.website_id
    )
    if scope_id is None:
        raise ConflictError("existing script has no scope id")
    replaced = _build_script_from_body(
        legacy_body, scope=existing.scope, scope_id=scope_id,
    )
    replaced.mongo_id = existing.mongo_id
    replaced.created_by = existing.created_by
    replaced.created_date = existing.created_date
    replaced.execution_stats = existing.execution_stats
    replaced.update_timestamp()
    if not get_db().update_page_setup_script(replaced):
        raise ConflictError("script could not be updated")
    return _script_to_out(replaced), 200


@api_bp.route("/scripts/<script_id>", methods=["PATCH"])
@api_endpoint
@document(
    request=ScriptPatch,
    response_200=ScriptOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Scripts"],
    summary="Partially update a setup script",
    description=(
        "Partial update -- only fields present in the request body are "
        "applied. Commonly used for the enable/disable toggle "
        "(``{\"enabled\": false}``) and similar narrow edits. "
        "``TEST_RUN``-scoped scripts surface as 404."
    ),
)
def patch_script_rest(
    script_id: str, body: ScriptPatch,
) -> tuple[ScriptOut, int] | tuple[Response, int] | Response:
    """Partial update -- covers the legacy enable/disable toggle (``{"enabled": false}``)
    plus any other field-level edit."""
    script = get_db().get_page_setup_script(script_id)
    if script is None:
        raise NotFoundError(f"script {script_id} not found")
    if script.scope is ScriptScope.TEST_RUN:
        raise NotFoundError(f"script {script_id} not found")
    website_id, page_id = _resolve_script_auth_context(script)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR,
        website_id=website_id, page_id=page_id,
    )
    legacy_body = _script_body_to_dict(body)
    patched = _apply_patch_to_script(script, legacy_body)
    if not get_db().update_page_setup_script(patched):
        raise ConflictError("script could not be updated")
    return _script_to_out(patched), 200


@api_bp.route("/scripts/<script_id>", methods=["DELETE"])
@api_endpoint
@document(
    response_204=Empty,
    errors=[401, 403, 404],
    tags=["Scripts"],
    summary="Delete a setup script",
    description=(
        "Removes the setup script. ``TEST_RUN``-scoped scripts surface "
        "as 404. Returns ``204 No Content`` with an empty body."
    ),
)
def delete_script_rest(
    script_id: str,
) -> tuple[Empty, int] | tuple[Response, int]:
    """Delete a setup script."""
    script = get_db().get_page_setup_script(script_id)
    if script is None:
        raise NotFoundError(f"script {script_id} not found")
    if script.scope is ScriptScope.TEST_RUN:
        raise NotFoundError(f"script {script_id} not found")
    website_id, page_id = _resolve_script_auth_context(script)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR,
        website_id=website_id, page_id=page_id,
    )
    get_db().delete_page_setup_script(script_id)
    return Empty(), 204


@api_bp.route("/scripts/<script_id>/test-runs", methods=["POST"])
@api_endpoint
@document(
    request=ScriptTestRunIn,
    response_200=ScriptTestRunOut,
    errors=[400, 401, 403, 404, 409, 500],
    tags=["Scripts"],
    summary="Execute a setup script synchronously",
    description=(
        "Runs the script against its target URL (page-scoped scripts "
        "use the linked page's URL; website-scoped scripts use the "
        "website's root URL). **Synchronous** -- the legacy "
        "``POST /scripts/<id>/test`` blocks the request thread until "
        "the browser executes the script and clients rely on the "
        "inline result; this REST shape preserves that semantic even "
        "though the URL implies async by convention. The 200 status "
        "code (rather than the usual 202) signals \"done synchronously\". "
        "If the script raises during execution the same JSON envelope "
        "is returned with ``success=false`` and a populated ``error`` "
        "field, but with status 500 so monitoring tooling can flag the "
        "failure. ``TEST_RUN``-scoped scripts surface as 404; "
        "``BROWSER_MODE`` set to ``disabled`` or ``remote`` surfaces as "
        "409. The request body is currently a no-op (the script's "
        "persisted ``steps`` are the authoritative input); accepting "
        "the body shape keeps the door open for per-run overrides."
    ),
)
def run_script_test(
    script_id: str, body: ScriptTestRunIn,
) -> tuple[ScriptTestRunOut, int] | tuple[Response, int] | Response:
    """Execute a setup script against its target URL and return the result.

    **Synchronous** -- distinct from every other ``/test-runs`` endpoint
    on this surface. The legacy ``POST /scripts/<id>/test`` blocks the
    request thread until the browser executes the script, and clients
    rely on the inline result; this REST shape preserves that semantic
    even though the URL implies async by convention. The 200 status
    code (rather than the usual 202) signals "done synchronously" so
    callers don't poll a non-existent job.

    Target URL resolution:

    - PAGE-scoped scripts run against the linked page's URL
    - WEBSITE-scoped scripts run against the website's root URL
    - TEST_RUN-scoped scripts are runtime-internal and surface as 404
      from this endpoint (matching the rest of the scripts REST API)

    Body fields are accepted but currently ignored -- the script's
    persisted ``steps`` are the authoritative input. Accepting the
    body shape keeps the door open for per-run overrides (e.g.
    ``environment_vars``) without an API version bump.

    Response shape:

        {
          "script_id":      "...",
          "success":        true|false,
          "duration_ms":    1234,
          "steps_executed": 7,
          "error":          null | "...",
          "target_url":     "https://..."
        }

    Errors:

    - **404** -- script doesn't exist, is TEST_RUN-scoped, or its
      target (page / website) can't be resolved
    - **409** -- server has no browser configured (BROWSER_MODE='disabled'
      or 'remote' -- the browser pipeline is local-only for this endpoint)
    - **500** -- script raises during execution; the body carries the
      error message so the operator can debug without polling logs
    """
    import asyncio

    from auto_a11y.core.browser_manager import BrowserManager
    from auto_a11y.testing.script_executor import ScriptExecutor

    # ``body`` is parsed by @document but intentionally unused: the
    # script's persisted ``steps`` are the authoritative input. Naming
    # the kwarg keeps the decorator contract intact while letting the
    # value be discarded.
    del body

    script = get_db().get_page_setup_script(script_id)
    if script is None:
        raise NotFoundError(f"script {script_id} not found")
    if script.scope is ScriptScope.TEST_RUN:
        # TEST_RUN-scoped scripts are runtime-internal and not exposed.
        raise NotFoundError(f"script {script_id} not found")

    website_id, page_id = _resolve_script_auth_context(script)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR,
        website_id=website_id, page_id=page_id,
    )

    if not script.website_id:
        raise NotFoundError(
            f"script {script_id} has no website target"
        )
    website = get_db().get_website(script.website_id)
    if website is None:
        raise NotFoundError(
            f"website {script.website_id} for script {script_id} not found"
        )

    target_url: str
    if script.scope is ScriptScope.PAGE:
        if not script.page_id:
            raise NotFoundError(
                f"page-scoped script {script_id} has no page target"
            )
        target_page = get_db().get_page(script.page_id)
        if target_page is None:
            raise NotFoundError(
                f"page {script.page_id} for script {script_id} not found"
            )
        target_url = target_page.url
    else:
        target_url = website.url

    # Reject deployments that don't run a local browser. The script
    # test endpoint is *not* implemented as a queued job, so it can't
    # transparently fall back to a remote runner the way test-runs
    # can -- surface the misconfiguration as 409 instead of pretending
    # the test ran with zero steps.
    browser_mode = getattr(get_app_config(), "BROWSER_MODE", "local")
    if browser_mode in ("disabled", "remote"):
        raise ConflictError(
            f"script test requires local browser; BROWSER_MODE={browser_mode!r}"
        )

    project = (
        get_db().get_project(website.project_id) if website.project_id else None
    )
    browser_config: dict[str, Any] = get_app_config().__dict__.copy()
    if project is not None and project.config:
        browser_config["stealth_mode"] = project.config.get(
            "stealth_mode", False
        )
        headless_setting = project.config.get("headless_browser", "true")
        browser_config["BROWSER_HEADLESS"] = headless_setting == "true"
    else:
        browser_config["stealth_mode"] = False

    async def _run_test() -> dict[str, Any]:
        # Local async coroutine -- instantiates a fresh browser per
        # request. Matches the legacy handler: no pooling, no reuse;
        # the test endpoint is rare enough that the per-request
        # browser-launch cost is acceptable.
        manager = BrowserManager(browser_config)
        try:
            await manager.start()
            context = await manager.create_context()
            page_obj = await context.new_page()
            await page_obj.goto(
                target_url, wait_until="networkidle", timeout=30000,
            )
            executor = ScriptExecutor()
            return await executor.execute_script(page_obj, script)
        finally:
            await manager.stop()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_run_test())
    except Exception as exc:
        logger.error(
            "Error running script %s against %s: %s",
            script_id, target_url, exc,
        )
        # Mirror the legacy route's 500 shape but emit a stable
        # JSON envelope rather than a raw error string. The
        # @api_endpoint wrapper would intercept ApiError subclasses;
        # here we want the *test result*, not the route, to be the
        # thing reporting failure. We validate through ScriptTestRunOut
        # so the 500-path payload exactly matches the advertised schema.
        failure_payload = ScriptTestRunOut(
            script_id=script_id,
            success=False,
            duration_ms=0,
            steps_executed=0,
            error=str(exc),
            target_url=target_url,
        )
        return jsonify(
            failure_payload.model_dump(mode="json", by_alias=True)
        ), 500
    finally:
        loop.close()

    return ScriptTestRunOut(
        script_id=script_id,
        success=bool(result.get("success", False)),
        duration_ms=int(result.get("duration_ms", 0) or 0),
        steps_executed=int(result.get("steps_executed", 0) or 0),
        error=result.get("error"),
        target_url=target_url,
    ), 200


# ---------------------------------------------------------------------------
# Recordings (REST shape — uses the @api_endpoint scaffolding).
#
# Sits alongside the existing recordings_bp HTML routes at /recordings/...
# (still serving the admin frontend) and the legacy ad-hoc JSON endpoints
# at /recordings/api/list, /recordings/api/<id>/issues, and
# /recordings/api/issue/<id>/status. Per docs/REST_API_ROADMAP.md §5.6.
#
# In scope for this PR: read/update/delete on existing recordings + their
# issues. Out of scope (deferred to a follow-up PR): multipart upload
# (`POST /api/v1/recordings`) — that involves file-system handoff,
# DictaphoneImporter parsing, and bulk issue creation, large enough to
# deserve its own review.
# ---------------------------------------------------------------------------

from auto_a11y.models.recording import Recording, RecordingType  # noqa: E402
from auto_a11y.models.recording_issue import RecordingIssue  # noqa: E402


_VALID_ISSUE_STATUSES: frozenset[str] = frozenset(
    {"open", "in_progress", "resolved", "verified"}
)


def _serialize_recording_issue(issue: RecordingIssue) -> dict[str, Any]:
    """Project a :class:`RecordingIssue` to a JSON-safe dict."""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": issue.id,
        "recording_id": issue.recording_id,
        "title": issue.title,
        "short_title": issue.short_title,
        "language": issue.language,
        "what": issue.what,
        "why": issue.why,
        "who": issue.who,
        "remediation": issue.remediation,
        "impact": issue.impact.value,
        "touchpoint": issue.touchpoint,
        "timecodes": [tc.to_dict() for tc in issue.timecodes],
        "wcag": [w.to_dict() for w in issue.wcag],
        "xpath": issue.xpath,
        "element": issue.element,
        "html": issue.html,
        "project_id": issue.project_id,
        "website_ids": list(issue.website_ids),
        "page_urls": list(issue.page_urls),
        "page_ids": list(issue.page_ids),
        "component_names": list(issue.component_names),
        "app_screens": list(issue.app_screens),
        "device_sections": list(issue.device_sections),
        "task_description": issue.task_description,
        "status": issue.status,
        "assigned_to": issue.assigned_to,
        "resolution_notes": issue.resolution_notes,
        "tags": list(issue.tags),
        "created_at": _iso(issue.created_at),
        "updated_at": _iso(issue.updated_at),
    }


def _apply_patch_to_recording_issue(
    issue: RecordingIssue, body: dict[str, Any]
) -> RecordingIssue:
    """Apply a partial-update body to ``issue``.

    Editable fields cover the issue-triage workflow: status (with enum
    validation), assigned_to, resolution_notes, tags. The bulk content
    fields (what/why/who/remediation, timecodes, WCAG references) are
    set during upload from the source JSON and are not patchable here.
    """
    if "status" in body:
        status = body["status"]
        if not isinstance(status, str) or status not in _VALID_ISSUE_STATUSES:
            raise ValidationError(
                f"status must be one of {sorted(_VALID_ISSUE_STATUSES)}",
                errors=(_FieldError(field="status", code="invalid_value", message="not a recognized status"),),
            )
        issue.status = status
    if "assigned_to" in body:
        assigned = body["assigned_to"]
        if assigned is not None and not isinstance(assigned, str):
            raise ValidationError(
                "assigned_to must be a string or null",
                errors=(_FieldError(field="assigned_to", code="invalid_type", message="must be string"),),
            )
        issue.assigned_to = assigned if isinstance(assigned, str) else None
    if "resolution_notes" in body:
        notes = body["resolution_notes"]
        if notes is not None and not isinstance(notes, str):
            raise ValidationError(
                "resolution_notes must be a string or null",
                errors=(_FieldError(field="resolution_notes", code="invalid_type", message="must be string"),),
            )
        issue.resolution_notes = notes if isinstance(notes, str) else None
    if "tags" in body:
        if not isinstance(body["tags"], list):
            raise ValidationError(
                "tags must be an array",
                errors=(_FieldError(field="tags", code="invalid_type", message="must be array"),),
            )
        issue.tags = _coerce_str_list(body["tags"])
    issue.updated_at = datetime.now()
    return issue


def _resolve_recording_project_id(recording: Recording) -> str | None:
    """Recordings are scoped to projects via ``project_id``.

    Some legacy recordings predate that linkage and may have
    ``project_id is None``. Treat those as not-found via REST rather
    than expose an unscopable resource.
    """
    return recording.project_id


def _parse_multiline_form_field(value: str | None) -> list[str]:
    """Split a textarea-style form field (one item per line) to a list.

    Empty input yields an empty list; whitespace-only lines are
    dropped. Used by the recordings upload route for the page_urls /
    component_names / app_screens / device_sections fields the legacy
    multipart form accepts.
    """
    if not value:
        return []
    return [line.strip() for line in value.split("\n") if line.strip()]


def _recording_to_out(recording: Recording) -> RecordingOut:
    """Project a :class:`Recording` to a :class:`RecordingOut` model.

    Mirrors :func:`_serialize_recording` byte-for-byte; the two helpers
    exist side-by-side during the §5.6 refactor so the legacy
    ``dict``-returning serialiser still backs internal callers (HTML
    views, the supplementary-content GET path) while the REST handlers
    move to typed Pydantic responses.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return RecordingOut(
        id=recording.id,
        recording_id=recording.recording_id,
        title=recording.title,
        description=recording.description,
        duration=recording.duration,
        recorded_date=_iso(recording.recorded_date),
        auditor_name=recording.auditor_name,
        auditor_role=recording.auditor_role,
        recording_type=recording.recording_type.value,
        project_id=recording.project_id,
        testing_scope=dict(recording.testing_scope),
        website_ids=list(recording.website_ids),
        page_urls=list(recording.page_urls),
        page_ids=list(recording.page_ids),
        discovered_page_ids=list(recording.discovered_page_ids),
        component_names=list(recording.component_names),
        app_screens=list(recording.app_screens),
        device_sections=list(recording.device_sections),
        task_description=recording.task_description,
        total_issues=recording.total_issues,
        high_impact_count=recording.high_impact_count,
        medium_impact_count=recording.medium_impact_count,
        low_impact_count=recording.low_impact_count,
        tags=list(recording.tags),
        notes=recording.notes,
        created_at=_iso(recording.created_at),
        updated_at=_iso(recording.updated_at),
    )


def _recording_content_to_out(recording: Recording) -> RecordingContentOut:
    """Project a :class:`Recording`'s supplementary content to its output model."""
    return RecordingContentOut(
        key_takeaways=dict(recording.key_takeaways),
        user_painpoints=dict(recording.user_painpoints),
        user_assertions=dict(recording.user_assertions),
    )


def _recording_issue_to_out(issue: RecordingIssue) -> RecordingIssueOut:
    """Project a :class:`RecordingIssue` to its output model.

    Mirrors :func:`_serialize_recording_issue` byte-for-byte.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return RecordingIssueOut(
        id=issue.id,
        recording_id=issue.recording_id,
        title=issue.title,
        short_title=issue.short_title,
        language=issue.language,
        what=issue.what,
        why=issue.why,
        who=issue.who,
        remediation=issue.remediation,
        impact=issue.impact.value,
        touchpoint=issue.touchpoint,
        # Widen the nested ``to_dict()`` returns to ``dict[str, object]``
        # so the list types line up with ``RecordingIssueOut``.
        # ``Timecode.to_dict`` returns ``dict[str, str]``; we treat it
        # as the broader object dict the schema declares.
        timecodes=[dict(tc.to_dict()) for tc in issue.timecodes],
        wcag=[dict(w.to_dict()) for w in issue.wcag],
        xpath=issue.xpath,
        element=issue.element,
        html=issue.html,
        project_id=issue.project_id,
        website_ids=list(issue.website_ids),
        page_urls=list(issue.page_urls),
        page_ids=list(issue.page_ids),
        component_names=list(issue.component_names),
        app_screens=list(issue.app_screens),
        device_sections=list(issue.device_sections),
        task_description=issue.task_description,
        status=issue.status,
        assigned_to=issue.assigned_to,
        resolution_notes=issue.resolution_notes,
        tags=list(issue.tags),
        created_at=_iso(issue.created_at),
        updated_at=_iso(issue.updated_at),
    )


def _apply_recording_patch_pyd(
    recording: Recording, body: RecordingPatch,
) -> Recording:
    """Apply a :class:`RecordingPatch` to ``recording`` in place.

    Pydantic already enforces the type contract (strings stay strings,
    ``tags`` stays a list of strings), so this helper only handles
    the value-shape rules the legacy ``_apply_patch_to_recording``
    enforced beyond type: title must be non-empty, optional strings
    collapse to ``None`` when they're empty/whitespace-only.
    """
    fields_set = body.model_fields_set
    if "title" in fields_set:
        if body.title is None or not body.title.strip():
            raise ValidationError(
                "title must be a non-empty string",
                errors=(
                    _FieldError(
                        field="title", code="invalid_value",
                        message="must be non-empty string",
                    ),
                ),
            )
        recording.title = body.title.strip()
    if "description" in fields_set:
        recording.description = (
            body.description.strip()
            if body.description is not None and body.description.strip()
            else None
        )
    if "auditor_name" in fields_set:
        recording.auditor_name = body.auditor_name
    if "auditor_role" in fields_set:
        recording.auditor_role = body.auditor_role
    if "tags" in fields_set and body.tags is not None:
        recording.tags = list(body.tags)
    if "notes" in fields_set:
        recording.notes = body.notes
    recording.updated_at = datetime.now()
    return recording


@api_bp.route("/recordings", methods=["POST"])
@api_endpoint
@document(
    request_form=RecordingUploadIn,
    request_files=["recording_json_en", "recording_json_fr"],
    response_201=RecordingOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Recordings"],
    summary="Upload a Dictaphone JSON recording",
    description=(
        "Multipart upload of a Dictaphone audit recording. The "
        "``recording_json_en`` file part is required; ``recording_json_fr`` "
        "is optional but, if provided, must share the same ``recording`` "
        "id as the English file. Returns 201 with the persisted "
        "``RecordingOut`` resource plus a ``Location`` header pointing "
        "at ``/api/v1/recordings/<id>``. Supplementary content (key-"
        "takeaways / painpoints / assertions) lands via the separate "
        "``PATCH /recordings/<id>/content`` endpoint."
    ),
)
def create_recording_rest(
    form: RecordingUploadIn,
    recording_json_en: FileStorage | None = None,
    recording_json_fr: FileStorage | None = None,
) -> tuple[Response, int] | Response:
    """Upload a Dictaphone JSON recording."""
    import json
    import tempfile
    from pathlib import Path

    from auto_a11y.importers import DictaphoneImporter

    if not form.project_id:
        raise ValidationError(
            "project_id is required",
            errors=(
                _FieldError(
                    field="project_id", code="required", message="required",
                ),
            ),
        )
    project_id_raw = form.project_id
    project = get_db().get_project(project_id_raw)
    if project is None:
        raise NotFoundError(f"project {project_id_raw} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id_raw,
    )

    file_en = recording_json_en
    if file_en is None or not file_en.filename:
        raise ValidationError(
            "recording_json_en part is required",
            errors=(
                _FieldError(
                    field="recording_json_en", code="required",
                    message="required",
                ),
            ),
        )
    if not file_en.filename.endswith(".json"):
        raise ValidationError(
            "recording_json_en must be a .json file",
            errors=(
                _FieldError(
                    field="recording_json_en", code="invalid_value",
                    message="must end with .json",
                ),
            ),
        )

    file_fr = recording_json_fr
    has_french = (
        file_fr is not None
        and file_fr.filename is not None
        and file_fr.filename.endswith(".json")
    )

    recording_type_raw = (
        form.recording_type if form.recording_type is not None else "audit"
    )
    try:
        RecordingType(recording_type_raw)
    except ValueError as exc:
        raise ValidationError(
            f"recording_type {recording_type_raw!r} is not recognized",
            errors=(
                _FieldError(
                    field="recording_type", code="invalid_value",
                    message=str(exc),
                ),
            ),
        ) from exc

    # Read both files now so a parse error reports as 400, not 500.
    content_en = file_en.read().decode("utf-8")
    try:
        data_en = json.loads(content_en)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"recording_json_en is not valid JSON: {exc}",
            errors=(
                _FieldError(
                    field="recording_json_en", code="invalid_format",
                    message=str(exc),
                ),
            ),
        ) from exc

    recording_id_value: str = str(data_en.get("recording", "")).strip()
    if not recording_id_value:
        raise ValidationError(
            "recording_json_en is missing the 'recording' id field",
            errors=(
                _FieldError(
                    field="recording_json_en.recording", code="required",
                    message="required",
                ),
            ),
        )

    content_fr: str | None = None
    if has_french and file_fr is not None:
        # Use a local non-Optional ``content_fr_text`` for the
        # ``json.loads`` call so pyright keeps the narrowing.
        content_fr_text = file_fr.read().decode("utf-8")
        content_fr = content_fr_text
        try:
            data_fr = json.loads(content_fr_text)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"recording_json_fr is not valid JSON: {exc}",
                errors=(
                    _FieldError(
                        field="recording_json_fr", code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc
        recording_id_fr = str(data_fr.get("recording", "")).strip()
        if recording_id_fr != recording_id_value:
            raise ValidationError(
                f"recording id mismatch: en={recording_id_value!r}, fr={recording_id_fr!r}",
                errors=(
                    _FieldError(
                        field="recording_json_fr.recording",
                        code="invalid_value",
                        message="must match recording_json_en.recording",
                    ),
                ),
            )

    existing = get_db().get_recording_by_recording_id(recording_id_value)
    if existing is not None:
        raise ConflictError(
            f"recording {recording_id_value!r} already exists"
        )

    auditor_info: dict[str, Any] = {
        "title": form.title if form.title is not None else "",
        "description": form.description if form.description is not None else "",
        "auditor_name": form.auditor_name if form.auditor_name is not None else "",
        "auditor_role": form.auditor_role if form.auditor_role is not None else "",
        "test_user_account": (
            form.test_user_account.strip()
            if form.test_user_account is not None and form.test_user_account.strip()
            else None
        ),
        "lived_experience_tester_id": (
            form.lived_experience_tester_id.strip()
            if form.lived_experience_tester_id is not None
            and form.lived_experience_tester_id.strip()
            else None
        ),
        "test_supervisor_id": (
            form.test_supervisor_id.strip()
            if form.test_supervisor_id is not None and form.test_supervisor_id.strip()
            else None
        ),
        "media_file_path": (
            form.media_file_path if form.media_file_path is not None else ""
        ),
    }
    testing_scope: dict[str, bool] = {
        "forms": form.scope_forms == "on",
        "video": form.scope_video == "on",
        "live_multimedia": form.scope_live_multimedia == "on",
        "multilingual": form.scope_multilingual == "on",
        "orientation": form.scope_orientation == "on",
        "zoom": form.scope_zoom == "on",
        "timeouts": form.scope_timeouts == "on",
        "motion_actuation": form.scope_motion_actuation == "on",
        "drag_drop": form.scope_drag_drop == "on",
    }

    page_urls = _parse_multiline_form_field(form.page_urls)
    component_names = _parse_multiline_form_field(form.component_names)
    app_screens = _parse_multiline_form_field(form.app_screens)
    device_sections = _parse_multiline_form_field(form.device_sections)
    # Multi-value form key: read directly off ``request.form`` since the
    # @document decorator collapses repeated keys via ``form.items()``.
    discovered_page_ids = request.form.getlist("discovered_page_ids")
    task_description = (
        form.task_description.strip()
        if form.task_description is not None and form.task_description.strip()
        else None
    )

    importer = DictaphoneImporter()
    tmp_paths: list[Path] = []
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_en.json", delete=False, encoding="utf-8",
        ) as tmp_en:
            tmp_en.write(content_en)
            tmp_paths.append(Path(tmp_en.name))

        recording, issues_en = importer.import_from_file(
            str(tmp_paths[0]),
            project_id=project_id_raw,
            page_urls=page_urls,
            discovered_page_ids=discovered_page_ids,
            component_names=component_names,
            app_screens=app_screens,
            device_sections=device_sections,
            task_description=task_description,
            auditor_info=auditor_info,
            recording_type=recording_type_raw,
            testing_scope=testing_scope,
            language="en",
        )

        all_issues: list[RecordingIssue] = list(issues_en)
        if content_fr is not None:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix="_fr.json", delete=False, encoding="utf-8",
            ) as tmp_fr:
                tmp_fr.write(content_fr)
                tmp_paths.append(Path(tmp_fr.name))
            _, issues_fr = importer.import_from_file(
                str(tmp_paths[1]),
                project_id=project_id_raw,
                page_urls=page_urls,
                discovered_page_ids=discovered_page_ids,
                component_names=component_names,
                app_screens=app_screens,
                device_sections=device_sections,
                task_description=task_description,
                auditor_info=auditor_info,
                recording_type=recording_type_raw,
                testing_scope=testing_scope,
                language="fr",
            )
            all_issues.extend(issues_fr)

        created_id = get_db().create_recording(recording)
        get_db().create_recording_issues_bulk(all_issues)

        # Mirror the legacy form: append the new recording id to
        # ``project.recording_ids`` so the project view sees it.
        if created_id not in project.recording_ids:
            project.recording_ids.append(created_id)
            get_db().update_project(project)

        refreshed = get_db().get_recording(created_id)
        if refreshed is None:
            raise ConflictError("recording failed to persist")
    finally:
        for path in tmp_paths:
            path.unlink(missing_ok=True)

    # The @document decorator serialises BaseModel returns through
    # ``jsonify``, but we need a ``Location`` header on the new resource,
    # so we pre-build the Response here and attach the header.
    payload = _recording_to_out(refreshed)
    response = jsonify(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
    response.headers["Location"] = f"/api/v1/recordings/{created_id}"
    return response, 201


@api_bp.route("/recordings", methods=["GET"])
@api_endpoint
@document(
    response_200=RecordingListOut,
    errors=[400, 401, 403, 404],
    tags=["Recordings"],
    summary="List recordings",
    description=(
        "Returns recordings filtered by ``?project_id=<id>`` (required) "
        "with optional ``?recording_type=<type>`` narrowing. Cursor-"
        "paginated using ``{items, next_cursor}`` shape."
    ),
)
def list_recordings_rest() -> (
    tuple[RecordingListOut, int] | tuple[Response, int] | Response
):
    """List recordings.

    Filter via ``?project_id=<id>`` (required for non-superadmins so the
    project-role check has a target). ``recording_type=<type>`` further
    narrows by type.
    """
    project_id = request.args.get("project_id")
    if project_id is None:
        raise ValidationError(
            "project_id query parameter is required",
            errors=(_FieldError(field="project_id", code="required", message="required"),),
        )
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    recording_type_raw = request.args.get("recording_type")
    if recording_type_raw is not None:
        try:
            RecordingType(recording_type_raw)
        except ValueError as exc:
            raise ValidationError(
                "recording_type is not a recognized value",
                errors=(_FieldError(field="recording_type", code="invalid_value", message=str(exc)),),
            ) from exc

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"project_id": project_id}
    if recording_type_raw is not None:
        query["recording_type"] = recording_type_raw
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().recordings.find(query).sort("_id", -1).limit(limit + 1))
    recordings = [Recording.from_dict(doc) for doc in docs]
    page = paginate(
        recordings, limit=limit, get_id=lambda r: str(r.mongo_id) if r.mongo_id else ""
    )
    return RecordingListOut(
        items=[_recording_to_out(r) for r in page["items"]],
        next_cursor=page["next_cursor"],
    ), 200


@api_bp.route("/recordings/<recording_id>", methods=["GET"])
@api_endpoint
@document(
    response_200=RecordingOut,
    errors=[401, 403, 404],
    tags=["Recordings"],
    summary="Get a recording by ID",
    description="Returns the recording resource (``RecordingOut``).",
)
def get_recording_rest(
    recording_id: str,
) -> tuple[RecordingOut, int] | tuple[Response, int] | Response:
    """Get a recording by id."""
    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    return _recording_to_out(recording), 200


@api_bp.route("/recordings/<recording_id>", methods=["PATCH"])
@api_endpoint
@document(
    request=RecordingPatch,
    response_200=RecordingOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Recordings"],
    summary="Partially update a recording's metadata",
    description=(
        "Partial update -- only fields present in the request body are "
        "applied. Editable fields are narrow (title, description, "
        "auditor_name/role, tags, notes); server-managed fields "
        "(counts, recording_type, project_id, Drupal sync, the multi-"
        "language content arrays) are not patchable here -- use the "
        "supplementary-content PATCH for content updates."
    ),
)
def patch_recording_rest(
    recording_id: str, body: RecordingPatch,
) -> tuple[RecordingOut, int] | tuple[Response, int] | Response:
    """Partial update of a recording's metadata."""
    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    patched = _apply_recording_patch_pyd(recording, body)
    if not get_db().update_recording(patched):
        raise ConflictError("recording could not be updated")
    return _recording_to_out(patched), 200


@api_bp.route("/recordings/<recording_id>", methods=["DELETE"])
@api_endpoint
@document(
    response_204=Empty,
    errors=[401, 403, 404],
    tags=["Recordings"],
    summary="Delete a recording",
    description=(
        "Deletes the recording and cascades to its ``RecordingIssues``. "
        "Returns ``204 No Content`` with an empty body."
    ),
)
def delete_recording_rest(
    recording_id: str,
) -> tuple[Empty, int] | tuple[Response, int]:
    """Delete a recording. Cascades to its RecordingIssues."""
    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, project_id=project_id
    )
    get_db().delete_recording(recording_id)
    return Empty(), 204


# --- Recording supplementary content (§5.6 deferred slot) ---------------------
#
# The legacy upload form accepts up to 6 optional content files
# (key_takeaways / user_painpoints / user_assertions × en/fr) in
# either HTML or JSON. These were called out as deferred in 38cdc2ac
# — they're not required for a recording to exist, and clients can
# add them after the initial upload.
#
# Shape: GET returns ``{key_takeaways, user_painpoints, user_assertions}``,
# each a per-language dict. PATCH accepts multipart with up to 6
# optional file parts; each present file updates that
# ``(content_type, language)`` slot on the recording — absent slots
# are preserved. Format (HTML vs JSON) is inferred from the file
# extension; ``.html``/``.htm`` parse as HTML, ``.json`` as JSON,
# anything else returns 400.


# Maps the multipart file part name → ``(recording attribute, language)``.
# A flat lookup keeps the loop body small and avoids three almost-
# identical inner functions.
_RECORDING_CONTENT_PARTS: tuple[tuple[str, str, str], ...] = (
    ("key_takeaways_file_en", "key_takeaways", "en"),
    ("key_takeaways_file_fr", "key_takeaways", "fr"),
    ("user_painpoints_file_en", "user_painpoints", "en"),
    ("user_painpoints_file_fr", "user_painpoints", "fr"),
    ("user_assertions_file_en", "user_assertions", "en"),
    ("user_assertions_file_fr", "user_assertions", "fr"),
)


def _parse_recording_content_file(
    filename: str, content_text: str, *, content_type: str, field: str,
) -> list[dict[str, Any]]:
    """Parse an uploaded content file as either HTML or JSON.

    Format dispatch is by extension:

    - ``.json`` → JSON parser for the given ``content_type``
    - ``.html`` / ``.htm`` → HTML parser

    Anything else raises :class:`ValidationError`. Invalid JSON or
    HTML also raises so the route surfaces a 400 with a field path
    pointing at the bad part.
    """
    import json

    from auto_a11y.parsers import (
        parse_key_takeaways_html,
        parse_key_takeaways_json,
        parse_user_assertions_html,
        parse_user_assertions_json,
        parse_user_painpoints_html,
        parse_user_painpoints_json,
    )

    lower = filename.lower()
    is_json = lower.endswith(".json")
    is_html = lower.endswith(".html") or lower.endswith(".htm")
    if not (is_json or is_html):
        raise ValidationError(
            f"{field} must be a .json, .html, or .htm file",
            errors=(
                _FieldError(
                    field=field, code="invalid_value",
                    message="extension must be .json/.html/.htm",
                ),
            ),
        )

    try:
        if is_json:
            data = json.loads(content_text)
            if content_type == "key_takeaways":
                return parse_key_takeaways_json(data)
            if content_type == "user_painpoints":
                return parse_user_painpoints_json(data)
            return parse_user_assertions_json(data)
        if content_type == "key_takeaways":
            return parse_key_takeaways_html(content_text)
        if content_type == "user_painpoints":
            return parse_user_painpoints_html(content_text)
        return parse_user_assertions_html(content_text)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"{field} is not valid JSON: {exc}",
            errors=(
                _FieldError(
                    field=field, code="invalid_format", message=str(exc),
                ),
            ),
        ) from exc
    except Exception as exc:  # parser raises ValueError on malformed input
        raise ValidationError(
            f"{field} could not be parsed: {exc}",
            errors=(
                _FieldError(
                    field=field, code="invalid_format", message=str(exc),
                ),
            ),
        ) from exc


@api_bp.route("/recordings/<recording_id>/content", methods=["GET"])
@api_endpoint
@document(
    response_200=RecordingContentOut,
    errors=[401, 403, 404],
    tags=["Recordings"],
    summary="Read a recording's supplementary content",
    description=(
        "Returns ``{key_takeaways, user_painpoints, user_assertions}``, "
        "each a per-language dict (``{en: [...], fr: [...]}``). Kept off "
        "the main recording response so the common case doesn't carry "
        "potentially-large content payloads."
    ),
)
def get_recording_content(
    recording_id: str,
) -> tuple[RecordingContentOut, int] | tuple[Response, int] | Response:
    """Read the recording's supplementary content fields."""
    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id,
    )
    return _recording_content_to_out(recording), 200


@api_bp.route("/recordings/<recording_id>/content", methods=["PATCH"])
@api_endpoint
@document(
    request_form=RecordingContentPatch,
    request_files=[
        "key_takeaways_file_en",
        "key_takeaways_file_fr",
        "user_painpoints_file_en",
        "user_painpoints_file_fr",
        "user_assertions_file_en",
        "user_assertions_file_fr",
    ],
    response_200=RecordingContentOut,
    errors=[400, 401, 403, 404, 409],
    tags=["Recordings"],
    summary="Merge in supplementary content for a recording",
    description=(
        "Multipart PATCH with up to six optional file parts "
        "(key_takeaways / user_painpoints / user_assertions × en/fr). "
        "Each present file is parsed (HTML or JSON by extension) and "
        "stored in that ``(content_type, language)`` slot; absent slots "
        "are preserved. Sending zero files is a valid no-op that "
        "returns the current content."
    ),
)
def patch_recording_content(
    recording_id: str,
    form: RecordingContentPatch,
    key_takeaways_file_en: FileStorage | None = None,
    key_takeaways_file_fr: FileStorage | None = None,
    user_painpoints_file_en: FileStorage | None = None,
    user_painpoints_file_fr: FileStorage | None = None,
    user_assertions_file_en: FileStorage | None = None,
    user_assertions_file_fr: FileStorage | None = None,
) -> tuple[RecordingContentOut, int] | tuple[Response, int] | Response:
    """Merge in supplementary content for a recording.

    See the route description for the request shape. The empty
    ``form`` parameter is required by the decorator's multipart
    contract; this endpoint takes no non-file form fields.
    """
    del form  # No non-file form fields; satisfied only for the decorator.

    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id,
    )

    # Pair the decorator-bound FileStorage kwargs with the
    # ``(attr_name, lang_code)`` they target. A local lookup avoids
    # touching ``request.files`` directly and keeps the file-handling
    # path uniform with the rest of the §5.6 surface.
    file_parts: tuple[tuple[FileStorage | None, str, str, str], ...] = (
        (key_takeaways_file_en, "key_takeaways_file_en", "key_takeaways", "en"),
        (key_takeaways_file_fr, "key_takeaways_file_fr", "key_takeaways", "fr"),
        (user_painpoints_file_en, "user_painpoints_file_en", "user_painpoints", "en"),
        (user_painpoints_file_fr, "user_painpoints_file_fr", "user_painpoints", "fr"),
        (user_assertions_file_en, "user_assertions_file_en", "user_assertions", "en"),
        (user_assertions_file_fr, "user_assertions_file_fr", "user_assertions", "fr"),
    )

    any_changes = False
    for uploaded, part_name, attr_name, lang_code in file_parts:
        if uploaded is None or not uploaded.filename:
            continue
        content_bytes = uploaded.read()
        try:
            content_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                f"{part_name} must be valid UTF-8",
                errors=(
                    _FieldError(
                        field=part_name, code="invalid_format",
                        message=str(exc),
                    ),
                ),
            ) from exc
        parsed = _parse_recording_content_file(
            uploaded.filename, content_text,
            content_type=attr_name, field=part_name,
        )
        # ``getattr(recording, attr_name)`` is the per-language dict;
        # we write a fresh dict-copy with the new slot so the model's
        # default-factory ``{}`` isn't mutated under us across requests.
        current: dict[str, list[dict[str, Any]]] = dict(
            getattr(recording, attr_name)
        )
        current[lang_code] = parsed
        setattr(recording, attr_name, current)
        any_changes = True

    if any_changes:
        recording.updated_at = datetime.now()
        if not get_db().update_recording(recording):
            raise ConflictError(
                f"recording {recording_id} could not be updated"
            )
        refreshed = get_db().get_recording(recording_id)
        if refreshed is None:
            raise ConflictError(
                f"recording {recording_id} disappeared after update"
            )
        recording = refreshed

    return _recording_content_to_out(recording), 200


@api_bp.route("/recordings/<recording_id>/issues", methods=["GET"])
@api_endpoint
@document(
    response_200=RecordingIssueListOut,
    errors=[400, 401, 403, 404],
    tags=["Recordings"],
    summary="List issues for a recording",
    description=(
        "Returns the issues belonging to the recording with cursor "
        "pagination. The response shape is ``{items, next_cursor}``."
    ),
)
def list_recording_issues_rest(
    recording_id: str,
) -> tuple[RecordingIssueListOut, int] | tuple[Response, int] | Response:
    """List the issues for a recording with cursor pagination."""
    recording = get_db().get_recording(recording_id)
    if recording is None:
        raise NotFoundError(f"recording {recording_id} not found")
    project_id = _resolve_recording_project_id(recording)
    if project_id is None:
        raise NotFoundError(f"recording {recording_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    # RecordingIssue.recording_id is the human-readable string
    # (e.g. "NED-A") on the parent Recording, not its ObjectId. Look it
    # up from the resolved recording rather than the URL parameter so a
    # caller cannot inject an arbitrary string here.
    query: dict[str, Any] = {"recording_id": recording.recording_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().recording_issues.find(query).sort("_id", -1).limit(limit + 1))
    issues = [RecordingIssue.from_dict(doc) for doc in docs]
    page = paginate(
        issues, limit=limit, get_id=lambda i: str(i.mongo_id) if i.mongo_id else ""
    )
    return RecordingIssueListOut(
        items=[_recording_issue_to_out(i) for i in page["items"]],
        next_cursor=page["next_cursor"],
    ), 200


@api_bp.route("/recording-issues/<issue_id>", methods=["GET"])
@api_endpoint
def get_recording_issue_rest(issue_id: str) -> tuple[Response, int] | Response:
    """Get a recording issue by id."""
    issue = get_db().get_recording_issue(issue_id)
    if issue is None:
        raise NotFoundError(f"recording issue {issue_id} not found")
    if issue.project_id is None:
        raise NotFoundError(f"recording issue {issue_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=issue.project_id
    )
    return jsonify(_serialize_recording_issue(issue))


@api_bp.route("/recording-issues/<issue_id>", methods=["PATCH"])
@api_endpoint
def patch_recording_issue_rest(issue_id: str) -> tuple[Response, int] | Response:
    """Partial update of a recording issue (status, assignment, notes, tags)."""
    issue = get_db().get_recording_issue(issue_id)
    if issue is None:
        raise NotFoundError(f"recording issue {issue_id} not found")
    if issue.project_id is None:
        raise NotFoundError(f"recording issue {issue_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=issue.project_id
    )
    body = _require_dict_body()
    patched = _apply_patch_to_recording_issue(issue, body)
    if not get_db().update_recording_issue(patched):
        raise ConflictError("recording issue could not be updated")
    return jsonify(_serialize_recording_issue(patched))


# ---------------------------------------------------------------------------
# Admin settings (REST shape — uses the @api_endpoint scaffolding).
#
# Settings live in the ``system_settings`` singleton Mongo doc, with each
# named "section" stored under a top-level key and falling back to the
# matching environment variables when the DB section is unset. The
# legacy admin_settings_bp HTML routes still serve the admin UI; these
# REST endpoints offer a JSON shape for programmatic access.
#
# Per docs/REST_API_ROADMAP.md §5.14. Auth is superadmin-only — settings
# changes affect every project on the deployment, so the project-role
# helper isn't sufficient.
# ---------------------------------------------------------------------------

import os  # noqa: E402

from auto_a11y.core.runtime_config import (  # noqa: E402
    CONFIG_SECTIONS,
    ConfigField,
    ConfigSection,
    FieldType,
    FieldValue,
    coerce_value,
    env_value,
    get_section as get_config_section,
    is_bool_true,
    is_password_set,
    parse_bool,
    section_source,
)
from auto_a11y.core.system_settings import SystemSettings  # noqa: E402
from auto_a11y.drupal.config import DRUPAL_SETTINGS_KEY  # noqa: E402
from auto_a11y.web.api import require_superadmin  # noqa: E402


def _serialize_field_value(field: ConfigField, section_data: dict[str, Any] | None) -> Any:
    """Project a field's *current effective* value to JSON.

    Password fields are never echoed — callers see ``"<set>"`` or ``null``
    instead so they can tell whether one is configured without leaking the
    secret. Booleans are real JSON booleans (the form layer uses string
    ``"True"``/``"False"``; we don't carry that into the API).
    """
    if field.field_type is FieldType.PASSWORD:
        return None  # see _serialize_section: password_set sibling reports the bit
    if section_data is not None and field.db_key in section_data:
        return section_data[field.db_key]
    raw_env = env_value(field)
    if field.field_type is FieldType.BOOL:
        return parse_bool(raw_env)
    if field.field_type is FieldType.INT:
        try:
            return int(raw_env) if raw_env != "" else None
        except ValueError:
            return None
    if field.field_type is FieldType.FLOAT:
        try:
            return float(raw_env) if raw_env != "" else None
        except ValueError:
            return None
    return raw_env


def _serialize_settings_section(
    section: ConfigSection, section_data: dict[str, Any] | None
) -> dict[str, Any]:
    """Project a settings section to JSON: values + per-field metadata.

    Each field shows up under its ``db_key`` with the effective value;
    password fields are reported as ``null`` with a sibling
    ``<key>_set`` boolean so callers can render a "[set]" indicator
    without us echoing the secret.
    """
    values: dict[str, Any] = {}
    for field in section.fields:
        if field.field_type is FieldType.PASSWORD:
            values[field.db_key] = None
            values[f"{field.db_key}_set"] = is_password_set(field, section_data)
        elif field.field_type is FieldType.BOOL:
            values[field.db_key] = is_bool_true(field, section_data)
        else:
            values[field.db_key] = _serialize_field_value(field, section_data)
    return {
        "section_id": section.section_id,
        "source": section_source(section, section_data),
        "values": values,
    }


def _serialize_drupal_settings(section_data: dict[str, Any] | None) -> dict[str, Any]:
    """Drupal config has its own shape (not in CONFIG_SECTIONS); project here."""
    if section_data is not None:
        return {
            "source": "database",
            "values": {
                "base_url": section_data.get("base_url", ""),
                "username": section_data.get("username", ""),
                "password_set": bool(section_data.get("password")),
                "enabled": bool(section_data.get("enabled", True)),
            },
        }
    env_present = any(
        os.getenv(name)
        for name in ("DRUPAL_BASE_URL", "DRUPAL_USERNAME", "DRUPAL_PASSWORD")
    )
    return {
        "source": "environment" if env_present else "unset",
        "values": {
            "base_url": os.getenv("DRUPAL_BASE_URL", ""),
            "username": os.getenv("DRUPAL_USERNAME", ""),
            "password_set": bool(os.getenv("DRUPAL_PASSWORD")),
            "enabled": os.getenv("DRUPAL_EXPORT_ENABLED", "true").lower() == "true",
        },
    }


def _coerce_field_value(field: ConfigField, raw: Any) -> FieldValue:
    """Coerce a JSON-decoded value to the field's typed value, raising on bad input.

    JSON ``true``/``false`` map directly to bools; integers and floats are
    accepted as-is or as numeric strings; password fields require strings.
    """
    if field.field_type is FieldType.BOOL:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return parse_bool(raw)
        raise ValidationError(
            f"{field.db_key} must be a boolean",
            errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be boolean"),),
        )
    if field.field_type is FieldType.INT:
        if isinstance(raw, bool):
            raise ValidationError(
                f"{field.db_key} must be an integer",
                errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be integer"),),
            )
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError as exc:
                raise ValidationError(
                    f"{field.db_key} must be an integer",
                    errors=(_FieldError(field=field.db_key, code="invalid_format", message=str(exc)),),
                ) from exc
        raise ValidationError(
            f"{field.db_key} must be an integer",
            errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be integer"),),
        )
    if field.field_type is FieldType.FLOAT:
        if isinstance(raw, bool):
            raise ValidationError(
                f"{field.db_key} must be a number",
                errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be number"),),
            )
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw)
            except ValueError as exc:
                raise ValidationError(
                    f"{field.db_key} must be a number",
                    errors=(_FieldError(field=field.db_key, code="invalid_format", message=str(exc)),),
                ) from exc
        raise ValidationError(
            f"{field.db_key} must be a number",
            errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be number"),),
        )
    if not isinstance(raw, str):
        raise ValidationError(
            f"{field.db_key} must be a string",
            errors=(_FieldError(field=field.db_key, code="invalid_type", message="must be string"),),
        )
    return coerce_value(field, raw)


@api_bp.route("/admin/settings", methods=["GET"])
@api_endpoint
def get_admin_settings() -> Response:
    """Return the full settings document — Drupal config + every named section."""
    require_superadmin()
    settings = SystemSettings(get_db())
    sections = [
        _serialize_settings_section(s, settings.get_section(s.section_id))
        for s in CONFIG_SECTIONS
    ]
    return jsonify(
        {
            "drupal": _serialize_drupal_settings(settings.get_section(DRUPAL_SETTINGS_KEY)),
            "sections": sections,
        }
    )


@api_bp.route("/admin/settings/drupal", methods=["PATCH"])
@api_endpoint
def patch_drupal_settings() -> Response:
    """Update Drupal connection settings.

    Validation mirrors the legacy form: ``base_url`` must be http(s),
    ``username`` is required, and a blank ``password`` keeps the existing
    stored value (so admins don't have to re-enter the secret on every
    edit).
    """
    require_superadmin()
    body = _require_dict_body()
    settings = SystemSettings(get_db())
    existing = settings.get_section(DRUPAL_SETTINGS_KEY) or {}

    base_url = body.get("base_url", existing.get("base_url", ""))
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValidationError(
            "base_url is required",
            errors=(_FieldError(field="base_url", code="required", message="required"),),
        )
    base_url = base_url.strip()
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise ValidationError(
            "base_url must be an http(s) URL",
            errors=(_FieldError(field="base_url", code="invalid_format", message="must start with http:// or https://"),),
        )

    username = body.get("username", existing.get("username", ""))
    if not isinstance(username, str) or not username.strip():
        raise ValidationError(
            "username is required",
            errors=(_FieldError(field="username", code="required", message="required"),),
        )
    username = username.strip()

    password_raw = body.get("password")
    existing_password = existing.get("password") if isinstance(existing.get("password"), str) else None
    if password_raw is None:
        password = existing_password
        if password is None:
            raise ValidationError(
                "password is required (no existing password to preserve)",
                errors=(_FieldError(field="password", code="required", message="required"),),
            )
    else:
        if not isinstance(password_raw, str):
            raise ValidationError(
                "password must be a string",
                errors=(_FieldError(field="password", code="invalid_type", message="must be string"),),
            )
        password = password_raw

    enabled_raw: Any = body.get("enabled", existing.get("enabled", True))
    if not isinstance(enabled_raw, bool):
        raise ValidationError(
            "enabled must be a boolean",
            errors=(_FieldError(field="enabled", code="invalid_type", message="must be boolean"),),
        )

    user_id = str(current_user.get_id()) if current_user.is_authenticated else None
    settings.set_section(
        DRUPAL_SETTINGS_KEY,
        {
            "base_url": base_url,
            "username": username,
            "password": password,
            "enabled": enabled_raw,
        },
        updated_by=user_id,
    )
    return jsonify(_serialize_drupal_settings(settings.get_section(DRUPAL_SETTINGS_KEY)))


@api_bp.route("/admin/settings/drupal", methods=["DELETE"])
@api_endpoint
def delete_drupal_settings() -> tuple[Response, int]:
    """Clear the Drupal section so the env-var fallback applies again."""
    require_superadmin()
    SystemSettings(get_db()).clear_section(DRUPAL_SETTINGS_KEY)
    return Response(status=204), 204


@api_bp.route("/admin/settings/<section_id>", methods=["PATCH"])
@api_endpoint
def patch_settings_section(section_id: str) -> Response:
    """Partial update of a named CONFIG_SECTIONS section.

    Each key in the request body must match a ``db_key`` defined in the
    section schema; the value is type-coerced per :class:`ConfigField`.
    Password fields with a blank string preserve the existing value
    (mirrors the legacy "leave blank to keep" form behaviour).

    The Drupal section is *not* reachable through this endpoint — its
    schema is special-cased and lives at /api/v1/admin/settings/drupal.
    """
    require_superadmin()
    if section_id == DRUPAL_SETTINGS_KEY:
        raise NotFoundError(
            f"unknown settings section {section_id!r} — use /admin/settings/drupal"
        )
    section = get_config_section(section_id)
    if section is None:
        raise NotFoundError(f"unknown settings section {section_id!r}")

    body = _require_dict_body()
    settings = SystemSettings(get_db())
    existing = settings.get_section(section_id) or {}
    db_keys = {f.db_key: f for f in section.fields}

    unknown = [k for k in body.keys() if k not in db_keys]
    if unknown:
        raise ValidationError(
            f"unknown field(s) for section {section_id}: {', '.join(unknown)}",
            errors=tuple(
                _FieldError(field=k, code="unknown_field", message="not in section schema")
                for k in unknown
            ),
        )

    new_values: dict[str, Any] = dict(existing)
    for db_key, raw in body.items():
        field = db_keys[db_key]
        if field.field_type is FieldType.PASSWORD:
            if raw == "" or raw is None:
                # Preserve existing value; only overwrite on a real string.
                if not isinstance(existing.get(db_key), str):
                    # Nothing stored to preserve and no new value provided —
                    # treat as clearing the field.
                    new_values[db_key] = ""
                continue
            if not isinstance(raw, str):
                raise ValidationError(
                    f"{db_key} must be a string",
                    errors=(_FieldError(field=db_key, code="invalid_type", message="must be string"),),
                )
            new_values[db_key] = raw
        else:
            new_values[db_key] = _coerce_field_value(field, raw)

    user_id = str(current_user.get_id()) if current_user.is_authenticated else None
    settings.set_section(section_id, new_values, updated_by=user_id)
    return jsonify(_serialize_settings_section(section, settings.get_section(section_id)))


@api_bp.route("/admin/settings/<section_id>", methods=["DELETE"])
@api_endpoint
def delete_settings_section(section_id: str) -> tuple[Response, int]:
    """Clear a named section so the env-var fallback applies again."""
    require_superadmin()
    if section_id == DRUPAL_SETTINGS_KEY:
        raise NotFoundError(
            f"unknown settings section {section_id!r} — use /admin/settings/drupal"
        )
    if get_config_section(section_id) is None:
        raise NotFoundError(f"unknown settings section {section_id!r}")
    SystemSettings(get_db()).clear_section(section_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Jobs (REST shape — uses the @api_endpoint scaffolding).
#
# The legacy /jobs/* endpoints (stats, active, clear-all, clear-stale,
# cleanup-page-counts) are unauthenticated administrative tools — they
# stay where they are, but the per-job operations (read, cancel) are
# the kind of thing a future SPA would poll, so they get a proper REST
# treatment with auth + RFC 7807 errors.
#
# In scope: ``GET /api/v1/jobs/<job_id>`` and
# ``POST /api/v1/jobs/<job_id>/cancel``. Restart is more involved (the
# legacy reports.py /job/<id>/restart hard-codes the report-job
# generator rebuild) and is deferred to a follow-up PR alongside the
# test-runs / reports REST work, where the per-job-type restart
# generators have a natural home.
#
# Per docs/REST_API_ROADMAP.md §5.15.
# ---------------------------------------------------------------------------

from auto_a11y.web.api import require_authenticated  # noqa: E402


def _serialize_job(doc: dict[str, Any]) -> dict[str, Any]:
    """Project a raw job Mongo doc to a JSON-safe dict.

    Datetimes go to ISO 8601, the Mongo ``_id`` is dropped (the public
    identifier is ``job_id``), and the nested ``progress``/``metadata``
    dicts are passed through as-is so callers can render whatever the
    individual job_type recorded there.
    """

    def _iso(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    return {
        "job_id": doc.get("job_id"),
        "job_type": doc.get("job_type"),
        "status": doc.get("status"),
        "website_id": doc.get("website_id"),
        "project_id": doc.get("project_id"),
        "user_id": doc.get("user_id"),
        "session_id": doc.get("session_id"),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "started_at": _iso(doc.get("started_at")),
        "completed_at": _iso(doc.get("completed_at")),
        "progress": doc.get("progress") or {},
        "metadata": doc.get("metadata") or {},
        "error": doc.get("error"),
        "result": doc.get("result"),
        "cancellation_requested": doc.get("cancellation_requested", False),
        "cancellation_requested_at": _iso(doc.get("cancellation_requested_at")),
        "cancellation_requested_by": doc.get("cancellation_requested_by"),
    }


def _resolve_job_or_404(job_id: str) -> dict[str, Any]:
    job_manager = JobManager(get_db())
    doc = job_manager.get_job(job_id)
    if doc is None:
        raise NotFoundError(f"job {job_id} not found")
    return doc


@api_bp.route("/jobs/<job_id>", methods=["GET"])
@api_endpoint
def get_job_rest(job_id: str) -> tuple[Response, int] | Response:
    """Read a single job's status, progress, and metadata."""
    require_authenticated()
    doc = _resolve_job_or_404(job_id)
    return jsonify(_serialize_job(doc))


@api_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@api_endpoint
def cancel_job_rest(job_id: str) -> tuple[Response, int] | Response:
    """Request cancellation of a pending or running job.

    Returns 202 because cancellation is asynchronous — the worker
    thread polls the ``cancellation_requested`` flag and transitions to
    CANCELLED on its next checkpoint. The response shape mirrors GET so
    callers can immediately observe the new ``CANCELLING`` status.

    Idempotent: a second POST against an already-cancelling job returns
    409, since the request_cancellation underlying call rejects the
    transition once the status has already moved past
    ``pending``/``running``.
    """
    require_authenticated()
    doc = _resolve_job_or_404(job_id)

    job_manager = JobManager(get_db())
    requested_by = (
        str(current_user.get_id()) if current_user.is_authenticated else None
    )
    requested = job_manager.request_cancellation(job_id, requested_by=requested_by)
    if not requested:
        # Either the job is no longer cancellable (already completed,
        # cancelled, or failed) or the update lost a race. Surface the
        # current status so callers can decide what to do.
        current_status = doc.get("status")
        raise ConflictError(
            f"job {job_id} cannot be cancelled (current status: {current_status})"
        )

    refreshed = job_manager.get_job(job_id)
    if refreshed is None:
        raise ConflictError(f"job {job_id} disappeared after cancel")
    return jsonify(_serialize_job(refreshed)), 202


# ---------------------------------------------------------------------------
# PDF documents (REST shape — uses the @api_endpoint scaffolding).
#
# Sits alongside the existing pdf_bp HTML routes at /projects/<id>/pdfs,
# /websites/<id>/pdfs, /pdfs/<id>, /pdfs/<id>/delete, etc. (still serving
# the admin frontend) and the various /pdfs/<id>/file, /audit, /export,
# /images, /issue-map, /pdfmax-report viewer routes.
#
# In scope: list (project- and website-scoped), single read, delete.
# Out of scope (deferred to follow-up PRs):
#   - POST /api/v1/projects/<id>/pdfs (multipart upload + DB dedup +
#     async fetch) — same complexity bucket as the recordings upload
#   - POST /pdf-documents/<id>/audits / /audits/latest / /audits/latest/cancel
#     (action endpoints; share idempotency-key + JobManager mechanics
#     with the deferred test-runs work)
#   - GET /file, /images/<n>, /export?format=, /issue-map, /reports/pdfmax
#     (binary streaming + cached-artefact serving; needs a separate review
#     pass for cache headers, range requests, and content-disposition)
#
# Per docs/REST_API_ROADMAP.md §5.9.
# ---------------------------------------------------------------------------

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus  # noqa: E402
from auto_a11y.pdf.storage import PdfStorage  # noqa: E402


def _pdf_storage() -> PdfStorage:
    return PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))


def _serialize_pdf_document(pdf: PdfDocument) -> dict[str, Any]:
    """Project a :class:`PdfDocument` to a JSON-safe dict.

    Mirrors the shape of the underlying model except that:
    - datetimes become ISO 8601 strings;
    - the Mongo ``_id`` is dropped (the public id is the string ``id`` property);
    - the ``storage_relpath`` and ``images_relpath`` filesystem paths are kept
      because they're useful identifiers for clients that consume the
      file-streaming endpoints (deferred), but they describe layout under a
      server-side base dir, not absolute paths.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": pdf.id,
        "website_id": pdf.website_id,
        "project_id": pdf.project_id,
        "source_url": pdf.source_url,
        "source_type": pdf.source_type,
        "discovered_from_page_id": pdf.discovered_from_page_id,
        "discovered_from_user_id": pdf.discovered_from_user_id,
        "sha256": pdf.sha256,
        "file_size_bytes": pdf.file_size_bytes,
        "storage_relpath": pdf.storage_relpath,
        "images_relpath": pdf.images_relpath,
        "original_filename": pdf.original_filename,
        "pdf_version": pdf.pdf_version,
        "page_count": pdf.page_count,
        "declared_lang": pdf.declared_lang,
        "detected_lang": pdf.detected_lang,
        "lang_confidence": pdf.lang_confidence,
        "status": pdf.status.value,
        "error_reason": pdf.error_reason,
        "last_audit_result_id": pdf.last_audit_result_id,
        "discovered_at": _iso(pdf.discovered_at),
        "last_audited_at": _iso(pdf.last_audited_at),
    }


def _list_pdfs_with_query(query: dict[str, Any]) -> Response:
    """Cursor-paginate ``query`` against the pdf_documents collection."""
    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    status_raw = request.args.get("status")
    if status_raw is not None:
        try:
            query["status"] = PdfDocumentStatus(status_raw).value
        except ValueError as exc:
            raise ValidationError(
                "status is not a recognized PdfDocumentStatus value",
                errors=(_FieldError(field="status", code="invalid_value", message=str(exc)),),
            ) from exc

    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().pdf_documents.find(query).sort("_id", -1).limit(limit + 1))
    pdfs = [PdfDocument.from_dict(doc) for doc in docs]
    page = paginate(
        pdfs, limit=limit, get_id=lambda p: str(p.mongo_id) if p.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_pdf_document(p) for p in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/projects/<project_id>/pdfs", methods=["GET"])
@api_endpoint
def list_pdfs_for_project(project_id: str) -> tuple[Response, int] | Response:
    """List PDF documents across every website in a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    return _list_pdfs_with_query({"project_id": project_id})


@api_bp.route("/websites/<website_id>/pdfs", methods=["GET"])
@api_endpoint
def list_pdfs_for_website(website_id: str) -> tuple[Response, int] | Response:
    """List PDF documents attached to one website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    return _list_pdfs_with_query({"website_id": website_id})


@api_bp.route("/projects/<project_id>/pdfs", methods=["POST"])
@api_endpoint
def create_pdf_for_project(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Add a PDF to a project — multipart upload OR remote URL fetch.

    Two request shapes share this handler:

    1. **multipart/form-data**: ``pdf_file`` part with the raw PDF
       bytes plus a ``website_id`` form field selecting which website
       in the project to attach the document to. Optional
       ``original_filename`` form field overrides the part's
       ``filename`` attribute.

    2. **application/json**: ``{"website_id": "...", "source_url":
       "https://..."}``. The server fetches the URL with
       :meth:`PdfRunner.fetch_pdf_from_url`. Optional ``website_user_id``
       supplies a project test-user credential for sites that require
       auth.

    The two paths converge on
    :meth:`PdfRunner.create_or_find_pdf_document` which dedupes by
    ``(website_id, sha256)``. A dedup hit returns the *existing*
    document with **status 200**; a new document persists and returns
    **201** with a ``Location`` header pointing at
    ``/api/v1/pdf-documents/<id>``.

    Errors:

    - **400** — body missing required fields, ``website_id`` not in
      ``project_id``, JSON body without ``source_url``, multipart
      without ``pdf_file``, fetch failure (4xx/5xx from source url),
      file not a PDF (magic-byte check), or file exceeds the
      ``PDF_MAX_SIZE`` cap.
    - **404** — project or website does not exist.
    - **409** — server has no :class:`PdfRunner` configured.

    Auth: ADMIN/AUDITOR on the project — same as the legacy
    ``POST /projects/<id>/pdfs`` form.
    """
    import asyncio

    from auto_a11y.pdf.errors import FetchFailed, NotAPdf, PdfTooLarge
    from auto_a11y.web.typed_app import get_pdf_runner

    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id,
    )

    runner = get_pdf_runner()
    if runner is None:
        raise ConflictError(
            "PDF runner is not configured on this server"
        )

    user_id_str: str | None = None
    if current_user.is_authenticated:
        raw_uid: Any = current_user.get_id()
        user_id_str = str(raw_uid) if raw_uid is not None else None

    # Branch on Content-Type. Browser-submitted multipart from the
    # legacy form and a curl-style JSON POST both end up here; the
    # handler routes by the body shape rather than separate URLs to
    # keep the upload-or-link choice client-side.
    is_multipart = request.content_type and request.content_type.startswith(
        "multipart/form-data"
    )

    if is_multipart:
        website_id_raw: str | None = request.form.get("website_id")
        if not website_id_raw:
            raise ValidationError(
                "website_id is required",
                errors=(
                    _FieldError(
                        field="website_id", code="required",
                        message="required",
                    ),
                ),
            )
        website = get_db().get_website(website_id_raw)
        if website is None or website.project_id != project_id:
            raise NotFoundError(
                f"website {website_id_raw} not in project {project_id}"
            )

        uploaded = request.files.get("pdf_file")
        if uploaded is None or not uploaded.filename:
            raise ValidationError(
                "pdf_file part is required",
                errors=(
                    _FieldError(
                        field="pdf_file", code="required",
                        message="required",
                    ),
                ),
            )

        original_filename = (
            request.form.get("original_filename")
            or uploaded.filename
            or "document.pdf"
        )
        pdf_bytes = uploaded.read()

        try:
            doc = asyncio.run(
                runner.create_or_find_pdf_document(
                    pdf_bytes,
                    website_id=website_id_raw,
                    project_id=project_id,
                    source_type="uploaded",
                    discovered_from_page_id=None,
                    discovered_from_user_id=user_id_str,
                    original_filename=original_filename,
                    source_url=None,
                )
            )
        except NotAPdf as exc:
            raise ValidationError(
                "uploaded bytes are not a PDF",
                errors=(
                    _FieldError(
                        field="pdf_file", code="invalid_value",
                        message=str(exc),
                    ),
                ),
            ) from exc
        except PdfTooLarge as exc:
            raise ValidationError(
                f"PDF exceeds max size ({exc.size_bytes} > {exc.limit_bytes})",
                errors=(
                    _FieldError(
                        field="pdf_file", code="too_large",
                        message=f"{exc.size_bytes} > {exc.limit_bytes}",
                    ),
                ),
            ) from exc
    else:
        body = _require_dict_body()
        website_id_body = body.get("website_id")
        if not isinstance(website_id_body, str) or not website_id_body:
            raise ValidationError(
                "website_id is required",
                errors=(
                    _FieldError(
                        field="website_id", code="required",
                        message="required",
                    ),
                ),
            )
        website = get_db().get_website(website_id_body)
        if website is None or website.project_id != project_id:
            raise NotFoundError(
                f"website {website_id_body} not in project {project_id}"
            )

        source_url_raw = body.get("source_url")
        if not isinstance(source_url_raw, str) or not source_url_raw:
            raise ValidationError(
                "source_url is required when not uploading a file",
                errors=(
                    _FieldError(
                        field="source_url", code="required",
                        message="required",
                    ),
                ),
            )
        website_user_id_raw = body.get("website_user_id")
        website_user_id = (
            website_user_id_raw
            if isinstance(website_user_id_raw, str) and website_user_id_raw
            else None
        )

        try:
            pdf_bytes = asyncio.run(
                runner.fetch_pdf_from_url(
                    source_url_raw, website_user_id=website_user_id,
                )
            )
        except FetchFailed as exc:
            raise ValidationError(
                f"fetch failed: {exc.reason}",
                errors=(
                    _FieldError(
                        field="source_url", code="fetch_failed",
                        message=exc.reason,
                    ),
                ),
            ) from exc
        except PdfTooLarge as exc:
            raise ValidationError(
                f"remote PDF exceeds max size ({exc.size_bytes} > {exc.limit_bytes})",
                errors=(
                    _FieldError(
                        field="source_url", code="too_large",
                        message=f"{exc.size_bytes} > {exc.limit_bytes}",
                    ),
                ),
            ) from exc
        except NotAPdf as exc:
            raise ValidationError(
                "fetched bytes are not a PDF",
                errors=(
                    _FieldError(
                        field="source_url", code="invalid_value",
                        message=str(exc),
                    ),
                ),
            ) from exc

        derived_filename = (
            source_url_raw.rsplit("/", 1)[-1] or "document.pdf"
        )
        try:
            doc = asyncio.run(
                runner.create_or_find_pdf_document(
                    pdf_bytes,
                    website_id=website_id_body,
                    project_id=project_id,
                    source_type="manual_url",
                    discovered_from_page_id=None,
                    discovered_from_user_id=user_id_str,
                    original_filename=derived_filename,
                    source_url=source_url_raw,
                )
            )
        except NotAPdf as exc:
            raise ValidationError(
                "fetched bytes are not a PDF",
                errors=(
                    _FieldError(
                        field="source_url", code="invalid_value",
                        message=str(exc),
                    ),
                ),
            ) from exc
        except PdfTooLarge as exc:
            raise ValidationError(
                f"remote PDF exceeds max size ({exc.size_bytes} > {exc.limit_bytes})",
                errors=(
                    _FieldError(
                        field="source_url", code="too_large",
                        message=f"{exc.size_bytes} > {exc.limit_bytes}",
                    ),
                ),
            ) from exc

    # Dedup behaviour: ``create_or_find_pdf_document`` returns the
    # existing record on a (website_id, sha256) hit. We can't tell new
    # vs hit from the doc alone, so we infer: a freshly-created doc
    # has its ``discovered_at`` within the last second of "now". This
    # is sound because the legacy form returned the same redirect on
    # both paths — we just want clients to distinguish 200 (hit) from
    # 201 (created) when they care.
    # PdfDocument.discovered_at is non-Optional, so the comparison
    # alone is enough to infer "just created" vs dedup hit.
    is_new = (datetime.now() - doc.discovered_at).total_seconds() < 2.0
    response = jsonify(_serialize_pdf_document(doc))
    response.headers["Location"] = f"/api/v1/pdf-documents/{doc.id}"
    return response, (201 if is_new else 200)


@api_bp.route("/pdf-documents/<pdf_id>", methods=["GET"])
@api_endpoint
def get_pdf_document_rest(pdf_id: str) -> tuple[Response, int] | Response:
    """Read a PDF document's metadata.

    The PDF *bytes* and extracted images live behind separate endpoints
    that are deferred to a follow-up PR — this endpoint is metadata-only.
    """
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=pdf.project_id
    )
    return jsonify(_serialize_pdf_document(pdf))


@api_bp.route("/pdf-documents/<pdf_id>", methods=["DELETE"])
@api_endpoint
def delete_pdf_document_rest(pdf_id: str) -> tuple[Response, int]:
    """Delete a PDF document — DB record + filesystem artefacts.

    Mirrors the legacy /pdfs/<id>/delete cascade: the storage helper
    removes the per-PDF directory (PDF bytes, extracted images, cached
    pdfMax outputs) and then the DB record is dropped. ADMIN/AUDITOR
    only — CLIENT readers cannot tear down audit artefacts.
    """
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=pdf.project_id
    )
    _pdf_storage().delete(pdf)
    get_db().delete_pdf_document(pdf_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# PDF audit action endpoints — §5.9 of REST_API_ROADMAP.md.
#
# Replaces the legacy POST /pdfs/<id>/audit, GET /pdfs/<id>/audit-status,
# and POST /pdfs/<id>/cancel HTML/JSON-mix routes. The legacy ones stay
# alive on `pdf_bp` until the issue #21 frontend migration; these emit
# RFC 7807 errors and use the canonical /pdf-documents path.
#
# A PdfDocument can have *many* audit jobs over time. The
# ``/audits/latest`` segment selects which one the read/cancel apply
# to: most-recent ACTIVE job for that document (PENDING / RUNNING /
# CANCELLING), falling back to the absolute most-recent record so the
# UI can still surface "the last run" after it ends.
# ---------------------------------------------------------------------------


def _find_latest_pdf_audit_job(
    pdf_document_id: str, *, active_only: bool = False,
) -> dict[str, Any] | None:
    """Locate the most relevant PDF_AUDIT job for a document.

    When ``active_only`` is true, return only jobs in
    ``{PENDING, RUNNING, CANCELLING}``. Otherwise prefer active, fall
    back to absolute most-recent. Mirrors the legacy ``audit_status``
    handler's lookup so the new and old surfaces show the same job.
    """
    job_manager = JobManager.get_instance(get_db())
    active_statuses = [
        JobStatus.PENDING.value,
        JobStatus.RUNNING.value,
        JobStatus.CANCELLING.value,
    ]
    active_query: dict[str, Any] = {
        "job_type": JobType.PDF_AUDIT.value,
        "metadata.pdf_document_id": pdf_document_id,
        "status": {"$in": active_statuses},
    }
    active_doc = job_manager.collection.find_one(
        active_query, sort=[("created_at", -1)],
    )
    if active_doc is not None:
        return active_doc
    if active_only:
        return None
    return job_manager.collection.find_one(
        {
            "job_type": JobType.PDF_AUDIT.value,
            "metadata.pdf_document_id": pdf_document_id,
        },
        sort=[("created_at", -1)],
    )


def _serialize_pdf_audit_job(record: dict[str, Any]) -> dict[str, Any]:
    """Shape a PDF_AUDIT job document for the REST progress poll.

    Surfaces the same nested ``progress.{current,total,message,
    stage,fraction}`` shape the legacy ``audit_status`` produces so
    clients that move from the old URL only need to swap the path,
    not the parser. Reads progress fields via ``Any``-typed locals to
    avoid widening nested dict types.
    """
    progress_raw: Any = record.get("progress")
    progress_obj: dict[str, Any] = (
        cast(dict[str, Any], progress_raw)
        if isinstance(progress_raw, dict) else {}
    )
    details_raw: Any = progress_obj.get("details")
    details_obj: dict[str, Any] = (
        cast(dict[str, Any], details_raw)
        if isinstance(details_raw, dict) else {}
    )

    return {
        "job_id": record.get("job_id"),
        "status": record.get("status"),
        "created_at": _iso_or_none(record.get("created_at")),
        "completed_at": _iso_or_none(record.get("completed_at")),
        "progress": {
            "current": progress_obj.get("current"),
            "total": progress_obj.get("total"),
            "message": progress_obj.get("message"),
            "stage": details_obj.get("stage"),
            "fraction": details_obj.get("fraction"),
        },
    }


def _parse_pdf_audit_body(body: dict[str, Any]) -> dict[str, Any]:
    """Validate the audit-start body. Returns the kwargs for PdfAuditJob."""
    run_ai_raw = body.get("run_ai")
    run_ai: bool = run_ai_raw if isinstance(run_ai_raw, bool) else False

    wcag_level_raw = body.get("wcag_level", "AA")
    if not isinstance(wcag_level_raw, str) or wcag_level_raw not in ("AA", "AAA"):
        raise ValidationError(
            "wcag_level must be 'AA' or 'AAA'",
            errors=(
                _FieldError(
                    field="wcag_level", code="invalid_value",
                    message="must be 'AA' or 'AAA'",
                ),
            ),
        )

    locale_raw = body.get("locale", "en")
    if not isinstance(locale_raw, str):
        raise ValidationError(
            "locale must be a string",
            errors=(
                _FieldError(
                    field="locale", code="invalid_type", message="must be string"
                ),
            ),
        )

    return {
        "run_ai": run_ai,
        "wcag_level": wcag_level_raw,
        "locale": locale_raw,
    }


@api_bp.route("/pdf-documents/<pdf_id>/audits", methods=["POST"])
@api_endpoint
def start_pdf_audit(pdf_id: str) -> tuple[Response, int] | Response:
    """Queue a fresh audit for a PDF document.

    Body (all optional):

        {
          "run_ai":     bool,           // default false
          "wcag_level": "AA"|"AAA",     // default AA
          "locale":     "en"|"fr"|...   // default en
        }

    Returns 202 with the new ``job_id``. The audit runs in the
    background via :class:`auto_a11y.core.pdf_audit_job.PdfAuditJob`;
    poll ``GET /pdf-documents/<id>/audits/latest`` for progress.

    Errors:

    - **404** — pdf document does not exist
    - **409** — an audit is already in flight for this document (a
      second start would compete for the same on-disk artefacts)
    - **503** — the server has no ``PdfRunner`` configured (the audit
      pipeline is optional; deployments without the playwright/poppler
      stack run with ``pdf_runner=None``)
    """
    from auto_a11y.core.pdf_audit_job import PdfAuditJob
    from auto_a11y.web.typed_app import get_pdf_runner

    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=pdf.project_id
    )

    runner = get_pdf_runner()
    if runner is None:
        raise ConflictError(
            "PDF audit runner is not configured on this server"
        )

    if pdf.status == PdfDocumentStatus.AUDITING:
        raise ConflictError(
            f"pdf document {pdf_id} already has an audit in progress"
        )

    body = _require_dict_body() if request.data else {}
    kwargs = _parse_pdf_audit_body(body)

    user_id_str: str
    if current_user.is_authenticated:
        raw_uid: Any = current_user.get_id()
        user_id_str = str(raw_uid) if raw_uid is not None else "anonymous"
    else:
        user_id_str = "anonymous"

    job = PdfAuditJob(
        runner=runner,
        db=get_db(),
        pdf_document_id=pdf_id,
        run_ai=kwargs["run_ai"],
        ai_api_key=None,
        wcag_level=kwargs["wcag_level"],
        locale=kwargs["locale"],
        user_id=user_id_str,
    )
    job_id = job.start()

    return jsonify({
        "job_id": job_id,
        "pdf_document_id": pdf_id,
        "run_ai": kwargs["run_ai"],
        "wcag_level": kwargs["wcag_level"],
        "locale": kwargs["locale"],
        "status": "queued",
    }), 202


@api_bp.route(
    "/pdf-documents/<pdf_id>/audits/latest", methods=["GET"]
)
@api_endpoint
def get_latest_pdf_audit(pdf_id: str) -> tuple[Response, int] | Response:
    """Read the most-recent (or in-flight) audit's status + progress.

    Returns:

        {
          "pdf_document_id":    "...",
          "doc_status":         "auditing|audited|audit_failed|...",
          "error_reason":       null | "...",
          "last_audit_result_id": null | "...",
          "job":                null | {...job shape...}
        }

    ``job`` is ``null`` only when the document has *never* had an
    audit job recorded. After at least one run the latest job stays
    in the response so clients can render "last audit failed at X"
    even when no fresh job is in flight.
    """
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    job_doc = _find_latest_pdf_audit_job(pdf_id)
    job_payload: dict[str, Any] | None = (
        _serialize_pdf_audit_job(job_doc) if job_doc is not None else None
    )

    return jsonify({
        "pdf_document_id": pdf_id,
        "doc_status": pdf.status.value,
        "error_reason": pdf.error_reason,
        "last_audit_result_id": pdf.last_audit_result_id,
        "job": job_payload,
    })


@api_bp.route(
    "/pdf-documents/<pdf_id>/audits/latest/cancel", methods=["POST"]
)
@api_endpoint
def cancel_latest_pdf_audit(
    pdf_id: str,
) -> tuple[Response, int] | Response:
    """Request cancellation of every in-flight audit for this PDF.

    Iterates over every PDF_AUDIT job for this document in
    ``{PENDING, RUNNING, CANCELLING}`` and calls
    :meth:`JobManager.request_cancellation` on each. The worker reads
    the flag at its next checkpoint and exits cleanly.

    The PDF's ``status`` is forcibly reset to ``AUDIT_FAILED`` with
    ``error_reason='Audit cancelled by user'``. This unblocks the user
    even when no live job exists (e.g. the previous run crashed and
    left the document stuck in ``AUDITING``) — the regular start
    endpoint refuses to re-audit a document already in ``AUDITING``,
    so this manual reset is the escape hatch.

    Returns 202 with the new doc status and how many active jobs were
    flagged. A document with no live job that is also not stuck in
    AUDITING returns 409 — the cancel verb implies there's something
    to cancel.
    """
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=pdf.project_id
    )

    job_manager = JobManager.get_instance(get_db())

    user_id_str: str
    if current_user.is_authenticated:
        raw_uid: Any = current_user.get_id()
        user_id_str = str(raw_uid) if raw_uid is not None else "anonymous"
    else:
        user_id_str = "anonymous"

    active_statuses = [
        JobStatus.PENDING.value,
        JobStatus.RUNNING.value,
        JobStatus.CANCELLING.value,
    ]
    cancelled = 0
    for raw_doc in job_manager.collection.find({
        "job_type": JobType.PDF_AUDIT.value,
        "metadata.pdf_document_id": pdf_id,
        "status": {"$in": active_statuses},
    }):
        doc_any: Any = raw_doc
        job_id_value: Any = doc_any.get("job_id")
        if not isinstance(job_id_value, str):
            continue
        if job_manager.request_cancellation(
            job_id_value, requested_by=user_id_str
        ):
            cancelled += 1

    # 409 when nothing to cancel — but allow forcing through if the
    # document is stuck in AUDITING (the legacy escape hatch).
    if cancelled == 0 and pdf.status != PdfDocumentStatus.AUDITING:
        raise ConflictError(
            f"pdf document {pdf_id} has no audit in progress"
        )

    pdf.status = PdfDocumentStatus.AUDIT_FAILED
    pdf.error_reason = "Audit cancelled by user"
    get_db().update_pdf_document(pdf)

    return jsonify({
        "pdf_document_id": pdf_id,
        "cancellation_requested_count": cancelled,
        "doc_status": pdf.status.value,
    }), 202


# ---------------------------------------------------------------------------
# PDF artefact serving — §5.9 of REST_API_ROADMAP.md.
#
# Five binary/JSON read endpoints that close out the §5.9 cluster:
# stored PDF bytes, extracted images, derived export reports
# (Markdown/HTML), the cached pdfMax issue-map JSON, and the cached
# pdfMax accessibility markdown report. The legacy ``pdf_bp`` routes
# still serve the admin frontend until the issue #21 migration; these
# REST equivalents emit RFC 7807 errors instead of flash + redirect
# and use the canonical /pdf-documents path.
# ---------------------------------------------------------------------------


def _resolve_pdf_or_404(pdf_id: str) -> PdfDocument:
    pdf = get_db().get_pdf_document(pdf_id)
    if pdf is None:
        raise NotFoundError(f"pdf document {pdf_id} not found")
    return pdf


@api_bp.route("/pdf-documents/<pdf_id>/file", methods=["GET"])
@api_endpoint
def get_pdf_file(pdf_id: str) -> Response | tuple[Response, int]:
    """Stream the stored PDF bytes.

    Returns ``application/pdf`` with ``Content-Disposition: inline`` so
    clients can embed the file in an ``<iframe>`` or render with a
    PDF.js viewer. ``X-Frame-Options: SAMEORIGIN`` and a same-origin
    CSP mirror the legacy route's iframe-friendly defaults.

    ``download_name`` falls back to ``document.pdf`` when the source
    record has no ``original_filename``; for uploads we keep the
    user-supplied filename so the browser's "Save As" prefill is
    useful.
    """
    from flask import send_file

    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    pdf_path = _pdf_storage().local_path(pdf)
    if not pdf_path.exists():
        raise NotFoundError(f"pdf document {pdf_id} file no longer on disk")

    download_name = pdf.original_filename or "document.pdf"
    response = send_file(
        str(pdf_path),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=download_name,
    )
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    # Explicit CSP override so the global after_request hook (if any)
    # leaves this iframe-embed surface alone. Same value as the legacy
    # /pdfs/<id>/file route.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; frame-ancestors 'self'"
    )
    response.headers["Content-Disposition"] = (
        f'inline; filename="{download_name}"'
    )
    return response


@api_bp.route(
    "/pdf-documents/<pdf_id>/images/<image_name>", methods=["GET"]
)
@api_endpoint
def get_pdf_image(
    pdf_id: str, image_name: str,
) -> Response | tuple[Response, int]:
    """Stream one extracted image (e.g. ``page_001.png``).

    The audit pipeline rasterises each PDF page into the document's
    ``images_dir_for`` directory; this endpoint serves any of those
    bytes back. Path traversal guard rejects ``..``, ``/``, and ``\\``
    in the image name so the URL cannot escape the per-document image
    directory. Any extracted image is returned as ``image/png`` — the
    pipeline only writes PNGs.
    """
    from flask import send_file

    if ".." in image_name or "/" in image_name or "\\" in image_name:
        raise ValidationError(
            "image_name must not traverse directories",
            errors=(
                _FieldError(
                    field="image_name", code="invalid_value",
                    message="must not contain '..' or path separators",
                ),
            ),
        )

    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    image_path = _pdf_storage().images_dir_for(pdf) / image_name
    if not image_path.exists():
        raise NotFoundError(
            f"image {image_name} not found for pdf document {pdf_id}"
        )

    return send_file(str(image_path), mimetype="image/png")


_PDF_EXPORT_FORMATS: frozenset[str] = frozenset({"md", "html"})
_PDF_EXPORT_LOCALES: frozenset[str] = frozenset({"en", "fr"})


@api_bp.route(
    "/pdf-documents/<pdf_id>/export", methods=["GET"]
)
@api_endpoint
def export_pdf_audit(pdf_id: str) -> Response | tuple[Response, int]:
    """Stream a self-contained audit report (Markdown or HTML).

    Replaces the legacy ``/pdfs/<id>/export.<fmt>`` URL with a
    ``?format=<fmt>`` query string per the roadmap.

    Query:

    - ``format=md|html`` — required
    - ``locale=en|fr``   — optional, default ``en``; unrecognised
      values silently fall back to ``en``

    The report body is derived purely from the persisted
    :class:`TestResult` referenced by ``pdf.last_audit_result_id``, so
    an in-flight audit returns the *previous* report, not a partial
    one. When the document has never been audited the export is still
    served but contains the "no findings" stub the report builders
    emit for an empty result — clients can detect this via the empty
    ``finding`` list in the body rather than a separate 404 branch.

    ``Cache-Control: private, no-cache`` matches the legacy route —
    derived content is safe to cache locally but a shared cache could
    leak between users.
    """
    fmt_raw = request.args.get("format")
    if not isinstance(fmt_raw, str) or fmt_raw not in _PDF_EXPORT_FORMATS:
        raise ValidationError(
            "format must be 'md' or 'html'",
            errors=(
                _FieldError(
                    field="format", code="invalid_value",
                    message="must be 'md' or 'html'",
                ),
            ),
        )

    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    test_result = None
    if pdf.last_audit_result_id:
        test_result = get_db().get_test_result(pdf.last_audit_result_id)

    locale = request.args.get("locale", "en")
    if locale not in _PDF_EXPORT_LOCALES:
        locale = "en"

    # Local import — keeps Fluent-aware report_export off the import
    # graph for every PDF read until an export is actually requested.
    from auto_a11y.pdf.report_export import (
        build_html_report,
        build_markdown_report,
    )

    base_name = (
        pdf.original_filename.removesuffix(".pdf")
        if pdf.original_filename
        and pdf.original_filename.lower().endswith(".pdf")
        else (pdf.original_filename or "audit-report")
    )

    if fmt_raw == "md":
        body = build_markdown_report(pdf, test_result, locale=locale)
        mimetype = "text/markdown; charset=utf-8"
        filename = f"{base_name}_accessibility_report.md"
    else:
        body = build_html_report(pdf, test_result, locale=locale)
        mimetype = "text/html; charset=utf-8"
        filename = f"{base_name}_accessibility_report.html"

    response = Response(body.encode("utf-8"), mimetype=mimetype)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )
    response.headers["Cache-Control"] = "private, no-cache"
    return response


@api_bp.route(
    "/pdf-documents/<pdf_id>/issue-map", methods=["GET"]
)
@api_endpoint
def get_pdf_issue_map(pdf_id: str) -> Response | tuple[Response, int]:
    """Serve the cached pdfMax ``*_issue_map.json`` for viewer overlays.

    The pdfMax subprocess writes one issue-map JSON per audit run
    alongside the Markdown report. Viewer UIs fetch this to position
    issue overlays on each page and highlight the matching sidebar
    card.

    Returns 404 (with a Problem-Details body) when:

    - the document doesn't exist
    - the document's status isn't ``AUDITED`` (clearing test results
      flips status back to PENDING but the cache files may linger;
      gating on status avoids leaking stale overlays into a fresh UI)
    - no ``pdfmax-report`` cache directory exists
    - no ``*_issue_map.json`` file is inside it

    ``Cache-Control: private, max-age=60`` matches the legacy route —
    cheap to re-render within a session, never via a shared cache.
    """
    from flask import send_file

    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    if pdf.status is not PdfDocumentStatus.AUDITED:
        raise NotFoundError(
            f"pdf document {pdf_id} has no current audit issue-map"
        )

    pdf_path = _pdf_storage().local_path(pdf)
    cache_dir = pdf_path.parent / "pdfmax-report"
    if not cache_dir.is_dir():
        raise NotFoundError(
            f"pdf document {pdf_id} issue-map cache not present"
        )

    candidates = sorted(cache_dir.glob("*_issue_map.json"))
    if not candidates:
        raise NotFoundError(
            f"pdf document {pdf_id} issue-map cache file missing"
        )

    response = send_file(str(candidates[0]), mimetype="application/json")
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@api_bp.route(
    "/pdf-documents/<pdf_id>/reports/pdfmax", methods=["GET"]
)
@api_endpoint
def get_pdfmax_report(pdf_id: str) -> Response | tuple[Response, int]:
    """Return the cached pdfMax accessibility Markdown report.

    Pure cache lookup — the pdfMax subprocess only runs as part of
    the audit job (:class:`PdfAuditJob`), so this endpoint never
    blocks on the 10-30s audit pipeline.

    Status is the gate: only ``AUDITED`` documents return content.
    PENDING / FETCHING / AUDITING / FETCH_FAILED / AUDIT_FAILED all
    return 404 even when stale cache files happen to exist on disk —
    keeps the response semantically aligned with what the audit
    pipeline considers "current".

    Body is ``text/markdown; charset=utf-8`` (the raw report text).
    HTML rendering of the markdown is the legacy ``pdf_bp`` route's
    job; this REST surface returns the source.
    """
    pdf = _resolve_pdf_or_404(pdf_id)
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=pdf.project_id,
    )

    if pdf.status is not PdfDocumentStatus.AUDITED:
        raise NotFoundError(
            f"pdf document {pdf_id} has no current pdfmax report"
        )

    pdf_path = _pdf_storage().local_path(pdf)
    cache_dir = pdf_path.parent / "pdfmax-report"
    if not cache_dir.is_dir():
        raise NotFoundError(
            f"pdf document {pdf_id} pdfmax report cache not present"
        )

    candidates = sorted(cache_dir.glob("*_accessibility_report.md"))
    if not candidates:
        raise NotFoundError(
            f"pdf document {pdf_id} pdfmax report cache file missing"
        )

    try:
        text = candidates[0].read_text(encoding="utf-8")
    except OSError as exc:
        raise NotFoundError(
            f"failed to read pdfmax report: {exc}"
        ) from exc

    response = Response(text.encode("utf-8"), mimetype="text/markdown; charset=utf-8")
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


# ---------------------------------------------------------------------------
# Permission groups (REST shape — uses the @api_endpoint scaffolding).
#
# Sits alongside the existing groups_bp HTML routes at /groups/* (still
# serving the admin frontend). Per docs/REST_API_ROADMAP.md §5.11.
# Members consolidation (members.py / project_users.py /
# project_participants.py / website_users.py) is a separate follow-up.
# ---------------------------------------------------------------------------

from auto_a11y.models.permission_group import (  # noqa: E402
    PERMISSION_LEVELS,
    PermissionGroup,
    RESOURCE_NOUNS,
)
from auto_a11y.web.api import require_global_permission  # noqa: E402

_RESOURCE_NOUNS_SET: frozenset[str] = frozenset(RESOURCE_NOUNS)
_PERMISSION_LEVEL_NAMES: frozenset[str] = frozenset(PERMISSION_LEVELS.keys())


def _serialize_permission_group(group: PermissionGroup) -> dict[str, Any]:
    """Project a :class:`PermissionGroup` to a JSON-safe dict."""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "permissions": dict(group.permissions),
        "is_system": group.is_system,
        "created_at": _iso(group.created_at),
        "updated_at": _iso(group.updated_at),
    }


def _validate_permissions_dict(raw: Any, *, field: str) -> dict[str, str]:
    """Validate a permissions dict against the ``RESOURCE_NOUNS`` and
    ``PERMISSION_LEVELS`` enums.

    Unknown resource nouns and unknown permission levels are both 400s
    with structured field errors.  Missing resource nouns default to
    ``'none'`` so callers can send a partial dict (only the resources
    they want to grant something on).
    """
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    field_errors: list[_FieldError] = []
    for key, value in raw_dict.items():
        if key not in _RESOURCE_NOUNS_SET:
            field_errors.append(_FieldError(
                field=f"{field}.{key}", code="unknown_resource",
                message=f"not a known resource noun (allowed: {sorted(_RESOURCE_NOUNS_SET)})",
            ))
            continue
        if not isinstance(value, str) or value not in _PERMISSION_LEVEL_NAMES:
            field_errors.append(_FieldError(
                field=f"{field}.{key}", code="invalid_value",
                message=f"not a recognized permission level (allowed: {sorted(_PERMISSION_LEVEL_NAMES)})",
            ))
            continue
    if field_errors:
        raise ValidationError(
            f"{field} contains invalid entries", errors=tuple(field_errors)
        )

    permissions: dict[str, str] = {r: "none" for r in RESOURCE_NOUNS}
    for key, value in raw_dict.items():
        if isinstance(value, str):
            permissions[key] = value
    return permissions


def _validate_group_name_unique(name: str, *, exclude_id: str | None = None) -> None:
    existing = get_db().get_group_by_name(name)
    if existing is not None and existing.id != exclude_id:
        raise ConflictError(f"group name {name!r} is already in use")


def _build_group_from_body(body: dict[str, Any]) -> PermissionGroup:
    name_raw = body.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        raise ValidationError(
            "name is required",
            errors=(_FieldError(field="name", code="required", message="required"),),
        )
    name = name_raw.strip()
    description_raw = body.get("description", "")
    if not isinstance(description_raw, str):
        raise ValidationError(
            "description must be a string",
            errors=(_FieldError(field="description", code="invalid_type", message="must be string"),),
        )
    permissions = (
        _validate_permissions_dict(body["permissions"], field="permissions")
        if "permissions" in body
        else {r: "none" for r in RESOURCE_NOUNS}
    )
    return PermissionGroup(
        name=name,
        description=description_raw,
        permissions=permissions,
    )


def _apply_patch_to_group(group: PermissionGroup, body: dict[str, Any]) -> PermissionGroup:
    """Apply only the keys present in ``body`` to ``group``.

    ``is_system`` is intentionally not patchable — that flag protects
    the seeded default groups from deletion, and clients shouldn't be
    able to flip it on/off.
    """
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise ValidationError(
                "name must be a non-empty string",
                errors=(_FieldError(field="name", code="invalid_value", message="must be non-empty string"),),
            )
        group.name = body["name"].strip()
    if "description" in body:
        desc = body["description"]
        if not isinstance(desc, str):
            raise ValidationError(
                "description must be a string",
                errors=(_FieldError(field="description", code="invalid_type", message="must be string"),),
            )
        group.description = desc
    if "permissions" in body:
        group.permissions = _validate_permissions_dict(body["permissions"], field="permissions")
    group.updated_at = datetime.now()
    return group


@api_bp.route("/groups", methods=["GET"])
@api_endpoint
def list_groups_rest() -> tuple[Response, int] | Response:
    """List all permission groups with cursor pagination.

    Defaults to the smallest sensible page; the underlying collection
    is bounded (a handful of system groups + custom additions), so the
    response shape with ``next_cursor`` is preserved for forward
    compatibility even though most deployments will fit on one page.
    """
    require_global_permission("groups", "read")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().groups.find(query).sort("_id", -1).limit(limit + 1))
    groups = [PermissionGroup.from_dict(doc) for doc in docs]
    page = paginate(
        groups, limit=limit, get_id=lambda g: str(g.mongo_id) if g.mongo_id else ""
    )
    return jsonify(
        {
            "items": [_serialize_permission_group(g) for g in page["items"]],
            "next_cursor": page["next_cursor"],
        }
    )


@api_bp.route("/groups", methods=["POST"])
@api_endpoint
def create_group_rest() -> tuple[Response, int]:
    """Create a permission group."""
    require_global_permission("groups", "create")
    body = _require_dict_body()
    group = _build_group_from_body(body)
    _validate_group_name_unique(group.name)
    group_id = get_db().create_group(group)
    refreshed = get_db().get_group(group_id)
    if refreshed is None:
        raise ConflictError("group failed to persist")
    response = jsonify(_serialize_permission_group(refreshed))
    response.headers["Location"] = f"/api/v1/groups/{group_id}"
    return response, 201


@api_bp.route("/groups/<group_id>", methods=["GET"])
@api_endpoint
def get_group_rest(group_id: str) -> tuple[Response, int] | Response:
    """Get a permission group by id."""
    require_global_permission("groups", "read")
    group = get_db().get_group(group_id)
    if group is None:
        raise NotFoundError(f"group {group_id} not found")
    return jsonify(_serialize_permission_group(group))


@api_bp.route("/groups/<group_id>", methods=["PUT"])
@api_endpoint
def replace_group_rest(group_id: str) -> tuple[Response, int] | Response:
    """Full replace of a group's editable fields.

    ``is_system`` and ``created_at`` are preserved; the ``updated_at``
    timestamp is bumped.
    """
    require_global_permission("groups", "update")
    existing = get_db().get_group(group_id)
    if existing is None:
        raise NotFoundError(f"group {group_id} not found")
    body = _require_dict_body()
    replaced = _build_group_from_body(body)
    _validate_group_name_unique(replaced.name, exclude_id=group_id)
    replaced.mongo_id = existing.mongo_id
    replaced.is_system = existing.is_system
    replaced.created_at = existing.created_at
    replaced.updated_at = datetime.now()
    if not get_db().update_group(replaced):
        raise ConflictError("group could not be updated")
    return jsonify(_serialize_permission_group(replaced))


@api_bp.route("/groups/<group_id>", methods=["PATCH"])
@api_endpoint
def patch_group_rest(group_id: str) -> tuple[Response, int] | Response:
    """Partial update — only fields present in the request body are changed."""
    require_global_permission("groups", "update")
    group = get_db().get_group(group_id)
    if group is None:
        raise NotFoundError(f"group {group_id} not found")
    body = _require_dict_body()
    if "name" in body and isinstance(body["name"], str):
        _validate_group_name_unique(body["name"].strip(), exclude_id=group_id)
    patched = _apply_patch_to_group(group, body)
    if not get_db().update_group(patched):
        raise ConflictError("group could not be updated")
    return jsonify(_serialize_permission_group(patched))


@api_bp.route("/groups/<group_id>", methods=["DELETE"])
@api_endpoint
def delete_group_rest(group_id: str) -> tuple[Response, int]:
    """Delete a non-system group.

    System groups (``is_system=True``) are the seeded default groups
    (Admin, Auditor, Client). Removing them would orphan every
    project_member.group_ids reference, so the legacy form blocks it
    and the REST endpoint mirrors that with a 409.
    """
    require_global_permission("groups", "delete")
    group = get_db().get_group(group_id)
    if group is None:
        raise NotFoundError(f"group {group_id} not found")
    if group.is_system:
        raise ConflictError(
            f"group {group_id} is a system group and cannot be deleted"
        )
    get_db().delete_group(group_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Users + project/website members and test users (REST shape).
#
# Per docs/REST_API_ROADMAP.md §5.11. The roadmap recommends folding
# five overlapping legacy blueprints — members.py, project_users.py,
# project_participants.py, website_users.py, plus the user-search bit
# of members — into three top-level surfaces:
#   - /api/v1/users (system-user search and "me" lookup)
#   - /api/v1/projects/<id>/members (platform access control;
#     ProjectMember[user_id, group_ids[]])
#   - /api/v1/websites/<id>/members
#
# We diverge slightly from the roadmap on naming for the test users:
# the legacy ProjectUser and WebsiteUser models are credentials for
# logging into sites *under* test (login automation), not platform
# membership, so calling them "members" would be misleading.  They
# live at /api/v1/projects/<id>/test-users and
# /api/v1/websites/<id>/test-users with a
# /api/v1/project-test-users/<id> + /api/v1/website-test-users/<id>
# single-resource surface.
#
# Out of scope, deferred to a follow-up:
#   - Lived-experience testers and supervisors (§5.11 also mentions
#     project_participants.py — they're inline arrays on the Project
#     document and need a separate endpoint design)
#   - The legacy POST .../toggle endpoint — subsumed by PATCH with
#     {enabled: false} on the test-user resources
#   - Test-login automation action endpoints (POST .../test-login) —
#     they share async-job mechanics with the broader test-runs PR
# ---------------------------------------------------------------------------

from auto_a11y.models.app_user import AppUser  # noqa: E402
from auto_a11y.models.project_member import ProjectMember  # noqa: E402
from auto_a11y.models.project_user import ProjectUser  # noqa: E402
from auto_a11y.models.website_user import WebsiteUser  # noqa: E402

# Allowed values are duplicated between the project_user.AuthenticationMethod
# and website_user.AuthenticationMethod enums — they have identical
# definitions but distinct types. We validate against the string values and
# let each model's ``from_dict`` reconstruct the right enum on its side.
_AUTH_METHOD_VALUES: frozenset[str] = frozenset(
    {"form_login", "basic_auth", "oauth", "sso"}
)


def _serialize_app_user_search_hit(user: AppUser) -> dict[str, Any]:
    """Project an :class:`AppUser` to the search-result shape.

    Deliberately narrow — this endpoint exists for picking a user when
    adding a project member, so it surfaces the email, display name,
    and id and nothing else (no password hash, no SSO id, no
    last_login). The full AppUser surface is out of scope for #27.
    """
    return {
        "user_id": user.id,
        "email": user.email,
        "display_name": user.display_name,
    }


def _serialize_login_config_via_model(config: Any) -> dict[str, Any]:
    """Project either model's LoginConfig to JSON.

    Both ProjectUser and WebsiteUser have their own LoginConfig class;
    they share an identical to_dict() shape, so we delegate to it
    instead of binding to one specific class.
    """
    raw: dict[str, Any] = config.to_dict()
    return {
        "authentication_method": raw["authentication_method"],
        "login_url": raw["login_url"],
        "username_field_selector": raw["username_field_selector"],
        "password_field_selector": raw["password_field_selector"],
        "submit_button_selector": raw["submit_button_selector"],
        "success_indicator_selector": raw["success_indicator_selector"],
        "logout_url": raw["logout_url"],
        "logout_button_selector": raw["logout_button_selector"],
        "logout_success_indicator_selector": raw["logout_success_indicator_selector"],
        "additional_steps": list(raw["additional_steps"]),
        "session_timeout_minutes": raw["session_timeout_minutes"],
    }


def _serialize_project_test_user(user: ProjectUser) -> dict[str, Any]:
    """Project a :class:`ProjectUser` to a JSON-safe dict.

    The raw ``password`` is intentionally not included — it's a
    test-credentials secret used by the login automation and the API
    surfaces only a ``password_set`` boolean. PATCHing with a blank
    password preserves the existing one, mirroring the admin-settings
    secret-handling rule.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": user.id,
        "project_id": user.project_id,
        "username": user.username,
        "password_set": bool(user.password),
        "display_name": user.display_name,
        "roles": list(user.roles),
        "description": user.description,
        "login_config": _serialize_login_config_via_model(user.login_config),
        "enabled": user.enabled,
        "last_used": _iso(user.last_used),
        "last_login_success": user.last_login_success,
        "last_login_error": user.last_login_error,
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }


def _serialize_website_test_user(user: WebsiteUser) -> dict[str, Any]:
    """Same shape as project test user, with ``website_id`` instead of ``project_id``."""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return {
        "id": user.id,
        "website_id": user.website_id,
        "username": user.username,
        "password_set": bool(user.password),
        "display_name": user.display_name,
        "roles": list(user.roles),
        "description": user.description,
        "login_config": _serialize_login_config_via_model(user.login_config),
        "enabled": user.enabled,
        "last_used": _iso(user.last_used),
        "last_login_success": user.last_login_success,
        "last_login_error": user.last_login_error,
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }


def _parse_login_config_dict(raw: Any, *, field: str) -> dict[str, Any]:
    """Validate a JSON ``login_config`` object and return a normalized dict.

    Returning a dict (rather than a model instance) lets each test-user
    model — ``ProjectUser`` and ``WebsiteUser`` each ship their own
    ``LoginConfig`` class — do its own ``LoginConfig.from_dict()``
    reconstruction without forcing the validator to know which one.

    Validates ``authentication_method`` against the shared set of
    allowed string values, types optional fields, and rejects
    ``additional_steps`` entries that aren't objects.
    """
    if not isinstance(raw, dict):
        raise ValidationError(
            f"{field} must be an object",
            errors=(_FieldError(field=field, code="invalid_type", message="must be object"),),
        )
    raw_dict = cast(dict[str, Any], raw)
    auth_method_raw = raw_dict.get("authentication_method", "form_login")
    if not isinstance(auth_method_raw, str) or auth_method_raw not in _AUTH_METHOD_VALUES:
        raise ValidationError(
            f"{field}.authentication_method is not recognized",
            errors=(_FieldError(
                field=f"{field}.authentication_method", code="invalid_value",
                message=f"must be one of {sorted(_AUTH_METHOD_VALUES)}"),),
        )

    def _opt_str(key: str) -> str | None:
        value = raw_dict.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationError(
                f"{field}.{key} must be a string or null",
                errors=(_FieldError(field=f"{field}.{key}", code="invalid_type", message="must be string"),),
            )
        return value

    additional_steps_raw: Any = raw_dict.get("additional_steps", [])
    if not isinstance(additional_steps_raw, list):
        raise ValidationError(
            f"{field}.additional_steps must be an array",
            errors=(_FieldError(field=f"{field}.additional_steps", code="invalid_type", message="must be array"),),
        )
    additional_steps: list[dict[str, Any]] = []
    for index, step in enumerate(_iter_to_any_list(additional_steps_raw)):
        if not isinstance(step, dict):
            raise ValidationError(
                f"{field}.additional_steps[{index}] must be an object",
                errors=(_FieldError(field=f"{field}.additional_steps[{index}]", code="invalid_type", message="must be object"),),
            )
        additional_steps.append(cast(dict[str, Any], step))

    session_timeout_raw: Any = raw_dict.get("session_timeout_minutes", 30)
    if isinstance(session_timeout_raw, bool) or not isinstance(session_timeout_raw, int):
        raise ValidationError(
            f"{field}.session_timeout_minutes must be an integer",
            errors=(_FieldError(field=f"{field}.session_timeout_minutes", code="invalid_type", message="must be integer"),),
        )

    return {
        "authentication_method": auth_method_raw,
        "login_url": _opt_str("login_url"),
        "username_field_selector": _opt_str("username_field_selector"),
        "password_field_selector": _opt_str("password_field_selector"),
        "submit_button_selector": _opt_str("submit_button_selector"),
        "success_indicator_selector": _opt_str("success_indicator_selector"),
        "logout_url": _opt_str("logout_url"),
        "logout_button_selector": _opt_str("logout_button_selector"),
        "logout_success_indicator_selector": _opt_str("logout_success_indicator_selector"),
        "additional_steps": additional_steps,
        "session_timeout_minutes": int(session_timeout_raw),
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@api_bp.route("/users/me", methods=["GET"])
@api_endpoint
def get_current_user() -> tuple[Response, int] | Response:
    """Return basic info about the currently-authenticated user.

    Useful for SPAs that need to know who they're logged in as without
    rolling their own session-introspection endpoint. Surface is
    intentionally minimal — full AppUser CRUD is out of scope (auth
    flows are owned by the legacy auth.py blueprint per roadmap §5.13).
    """
    user = require_authenticated()
    user_id = getattr(user, "get_id", lambda: None)() or getattr(user, "id", None)
    return jsonify({
        "user_id": str(user_id) if user_id is not None else None,
        "email": getattr(user, "email", None),
        "display_name": getattr(user, "display_name", None),
        "is_superadmin": bool(getattr(user, "is_superadmin", False)),
    })


@api_bp.route("/users/search", methods=["GET"])
@api_endpoint
def search_users_rest() -> tuple[Response, int] | Response:
    """Search active app users by email/display name.

    Replaces the legacy ``GET /members/api/search-users``. Results are
    capped at 20 entries server-side. The ``exclude_project`` query
    param strips out users who are already members of that project,
    which is the standard usage when populating a "add member"
    autocomplete.
    """
    require_authenticated()
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"users": []})

    limit_raw = request.args.get("limit", "10")
    try:
        limit = min(max(int(limit_raw), 1), 20)
    except ValueError as exc:
        raise ValidationError(
            "limit must be an integer",
            errors=(_FieldError(field="limit", code="invalid_type", message=str(exc)),),
        ) from exc

    exclude_user_ids: list[str] = []
    exclude_project = (request.args.get("exclude_project") or "").strip()
    if exclude_project:
        project = get_db().get_project(exclude_project)
        if project is not None:
            exclude_user_ids = [m.user_id for m in project.members]

    users = get_db().search_app_users(
        query=q,
        exclude_user_ids=exclude_user_ids or None,
        limit=limit,
    )
    return jsonify({"users": [_serialize_app_user_search_hit(u) for u in users]})


# ---------------------------------------------------------------------------
# Project members (platform access control — ProjectMember[user_id, group_ids[]])
# ---------------------------------------------------------------------------


def _serialize_project_member(member: ProjectMember, *, user: AppUser | None) -> dict[str, Any]:
    return {
        "user_id": member.user_id,
        "email": user.email if user is not None else None,
        "display_name": user.display_name if user is not None else None,
        "group_ids": list(member.group_ids),
    }


def _validate_group_ids_body(body: dict[str, Any], *, field: str = "group_ids") -> list[str]:
    raw = body.get(field)
    if not isinstance(raw, list) or not raw:
        raise ValidationError(
            f"{field} must be a non-empty array",
            errors=(_FieldError(field=field, code="required", message="must be non-empty array"),),
        )
    return _coerce_str_list(raw)


@api_bp.route("/projects/<project_id>/members", methods=["GET"])
@api_endpoint
def list_project_members_rest(project_id: str) -> tuple[Response, int] | Response:
    """List the platform members of a project + the available groups.

    Returns each member with their email + display name + ``group_ids``,
    plus an ``available_groups`` array (id + name) so a UI can render
    a group picker without a second round-trip.
    """
    require_global_permission("project_members", "read")
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    members: list[dict[str, Any]] = []
    for member in project.members:
        user = get_db().get_app_user(member.user_id)
        members.append(_serialize_project_member(member, user=user))

    available_groups = [
        {"id": g.id, "name": g.name, "is_system": g.is_system}
        for g in get_db().get_all_groups()
    ]
    return jsonify({"members": members, "available_groups": available_groups})


@api_bp.route("/projects/<project_id>/members", methods=["POST"])
@api_endpoint
def add_project_member_rest(project_id: str) -> tuple[Response, int]:
    """Add a member to a project."""
    require_global_permission("project_members", "create")
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    user_id_raw = body.get("user_id")
    if not isinstance(user_id_raw, str) or not user_id_raw.strip():
        raise ValidationError(
            "user_id is required",
            errors=(_FieldError(field="user_id", code="required", message="required"),),
        )
    user_id = user_id_raw.strip()

    user = get_db().get_app_user(user_id)
    if user is None:
        raise NotFoundError(f"user {user_id} not found")

    if any(m.user_id == user_id for m in project.members):
        raise ConflictError(f"user {user_id} is already a member of project {project_id}")

    group_ids = _validate_group_ids_body(body)
    get_db().add_project_member(project_id, user_id, group_ids)

    refreshed = get_db().get_project(project_id)
    if refreshed is None:
        raise ConflictError("project disappeared after member-add")
    member = next((m for m in refreshed.members if m.user_id == user_id), None)
    if member is None:
        raise ConflictError("member-add did not persist")
    response = jsonify(_serialize_project_member(member, user=user))
    response.headers["Location"] = f"/api/v1/projects/{project_id}/members/{user_id}"
    return response, 201


@api_bp.route("/projects/<project_id>/members/<user_id>", methods=["GET"])
@api_endpoint
def get_project_member_rest(project_id: str, user_id: str) -> tuple[Response, int] | Response:
    """Get one project member."""
    require_global_permission("project_members", "read")
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    member = next((m for m in project.members if m.user_id == user_id), None)
    if member is None:
        raise NotFoundError(f"user {user_id} is not a member of project {project_id}")
    user = get_db().get_app_user(user_id)
    return jsonify(_serialize_project_member(member, user=user))


@api_bp.route("/projects/<project_id>/members/<user_id>", methods=["PUT"])
@api_endpoint
def update_project_member_rest(
    project_id: str, user_id: str
) -> tuple[Response, int] | Response:
    """Replace a member's group assignments."""
    require_global_permission("project_members", "update")
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    if not any(m.user_id == user_id for m in project.members):
        raise NotFoundError(f"user {user_id} is not a member of project {project_id}")

    body = _require_dict_body()
    group_ids = _validate_group_ids_body(body)
    if not get_db().update_project_member_groups(project_id, user_id, group_ids):
        raise ConflictError("member group update did not modify any document")

    refreshed = get_db().get_project(project_id)
    if refreshed is None:
        raise ConflictError("project disappeared after member-update")
    member = next((m for m in refreshed.members if m.user_id == user_id), None)
    if member is None:
        raise ConflictError("member disappeared after update")
    user = get_db().get_app_user(user_id)
    return jsonify(_serialize_project_member(member, user=user))


@api_bp.route("/projects/<project_id>/members/<user_id>", methods=["DELETE"])
@api_endpoint
def remove_project_member_rest(
    project_id: str, user_id: str
) -> tuple[Response, int]:
    """Remove a member from a project. Self-removal is rejected (400)."""
    require_global_permission("project_members", "delete")
    if user_id == str(current_user.get_id()):
        raise ValidationError(
            "cannot remove yourself from a project",
            errors=(_FieldError(field="user_id", code="self_removal", message="self-removal is not allowed"),),
        )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    if not any(m.user_id == user_id for m in project.members):
        raise NotFoundError(f"user {user_id} is not a member of project {project_id}")
    get_db().remove_project_member(project_id, user_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Test users — login automation credentials for sites under test
# ---------------------------------------------------------------------------


def _build_project_test_user_from_body(project_id: str, body: dict[str, Any]) -> ProjectUser:
    from auto_a11y.models.project_user import LoginConfig as ProjectLoginConfig

    username_raw = body.get("username")
    if not isinstance(username_raw, str) or not username_raw.strip():
        raise ValidationError(
            "username is required",
            errors=(_FieldError(field="username", code="required", message="required"),),
        )
    password_raw = body.get("password")
    if not isinstance(password_raw, str) or not password_raw:
        raise ValidationError(
            "password is required on create",
            errors=(_FieldError(field="password", code="required", message="required"),),
        )
    login_config_dict = (
        _parse_login_config_dict(body["login_config"], field="login_config")
        if "login_config" in body and body["login_config"] is not None
        else None
    )
    return ProjectUser(
        project_id=project_id,
        username=username_raw.strip(),
        password=password_raw,
        display_name=_optional_str(body.get("display_name"), field="display_name"),
        roles=_coerce_str_list(body["roles"]) if isinstance(body.get("roles"), list) else [],
        description=_optional_str(body.get("description"), field="description"),
        login_config=ProjectLoginConfig.from_dict(login_config_dict) if login_config_dict else ProjectLoginConfig(),
        enabled=bool(body.get("enabled", True)),
    )


def _build_website_test_user_from_body(website_id: str, body: dict[str, Any]) -> WebsiteUser:
    from auto_a11y.models.website_user import LoginConfig as WebsiteLoginConfig

    username_raw = body.get("username")
    if not isinstance(username_raw, str) or not username_raw.strip():
        raise ValidationError(
            "username is required",
            errors=(_FieldError(field="username", code="required", message="required"),),
        )
    password_raw = body.get("password")
    if not isinstance(password_raw, str) or not password_raw:
        raise ValidationError(
            "password is required on create",
            errors=(_FieldError(field="password", code="required", message="required"),),
        )
    login_config_dict = (
        _parse_login_config_dict(body["login_config"], field="login_config")
        if "login_config" in body and body["login_config"] is not None
        else None
    )
    return WebsiteUser(
        website_id=website_id,
        username=username_raw.strip(),
        password=password_raw,
        display_name=_optional_str(body.get("display_name"), field="display_name"),
        roles=_coerce_str_list(body["roles"]) if isinstance(body.get("roles"), list) else [],
        description=_optional_str(body.get("description"), field="description"),
        login_config=WebsiteLoginConfig.from_dict(login_config_dict) if login_config_dict else WebsiteLoginConfig(),
        enabled=bool(body.get("enabled", True)),
    )


def _optional_str(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(
            f"{field} must be a string or null",
            errors=(_FieldError(field=field, code="invalid_type", message="must be string"),),
        )
    return value if value else None


def _apply_patch_to_test_user(
    user: ProjectUser | WebsiteUser, body: dict[str, Any]
) -> None:
    """Apply a partial update to a test-user model in place.

    Same shape for ProjectUser and WebsiteUser — the only difference
    between them is the parent-id field, which is locked.

    A blank or omitted password preserves the existing value (mirrors
    the admin-settings rule). Setting password to a non-empty string
    rotates it.
    """
    if "username" in body:
        if not isinstance(body["username"], str) or not body["username"].strip():
            raise ValidationError(
                "username must be a non-empty string",
                errors=(_FieldError(field="username", code="invalid_value", message="must be non-empty"),),
            )
        user.username = body["username"].strip()
    if "password" in body:
        password = body["password"]
        if password is None or password == "":
            pass  # preserve existing
        elif not isinstance(password, str):
            raise ValidationError(
                "password must be a string",
                errors=(_FieldError(field="password", code="invalid_type", message="must be string"),),
            )
        else:
            user.password = password
    if "display_name" in body:
        user.display_name = _optional_str(body["display_name"], field="display_name")
    if "description" in body:
        user.description = _optional_str(body["description"], field="description")
    if "roles" in body:
        if not isinstance(body["roles"], list):
            raise ValidationError(
                "roles must be an array",
                errors=(_FieldError(field="roles", code="invalid_type", message="must be array"),),
            )
        user.roles = _coerce_str_list(body["roles"])
    if "login_config" in body:
        login_config_dict = (
            None
            if body["login_config"] is None
            else _parse_login_config_dict(body["login_config"], field="login_config")
        )
        # Each model carries its own LoginConfig class — reach into the
        # right one based on the runtime type.
        if isinstance(user, ProjectUser):
            from auto_a11y.models.project_user import LoginConfig as ProjectLoginConfig
            user.login_config = (
                ProjectLoginConfig.from_dict(login_config_dict) if login_config_dict
                else ProjectLoginConfig()
            )
        else:
            from auto_a11y.models.website_user import LoginConfig as WebsiteLoginConfig
            user.login_config = (
                WebsiteLoginConfig.from_dict(login_config_dict) if login_config_dict
                else WebsiteLoginConfig()
            )
    if "enabled" in body:
        user.enabled = bool(body["enabled"])
    user.update_timestamp()


# --- Project-scoped test users -------------------------------------------------


@api_bp.route("/projects/<project_id>/test-users", methods=["GET"])
@api_endpoint
def list_project_test_users_rest(project_id: str) -> tuple[Response, int] | Response:
    """List test users (login credentials) for a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    users = get_db().get_project_users(project_id)
    return jsonify({"items": [_serialize_project_test_user(u) for u in users]})


@api_bp.route("/projects/<project_id>/test-users", methods=["POST"])
@api_endpoint
def create_project_test_user_rest(project_id: str) -> tuple[Response, int]:
    """Create a test user under a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    user = _build_project_test_user_from_body(project_id, body)

    if get_db().get_project_user_by_username(project_id, user.username) is not None:
        raise ConflictError(f"username {user.username!r} is already in use in this project")

    user_id = get_db().create_project_user(user)
    refreshed = get_db().get_project_user(user_id)
    if refreshed is None:
        raise ConflictError("project test user failed to persist")
    response = jsonify(_serialize_project_test_user(refreshed))
    response.headers["Location"] = f"/api/v1/project-test-users/{user_id}"
    return response, 201


@api_bp.route("/project-test-users/<user_id>", methods=["GET"])
@api_endpoint
def get_project_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Get one project test user."""
    user = get_db().get_project_user(user_id)
    if user is None:
        raise NotFoundError(f"project test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=user.project_id
    )
    return jsonify(_serialize_project_test_user(user))


@api_bp.route("/project-test-users/<user_id>", methods=["PUT"])
@api_endpoint
def replace_project_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Full replace of a project test user's editable fields.

    project_id and metadata (last_used, last_login_*) are preserved;
    blank password preserves the existing one.
    """
    existing = get_db().get_project_user(user_id)
    if existing is None:
        raise NotFoundError(f"project test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=existing.project_id
    )
    body = _require_dict_body()
    # PUT body needs a password unless the existing record has one we
    # can preserve (admin-settings rule).
    if "password" not in body or not isinstance(body.get("password"), str) or not body["password"]:
        body["password"] = existing.password
    replaced = _build_project_test_user_from_body(existing.project_id, body)
    if (
        replaced.username != existing.username
        and get_db().get_project_user_by_username(existing.project_id, replaced.username) is not None
    ):
        raise ConflictError(f"username {replaced.username!r} is already in use in this project")
    replaced.mongo_id = existing.mongo_id
    replaced.created_at = existing.created_at
    replaced.last_used = existing.last_used
    replaced.last_login_success = existing.last_login_success
    replaced.last_login_error = existing.last_login_error
    replaced.update_timestamp()
    if not get_db().update_project_user(replaced):
        raise ConflictError("project test user could not be updated")
    return jsonify(_serialize_project_test_user(replaced))


@api_bp.route("/project-test-users/<user_id>", methods=["PATCH"])
@api_endpoint
def patch_project_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Partial update — covers the legacy enable/disable toggle."""
    user = get_db().get_project_user(user_id)
    if user is None:
        raise NotFoundError(f"project test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=user.project_id
    )
    body = _require_dict_body()
    if "username" in body and isinstance(body["username"], str):
        new_username = body["username"].strip()
        if new_username != user.username:
            if get_db().get_project_user_by_username(user.project_id, new_username) is not None:
                raise ConflictError(f"username {new_username!r} is already in use in this project")
    _apply_patch_to_test_user(user, body)
    if not get_db().update_project_user(user):
        raise ConflictError("project test user could not be updated")
    return jsonify(_serialize_project_test_user(user))


@api_bp.route("/project-test-users/<user_id>", methods=["DELETE"])
@api_endpoint
def delete_project_test_user_rest(user_id: str) -> tuple[Response, int]:
    """Delete a project test user."""
    user = get_db().get_project_user(user_id)
    if user is None:
        raise NotFoundError(f"project test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=user.project_id
    )
    get_db().delete_project_user(user_id)
    return Response(status=204), 204


# --- Test-login action endpoints (§5.11) -------------------------------------
#
# Synchronous — run the login automation against the live site and
# return the result inline (like /scripts/<id>/test-runs and unlike
# /test-runs, which queue a JobManager job). Status 200 (not 202)
# signals "done synchronously" so callers don't poll a non-existent
# job. The unhappy-path envelope is the same shape as the happy-path
# so clients have a single parser.


def _build_login_browser_config(
    project_stealth: bool | None, project_headless: str | None,
) -> dict[str, Any]:
    """Snapshot the app config + project overrides for a fresh browser.

    Mirrors the legacy ``test_login`` handlers — project-level
    ``stealth_mode`` and ``headless_browser`` win when present;
    otherwise the runtime defaults apply.
    """
    browser_config: dict[str, Any] = get_app_config().__dict__.copy()
    if project_stealth is not None:
        browser_config["stealth_mode"] = project_stealth
    else:
        browser_config["stealth_mode"] = False
    if project_headless is not None:
        browser_config["BROWSER_HEADLESS"] = project_headless == "true"
    return browser_config


async def _run_login_test(
    browser_config: dict[str, Any], user_obj: Any, timeout_ms: int,
) -> dict[str, Any]:
    """Spin a fresh browser, attempt the user's login, return the result.

    Imported lazily because :class:`BrowserManager` and
    :class:`LoginAutomation` pull Playwright at module load — keeping
    them out of the route's import path makes ``api.py`` cheap to
    import even when the test-login endpoint is never called.
    """
    from auto_a11y.core.browser_manager import BrowserManager
    from auto_a11y.testing.login_automation import LoginAutomation

    bm = BrowserManager(browser_config)
    try:
        await bm.start()
        context = await bm.create_context()
        page_obj = await context.new_page()
        login_automation = LoginAutomation(get_db())
        result = await login_automation.perform_login(
            page_obj, user_obj, timeout=timeout_ms,
        )
        return result
    finally:
        await bm.stop()


@api_bp.route(
    "/project-test-users/<user_id>/test-login", methods=["POST"]
)
@api_endpoint
def test_project_user_login(
    user_id: str,
) -> tuple[Response, int] | Response:
    """Run the project test-user's login automation against the live site.

    Synchronous; the response is the same envelope shape regardless of
    success or failure so clients have a single parser. The fresh
    browser is closed before returning even when login fails or
    raises — no leaked Playwright instances.

    Response shape:

        {
          "user_id":      "...",
          "scope":        "project",
          "success":      true|false,
          "duration_ms":  int,
          "error":        null | "...",
          "manual_login": bool,
          "wait_seconds": int | null
        }

    Errors:

    - **404** — user does not exist
    - **400** — login_url is not configured AND authentication_method
      is not ``manual_login`` (manual login doesn't need a URL — it
      pops a visible browser for the operator to drive)
    - **409** — server-side BROWSER_MODE rules out a local browser
      (``disabled`` / ``remote`` — the endpoint has no queueable
      fallback)
    - **500** — login_automation raises; the body still carries the
      ``success: false`` envelope plus the error message
    """
    import asyncio

    user = get_db().get_project_user(user_id)
    if user is None:
        raise NotFoundError(f"project test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=user.project_id,
    )

    login_config = user.login_config
    is_manual = login_config.authentication_method.value == "manual_login"
    if not login_config.login_url and not is_manual:
        raise ValidationError(
            f"login_url is not configured for user {user_id}",
            errors=(
                _FieldError(
                    field="login_config.login_url", code="required",
                    message="required for non-manual authentication methods",
                ),
            ),
        )

    browser_mode = getattr(get_app_config(), "BROWSER_MODE", "local")
    if browser_mode in ("disabled", "remote"):
        raise ConflictError(
            f"test-login requires local browser; BROWSER_MODE={browser_mode!r}"
        )

    project = get_db().get_project(user.project_id)
    project_stealth = (
        bool(project.config.get("stealth_mode", False))
        if project is not None and project.config else None
    )
    project_headless = (
        str(project.config.get("headless_browser", "true"))
        if project is not None and project.config else None
    )
    browser_config = _build_login_browser_config(
        project_stealth, project_headless,
    )

    wait_seconds = (
        login_config.manual_login_wait_seconds if is_manual else 30
    )
    timeout_ms = max(30000, (wait_seconds + 30) * 1000)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            _run_login_test(browser_config, user, timeout_ms),
        )
    except Exception as exc:
        logger.error(
            "Error testing login for project user %s: %s", user_id, exc,
        )
        return jsonify({
            "user_id": user_id,
            "scope": "project",
            "success": False,
            "duration_ms": 0,
            "error": str(exc),
            "manual_login": is_manual,
            "wait_seconds": wait_seconds if is_manual else None,
        }), 500
    finally:
        loop.close()

    return jsonify({
        "user_id": user_id,
        "scope": "project",
        "success": bool(result.get("success", False)),
        "duration_ms": int(result.get("duration_ms", 0) or 0),
        "error": result.get("error"),
        "manual_login": is_manual,
        "wait_seconds": wait_seconds if is_manual else None,
    })


@api_bp.route(
    "/website-test-users/<user_id>/test-login", methods=["POST"]
)
@api_endpoint
def test_website_user_login(
    user_id: str,
) -> tuple[Response, int] | Response:
    """Run the website test-user's login automation against the live site.

    Companion to :func:`test_project_user_login`. The two share the
    same response envelope and synchronous semantics — the difference
    is which collection the user record lives in and which target
    (website's project) the role check runs against.
    """
    import asyncio

    user = get_db().get_website_user(user_id)
    if user is None:
        raise NotFoundError(f"website test user {user_id} not found")

    website = get_db().get_website(user.website_id)
    if website is None:
        raise NotFoundError(
            f"website {user.website_id} for user {user_id} not found"
        )
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=user.website_id,
    )

    login_config = user.login_config
    is_manual = login_config.authentication_method.value == "manual_login"
    if not login_config.login_url and not is_manual:
        raise ValidationError(
            f"login_url is not configured for user {user_id}",
            errors=(
                _FieldError(
                    field="login_config.login_url", code="required",
                    message="required for non-manual authentication methods",
                ),
            ),
        )

    browser_mode = getattr(get_app_config(), "BROWSER_MODE", "local")
    if browser_mode in ("disabled", "remote"):
        raise ConflictError(
            f"test-login requires local browser; BROWSER_MODE={browser_mode!r}"
        )

    project = (
        get_db().get_project(website.project_id)
        if website.project_id else None
    )
    project_stealth = (
        bool(project.config.get("stealth_mode", False))
        if project is not None and project.config else None
    )
    project_headless = (
        str(project.config.get("headless_browser", "true"))
        if project is not None and project.config else None
    )
    browser_config = _build_login_browser_config(
        project_stealth, project_headless,
    )

    wait_seconds = (
        login_config.manual_login_wait_seconds if is_manual else 30
    )
    timeout_ms = max(30000, (wait_seconds + 30) * 1000)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            _run_login_test(browser_config, user, timeout_ms),
        )
    except Exception as exc:
        logger.error(
            "Error testing login for website user %s: %s", user_id, exc,
        )
        return jsonify({
            "user_id": user_id,
            "scope": "website",
            "success": False,
            "duration_ms": 0,
            "error": str(exc),
            "manual_login": is_manual,
            "wait_seconds": wait_seconds if is_manual else None,
        }), 500
    finally:
        loop.close()

    return jsonify({
        "user_id": user_id,
        "scope": "website",
        "success": bool(result.get("success", False)),
        "duration_ms": int(result.get("duration_ms", 0) or 0),
        "error": result.get("error"),
        "manual_login": is_manual,
        "wait_seconds": wait_seconds if is_manual else None,
    })


# --- Website-scoped test users ------------------------------------------------


@api_bp.route("/websites/<website_id>/test-users", methods=["GET"])
@api_endpoint
def list_website_test_users_rest(website_id: str) -> tuple[Response, int] | Response:
    """List test users (login credentials) for a website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")
    users = get_db().get_website_users(website_id)
    return jsonify({"items": [_serialize_website_test_user(u) for u in users]})


@api_bp.route("/websites/<website_id>/test-users", methods=["POST"])
@api_endpoint
def create_website_test_user_rest(website_id: str) -> tuple[Response, int]:
    """Create a test user under a website."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=website_id
    )
    if get_db().get_website(website_id) is None:
        raise NotFoundError(f"website {website_id} not found")

    body = _require_dict_body()
    user = _build_website_test_user_from_body(website_id, body)

    if get_db().get_website_user_by_username(website_id, user.username) is not None:
        raise ConflictError(f"username {user.username!r} is already in use on this website")

    user_id = get_db().create_website_user(user)
    refreshed = get_db().get_website_user(user_id)
    if refreshed is None:
        raise ConflictError("website test user failed to persist")
    response = jsonify(_serialize_website_test_user(refreshed))
    response.headers["Location"] = f"/api/v1/website-test-users/{user_id}"
    return response, 201


@api_bp.route("/website-test-users/<user_id>", methods=["GET"])
@api_endpoint
def get_website_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Get one website test user."""
    user = get_db().get_website_user(user_id)
    if user is None:
        raise NotFoundError(f"website test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, website_id=user.website_id
    )
    return jsonify(_serialize_website_test_user(user))


@api_bp.route("/website-test-users/<user_id>", methods=["PUT"])
@api_endpoint
def replace_website_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Full replace of a website test user's editable fields."""
    existing = get_db().get_website_user(user_id)
    if existing is None:
        raise NotFoundError(f"website test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=existing.website_id
    )
    body = _require_dict_body()
    if "password" not in body or not isinstance(body.get("password"), str) or not body["password"]:
        body["password"] = existing.password
    replaced = _build_website_test_user_from_body(existing.website_id, body)
    if (
        replaced.username != existing.username
        and get_db().get_website_user_by_username(existing.website_id, replaced.username) is not None
    ):
        raise ConflictError(f"username {replaced.username!r} is already in use on this website")
    replaced.mongo_id = existing.mongo_id
    replaced.created_at = existing.created_at
    replaced.last_used = existing.last_used
    replaced.last_login_success = existing.last_login_success
    replaced.last_login_error = existing.last_login_error
    replaced.update_timestamp()
    if not get_db().update_website_user(replaced):
        raise ConflictError("website test user could not be updated")
    return jsonify(_serialize_website_test_user(replaced))


@api_bp.route("/website-test-users/<user_id>", methods=["PATCH"])
@api_endpoint
def patch_website_test_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Partial update of a website test user."""
    user = get_db().get_website_user(user_id)
    if user is None:
        raise NotFoundError(f"website test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=user.website_id
    )
    body = _require_dict_body()
    if "username" in body and isinstance(body["username"], str):
        new_username = body["username"].strip()
        if new_username != user.username:
            if get_db().get_website_user_by_username(user.website_id, new_username) is not None:
                raise ConflictError(f"username {new_username!r} is already in use on this website")
    _apply_patch_to_test_user(user, body)
    if not get_db().update_website_user(user):
        raise ConflictError("website test user could not be updated")
    return jsonify(_serialize_website_test_user(user))


@api_bp.route("/website-test-users/<user_id>", methods=["DELETE"])
@api_endpoint
def delete_website_test_user_rest(user_id: str) -> tuple[Response, int]:
    """Delete a website test user."""
    user = get_db().get_website_user(user_id)
    if user is None:
        raise NotFoundError(f"website test user {user_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, website_id=user.website_id
    )
    get_db().delete_website_user(user_id)
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Project participants (lived-experience testers + test supervisors).
#
# Closes the last open piece of docs/REST_API_ROADMAP.md §5.11. Both
# resources live as inline arrays on the Project document
# (project.lived_experience_testers, project.test_supervisors), with
# string UUID ids assigned on insert via ``ensure_id()``. The Project
# helpers (add_tester, get_tester, update_tester, remove_tester and
# their supervisor mirrors) are the system of record; we lift them
# into REST and persist by replacing the whole project document.
#
# The legacy project_participants_bp HTML routes still serve the admin
# UI under /projects/<id>/participants/...
# ---------------------------------------------------------------------------

from auto_a11y.models.project import LivedExperienceTester, TestSupervisor  # noqa: E402


def _serialize_tester(tester: LivedExperienceTester) -> dict[str, Any]:
    return {
        "id": tester.id,
        "name": tester.name,
        "email": tester.email,
        "disability_type": tester.disability_type,
        "assistive_tech": list(tester.assistive_tech),
        "notes": tester.notes,
    }


def _serialize_supervisor(supervisor: TestSupervisor) -> dict[str, Any]:
    return {
        "id": supervisor.id,
        "name": supervisor.name,
        "email": supervisor.email,
        "role": supervisor.role,
        "organization": supervisor.organization,
        "notes": supervisor.notes,
    }


def _validate_required_name(body: dict[str, Any]) -> str:
    name_raw = body.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        raise ValidationError(
            "name is required",
            errors=(_FieldError(field="name", code="required", message="required"),),
        )
    return name_raw.strip()


def _build_tester_from_body(body: dict[str, Any]) -> LivedExperienceTester:
    name = _validate_required_name(body)
    assistive_tech_raw: Any = body.get("assistive_tech", [])
    if not isinstance(assistive_tech_raw, list):
        raise ValidationError(
            "assistive_tech must be an array",
            errors=(_FieldError(field="assistive_tech", code="invalid_type", message="must be array"),),
        )
    return LivedExperienceTester(
        name=name,
        email=_optional_str(body.get("email"), field="email"),
        disability_type=_optional_str(body.get("disability_type"), field="disability_type"),
        assistive_tech=_coerce_str_list(assistive_tech_raw),
        notes=_optional_str(body.get("notes"), field="notes"),
    )


def _build_supervisor_from_body(body: dict[str, Any]) -> TestSupervisor:
    name = _validate_required_name(body)
    return TestSupervisor(
        name=name,
        email=_optional_str(body.get("email"), field="email"),
        role=_optional_str(body.get("role"), field="role"),
        organization=_optional_str(body.get("organization"), field="organization"),
        notes=_optional_str(body.get("notes"), field="notes"),
    )


def _apply_patch_to_tester(
    tester: LivedExperienceTester, body: dict[str, Any]
) -> None:
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise ValidationError(
                "name must be a non-empty string",
                errors=(_FieldError(field="name", code="invalid_value", message="must be non-empty"),),
            )
        tester.name = body["name"].strip()
    if "email" in body:
        tester.email = _optional_str(body["email"], field="email")
    if "disability_type" in body:
        tester.disability_type = _optional_str(body["disability_type"], field="disability_type")
    if "assistive_tech" in body:
        if not isinstance(body["assistive_tech"], list):
            raise ValidationError(
                "assistive_tech must be an array",
                errors=(_FieldError(field="assistive_tech", code="invalid_type", message="must be array"),),
            )
        tester.assistive_tech = _coerce_str_list(body["assistive_tech"])
    if "notes" in body:
        tester.notes = _optional_str(body["notes"], field="notes")


def _apply_patch_to_supervisor(
    supervisor: TestSupervisor, body: dict[str, Any]
) -> None:
    if "name" in body:
        if not isinstance(body["name"], str) or not body["name"].strip():
            raise ValidationError(
                "name must be a non-empty string",
                errors=(_FieldError(field="name", code="invalid_value", message="must be non-empty"),),
            )
        supervisor.name = body["name"].strip()
    if "email" in body:
        supervisor.email = _optional_str(body["email"], field="email")
    if "role" in body:
        supervisor.role = _optional_str(body["role"], field="role")
    if "organization" in body:
        supervisor.organization = _optional_str(body["organization"], field="organization")
    if "notes" in body:
        supervisor.notes = _optional_str(body["notes"], field="notes")


# --- Testers ------------------------------------------------------------------


@api_bp.route("/projects/<project_id>/testers", methods=["GET"])
@api_endpoint
def list_testers_rest(project_id: str) -> tuple[Response, int] | Response:
    """List lived-experience testers for a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return jsonify(
        {"items": [_serialize_tester(t) for t in project.lived_experience_testers]}
    )


@api_bp.route("/projects/<project_id>/testers", methods=["POST"])
@api_endpoint
def create_tester_rest(project_id: str) -> tuple[Response, int]:
    """Create a lived-experience tester on a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    tester = _build_tester_from_body(body)
    project.add_tester(tester)
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")

    response = jsonify(_serialize_tester(tester))
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/testers/{tester.id}"
    )
    return response, 201


@api_bp.route("/projects/<project_id>/testers/<tester_id>", methods=["GET"])
@api_endpoint
def get_tester_rest(
    project_id: str, tester_id: str
) -> tuple[Response, int] | Response:
    """Get one lived-experience tester."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    tester = project.get_tester(tester_id)
    if tester is None:
        raise NotFoundError(f"tester {tester_id} not found in project {project_id}")
    return jsonify(_serialize_tester(tester))


@api_bp.route("/projects/<project_id>/testers/<tester_id>", methods=["PUT"])
@api_endpoint
def replace_tester_rest(
    project_id: str, tester_id: str
) -> tuple[Response, int] | Response:
    """Full replace of a tester's editable fields. The id is preserved."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    existing = project.get_tester(tester_id)
    if existing is None:
        raise NotFoundError(f"tester {tester_id} not found in project {project_id}")

    body = _require_dict_body()
    replaced = _build_tester_from_body(body)
    replaced.mongo_id = tester_id
    if not project.update_tester(replaced):
        raise ConflictError("tester could not be updated")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return jsonify(_serialize_tester(replaced))


@api_bp.route("/projects/<project_id>/testers/<tester_id>", methods=["PATCH"])
@api_endpoint
def patch_tester_rest(
    project_id: str, tester_id: str
) -> tuple[Response, int] | Response:
    """Partial update of a tester."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    tester = project.get_tester(tester_id)
    if tester is None:
        raise NotFoundError(f"tester {tester_id} not found in project {project_id}")

    body = _require_dict_body()
    _apply_patch_to_tester(tester, body)
    if not project.update_tester(tester):
        raise ConflictError("tester could not be updated")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return jsonify(_serialize_tester(tester))


@api_bp.route("/projects/<project_id>/testers/<tester_id>", methods=["DELETE"])
@api_endpoint
def delete_tester_rest(project_id: str, tester_id: str) -> tuple[Response, int]:
    """Delete a lived-experience tester from a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    if not project.remove_tester(tester_id):
        raise NotFoundError(f"tester {tester_id} not found in project {project_id}")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return Response(status=204), 204


# --- Supervisors --------------------------------------------------------------


@api_bp.route("/projects/<project_id>/supervisors", methods=["GET"])
@api_endpoint
def list_supervisors_rest(project_id: str) -> tuple[Response, int] | Response:
    """List test supervisors for a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return jsonify(
        {"items": [_serialize_supervisor(s) for s in project.test_supervisors]}
    )


@api_bp.route("/projects/<project_id>/supervisors", methods=["POST"])
@api_endpoint
def create_supervisor_rest(project_id: str) -> tuple[Response, int]:
    """Create a test supervisor on a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body()
    supervisor = _build_supervisor_from_body(body)
    project.add_supervisor(supervisor)
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")

    response = jsonify(_serialize_supervisor(supervisor))
    response.headers["Location"] = (
        f"/api/v1/projects/{project_id}/supervisors/{supervisor.id}"
    )
    return response, 201


@api_bp.route("/projects/<project_id>/supervisors/<supervisor_id>", methods=["GET"])
@api_endpoint
def get_supervisor_rest(
    project_id: str, supervisor_id: str
) -> tuple[Response, int] | Response:
    """Get one test supervisor."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    supervisor = project.get_supervisor(supervisor_id)
    if supervisor is None:
        raise NotFoundError(
            f"supervisor {supervisor_id} not found in project {project_id}"
        )
    return jsonify(_serialize_supervisor(supervisor))


@api_bp.route("/projects/<project_id>/supervisors/<supervisor_id>", methods=["PUT"])
@api_endpoint
def replace_supervisor_rest(
    project_id: str, supervisor_id: str
) -> tuple[Response, int] | Response:
    """Full replace of a supervisor's editable fields. The id is preserved."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    existing = project.get_supervisor(supervisor_id)
    if existing is None:
        raise NotFoundError(
            f"supervisor {supervisor_id} not found in project {project_id}"
        )

    body = _require_dict_body()
    replaced = _build_supervisor_from_body(body)
    replaced.mongo_id = supervisor_id
    if not project.update_supervisor(replaced):
        raise ConflictError("supervisor could not be updated")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return jsonify(_serialize_supervisor(replaced))


@api_bp.route("/projects/<project_id>/supervisors/<supervisor_id>", methods=["PATCH"])
@api_endpoint
def patch_supervisor_rest(
    project_id: str, supervisor_id: str
) -> tuple[Response, int] | Response:
    """Partial update of a supervisor."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    supervisor = project.get_supervisor(supervisor_id)
    if supervisor is None:
        raise NotFoundError(
            f"supervisor {supervisor_id} not found in project {project_id}"
        )

    body = _require_dict_body()
    _apply_patch_to_supervisor(supervisor, body)
    if not project.update_supervisor(supervisor):
        raise ConflictError("supervisor could not be updated")
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return jsonify(_serialize_supervisor(supervisor))


@api_bp.route("/projects/<project_id>/supervisors/<supervisor_id>", methods=["DELETE"])
@api_endpoint
def delete_supervisor_rest(
    project_id: str, supervisor_id: str
) -> tuple[Response, int]:
    """Delete a test supervisor from a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    if not project.remove_supervisor(supervisor_id):
        raise NotFoundError(
            f"supervisor {supervisor_id} not found in project {project_id}"
        )
    if not get_db().update_project(project):
        raise ConflictError("project document could not be updated")
    return Response(status=204), 204


# ---------------------------------------------------------------------------
# Discovered pages (REST shape — uses the @api_endpoint scaffolding).
#
# Per docs/REST_API_ROADMAP.md §5.3. A "discovered page" is a key page
# or screen flagged for manual inspection / lived-experience testing
# (typically the 20-25 most interesting pages in an audit). They live
# in their own ``discovered_pages`` Mongo collection — separate from
# the regular Page model — and carry taxonomy tags (``interested_because``,
# ``page_elements``) plus public/private notes that flow into Drupal
# audit-report nodes.
#
# The legacy discovered_pages_bp HTML routes still serve the admin UI;
# the REST endpoints add the standard /api/v1 surface with cursor
# pagination and Problem-Details errors.
# ---------------------------------------------------------------------------

from auto_a11y.models.discovered_page import DiscoveredPage  # noqa: E402
from auto_a11y.models.page import DrupalSyncStatus  # noqa: E402


def _discovered_page_to_out(page: DiscoveredPage) -> DiscoveredPageOut:
    """Project a :class:`DiscoveredPage` to a :class:`DiscoveredPageOut`.

    Mirrors the legacy ``_serialize_discovered_page`` shape byte-for-byte:
    datetimes emit as ISO 8601 strings and the Mongo ``_id`` is dropped
    (the public identifier is the ``id`` string property). Drupal-sync
    fields are exposed read-only.
    """

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    return DiscoveredPageOut(
        id=page.id,
        title=page.title,
        url=page.url,
        project_id=page.project_id,
        source_type=page.source_type,
        source_page_id=page.source_page_id,
        source_website_id=page.source_website_id,
        source_component_signature=page.source_component_signature,
        source_upload_id=page.source_upload_id,
        interested_because=list(page.interested_because),
        page_elements=list(page.page_elements),
        private_notes=page.private_notes,
        public_notes=page.public_notes,
        include_in_report=page.include_in_report,
        audited=page.audited,
        manual_audit=page.manual_audit,
        screenshot_paths=list(page.screenshot_paths),
        document_links=[dict(d) for d in page.document_links],
        drupal_uuid=page.drupal_uuid,
        drupal_sync_status=page.drupal_sync_status.value,
        drupal_last_synced=_iso(page.drupal_last_synced),
        drupal_error_message=page.drupal_error_message,
        created_at=_iso(page.created_at),
        updated_at=_iso(page.updated_at),
        created_by=page.created_by,
    )


def _discovered_page_from_in(
    project_id: str, body: DiscoveredPageIn | DiscoveredPagePut
) -> DiscoveredPage:
    """Construct a new :class:`DiscoveredPage` from a POST or PUT body.

    ``title`` and ``url`` are trimmed of leading/trailing whitespace
    (matching the legacy helper). Optional list fields default to empty
    lists when the client omits them; optional booleans default to the
    dataclass defaults (``include_in_report=True``, others ``False``).
    """
    return DiscoveredPage(
        title=body.title.strip(),
        url=body.url.strip(),
        project_id=project_id,
        source_type=body.source_type if body.source_type is not None else "manual",
        interested_because=list(body.interested_because or ()),
        page_elements=list(body.page_elements or ()),
        private_notes=body.private_notes if body.private_notes else None,
        public_notes=body.public_notes if body.public_notes else None,
        include_in_report=(
            body.include_in_report if body.include_in_report is not None else True
        ),
        audited=body.audited if body.audited is not None else False,
        manual_audit=body.manual_audit if body.manual_audit is not None else False,
        screenshot_paths=list(body.screenshot_paths or ()),
        document_links=[dict(d) for d in (body.document_links or ())],
        created_by=str(current_user.get_id()) if current_user.is_authenticated else None,
    )


def _apply_discovered_page_patch(
    page: DiscoveredPage, body: DiscoveredPagePatch
) -> None:
    """Apply a :class:`DiscoveredPagePatch` to ``page`` in place.

    Only fields the client *sent* are applied (Pydantic's
    ``model_fields_set`` distinguishes "absent" from "explicit null").
    ``updated_at`` is bumped regardless. Matches the legacy "patch only
    present keys" contract from ``_apply_patch_to_discovered_page``.
    """
    fields_set = body.model_fields_set
    if "title" in fields_set and body.title is not None:
        page.title = body.title.strip()
    if "url" in fields_set and body.url is not None:
        page.url = body.url.strip()
    if "interested_because" in fields_set:
        page.interested_because = list(body.interested_because or ())
    if "page_elements" in fields_set:
        page.page_elements = list(body.page_elements or ())
    if "private_notes" in fields_set:
        page.private_notes = (
            body.private_notes if body.private_notes else None
        )
    if "public_notes" in fields_set:
        page.public_notes = body.public_notes if body.public_notes else None
    if "include_in_report" in fields_set and body.include_in_report is not None:
        page.include_in_report = body.include_in_report
    if "audited" in fields_set and body.audited is not None:
        page.audited = body.audited
    if "manual_audit" in fields_set and body.manual_audit is not None:
        page.manual_audit = body.manual_audit
    if "screenshot_paths" in fields_set:
        page.screenshot_paths = list(body.screenshot_paths or ())
    if "document_links" in fields_set:
        page.document_links = [dict(d) for d in (body.document_links or ())]
    page.updated_at = datetime.now()


@api_bp.route("/projects/<project_id>/discovered-pages", methods=["GET"])
@api_endpoint
@document(
    response_200=DiscoveredPageListOut,
    errors=[400, 401, 403, 404],
    tags=["DiscoveredPages"],
    summary="List discovered pages in a project",
    description=(
        "Returns the discovered pages belonging to ``project_id`` with "
        "cursor pagination. The response shape is "
        "``{items, next_cursor}``."
    ),
)
def list_discovered_pages_rest(
    project_id: str,
) -> tuple[DiscoveredPageListOut, int] | tuple[Response, int] | Response:
    """List discovered pages within a project, with cursor pagination."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    limit = parse_limit(request.args.get("limit"))
    cursor_raw = request.args.get("cursor")
    cursor = _Cursor.decode(cursor_raw) if cursor_raw else None

    query: dict[str, Any] = {"project_id": project_id}
    if cursor is not None:
        from bson import ObjectId
        try:
            query["_id"] = {"$lt": ObjectId(cursor.last_id)}
        except Exception as exc:
            raise ValidationError(
                "cursor.last_id is not a valid ObjectId",
                errors=(_FieldError(field="cursor.last_id", code="invalid_format", message=str(exc)),),
            ) from exc

    docs = list(get_db().discovered_pages.find(query).sort("_id", -1).limit(limit + 1))
    pages = [DiscoveredPage.from_dict(doc) for doc in docs]
    page = paginate(
        pages, limit=limit, get_id=lambda p: str(p.mongo_id) if p.mongo_id else ""
    )
    return DiscoveredPageListOut(
        items=[_discovered_page_to_out(p) for p in page["items"]],
        next_cursor=page["next_cursor"],
    ), 200


@api_bp.route("/projects/<project_id>/discovered-pages", methods=["POST"])
@api_endpoint
@document(
    request=DiscoveredPageIn,
    response_201=DiscoveredPageOut,
    errors=[400, 401, 403, 404, 409],
    tags=["DiscoveredPages"],
    summary="Create a discovered page",
    description=(
        "Creates a discovered page on ``project_id``. Returns the full "
        "resource plus a ``Location`` header pointing at "
        "``/api/v1/discovered-pages/<id>``."
    ),
)
def create_discovered_page_rest(
    project_id: str, body: DiscoveredPageIn
) -> tuple[Response, int]:
    """Create a discovered page on a project."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    page = _discovered_page_from_in(project_id, body)
    new_id = get_db().create_discovered_page(page)
    refreshed = get_db().get_discovered_page_by_id(new_id)
    if refreshed is None:
        raise ConflictError("discovered page failed to persist")

    payload = _discovered_page_to_out(refreshed)
    response = jsonify(
        payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    response.headers["Location"] = f"/api/v1/discovered-pages/{new_id}"
    return response, 201


@api_bp.route("/discovered-pages/<page_id>", methods=["GET"])
@api_endpoint
@document(
    response_200=DiscoveredPageOut,
    errors=[401, 403, 404],
    tags=["DiscoveredPages"],
    summary="Get a discovered page by ID",
    description="Returns the discovered page resource.",
)
def get_discovered_page_rest(
    page_id: str,
) -> tuple[DiscoveredPageOut, int] | tuple[Response, int] | Response:
    """Get a discovered page by id."""
    page = get_db().get_discovered_page_by_id(page_id)
    if page is None:
        raise NotFoundError(f"discovered page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT, project_id=page.project_id
    )
    return _discovered_page_to_out(page), 200


@api_bp.route("/discovered-pages/<page_id>", methods=["PUT"])
@api_endpoint
@document(
    request=DiscoveredPagePut,
    response_200=DiscoveredPageOut,
    errors=[400, 401, 403, 404, 409],
    tags=["DiscoveredPages"],
    summary="Replace a discovered page",
    description=(
        "Full replace of a discovered page's editable fields. "
        "Server-managed fields (``project_id``, ``source_*``, "
        "``drupal_*``, ``created_at``, ``created_by``) are preserved "
        "from the existing record; clients cannot reassign a "
        "discovered page to a different project, change its source, "
        "or rewrite Drupal-sync state via this endpoint."
    ),
)
def replace_discovered_page_rest(
    page_id: str, body: DiscoveredPagePut
) -> tuple[DiscoveredPageOut, int] | tuple[Response, int] | Response:
    """Full replace of a discovered page's editable fields."""
    existing = get_db().get_discovered_page_by_id(page_id)
    if existing is None:
        raise NotFoundError(f"discovered page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=existing.project_id
    )
    replaced = _discovered_page_from_in(existing.project_id, body)
    replaced.mongo_id = existing.mongo_id
    replaced.created_at = existing.created_at
    replaced.created_by = existing.created_by
    replaced.source_type = existing.source_type
    replaced.source_page_id = existing.source_page_id
    replaced.source_website_id = existing.source_website_id
    replaced.source_component_signature = existing.source_component_signature
    replaced.source_upload_id = existing.source_upload_id
    replaced.drupal_uuid = existing.drupal_uuid
    replaced.drupal_sync_status = existing.drupal_sync_status
    replaced.drupal_last_synced = existing.drupal_last_synced
    replaced.drupal_error_message = existing.drupal_error_message
    replaced.updated_at = datetime.now()
    if not get_db().update_discovered_page(replaced):
        raise ConflictError("discovered page could not be updated")
    return _discovered_page_to_out(replaced), 200


@api_bp.route("/discovered-pages/<page_id>", methods=["PATCH"])
@api_endpoint
@document(
    request=DiscoveredPagePatch,
    response_200=DiscoveredPageOut,
    errors=[400, 401, 403, 404, 409],
    tags=["DiscoveredPages"],
    summary="Partially update a discovered page",
    description=(
        "Partial update -- only fields present in the request body "
        "are applied. ``updated_at`` is bumped on every successful "
        "PATCH. Returns the full updated resource."
    ),
)
def patch_discovered_page_rest(
    page_id: str, body: DiscoveredPagePatch
) -> tuple[DiscoveredPageOut, int] | tuple[Response, int] | Response:
    """Partial update — covers the legacy edit form's per-field updates."""
    page = get_db().get_discovered_page_by_id(page_id)
    if page is None:
        raise NotFoundError(f"discovered page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=page.project_id
    )
    _apply_discovered_page_patch(page, body)
    if not get_db().update_discovered_page(page):
        raise ConflictError("discovered page could not be updated")
    return _discovered_page_to_out(page), 200


@api_bp.route("/discovered-pages/<page_id>", methods=["DELETE"])
@api_endpoint
@document(
    response_204=Empty,
    errors=[401, 403, 404],
    tags=["DiscoveredPages"],
    summary="Delete a discovered page",
    description=(
        "Deletes the discovered page. Returns ``204 No Content`` with "
        "an empty body."
    ),
)
def delete_discovered_page_rest(
    page_id: str,
) -> tuple[Empty, int] | tuple[Response, int]:
    """Delete a discovered page."""
    page = get_db().get_discovered_page_by_id(page_id)
    if page is None:
        raise NotFoundError(f"discovered page {page_id} not found")
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=page.project_id
    )
    get_db().delete_discovered_page(page_id)
    return Empty(), 204


# ---------------------------------------------------------------------------
# Auth API (§5.13) — login/logout/register/password-reset/profile + admin
# user CRUD + Microsoft/Google SSO.
#
# The legacy /auth/* HTML blueprint stays alive for the browser-form
# UX (session cookie auth, email-link reset flow, OAuth callbacks
# pointing at template-rendered redirects). This REST surface mints
# Bearer tokens via auto_a11y.models.api_token.ApiToken and is the
# authentication path for SPAs and CLI clients on /api/v1.
#
# Tokens are returned **once** at create time; subsequent requests
# carry ``Authorization: Bearer <raw>``. The require_authenticated
# helper in auto_a11y.web.api.auth accepts either the session cookie
# or a Bearer token — see that module's docstring for the merge
# semantic.
# ---------------------------------------------------------------------------

from auto_a11y.models.api_token import ApiToken  # noqa: E402
from auto_a11y.web.api.tokens import (  # noqa: E402
    extract_bearer,
    mint_token,
    revoke_token_by_hash,
)


def _serialize_app_user(user: AppUser) -> dict[str, Any]:
    """Project an :class:`AppUser` to a JSON-safe dict.

    Hides ``password_hash`` and any internal SSO fields. ``role`` is
    the enum's string value; the timestamps are ISO 8601.
    """

    def _iso(dt: Any) -> str | None:
        return dt.isoformat() if isinstance(dt, datetime) else None

    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "is_active": user.is_active,
        "is_superadmin": bool(user.is_superadmin),
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
        "last_login": _iso(getattr(user, "last_login", None)),
    }


def _serialize_token(token: ApiToken) -> dict[str, Any]:
    return {
        "id": token.id,
        "user_id": token.user_id,
        "description": token.description,
        "created_at": (
            token.created_at.isoformat() if token.created_at else None
        ),
        "last_used_at": (
            token.last_used_at.isoformat() if token.last_used_at else None
        ),
        "expires_at": (
            token.expires_at.isoformat() if token.expires_at else None
        ),
    }


@api_bp.route("/auth/login", methods=["POST"])
@api_endpoint
def auth_login_rest() -> tuple[Response, int] | Response:
    """Exchange email + password for an API Bearer token.

    Body:

        {"email": "...", "password": "...", "description": "..."?}

    On success returns:

        {
          "token":        "a11y_…",          // raw token, returned ONCE
          "token_record": {id, ...},          // persisted metadata
          "user":         {id, email, role, ...}
        }

    On any failure (unknown email, wrong password, deactivated user)
    returns 401 with an opaque ``invalid_credentials`` message so the
    caller can't distinguish which case fired. The legacy HTML route
    records the attempt for rate-limiting; we keep that bookkeeping
    by calling ``record_login`` on success.
    """
    body = _require_dict_body()
    email = body.get("email")
    password = body.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        raise UnauthorizedError("invalid credentials")
    description_raw = body.get("description")
    description = (
        description_raw if isinstance(description_raw, str) else None
    )

    user = get_db().get_app_user_by_email(email)
    if user is None or not user.is_active or not user.check_password(password):
        raise UnauthorizedError("invalid credentials")

    user.record_login(success=True)
    get_db().update_app_user(user)

    assert user.id is not None
    raw_token, token = mint_token(
        get_db(), user_id=user.id, description=description,
    )
    return jsonify({
        "token": raw_token,
        "token_record": _serialize_token(token),
        "user": _serialize_app_user(user),
    })


@api_bp.route("/auth/logout", methods=["POST"])
@api_endpoint
def auth_logout_rest() -> tuple[Response, int] | Response:
    """Revoke the Bearer token used to authorize the call.

    Idempotent — a request with no token or an already-revoked token
    still returns 204. The motivation is a "log out" button on the
    client that should always succeed even if the session expired
    between the user clicking and the server seeing the request.

    Returns 204. Session-cookie callers still need to hit the legacy
    ``/auth/logout`` HTML route to clear the cookie — this endpoint
    only touches the Bearer side.
    """
    raw = extract_bearer(request.headers.get("Authorization"))
    if raw:
        revoke_token_by_hash(get_db(), raw)
    return Response(status=204), 204


_REGISTER_FIELDS = ("email", "password", "display_name")


@api_bp.route("/auth/register", methods=["POST"])
@api_endpoint
def auth_register_rest() -> tuple[Response, int] | Response:
    """Create a new :class:`AppUser` and return a token.

    Body: ``{email, password, display_name?}``.

    Returns 201 with the same envelope as ``/auth/login`` — a freshly
    minted token plus the user record — so a client can register and
    immediately make authenticated requests without a second roundtrip.

    Errors:

    - **400** — missing email/password, or email already registered
      (the dedup check is case-insensitive)
    """
    body = _require_dict_body()
    email = body.get("email")
    password = body.get("password")
    if not isinstance(email, str) or not email:
        raise ValidationError(
            "email is required",
            errors=(
                _FieldError(field="email", code="required", message="required"),
            ),
        )
    if not isinstance(password, str) or len(password) < 6:
        raise ValidationError(
            "password must be at least 6 characters",
            errors=(
                _FieldError(
                    field="password", code="too_short",
                    message="min 6 characters",
                ),
            ),
        )

    display_name_raw = body.get("display_name")
    display_name = (
        display_name_raw if isinstance(display_name_raw, str) else None
    )

    existing = get_db().get_app_user_by_email(email)
    if existing is not None:
        raise ValidationError(
            f"email {email!r} is already registered",
            errors=(
                _FieldError(
                    field="email", code="duplicate",
                    message="already registered",
                ),
            ),
        )

    new_user = AppUser.create(
        email=email, password=password, display_name=display_name,
    )
    new_user_id = get_db().create_app_user(new_user)
    persisted = get_db().get_app_user(new_user_id)
    if persisted is None:
        raise ConflictError("user failed to persist")

    raw_token, token = mint_token(get_db(), user_id=new_user_id)

    response = jsonify({
        "token": raw_token,
        "token_record": _serialize_token(token),
        "user": _serialize_app_user(persisted),
    })
    response.headers["Location"] = f"/api/v1/users/{new_user_id}"
    return response, 201


@api_bp.route("/auth/forgot-password", methods=["POST"])
@api_endpoint
def auth_forgot_password_rest() -> tuple[Response, int] | Response:
    """Request a password-reset email.

    Body: ``{email}``.

    Always returns 202 — even if the email is unknown — so an
    attacker can't probe which addresses are registered. The legacy
    HTML route does the same. When the email matches a real user,
    we generate a signed reset token and dispatch the standard
    auth.py email template.
    """
    from auto_a11y.web.routes.auth import send_password_reset_email

    body = _require_dict_body()
    email = body.get("email")
    if isinstance(email, str) and email:
        user = get_db().get_app_user_by_email(email)
        if user is not None and user.is_active:
            try:
                send_password_reset_email(user)
            except Exception as exc:  # noqa: BLE001
                # Don't leak SMTP/template failures to the caller —
                # the legacy HTML route swallows them with a flash;
                # we log and return 202 the same.
                logger.warning(
                    "password reset email failed for %s: %s", email, exc,
                )
    return Response(status=202), 202


@api_bp.route("/auth/reset-password", methods=["POST"])
@api_endpoint
def auth_reset_password_rest() -> tuple[Response, int] | Response:
    """Complete a password reset using the token from the email link.

    Body: ``{token, password}``. Token is the signed value emitted
    by ``generate_reset_token``; ``password`` must be at least 6
    characters. On success returns 200 with a freshly-minted Bearer
    token so the caller can land on the app authenticated.

    Errors:

    - **400** — token is invalid/expired, or password too short
    - **404** — token decodes but the email no longer maps to a user
      (e.g. the account was deleted between request and completion)
    """
    from auto_a11y.web.routes.auth import verify_reset_token

    body = _require_dict_body()
    token = body.get("token")
    password = body.get("password")
    if not isinstance(token, str) or not token:
        raise ValidationError(
            "token is required",
            errors=(
                _FieldError(field="token", code="required", message="required"),
            ),
        )
    if not isinstance(password, str) or len(password) < 6:
        raise ValidationError(
            "password must be at least 6 characters",
            errors=(
                _FieldError(
                    field="password", code="too_short",
                    message="min 6 characters",
                ),
            ),
        )

    email = verify_reset_token(token)
    if email is None:
        raise ValidationError(
            "reset token is invalid or expired",
            errors=(
                _FieldError(
                    field="token", code="invalid_value",
                    message="invalid or expired",
                ),
            ),
        )

    user = get_db().get_app_user_by_email(email)
    if user is None:
        raise NotFoundError(f"no user found for token {token!r}")

    user.set_password(password)
    get_db().update_app_user(user)

    assert user.id is not None
    raw_token, persisted_token = mint_token(get_db(), user_id=user.id)
    return jsonify({
        "token": raw_token,
        "token_record": _serialize_token(persisted_token),
        "user": _serialize_app_user(user),
    })


@api_bp.route("/auth/me", methods=["GET"])
@api_endpoint
def auth_me_rest() -> tuple[Response, int] | Response:
    """Return the authenticated user — session cookie or Bearer.

    The richer counterpart of ``/users/me`` (which is intentionally
    minimal). Surfaces ``role``, ``is_active``, ``is_superadmin``,
    and the user's timestamps so SPAs can render an authenticated
    profile without a separate `/users/<id>` GET.
    """
    user = require_authenticated()
    user_id = getattr(user, "id", None) or getattr(user, "get_id", lambda: None)()
    if not isinstance(user_id, str):
        raise UnauthorizedError("Authentication required")
    refreshed = get_db().get_app_user(user_id)
    if refreshed is None:
        raise NotFoundError(f"user {user_id} not found")
    return jsonify(_serialize_app_user(refreshed))


@api_bp.route("/auth/me", methods=["PATCH"])
@api_endpoint
def auth_me_patch_rest() -> tuple[Response, int] | Response:
    """Partial-update the authenticated user's editable profile fields.

    Body fields (all optional):

    - ``display_name``: string or null
    - ``password``: string (≥6 chars) — old password not required for
      Bearer-authenticated calls because the bearer is itself proof
      of identity; clients should re-prompt the user UI-side if they
      want a "current password" gate.

    Email and role are deliberately not patchable here — email is the
    natural key for SSO matching and the role/superadmin flags are
    admin-only mutations (use ``PATCH /users/<id>``).
    """
    user = require_authenticated()
    user_id = getattr(user, "id", None) or getattr(user, "get_id", lambda: None)()
    if not isinstance(user_id, str):
        raise UnauthorizedError("Authentication required")

    fresh = get_db().get_app_user(user_id)
    if fresh is None:
        raise NotFoundError(f"user {user_id} not found")

    body = _require_dict_body()
    if "display_name" in body:
        dn = body["display_name"]
        if dn is not None and not isinstance(dn, str):
            raise ValidationError(
                "display_name must be a string or null",
                errors=(
                    _FieldError(
                        field="display_name", code="invalid_type",
                        message="must be string",
                    ),
                ),
            )
        fresh.display_name = dn if isinstance(dn, str) else None
    if "password" in body:
        password = body["password"]
        if not isinstance(password, str) or len(password) < 6:
            raise ValidationError(
                "password must be at least 6 characters",
                errors=(
                    _FieldError(
                        field="password", code="too_short",
                        message="min 6 characters",
                    ),
                ),
            )
        fresh.set_password(password)

    if not get_db().update_app_user(fresh):
        # update_app_user returns False when nothing actually changed —
        # treat as success since the user's view of the world is correct.
        pass
    return jsonify(_serialize_app_user(fresh))


# --- Admin user CRUD --------------------------------------------------------
#
# The /users/me + /users/search endpoints are LIVE-MAIN. The roadmap §5.13
# also calls for full user CRUD; the routes below complete that surface.
# All require superadmin.


_VALID_USER_ROLES: frozenset[str] = frozenset(
    {role.value for role in UserRole}
)


@api_bp.route("/users", methods=["GET"])
@api_endpoint
def list_users_rest() -> tuple[Response, int] | Response:
    """List all :class:`AppUser` accounts. Superadmin-only.

    Returns the full list with no pagination — the legacy admin UI
    rendered all users on one page; the production database has a
    small enough user count for that to be fine. Add cursor
    pagination here later if the user count grows.
    """
    require_superadmin()
    cursor = get_db().app_users.find({}).sort("email", 1)
    users = [AppUser.from_dict(doc) for doc in cursor]
    return jsonify({"users": [_serialize_app_user(u) for u in users]})


@api_bp.route("/users", methods=["POST"])
@api_endpoint
def create_user_rest() -> tuple[Response, int] | Response:
    """Admin-create a new user. Superadmin-only.

    Body: ``{email, password, display_name?, role?, is_superadmin?}``.
    ``role`` must be one of the :class:`UserRole` string values;
    defaults to ``client``. ``is_superadmin`` defaults to false.

    Distinct from :func:`auth_register_rest` because the admin path
    can set role + superadmin flag at create time and doesn't issue
    a Bearer token (the admin doesn't need to become the new user).
    """
    require_superadmin()
    body = _require_dict_body()
    email = body.get("email")
    password = body.get("password")
    if not isinstance(email, str) or not email:
        raise ValidationError(
            "email is required",
            errors=(_FieldError(field="email", code="required", message="required"),),
        )
    if not isinstance(password, str) or len(password) < 6:
        raise ValidationError(
            "password must be at least 6 characters",
            errors=(
                _FieldError(
                    field="password", code="too_short",
                    message="min 6 characters",
                ),
            ),
        )

    role_raw = body.get("role", UserRole.CLIENT.value)
    if not isinstance(role_raw, str) or role_raw not in _VALID_USER_ROLES:
        raise ValidationError(
            f"role must be one of {sorted(_VALID_USER_ROLES)}",
            errors=(
                _FieldError(
                    field="role", code="invalid_value",
                    message=f"must be one of {sorted(_VALID_USER_ROLES)}",
                ),
            ),
        )
    role = UserRole(role_raw)

    display_name_raw = body.get("display_name")
    display_name = (
        display_name_raw if isinstance(display_name_raw, str) else None
    )

    if get_db().get_app_user_by_email(email) is not None:
        raise ValidationError(
            f"email {email!r} is already registered",
            errors=(
                _FieldError(
                    field="email", code="duplicate",
                    message="already registered",
                ),
            ),
        )

    new_user = AppUser.create(
        email=email, password=password, role=role, display_name=display_name,
    )
    if body.get("is_superadmin") is True:
        new_user.is_superadmin = True
    new_user_id = get_db().create_app_user(new_user)
    persisted = get_db().get_app_user(new_user_id)
    if persisted is None:
        raise ConflictError("user failed to persist")

    response = jsonify(_serialize_app_user(persisted))
    response.headers["Location"] = f"/api/v1/users/{new_user_id}"
    return response, 201


@api_bp.route("/users/<user_id>", methods=["GET"])
@api_endpoint
def get_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Read a single user by id. Superadmin-only."""
    require_superadmin()
    user = get_db().get_app_user(user_id)
    if user is None:
        raise NotFoundError(f"user {user_id} not found")
    return jsonify(_serialize_app_user(user))


@api_bp.route("/users/<user_id>", methods=["PATCH"])
@api_endpoint
def patch_user_rest(user_id: str) -> tuple[Response, int] | Response:
    """Admin-patch a user. Superadmin-only.

    Body fields (all optional):

    - ``display_name``: str | null
    - ``password``: string (≥6 chars)
    - ``role``: one of the :class:`UserRole` values
    - ``is_active``: bool — deactivate without deleting
    - ``is_superadmin``: bool — superadmins can grant/revoke this
      flag on any user including themselves; the API does not gate
      against self-demotion (the operator can recover by editing the
      DB directly if they lock themselves out).

    Email is deliberately not editable through this endpoint — email
    is the natural key for SSO matching and changing it can orphan
    OAuth-linked sessions.
    """
    require_superadmin()
    user = get_db().get_app_user(user_id)
    if user is None:
        raise NotFoundError(f"user {user_id} not found")

    body = _require_dict_body()
    if "display_name" in body:
        dn = body["display_name"]
        if dn is not None and not isinstance(dn, str):
            raise ValidationError(
                "display_name must be a string or null",
                errors=(
                    _FieldError(
                        field="display_name", code="invalid_type",
                        message="must be string",
                    ),
                ),
            )
        user.display_name = dn if isinstance(dn, str) else None
    if "password" in body:
        password = body["password"]
        if not isinstance(password, str) or len(password) < 6:
            raise ValidationError(
                "password must be at least 6 characters",
                errors=(
                    _FieldError(
                        field="password", code="too_short",
                        message="min 6 characters",
                    ),
                ),
            )
        user.set_password(password)
    if "role" in body:
        role_raw = body["role"]
        if not isinstance(role_raw, str) or role_raw not in _VALID_USER_ROLES:
            raise ValidationError(
                f"role must be one of {sorted(_VALID_USER_ROLES)}",
                errors=(
                    _FieldError(
                        field="role", code="invalid_value",
                        message="not a known role",
                    ),
                ),
            )
        user.role = UserRole(role_raw)
    if "is_active" in body:
        if not isinstance(body["is_active"], bool):
            raise ValidationError(
                "is_active must be a boolean",
                errors=(
                    _FieldError(
                        field="is_active", code="invalid_type",
                        message="must be bool",
                    ),
                ),
            )
        user.is_active = body["is_active"]
    if "is_superadmin" in body:
        if not isinstance(body["is_superadmin"], bool):
            raise ValidationError(
                "is_superadmin must be a boolean",
                errors=(
                    _FieldError(
                        field="is_superadmin", code="invalid_type",
                        message="must be bool",
                    ),
                ),
            )
        user.is_superadmin = body["is_superadmin"]

    get_db().update_app_user(user)
    refreshed = get_db().get_app_user(user_id)
    if refreshed is None:
        raise ConflictError(f"user {user_id} disappeared during update")
    return jsonify(_serialize_app_user(refreshed))


@api_bp.route("/users/<user_id>", methods=["DELETE"])
@api_endpoint
def delete_user_rest(user_id: str) -> tuple[Response, int]:
    """Admin-delete a user. Superadmin-only."""
    require_superadmin()
    user = get_db().get_app_user(user_id)
    if user is None:
        raise NotFoundError(f"user {user_id} not found")
    get_db().delete_app_user(user_id)
    return Response(status=204), 204


# --- SSO endpoints ----------------------------------------------------------
#
# The OAuth state for Microsoft (MSAL flow) and Google (Authlib) lives in
# the Flask session — the legacy callbacks read it back. To keep that
# working over the REST surface we expose:
#
#   GET  /api/v1/auth/sso/<provider>/url       — start the dance
#   GET  /api/v1/auth/sso/<provider>/callback  — JSON callback
#
# Browsers / SPAs / CLI cookiejars carry the session cookie through the
# redirect chain, so the state survives. The callback URL pattern matches
# the legacy /auth/<provider>/callback shape — sites that registered the
# legacy URL with Microsoft/Google keep working; sites that want the JSON
# response register the /api/v1 variant separately.


_SSO_PROVIDERS = frozenset({"microsoft", "google"})


def _sso_enabled(provider: str) -> bool:
    cfg = get_app_config()
    if provider == "microsoft":
        return bool(getattr(cfg, "MICROSOFT_SSO_ENABLED", False))
    if provider == "google":
        return bool(getattr(cfg, "GOOGLE_SSO_ENABLED", False))
    return False


@api_bp.route("/auth/sso/<provider>/url", methods=["GET"])
@api_endpoint
def auth_sso_url_rest(provider: str) -> tuple[Response, int] | Response:
    """Build the OAuth authorization URL for the named provider.

    Returns ``{"url": "https://login.microsoftonline.com/…"}`` — the
    client is responsible for redirecting the user to it (e.g.
    ``window.location.href = response.url``). The accompanying state
    (MSAL flow or Google session info) is written to the Flask
    session so the callback can complete the exchange.

    Errors:

    - **404** — unknown provider or provider not enabled in config
    """
    from auto_a11y.web.routes.auth import (
        get_google_auth_url, get_microsoft_auth_url,
    )

    if provider not in _SSO_PROVIDERS:
        raise NotFoundError(f"unknown SSO provider: {provider}")
    if not _sso_enabled(provider):
        raise NotFoundError(f"SSO provider {provider} is not enabled")

    cfg = get_app_config()
    if provider == "microsoft":
        redirect_uri = (
            request.url_root.rstrip("/") + cfg.MICROSOFT_REDIRECT_PATH
        )
        url = get_microsoft_auth_url(redirect_uri)
    else:
        redirect_uri = (
            request.url_root.rstrip("/") + cfg.GOOGLE_REDIRECT_PATH
        )
        url = get_google_auth_url(redirect_uri)

    return jsonify({"provider": provider, "url": url})


# ---------------------------------------------------------------------------
# Drupal sync API (§5.12) — REST shapes around the legacy
# drupal_sync_bp. The legacy blueprint stays alive for the in-flight
# admin UI (which uses NDJSON streaming progress); these REST routes
# return aggregate summaries instead so non-streaming clients have a
# single response to parse. The shared Drupal client / exporter /
# importer classes are reused so both surfaces talk to Drupal the
# same way.
#
# Auth: ADMIN/AUDITOR on the project for all action routes;
# ADMIN/AUDITOR/CLIENT on the read-only listings.
# ---------------------------------------------------------------------------


def _drupal_audit_uuid_or_400(project: Any) -> str:
    """Look up the Drupal audit UUID for a project, raising 400 on miss.

    The legacy flow flashes an error and redirects; the REST equivalent
    is a ValidationError pointing at ``drupal_audit_name``.
    """
    from auto_a11y.drupal.config import get_drupal_config

    db = get_db()
    config = get_drupal_config(db=db)
    if not config.enabled:
        raise ConflictError("Drupal integration is not enabled")

    import base64
    import requests as _req

    credentials = f"{config.username}:{config.password}"
    b64_creds = base64.b64encode(credentials.encode()).decode()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {b64_creds}",
    }
    try:
        response = _req.get(
            f"{config.base_url}/rest/open_audits?_format=json",
            headers=headers, timeout=10,
        )
        response.raise_for_status()
        audits = response.json()
    except Exception as exc:  # noqa: BLE001
        raise ConflictError(f"Could not query Drupal audits: {exc}") from exc

    audit_name = project.drupal_audit_name or project.name
    for audit in audits:
        if audit.get("title", "").lower() == audit_name.lower():
            uuid_value = audit.get("uuid") or audit.get("uuId")
            if isinstance(uuid_value, str) and uuid_value:
                return uuid_value
    raise ValidationError(
        f"No Drupal audit named {audit_name!r}",
        errors=(
            _FieldError(
                field="drupal_audit_name", code="not_found",
                message="no matching Drupal audit",
            ),
        ),
    )


def _drupal_client_or_503() -> Any:
    """Build a configured :class:`DrupalJSONAPIClient` or raise 503-ish.

    503 is the conventional shape for an unconfigured dependency, but
    we don't have a 503 helper — :class:`ConflictError` (mapped to
    409) is the closest thing in the existing API surface.
    """
    from auto_a11y.drupal import DrupalJSONAPIClient
    from auto_a11y.drupal.config import get_drupal_config

    config = get_drupal_config(db=get_db())
    if not config.enabled:
        raise ConflictError("Drupal integration is not enabled")
    return DrupalJSONAPIClient(
        base_url=config.base_url,
        username=config.username,
        password=config.password,
    )


@api_bp.route("/drupal/audits", methods=["GET"])
@api_endpoint
def drupal_list_audits_rest() -> tuple[Response, int] | Response:
    """List every audit visible in the configured Drupal instance.

    Replaces the legacy ``/drupal/audits/list`` JSON route. Authenticated;
    no project scope because the caller may be picking a Drupal audit
    to *link* to a project that doesn't yet have ``drupal_audit_name``
    set.

    Errors:

    - **409** — Drupal integration is not enabled
    - **502** — upstream Drupal returned a non-2xx (mapped to
      :class:`ConflictError` since the API doesn't have a 502 helper)
    """
    require_authenticated()
    from auto_a11y.drupal.config import get_drupal_config

    config = get_drupal_config(db=get_db())
    if not config.enabled:
        raise ConflictError("Drupal integration is not enabled")

    import base64
    import requests as _req

    credentials = f"{config.username}:{config.password}"
    b64_creds = base64.b64encode(credentials.encode()).decode()
    try:
        response = _req.get(
            f"{config.base_url}/rest/open_audits?_format=json",
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {b64_creds}",
            },
            timeout=10,
        )
        response.raise_for_status()
        audits = response.json()
    except Exception as exc:  # noqa: BLE001
        raise ConflictError(f"Drupal upstream error: {exc}") from exc

    return jsonify({
        "audits": sorted(
            [
                {
                    "title": a.get("title", ""),
                    "uuid": a.get("uuid") or a.get("uuId"),
                    "nid": a.get("nid"),
                }
                for a in audits
            ],
            key=lambda r: (r["title"] or "").lower(),
        ),
    })


@api_bp.route("/drupal/projects/<project_id>/sync-status", methods=["GET"])
@api_endpoint
def drupal_sync_status_rest(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Return per-project sync counts: pages, recordings, errors.

    Replaces the legacy ``/drupal/projects/<id>/sync/status``. Returns
    aggregates over the project's ``discovered_pages`` and
    ``recordings`` collections — synced/pending/failed counts plus the
    most-recent sync timestamp across both. Up to 5 most-recent error
    messages are included for surfacing in a sync-status banner.
    """
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=project_id,
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    from auto_a11y.drupal.config import get_drupal_config

    try:
        drupal_enabled = get_drupal_config(db=get_db()).enabled
    except Exception:  # noqa: BLE001
        drupal_enabled = False

    pages = list(get_db().discovered_pages.find({"project_id": project_id}))
    recordings = list(get_db().recordings.find({"project_id": project_id}))

    def _count(items: list[dict[str, Any]], status: str) -> int:
        return sum(
            1 for it in items if it.get("drupal_sync_status") == status
        )

    last_sync_times: list[datetime] = []
    for it in pages + recordings:
        when = it.get("drupal_last_synced")
        if isinstance(when, datetime):
            last_sync_times.append(when)
    last_sync_time = max(last_sync_times) if last_sync_times else None

    sync_errors: list[str] = []
    for p in pages:
        err = p.get("drupal_error_message")
        if isinstance(err, str) and err:
            sync_errors.append(f"Page '{p.get('title')}': {err}")
    for r in recordings:
        err = r.get("drupal_error_message")
        if isinstance(err, str) and err:
            sync_errors.append(f"Recording '{r.get('title')}': {err}")

    return jsonify({
        "drupal_enabled": drupal_enabled,
        "project_name": project.name,
        "discovered_pages": {
            "total": len(pages),
            "synced": _count(pages, "synced"),
            "pending": _count(pages, "not_synced") + _count(pages, "pending"),
            "failed": _count(pages, "sync_failed"),
        },
        "recordings": {
            "total": len(recordings),
            "synced": _count(recordings, "synced"),
            "pending": _count(recordings, "not_synced") + _count(recordings, "pending"),
            "failed": _count(recordings, "sync_failed"),
        },
        "last_sync_time": (
            last_sync_time.isoformat() if last_sync_time else None
        ),
        "sync_errors": sync_errors[:5],
    })


@api_bp.route(
    "/drupal/projects/<project_id>/discovered-pages", methods=["GET"]
)
@api_endpoint
def drupal_list_discovered_pages_rest(
    project_id: str,
) -> tuple[Response, int] | Response:
    """List discovered pages with their Drupal-sync state.

    Distinct from :func:`list_discovered_pages_for_project_rest` (the
    §5.1 / §5.3 endpoint) — that one returns the page's structural
    fields; this one surfaces the ``drupal_*`` sync fields used by the
    Drupal sync UI.
    """
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=project_id,
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    pages = list(get_db().discovered_pages.find({"project_id": project_id}))
    items: list[dict[str, Any]] = []
    for doc in pages:
        page = DiscoveredPage.from_dict(doc)
        items.append({
            "id": page.id,
            "title": page.title,
            "url": page.url,
            "interested_because": page.interested_because,
            "page_elements": page.page_elements,
            "drupal_uuid": page.drupal_uuid,
            "drupal_sync_status": page.drupal_sync_status.value,
            "drupal_last_synced": (
                page.drupal_last_synced.isoformat()
                if page.drupal_last_synced else None
            ),
            "is_synced": page.is_synced,
            "needs_sync": page.needs_sync,
        })
    return jsonify({"discovered_pages": items})


@api_bp.route(
    "/drupal/projects/<project_id>/recordings", methods=["GET"]
)
@api_endpoint
def drupal_list_recordings_rest(
    project_id: str,
) -> tuple[Response, int] | Response:
    """List the project's recordings with their Drupal-sync state."""
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=project_id,
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    docs = list(get_db().recordings.find({"project_id": project_id}))
    items: list[dict[str, Any]] = []
    for doc in docs:
        rec = Recording.from_dict(doc)
        items.append({
            "id": rec.id,
            "title": rec.title,
            "duration": rec.duration,
            "auditor_name": rec.auditor_name,
            "recording_type": rec.recording_type.value,
            "total_issues": rec.total_issues,
            "component_names": rec.component_names,
            "drupal_video_uuid": rec.drupal_video_uuid,
            "drupal_video_nid": rec.drupal_video_nid,
            "drupal_sync_status": rec.drupal_sync_status.value,
            "drupal_last_synced": (
                rec.drupal_last_synced.isoformat()
                if rec.drupal_last_synced else None
            ),
            "is_synced": rec.is_synced,
            "needs_sync": rec.needs_sync,
        })
    return jsonify({"recordings": items})


@api_bp.route("/drupal/projects/<project_id>/issues", methods=["GET"])
@api_endpoint
def drupal_list_issues_rest(
    project_id: str,
) -> tuple[Response, int] | Response:
    """List the project's Drupal-bound issues with their sync state."""
    from auto_a11y.models import Issue

    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.CLIENT,
        project_id=project_id,
    )
    if get_db().get_project(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")

    docs = list(get_db().issues.find({"project_id": project_id}))
    items: list[dict[str, Any]] = []
    for doc in docs:
        issue = Issue.from_dict(doc)
        items.append({
            "id": issue.id,
            "title": issue.title,
            "impact": issue.impact.value,
            "issue_type": issue.issue_type,
            "location_on_page": issue.location_on_page,
            "wcag_criteria": issue.wcag_criteria,
            "source_type": issue.source_type,
            "detection_method": issue.detection_method,
            "status": issue.status,
            "drupal_uuid": issue.drupal_uuid,
            "drupal_nid": issue.drupal_nid,
            "drupal_sync_status": issue.drupal_sync_status.value,
            "drupal_last_synced": (
                issue.drupal_last_synced.isoformat()
                if issue.drupal_last_synced else None
            ),
            "is_synced": issue.is_synced,
            "needs_sync": issue.needs_sync,
        })
    return jsonify({"issues": items})


def _aggregate_result() -> dict[str, Any]:
    """Empty aggregate-result envelope the action routes return."""
    return {
        "success_count": 0,
        "failure_count": 0,
        "skipped_count": 0,
        "errors": [],  # list[{item, error}]
    }


@api_bp.route(
    "/drupal/projects/<project_id>/upload", methods=["POST"]
)
@api_endpoint
def drupal_upload_rest(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Push selected discovered-pages / recordings / issues to Drupal.

    Body:

        {
          "discovered_page_ids": ["...", ...],   // optional
          "recording_ids":       ["...", ...],   // optional
          "issue_ids":           ["...", ...],   // optional
          "options": {"include_french": bool}    // optional
        }

    The action runs synchronously and returns an aggregate summary
    once the loop finishes. Unlike the legacy NDJSON streaming
    counterpart, there is no per-item progress channel — clients that
    need real-time feedback should keep using ``/drupal/projects/<id>/
    sync/upload`` on the HTML blueprint.
    """
    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id,
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    from auto_a11y.drupal import (
        DiscoveredPageExporter,
        DiscoveredPageTaxonomies,
        IssueExporter,
        RecordingExporter,
        WCAGChapterCache,
    )
    from auto_a11y.models import RecordingIssue

    body = _require_dict_body() if request.data else {}
    page_ids = _coerce_str_list(body.get("discovered_page_ids") or [])
    recording_ids = _coerce_str_list(body.get("recording_ids") or [])
    issue_ids = _coerce_str_list(body.get("issue_ids") or [])
    options_any: Any = body.get("options", {})
    options: dict[str, Any] = (
        cast(dict[str, Any], options_any)
        if isinstance(options_any, dict) else {}
    )
    include_french = bool(options.get("include_french", False))

    client = _drupal_client_or_503()
    audit_uuid = _drupal_audit_uuid_or_400(project)

    taxonomies = DiscoveredPageTaxonomies(client)
    wcag_cache = WCAGChapterCache(client)
    taxonomies.cache.get_terms("issue_type")
    taxonomies.cache.get_terms("issue_category")
    wcag_cache.get_chapters()

    page_exporter = DiscoveredPageExporter(client, taxonomies)
    recording_exporter = RecordingExporter(client)
    issue_exporter = IssueExporter(client, taxonomies.cache, wcag_cache)

    result = _aggregate_result()
    db = get_db()

    # Pages
    for page_id in page_ids:
        try:
            from bson import ObjectId
            page_doc = db.discovered_pages.find_one({"_id": ObjectId(page_id)})
            if not page_doc:
                result["failure_count"] += 1
                result["errors"].append({"item": page_id, "error": "page not found"})
                continue
            page = DiscoveredPage.from_dict(page_doc)
            res = page_exporter.export_from_discovered_page_model(page, audit_uuid)
            if res.get("success"):
                db.discovered_pages.update_one(
                    {"_id": page_doc["_id"]},
                    {"$set": {
                        "drupal_uuid": res["uuid"],
                        "drupal_sync_status": "synced",
                        "drupal_last_synced": datetime.now(),
                        "drupal_error_message": None,
                    }},
                )
                result["success_count"] += 1
            else:
                db.discovered_pages.update_one(
                    {"_id": page_doc["_id"]},
                    {"$set": {
                        "drupal_sync_status": "sync_failed",
                        "drupal_error_message": res.get("error"),
                    }},
                )
                result["failure_count"] += 1
                result["errors"].append(
                    {"item": page.title, "error": res.get("error")}
                )
        except Exception as exc:  # noqa: BLE001
            result["failure_count"] += 1
            result["errors"].append({"item": page_id, "error": str(exc)})

    # Recordings (cascade includes their RecordingIssues)
    for recording_id in recording_ids:
        try:
            from bson import ObjectId
            rec_doc = db.recordings.find_one({"_id": ObjectId(recording_id)})
            if not rec_doc:
                result["failure_count"] += 1
                result["errors"].append({"item": recording_id, "error": "recording not found"})
                continue
            recording = Recording.from_dict(rec_doc)

            discovered_page_uuids: list[str] = []
            for pid in recording.discovered_page_ids:
                try:
                    p_doc = db.discovered_pages.find_one({"_id": ObjectId(pid)})
                    if p_doc and isinstance(p_doc.get("drupal_uuid"), str):
                        discovered_page_uuids.append(p_doc["drupal_uuid"])
                except Exception:  # noqa: BLE001
                    pass

            rec_res = recording_exporter.export_from_recording_model(
                recording, audit_uuid, discovered_page_uuids,
                include_french=include_french,
            )
            if rec_res.get("success"):
                video_uuid = rec_res["uuid"]
                db.recordings.update_one(
                    {"_id": rec_doc["_id"]},
                    {"$set": {
                        "drupal_video_uuid": video_uuid,
                        "drupal_video_nid": rec_res.get("nid"),
                        "drupal_sync_status": "synced",
                        "drupal_last_synced": datetime.now(),
                        "drupal_error_message": None,
                    }},
                )
                result["success_count"] += 1

                # Cascade RecordingIssues for this recording.
                for ri_doc in db.recording_issues.find(
                    {"recording_id": recording.recording_id}
                ):
                    try:
                        ri = RecordingIssue.from_dict(ri_doc)
                        ri_res = issue_exporter.export_from_recording_issue(
                            ri, audit_uuid, video_uuid,
                        )
                        if ri_res.get("success"):
                            db.recording_issues.update_one(
                                {"_id": ri_doc["_id"]},
                                {"$set": {
                                    "drupal_uuid": ri_res["uuid"],
                                    "drupal_nid": ri_res.get("nid"),
                                    "drupal_sync_status": "synced",
                                    "drupal_last_synced": datetime.now(),
                                    "drupal_error_message": None,
                                }},
                            )
                        else:
                            db.recording_issues.update_one(
                                {"_id": ri_doc["_id"]},
                                {"$set": {
                                    "drupal_sync_status": "sync_failed",
                                    "drupal_error_message": ri_res.get("error"),
                                }},
                            )
                    except Exception:  # noqa: BLE001
                        pass
            else:
                db.recordings.update_one(
                    {"_id": rec_doc["_id"]},
                    {"$set": {
                        "drupal_sync_status": "sync_failed",
                        "drupal_error_message": rec_res.get("error"),
                    }},
                )
                result["failure_count"] += 1
                result["errors"].append(
                    {"item": recording.title, "error": rec_res.get("error")}
                )
        except Exception as exc:  # noqa: BLE001
            result["failure_count"] += 1
            result["errors"].append({"item": recording_id, "error": str(exc)})

    # Standalone issues
    for issue_id in issue_ids:
        try:
            from auto_a11y.models import Issue
            from bson import ObjectId
            issue_doc = db.issues.find_one({"_id": ObjectId(issue_id)})
            if not issue_doc:
                result["failure_count"] += 1
                result["errors"].append({"item": issue_id, "error": "issue not found"})
                continue
            issue = Issue.from_dict(issue_doc)
            i_res = issue_exporter.export_from_issue_model(issue, audit_uuid)
            if i_res.get("success"):
                db.issues.update_one(
                    {"_id": issue_doc["_id"]},
                    {"$set": {
                        "drupal_uuid": i_res["uuid"],
                        "drupal_nid": i_res.get("nid"),
                        "drupal_sync_status": "synced",
                        "drupal_last_synced": datetime.now(),
                        "drupal_error_message": None,
                    }},
                )
                result["success_count"] += 1
            else:
                db.issues.update_one(
                    {"_id": issue_doc["_id"]},
                    {"$set": {
                        "drupal_sync_status": "sync_failed",
                        "drupal_error_message": i_res.get("error"),
                    }},
                )
                result["failure_count"] += 1
                result["errors"].append(
                    {"item": issue.title, "error": i_res.get("error")}
                )
        except Exception as exc:  # noqa: BLE001
            result["failure_count"] += 1
            result["errors"].append({"item": issue_id, "error": str(exc)})

    return jsonify({
        "project_id": project_id,
        "audit_uuid": audit_uuid,
        **result,
    })


@api_bp.route(
    "/drupal/projects/<project_id>/import-pages", methods=["POST"]
)
@api_endpoint
def drupal_import_pages_rest(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Pull discovered pages from Drupal into the local database.

    Aggregate-summary REST form of the legacy NDJSON
    ``/drupal/projects/<id>/sync/import-pages``. Counts are
    ``imported`` (new local row), ``updated`` (existing local row
    keyed on ``drupal_uuid``), and ``skipped`` (failed on any page).
    """
    from auto_a11y.drupal import (
        DiscoveredPageImporter,
        DiscoveredPageTaxonomies,
    )

    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id,
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    client = _drupal_client_or_503()
    audit_uuid = _drupal_audit_uuid_or_400(project)

    taxonomies = DiscoveredPageTaxonomies(client)
    importer = DiscoveredPageImporter(client, taxonomies)
    drupal_pages = importer.fetch_discovered_pages_for_audit(audit_uuid)

    db = get_db()
    imported_count = 0
    updated_count = 0
    skipped_count = 0
    errors: list[dict[str, Any]] = []

    for drupal_page in drupal_pages:
        try:
            existing = db.discovered_pages.find_one(
                {"drupal_uuid": drupal_page["uuid"]}
            )
            if existing:
                page_model = DiscoveredPage.from_dict(existing)
                page_model.title = drupal_page["title"]
                page_model.url = drupal_page["url"]
                page_model.interested_because = drupal_page["interested_because"]
                page_model.page_elements = drupal_page["page_elements"]
                page_model.private_notes = drupal_page["private_notes"]
                page_model.public_notes = drupal_page["public_notes"]
                page_model.include_in_report = drupal_page["include_in_report"]
                page_model.audited = drupal_page["audited"]
                page_model.manual_audit = drupal_page["manual_audit"]
                page_model.document_links = drupal_page["document_links"]
                page_model.drupal_sync_status = DrupalSyncStatus.SYNCED
                page_model.drupal_last_synced = datetime.now()
                page_model.drupal_error_message = None
                db.discovered_pages.update_one(
                    {"_id": existing["_id"]}, {"$set": page_model.to_dict()},
                )
                updated_count += 1
            else:
                page_data = importer.import_to_discovered_page_model(
                    drupal_page, project_id,
                )
                page_model = DiscoveredPage(**page_data)
                db.discovered_pages.insert_one(page_model.to_dict())
                imported_count += 1
        except Exception as exc:  # noqa: BLE001
            skipped_count += 1
            errors.append(
                {"item": drupal_page.get("title", "unknown"), "error": str(exc)}
            )

    return jsonify({
        "project_id": project_id,
        "audit_uuid": audit_uuid,
        "fetched": len(drupal_pages),
        "imported": imported_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "errors": errors,
    })


@api_bp.route(
    "/drupal/projects/<project_id>/import-issues", methods=["POST"]
)
@api_endpoint
def drupal_import_issues_rest(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Pull issues from Drupal into the local database. Aggregate summary.

    Companion to :func:`drupal_import_pages_rest` for the Issue model.
    """
    from auto_a11y.drupal import IssueImporter

    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id,
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    client = _drupal_client_or_503()
    audit_uuid = _drupal_audit_uuid_or_400(project)

    importer = IssueImporter(client)
    drupal_issues = importer.fetch_issues_for_audit(audit_uuid)

    db = get_db()
    imported_count = 0
    updated_count = 0
    skipped_count = 0
    errors: list[dict[str, Any]] = []

    for drupal_issue in drupal_issues:
        try:
            existing = db.issues.find_one({"drupal_uuid": drupal_issue["uuid"]})
            if existing:
                issue_dict = importer.to_database_dict(drupal_issue, project_id)
                issue_dict["_id"] = existing["_id"]
                db.issues.update_one(
                    {"_id": existing["_id"]}, {"$set": issue_dict},
                )
                updated_count += 1
            else:
                issue_dict = importer.to_database_dict(drupal_issue, project_id)
                db.issues.insert_one(issue_dict)
                imported_count += 1
        except Exception as exc:  # noqa: BLE001
            skipped_count += 1
            errors.append(
                {"item": drupal_issue.get("title", "unknown"), "error": str(exc)}
            )

    return jsonify({
        "project_id": project_id,
        "audit_uuid": audit_uuid,
        "fetched": len(drupal_issues),
        "imported": imported_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "errors": errors,
    })


@api_bp.route(
    "/drupal/projects/<project_id>/upload-automated-results",
    methods=["POST"],
)
@api_endpoint
def drupal_upload_automated_results_rest(
    project_id: str,
) -> tuple[Response, int] | Response:
    """Upload deduplicated automated-test issues to Drupal.

    Body (all optional):

        {
          "options": {
            "min_component_pages": 2,            // pages a component must
                                                  // appear on to count as
                                                  // "common"
            "mark_pages_for_inspection": false   // mark URL-only pages for
                                                  // manual inspection
          }
        }

    The pipeline:
    1. Generates the project's comprehensive automated-test report
    2. Deduplicates issues by common component (XPath-based)
    3. Creates :class:`DiscoveredPage` rows for components and URLs
    4. Uploads each of those pages to Drupal via
       :class:`DiscoveredPageExporter`

    Returns the aggregate result — total discovered pages created and
    per-item upload outcomes.
    """
    from auto_a11y.drupal import (
        DiscoveredPageExporter, DiscoveredPageTaxonomies,
    )
    from auto_a11y.reporting.deduplication_service import (
        AutomatedTestDeduplicationService,
    )
    from auto_a11y.reporting.report_generator import ReportGenerator

    require_project_role(
        UserRole.ADMIN, UserRole.AUDITOR, project_id=project_id,
    )
    project = get_db().get_project(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")

    body = _require_dict_body() if request.data else {}
    options_any: Any = body.get("options", {})
    options: dict[str, Any] = (
        cast(dict[str, Any], options_any)
        if isinstance(options_any, dict) else {}
    )
    min_component_pages_raw = options.get("min_component_pages", 2)
    min_component_pages = (
        min_component_pages_raw
        if isinstance(min_component_pages_raw, int) else 2
    )
    mark_pages_for_inspection = bool(
        options.get("mark_pages_for_inspection", False)
    )

    db = get_db()
    client = _drupal_client_or_503()
    audit_uuid = _drupal_audit_uuid_or_400(project)

    # Step 1: build the comprehensive project report data (same shape
    # the legacy NDJSON route assembles, just inline rather than
    # streamed).
    report_gen = ReportGenerator(db, config={})
    websites = db.get_websites(project_id)
    website_data_list: list[dict[str, Any]] = []
    for website in websites:
        if not website.id:
            continue
        pages = db.get_pages(website.id)
        page_results_list: list[dict[str, Any]] = []
        for page in pages:
            if not page.id:
                continue
            tr = db.get_latest_test_result(page.id)
            if tr is not None:
                page_results_list.append({"page": page, "test_result": tr})
        website_data_list.append(
            {"website": website, "pages": page_results_list}
        )
    report_data = report_gen.prepare_project_report_data(
        project, website_data_list,
    )

    # Step 2: deduplicate via the existing service.
    dedup_service = AutomatedTestDeduplicationService(db)
    dedup_result = dedup_service.process_automated_test_results(
        project_id=project_id,
        project_data=report_data,
        min_component_pages=min_component_pages,
        mark_pages_for_inspection=mark_pages_for_inspection,
    )

    component_page_ids = dedup_result["component_page_ids"]
    page_url_ids = dedup_result["page_url_ids"]
    all_page_ids: list[str] = (
        list(component_page_ids) + list(page_url_ids)
    )

    # Step 3 + 4: upload each created DiscoveredPage to Drupal.
    taxonomies = DiscoveredPageTaxonomies(client)
    page_exporter = DiscoveredPageExporter(client, taxonomies)

    success_count = 0
    failure_count = 0
    errors: list[dict[str, Any]] = []
    from bson import ObjectId
    for page_id in all_page_ids:
        try:
            page_doc = db.discovered_pages.find_one({"_id": ObjectId(page_id)})
            if not page_doc:
                failure_count += 1
                errors.append({"item": page_id, "error": "page not found"})
                continue
            disc_page = DiscoveredPage.from_dict(page_doc)
            res = page_exporter.export_from_discovered_page_model(
                disc_page, audit_uuid,
            )
            if res.get("success"):
                db.discovered_pages.update_one(
                    {"_id": page_doc["_id"]},
                    {"$set": {
                        "drupal_uuid": res["uuid"],
                        "drupal_sync_status": "synced",
                        "drupal_last_synced": datetime.now(),
                        "drupal_error_message": None,
                    }},
                )
                success_count += 1
            else:
                db.discovered_pages.update_one(
                    {"_id": page_doc["_id"]},
                    {"$set": {
                        "drupal_sync_status": "sync_failed",
                        "drupal_error_message": res.get("error"),
                    }},
                )
                failure_count += 1
                errors.append({
                    "item": disc_page.title, "error": res.get("error"),
                })
        except Exception as exc:  # noqa: BLE001
            failure_count += 1
            errors.append({"item": page_id, "error": str(exc)})

    return jsonify({
        "project_id": project_id,
        "audit_uuid": audit_uuid,
        "upload_id": dedup_result.get("upload_id"),
        "discovered_pages_created": len(all_page_ids),
        "common_components": len(component_page_ids),
        "page_urls": len(page_url_ids),
        "success_count": success_count,
        "failure_count": failure_count,
        "errors": errors,
    })


@api_bp.route("/auth/sso/<provider>/callback", methods=["GET"])
@api_endpoint
def auth_sso_callback_rest(
    provider: str,
) -> tuple[Response, int] | Response:
    """Complete the OAuth dance for the named provider; mint a token.

    The provider redirects the user here with ``?code=…`` and
    matching state. We exchange that for ID-token claims, look up
    the matching :class:`AppUser` by email (no auto-provisioning —
    same as the legacy callbacks), and mint a Bearer token.

    Errors:

    - **404** — unknown provider or provider not enabled
    - **401** — OAuth failure / no matching user / deactivated user
    """
    from auto_a11y.web.routes.auth import (
        complete_google_auth, complete_microsoft_auth, find_sso_user,
    )

    if provider not in _SSO_PROVIDERS:
        raise NotFoundError(f"unknown SSO provider: {provider}")
    if not _sso_enabled(provider):
        raise NotFoundError(f"SSO provider {provider} is not enabled")

    cfg = get_app_config()
    if provider == "microsoft":
        redirect_uri = (
            request.url_root.rstrip("/") + cfg.MICROSOFT_REDIRECT_PATH
        )
        claims = complete_microsoft_auth(request, redirect_uri)
    else:
        redirect_uri = (
            request.url_root.rstrip("/") + cfg.GOOGLE_REDIRECT_PATH
        )
        claims = complete_google_auth(request, redirect_uri)

    if claims is None:
        raise UnauthorizedError("SSO authentication failed")

    user = find_sso_user(claims)
    if user is None:
        raise UnauthorizedError(
            "no account found for the SSO-provided email"
        )
    if not user.is_active:
        raise UnauthorizedError("account is deactivated")

    user.record_login(success=True)
    get_db().update_app_user(user)

    assert user.id is not None
    raw_token, token = mint_token(
        get_db(), user_id=user.id, description=f"sso/{provider}",
    )
    return jsonify({
        "provider": provider,
        "token": raw_token,
        "token_record": _serialize_token(token),
        "user": _serialize_app_user(user),
    })