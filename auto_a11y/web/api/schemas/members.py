"""Pydantic schemas for the /api/v1/* members + groups endpoints (§5.11).

Wire-shape preservation
-----------------------
This module covers three overlapping surfaces folded together by the
roadmap §5.11 cleanup:

- ``/api/v1/groups`` — permission group CRUD. Cursor-paginated list,
  POST/GET/PUT/PATCH/DELETE on the single-resource path.
- ``/api/v1/users/search`` — narrow read-only autocomplete for picking
  a user to add to a project. Returns ``{users: [...]}`` (not the
  shared ``{items, next_cursor}`` cursor envelope — this is a capped
  20-row search hit, not a paginated list).
- ``/api/v1/projects/<id>/members`` — platform access control. Lists
  return ``{members, available_groups}`` so a UI can render an
  add-member dialog in one round-trip; per-resource shape mirrors
  ``_serialize_project_member`` byte-for-byte.

The schemas in this module mirror the legacy helpers in
``auto_a11y/web/routes/api.py`` (``_serialize_permission_group``,
``_serialize_app_user_search_hit``, ``_serialize_project_member``)
byte-for-byte so the refactor is a documentation pass, not a
behavioural change.

Decisions worth flagging
------------------------
- Group ``permissions`` is typed as ``dict[str, str]`` rather than a
  closed enum of the 16 resource nouns × 5 permission levels. The
  resource-noun list lives in
  ``auto_a11y/models/permission_group.py`` (``RESOURCE_NOUNS``) and is
  expected to grow over time; binding the wire shape to the current
  list would force a schema bump every time a new resource is added.
  The handler validates the values against the closed enums at the
  application level and surfaces 400s on unknown keys/values.
- Project-member list returns a non-paginated ``{members,
  available_groups}`` shape rather than the cursor-paginated envelope.
  This is intentional: a project's member count is bounded by a
  small constant (typically <20) and the UI needs the full available
  groups list alongside; cursoring would force two round-trips for
  the common case.
- ``MemberIn`` requires ``user_id`` + ``group_ids`` (no role) — the
  legacy single ``role`` field was replaced by ``group_ids: list[str]``
  in the platform-permissions migration. ``user_id`` is typed
  ``Optional[str]`` here even though the handler rejects empty values,
  because the legacy parser called ``body.get("user_id")`` and surfaced
  its own 400 shape (field path ``user_id``, code ``required``) rather
  than letting Pydantic generate the error.
- ``MemberPatch`` omits ``user_id`` — the member identity is in the
  URL path, not the body, and the only patchable field is
  ``group_ids``. Typed ``Optional[list[str]]`` to keep the field
  technically present-or-absent on the wire even though the handler
  always rejects an empty list with a 400.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


class GroupIn(StrictModel):
    """POST/PUT body for creating or replacing a permission group.

    Mirrors the legacy ``_build_group_from_body``: ``name`` is required
    at the semantic level (the handler rejects empty/whitespace-only
    values) but typed as ``Optional[str]`` because the legacy parser
    surfaces its own 400 shape rather than letting Pydantic generate
    the error. ``description`` defaults to ``""`` on the wire when the
    field is absent. ``permissions`` is an open ``dict[str, str]``
    (resource-noun → permission-level); the handler validates against
    the closed enums at the application level.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[dict[str, str]] = None


class GroupPatch(StrictModel):
    """PATCH body for partially updating a permission group.

    Same shape as :class:`GroupIn` — every field optional. ``is_system``
    is intentionally absent: that flag protects the seeded default
    groups from deletion and clients are never permitted to flip it on
    or off.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[dict[str, str]] = None


class GroupOut(StrictModel):
    """Response shape for a single permission group.

    Mirrors :func:`_serialize_permission_group` byte-for-byte. Used by:

    - ``GET /groups/<group_id>``
    - ``POST /groups`` (create response)
    - ``PUT /groups/<group_id>`` (replace response)
    - ``PATCH /groups/<group_id>`` (patch response)
    - ``GET /groups`` (inside ``items``)

    Field details:

    - ``id`` — stringified Mongo ``_id``; ``None`` only for transient
      unsaved groups that never appear over the wire.
    - ``permissions`` — open ``dict[str, str]`` rather than a closed
      enum-of-enums because the resource-noun list is expected to grow.
    - ``is_system`` — ``True`` for the seeded default groups (Admin,
      Auditor, Client) which are protected from deletion.
    - ``created_at`` / ``updated_at`` — ISO 8601 datetime strings.
    """

    id: Optional[str] = None
    name: str
    description: str
    permissions: dict[str, str]
    is_system: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class GroupListOut(StrictModel):
    """Response body for ``GET /groups``.

    Cursor-paginated; same ``{items, next_cursor}`` shape as the other
    v1 list endpoints. Most deployments will fit on one page (the
    seeded defaults plus a small handful of custom groups) but the
    cursor shape is preserved for forward compatibility.
    """

    items: list[GroupOut]
    next_cursor: Optional[str] = None


# ---------------------------------------------------------------------------
# User search
# ---------------------------------------------------------------------------


class UserSearchOut(StrictModel):
    """Single hit on the ``/users/search`` autocomplete endpoint.

    Deliberately narrow — this endpoint exists for picking a user when
    adding a project member, so it surfaces only the email, display
    name, and id. The full AppUser surface (password hash, SSO ids,
    last_login, etc.) is out of scope for this endpoint by design.

    ``display_name`` is optional because AppUser allows it to be unset
    (the UI falls back to the email). ``user_id`` is typed
    ``Optional[str]`` because the underlying ``AppUser.id`` is
    ``Optional[str]`` (transient unsaved users return ``None``); in
    practice the search endpoint only returns persisted users so the
    field is always set, but the type preserves byte-for-byte wire
    parity with the legacy ``_serialize_app_user_search_hit``.
    """

    user_id: Optional[str] = None
    email: str
    display_name: Optional[str] = None


class UserSearchListOut(StrictModel):
    """Response body for ``GET /users/search``.

    Non-paginated ``{users: [...]}`` shape. The results are capped at
    20 entries server-side; queries shorter than 2 characters return
    an empty list rather than 400 to keep the autocomplete UX smooth.
    """

    users: list[UserSearchOut]


# ---------------------------------------------------------------------------
# Project members
# ---------------------------------------------------------------------------


class MemberIn(StrictModel):
    """POST body for adding a project member.

    Mirrors the legacy ``add_project_member_rest`` validator:

    - ``user_id`` is semantically required (the handler rejects empty
      values) but typed ``Optional[str]`` because the legacy parser
      surfaces its own 400 shape (field path ``user_id``, code
      ``required``).
    - ``group_ids`` is semantically a non-empty list (the handler
      rejects an empty array with a 400) but typed
      ``Optional[list[str]]`` for the same reason.
    """

    user_id: Optional[str] = None
    group_ids: Optional[list[str]] = None


class MemberPatch(StrictModel):
    """PUT body for replacing a member's group assignments.

    Despite being a PUT, the only mutable field is ``group_ids`` —
    ``user_id`` lives in the URL path, not the body, and is never
    rewritten. Typed ``Optional[list[str]]`` to keep the field
    technically present-or-absent on the wire even though the handler
    always rejects an empty list with a 400.
    """

    group_ids: Optional[list[str]] = None


class MemberOut(StrictModel):
    """Response shape for a single project member.

    Mirrors :func:`_serialize_project_member` byte-for-byte. Used by:

    - ``GET /projects/<id>/members/<user_id>``
    - ``POST /projects/<id>/members`` (create response)
    - ``PUT /projects/<id>/members/<user_id>`` (update response)
    - ``GET /projects/<id>/members`` (inside ``members``)

    The ``email`` and ``display_name`` fields are populated by joining
    against the AppUser collection at serialization time; both may be
    ``None`` if the referenced AppUser has been deleted (the member
    row is preserved to keep audit trails intact).
    """

    user_id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    group_ids: list[str]


class AvailableGroupOut(StrictModel):
    """A group entry on the project-member list response.

    Narrow projection of :class:`GroupOut` carrying only the fields
    the add-member UI needs (id, name, ``is_system`` so the picker
    can flag protected defaults). The full group resource lives at
    ``/api/v1/groups/<id>``.
    """

    id: Optional[str] = None
    name: str
    is_system: bool


class MemberListOut(StrictModel):
    """Response body for ``GET /projects/<id>/members``.

    Non-paginated ``{members, available_groups}`` shape. A project's
    member count is bounded by a small constant (typically <20) and
    the UI needs the available-groups list alongside to render an
    add-member dialog in a single round-trip; cursoring would force
    two HTTP requests for the common case.
    """

    members: list[MemberOut]
    available_groups: list[AvailableGroupOut]
