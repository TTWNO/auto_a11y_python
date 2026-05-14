"""Pydantic schemas for project people CRUD (F1c — testers + supervisors).

Wire-shape preservation
-----------------------
This module covers two parallel project-scoped people resources:

- ``/projects/<project_id>/testers`` + ``/projects/<project_id>/testers/<tester_id>``
  — lived-experience accessibility testers. The model is
  :class:`auto_a11y.models.project.LivedExperienceTester`; the
  identifying fields on the wire are ``name`` (required) plus
  optional ``email``, ``disability_type``, ``assistive_tech`` (a
  list of free-text assistive technology names), and ``notes``.
- ``/projects/<project_id>/supervisors`` +
  ``/projects/<project_id>/supervisors/<supervisor_id>`` — test
  supervisors who oversee lived-experience testing sessions. The
  model is :class:`auto_a11y.models.project.TestSupervisor`; the
  identifying fields are ``name`` (required) plus optional
  ``email``, ``role`` (e.g. "Accessibility Specialist"),
  ``organization``, and ``notes``.

Both resources live as inline arrays on the parent ``Project``
document — they are not standalone Mongo collections. IDs are
string UUIDs assigned by :func:`LivedExperienceTester.ensure_id` /
:func:`TestSupervisor.ensure_id` on insert; the parent project
document is replaced wholesale on every write.

The two resources share a *similar* (but not identical) shape:
both have ``name``, ``email``, ``notes``; testers add
``disability_type`` and ``assistive_tech`` whereas supervisors add
``role`` and ``organization``. Because the role-specific fields
diverge, we ship two separate request/response schema pairs rather
than a unified one — collapsing them would either lose the
type-narrowing or force a discriminator field the legacy wire
never had.

The schemas mirror the legacy helpers in
``auto_a11y/web/routes/api.py`` (``_serialize_tester``,
``_serialize_supervisor``, ``_build_tester_from_body``,
``_build_supervisor_from_body``, ``_apply_patch_to_tester``,
``_apply_patch_to_supervisor``) byte-for-byte so this refactor is
a documentation pass, not a behavioural change.

Decisions worth flagging
------------------------
- **``name`` is ``Optional[str]`` despite being semantically
  required on create.** The legacy ``_validate_required_name``
  helper raises its own :class:`ValidationError` with field path
  ``name`` and code ``required`` before Pydantic gets a chance to
  complain. Modelling it as required at the schema layer would
  change the error envelope.
- **``TesterPatch`` / ``SupervisorPatch`` are structurally
  identical to ``TesterIn`` / ``SupervisorIn``.** PATCH allows
  every field to be omitted; the legacy
  ``_apply_patch_to_tester`` / ``_apply_patch_to_supervisor``
  accept the full create-shape body and treat missing keys as
  "leave alone". Splitting the types would imply a behavioural
  difference the runtime doesn't actually implement.
- **``TesterListOut`` / ``SupervisorListOut`` use the
  ``{items: [...]}`` envelope without ``next_cursor``.** Both
  resources live as inline arrays on the parent project document;
  participant counts are bounded by small constants in practice
  (a project tracks a handful of testers and supervisors, not
  thousands) so cursoring is unnecessary. The cursor field is
  omitted entirely rather than serialised as ``null`` to keep
  byte-parity with the legacy ``jsonify({"items": [...]})``.
- **``id`` is ``Optional[str]`` on the response.** The legacy
  ``_serialize_tester`` / ``_serialize_supervisor`` return
  ``tester.id`` / ``supervisor.id``, which are ``str | None`` on
  the dataclass (an unsaved tester before ``ensure_id`` runs has
  ``_id=None``). In practice every persisted record has an id,
  but the property type is honest about the unsaved transient.
- **``assistive_tech`` is ``list[str]`` on responses but
  ``Optional[list[str]]`` on requests.** On responses it always
  serialises (the dataclass default-factory yields ``[]``), but
  on requests the field is optional — a body without
  ``assistive_tech`` is accepted and treated as "no AT specified".
  The legacy ``_build_tester_from_body`` defaults to ``[]`` when
  the key is missing.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Testers — request bodies
# ---------------------------------------------------------------------------


class TesterIn(StrictModel):
    """POST/PUT body for creating or replacing a lived-experience tester.

    Mirrors the legacy ``_build_tester_from_body`` validator.

    Fields:

    - ``name`` — semantically required on create / replace; the
      handler surfaces its own 400 with field path ``name`` and
      code ``required`` on missing/blank values. Typed
      ``Optional[str]`` to preserve the legacy error envelope.
    - ``email`` — optional, free-text. Empty string is normalised
      to ``None`` by the handler.
    - ``disability_type`` — optional, free-text label (e.g.
      "Blind", "Low Vision", "Deaf", "Motor Disability"). Not a
      closed set — projects vary on terminology.
    - ``assistive_tech`` — optional list of free-text AT names
      (e.g. ``["JAWS", "Screen Magnifier"]``). Defaults to ``[]``
      when omitted.
    - ``notes`` — optional, free-text.
    """

    name: Optional[str] = None
    email: Optional[str] = None
    disability_type: Optional[str] = None
    assistive_tech: Optional[list[str]] = None
    notes: Optional[str] = None


class TesterPatch(StrictModel):
    """PATCH body for partially updating a lived-experience tester.

    Structurally identical to :class:`TesterIn` — every field
    optional. PATCH is intentionally a thin alias rather than a
    distinct type because ``_apply_patch_to_tester`` accepts the
    full create-shape body and treats missing keys as "leave alone".
    Splitting the types would imply a behavioural difference the
    runtime doesn't actually implement.
    """

    name: Optional[str] = None
    email: Optional[str] = None
    disability_type: Optional[str] = None
    assistive_tech: Optional[list[str]] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Testers — response shapes
# ---------------------------------------------------------------------------


class TesterOut(StrictModel):
    """Response shape for a single lived-experience tester.

    Mirrors :func:`_serialize_tester` byte-for-byte. Used by:

    - ``GET /projects/<project_id>/testers/<tester_id>``
    - ``POST /projects/<project_id>/testers`` (create response)
    - ``PUT /projects/<project_id>/testers/<tester_id>`` (replace response)
    - ``PATCH /projects/<project_id>/testers/<tester_id>`` (patch response)
    - ``GET /projects/<project_id>/testers`` (inside ``items``)

    Field details:

    - ``id`` — string UUID assigned by ``ensure_id()`` on insert;
      ``None`` only for transient unsaved testers that never
      appear over the wire.
    - ``name`` — required on the response (the model enforces it).
    - ``assistive_tech`` — always a list (the dataclass default
      factory yields ``[]`` when no AT is specified).
    """

    id: Optional[str] = None
    name: str
    email: Optional[str] = None
    disability_type: Optional[str] = None
    assistive_tech: list[str]
    notes: Optional[str] = None


class TesterListOut(StrictModel):
    """Response body for ``GET /projects/<project_id>/testers``.

    Non-paginated ``{items: [...]}`` envelope. Tester counts are
    bounded by small constants in practice (a project tracks a
    handful of participants, not thousands) so cursoring is
    unnecessary.
    """

    items: list[TesterOut]


# ---------------------------------------------------------------------------
# Supervisors — request bodies
# ---------------------------------------------------------------------------


class SupervisorIn(StrictModel):
    """POST/PUT body for creating or replacing a test supervisor.

    Mirrors the legacy ``_build_supervisor_from_body`` validator.

    Fields:

    - ``name`` — semantically required on create / replace; the
      handler surfaces its own 400 with field path ``name`` and
      code ``required`` on missing/blank values. Typed
      ``Optional[str]`` to preserve the legacy error envelope.
    - ``email`` — optional, free-text.
    - ``role`` — optional, free-text job title (e.g.
      "Accessibility Specialist", "Research Lead").
    - ``organization`` — optional, free-text affiliation.
    - ``notes`` — optional, free-text.
    """

    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    organization: Optional[str] = None
    notes: Optional[str] = None


class SupervisorPatch(StrictModel):
    """PATCH body for partially updating a test supervisor.

    Structurally identical to :class:`SupervisorIn` — every field
    optional. PATCH is a thin alias rather than a distinct type
    because ``_apply_patch_to_supervisor`` accepts the full
    create-shape body and treats missing keys as "leave alone".
    """

    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    organization: Optional[str] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Supervisors — response shapes
# ---------------------------------------------------------------------------


class SupervisorOut(StrictModel):
    """Response shape for a single test supervisor.

    Mirrors :func:`_serialize_supervisor` byte-for-byte. Used by:

    - ``GET /projects/<project_id>/supervisors/<supervisor_id>``
    - ``POST /projects/<project_id>/supervisors`` (create response)
    - ``PUT /projects/<project_id>/supervisors/<supervisor_id>`` (replace response)
    - ``PATCH /projects/<project_id>/supervisors/<supervisor_id>`` (patch response)
    - ``GET /projects/<project_id>/supervisors`` (inside ``items``)

    Field details:

    - ``id`` — string UUID assigned by ``ensure_id()`` on insert;
      ``None`` only for transient unsaved supervisors that never
      appear over the wire.
    - ``name`` — required on the response (the model enforces it).
    """

    id: Optional[str] = None
    name: str
    email: Optional[str] = None
    role: Optional[str] = None
    organization: Optional[str] = None
    notes: Optional[str] = None


class SupervisorListOut(StrictModel):
    """Response body for ``GET /projects/<project_id>/supervisors``.

    Non-paginated ``{items: [...]}`` envelope. Supervisor counts
    are bounded by small constants in practice (a project tracks
    a handful of supervisors, not thousands) so cursoring is
    unnecessary.
    """

    items: list[SupervisorOut]
