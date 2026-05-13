"""Auth helpers for `/api/v1` handlers.

The legacy Flask routes use the ``project_role_required`` decorator,
which returns ad-hoc ``{"error": "..."}`` envelopes on auth failure.
The REST API surface returns RFC 7807 Problem Details, so handlers
call :func:`require_project_role` (or :func:`require_authenticated`)
inline and let the raised :class:`ApiError` flow through
``@api_endpoint``.

The actual permission logic lives in
:mod:`auto_a11y.web.routes.auth` — this module is a thin adapter that
swaps the failure shape, nothing more.

Authentication accepts **two** mechanisms:

1. Flask-Login session cookie — what the existing HTML routes use,
   so REST callers sharing the cookie (typically same-origin SPAs)
   are authenticated automatically.
2. ``Authorization: Bearer <token>`` — for non-browser clients and
   for the new SSO + token flow added in §5.13. Tokens are minted by
   :mod:`auto_a11y.web.api.tokens` and resolved here on every
   request that lacks a session.

The bearer path takes effect only when the session is *not* already
authenticated — a logged-in user with a Bearer header for a different
account gets the session user, not the token user. Callers that need
to swap identities should log out first.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from flask import g, has_request_context, request
from flask_login import current_user

from auto_a11y.models.app_user import UserRole
from auto_a11y.web.api.errors import ForbiddenError, UnauthorizedError

if TYPE_CHECKING:
    from flask_login import CurrentUserProtocol


def _resolve_bearer_user() -> object | None:
    """Look up the user behind an ``Authorization: Bearer …`` header.

    Returns the :class:`AppUser` on a valid token, ``None`` otherwise.
    Imports are kept inside the function so this module stays
    import-cheap (and so the test suite can monkey-patch
    ``auto_a11y.web.api.tokens.resolve_token`` if needed).
    """
    if not has_request_context():
        return None
    from auto_a11y.web.api.tokens import extract_bearer, resolve_token
    from auto_a11y.web.typed_app import get_db

    raw = extract_bearer(request.headers.get("Authorization"))
    if raw is None:
        return None
    return resolve_token(get_db(), raw)


def require_authenticated() -> object:
    """Raise :class:`UnauthorizedError` if no session and no valid Bearer.

    Returns either the Flask-Login ``current_user`` proxy (session
    auth) or the resolved :class:`AppUser` (Bearer auth). When a
    request supplies both, the session wins — see the module
    docstring for the rationale.
    """
    if current_user.is_authenticated:
        return current_user
    token_user = _resolve_bearer_user()
    if token_user is not None:
        # Stash on flask.g so role checks below (which read
        # ``current_user``) can also see token-authenticated users.
        # The legacy ``get_effective_role`` reaches into
        # ``current_user`` directly, so for Bearer-auth requests
        # role checks should use the explicit ``user=`` argument
        # callers pass; ``g.api_token_user`` is the single source of
        # truth.
        g.api_token_user = token_user
        return token_user
    raise UnauthorizedError("Authentication required")


def _effective_user() -> CurrentUserProtocol:
    """Return whichever of (session, token) is currently authenticated.

    Used by the role-check helpers below — they need a single
    object to ask ``is_superadmin`` of regardless of how the request
    authenticated. :class:`AppUser` and Flask-Login's anonymous user
    both satisfy :class:`CurrentUserProtocol` structurally, so we
    return that for the type checkers.
    """
    if current_user.is_authenticated:
        return current_user
    if has_request_context():
        token_user = getattr(g, "api_token_user", None)
        if token_user is not None:
            return cast("CurrentUserProtocol", token_user)
    return current_user


def require_superadmin() -> object:
    """Raise :class:`UnauthorizedError`/:class:`ForbiddenError` for non-superadmins.

    Mirrors the legacy ``@admin_required`` decorator's check. Returns
    whichever user (session or token) is authenticated on success.
    """
    require_authenticated()
    user = _effective_user()
    if not getattr(user, "is_superadmin", False):
        raise ForbiddenError("Superadmin access required")
    return user


def require_global_permission(resource: str, level: str) -> object:
    """Enforce a permission level on a global resource (no project scope).

    Used for the resources flagged as ``GLOBAL_RESOURCES`` in
    :mod:`auto_a11y.models.permission_group` — currently ``users``,
    ``groups``, and ``fixture_tests``. The check delegates to
    :func:`auto_a11y.core.permissions.user_has_global_permission`,
    which sweeps every project the user belongs to and returns true if
    *any* of their groups grants the level on the resource.

    Raises:
        UnauthorizedError: not logged in.
        ForbiddenError: logged in but no project membership grants the
            required level on this global resource.
    """
    require_authenticated()
    user = _effective_user()
    if getattr(user, "is_superadmin", False):
        return user

    from auto_a11y.core.permissions import user_has_global_permission

    if not user_has_global_permission(user, resource, level):
        raise ForbiddenError(
            f"Insufficient permission on {resource} (need {level})"
        )
    return user


def require_project_role(
    *roles: UserRole,
    project_id: str | None = None,
    website_id: str | None = None,
    page_id: str | None = None,
) -> UserRole:
    """Enforce that the caller holds at least one of ``roles`` on the resource.

    At least one of ``project_id``, ``website_id``, ``page_id`` must be
    supplied — the same lookup chain the legacy decorator uses. The
    effective role is recorded on ``flask.g`` for downstream handlers (the
    legacy decorator already does this; we keep parity for HTML routes
    that consume ``g.effective_role``).

    Raises:
        UnauthorizedError: not logged in.
        ForbiddenError: logged in but role is not permitted on the target.
    """
    require_authenticated()
    user = _effective_user()

    if getattr(user, "is_superadmin", False):
        g.effective_role = UserRole.ADMIN
        return UserRole.ADMIN

    # Imported lazily so this module stays import-cheap and the auth
    # dependency graph remains routes/auth.py → api/auth.py, not the
    # other way around.
    from auto_a11y.web.routes.auth import get_effective_role

    effective_role = get_effective_role(
        user, request, project_id, website_id, page_id
    )
    if effective_role is None or effective_role not in roles:
        raise ForbiddenError("Insufficient permissions for this resource")

    g.effective_role = effective_role
    return effective_role
