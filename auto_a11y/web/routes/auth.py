"""
Authentication routes for user login, logout, and registration
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any, cast

from flask import Blueprint, Response, render_template, url_for, flash, request, current_app, session, g, abort, jsonify
from urllib.parse import urlparse
from flask_login import login_user, logout_user, login_required, current_user
from auto_a11y.web.fluent import ftl
from functools import wraps
from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer, BadSignature, SignatureExpired
from auto_a11y.models import AppUser, UserRole
from auto_a11y.core.permissions import permission_required
from auto_a11y.core.email import send_email
from auto_a11y.web.typed_app import get_db, get_app_config, redirect

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


def role_required(*roles: UserRole) -> Callable[..., Any]:
    """Legacy decorator -- now checks is_superadmin for admin, otherwise passes."""
    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            if not current_user.is_authenticated:
                flash(ftl('common-please-log-in-to-access-this-page'), 'warning')
                return redirect(url_for('auth.login', next=request.url))
            if getattr(current_user, 'is_superadmin', False):
                return f(*args, **kwargs)
            flash(ftl('common-you-do-not-have-permission-to-access-this-page'), 'danger')
            return redirect(url_for('index'))
        return decorated_function
    return decorator


def admin_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Legacy decorator -- requires superadmin."""
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated:
            flash(ftl('common-please-log-in-to-access-this-page'), 'warning')
            return redirect(url_for('auth.login', next=request.url))
        if not getattr(current_user, 'is_superadmin', False):
            flash(ftl('auth-administrator-access-required'), 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def auditor_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Legacy decorator -- requires superadmin or projects:create permission."""
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated:
            flash(ftl('common-please-log-in-to-access-this-page'), 'warning')
            return redirect(url_for('auth.login', next=request.url))
        if getattr(current_user, 'is_superadmin', False):
            return f(*args, **kwargs)
        # For non-superadmins, check if they have projects:create (auditor-level)
        from auto_a11y.core.permissions import user_has_global_permission
        if user_has_global_permission(current_user, 'projects', 'create'):
            return f(*args, **kwargs)
        flash(ftl('auth-auditor-access-required'), 'danger')
        return redirect(url_for('index'))
    return decorated_function


# ------------------------------------------------------------------
# Per-project/website permission helpers
# ------------------------------------------------------------------

def _get_db() -> Any:
    """Get database instance from current app."""
    return get_db()


def get_effective_role(user: Any, request_obj: Any = None, project_id: str | None = None, website_id: str | None = None, page_id: str | None = None) -> UserRole | None:
    """Legacy function -- returns UserRole for backward compat.
    Used by templates to determine is_project_admin etc.
    """
    if getattr(user, 'is_superadmin', False):
        return UserRole.ADMIN

    from auto_a11y.core.permissions import user_has_permission
    db = _get_db()

    # Resolve page to website
    if page_id and not website_id:
        page = db.get_page(page_id)
        if not page:
            return None
        website_id = page.website_id

    if website_id and not project_id:
        website = db.get_website(website_id)
        if not website:
            return None
        project_id = website.project_id

    if not project_id:
        return None

    # Map permission levels to legacy roles
    if user_has_permission(user, project_id, 'project_members', 'delete'):
        return UserRole.ADMIN
    if user_has_permission(user, project_id, 'test_results', 'create'):
        return UserRole.AUDITOR
    if user_has_permission(user, project_id, 'projects', 'read'):
        return UserRole.CLIENT
    return None


def project_role_required(*roles: UserRole) -> Callable[..., Any]:
    """Legacy decorator -- checks group permissions instead of roles."""
    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            if not current_user.is_authenticated:
                if request.is_json:
                    return jsonify({'error': ftl('common-authentication-required')}), 401
                flash(ftl('common-please-log-in-to-access-this-page'), 'warning')
                return redirect(url_for('auth.login', next=request.url))

            if getattr(current_user, 'is_superadmin', False):
                g.effective_role = UserRole.ADMIN
                return f(*args, **kwargs)

            project_id = kwargs.get('project_id')
            website_id = kwargs.get('website_id')
            page_id = kwargs.get('page_id')

            effective_role = get_effective_role(
                current_user, request, project_id, website_id, page_id
            )

            if effective_role not in roles:
                if request.is_json:
                    return jsonify({'error': ftl('common-insufficient-permissions')}), 403
                flash(ftl('common-you-do-not-have-permission-to-access-this-resource'), 'danger')
                abort(403)

            g.effective_role = effective_role
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def project_admin_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Legacy decorator -- checks project_members:delete permission."""
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated:
            if request.is_json:
                return jsonify({'error': ftl('common-authentication-required')}), 401
            flash(ftl('common-please-log-in-to-access-this-page'), 'warning')
            return redirect(url_for('auth.login', next=request.url))

        if getattr(current_user, 'is_superadmin', False):
            return f(*args, **kwargs)

        from auto_a11y.core.permissions import user_has_permission, resolve_project_id
        project_id = resolve_project_id(**kwargs)

        if project_id and user_has_permission(current_user, project_id, 'project_members', 'delete'):
            return f(*args, **kwargs)

        if request.is_json:
            return jsonify({'error': ftl('auth-project-admin-access-required')}), 403
        flash(ftl('auth-project-administrator-access-required'), 'danger')
        abort(403)
    return decorated_function


# ------------------------------------------------------------------
# Token validation helpers
# ------------------------------------------------------------------

def validate_token(token_string: str) -> dict[str, Any] | None:
    """
    Validate a share-link token.
    Uses URLSafeSerializer (not TimedSerializer -- expiry is checked via
    the DB ``expires_at`` field).
    Returns ``{scope, scope_id}`` on success or ``None``.
    """
    secret_key = get_app_config().SECRET_KEY
    serializer = URLSafeSerializer(
        secret_key,
        salt=get_app_config().TOKEN_SALT,
    )
    try:
        serializer.loads(token_string)
    except BadSignature:
        return None

    token_hash = hashlib.sha256(token_string.encode('utf-8')).hexdigest()
    token = get_db().get_share_token_by_hash(token_hash)
    if token is None or not token.is_valid:
        return None

    get_db().record_token_use(token_hash)
    return {'scope': token.scope, 'scope_id': token.scope_id}


def require_access(f: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator that gates access to public routes.
    Checks for a ``token`` URL parameter first, then falls back to
    ``current_user.is_authenticated``.
    Sets ``g.access_scope`` and ``g.access_scope_id`` for downstream scope enforcement.
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        token_string = kwargs.get('token')

        if token_string:
            result = validate_token(token_string)
            if result is None:
                abort(403)
            g.access_scope = result['scope']
            g.access_scope_id = result['scope_id']
            return f(*args, **kwargs)

        if current_user.is_authenticated:
            g.access_scope = None
            g.access_scope_id = None
            return f(*args, **kwargs)

        return redirect(url_for('auth.login', next=request.url))

    return decorated


def check_scope(scope_type: str, scope_id: str) -> None:
    """
    Verify the current token/login grants access to the requested resource.
    Aborts with 403 if access is denied.
    """
    from auto_a11y.models import TokenScope

    # Logged-in user (no token) -- allow all for MVP
    if g.access_scope is None:
        return

    if g.access_scope == TokenScope.PROJECT:
        if scope_type == 'project' and scope_id == g.access_scope_id:
            return
        if scope_type in ('website', 'page'):
            if scope_type == 'website':
                website = get_db().get_website(scope_id)
                if website and website.project_id == g.access_scope_id:
                    return
            elif scope_type == 'page':
                page = get_db().get_page(scope_id)
                if page:
                    website = get_db().get_website(page.website_id)
                    if website and website.project_id == g.access_scope_id:
                        return

    elif g.access_scope == TokenScope.WEBSITE:
        if scope_type == 'website' and scope_id == g.access_scope_id:
            return
        if scope_type == 'page':
            page = get_db().get_page(scope_id)
            if page and page.website_id == g.access_scope_id:
                return

    abort(403)


# ------------------------------------------------------------------
# Password reset helpers
# ------------------------------------------------------------------

PASSWORD_RESET_SALT = 'password-reset'
PASSWORD_RESET_MAX_AGE = 900  # 15 minutes


def generate_reset_token(email: str) -> str:
    """Generate a signed, time-limited password reset token."""
    secret = get_app_config().SECRET_KEY
    serializer = URLSafeTimedSerializer(secret)
    return serializer.dumps(email, salt=PASSWORD_RESET_SALT)


def verify_reset_token(token: str) -> str | None:
    """
    Verify a password reset token.
    Returns the email on success, None on failure.
    """
    secret2 = get_app_config().SECRET_KEY
    serializer = URLSafeTimedSerializer(secret2)
    try:
        email = serializer.loads(token, salt=PASSWORD_RESET_SALT, max_age=PASSWORD_RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return str(email)


def send_password_reset_email(user: AppUser) -> bool:
    """Send a password reset email to the given user."""
    config = get_app_config()
    token = generate_reset_token(user.email)
    reset_url = url_for('auth.reset_password', token=token, _external=True)

    text_body = render_template(
        'email/password_reset.txt',
        user_name=user.display_name,
        reset_url=reset_url,
        from_name=config.SMTP_FROM_NAME,
    )
    html_body = render_template(
        'email/password_reset.html',
        user_name=user.display_name,
        reset_url=reset_url,
        from_name=config.SMTP_FROM_NAME,
    )

    return send_email(
        config,
        to=user.email,
        subject=ftl('auth-password-reset-request'),
        text_body=text_body,
        html_body=html_body,
    )


# ------------------------------------------------------------------
# Microsoft SSO helpers
# ------------------------------------------------------------------

def get_msal_app() -> Any:
    """Create a ConfidentialClientApplication for Microsoft SSO."""
    import msal
    config = get_app_config()
    return msal.ConfidentialClientApplication(
        config.MICROSOFT_CLIENT_ID,
        authority=config.MICROSOFT_AUTHORITY,
        client_credential=config.MICROSOFT_CLIENT_SECRET,
    )


def get_microsoft_auth_url(redirect_uri: str) -> str:
    """Build the Microsoft authorization URL and store state in session."""
    msal_app = get_msal_app()
    flow = msal_app.initiate_auth_code_flow(
        scopes=get_app_config().MICROSOFT_SCOPE,
        redirect_uri=redirect_uri,
    )
    session['msal_flow'] = flow
    return str(flow['auth_uri'])


def complete_microsoft_auth(auth_request: Any, redirect_uri: str) -> dict[str, str] | None:
    """
    Exchange the Microsoft authorization code for tokens.
    Returns a dict of user claims on success, or None on failure.
    """
    flow = session.pop('msal_flow', None)
    if flow is None:
        return None

    msal_app = get_msal_app()
    result = msal_app.acquire_token_by_auth_code_flow(
        flow,
        auth_request.args,
    )

    if 'error' in result:
        logger.warning('Microsoft SSO token error: %s - %s',
                        result.get('error'), result.get('error_description'))
        return None

    claims = result.get('id_token_claims', {})
    email = claims.get('preferred_username') or claims.get('email')
    if not email:
        logger.warning('Microsoft SSO: no email in id_token_claims')
        return None

    return {
        'email': email.lower(),
        'name': claims.get('name', ''),
        'provider': 'microsoft',
        'provider_id': claims.get('oid', ''),
    }


# ------------------------------------------------------------------
# Google SSO helpers
# ------------------------------------------------------------------

def _google_flow(redirect_uri: str) -> Any:
    """Create a Google OAuth2 flow."""
    import os
    from google_auth_oauthlib.flow import Flow
    config = get_app_config()
    # Allow http:// redirect URIs in local development (debug mode only)
    if current_app.debug:
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    # Google returns fully-qualified scope URLs that differ from the
    # shorthand names we request; accept the changed scopes.
    os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
    return Flow.from_client_config(
        {
            'web': {
                'client_id': config.GOOGLE_CLIENT_ID,
                'client_secret': config.GOOGLE_CLIENT_SECRET,
                'auth_uri': 'https://accounts.google.com/o/oauth2/v2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        },
        scopes=['openid', 'email', 'profile'],
        redirect_uri=redirect_uri,
    )


def get_google_auth_url(redirect_uri: str) -> str:
    """Build the Google authorization URL and store state in session."""
    flow = _google_flow(redirect_uri)
    auth_url, state = flow.authorization_url(prompt='select_account')
    session['google_oauth_state'] = state
    # PKCE: the library generates a code_verifier automatically;
    # persist it so the callback flow can send it with the token request.
    session['google_code_verifier'] = flow.code_verifier
    return str(auth_url)


def complete_google_auth(auth_request: Any, redirect_uri: str) -> dict[str, str] | None:
    """
    Exchange the Google authorization code for tokens.
    Returns a dict of user claims on success, or None on failure.
    """
    state = session.pop('google_oauth_state', None)
    if state is None:
        return None

    code_verifier = session.pop('google_code_verifier', None)

    try:
        flow = _google_flow(redirect_uri)
        flow.code_verifier = code_verifier
        flow.fetch_token(authorization_response=auth_request.url)
    except Exception:
        logger.warning('Google SSO token exchange failed', exc_info=True)
        return None

    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    try:
        _verify = getattr(google_id_token, 'verify_oauth2_token')
        claims: dict[str, Any] = _verify(
            flow.credentials.id_token,
            google_requests.Request(),
            get_app_config().GOOGLE_CLIENT_ID,
        )
    except ValueError:
        logger.warning('Google SSO: invalid id_token', exc_info=True)
        return None

    email = claims.get('email')
    if not email:
        logger.warning('Google SSO: no email in id_token')
        return None

    return {
        'email': email.lower(),
        'name': claims.get('name', ''),
        'provider': 'google',
        'provider_id': claims.get('sub', ''),
    }


# ------------------------------------------------------------------
# Shared SSO user management
# ------------------------------------------------------------------

def find_sso_user(claims: dict[str, str]) -> AppUser | None:
    """
    Look up AppUser by email.  Returns None if the email is not in
    the database — callers should redirect to the contact-us page.
    For existing users that haven't used SSO before, link their
    sso_provider/sso_id fields.
    """
    db = get_db()
    user = db.get_app_user_by_email(claims['email'])

    if user is None:
        logger.info('SSO login attempt with unknown email (%s): %s',
                     claims['provider'], claims['email'])
        return None

    # Link SSO if not already set
    if not user.sso_provider:
        user.sso_provider = claims['provider']
        user.sso_id = claims['provider_id']
        db.update_app_user(user)
        logger.info('Linked %s SSO to existing user: %s', claims['provider'], user.email)

    return user


@auth_bp.route('/contact')
def contact() -> str:
    """Contact us page — shown when an SSO user has no account."""
    return render_template('auth/contact.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login() -> str | Response:
    """User login page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False) == 'on'
        
        if not email or not password:
            flash(ftl('auth-please-enter-both-email-and-password'), 'danger')
            return render_template('auth/login.html')
        
        user = get_db().get_app_user_by_email(email)
        
        if user is None:
            flash(ftl('auth-invalid-email-or-password'), 'danger')
            return render_template('auth/login.html')
        
        if user.is_locked():
            flash(ftl('auth-account-is-temporarily-locked-please-try-again'), 'danger')
            return render_template('auth/login.html')
        
        if not user.is_active:
            flash(ftl('auth-your-account-has-been-deactivated-please-contact'), 'danger')
            return render_template('auth/login.html')
        
        if not user.check_password(password):
            user.record_login(success=False)
            get_db().update_app_user(user)
            flash(ftl('auth-invalid-email-or-password'), 'danger')
            return render_template('auth/login.html')
        
        user.record_login(success=True)
        get_db().update_app_user(user)
        
        login_user(user, remember=remember)

        # Accept ``next`` from either the form body or the query string.
        # The form action preserves ``next`` in both, but checking both
        # also tolerates Flask-Login's default ``?next=`` redirect, which
        # passes the full absolute URL (scheme + host + path).
        next_page = request.form.get('next') or request.args.get('next')
        if next_page:
            parsed = urlparse(next_page)
            host_url = urlparse(request.host_url)
            is_relative = (
                parsed.path.startswith('/')
                and not parsed.netloc
                and not parsed.scheme
            )
            is_same_origin = (
                parsed.scheme in ('http', 'https')
                and parsed.netloc == host_url.netloc
                and parsed.path.startswith('/')
            )
            if is_relative or is_same_origin:
                return redirect(next_page)

        return redirect(url_for('dashboard'))
    
    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout() -> Response:
    """User logout"""
    logout_user()
    flash(ftl('auth-you-have-been-logged-out'), 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/register', methods=['GET', 'POST'])
def register() -> str | Response:
    """User registration page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    admin_count = get_db().count_app_users(role=UserRole.ADMIN)
    is_first_user = admin_count == 0
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        display_name = request.form.get('display_name', '').strip()
        password_hint = request.form.get('password_hint', '').strip()

        errors: list[str] = []

        if not email:
            errors.append(ftl('auth-email-is-required'))
        elif '@' not in email:
            errors.append(ftl('auth-please-enter-a-valid-email-address'))

        if not password:
            errors.append(ftl('auth-password-is-required'))
        elif len(password) < 8:
            errors.append(ftl('auth-password-must-be-at-least-8-characters'))

        if password != confirm_password:
            errors.append(ftl('auth-passwords-do-not-match'))

        if get_db().app_user_exists(email):
            errors.append(ftl('auth-an-account-with-this-email-already-exists'))

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('auth/register.html', is_first_user=is_first_user)
        
        role = UserRole.ADMIN if is_first_user else UserRole.CLIENT

        user = AppUser.create(
            email=email,
            password=password,
            role=role,
            display_name=display_name or None
        )
        user.password_hint = password_hint or None
        if is_first_user:
            user.is_superadmin = True

        try:
            get_db().create_app_user(user)
            
            if is_first_user:
                flash(ftl('auth-admin-account-created-successfully-please-log-in'), 'success')
            else:
                flash(ftl('auth-account-created-successfully-please-log-in'), 'success')
            
            return redirect(url_for('auth.login'))
        
        except ValueError as e:
            flash(str(e), 'danger')
            return render_template('auth/register.html', is_first_user=is_first_user)
    
    return render_template('auth/register.html', is_first_user=is_first_user)


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password() -> str | Response:
    """Self-service password reset -- sends a reset link via email."""
    if not get_app_config().SMTP_ENABLED:
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        if email:
            user = get_db().get_app_user_by_email(email)
            if user and user.is_active:
                if get_app_config().SMTP_ENABLED:
                    send_password_reset_email(user)
                else:
                    logger.warning('Password reset requested but SMTP is not configured')

        # Always show the same message to prevent email enumeration
        flash(ftl('auth-if-that-email-address-is-in-our-system-we-have'), 'info')
        return redirect(url_for('auth.forgot_password'))

    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token: str) -> str | Response:
    """Set a new password using a valid reset token."""
    if not get_app_config().SMTP_ENABLED:
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    email = verify_reset_token(token)
    if email is None:
        flash(ftl('auth-this-password-reset-link-is-invalid-or-has-expired'), 'danger')
        return redirect(url_for('auth.forgot_password'))

    user = get_db().get_app_user_by_email(email)
    if user is None or not user.is_active:
        flash(ftl('auth-this-password-reset-link-is-invalid-or-has-expired'), 'danger')
        return redirect(url_for('auth.forgot_password'))

    # Reject token if it was generated before the last reset
    if user.password_reset_at:
        secret3 = get_app_config().SECRET_KEY
        serializer = URLSafeTimedSerializer(secret3)
        try:
            _email, timestamp = serializer.loads_unsafe(token, salt=PASSWORD_RESET_SALT)
        except Exception:
            flash(ftl('auth-this-password-reset-link-is-invalid-or-has-expired'), 'danger')
            return redirect(url_for('auth.forgot_password'))
        from datetime import datetime, timezone
        token_created = datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
        if token_created < user.password_reset_at:
            flash(ftl('auth-this-password-reset-link-has-already-been-used'), 'danger')
            return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if len(password) < 8:
            flash(ftl('auth-password-must-be-at-least-8-characters'), 'danger')
            return render_template('auth/reset_password.html', token=token)

        if password != confirm_password:
            flash(ftl('auth-passwords-do-not-match'), 'danger')
            return render_template('auth/reset_password.html', token=token)

        from datetime import datetime
        user.set_password(password)
        user.password_reset_at = datetime.now()
        user.failed_login_count = 0
        user.locked_until = None
        user.update_timestamp()
        get_db().update_app_user(user)

        flash(ftl('auth-your-password-has-been-reset-please-log-in'), 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile() -> str | Response:
    """User profile page"""
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_profile':
            display_name = request.form.get('display_name', '').strip()
            current_user.display_name = display_name or None
            current_user.update_timestamp()
            get_db().update_app_user(cast(AppUser, current_user))
            flash(ftl('auth-profile-updated-successfully'), 'success')
        
        elif action == 'change_password':
            current_password = request.form.get('current_password', '')
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')
            password_hint = request.form.get('password_hint', '').strip()

            if not current_user.check_password(current_password):
                flash(ftl('auth-current-password-is-incorrect'), 'danger')
            elif len(new_password) < 8:
                flash(ftl('auth-new-password-must-be-at-least-8-characters'), 'danger')
            elif new_password != confirm_password:
                flash(ftl('auth-new-passwords-do-not-match'), 'danger')
            else:
                current_user.set_password(new_password)
                current_user.password_hint = password_hint or None
                current_user.update_timestamp()
                get_db().update_app_user(cast(AppUser, current_user))
                flash(ftl('auth-password-changed-successfully'), 'success')
        
        return redirect(url_for('auth.profile'))
    
    return render_template('auth/profile.html')


@auth_bp.route('/users')
@permission_required('users', 'read')
def user_list() -> str:
    """List all users"""
    users = get_db().get_app_users()
    return render_template('auth/user_list.html', users=users)


@auth_bp.route('/users/create', methods=['GET', 'POST'])
@permission_required('users', 'create')
def user_create() -> str | Response:
    """Create a new user"""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        display_name = request.form.get('display_name', '').strip()
        password_hint = request.form.get('password_hint', '').strip()

        errors: list[str] = []

        if not email or '@' not in email:
            errors.append(ftl('auth-please-enter-a-valid-email-address'))

        if not password or len(password) < 8:
            errors.append(ftl('auth-password-must-be-at-least-8-characters'))

        if get_db().app_user_exists(email):
            errors.append(ftl('auth-an-account-with-this-email-already-exists'))

        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('auth/user_create.html')

        user = AppUser.create(
            email=email,
            password=password,
            role=UserRole.CLIENT,
            display_name=display_name or None
        )
        user.password_hint = password_hint or None
        user.is_verified = True

        try:
            get_db().create_app_user(user)
            flash(ftl('auth-user-created-successfully'), 'success')
            return redirect(url_for('auth.user_list'))
        except ValueError as e:
            flash(str(e), 'danger')

    return render_template('auth/user_create.html')


@auth_bp.route('/users/<user_id>/edit', methods=['GET', 'POST'])
@permission_required('users', 'update')
def user_edit(user_id: str) -> str | Response:
    """Edit a user"""
    user = get_db().get_app_user(user_id)
    if not user:
        flash(ftl('auth-user-not-found'), 'danger')
        return redirect(url_for('auth.user_list'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update':
            user.display_name = request.form.get('display_name', '').strip() or None
            user.is_active = request.form.get('is_active') == 'on'
            # Only superadmins can toggle superadmin
            if getattr(current_user, 'is_superadmin', False):
                user.is_superadmin = request.form.get('is_superadmin') == 'on'
            user.update_timestamp()
            get_db().update_app_user(user)
            flash(ftl('auth-user-updated-successfully'), 'success')

        elif action == 'reset_password':
            new_password = request.form.get('new_password', '')
            password_hint = request.form.get('password_hint', '').strip()
            if len(new_password) < 8:
                flash(ftl('auth-password-must-be-at-least-8-characters'), 'danger')
            else:
                user.set_password(new_password)
                user.password_hint = password_hint or None
                user.failed_login_count = 0
                user.locked_until = None
                user.update_timestamp()
                get_db().update_app_user(user)
                flash(ftl('auth-password-reset-successfully'), 'success')

        elif action == 'unlock':
            user.failed_login_count = 0
            user.locked_until = None
            user.update_timestamp()
            get_db().update_app_user(user)
            flash(ftl('auth-account-unlocked'), 'success')

        elif action == 'send_reset_email':
            if get_app_config().SMTP_ENABLED:
                if send_password_reset_email(user):
                    flash(ftl('auth-password-reset-email-sent-to-email', email=user.email), 'success')
                else:
                    flash(ftl('auth-failed-to-send-password-reset-email-check-smtp'), 'danger')
            else:
                flash(ftl('auth-smtp-is-not-configured-cannot-send-email'), 'danger')

        return redirect(url_for('auth.user_edit', user_id=user_id))

    # Build project membership data for display
    user_projects: list[dict[str, Any]] = []
    projects = get_db().get_projects_for_user(user_id)
    all_groups = get_db().get_all_groups()
    group_map = {g.id: g.name for g in all_groups}
    for p in projects:
        member = next((m for m in p.members if m.user_id == user_id), None)
        if member:
            group_names = [group_map.get(gid, '?') for gid in member.group_ids]
            user_projects.append({
                'id': p.id, 'name': p.name, 'group_names': group_names
            })

    return render_template('auth/user_edit.html', user=user, user_projects=user_projects,
                           smtp_enabled=get_app_config().SMTP_ENABLED)


@auth_bp.route('/users/<user_id>/delete', methods=['POST'])
@permission_required('users', 'delete')
def user_delete(user_id: str) -> Response:
    """Delete a user"""
    if current_user.id == user_id:
        flash(ftl('auth-you-cannot-delete-your-own-account'), 'danger')
        return redirect(url_for('auth.user_list'))
    
    user = get_db().get_app_user(user_id)
    if not user:
        flash(ftl('auth-user-not-found'), 'danger')
        return redirect(url_for('auth.user_list'))

    # Remove user from all project memberships before deleting
    projects = get_db().get_projects_for_user(user_id)
    for project in projects:
        if project.id:
            get_db().remove_project_member(project.id, user_id)

    get_db().delete_app_user(user_id)
    flash(ftl('auth-user-deleted-successfully'), 'success')
    return redirect(url_for('auth.user_list'))


# ------------------------------------------------------------------
# Microsoft SSO routes
# ------------------------------------------------------------------

@auth_bp.route('/login/microsoft')
def microsoft_login() -> Response:
    """Redirect user to Microsoft login page."""
    if not get_app_config().MICROSOFT_SSO_ENABLED:
        abort(404)
    redirect_uri = request.url_root.rstrip('/') + get_app_config().MICROSOFT_REDIRECT_PATH
    auth_url = get_microsoft_auth_url(redirect_uri)
    return redirect(auth_url)


@auth_bp.route('/microsoft/callback')
def microsoft_callback() -> Response:
    """Handle the OAuth callback from Microsoft."""
    if not get_app_config().MICROSOFT_SSO_ENABLED:
        abort(404)

    redirect_uri = request.url_root.rstrip('/') + get_app_config().MICROSOFT_REDIRECT_PATH
    claims = complete_microsoft_auth(request, redirect_uri)

    if claims is None:
        flash(ftl('auth-microsoft-sign-in-failed-please-try-again'), 'danger')
        return redirect(url_for('auth.login'))

    user = find_sso_user(claims)

    if user is None:
        flash(ftl('auth-no-account-found-for-that-email-please-contact-us'), 'warning')
        return redirect(url_for('auth.contact'))

    if not user.is_active:
        flash(ftl('auth-your-account-has-been-deactivated'), 'danger')
        return redirect(url_for('auth.login'))

    user.record_login(success=True)
    get_db().update_app_user(user)
    login_user(user)
    return redirect(url_for('dashboard'))


# ------------------------------------------------------------------
# Google SSO routes
# ------------------------------------------------------------------

@auth_bp.route('/login/google')
def google_login() -> Response:
    """Redirect user to Google login page."""
    if not get_app_config().GOOGLE_SSO_ENABLED:
        abort(404)
    redirect_uri = request.url_root.rstrip('/') + get_app_config().GOOGLE_REDIRECT_PATH
    auth_url = get_google_auth_url(redirect_uri)
    return redirect(auth_url)


@auth_bp.route('/google/callback')
def google_callback() -> Response:
    """Handle the OAuth callback from Google."""
    if not get_app_config().GOOGLE_SSO_ENABLED:
        abort(404)

    redirect_uri = request.url_root.rstrip('/') + get_app_config().GOOGLE_REDIRECT_PATH
    claims = complete_google_auth(request, redirect_uri)

    if claims is None:
        flash(ftl('auth-google-sign-in-failed-please-try-again'), 'danger')
        return redirect(url_for('auth.login'))

    user = find_sso_user(claims)

    if user is None:
        flash(ftl('auth-no-account-found-for-that-email-please-contact-us'), 'warning')
        return redirect(url_for('auth.contact'))

    if not user.is_active:
        flash(ftl('auth-your-account-has-been-deactivated'), 'danger')
        return redirect(url_for('auth.login'))

    user.record_login(success=True)
    get_db().update_app_user(user)
    login_user(user)
    return redirect(url_for('dashboard'))
