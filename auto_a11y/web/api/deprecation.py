"""RFC 8594 / RFC 9421 deprecation signalling for legacy JSON routes.

The legacy admin frontend talks to a handful of bespoke JSON routes
that pre-date the canonical ``/api/v1`` REST surface (e.g.
``/projects/api/list``, ``/recordings/api/list``,
``/testing/api/run-tests``). Per docs/REST_API_ROADMAP.md §4.4 those
routes stay alive until the issue #21 frontend migration retires
them, but the responses ship with deprecation headers so external
callers (and future frontend developers reading the network panel)
know to switch.

The standard signalling vocabulary is:

- ``Deprecation: true`` (RFC 8594 §2) — flag that this route is
  deprecated. The value is the date the deprecation took effect, in
  HTTP-date form, or the literal string ``"true"`` when no specific
  date applies.
- ``Sunset: <HTTP-date>`` (RFC 8594 §3) — the date the route will be
  removed. Callers SHOULD migrate before this date.
- ``Link: <successor-url>; rel="successor-version"`` (RFC 8631 §4.1)
  — points at the canonical REST replacement.

Usage:

.. code-block:: python

    @deprecated(
        successor="/api/v1/projects",
        sunset="2026-09-01",
    )
    @projects_bp.route("/api/list")
    def legacy_project_list() -> Response:
        ...

Headers are applied via a wrapping closure, so the decorator must be
*outside* the route decorator (closer to ``def``) so the wrapper is
seen first by Flask's URL map.

Stack order rationale: Flask's view decorators apply bottom-up, so
``@deprecated`` belongs above ``@blueprint.route`` to wrap the view
function the route ultimately calls. (Conversely, applying it inside
the route decorator wouldn't wrap the final responder.) The doctest
in :func:`deprecated` shows both shapes.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import wraps
from typing import Any, TypeVar, cast

from flask import Response, make_response

_F = TypeVar("_F", bound=Callable[..., Any])


_HTTP_DATE_FORMAT = "%a, %d %b %Y %H:%M:%S GMT"


def _to_http_date(value: str | datetime) -> str:
    """Format a ``YYYY-MM-DD`` string or :class:`datetime` as HTTP-date.

    Accepts:

    - ``datetime`` (naive treated as UTC)
    - ``"YYYY-MM-DD"`` string (treated as UTC midnight)
    - already-formatted HTTP-date string (returned as-is)

    Raises:
        ValueError: when the input is a string but neither an ISO
            date nor a valid HTTP-date.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            dt = value.replace(tzinfo=timezone.utc)
        else:
            dt = value.astimezone(timezone.utc)
        return dt.strftime(_HTTP_DATE_FORMAT)

    # Try ISO date first; fall back to assuming caller passed an
    # already-formatted HTTP-date.
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        # Not an ISO date — validate that it parses as HTTP-date.
        try:
            datetime.strptime(value, _HTTP_DATE_FORMAT)
        except ValueError as exc:
            raise ValueError(
                f"value must be YYYY-MM-DD or HTTP-date, got: {value!r}"
            ) from exc
        return value
    return parsed.replace(tzinfo=timezone.utc).strftime(_HTTP_DATE_FORMAT)


def _format_link_header(
    successor: str, *, rel: str = "successor-version",
) -> str:
    """Build an RFC 8288 Link header value pointing at the successor.

    The URL is wrapped in angle brackets per the spec; the rel param
    is double-quoted. Multiple Link entries can be added by the caller
    via ``Link`` header append — this function returns one value.
    """
    return f'<{successor}>; rel="{rel}"'


def apply_deprecation_headers(
    response: Response,
    *,
    successor: str | None,
    sunset: str | datetime | None,
    deprecation: str | datetime | None = None,
) -> Response:
    """Mutate ``response`` in place with the three RFC 8594 headers.

    Returns the same ``response`` so the call chains naturally in a
    decorator wrapper.

    - ``successor`` — URL of the replacement REST endpoint. Adds a
      ``Link: <url>; rel="successor-version"`` header. ``None`` skips
      the Link (use sparingly — routes without a successor are rare
      and usually signal an in-progress migration).
    - ``sunset`` — when the route will be removed. ISO date string,
      HTTP-date string, or :class:`datetime`.
    - ``deprecation`` — when deprecation took effect. Same shape as
      ``sunset``. Defaults to ``"true"`` (no specific date) when
      omitted.
    """
    if deprecation is None:
        response.headers["Deprecation"] = "true"
    else:
        response.headers["Deprecation"] = _to_http_date(deprecation)

    if sunset is not None:
        response.headers["Sunset"] = _to_http_date(sunset)

    if successor is not None:
        # Append to any existing Link header instead of overwriting —
        # routes can carry their own pagination / preload Links.
        existing = response.headers.get("Link")
        new_link = _format_link_header(successor)
        response.headers["Link"] = (
            f"{existing}, {new_link}" if existing else new_link
        )

    return response


def deprecated(
    *,
    successor: str | None = None,
    sunset: str | datetime | None = None,
    deprecation: str | datetime | None = None,
) -> Callable[[_F], _F]:
    """Mark a view function as deprecated.

    Adds RFC 8594 ``Deprecation`` and ``Sunset`` headers (and a
    ``Link: rel="successor-version"`` when ``successor`` is given) to
    every response the wrapped view returns, regardless of whether
    the view emitted a Flask :class:`Response`, a ``(body, status)``
    tuple, or a bare string.

    The wrapper normalises the return value through
    :func:`flask.make_response` so headers are applied at exactly one
    layer — the original view's body / status / headers are preserved.

    Example:

    .. code-block:: python

        @deprecated(
            successor="/api/v1/projects",
            sunset="2026-09-01",
        )
        @projects_bp.route("/api/list")
        def legacy_project_list() -> Response:
            return jsonify({"success": True, "projects": [...]})

    The wrapper runs after the view, so headers are added *only* on
    success paths. View-raised exceptions propagate unmodified — by
    design, since a 5xx response from a deprecated route is no more
    or less deprecated than a 2xx one, and we want Flask's error
    handlers to control the failure shape.
    """
    def decorator(view: _F) -> _F:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Response:
            result = view(*args, **kwargs)
            response = make_response(result)
            return apply_deprecation_headers(
                response,
                successor=successor,
                sunset=sunset,
                deprecation=deprecation,
            )
        return cast(_F, wrapper)
    return decorator


__all__ = [
    "apply_deprecation_headers",
    "deprecated",
]
