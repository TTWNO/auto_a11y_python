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
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from flask import g, request
from flask_login import current_user

from auto_a11y.models.app_user import UserRole
from auto_a11y.web.api.errors import ForbiddenError, UnauthorizedError

if TYPE_CHECKING:
    pass


def require_authenticated() -> object:
    """Raise :class:`UnauthorizedError` if the current request has no session.

    Returns the Flask-Login ``current_user`` proxy on success so callers can
    inline it: ``user = require_authenticated()``.
    """
    if not current_user.is_authenticated:
        raise UnauthorizedError("Authentication required")
    return current_user


def require_superadmin() -> object:
    """Raise :class:`UnauthorizedError`/:class:`ForbiddenError` for non-superadmins.

    Mirrors the legacy ``@admin_required`` decorator's check. Returns the
    Flask-Login ``current_user`` proxy on success.
    """
    require_authenticated()
    if not getattr(current_user, "is_superadmin", False):
        raise ForbiddenError("Superadmin access required")
    return current_user


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

    if getattr(current_user, "is_superadmin", False):
        g.effective_role = UserRole.ADMIN
        return UserRole.ADMIN

    # Imported lazily so this module stays import-cheap and the auth
    # dependency graph remains routes/auth.py → api/auth.py, not the
    # other way around.
    from auto_a11y.web.routes.auth import get_effective_role

    effective_role = get_effective_role(
        current_user, request, project_id, website_id, page_id
    )
    if effective_role is None or effective_role not in roles:
        raise ForbiddenError("Insufficient permissions for this resource")

    g.effective_role = effective_role
    return effective_role
