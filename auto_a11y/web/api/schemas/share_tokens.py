"""Pydantic schemas for the /api/v1/share-tokens endpoints (§5.10).

Wire-shape preservation
-----------------------
Share tokens grant read-only public access to project- or website-scoped
results via signed URLs. They are surfaced through three collection
shapes and three per-resource operations:

- POST ``/projects/<id>/share-tokens`` — create a project-scoped token.
- POST ``/websites/<id>/share-tokens`` — create a website-scoped token.
- GET ``/projects/<id>/share-tokens`` and
  ``/websites/<id>/share-tokens`` — cursor-paginated list endpoints.
- GET ``/share-tokens/<id>`` — single-resource metadata read.
- DELETE ``/share-tokens/<id>`` — idempotent revoke (204).

The schemas in this module mirror the legacy helpers in
``auto_a11y/web/routes/api.py`` (``_serialize_share_token``,
``_parse_share_token_body``, ``_create_share_token``) byte-for-byte so
the refactor is a documentation pass, not a behavioural change.

Sensitive-payload contract
~~~~~~~~~~~~~~~~~~~~~~~~~~
The raw token string (and the derived ``public_url`` that embeds it) is
returned ONLY by the create handlers, in the same response that
persists the token. Every subsequent read — list, single GET — returns
metadata without the token value, so a leaked database read or token id
cannot be exchanged for the signing material. The SHA-256
``token_hash`` is the only persisted form and is never serialized to
the wire.

This split is encoded in two distinct response models:

- :class:`ShareTokenOut` — the metadata-only shape. Used by list and
  single GET. ``token`` and ``public_url`` are *not* present.
- :class:`ShareTokenCreatedOut` — superset returned by POST. Adds
  ``token`` (the signed URL-safe string) and ``public_url`` (the
  pre-built public landing URL). These two fields are the only chance
  the caller has to capture the raw token value.

Body shape
~~~~~~~~~~
- :class:`ShareTokenIn` — POST body for both creation endpoints. The
  handler injects ``scope`` and ``scope_id`` from the URL path; the
  body must not carry them. ``label`` is semantically required (the
  handler rejects empty/whitespace-only values with a 400 carrying
  field path ``label``) but typed as ``Optional[str]`` because the
  legacy parser called ``body.get("label")`` and surfaced its own 400
  shape rather than letting Pydantic generate the error. ``expires_at``
  is an ISO 8601 datetime string or ``None`` (never expires).

Decisions worth flagging
------------------------
- ``token`` and ``public_url`` live only on :class:`ShareTokenCreatedOut`.
  Putting them on :class:`ShareTokenOut` (even as ``Optional[str]``)
  would mean a future bug that forgot to strip them on list/GET could
  leak the signing material — the type system would not catch it. The
  split classes mean a list endpoint physically cannot construct a
  shape that carries the raw token.
- ``scope`` is the stringified ``TokenScope`` enum value (``"project"``
  / ``"website"``); kept as ``str`` rather than a ``Literal`` so the
  spec generator surfaces both values as documentation and a typo at
  the handler level produces a 500, not a wire-shape mismatch.
- ``is_valid`` is a computed property on the model (``not revoked and
  not is_expired``) — kept on the wire because the legacy serializer
  emits it and the frontend reads it directly to decide whether to
  render the "revoke" button.
"""
from __future__ import annotations

from typing import Optional

from auto_a11y.web.api.schemas.common import StrictModel


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


class ShareTokenIn(StrictModel):
    """POST body for creating a share token.

    Shared by ``POST /projects/<id>/share-tokens`` and
    ``POST /websites/<id>/share-tokens``; the handler injects
    ``scope`` and ``scope_id`` from the URL path.

    - ``label`` is required at the semantic level (the handler rejects
      empty/whitespace-only values) but typed as ``Optional[str]`` here
      because the legacy parser permitted the field to be omitted (it
      then surfaces a 400 with field path ``label``, code ``required``).
    - ``expires_at`` is an ISO 8601 datetime string or ``null`` (never
      expires). Parsed by ``_parse_iso_datetime`` to preserve the legacy
      400-shape on parse failures.
    """

    label: Optional[str] = None
    expires_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Response bodies
# ---------------------------------------------------------------------------


class ShareTokenOut(StrictModel):
    """Metadata-only response shape for a single share token.

    Mirrors :func:`_serialize_share_token` byte-for-byte. Used by:

    - ``GET /share-tokens/<id>``
    - ``GET /projects/<id>/share-tokens`` (inside ``items``)
    - ``GET /websites/<id>/share-tokens`` (inside ``items``)

    **Never carries the raw token or the SHA-256 hash.** The signing
    material is exposed only by :class:`ShareTokenCreatedOut`, returned
    by the create handlers in the same response that persists the token.
    Splitting create vs read into two distinct models means a list
    endpoint physically cannot construct a payload that leaks the
    token.

    Field details:

    - ``id`` — stringified Mongo ``_id``; ``None`` only for transient
      unsaved tokens that never appear over the wire.
    - ``scope`` — stringified ``TokenScope`` value (``"project"`` /
      ``"website"``); kept as ``str`` to keep the wire forward-compatible
      with any future scope kinds.
    - ``scope_id`` — the project or website ObjectId string the token
      grants access to.
    - ``created_at``, ``expires_at``, ``revoked_at``, ``last_used`` —
      ISO 8601 datetime strings (or ``None`` when unset).
    - ``is_valid`` — computed property on the model (``not revoked and
      not is_expired``); the frontend reads this directly to decide
      whether to render the "revoke" button.
    """

    id: Optional[str] = None
    scope: str
    scope_id: str
    label: str
    created_by: str
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    revoked: bool
    revoked_at: Optional[str] = None
    last_used: Optional[str] = None
    use_count: int
    is_valid: bool


class ShareTokenCreatedOut(ShareTokenOut):
    """Response body for ``POST /{projects,websites}/<id>/share-tokens``.

    Superset of :class:`ShareTokenOut` that carries the raw token value.
    This is the only response shape on the entire share-token surface
    that surfaces the signing material — every subsequent read returns
    a :class:`ShareTokenOut` with these two fields absent.

    - ``token`` — the URL-safe signed string. The caller MUST capture
      it from this single response; the server cannot return it again
      because only the SHA-256 hash is persisted.
    - ``public_url`` — pre-built landing URL (``{host}/t/<token>/``).
      The handler builds it from ``request.host_url`` to keep the result
      correct in test contexts where the public blueprint is not
      registered (and ``url_for('public.token_landing', ...)`` would
      raise ``BuildError``).
    """

    token: str
    public_url: str


class ShareTokenListOut(StrictModel):
    """Response body for the share-token list endpoints.

    Cursor-paginated; same ``{items, next_cursor}`` shape as the other
    v1 list endpoints. Used by both
    ``GET /api/v1/projects/<id>/share-tokens`` and
    ``GET /api/v1/websites/<id>/share-tokens``.

    ``items`` carries :class:`ShareTokenOut` (metadata only) — never
    :class:`ShareTokenCreatedOut`. The split-class design means it is
    not physically possible to serialise a list response that leaks
    the raw token, even by mistake.
    """

    items: list[ShareTokenOut]
    next_cursor: Optional[str] = None
