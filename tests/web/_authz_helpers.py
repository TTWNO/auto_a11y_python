"""Reusable route-authorization test harness.

Used by the §1.3-1.11 route-guard tasks to assert that a blueprint route
aborts 403 for a non-member and passes for a member, WITHOUT Mongo.

The harness builds a tiny Flask app, registers the blueprint under test,
installs Flask-Login with a stub (authenticated, non-superadmin) user, and
monkeypatches the authorization layer's permission check so that
``user_has_permission`` grants exactly the requested role tier.

Role tiering mirrors :func:`auto_a11y.web.routes.auth.get_effective_role`,
which maps DB group-permissions to legacy roles:

* ``'admin'``   -> ``project_members:delete`` granted -> ``UserRole.ADMIN``
* ``'auditor'`` -> ``test_results:create`` granted    -> ``UserRole.AUDITOR``
* ``'client'``  -> ``projects:read`` granted          -> ``UserRole.CLIENT``
* ``None``      -> nothing granted                     -> no role

The single seam we patch is ``auto_a11y.core.permissions.user_has_permission``
(``get_effective_role`` imports it from that module at call time, so patching
the module attribute is sufficient). ``resolve_project_id`` calls
``permissions._get_db()`` unconditionally, so the app carries a ``MagicMock``
``db`` -- for a ``project_id`` URL param that mock is never actually consulted
(the resolver returns the param directly).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from unittest.mock import MagicMock

import pytest
from flask import Blueprint, Flask
from flask_login import LoginManager, UserMixin

Role = Literal['admin', 'auditor', 'client']

#: Minimum ``(resource, action)`` pair that each tier must satisfy in
#: ``user_has_permission``, taken verbatim from ``get_effective_role``.
_TIER_REQUIREMENTS: dict[Role, tuple[str, str]] = {
    'admin': ('project_members', 'delete'),
    'auditor': ('test_results', 'create'),
    'client': ('projects', 'read'),
}

#: For a granted tier, which lower-tier ``(resource, action)`` checks also
#: return True. ``get_effective_role`` probes admin first, then auditor, then
#: client, so an admin must also satisfy the auditor and client probes for the
#: function to short-circuit at the admin branch.
_TIER_GRANTS: dict[Role, set[tuple[str, str]]] = {
    'admin': {
        ('project_members', 'delete'),
        ('test_results', 'create'),
        ('projects', 'read'),
    },
    'auditor': {
        ('test_results', 'create'),
        ('projects', 'read'),
    },
    'client': {
        ('projects', 'read'),
    },
}


class StubUser(UserMixin):
    """Authenticated, non-superadmin Flask-Login user.

    ``UserMixin`` supplies ``is_authenticated``/``is_active``/``is_anonymous``
    and ``get_id`` (from ``self.id``). The authorization layer reads
    ``is_superadmin`` via ``getattr(user, 'is_superadmin', False)`` -- we pin
    it to ``False`` so the decorator exercises the permission path rather than
    the superadmin bypass.
    """

    def __init__(self, user_id: str = 'stub-user') -> None:
        self.id = user_id
        self.is_superadmin = False


def _permission_check_for_role(role: Role | None) -> Callable[..., bool]:
    """Build a ``user_has_permission`` replacement granting exactly ``role``.

    Signature mirrors the real
    ``user_has_permission(user, project_id, resource, required_level)``.
    """
    granted: set[tuple[str, str]] = set() if role is None else _TIER_GRANTS[role]

    def _check(
        _user: object,
        _project_id: str | None,
        resource: str,
        required_level: str,
    ) -> bool:
        return (resource, required_level) in granted

    return _check


def make_app_with_blueprint(
    blueprint: Blueprint,
    *,
    role: Role | None,
    monkeypatch: pytest.MonkeyPatch,
    url_prefix: str | None = None,
) -> Flask:
    """Build a Mongo-free Flask app wired for route-authorization tests.

    :param blueprint: the blueprint under test (registered as-is).
    :param role: the role tier ``user_has_permission`` should grant the stub
        user -- ``'admin' | 'auditor' | 'client' | None``. ``None`` grants no
        permissions, so guarded routes resolve to no effective role.
    :param monkeypatch: the test's ``monkeypatch`` fixture; used to patch the
        permission seam for the duration of the test.
    :param url_prefix: optional prefix passed to ``register_blueprint``.
    :returns: a configured :class:`flask.Flask` app (use ``app.test_client()``).
    """
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'route-authz-harness-secret'
    app.config['WTF_CSRF_ENABLED'] = False

    # ``resolve_project_id`` and ``user_has_permission`` both call
    # ``permissions._get_db()`` -> ``current_app.db``. A MagicMock satisfies
    # the attribute; for a ``project_id`` URL param it is never queried.
    setattr(app, 'db', MagicMock())

    login_manager = LoginManager()
    login_manager.init_app(app)

    stub_user = StubUser()

    def _load_user(user_id: str) -> StubUser | None:
        return stub_user if user_id == stub_user.id else None

    login_manager.user_loader(_load_user)

    # Fluent must be initialised so the ftl(...) calls inside the decorator
    # (flash/JSON messages) resolve instead of raising.
    from auto_a11y.web.fluent import init_fluent
    init_fluent(app)

    # Patch the single authorization seam. ``get_effective_role`` does
    # ``from auto_a11y.core.permissions import user_has_permission`` at call
    # time, so patching the module attribute is what takes effect.
    monkeypatch.setattr(
        'auto_a11y.core.permissions.user_has_permission',
        _permission_check_for_role(role),
    )

    app.register_blueprint(blueprint, url_prefix=url_prefix)

    return app


def login_stub_user(app: Flask) -> None:
    """Pre-seat the stub user into the session for ``test_request_context``.

    Most tests instead drive requests through ``app.test_client()`` with a
    session transaction; this helper is provided for callers that need to log
    the user in imperatively. It is a thin convenience around setting the
    Flask-Login ``_user_id`` session key.
    """
    with app.test_request_context():
        from flask import session
        session['_user_id'] = StubUser().id
