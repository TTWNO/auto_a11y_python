"""
Flask application factory
"""
from __future__ import annotations

from typing import Any

from flask import Flask, Response, render_template, jsonify, request, session, url_for
from flask_cors import CORS
from flask_login import LoginManager, current_user, login_required
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import logging
import atexit

from auto_a11y.core import Database
from auto_a11y.core import user_settings
from auto_a11y.core.preflight import get_registry
# Import for the side-effect of registering Mongo / Deepgram / Anthropic
# checks; ffmpeg + ffprobe checks register via the audio package import.
from auto_a11y.core import preflight_registrations as _preflight_registrations
from auto_a11y.audio import ffmpeg as _audio_ffmpeg
_ = (_preflight_registrations, _audio_ffmpeg)  # keep imports for side-effect
from auto_a11y.web.routes import (
    projects_bp,
    websites_bp,
    pages_bp,
    testing_bp,
    reports_bp,
    api_bp,
    scripts_bp,
    website_users_bp,
    project_users_bp,
    project_participants_bp,
    recordings_bp,
    drupal_sync_bp,
    discovered_pages_bp,
    automated_tests_bp,
    auth_bp,
    schedules_bp,
    share_tokens_bp,
    public_bp,
    members_bp,
    desktop_bp,
    pdf_bp,
    admin_settings_bp,
)
from auto_a11y.web.routes.demo import demo_bp
from auto_a11y.web.routes.recovery import recovery_bp
from auto_a11y.web.fluent import SUPPORTED_LOCALES
from auto_a11y.web.typed_app import redirect

logger = logging.getLogger(__name__)


def _build_recovery_only_app(
    app: Flask,
    config: Any,
    failures: list[Any],
) -> Flask:
    """Wire ``app`` for Settings Recovery mode and return it.

    Called from :func:`create_app` when preflight reports any failure.
    Only the recovery blueprint is registered, and a ``before_request``
    interceptor 302-redirects every non-``/recovery/`` / non-``/static``
    path to ``/recovery/`` so the user always lands on the recovery page
    until the underlying configuration is fixed and the app is restarted.
    """
    _ = config  # currently unused; kept for symmetry with create_app
    # Stash the failed CheckResults via setattr so the recovery blueprint
    # can read them as a typed ``list[CheckResult]`` rather than via the
    # untyped ``app.config`` dict.
    setattr(app, 'preflight_failures', failures)

    # CSRF was installed earlier in create_app. Look up the existing
    # extension instance and exempt the recovery blueprint from CSRF
    # so the JSON test/save endpoints work without round-tripping a
    # token through the JS fetch() calls (the page itself is unauthenticated
    # at this point — there is no Flask-Login user yet).
    csrf_ext = app.extensions.get('csrf')
    if isinstance(csrf_ext, CSRFProtect):
        csrf_ext.exempt(recovery_bp)

    # Make get_locale available to the recovery templates.
    from auto_a11y.web.fluent import get_current_locale as get_locale

    @app.context_processor
    def inject_recovery_globals() -> dict[str, Any]:
        return dict(
            get_locale=get_locale,
            show_error_codes=False,
            current_user=None,
            microsoft_sso_enabled=False,
            google_sso_enabled=False,
            smtp_enabled=False,
            user_has_projects=False,
            is_superadmin=False,
        )
    _ = inject_recovery_globals  # registered by @app.context_processor

    app.register_blueprint(recovery_bp)

    @app.before_request
    def force_recovery() -> Response | None:
        """Send every URL except ``/recovery``, ``/static`` and ``/health`` to /recovery/.

        ``/health`` is answered directly with HTTP 503 rather than redirected:
        a load balancer / uptime monitor must receive a clear unhealthy signal
        (distinct from "healthy") instead of following a 302 into an HTML
        recovery page. The recovery-only app does not register the normal
        ``/health`` handler, so we serve the signal here.
        """
        path = request.path
        if path.startswith("/recovery"):
            return None
        if path.startswith("/static"):
            return None
        if path == "/health":
            resp = jsonify({
                'status': 'recovery',
                'message': 'Settings Recovery mode: configuration must be fixed.',
            })
            resp.status_code = 503
            return resp
        return redirect("/recovery/")
    _ = force_recovery  # registered by @app.before_request

    logger.warning(
        "Flask app started in Settings Recovery mode (%d preflight failure(s)).",
        len(failures),
    )
    return app


def create_app(config: Any) -> Flask:
    """
    Create Flask application

    Args:
        config: Application configuration object

    Returns:
        Flask app instance
    """
    app = Flask(__name__, 
                template_folder='templates',
                static_folder='static')
    
    # Apply configuration
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['DEBUG'] = config.DEBUG

    # Session cookie security
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = config.SESSION_COOKIE_SECURE
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours

    # Configure CORS - only enable if origins are explicitly configured
    if config.CORS_ORIGINS:
        cors_origins = [o.strip() for o in config.CORS_ORIGINS.split(',')]
        CORS(app, resources={r"/api/*": {"origins": cors_origins}})

    # CSRF protection. Tokens stay valid for the whole session rather than
    # Flask-WTF's default 1-hour limit — a long-lived Reports page would
    # otherwise start failing POSTs with an HTML 400 ("CSRF token expired")
    # that the fetch()->json() callers can't parse.
    app.config['WTF_CSRF_TIME_LIMIT'] = None
    csrf = CSRFProtect(app)
    csrf.exempt(api_bp)     # API uses token auth, not session cookies
    csrf.exempt(demo_bp)    # Static demo site
    csrf.exempt(public_bp)  # Public share token routes (stateless)

    # Rate limiting
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[config.RATELIMIT_DEFAULT],
        storage_uri="memory://",
    )

    # Initialize Fluent (Project Fluent) — sole i18n system
    from auto_a11y.web.fluent import init_fluent
    init_fluent(app)

    # Overlay the user-settings file onto os.environ *before* preflight,
    # so that values the user saved via the Settings Recovery blueprint
    # are visible to the Mongo / Deepgram / Anthropic checks. The config
    # object on this Flask app was built earlier from os.environ; we
    # therefore also patch in the same Mongo URI override so the
    # ``Database(...)`` constructor below sees the user's choice on the
    # very first start after they save settings.
    _user_settings = user_settings.read()
    user_settings.apply_to_environment(_user_settings)
    if _user_settings.mongodb_uri:
        config.MONGODB_URI = _user_settings.mongodb_uri

    # Settings Recovery (Phase 10): if preflight detects a *required* piece
    # of configuration missing or broken (e.g. MongoDB unreachable), register
    # only the recovery blueprint and a 302-everything interceptor so the user
    # can fix things via the web UI without editing env vars by hand. The full
    # app finishes initialising only once every required check passes.
    #
    # Optional checks (Deepgram / Anthropic API keys) gate opt-in features and
    # must NOT block startup — otherwise a first-launch DMG, which ships with
    # no keys, could never reach the main UI. We log their absence so it's
    # discoverable in the logs and continue building the full app; the user
    # can add the keys later via the settings UI.
    preflight_result = get_registry().run_all()
    if preflight_result.blocking_failures:
        return _build_recovery_only_app(
            app, config, preflight_result.blocking_failures
        )
    for failure in preflight_result.optional_failures:
        logger.warning(
            "Optional preflight check %r not satisfied; the related feature is"
            + " disabled until configured. %s",
            failure.name,
            failure.remediation,
        )

    # Initialize database connection (needed before Flask-Login)
    db = Database(config.MONGODB_URI, config.DATABASE_NAME)
    setattr(app, 'db', db)

    # Warn about projects without members (pre-migration)
    try:
        empty_count = db.projects.count_documents({"$or": [
            {"members": {"$exists": False}},
            {"members": {"$size": 0}},
        ]})
        if empty_count > 0:
            logger.warning(
                f"{empty_count} project(s) have no members. Run 'python migrate_add_project_members.py' to populate membership."
            )
    except Exception:
        pass  # Don't block startup

    # Run group permissions migration (idempotent)
    from auto_a11y.core.migrate_groups import run_migration
    try:
        run_migration(db)
    except Exception as e:
        logger.error(f"Group migration failed: {e}")

    # Configure Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    from auto_a11y.web.fluent import lazy_ftl
    login_manager.login_message = lazy_ftl('common-please-log-in-to-access-this-page')
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id: str) -> Any:
        """Load user by ID for Flask-Login"""
        return db.get_app_user(user_id)

    # Make get_locale, config, and current_user available to all templates
    from auto_a11y.web.fluent import get_current_locale as get_locale

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return dict(
            get_locale=get_locale,
            show_error_codes=config.SHOW_ERROR_CODES,
            current_user=current_user,
            microsoft_sso_enabled=config.MICROSOFT_SSO_ENABLED,
            google_sso_enabled=config.GOOGLE_SSO_ENABLED,
            smtp_enabled=config.SMTP_ENABLED,
        )
    
    # Store config for access in routes
    setattr(app, 'app_config', config)

    # Initialize test configuration with database and debug mode
    from auto_a11y.config import get_test_config
    setattr(app, 'test_config', get_test_config(
        database=db,
        debug_mode=config.DEBUG
    ))
    
    # Start task runner
    from auto_a11y.core.task_runner import task_runner
    task_runner.start()

    # Initialize PDF audit runner (Phase 8.1 / 9.3) — exposed via
    # ``current_app.pdf_runner`` (see ``typed_app.get_pdf_runner``).
    from pathlib import Path as _Path
    from auto_a11y.pdf.storage import PdfStorage as _PdfStorage
    from auto_a11y.testing.pdf_runner import PdfRunner as _PdfRunner
    _pdf_storage = _PdfStorage(base_dir=_Path(config.PDF_STORAGE_DIR))
    setattr(app, 'pdf_runner', _PdfRunner(
        database=db,
        storage=_pdf_storage,
        max_parallel=config.PDF_AUDIT_MAX_PARALLEL,
        max_size_mb=config.PDF_MAX_SIZE_MB,
    ))

    # Initialize AudioA11y video runner (Phase 7 of audioA11y integration).
    # Exposed via ``current_app.audio_storage`` / ``current_app.video_runner``
    # (see ``typed_app.get_audio_storage`` / ``get_video_runner``). API keys
    # may be empty here — they are only consulted at ``VideoRunner.run`` time
    # when an actual pipeline run is started.
    from auto_a11y.audio.config import AudioConfig as _AudioConfig
    from auto_a11y.audio.runner import VideoRunner as _VideoRunner
    from auto_a11y.audio.storage import AudioStorage as _AudioStorage
    _audio_storage = _AudioStorage(root=_Path(config.AUDIO_STORAGE_DIR))
    setattr(app, 'audio_storage', _audio_storage)
    setattr(app, 'video_runner', _VideoRunner(
        db=db,
        storage=_audio_storage,
        config=_AudioConfig.from_env(),
    ))

    # Initialize scheduler for scheduled testing
    if config.SCHEDULER_ENABLED:
        from auto_a11y.core.scheduler import SchedulerService
        scheduler = SchedulerService(db, config)
        setattr(app, 'scheduler', scheduler)
        scheduler.start()
        logger.info("Scheduler service started")
    else:
        setattr(app, 'scheduler', None)
        logger.info("Scheduler is disabled")

    # Register shutdown handler — stop task runner first (waits for
    # in-flight Playwright tests to finish and close browsers), then
    # the scheduler.  Without this ordering the Python process tears
    # down while Playwright's Node.js driver still has open pipes,
    # causing an unhandled EPIPE crash.
    def _graceful_shutdown() -> None:
        logger.info("Shutting down task runner...")
        try:
            task_runner.stop()
        except Exception as e:
            logger.warning(f"Task runner shutdown error: {e}")

        _pdf_runner = getattr(app, 'pdf_runner', None)
        if _pdf_runner is not None:
            logger.info("Shutting down PDF runner...")
            try:
                _pdf_runner.shutdown()
            except Exception as e:
                logger.warning(f"PDF runner shutdown error: {e}")

        _scheduler = getattr(app, 'scheduler', None)
        if _scheduler:
            logger.info("Shutting down scheduler...")
            # wait=False so APScheduler doesn't block atexit waiting for
            # in-flight scheduled jobs to finish (Ctrl+C should be prompt).
            _scheduler.shutdown(wait=False)

    atexit.register(_graceful_shutdown)

    # Language switching route
    @app.route('/set-language/<language>')
    def set_language(language: str) -> Response:
        """Set the user's preferred language.

        Reject unsupported languages with HTTP 400 so the caller can tell
        the request was ignored instead of receiving a misleading 200
        ``success`` for a language that was never applied.
        """
        if language not in SUPPORTED_LOCALES:
            resp = jsonify({
                'status': 'error',
                'language': language,
                'message': 'Unsupported language',
                'supported': list(SUPPORTED_LOCALES),
            })
            resp.status_code = 400
            return resp
        session['language'] = language
        return jsonify({'status': 'success', 'language': language})

    # Register blueprints
    app.register_blueprint(projects_bp, url_prefix='/projects')
    app.register_blueprint(websites_bp, url_prefix='/websites')
    app.register_blueprint(pages_bp, url_prefix='/pages')
    app.register_blueprint(testing_bp, url_prefix='/testing')
    app.register_blueprint(reports_bp, url_prefix='/reports')

    # Import v1_openapi BEFORE register_blueprint(api_bp). Its module-level
    # code attaches /openapi.{json,yaml} via api_bp.add_url_rule, which
    # Flask only permits before the blueprint is registered on any app.
    from auto_a11y.web.routes import v1_openapi
    from auto_a11y.web.api.openapi.document import register_documented_views

    # Reference the views so the linter doesn't flag the import as unused.
    _ = (v1_openapi.openapi_json, v1_openapi.openapi_yaml)

    app.register_blueprint(api_bp, url_prefix='/api/v1')

    register_documented_views(app)
    app.register_blueprint(scripts_bp, url_prefix='/scripts')
    app.register_blueprint(website_users_bp, url_prefix='/users')
    app.register_blueprint(project_users_bp, url_prefix='')
    app.register_blueprint(project_participants_bp, url_prefix='')
    app.register_blueprint(recordings_bp, url_prefix='/recordings')
    app.register_blueprint(drupal_sync_bp, url_prefix='/drupal')
    app.register_blueprint(discovered_pages_bp, url_prefix='')
    app.register_blueprint(automated_tests_bp, url_prefix='/automated_tests')
    app.register_blueprint(demo_bp, url_prefix='/demo')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(schedules_bp, url_prefix='')
    app.register_blueprint(share_tokens_bp, url_prefix='/share-tokens')
    app.register_blueprint(public_bp, url_prefix='')
    app.register_blueprint(members_bp)
    # PDF routes already include /projects/, /websites/, and /pdfs/
    # prefixes in their rules — register at the root.
    app.register_blueprint(pdf_bp, url_prefix='')

    from auto_a11y.web.routes.groups import groups_bp
    app.register_blueprint(groups_bp, url_prefix='/groups')

    app.register_blueprint(admin_settings_bp, url_prefix='')

    # REST API: translate uncaught ApiError into RFC 7807 responses.
    # Registered on the app (not the blueprint) so create_app() can be
    # called multiple times in tests — Flask blueprints reject setup
    # methods after their first registration with any app.
    from auto_a11y.web.api import register_api_error_handlers
    register_api_error_handlers(app)

    # Rate limits on auth endpoints (must be after register_blueprint calls)
    limiter.limit("10/minute")(app.view_functions['auth.login'])
    limiter.limit("5/minute")(app.view_functions['auth.register'])
    if 'auth.forgot_password' in app.view_functions:
        limiter.limit("3/minute")(app.view_functions['auth.forgot_password'])

    # Desktop-mode blueprint and auto-login (only active when DESKTOP_MODE env var is set)
    if config.DESKTOP_MODE:
        app.register_blueprint(desktop_bp)

        @app.before_request
        def desktop_auto_login() -> None:
            """In desktop mode with auth disabled, auto-login as a superadmin user."""
            if not config.AUTH_ENABLED and not current_user.is_authenticated:
                from auto_a11y.models.app_user import AppUser, UserRole
                from flask_login import login_user
                desktop_user = db.get_app_user_by_email('desktop@auto-a11y.local')
                if not desktop_user:
                    new_user = AppUser(
                        email='desktop@auto-a11y.local',
                        password_hash='',
                        role=UserRole.ADMIN,
                        display_name='Desktop User',
                        is_active=True,
                        is_verified=True,
                        is_superadmin=True,
                    )
                    db.create_app_user(new_user)
                    desktop_user = db.get_app_user_by_email('desktop@auto-a11y.local')
                if desktop_user:
                    login_user(desktop_user)
        _ = desktop_auto_login  # registered by @app.before_request

    # Global login requirement - protect all routes except auth, static, demo, and health
    @app.before_request
    def require_login() -> Response | None:
        """Require login for all routes except auth, static, demo, and health endpoints"""
        allowed_endpoints = [
            'auth.login', 'auth.register', 'auth.logout',
            'auth.microsoft_login', 'auth.microsoft_callback',
            'auth.google_login', 'auth.google_callback',
            'api.auth_login_rest', 'api.auth_register_rest',
            'api.auth_forgot_password_rest', 'api.auth_reset_password_rest',
            'api.auth_sso_url_rest', 'api.auth_sso_callback_rest',
            # The dynamic OpenAPI 3.1 spec endpoints are deliberately
            # public — documenting the documentation is the whole point.
            'api.openapi_json', 'api.openapi_yaml',
            'static', 'health', 'set_language',
            'desktop.shutdown',
            # The accessibility statement (incl. how to report barriers) must be
            # reachable without an account — requiring login is itself a barrier.
            'accessibility_statement',
        ]
        if request.endpoint and request.endpoint in allowed_endpoints:
            return None
        if request.endpoint and request.endpoint.startswith('static'):
            return None
        if request.endpoint and request.endpoint.startswith('demo.'):
            return None
        if request.endpoint and request.endpoint.startswith('public.'):
            return None
        if request.path.startswith('/demo'):
            return None
        if not current_user.is_authenticated:
            if request.is_json or request.path.startswith('/api/'):
                resp = jsonify({'error': 'Authentication required'})
                resp.status_code = 401
                return resp
            return redirect(url_for('auth.login', next=request.url))
        return None

    # Template context processor - make user_has_projects available in all templates
    @app.context_processor
    def inject_user_has_projects() -> dict[str, bool]:
        if current_user.is_authenticated and not getattr(current_user, 'is_superadmin', False):
            projects = db.get_projects_for_user(str(current_user.get_id()))
            return {'user_has_projects': len(projects) > 0}
        return {'user_has_projects': True}

    # Register permission helpers (user_can, is_superadmin) as template context
    from auto_a11y.core.permissions import inject_permission_helpers
    inject_permission_helpers(app)

    # Custom Jinja filters
    @app.template_filter('error_code_only')
    def error_code_only(violation_id: str | None) -> str | None:
        """Extract just the error code from full violation ID (e.g., 'event_handlers_WarnTabindexDefaultFocus' -> 'WarnTabindexDefaultFocus')"""
        if not violation_id or '_' not in violation_id:
            return violation_id

        # Split by underscore and find the part that starts with Err/Warn/Info/Disco/AI
        parts = violation_id.split('_')
        for i, part in enumerate(parts):
            if part.startswith(('Err', 'Warn', 'Info', 'Disco', 'AI')):
                # Join from here to the end
                return '_'.join(parts[i:])

        # If no match, return original
        return violation_id

    @app.template_filter('wcag_understanding_url')
    def wcag_understanding_url(criterion: str) -> str:
        """Generate WCAG 2.2 Understanding URL for a criterion"""
        from auto_a11y.reporting.wcag_mapper import format_wcag_link
        return format_wcag_link(criterion, 'understanding')

    @app.template_filter('wcag_quickref_url')
    def wcag_quickref_url(criterion: str) -> str:
        """Generate WCAG 2.2 Quick Reference URL for a criterion"""
        from auto_a11y.reporting.wcag_mapper import format_wcag_link
        return format_wcag_link(criterion, 'quickref')

    @app.template_filter('wcag_name')
    def wcag_name(criterion: str | None) -> str | None:
        """Extract just the name from a WCAG criterion string (e.g., '2.4.8 Location (Level AAA)' -> 'Location')

        Returns translated name via Fluent (supports EN/FR).
        """
        # Handle both full format "2.4.8 Location (Level AAA)" and short format "2.4.8"
        if not criterion:
            return criterion

        # Split and check if we have a name part
        parts = str(criterion).split()
        if len(parts) >= 2:
            # Extract name (everything after the number, before the level)
            # e.g., "2.4.8 Location (Level AAA)" -> parts[1] = "Location"
            # e.g., "5.2.4 Accessibility Supported" -> parts[1:] = ["Accessibility", "Supported"]
            name_parts: list[str] = []
            for _idx, part in enumerate(parts[1:], 1):
                if part.startswith('('):  # Stop at "(Level"
                    break
                name_parts.append(part)
            english_name = ' '.join(name_parts) if name_parts else parts[0]

            # Resolve via Fluent (handles locale automatically)
            from auto_a11y.web.fluent import ftl_wcag
            result = ftl_wcag(english_name)
            # ftl_wcag returns Markup on success, plain str message ID on miss
            if result and str(result) != english_name:
                return result
            return english_name

        return criterion

    @app.template_filter('translate_issue')
    def translate_issue(text: str | None) -> str | None:
        """Translate issue description text via Fluent.

        Falls back to original text if no translation is found.
        """
        if not text:
            return text

        from auto_a11y.web.fluent import ftl_translate_issue
        return ftl_translate_issue(text)

    # Main routes
    @app.route('/')
    def index() -> Response:
        """Home page - redirect to dashboard if logged in"""
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        return redirect(url_for('auth.login'))
    
    @app.route('/dashboard')
    @login_required
    def dashboard() -> str | Response:
        """Main dashboard"""
        # Non-admin users only see stats for projects they are members of
        if getattr(current_user, 'is_superadmin', False):
            user_projects = db.get_projects()
        else:
            user_projects = db.get_projects_for_user(str(current_user.get_id()))

        # If user has no project access, show welcome page
        if not user_projects and not getattr(current_user, 'is_superadmin', False):
            return render_template('dashboard.html', stats=None, config=config,
                                   has_projects=False)

        # Collect project IDs to scope the stats query
        project_ids = [p.id for p in user_projects if p.id is not None]

        # Get pages belonging to user's projects
        if getattr(current_user, 'is_superadmin', False):
            pages = list(db.pages.find({'status': 'tested'}))
            total_pages = db.pages.count_documents({})
            tested_pages = db.pages.count_documents({'status': 'tested'})
        else:
            # Get website IDs for user's projects
            website_ids: list[str | None] = []
            for pid in project_ids:
                for w in db.get_websites(pid):
                    website_ids.append(w.id)
            if website_ids:
                pages = list(db.pages.find({
                    'website_id': {'$in': website_ids},
                    'status': 'tested'
                }))
                total_pages = db.pages.count_documents({'website_id': {'$in': website_ids}})
                tested_pages = db.pages.count_documents({
                    'website_id': {'$in': website_ids},
                    'status': 'tested'
                })
            else:
                pages = []
                total_pages = 0
                tested_pages = 0

        # Aggregate issue counts from test_results (source of truth).
        # Each tested page's latest result has violation_count, warning_count, etc.
        page_ids = [str(p['_id']) for p in pages]
        if page_ids:
            pipeline: list[dict[str, Any]] = [
                {'$match': {'page_id': {'$in': page_ids}}},
                {'$sort': {'test_date': -1}},
                {'$group': {
                    '_id': '$page_id',
                    'violation_count': {'$first': {'$ifNull': ['$violation_count', 0]}},
                    'warning_count': {'$first': {'$ifNull': ['$warning_count', 0]}},
                    'info_count': {'$first': {'$ifNull': ['$info_count', 0]}},
                    'discovery_count': {'$first': {'$ifNull': ['$discovery_count', 0]}},
                }},
            ]
            latest_results = list(db.test_results.aggregate(pipeline))
            total_violations = sum(r.get('violation_count', 0) for r in latest_results)
            total_warnings = sum(r.get('warning_count', 0) for r in latest_results)
            total_info = sum(r.get('info_count', 0) for r in latest_results)
            total_discovery = sum(r.get('discovery_count', 0) for r in latest_results)
        else:
            total_violations = 0
            total_warnings = 0
            total_info = 0
            total_discovery = 0

        stats = {
            'projects': len(user_projects),
            'total_pages': total_pages,
            'tested_pages': tested_pages,
            'total_violations': total_violations,
            'total_warnings': total_warnings,
            'total_info': total_info,
            'total_discovery': total_discovery,
        }
        return render_template('dashboard.html', stats=stats, config=config,
                               has_projects=True)
    
    @app.route('/health')
    def health() -> Response:
        """Health check endpoint"""
        return jsonify({
            'status': 'healthy',
            'database': 'connected' if db.client else 'disconnected'
        })
    
    @app.route('/help')
    def help() -> str:
        """Help and documentation page"""
        return render_template('help.html')

    @app.route('/accessibility-statement')
    def accessibility_statement() -> str:
        """Accessibility statement: conformance, measures, and known limitations"""
        return render_template('accessibility_statement.html')

    @app.route('/screenshots/<path:filename>')
    @limiter.exempt
    def serve_screenshot(filename: str) -> Response | tuple[Response, int]:
        """Serve screenshot files"""
        from flask import send_from_directory
        from pathlib import Path
        # Use the configured directory rather than cwd-relative; the test runner
        # writes to config.SCREENSHOTS_DIR (absolute) and cwd is not guaranteed
        # to match BASE_DIR (desktop mode, systemd WorkingDirectory, etc.).
        screenshots_dir = Path(config.SCREENSHOTS_DIR).resolve()
        file_path = (screenshots_dir / filename).resolve()
        if not file_path.is_relative_to(screenshots_dir):
            return jsonify({'error': 'Invalid file path'}), 403
        return send_from_directory(str(screenshots_dir), filename)

    # Security headers
    @app.after_request
    def add_security_headers(response: Response) -> Response:
        # Only set the global DENY if the route hasn't already set its own
        # X-Frame-Options (the PDF /pdfs/<id>/file route uses SAMEORIGIN so
        # the inline viewer iframe on the detail page can render).
        if 'X-Frame-Options' not in response.headers:
            response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection'] = '0'
        # Same logic for CSP — routes that need a permissive frame-ancestors
        # directive (e.g. the PDF inline viewer) set their own CSP first.
        if 'Content-Security-Policy' not in response.headers:
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://code.jquery.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data:; "
                "font-src 'self' https://cdn.jsdelivr.net"
            )
        if not config.DEBUG:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    # Error handlers
    @app.errorhandler(403)
    def forbidden(error: Exception) -> str | tuple[str, int] | tuple[Response, int]:
        """403 error handler"""
        if '/api/' in request.path:
            return jsonify({'error': 'Forbidden'}), 403
        if request.path.startswith('/t/'):
            return render_template('public/error/403.html'), 403
        return render_template('403.html'), 403

    @app.errorhandler(404)
    def not_found(error: Exception) -> tuple[str, int] | tuple[Response, int]:
        """404 error handler"""
        if '/api/' in request.path:
            return jsonify({'error': 'Endpoint not found'}), 404
        return render_template('404.html'), 404
    
    @app.errorhandler(500)
    def internal_error(error: Exception) -> tuple[str, int] | tuple[Response, int]:
        """500 error handler"""
        logger.error(f"Internal error: {error}")
        if '/api/' in request.path:
            return jsonify({'error': 'Internal server error'}), 500
        return render_template('500.html'), 500
    
    # Cleanup on shutdown
    @app.teardown_appcontext
    def cleanup(exception: BaseException | None = None) -> None:
        """Cleanup resources"""
        if exception:
            logger.error(f"Request teardown with exception: {exception}")
    
    # Ensure pyright recognises framework-registered callbacks as referenced.
    _framework_callbacks = (
        load_user, inject_globals, set_language,
        inject_user_has_projects, require_login,
        add_security_headers,
        error_code_only, wcag_understanding_url, wcag_quickref_url,
        wcag_name, translate_issue,
        index, dashboard, health, help, accessibility_statement, serve_screenshot,
        forbidden, not_found, internal_error, cleanup,
    )
    del _framework_callbacks

    logger.info("Flask application created")
    return app