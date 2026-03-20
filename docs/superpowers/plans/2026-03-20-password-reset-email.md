# Password Reset via Email Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add self-service and admin-triggered password reset via email with signed time-limited tokens.

**Architecture:** URLSafeTimedSerializer generates a 15-minute token containing the user's email. A `password_reset_at` timestamp on the user model invalidates tokens after use. Email is sent via Python's `smtplib` with SMTP_ prefixed config vars. Both a "Forgot password?" self-service flow and an admin "Send reset email" button are provided.

**Tech Stack:** Flask, itsdangerous (URLSafeTimedSerializer), smtplib, email.mime, Jinja2, MongoDB

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `config.py` | Add SMTP_ configuration variables |
| Modify | `.env.example` | Document SMTP_ env vars |
| Modify | `auto_a11y/models/app_user.py` | Add `password_reset_at` field |
| Create | `auto_a11y/core/email.py` | SMTP email sending |
| Create | `auto_a11y/web/templates/email/password_reset.html` | HTML email template |
| Create | `auto_a11y/web/templates/email/password_reset.txt` | Plain text email template |
| Modify | `auto_a11y/web/routes/auth.py` | Add forgot/reset password routes |
| Create | `auto_a11y/web/templates/auth/forgot_password.html` | Email entry form |
| Create | `auto_a11y/web/templates/auth/reset_password.html` | New password form |
| Modify | `auto_a11y/web/templates/auth/login.html` | Add "Forgot password?" link |
| Modify | `auto_a11y/web/templates/auth/user_edit.html` | Add "Send reset email" button |

---

### Task 1: Add SMTP configuration

**Files:**
- Modify: `config.py:86-100`
- Modify: `.env.example`

- [ ] **Step 1: Add SMTP fields to Config dataclass in `config.py`**

After the `RATELIMIT_DEFAULT` line (line 88) and before the Microsoft SSO section, add:

```python
    # SMTP email (optional -- leave blank to disable password reset emails)
    SMTP_HOST: str = os.getenv('SMTP_HOST', '')
    SMTP_PORT: int = int(os.getenv('SMTP_PORT', 587))
    SMTP_USERNAME: str = os.getenv('SMTP_USERNAME', '')
    SMTP_PASSWORD: str = os.getenv('SMTP_PASSWORD', '')
    SMTP_USE_TLS: bool = os.getenv('SMTP_USE_TLS', 'True').lower() == 'true'
    SMTP_FROM_EMAIL: str = os.getenv('SMTP_FROM_EMAIL', '')
    SMTP_FROM_NAME: str = os.getenv('SMTP_FROM_NAME', 'CNIB Access Labs | AutoA11y')
```

Add a property after `SSO_ENABLED`:

```python
    @property
    def SMTP_ENABLED(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_FROM_EMAIL)
```

- [ ] **Step 2: Add SMTP vars to `.env.example`**

Append before the Microsoft SSO section:

```
# SMTP email (optional -- leave blank to disable password reset emails)
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_TLS=True
SMTP_FROM_EMAIL=
SMTP_FROM_NAME=CNIB Access Labs | AutoA11y
```

- [ ] **Step 3: Commit**

```bash
git add config.py .env.example
git commit -m "Add SMTP configuration variables for password reset emails"
```

---

### Task 2: Add `password_reset_at` to AppUser model

**Files:**
- Modify: `auto_a11y/models/app_user.py`

- [ ] **Step 1: Add field to dataclass**

After the `password_hint` field (line 42), add:

```python
    password_reset_at: Optional[datetime] = None
```

- [ ] **Step 2: Add to `to_dict()`**

In the `to_dict` method, add to the data dict:

```python
            'password_reset_at': self.password_reset_at,
```

- [ ] **Step 3: Add to `from_dict()`**

In the `from_dict` method, add to the constructor call:

```python
            password_reset_at=data.get('password_reset_at'),
```

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/models/app_user.py
git commit -m "Add password_reset_at field to AppUser model"
```

---

### Task 3: Create email module

**Files:**
- Create: `auto_a11y/core/email.py`

- [ ] **Step 1: Create `auto_a11y/core/email.py`**

```python
"""
Email sending via SMTP for Auto A11y.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_email(config, to, subject, text_body, html_body):
    """
    Send an email using SMTP settings from config.

    Args:
        config: App config object with SMTP_ attributes.
        to: Recipient email address.
        subject: Email subject line.
        text_body: Plain text version of the email.
        html_body: HTML version of the email.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not config.SMTP_ENABLED:
        logger.warning('SMTP is not configured -- cannot send email to %s', to)
        return False

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f'{config.SMTP_FROM_NAME} <{config.SMTP_FROM_EMAIL}>'
    msg['To'] = to

    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        if config.SMTP_USE_TLS:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT)
            server.starttls()
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT)

        if config.SMTP_USERNAME:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)

        server.sendmail(config.SMTP_FROM_EMAIL, to, msg.as_string())
        server.quit()
        logger.info('Email sent to %s: %s', to, subject)
        return True
    except Exception:
        logger.error('Failed to send email to %s', to, exc_info=True)
        return False
```

- [ ] **Step 2: Commit**

```bash
git add auto_a11y/core/email.py
git commit -m "Add SMTP email module"
```

---

### Task 4: Create email templates

**Files:**
- Create: `auto_a11y/web/templates/email/password_reset.txt`
- Create: `auto_a11y/web/templates/email/password_reset.html`

- [ ] **Step 1: Create plain text template**

```
{{ from_name }}

Password Reset Request

Hello{{ ' ' + user_name if user_name else '' }},

We received a request to reset your password. Click the link below to set a new password:

{{ reset_url }}

This link will expire in 15 minutes. If you did not request a password reset, you can safely ignore this email.

---
{{ from_name }}
```

- [ ] **Step 2: Create HTML template**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Password Reset</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4;padding:20px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;">
                    <!-- Header -->
                    <tr>
                        <td style="background-color:#0d6efd;padding:24px 32px;">
                            <h1 style="margin:0;color:#ffffff;font-size:20px;">{{ from_name }}</h1>
                        </td>
                    </tr>
                    <!-- Body -->
                    <tr>
                        <td style="padding:32px;">
                            <h2 style="margin:0 0 16px;color:#333333;font-size:22px;">Password Reset Request</h2>
                            <p style="margin:0 0 16px;color:#555555;font-size:16px;line-height:1.5;">
                                Hello{{ ' ' + user_name if user_name else '' }},
                            </p>
                            <p style="margin:0 0 24px;color:#555555;font-size:16px;line-height:1.5;">
                                We received a request to reset your password. Click the button below to set a new password:
                            </p>
                            <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
                                <tr>
                                    <td style="border-radius:6px;background-color:#0d6efd;">
                                        <a href="{{ reset_url }}" style="display:inline-block;padding:14px 32px;color:#ffffff;text-decoration:none;font-size:16px;font-weight:bold;">Reset Password</a>
                                    </td>
                                </tr>
                            </table>
                            <p style="margin:0 0 8px;color:#555555;font-size:14px;line-height:1.5;">
                                This link will expire in <strong>15 minutes</strong>.
                            </p>
                            <p style="margin:0 0 24px;color:#555555;font-size:14px;line-height:1.5;">
                                If you did not request a password reset, you can safely ignore this email.
                            </p>
                            <hr style="border:none;border-top:1px solid #eeeeee;margin:24px 0;">
                            <p style="margin:0;color:#999999;font-size:12px;line-height:1.5;">
                                If the button doesn't work, copy and paste this URL into your browser:
                            </p>
                            <p style="margin:4px 0 0;color:#999999;font-size:12px;word-break:break-all;">
                                {{ reset_url }}
                            </p>
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="background-color:#f8f9fa;padding:16px 32px;text-align:center;">
                            <p style="margin:0;color:#999999;font-size:12px;">{{ from_name }}</p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/email/
git commit -m "Add password reset email templates (TXT + HTML)"
```

---

### Task 5: Add password reset routes to auth.py

**Files:**
- Modify: `auto_a11y/web/routes/auth.py`

- [ ] **Step 1: Add imports at top of auth.py**

Add `URLSafeTimedSerializer, SignatureExpired` to the existing itsdangerous import:

```python
from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer, BadSignature, SignatureExpired
```

Add the email import:

```python
from auto_a11y.core.email import send_email
```

- [ ] **Step 2: Add helper functions after the `check_scope` function (before Microsoft SSO helpers)**

```python
# ------------------------------------------------------------------
# Password reset helpers
# ------------------------------------------------------------------

PASSWORD_RESET_SALT = 'password-reset'
PASSWORD_RESET_MAX_AGE = 900  # 15 minutes


def generate_reset_token(email):
    """Generate a signed, time-limited password reset token."""
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return serializer.dumps(email, salt=PASSWORD_RESET_SALT)


def verify_reset_token(token):
    """
    Verify a password reset token.
    Returns the email on success, None on failure.
    """
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = serializer.loads(token, salt=PASSWORD_RESET_SALT, max_age=PASSWORD_RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return email


def send_password_reset_email(user):
    """Send a password reset email to the given user."""
    config = current_app.app_config
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
        subject=_('Password Reset Request'),
        text_body=text_body,
        html_body=html_body,
    )
```

- [ ] **Step 3: Add forgot-password route (after the `register` route)**

```python
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Self-service password reset -- sends a reset link via email."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        if email:
            user = current_app.db.get_app_user_by_email(email)
            if user and user.is_active:
                if current_app.app_config.SMTP_ENABLED:
                    send_password_reset_email(user)
                else:
                    logger.warning('Password reset requested but SMTP is not configured')

        # Always show the same message to prevent email enumeration
        flash(_('If that email address is in our system, we have sent a password reset link.'), 'info')
        return redirect(url_for('auth.forgot_password'))

    return render_template('auth/forgot_password.html')
```

- [ ] **Step 4: Add reset-password route (after forgot-password route)**

```python
@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Set a new password using a valid reset token."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    email = verify_reset_token(token)
    if email is None:
        flash(_('This password reset link is invalid or has expired.'), 'danger')
        return redirect(url_for('auth.forgot_password'))

    user = current_app.db.get_app_user_by_email(email)
    if user is None or not user.is_active:
        flash(_('This password reset link is invalid or has expired.'), 'danger')
        return redirect(url_for('auth.forgot_password'))

    # Reject token if it was generated before the last reset
    if user.password_reset_at:
        serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        try:
            _email, timestamp = serializer.loads_unsafe(token, salt=PASSWORD_RESET_SALT)
        except Exception:
            flash(_('This password reset link is invalid or has expired.'), 'danger')
            return redirect(url_for('auth.forgot_password'))
        from datetime import datetime
        token_created = datetime.utcfromtimestamp(timestamp)
        if token_created < user.password_reset_at:
            flash(_('This password reset link has already been used.'), 'danger')
            return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if len(password) < 8:
            flash(_('Password must be at least 8 characters.'), 'danger')
            return render_template('auth/reset_password.html', token=token)

        if password != confirm_password:
            flash(_('Passwords do not match.'), 'danger')
            return render_template('auth/reset_password.html', token=token)

        user.set_password(password)
        user.password_reset_at = datetime.now()
        user.failed_login_count = 0
        user.locked_until = None
        user.update_timestamp()
        current_app.db.update_app_user(user)

        flash(_('Your password has been reset. Please log in.'), 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)
```

- [ ] **Step 5: Add admin-triggered send-reset-email action to the `user_edit` route**

In the existing `user_edit` POST handler, add a new `elif` branch after the `'unlock'` action:

```python
        elif action == 'send_reset_email':
            if current_app.app_config.SMTP_ENABLED:
                if send_password_reset_email(user):
                    flash(_('Password reset email sent to %(email)s.', email=user.email), 'success')
                else:
                    flash(_('Failed to send password reset email. Check SMTP configuration.'), 'danger')
            else:
                flash(_('SMTP is not configured. Cannot send email.'), 'danger')
```

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/web/routes/auth.py
git commit -m "Add forgot-password and reset-password routes with admin email trigger"
```

---

### Task 6: Create auth form templates

**Files:**
- Create: `auto_a11y/web/templates/auth/forgot_password.html`
- Create: `auto_a11y/web/templates/auth/reset_password.html`

- [ ] **Step 1: Create `forgot_password.html`**

```html
{% extends "base.html" %}

{% block title %}{{ _('Forgot Password') }} - {{ _('Auto A11y') }}{% endblock %}

{% block content %}
<div class="row justify-content-center">
    <div class="col-md-6 col-lg-4">
        <div class="card shadow">
            <div class="card-header bg-primary text-white">
                <h1 class="mb-0"><i class="bi bi-envelope" aria-hidden="true"></i> {{ _('Forgot Password') }}</h1>
            </div>
            <div class="card-body">
                <p class="text-muted">{{ _('Enter your email address and we will send you a link to reset your password.') }}</p>
                <form method="POST" action="{{ url_for('auth.forgot_password') }}">
                    <fieldset>
                        <legend class="visually-hidden">{{ _('Password Reset') }}</legend>
                        <div class="mb-3">
                            <label for="email" class="form-label">{{ _('Email') }}</label>
                            <input type="email" class="form-control" id="email" name="email" required autofocus autocomplete="email">
                        </div>
                        <button type="submit" class="btn btn-primary w-100">
                            <i class="bi bi-send" aria-hidden="true"></i> {{ _('Send Reset Link') }}
                        </button>
                    </fieldset>
                </form>
            </div>
            <div class="card-footer text-center">
                <a href="{{ url_for('auth.login') }}">{{ _('Back to Login') }}</a>
            </div>
        </div>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 2: Create `reset_password.html`**

```html
{% extends "base.html" %}

{% block title %}{{ _('Reset Password') }} - {{ _('Auto A11y') }}{% endblock %}

{% block content %}
<div class="row justify-content-center">
    <div class="col-md-6 col-lg-4">
        <div class="card shadow">
            <div class="card-header bg-primary text-white">
                <h1 class="mb-0"><i class="bi bi-key" aria-hidden="true"></i> {{ _('Reset Password') }}</h1>
            </div>
            <div class="card-body">
                <form method="POST" action="{{ url_for('auth.reset_password', token=token) }}">
                    <fieldset>
                        <legend class="visually-hidden">{{ _('New Password') }}</legend>
                        <div class="mb-3">
                            <label for="password" class="form-label">{{ _('New Password') }} <span class="text-danger">*</span></label>
                            <input type="password" class="form-control" id="password" name="password" required minlength="8" autocomplete="new-password" aria-describedby="password_help">
                            <div class="form-text" id="password_help">{{ _('Minimum 8 characters.') }}</div>
                        </div>
                        <div class="mb-3">
                            <label for="confirm_password" class="form-label">{{ _('Confirm Password') }} <span class="text-danger">*</span></label>
                            <input type="password" class="form-control" id="confirm_password" name="confirm_password" required autocomplete="new-password">
                        </div>
                        <button type="submit" class="btn btn-primary w-100">
                            <i class="bi bi-check-lg" aria-hidden="true"></i> {{ _('Set New Password') }}
                        </button>
                    </fieldset>
                </form>
            </div>
            <div class="card-footer text-center">
                <a href="{{ url_for('auth.login') }}">{{ _('Back to Login') }}</a>
            </div>
        </div>
    </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/auth/forgot_password.html auto_a11y/web/templates/auth/reset_password.html
git commit -m "Add forgot-password and reset-password form templates"
```

---

### Task 7: Update login template with "Forgot password?" link

**Files:**
- Modify: `auto_a11y/web/templates/auth/login.html:60-63`

- [ ] **Step 1: Add forgot password link to the card footer**

In `login.html`, the card-footer currently contains only the register link. Add a forgot-password link before it:

Replace the card-footer div:
```html
            <div class="card-footer text-center">
                <a href="{{ url_for('auth.register') }}">{{ _("Don't have an account? Register") }}</a>
            </div>
```

With:
```html
            <div class="card-footer text-center">
                <a href="{{ url_for('auth.forgot_password') }}" class="d-block mb-1">{{ _('Forgot your password?') }}</a>
                <a href="{{ url_for('auth.register') }}">{{ _("Don't have an account? Register") }}</a>
            </div>
```

- [ ] **Step 2: Commit**

```bash
git add auto_a11y/web/templates/auth/login.html
git commit -m "Add forgot password link to login page"
```

---

### Task 8: Add "Send password reset email" button to user edit page

**Files:**
- Modify: `auto_a11y/web/templates/auth/user_edit.html:72-98`

- [ ] **Step 1: Add the send-reset-email form to user_edit.html**

After the existing "Reset Password" card (after line 98's closing `</div>`), add a new card:

```html
        <div class="card shadow mb-4">
            <div class="card-header bg-secondary text-white">
                <h3 class="mb-0"><i class="bi bi-envelope" aria-hidden="true"></i> {{ _('Email Password Reset') }}</h3>
            </div>
            <div class="card-body">
                <p class="text-muted">{{ _('Send the user an email with a link to reset their own password. The link expires in 15 minutes.') }}</p>
                <form method="POST" action="{{ url_for('auth.user_edit', user_id=user.id) }}">
                    <input type="hidden" name="action" value="send_reset_email">
                    <button type="submit" class="btn btn-secondary" {% if not smtp_enabled %}disabled{% endif %}>
                        <i class="bi bi-send" aria-hidden="true"></i> {{ _('Send Password Reset Email') }}
                    </button>
                    {% if not smtp_enabled %}
                    <div class="form-text text-warning mt-2">
                        <i class="bi bi-exclamation-triangle" aria-hidden="true"></i> {{ _('SMTP is not configured. Set SMTP_ environment variables to enable.') }}
                    </div>
                    {% endif %}
                </form>
            </div>
        </div>
```

- [ ] **Step 2: Pass `smtp_enabled` to the template from the `user_edit` route**

In `auth.py`, update the `render_template` call at the end of the `user_edit` function to include `smtp_enabled`:

```python
    return render_template('auth/user_edit.html', user=user, user_projects=user_projects,
                           smtp_enabled=current_app.app_config.SMTP_ENABLED)
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/auth/user_edit.html auto_a11y/web/routes/auth.py
git commit -m "Add send-password-reset-email button to user edit page"
```

---

### Task 9: Manual verification

- [ ] **Step 1: Start the app and verify routes load**

```bash
python run.py --debug
```

Navigate to:
- `/auth/login` -- verify "Forgot your password?" link appears
- `/auth/forgot-password` -- verify the form renders
- `/auth/users/<id>/edit` -- verify "Email Password Reset" card appears (with disabled button if SMTP not configured)

- [ ] **Step 2: Test with SMTP configured (if available)**

Set SMTP_ vars in `.env` and test the full flow:
1. Go to `/auth/forgot-password`, enter an email
2. Check inbox for reset email
3. Click link, set new password
4. Log in with new password
5. Try the same link again -- should say "already been used"

- [ ] **Step 3: Test admin-triggered reset**

As an admin, go to `/auth/users/<id>/edit` and click "Send Password Reset Email". Verify email is received.
