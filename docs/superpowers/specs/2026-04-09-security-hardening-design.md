# Security Hardening Design

**Date:** 2026-04-09
**Context:** Comprehensive security audit and remediation for internet-facing + internal deployment

## Scope

Fix all identified vulnerabilities from a thorough security audit. The application serves both internal CNIB staff and external clients via a public portal.

## Findings Summary

### Critical
1. **Hardcoded weak SECRET_KEY** — `config.py:32` defaults to `'dev-secret-key-change-in-production'`
2. **DEBUG defaults to True** — `config.py:33` exposes Werkzeug debugger
3. **Open redirect via `next` param** — `auth.py:548` only checks `startswith('/')`, allows `//evil.com`
4. **Path traversal in demo route** — `demo.py:30` no validation on user-supplied filename

### High
5. **Open redirect in demo login** — `demo.py:115-116` appends to `request.referrer` directly
6. **CORS wildcard** — `app.py:61` allows `origins: "*"` on all API routes
7. **No session cookie security flags** — missing Secure, HttpOnly, SameSite
8. **Password hint leak** — `auth.py:540` returns hint on failed login (user enumeration)
9. **XSS via innerHTML** — `filters.js:176,183` injects user filter values unsanitized
10. **Path traversal in screenshots** — `app.py:506-511` no validation on filename
11. **Path traversal in report download** — `reports.py:396` parent check insufficient for nested paths
12. **Error message disclosure** — `demo.py:50` returns `str(e)` to users

### Medium
13. **No CSRF protection** on any POST endpoints
14. **No rate limiting** on auth endpoints
15. **Missing security headers** — no CSP, X-Frame-Options, HSTS, etc.

## Fix Design

### 1. Configuration & Session Security

**SECRET_KEY** (`config.py:32`): Generate a random 32-byte hex key via `os.urandom(32).hex()` when no env var is set. Log a warning so operators know to set a stable one.

**DEBUG** (`config.py:33`): Default to `False`.

**Session cookies** (`app.py`): Add after SECRET_KEY config:
- `SESSION_COOKIE_HTTPONLY = True`
- `SESSION_COOKIE_SAMESITE = 'Lax'`
- `SESSION_COOKIE_SECURE = True` only when not DEBUG (local dev works over HTTP)
- `PERMANENT_SESSION_LIFETIME = 86400` (24 hours)

### 2. Open Redirect Fixes

**Auth login** (`auth.py:547-549`): Use `urllib.parse.urlparse` — reject if `netloc` or `scheme` is present:
```python
from urllib.parse import urlparse
parsed = urlparse(next_page)
if next_page and parsed.path.startswith('/') and not parsed.netloc and not parsed.scheme:
    return redirect(next_page)
```

**Demo login** (`demo.py:115-116`): Replace `request.referrer` usage with explicit `url_for` redirects based on language detection.

### 3. Path Traversal Fixes

**Demo route** (`demo.py:29-35`): Resolve both paths and check `file_path.resolve().is_relative_to(demo_dir.resolve())`.

**Screenshots** (`app.py:506-511`): Same pattern — resolve and check `is_relative_to`.

**Report download** (`reports.py:396`): Replace `parent ==` with `is_relative_to()`.

### 4. XSS & Information Disclosure

**filters.js** (`176,183`): Build DOM with `textContent` for user values. Only static markup uses innerHTML.

**Password hint** (`auth.py:540`): Remove `password_hint=user.password_hint` from failed-login render.

**Error disclosure** (`demo.py:50`): Return generic error message.

### 5. CORS

**app.py:61**: New `CORS_ORIGINS` config field (default empty = no CORS). When set, split on commas. When empty, don't call `CORS()` at all.

### 6. Security Headers Middleware

`@app.after_request` handler sets:
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` (non-DEBUG only)
- `X-XSS-Protection: 0`
- `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'`

### 7. CSRF Protection

Add `Flask-WTF` dependency. `CSRFProtect(app)` in `create_app`.
- Exempt API blueprint (token-based auth + CORS)
- Exempt demo blueprint (static demo site)
- Add `{{ csrf_token() }}` to all form templates
- Add `<meta name="csrf-token">` to base template for AJAX
- Update JS POST requests to include CSRF token header

### 8. Rate Limiting

Add `Flask-Limiter` dependency. Auth endpoint limits:
- `/auth/login` POST: 10/minute
- `/auth/register` POST: 5/minute
- `/auth/forgot-password` POST: 3/minute

Global default: use existing `RATELIMIT_DEFAULT` config (60/minute). Key by remote address.

## Files Modified

- `config.py` — SECRET_KEY, DEBUG, CORS_ORIGINS
- `auto_a11y/web/app.py` — session config, CORS, security headers, CSRF init
- `auto_a11y/web/routes/auth.py` — open redirect fix, password hint removal, rate limiting
- `auto_a11y/web/routes/demo.py` — path traversal, open redirect, error disclosure
- `auto_a11y/web/routes/reports.py` — path traversal fix
- `auto_a11y/web/static/js/filters.js` — XSS fix
- `auto_a11y/web/templates/base.html` — CSRF meta tag
- All form templates — CSRF token hidden inputs
- `requirements.txt` — Flask-WTF, Flask-Limiter

## Testing

- Verify login/logout still works
- Verify demo site still serves files
- Verify report download still works
- Verify API endpoints still respond
- Verify forms submit successfully with CSRF tokens
- Verify rate limiting triggers on rapid auth attempts
- Check response headers include security headers
