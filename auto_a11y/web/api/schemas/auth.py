"""Pydantic schemas for the /api/v1/auth/* + /api/v1/users/* endpoints (§5.13).

Wire-shape preservation
-----------------------
This module covers fifteen handlers spanning three sub-surfaces:

- **Auth lifecycle** — login, logout, register, forgot-password,
  reset-password, ``GET``/``PATCH /auth/me``. Login / register /
  reset-password all return the same envelope (``token`` +
  ``token_record`` + ``user``) so a client can call any of those
  three and immediately be authenticated for follow-up calls. Logout
  and forgot-password return ``204``/``202`` with no body. Password
  fields are present on inputs only — the output projection never
  surfaces ``password_hash``.

- **Admin user CRUD** — ``GET``/``POST /users``, plus the
  ``GET``/``PATCH``/``DELETE /users/<id>`` triplet. Listing returns
  ``{users: [...]}`` (non-paginated; the production user count is
  small) rather than the cursor-paginated envelope. ``POST /users``
  is the admin path and does NOT issue a Bearer token (distinct from
  ``/auth/register`` — the admin doesn't need to become the new user).

- **SSO** — ``GET /auth/sso/<provider>/url`` returns
  ``{provider, url}`` to start the OAuth dance; ``GET
  /auth/sso/<provider>/callback`` completes it and returns the same
  login envelope plus a ``provider`` field so the client knows which
  provider just authed.

The single shared resource model :class:`AppUserOut` mirrors the
legacy ``_serialize_app_user`` byte-for-byte: ``id``, ``email``,
``display_name``, ``role`` (enum string value), ``is_active``,
``is_superadmin``, three timestamps. ``password_hash`` and the
``sso_id`` field are deliberately not surfaced.

Decisions worth flagging
------------------------
- **Login / register / reset-password envelopes are shared but not
  identical.** All three return ``{token, token_record, user}``, but
  the response from each endpoint is modelled as its own ``…Out``
  alias rather than a single ``AuthTokenEnvelope``. Three reasons:
  (a) the OpenAPI spec surfaces three distinct operation IDs and
  gets cleaner per-endpoint examples; (b) future divergence (e.g.
  adding ``new_user: bool`` to the register response) doesn't force
  a schema bump on the other two; (c) the existing per-endpoint docs
  already distinguish their semantics so the schemas should follow.

- **Password fields are typed loosely** (``Optional[str]``) on the
  input bodies even though the handlers reject anything not matching
  ``isinstance(str)`` + ``len >= 6``. Pydantic ``str`` would surface
  a different error shape than the existing ``_FieldError`` envelope;
  preserving the legacy field-path 400s requires the handler-side
  parsing to stay. The schemas document the wire shape but
  intentionally don't re-enforce the validation.

- **``token_record`` is typed as a sub-model** :class:`ApiTokenOut`
  rather than ``dict[str, object]``. The shape comes from the
  existing ``_serialize_token`` helper and is stable enough to
  surface as a typed schema. Fields whose nullability is intrinsic
  (``description``, ``last_used_at``, ``expires_at``) are
  ``Optional``; the timestamps are ISO-8601 strings.

- **``logout`` and ``forgot-password`` return ``204``/``202`` with
  no body.** They have no response schema (the handler returns a
  bare ``Response(status=…)`` and the ``@document`` decorator
  registers them with ``response_204=Empty`` / ``response_202=Empty``).
  No model is exported here for those — they re-use the shared
  :class:`auto_a11y.web.api.schemas.common.Empty`.

- **The SSO callback response includes the ``provider`` field**
  alongside the login envelope so SPAs that share a callback handler
  across providers can branch on it without parsing the URL. The
  ``/url`` response includes the same field for symmetry even though
  the client already knows which provider it asked for.

- **Admin-create response is the bare :class:`AppUserOut`** (not the
  login envelope). The legacy handler ``create_user_rest`` returns
  ``_serialize_app_user(persisted)`` directly because the admin
  doesn't need a token for the newly-created user. Mirrors §5.1's
  ``ProjectCreatedOut``-vs-``ProjectDictOut`` split: the create path
  is its own shape.

- **``AppUserPatch`` and ``AuthMePatch`` are distinct** even though
  they overlap. ``AuthMePatch`` only permits ``display_name`` +
  ``password`` (self-service profile edits); ``AppUserPatch`` adds
  ``role``, ``is_active``, ``is_superadmin`` (admin-only mutations).
  Keeping them separate makes the OpenAPI spec surface the per-route
  permission boundary explicitly.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Shared resource models
# ---------------------------------------------------------------------------


class AppUserOut(StrictModel):
    """JSON-safe projection of an :class:`AppUser`.

    Mirrors the legacy ``_serialize_app_user`` helper byte-for-byte:
    ``id``, ``email``, ``display_name`` (nullable), ``role`` (enum
    string value), ``is_active``, ``is_superadmin``, plus three
    ISO-8601 timestamps (``created_at``, ``updated_at``,
    ``last_login`` — the last one nullable because never-logged-in
    users carry ``None``).

    Deliberately omits ``password_hash``, ``sso_provider``, and
    ``sso_id`` — those are internal-only and never surface on the
    wire.
    """

    id: Optional[str] = None
    email: str
    display_name: Optional[str] = None
    role: str
    is_active: bool
    is_superadmin: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_login: Optional[str] = None


class ApiTokenOut(StrictModel):
    """JSON-safe projection of an :class:`ApiToken`.

    Mirrors the legacy ``_serialize_token`` helper byte-for-byte.
    The raw token value is NEVER surfaced here — it appears once on
    the ``token`` field of the login envelope and is not persisted
    in plaintext. This model is the token's metadata only.
    """

    id: Optional[str] = None
    user_id: str
    description: Optional[str] = None
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None
    expires_at: Optional[str] = None


class AuthEnvelopeOut(StrictModel):
    """Shared envelope for token-issuing auth endpoints.

    Used as the base for :class:`LoginOut`, :class:`RegisterOut`,
    :class:`ResetPasswordOut`, and :class:`SsoCallbackOut`. The
    ``token`` field is the raw Bearer token returned ONCE on success;
    the caller must persist it client-side because it cannot be
    retrieved again.

    Concrete subclasses add per-endpoint fields (e.g.
    :class:`SsoCallbackOut` adds ``provider``) but the three core
    fields are always present.
    """

    token: str
    token_record: ApiTokenOut
    user: AppUserOut


# ---------------------------------------------------------------------------
# Auth lifecycle — login / logout / register / forgot / reset
# ---------------------------------------------------------------------------


class LoginIn(StrictModel):
    """Request body for ``POST /auth/login``.

    ``email`` and ``password`` are typed as ``Optional[str]`` because
    the legacy handler surfaces an opaque ``invalid_credentials`` 401
    on missing/non-string values rather than a per-field 400.
    Tightening the schema here would bump that path to a 400 and
    leak which field is missing — the opaque 401 is deliberate
    (defends against email-enumeration attacks).

    ``description`` is a free-form label persisted with the minted
    :class:`ApiToken` so a user can later identify the session on the
    ``/api/v1/tokens`` page.

    ``session`` opts the caller into Flask-Login session cookie
    establishment. The default (``None`` / ``False``) returns a Bearer
    token only — appropriate for API clients and integrations. The
    admin HTML frontend sets ``session: true`` so a subsequent same-
    origin navigation (no Authorization header) is still authenticated.
    The two mechanisms coexist: a session-cookie login still mints and
    returns a Bearer token.

    ``remember`` is forwarded to Flask-Login when ``session`` is true;
    ignored otherwise.
    """

    # email / password typed as ``Optional[object]`` (not ``Optional[str]``)
    # so Pydantic does NOT reject non-string values at the @document
    # boundary. The handler's ``isinstance(_, str)`` check fires inside
    # the route body and uniformly returns the opaque 401 — preserving
    # the email-enumeration defence the docstring above describes.
    email: Optional[object] = None
    password: Optional[object] = None
    description: Optional[str] = None
    session: Optional[bool] = None
    remember: Optional[bool] = None


class LoginOut(AuthEnvelopeOut):
    """Response body for ``POST /auth/login``.

    Returns ``{token, token_record, user}`` — the raw Bearer token,
    its persisted metadata, and the user record. The token value is
    returned ONCE; clients must persist it because the database only
    stores its hash.
    """


class RegisterIn(StrictModel):
    """Request body for ``POST /auth/register``.

    Same field-path / loose-typing convention as :class:`LoginIn`:
    the handler does the actual validation and surfaces field-path
    400s on missing email, short password, or duplicate email.

    ``display_name`` is optional; when absent the user's email
    local-part is used for display.

    ``session`` opts into Flask-Login session cookie establishment
    after a successful registration (same semantics as
    :attr:`LoginIn.session`). The admin HTML registration form sets
    this to ``true``; API clients leave it unset.
    """

    email: Optional[str] = None
    password: Optional[str] = None
    display_name: Optional[str] = None
    password_hint: Optional[str] = None
    session: Optional[bool] = None


class RegisterOut(AuthEnvelopeOut):
    """Response body for ``POST /auth/register`` (status 201).

    Same envelope as :class:`LoginOut` — register-and-token-in-one
    saves the new account from a second roundtrip to ``/auth/login``.
    The handler also sets a ``Location`` header pointing at
    ``/api/v1/users/<new_user_id>``.
    """


class ForgotPasswordIn(StrictModel):
    """Request body for ``POST /auth/forgot-password``.

    ``email`` is ``Optional[str]`` because the handler ALWAYS
    returns 202 — even on missing/unknown/inactive email — to defend
    against email-enumeration attacks. Tightening the schema here
    would let a caller distinguish "missing field" from "unknown
    email" via the 400-vs-202 status difference.
    """

    email: Optional[str] = None


class ResetPasswordIn(StrictModel):
    """Request body for ``POST /auth/reset-password``.

    ``token`` is the signed value emitted by the email's reset link
    (see ``auto_a11y.web.routes.auth.generate_reset_token``).
    ``password`` is the new password; the handler enforces ``len >=
    6``. Both are typed loosely (``Optional[str]``) to keep the
    legacy field-path 400 envelope in play.

    ``session`` opts into Flask-Login session cookie establishment so
    the user lands on the app authenticated after the reset, same
    semantics as :attr:`LoginIn.session`.
    """

    token: Optional[str] = None
    password: Optional[str] = None
    session: Optional[bool] = None


class ResetPasswordOut(AuthEnvelopeOut):
    """Response body for ``POST /auth/reset-password``.

    Same envelope as :class:`LoginOut`. On a successful reset the
    handler mints a fresh Bearer token so the caller lands on the
    app authenticated without a redirect-and-login dance.
    """


# ---------------------------------------------------------------------------
# /auth/me — current-user surface
# ---------------------------------------------------------------------------


class AuthMePatch(StrictModel):
    """Request body for ``PATCH /auth/me``.

    Self-service profile edits — only ``display_name`` and
    ``password`` are patchable. ``email`` is the natural key for SSO
    matching and the role/superadmin flags are admin-only mutations
    (use ``PATCH /users/<id>`` for those). Both fields are
    ``Optional`` because PATCH semantics mean absent ⇒ no change.

    Password is typed loosely (``Optional[str]``) so the existing
    field-path 400 envelope continues to fire for invalid values.
    """

    display_name: Optional[str] = None
    password: Optional[str] = None
    # Self-service password hint. The profile form offers it; without
    # modeling it the StrictModel rejects the field and the hint is dropped.
    password_hint: Optional[str] = None


# ---------------------------------------------------------------------------
# Admin user CRUD
# ---------------------------------------------------------------------------


class AppUserListOut(StrictModel):
    """Response body for ``GET /users``.

    Non-paginated ``{users: [...]}`` envelope. The production user
    count is small (typically <100) so cursor pagination is overkill;
    if the user count ever grows past that, swap to the shared
    cursor-pagination shape.
    """

    users: list[AppUserOut]


class AppUserCreateIn(StrictModel):
    """Request body for ``POST /users`` (admin-create).

    Distinct from :class:`RegisterIn` because the admin path can
    set ``role`` and ``is_superadmin`` at create time. ``role``
    defaults to ``client`` server-side when absent; ``is_superadmin``
    defaults to false. Both ``email`` and ``password`` are typed
    loosely (``Optional[str]``) to preserve the legacy field-path
    400 envelope.

    Note: this endpoint does NOT issue a Bearer token (unlike
    ``/auth/register``) because the admin doesn't need to become the
    newly-created user.
    """

    email: Optional[str] = None
    password: Optional[str] = None
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_superadmin: Optional[bool] = None
    # Optional password hint the admin-create form offers; modeled here so
    # StrictModel accepts it and the create handler can persist it.
    password_hint: Optional[str] = None


class AppUserPatch(StrictModel):
    """Request body for ``PATCH /users/<id>`` (admin-patch).

    Adds ``role``, ``is_active``, ``is_superadmin`` over the
    self-service :class:`AuthMePatch` shape. Email is deliberately
    not editable through this endpoint — email is the natural key for
    SSO matching and changing it can orphan OAuth-linked sessions.

    ``is_superadmin`` is not gated against self-demotion at the API
    layer; the operator can lock themselves out and would need to
    recover via direct DB access.
    """

    display_name: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    is_superadmin: Optional[bool] = None
    # Admin-set password hint (reset-password form offers it); modeled so
    # StrictModel accepts it and the patch handler can persist it.
    password_hint: Optional[str] = None


# ---------------------------------------------------------------------------
# /users/me — narrow current-user surface
# ---------------------------------------------------------------------------


class CurrentUserOut(StrictModel):
    """Response body for ``GET /users/me``.

    Intentionally narrower than :class:`AppUserOut` — the legacy
    handler surfaced only ``user_id``, ``email``, ``display_name``,
    and ``is_superadmin``. SPAs that need the fuller surface
    (``role``, timestamps, ``is_active``) should call ``GET
    /auth/me`` instead.

    ``user_id`` is ``Optional[str]`` because the legacy projection
    coerced ``None`` IDs to ``"None"`` then back to ``None`` —
    preserving that nullability rather than coercing to ``""``.
    """

    user_id: Optional[str] = None
    email: Optional[str] = None
    display_name: Optional[str] = None
    is_superadmin: bool


# ---------------------------------------------------------------------------
# SSO endpoints
# ---------------------------------------------------------------------------


class SsoUrlOut(StrictModel):
    """Response body for ``GET /auth/sso/<provider>/url``.

    Returns the OAuth authorization URL the client should redirect
    the user to (e.g. ``window.location.href = response.url``). The
    matching state is written to the Flask session so the callback
    can complete the exchange. ``provider`` echoes the path param
    for symmetry with :class:`SsoCallbackOut`.
    """

    provider: str
    url: str


class SsoCallbackOut(AuthEnvelopeOut):
    """Response body for ``GET /auth/sso/<provider>/callback``.

    Extends the login envelope with a ``provider`` field so SPAs
    that share a single callback handler across providers can branch
    on it without parsing the URL.
    """

    provider: str
