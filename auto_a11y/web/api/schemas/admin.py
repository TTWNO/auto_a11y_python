"""Pydantic schemas for the ``/api/v1/admin/settings/*`` endpoints (§5.14).

Wire-shape preservation
-----------------------
This module covers five superadmin-only handlers that read and mutate
the runtime configuration store:

- ``GET /admin/settings`` — full settings document. Returns a
  ``{drupal: DrupalSettingsOut, sections: [SettingsSectionOut, ...]}``
  envelope. The drupal section gets its own shape because its field
  set is special-cased (not part of the generic ``CONFIG_SECTIONS``
  registry); every other section uses the generic per-field metadata
  projection produced by ``_serialize_settings_section``.

- ``PATCH /admin/settings/drupal`` — dedicated drupal patch. Accepts
  ``base_url``, ``username``, ``password``, ``enabled``; the handler
  validates each explicitly. ``password`` omitted preserves the
  existing value (so admins don't have to re-enter the secret on every
  edit), but ``password: null`` raises 400.

- ``DELETE /admin/settings/drupal`` — clear the drupal section so the
  env-var fallback applies again. Empty 204 response.

- ``PATCH /admin/settings/<section_id>`` — generic-section patch. The
  body is a free-form ``dict[str, object]`` keyed by ``db_key`` from
  the section schema; each value is type-coerced per
  :class:`ConfigField`. Password fields with a blank string preserve
  the existing value. Unknown keys produce a 400 listing them.

- ``DELETE /admin/settings/<section_id>`` — clear a generic section.

Decisions worth flagging
------------------------
- **The generic PATCH body is a :class:`RootModel`** wrapping
  ``dict[str, object]`` rather than a typed ``StrictModel``. The set
  of valid fields varies per section (e.g. the SMTP section's keys
  differ from the LDAP section's), and the handler already does the
  full per-section validation (unknown-field detection, type
  coercion, password preservation) against the live ``CONFIG_SECTIONS``
  registry. Re-encoding that registry as Pydantic schemas here would
  duplicate the source of truth in two places and force a schema-side
  change every time a new section ships. The OpenAPI surface for the
  generic patch is therefore a free-form object — consumers are
  expected to consult the GET response for the current per-section
  schema before patching.

- **The drupal PATCH body is a typed :class:`StrictModel`** because
  its field set is stable (four fields, one of which is the
  preserve-on-omit secret). Modelling it explicitly surfaces a clean
  per-field 400 in the OpenAPI spec and lets the existing field-path
  ValidationError envelope keep firing on the handler side.

- **All four drupal patch fields are ``Optional``** so PATCH semantics
  hold (absent ⇒ no change). The handler uses
  :pyattr:`pydantic.BaseModel.model_fields_set` to distinguish "field
  omitted" from "field set to null" — the legacy ``body.get(key,
  default)`` collapses the two for missing-default lookups (returns
  ``None`` on both) but preserves the distinction when an explicit
  default is supplied. The ``fields_set`` check restores the original
  wire behaviour exactly: omitted ⇒ fall back to existing/default,
  explicit ``null`` ⇒ 400.

- **``SettingsSectionOut.values`` is ``dict[str, object]``** because
  the per-field types vary across sections (a section can hold any
  mix of bool / int / float / string / password / null fields plus
  synthetic ``<key>_set`` booleans for password fields). Modelling
  those uniformly would either lose precision (everything becomes
  ``Optional[str]``) or fan out into a section-specific union too
  noisy to document. The free-form ``object`` schema is the honest
  surface.

- **``DrupalSettingsValuesOut`` is typed concretely** even though
  ``SettingsSectionOut.values`` is free-form. Drupal's value shape is
  fixed at four well-known fields and is worth documenting precisely
  — the password is never echoed (``password_set: bool`` reports the
  bit instead).

- **The GET-style ``source`` field is typed as a plain ``str``** (not
  a ``Literal['database', 'environment', 'unset']``) because the
  underlying ``section_source`` helper can grow new source values
  (e.g. a future "file" source if we add ``settings.toml``). Keeping
  it open avoids a schema bump every time the runtime config layer
  grows a source.
"""
from __future__ import annotations

from typing import Optional

from pydantic import RootModel, StrictBool

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Shared output models — sections and values
# ---------------------------------------------------------------------------


class DrupalSettingsValuesOut(StrictModel):
    """Per-field values for the drupal section.

    Mirrors the ``values`` block of ``_serialize_drupal_settings``:
    ``base_url`` and ``username`` are echoed verbatim, ``enabled`` is
    a real JSON boolean, and ``password_set`` is a derived bit so
    callers can render a "[set]" indicator without us echoing the
    secret. The raw password is NEVER on the wire.
    """

    base_url: str
    username: str
    password_set: bool
    enabled: bool


class DrupalSettingsOut(StrictModel):
    """Top-level shape for the drupal section.

    Returned both inside the ``GET /admin/settings`` envelope (under
    the ``drupal`` key) and as the direct response of ``PATCH
    /admin/settings/drupal``. ``source`` is one of ``"database"``,
    ``"environment"``, or ``"unset"`` — typed loosely as ``str`` so
    the runtime config layer can grow new source values without a
    schema bump.
    """

    source: str
    values: DrupalSettingsValuesOut


class SettingsSectionOut(StrictModel):
    """Projection of a generic CONFIG_SECTIONS section.

    Mirrors the legacy ``_serialize_settings_section`` helper:

    - ``section_id`` — stable identifier from the section schema.
    - ``source`` — where the effective values came from (database /
      environment / default), as a free-form string.
    - ``values`` — a free-form ``object`` keyed by each ``db_key`` in
      the section schema. Booleans surface as real JSON booleans;
      integers/floats as numbers; password fields as ``null`` plus a
      sibling ``<key>_set: bool`` so callers can render a "[set]"
      indicator without us echoing secrets.

    The per-field union is intentionally not modelled — the set of
    fields is section-specific and growing it section by section in
    Pydantic would duplicate the ``ConfigField`` registry. Consumers
    should treat ``values`` as opaque and reflect the current shape
    by inspecting the section schema (the GET payload is the source
    of truth).
    """

    section_id: str
    source: str
    values: dict[str, object]


class AdminSettingsOut(StrictModel):
    """Response body for ``GET /admin/settings``.

    Top-level envelope combining the special-cased drupal section
    with the list of generic CONFIG_SECTIONS sections. The list order
    matches the registry order in ``auto_a11y.core.runtime_config``
    so the admin UI can render the sections in a stable, predictable
    sequence.
    """

    drupal: DrupalSettingsOut
    sections: list[SettingsSectionOut]


# ---------------------------------------------------------------------------
# Drupal patch — typed body
# ---------------------------------------------------------------------------


class DrupalSettingsPatchIn(StrictModel):
    """Request body for ``PATCH /admin/settings/drupal``.

    All fields are ``Optional`` so PATCH semantics hold — absent ⇒
    no change for that field. The handler uses
    :pyattr:`pydantic.BaseModel.model_fields_set` to distinguish
    "field omitted" from "field set to null":

    - ``base_url``, ``username`` — must be non-empty http(s) string
      when present; an explicit ``null`` raises 400.
    - ``password`` — omitted preserves the existing stored value
      (so admins don't re-enter the secret on every edit). An
      explicit ``null`` also preserves; only a real string overwrites.
      If no existing password is stored AND the field is omitted, a
      400 is raised (the section can't be partially configured).
    - ``enabled`` — boolean; absent ⇒ fall back to existing or
      ``true``; explicit ``null`` ⇒ 400. Typed as
      :class:`pydantic.StrictBool` so JSON string values like
      ``"true"`` are rejected at validation time rather than coerced
      (matching the legacy ``isinstance(bool)`` gate).

    The string fields are typed loosely (``Optional[str]``) rather
    than strictly because the legacy handler emits a field-path 400
    via the :class:`_FieldError` envelope on bad types; Pydantic's
    own ValidationError envelope would surface a different error
    shape. The schema documents the wire shape; the handler enforces
    the per-field validation.
    """

    base_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    enabled: Optional[StrictBool] = None


# ---------------------------------------------------------------------------
# Generic section patch — free-form body
# ---------------------------------------------------------------------------


class GenericSectionPatchIn(RootModel[dict[str, object]]):
    """Request body for ``PATCH /admin/settings/<section_id>``.

    The set of valid keys varies per section (each ``CONFIG_SECTIONS``
    entry has its own ``ConfigField`` schema) so we use a
    :class:`RootModel` wrapping ``dict[str, object]`` rather than a
    typed model. The handler does the per-section validation against
    the live registry: unknown keys produce a 400 listing them,
    typed coercion happens per :class:`ConfigField`, and password
    fields with a blank string preserve the existing value.

    The OpenAPI surface for this endpoint is therefore a free-form
    object; consumers are expected to consult the corresponding
    ``GET /admin/settings`` section payload for the current per-field
    schema before patching.
    """
