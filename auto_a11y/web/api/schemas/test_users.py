"""Pydantic schemas for the test-user CRUD + test-login endpoints (§5.x F1b).

Wire-shape preservation
-----------------------
This module covers two parallel test-user resources plus the
synchronous test-login action that each one exposes:

- ``/projects/<project_id>/test-users`` + ``/project-test-users/<id>``
  — login credentials scoped to a project. The model is
  :class:`auto_a11y.models.project_user.ProjectUser`; the parent-id
  field on the wire is ``project_id``.
- ``/websites/<website_id>/test-users`` + ``/website-test-users/<id>``
  — login credentials scoped to a single website. The model is
  :class:`auto_a11y.models.website_user.WebsiteUser`; the parent-id
  field on the wire is ``website_id``.
- ``POST /{scope}-test-users/<id>/test-login`` — synchronous "run
  the login automation right now" action. Returns 200 with a
  uniform ``success | error`` envelope so the caller has a single
  parser regardless of outcome.

The two test-user resources have *identical field shapes* apart from
the parent-id rename, so this module defines a single set of bodies
(``TestUserIn``, ``TestUserPatch``) and two response models that diverge
only on whether ``project_id`` or ``website_id`` is the parent
foreign-key field. The login response also carries a ``scope``
discriminator string (``"project"`` | ``"website"``) so a generic
client can route the response on the same field.

The schemas mirror the legacy helpers in
``auto_a11y/web/routes/api.py`` (``_serialize_project_test_user``,
``_serialize_website_test_user``, ``_serialize_login_config_via_model``,
``_build_project_test_user_from_body``,
``_build_website_test_user_from_body``, ``_apply_patch_to_test_user``,
plus the two ``test_*_user_login`` handlers) byte-for-byte so the
refactor is a documentation pass, not a behavioural change.

Decisions worth flagging
------------------------
- **One ``TestUserIn`` for both scopes.** Project and website test
  users have identical mutable surfaces; the parent id is read from
  the URL path (``project_id`` / ``website_id``) and never appears in
  the request body. Splitting the request schema would be redundant
  noise on the OpenAPI surface.
- **``username`` / ``password`` are ``Optional[str]`` despite being
  semantically required on create.** The legacy ``_build_*_from_body``
  helpers raise their own :class:`ValidationError` with field-specific
  codes (``username`` → ``required``, ``password`` → ``required``)
  before Pydantic gets a chance to complain. Modelling them as
  required would change the error envelope.
- **``TestUserPatch`` is identical to ``TestUserIn``.** PATCH allows
  every field to be omitted; ``password`` specifically — when omitted
  or an empty string — preserves the existing value (admin-settings
  secret-handling rule). PUT shares the same schema because the
  legacy handler treats a missing/blank ``password`` on PUT the same
  way (preserve existing).
- **``LoginConfigDict`` is an open ``StrictModel`` rather than a
  closed Pydantic schema with enum'd ``authentication_method``.** The
  legacy ``_parse_login_config_dict`` validates against the closed
  set ``{form_login, basic_auth, oauth, sso}`` and raises its own
  400 with field path ``login_config.authentication_method`` and
  code ``invalid_value``. Modelling it as a ``Literal`` would change
  that error code. We keep the runtime check in the handler and use
  ``Optional[str]`` on the schema so the OpenAPI consumer sees the
  right type without behaviour drift. ``additional_steps`` is typed
  ``Optional[list[LoginAdditionalStep]]`` where each step is itself
  an open dict — the legacy parser only rejects entries that aren't
  objects; the inner shape is not constrained at this layer.
- **``LoginConfigOut`` omits ``manual_login_wait_seconds``.** The
  legacy ``_serialize_login_config_via_model`` does too (it surfaces
  only the 11 fields the wire has always carried; the
  ``manual_login_wait_seconds`` field is a server-side knob the
  login-automation step reads directly from the model). Adding it
  would change the wire shape.
- **Two response schemas for the two scopes.** ``ProjectTestUserOut``
  carries ``project_id``; ``WebsiteTestUserOut`` carries
  ``website_id``. A single union schema would either lose the
  type-narrowing on the OpenAPI consumer or force a discriminator
  field that the legacy wire never had.
- **``ProjectTestUserListOut`` / ``WebsiteTestUserListOut`` use the
  ``{items: [...]}`` envelope without ``next_cursor``.** The
  underlying ``get_project_users`` / ``get_website_users`` helpers
  return the full set unpaginated (test-user counts are bounded by
  small constants — a project typically tracks a handful of test
  accounts, not thousands). The cursor field is omitted entirely
  rather than serialised as ``null`` to keep byte-parity with the
  legacy ``jsonify({"items": [...]})``.
- **``TestUserLoginOut`` has the same envelope on success and
  failure.** The legacy handlers return the same JSON shape with
  ``success=false`` and ``error="..."`` on the 500 path so clients
  have a single parser. ``manual_login`` and ``wait_seconds`` are
  set from ``authentication_method == "manual_login"``; on the
  non-manual path ``wait_seconds`` is ``None``.
"""
from __future__ import annotations

from typing import Literal, Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Login config (shared by both scopes)
# ---------------------------------------------------------------------------


class LoginConfigIn(StrictModel):
    """Login-config object on a test-user create/update body.

    Mirrors the legacy ``_parse_login_config_dict`` validator.
    Every field is optional on the wire — the handler rejects an
    invalid ``authentication_method`` (not one of the four allowed
    string values) and type-checks the rest, but missing fields
    take the server-side default (``form_login``, empty selectors,
    30 minute session timeout, empty additional_steps).

    ``additional_steps`` is typed as
    ``Optional[list[dict[str, object]]]``; the legacy parser only
    rejects entries that aren't objects (it does not introspect
    step contents), and step values in the wild mix strings (for
    selectors / static text), ints (for wait timeouts), and bools
    (for optional toggles). Narrowing the value type to ``str`` here
    would change the wire shape on the int / bool fields.
    """

    authentication_method: Optional[str] = None
    login_url: Optional[str] = None
    username_field_selector: Optional[str] = None
    password_field_selector: Optional[str] = None
    submit_button_selector: Optional[str] = None
    success_indicator_selector: Optional[str] = None
    logout_url: Optional[str] = None
    logout_button_selector: Optional[str] = None
    logout_success_indicator_selector: Optional[str] = None
    additional_steps: Optional[list[dict[str, object]]] = None
    session_timeout_minutes: Optional[int] = None


class LoginConfigOut(StrictModel):
    """Login-config object on a test-user response.

    Mirrors :func:`_serialize_login_config_via_model` byte-for-byte.
    11 fields. ``manual_login_wait_seconds`` is intentionally absent
    — the legacy serialiser doesn't expose it.

    ``authentication_method`` is typed ``str`` (not a closed
    ``Literal``) for the same reason as :class:`LoginConfigIn`: the
    set of allowed values is defined and validated at the model
    layer, and surfacing it as an open string here keeps the
    OpenAPI consumer from mis-coercing a value the legacy wire
    accepts as a string.
    """

    authentication_method: str
    login_url: Optional[str] = None
    username_field_selector: Optional[str] = None
    password_field_selector: Optional[str] = None
    submit_button_selector: Optional[str] = None
    success_indicator_selector: Optional[str] = None
    logout_url: Optional[str] = None
    logout_button_selector: Optional[str] = None
    logout_success_indicator_selector: Optional[str] = None
    additional_steps: list[dict[str, object]]
    session_timeout_minutes: int


# ---------------------------------------------------------------------------
# Test user — request bodies (shared between project + website scopes)
# ---------------------------------------------------------------------------


class TestUserIn(StrictModel):
    """POST/PUT body for creating or replacing a test user.

    Mirrors the legacy ``_build_project_test_user_from_body`` and
    ``_build_website_test_user_from_body`` validators (their bodies
    are byte-identical apart from which parent foreign-key the
    handler injects from the URL path).

    Fields:

    - ``username`` — semantically required on create; the handler
      surfaces its own 400 with field path ``username`` and code
      ``required`` on missing/blank values. PUT accepts a missing
      ``username`` only if it can preserve the existing one (which
      the handler does not — the legacy parser always rejects).
      Typed ``Optional[str]`` to preserve the legacy error envelope.
    - ``password`` — required on create; on PUT a missing or blank
      value preserves the existing password (admin-settings
      secret-handling rule). Typed ``Optional[str]`` for the same
      reason as ``username``.
    - ``display_name`` / ``description`` — optional, free-text.
      Empty string is normalised to ``None`` by the handler.
    - ``roles`` — optional, list of free-text role tags (e.g.
      ``["student", "premium"]``).
    - ``login_config`` — optional; when present, must be an object.
      See :class:`LoginConfigIn`.
    - ``enabled`` — optional, default ``True``. The PATCH endpoint
      uses this to implement the legacy enable/disable toggle.
    """

    username: Optional[str] = None
    password: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    roles: Optional[list[str]] = None
    login_config: Optional[LoginConfigIn] = None
    enabled: Optional[bool] = None


class TestUserPatch(StrictModel):
    """PATCH body for partially updating a test user.

    Structurally identical to :class:`TestUserIn` — every field
    optional. PATCH is intentionally a thin alias rather than a
    distinct type because the legacy ``_apply_patch_to_test_user``
    accepts the full create-shape body and treats missing keys as
    "leave alone". Splitting the types would imply a behavioural
    difference the runtime doesn't actually implement.
    """

    username: Optional[str] = None
    password: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    roles: Optional[list[str]] = None
    login_config: Optional[LoginConfigIn] = None
    enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Test user — response shapes (one per scope, diverge only on parent id)
# ---------------------------------------------------------------------------


class ProjectTestUserOut(StrictModel):
    """Response shape for a single project-scoped test user.

    Mirrors :func:`_serialize_project_test_user` byte-for-byte. Used
    by:

    - ``GET /project-test-users/<user_id>``
    - ``POST /projects/<project_id>/test-users`` (create response)
    - ``PUT /project-test-users/<user_id>`` (replace response)
    - ``PATCH /project-test-users/<user_id>`` (patch response)
    - ``GET /projects/<project_id>/test-users`` (inside ``items``)

    Field details:

    - ``id`` — stringified Mongo ``_id``; ``None`` only for transient
      unsaved users that never appear over the wire.
    - ``password_set`` — boolean indicator (the raw password is
      never serialised).
    - ``last_used`` / ``created_at`` / ``updated_at`` — ISO 8601
      datetime strings; ``last_used`` may be ``None`` if the user
      has never been used for testing.
    - ``last_login_success`` / ``last_login_error`` — populated by
      the login automation. Both may be ``None`` if there has been
      no login attempt yet.
    """

    id: Optional[str] = None
    project_id: str
    username: str
    password_set: bool
    display_name: Optional[str] = None
    roles: list[str]
    description: Optional[str] = None
    login_config: LoginConfigOut
    enabled: bool
    last_used: Optional[str] = None
    last_login_success: Optional[bool] = None
    last_login_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class WebsiteTestUserOut(StrictModel):
    """Response shape for a single website-scoped test user.

    Mirrors :func:`_serialize_website_test_user` byte-for-byte. The
    only difference from :class:`ProjectTestUserOut` is the parent
    foreign-key field (``website_id`` instead of ``project_id``).
    """

    id: Optional[str] = None
    website_id: str
    username: str
    password_set: bool
    display_name: Optional[str] = None
    roles: list[str]
    description: Optional[str] = None
    login_config: LoginConfigOut
    enabled: bool
    last_used: Optional[str] = None
    last_login_success: Optional[bool] = None
    last_login_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ProjectTestUserListOut(StrictModel):
    """Response body for ``GET /projects/<project_id>/test-users``.

    Non-paginated ``{items: [...]}`` envelope. Test-user counts are
    bounded by small constants in practice (a project tracks a
    handful of accounts, not thousands) so cursoring is unnecessary.
    """

    items: list[ProjectTestUserOut]


class WebsiteTestUserListOut(StrictModel):
    """Response body for ``GET /websites/<website_id>/test-users``.

    Non-paginated ``{items: [...]}`` envelope. Same reasoning as
    :class:`ProjectTestUserListOut`.
    """

    items: list[WebsiteTestUserOut]


# ---------------------------------------------------------------------------
# Test-login action — synchronous "run the login automation right now"
# ---------------------------------------------------------------------------


class TestUserLoginOut(StrictModel):
    """Response body for ``POST /{scope}-test-users/<user_id>/test-login``.

    Synchronous — the handler spins a fresh browser, attempts the
    user's login automation, and returns the result inline (no
    polling). Status 200 (not 202) on both success and failure
    signals "done synchronously" so clients don't poll a non-existent
    job. Status 500 on uncaught exceptions still carries this same
    envelope shape so clients have a single parser.

    Fields:

    - ``user_id`` — echoes the URL path parameter.
    - ``scope`` — ``"project"`` or ``"website"``; lets a generic
      client route the response without inspecting the URL.
    - ``success`` — ``True`` if the login automation reported
      success.
    - ``duration_ms`` — wall-clock duration of the login attempt;
      ``0`` if the handler errored before the automation could run.
    - ``error`` — error message string on failure; ``None`` on
      success.
    - ``manual_login`` — ``True`` if the user's authentication
      method is ``manual_login`` (the operator drives a visible
      browser by hand).
    - ``wait_seconds`` — for manual login, the per-attempt timeout
      in seconds; ``None`` for non-manual methods (the handler uses
      a fixed 30-second default that is not exposed on the wire).
    """

    user_id: str
    scope: Literal["project", "website"]
    success: bool
    duration_ms: int
    error: Optional[str] = None
    manual_login: bool
    wait_seconds: Optional[int] = None
