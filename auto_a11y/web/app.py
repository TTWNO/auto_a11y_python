"""
Flask application factory
"""

from flask import Flask, render_template, jsonify, request, session, g, redirect, url_for
from flask_cors import CORS
from flask_login import LoginManager, current_user, login_required
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import logging
import atexit

from auto_a11y.core import Database
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
    desktop_bp
)
from auto_a11y.web.routes.demo import demo_bp

logger = logging.getLogger(__name__)


def create_app(config):
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

    # CSRF protection
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

    # Initialize database connection (needed before Flask-Login)
    app.db = Database(config.MONGODB_URI, config.DATABASE_NAME)

    # Warn about projects without members (pre-migration)
    try:
        empty_count = app.db.projects.count_documents({"$or": [
            {"members": {"$exists": False}},
            {"members": {"$size": 0}},
        ]})
        if empty_count > 0:
            logger.warning(
                f"{empty_count} project(s) have no members. "
                "Run 'python migrate_add_project_members.py' to populate membership."
            )
    except Exception:
        pass  # Don't block startup

    # Run group permissions migration (idempotent)
    from auto_a11y.core.migrate_groups import run_migration
    try:
        run_migration(app.db)
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
    def load_user(user_id):
        """Load user by ID for Flask-Login"""
        return app.db.get_app_user(user_id)

    # Make get_locale, config, and current_user available to all templates
    from auto_a11y.web.fluent import _get_current_locale as get_locale

    @app.context_processor
    def inject_globals():
        return dict(
            get_locale=get_locale,
            show_error_codes=config.SHOW_ERROR_CODES,
            current_user=current_user,
            microsoft_sso_enabled=config.MICROSOFT_SSO_ENABLED,
            google_sso_enabled=config.GOOGLE_SSO_ENABLED,
            smtp_enabled=config.SMTP_ENABLED,
        )
    
    # Store config for access in routes
    app.app_config = config
    
    # Initialize test configuration with database and debug mode
    from auto_a11y.config import get_test_config
    app.test_config = get_test_config(
        database=app.db,
        debug_mode=config.DEBUG
    )
    
    # Start task runner
    from auto_a11y.core.task_runner import task_runner
    task_runner.start()

    # Initialize scheduler for scheduled testing
    if config.SCHEDULER_ENABLED:
        from auto_a11y.core.scheduler import SchedulerService
        app.scheduler = SchedulerService(app.db, config)
        app.scheduler.start()
        logger.info("Scheduler service started")

        # Register shutdown handler
        def shutdown_scheduler():
            if hasattr(app, 'scheduler') and app.scheduler:
                logger.info("Shutting down scheduler...")
                app.scheduler.shutdown()

        atexit.register(shutdown_scheduler)
    else:
        app.scheduler = None
        logger.info("Scheduler is disabled")

    # Language switching route
    @app.route('/set-language/<language>')
    def set_language(language):
        """Set the user's preferred language"""
        if language in ['en', 'fr']:
            session['language'] = language
        return jsonify({'status': 'success', 'language': language})

    # Register blueprints
    app.register_blueprint(projects_bp, url_prefix='/projects')
    app.register_blueprint(websites_bp, url_prefix='/websites')
    app.register_blueprint(pages_bp, url_prefix='/pages')
    app.register_blueprint(testing_bp, url_prefix='/testing')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    app.register_blueprint(api_bp, url_prefix='/api/v1')
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

    from auto_a11y.web.routes.groups import groups_bp
    app.register_blueprint(groups_bp, url_prefix='/groups')

    # Rate limits on auth endpoints (must be after register_blueprint calls)
    limiter.limit("10/minute")(app.view_functions['auth.login'])
    limiter.limit("5/minute")(app.view_functions['auth.register'])
    if 'auth.forgot_password' in app.view_functions:
        limiter.limit("3/minute")(app.view_functions['auth.forgot_password'])

    # Desktop-mode blueprint and auto-login (only active when DESKTOP_MODE env var is set)
    if config.DESKTOP_MODE:
        app.register_blueprint(desktop_bp)

        @app.before_request
        def desktop_auto_login():
            """In desktop mode with auth disabled, auto-login as a superadmin user."""
            if not config.AUTH_ENABLED and not current_user.is_authenticated:
                from auto_a11y.models.app_user import AppUser, UserRole
                from flask_login import login_user
                desktop_user = app.db.get_app_user_by_email('desktop@auto-a11y.local')
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
                    app.db.create_app_user(new_user)
                    desktop_user = app.db.get_app_user_by_email('desktop@auto-a11y.local')
                if desktop_user:
                    login_user(desktop_user)

    # Global login requirement - protect all routes except auth, static, demo, and health
    @app.before_request
    def require_login():
        """Require login for all routes except auth, static, demo, and health endpoints"""
        allowed_endpoints = [
            'auth.login', 'auth.register', 'auth.logout',
            'auth.microsoft_login', 'auth.microsoft_callback',
            'auth.google_login', 'auth.google_callback',
            'static', 'health', 'set_language',
            'desktop.shutdown'
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
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth.login', next=request.url))

    # Template context processor - make user_has_projects available in all templates
    @app.context_processor
    def inject_user_has_projects():
        if current_user.is_authenticated and not getattr(current_user, 'is_superadmin', False):
            projects = app.db.get_projects_for_user(str(current_user.get_id()))
            return {'user_has_projects': len(projects) > 0}
        return {'user_has_projects': True}

    # Register permission helpers (user_can, is_superadmin) as template context
    from auto_a11y.core.permissions import inject_permission_helpers
    inject_permission_helpers(app)

    # Custom Jinja filters
    @app.template_filter('error_code_only')
    def error_code_only(violation_id):
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
    def wcag_understanding_url(criterion):
        """Generate WCAG 2.2 Understanding URL for a criterion"""
        from auto_a11y.reporting.wcag_mapper import format_wcag_link
        return format_wcag_link(criterion, 'understanding')

    @app.template_filter('wcag_quickref_url')
    def wcag_quickref_url(criterion):
        """Generate WCAG 2.2 Quick Reference URL for a criterion"""
        from auto_a11y.reporting.wcag_mapper import format_wcag_link
        return format_wcag_link(criterion, 'quickref')

    @app.template_filter('wcag_name')
    def wcag_name(criterion):
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
            name_parts = []
            for i, part in enumerate(parts[1:], 1):
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
    def translate_issue(text):
        """Translate issue description text via Fluent.

        Falls back to original text if no translation is found.
        """
        if not text:
            return text

        from auto_a11y.web.fluent import ftl_translate_issue
        return ftl_translate_issue(text)

    # Main routes
    @app.route('/')
    def index():
        """Home page - redirect to dashboard if logged in"""
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        return redirect(url_for('auth.login'))
    
    @app.route('/dashboard')
    @login_required
    def dashboard():
        """Main dashboard"""
        # Non-admin users only see stats for projects they are members of
        if getattr(current_user, 'is_superadmin', False):
            user_projects = app.db.get_projects()
        else:
            user_projects = app.db.get_projects_for_user(str(current_user.get_id()))

        # If user has no project access, show welcome page
        if not user_projects and not getattr(current_user, 'is_superadmin', False):
            return render_template('dashboard.html', stats=None, config=app.app_config,
                                   has_projects=False)

        # Collect project IDs to scope the stats query
        project_ids = [p.id for p in user_projects]

        # Get pages belonging to user's projects
        if getattr(current_user, 'is_superadmin', False):
            pages = list(app.db.pages.find({'status': 'tested'}))
            total_pages = app.db.pages.count_documents({})
            tested_pages = app.db.pages.count_documents({'status': 'tested'})
        else:
            # Get website IDs for user's projects
            website_ids = []
            for pid in project_ids:
                for w in app.db.get_websites(pid):
                    website_ids.append(w.id)
            if website_ids:
                pages = list(app.db.pages.find({
                    'website_id': {'$in': website_ids},
                    'status': 'tested'
                }))
                total_pages = app.db.pages.count_documents({'website_id': {'$in': website_ids}})
                tested_pages = app.db.pages.count_documents({
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
            pipeline = [
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
            latest_results = list(app.db.test_results.aggregate(pipeline))
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
        return render_template('dashboard.html', stats=stats, config=app.app_config,
                               has_projects=True)
    
    @app.route('/health')
    def health():
        """Health check endpoint"""
        return jsonify({
            'status': 'healthy',
            'database': 'connected' if app.db.client else 'disconnected'
        })
    
    @app.route('/help')
    def help():
        """Help and documentation page"""
        return render_template('help.html')

    @app.route('/screenshots/<path:filename>')
    @limiter.exempt
    def serve_screenshot(filename):
        """Serve screenshot files"""
        from flask import send_from_directory
        from pathlib import Path
        import os
        screenshots_dir = os.path.join(os.getcwd(), 'screenshots')
        file_path = Path(screenshots_dir) / filename
        if not file_path.resolve().is_relative_to(Path(screenshots_dir).resolve()):
            return jsonify({'error': 'Invalid file path'}), 403
        return send_from_directory(screenshots_dir, filename)

    # Security headers
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection'] = '0'
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
    def forbidden(error):
        """403 error handler"""
        if '/api/' in request.path:
            return jsonify({'error': 'Forbidden'}), 403
        if request.path.startswith('/t/'):
            return render_template('public/error/403.html'), 403
        return render_template('403.html'), 403

    @app.errorhandler(404)
    def not_found(error):
        """404 error handler"""
        if '/api/' in request.path:
            return jsonify({'error': 'Endpoint not found'}), 404
        return render_template('404.html'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        """500 error handler"""
        logger.error(f"Internal error: {error}")
        if '/api/' in request.path:
            return jsonify({'error': 'Internal server error'}), 500
        return render_template('500.html'), 500
    
    # Cleanup on shutdown
    @app.teardown_appcontext
    def cleanup(exception=None):
        """Cleanup resources"""
        if exception:
            logger.error(f"Request teardown with exception: {exception}")
    
    logger.info("Flask application created")
    return app