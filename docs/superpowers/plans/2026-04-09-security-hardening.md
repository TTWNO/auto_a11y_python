# Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all identified security vulnerabilities from comprehensive audit — critical through medium severity.

**Architecture:** Targeted fixes to config, app factory, routes, templates, and JS. Adds Flask-WTF for CSRF protection, configures existing Flask-Limiter for rate limiting, and adds security headers middleware. No structural refactoring — surgical fixes only.

**Tech Stack:** Flask, Flask-WTF (new), Flask-Limiter (existing), Jinja2, JavaScript

**Spec:** `docs/superpowers/specs/2026-04-09-security-hardening-design.md`

---

### Task 1: Configuration Security (SECRET_KEY, DEBUG)

**Files:**
- Modify: `config.py:32-33`

- [ ] **Step 1: Fix SECRET_KEY default**

In `config.py`, change the `SECRET_KEY` field from the hardcoded default to a generated one. Add a warning log when no env var is set.

```python
# Replace line 32:
SECRET_KEY: str = os.getenv('SECRET_KEY', '')

# Add after the Config class definition (before config = Config()), around line 161:
import secrets
import logging
_config_logger = logging.getLogger(__name__)

# Then in the validate() method or after Config() instantiation, add:
if not config.SECRET_KEY:
    config.SECRET_KEY = secrets.token_hex(32)
    _config_logger.warning(
        "No SECRET_KEY set — using a random key. Sessions will not persist across restarts. "
        "Set SECRET_KEY in your .env file for production."
    )
```

- [ ] **Step 2: Fix DEBUG default**

In `config.py`, change line 33:
```python
# Before:
DEBUG: bool = os.getenv('DEBUG', 'True').lower() == 'true'
# After:
DEBUG: bool = os.getenv('DEBUG', 'False').lower() == 'true'
```

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "fix: secure SECRET_KEY generation and default DEBUG to False"
```

---

### Task 2: Session Cookie Security

**Files:**
- Modify: `auto_a11y/web/app.py:56-58`

- [ ] **Step 1: Add session security config**

In `auto_a11y/web/app.py`, after line 58 (`app.config['DEBUG'] = config.DEBUG`), add:

```python
    # Session cookie security
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = not config.DEBUG  # Allow HTTP in dev
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours
```

- [ ] **Step 2: Commit**

```bash
git add auto_a11y/web/app.py
git commit -m "fix: add session cookie security flags"
```

---

### Task 3: Open Redirect Fixes

**Files:**
- Modify: `auto_a11y/web/routes/auth.py:547-549`
- Modify: `auto_a11y/web/routes/demo.py:107-116`

- [ ] **Step 1: Fix auth.py open redirect**

In `auth.py`, add import at top:
```python
from urllib.parse import urlparse
```

Replace lines 547-549:
```python
        # Before:
        next_page = request.args.get('next')
        if next_page and next_page.startswith('/'):
            return redirect(next_page)

        # After:
        next_page = request.args.get('next')
        if next_page:
            parsed = urlparse(next_page)
            if parsed.path.startswith('/') and not parsed.netloc and not parsed.scheme:
                return redirect(next_page)
```

- [ ] **Step 2: Fix demo.py open redirect**

In `demo.py`, replace lines 107-116 (the else branch of the login route):
```python
    else:
        # Login failed - redirect back with error
        logger.warning(f"Failed login attempt: {email}")
        referer = request.referrer or ''
        if 'login-en' in referer:
            return redirect(url_for('demo.serve_demo', filename='login-en.html') + '?error=1')
        else:
            return redirect(url_for('demo.serve_demo', filename='login.html') + '?error=1')
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/routes/auth.py auto_a11y/web/routes/demo.py
git commit -m "fix: prevent open redirect via next param and demo login referrer"
```

---

### Task 4: Path Traversal Fixes

**Files:**
- Modify: `auto_a11y/web/routes/demo.py:23-50`
- Modify: `auto_a11y/web/app.py:505-511`
- Modify: `auto_a11y/web/routes/reports.py:386-403`

- [ ] **Step 1: Fix demo.py path traversal**

In `demo.py`, in the `serve_demo` function, add path validation after line 30 (`file_path = demo_dir / filename`). Insert before the `if file_path.is_dir():` check:

```python
        # Security: ensure resolved path is within demo directory
        if not file_path.resolve().is_relative_to(demo_dir.resolve()):
            logger.warning(f"Path traversal attempt blocked: {filename}")
            return "Not found", 404
```

- [ ] **Step 2: Fix screenshots path traversal**

In `app.py`, replace the `serve_screenshot` function (lines ~505-511):

```python
    @app.route('/screenshots/<path:filename>')
    def serve_screenshot(filename):
        """Serve screenshot files"""
        from flask import send_from_directory
        import os
        screenshots_dir = os.path.join(os.getcwd(), 'screenshots')
        file_path = Path(screenshots_dir) / filename
        if not file_path.resolve().is_relative_to(Path(screenshots_dir).resolve()):
            return jsonify({'error': 'Invalid file path'}), 403
        return send_from_directory(screenshots_dir, filename)
```

- [ ] **Step 3: Fix reports.py path traversal**

In `reports.py`, replace line 396:
```python
    # Before:
    if not file_path.resolve().parent == reports_dir.resolve():
    # After:
    if not file_path.resolve().is_relative_to(reports_dir.resolve()):
```

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/routes/demo.py auto_a11y/web/app.py auto_a11y/web/routes/reports.py
git commit -m "fix: prevent path traversal in demo, screenshots, and report download"
```

---

### Task 5: XSS Fix in filters.js

**Files:**
- Modify: `auto_a11y/web/static/js/filters.js:170-186`

- [ ] **Step 1: Replace innerHTML with safe DOM construction**

In `filters.js`, replace the innerHTML assignments (lines ~172-185) with safe DOM construction:

```javascript
            Object.entries(this.activeFilters).forEach(([type, values]) => {
                if (typeof values === 'string' && values) {
                    // Search filter - use textContent for user value
                    const tag = document.createElement('span');
                    tag.className = 'active-filter-tag';
                    tag.textContent = `Search: "${values}" `;
                    const removeBtn = document.createElement('span');
                    removeBtn.className = 'remove-filter';
                    removeBtn.dataset.type = type;
                    removeBtn.textContent = '\u00d7';
                    tag.appendChild(removeBtn);
                    activeFilterTags.appendChild(tag);
                } else if (values.size > 0) {
                    // Other filters - use textContent for user values
                    values.forEach(value => {
                        const tag = document.createElement('span');
                        tag.className = 'active-filter-tag';
                        tag.textContent = `${type}: ${value} `;
                        const removeBtn = document.createElement('span');
                        removeBtn.className = 'remove-filter';
                        removeBtn.dataset.type = type;
                        removeBtn.dataset.value = value;
                        removeBtn.textContent = '\u00d7';
                        tag.appendChild(removeBtn);
                        activeFilterTags.appendChild(tag);
                    });
                }
            });
```

- [ ] **Step 2: Commit**

```bash
git add auto_a11y/web/static/js/filters.js
git commit -m "fix: prevent XSS in filter tags by using textContent instead of innerHTML"
```

---

### Task 6: Password Hint Leak & Error Disclosure

**Files:**
- Modify: `auto_a11y/web/routes/auth.py:540`
- Modify: `auto_a11y/web/routes/demo.py:48-50`

- [ ] **Step 1: Remove password hint from failed login**

In `auth.py` line 540, remove the `password_hint` parameter:
```python
# Before:
            return render_template('auth/login.html', password_hint=user.password_hint)
# After:
            return render_template('auth/login.html')
```

- [ ] **Step 2: Fix error disclosure in demo.py**

In `demo.py`, replace lines 48-50:
```python
    except Exception as e:
        logger.error(f"Error serving demo file {filename}: {e}", exc_info=True)
        return "Not found", 404
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/routes/auth.py auto_a11y/web/routes/demo.py
git commit -m "fix: remove password hint leak and generic error messages"
```

---

### Task 7: CORS Configuration

**Files:**
- Modify: `config.py` (add `CORS_ORIGINS` field)
- Modify: `auto_a11y/web/app.py:60-61`

- [ ] **Step 1: Add CORS_ORIGINS config field**

In `config.py`, add after the `AUTH_ENABLED` line (~line 93):
```python
    # CORS allowed origins (comma-separated, empty = no CORS)
    CORS_ORIGINS: str = os.getenv('CORS_ORIGINS', '')
```

- [ ] **Step 2: Make CORS conditional in app.py**

In `app.py`, replace line 61:
```python
    # Before:
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # After:
    # Configure CORS - only enable if origins are explicitly configured
    if config.CORS_ORIGINS:
        cors_origins = [o.strip() for o in config.CORS_ORIGINS.split(',')]
        CORS(app, resources={r"/api/*": {"origins": cors_origins}})
```

- [ ] **Step 3: Commit**

```bash
git add config.py auto_a11y/web/app.py
git commit -m "fix: restrict CORS to explicitly configured origins only"
```

---

### Task 8: Security Headers Middleware

**Files:**
- Modify: `auto_a11y/web/app.py` (add after_request handler)

- [ ] **Step 1: Add security headers**

In `app.py`, add before the error handlers section (before the `@app.errorhandler(403)` line):

```python
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
```

Note: CSP includes `cdn.jsdelivr.net` because Bootstrap CSS/JS and Bootstrap Icons are loaded from that CDN (see `base.html` lines 15-17).

- [ ] **Step 2: Commit**

```bash
git add auto_a11y/web/app.py
git commit -m "feat: add security headers middleware (CSP, X-Frame-Options, HSTS)"
```

---

### Task 9: CSRF Protection Setup

**Files:**
- Modify: `requirements.txt` (add flask-wtf)
- Modify: `auto_a11y/web/app.py` (init CSRFProtect, exempt blueprints)
- Modify: `auto_a11y/web/templates/base.html` (add CSRF meta tag + fetch wrapper)

- [ ] **Step 1: Add flask-wtf dependency**

Add `flask-wtf` to `requirements.txt` (alphabetical order, after `flask-login`):
```
flask-wtf==1.2.2
```

Then install:
```bash
.venv/bin/pip install flask-wtf==1.2.2
```

- [ ] **Step 2: Initialize CSRFProtect in app.py**

In `app.py`, add import at top:
```python
from flask_wtf.csrf import CSRFProtect
```

After the CORS configuration block, add:
```python
    # CSRF protection
    csrf = CSRFProtect(app)
    csrf.exempt(api_bp)     # API uses token auth, not session cookies
    csrf.exempt(demo_bp)    # Static demo site
    csrf.exempt(public_bp)  # Public share token routes (stateless)
```

- [ ] **Step 3: Add CSRF meta tag and global fetch wrapper to base.html**

In `base.html`, after line 25 (`{% block extra_css %}{% endblock %}`), add:
```html
    <!-- CSRF Token -->
    <meta name="csrf-token" content="{{ csrf_token() }}">
```

At the bottom of `base.html`, before the closing `</body>` tag, add a script that patches all inline `fetch()` calls to include the CSRF token header automatically:
```html
    <!-- CSRF token for AJAX requests -->
    <script>
    (function() {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
        if (csrfToken) {
            const originalFetch = window.fetch;
            window.fetch = function(url, options = {}) {
                options = options || {};
                const method = (options.method || 'GET').toUpperCase();
                if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
                    options.headers = options.headers || {};
                    if (options.headers instanceof Headers) {
                        if (!options.headers.has('X-CSRFToken')) {
                            options.headers.set('X-CSRFToken', csrfToken);
                        }
                    } else {
                        options.headers['X-CSRFToken'] = options.headers['X-CSRFToken'] || csrfToken;
                    }
                }
                return originalFetch.call(this, url, options);
            };
        }
    })();
    </script>
```

This automatically handles ALL fetch-based AJAX calls in templates without modifying each one individually.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt auto_a11y/web/app.py auto_a11y/web/templates/base.html
git commit -m "feat: add CSRF protection with global fetch wrapper"
```

---

### Task 10: Add CSRF Tokens to All Form Templates

**Files:** All templates containing `<form` with `method="POST"`.

The approach: Add `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>` immediately after each `<form>` tag that uses POST method.

- [ ] **Step 1: Add CSRF tokens to auth templates**

Templates:
- `auth/login.html` (line 31)
- `auth/register.html` (line 19)
- `auth/forgot_password.html` (line 14)
- `auth/reset_password.html` (line 13)
- `auth/user_create.html` (line 13)
- `auth/user_edit.html` (lines 13, 78, 106, 128)
- `auth/user_list.html` (lines 51, 111)
- `auth/profile.html` (lines 13, 50)

After each `<form method="POST"...>` or `<form ... method="post"...>` tag, add on the next line:
```html
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>
```

- [ ] **Step 2: Add CSRF tokens to project/website/page templates**

Templates:
- `projects/create.html` (line 89)
- `projects/edit.html` (lines 55, 474)
- `projects/view.html` (lines 477, 573, 628)
- `websites/edit.html` (lines 23, 138)
- `pages/edit.html` (line 29)
- `pages/test_matrix.html` (line 331)
- `pages/test_matrix_v2.html` (line 207)

Same pattern — add the hidden input after each POST form tag.

- [ ] **Step 3: Add CSRF tokens to user/participant/group templates**

Templates:
- `website_users/create.html` (line 38)
- `website_users/edit.html` (line 38)
- `project_users/create.html` (line 35)
- `project_users/edit.html` (line 35)
- `project_users/view.html` (lines 181, 190)
- `project_participants/create_supervisor.html` (line 35)
- `project_participants/create_tester.html` (line 35)
- `project_participants/edit_supervisor.html` (line 35)
- `project_participants/edit_tester.html` (line 35)
- `groups/list.html` (lines 50, 101)
- `groups/edit.html` (line 18)

- [ ] **Step 4: Add CSRF tokens to remaining templates**

Templates:
- `scripts/create.html` (line 85)
- `schedules/form.html` (line 41)

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/templates/
git commit -m "feat: add CSRF tokens to all POST form templates"
```

---

### Task 11: Add CSRF Token to public_base.html

**Files:**
- Modify: `auto_a11y/web/templates/public/public_base.html`

- [ ] **Step 1: Add CSRF meta tag**

In `public_base.html`, add the CSRF meta tag in the `<head>` section:
```html
    <meta name="csrf-token" content="{{ csrf_token() }}">
```

Also add the same global fetch wrapper script before `</body>` if public templates make POST requests.

- [ ] **Step 2: Commit**

```bash
git add auto_a11y/web/templates/public/public_base.html
git commit -m "feat: add CSRF meta tag to public base template"
```

---

### Task 12: Rate Limiting on Auth Endpoints

**Files:**
- Modify: `auto_a11y/web/routes/auth.py` (add limiter decorators)
- Modify: `auto_a11y/web/app.py` (initialize limiter)

- [ ] **Step 1: Initialize Flask-Limiter in app.py**

In `app.py`, add import:
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
```

After the CSRF protection block, add:
```python
    # Rate limiting
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[config.RATELIMIT_DEFAULT],
        storage_uri="memory://",
    )
    app.limiter = limiter  # Make accessible to blueprints
```

- [ ] **Step 2: Add rate limits to auth endpoints**

In `auth.py`, at the top of the login route (line 507), add the decorator. Since the limiter is on the app, access it via `current_app`:

Add a helper at the top of auth.py (after imports):
```python
from flask import current_app

def get_limiter():
    return current_app.limiter
```

Then apply limits to view functions. **Important:** These calls must go AFTER all `register_blueprint()` calls (after line ~260 in app.py), because `app.view_functions` is only populated after blueprint registration:

```python
    # Rate limits on auth endpoints (must be after register_blueprint calls)
    limiter.limit("10/minute")(app.view_functions['auth.login'])
    limiter.limit("5/minute")(app.view_functions['auth.register'])
    limiter.limit("3/minute")(app.view_functions['auth.forgot_password'])
```

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/app.py
git commit -m "feat: add rate limiting to auth endpoints"
```

---

### Task 13: Verify All Changes Work

- [ ] **Step 1: Start the app and verify**

```bash
cd /home/tait/Documents/cnib/code/auto_a11y_python
.venv/bin/python run.py --debug
```

Test manually:
- Login page loads and submits
- Demo site serves files
- Report download works
- API endpoints respond
- Security headers present in responses (check browser dev tools)
- CSRF token present in forms

- [ ] **Step 2: Verify security headers with curl**

```bash
curl -I http://127.0.0.1:5001/ 2>/dev/null | grep -E "X-Frame|X-Content|Content-Security|Strict-Transport"
```

Expected output should include all security headers.
