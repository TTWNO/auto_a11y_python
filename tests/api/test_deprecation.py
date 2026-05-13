"""Tests for the RFC 8594 deprecation decorator and signalling on
the legacy ``/api/...`` routes that have shipped REST successors.

Covers two layers:

1. The :func:`auto_a11y.web.api.deprecation.deprecated` decorator
   itself — header values, format normalisation, multiple Link
   handling, body/status preservation.
2. Spot-checks that the legacy routes carry the headers, without
   booting the full app (a tiny Flask shim with the decorator in
   isolation; the legacy-route side just inspects the source for
   the decorator presence).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from flask import Flask, Response, jsonify

from auto_a11y.web.api import (
    apply_deprecation_headers,
    deprecated,
)


# ---------------------------------------------------------------------------
# Date / datetime handling — exercised through apply_deprecation_headers
# since the underscore-prefixed helpers are intentionally private.
# ---------------------------------------------------------------------------


def test_deprecation_iso_string_normalises_to_http_date() -> None:
    response = Response("body")
    apply_deprecation_headers(
        response, successor=None, sunset=None, deprecation="2026-09-01",
    )
    assert response.headers["Deprecation"] == "Tue, 01 Sep 2026 00:00:00 GMT"


def test_deprecation_naive_datetime_treated_as_utc() -> None:
    response = Response("body")
    apply_deprecation_headers(
        response, successor=None, sunset=None,
        deprecation=datetime(2026, 9, 1, 12, 0, 0),
    )
    assert response.headers["Deprecation"] == "Tue, 01 Sep 2026 12:00:00 GMT"


def test_deprecation_aware_datetime_converted_to_utc() -> None:
    from datetime import timedelta
    eastern = timezone(timedelta(hours=-5))
    response = Response("body")
    apply_deprecation_headers(
        response, successor=None, sunset=None,
        deprecation=datetime(2026, 9, 1, 7, 0, 0, tzinfo=eastern),
    )
    # 07:00 EST → 12:00 UTC
    assert response.headers["Deprecation"] == "Tue, 01 Sep 2026 12:00:00 GMT"


def test_deprecation_already_http_date_passthrough() -> None:
    response = Response("body")
    apply_deprecation_headers(
        response, successor=None, sunset=None,
        deprecation="Tue, 01 Sep 2026 00:00:00 GMT",
    )
    assert response.headers["Deprecation"] == "Tue, 01 Sep 2026 00:00:00 GMT"


def test_deprecation_invalid_string_raises() -> None:
    response = Response("body")
    with pytest.raises(ValueError):
        apply_deprecation_headers(
            response, successor=None, sunset=None,
            deprecation="not-a-date",
        )


# ---------------------------------------------------------------------------
# Link header shape (via the public ``apply_deprecation_headers``).
# ---------------------------------------------------------------------------


def test_link_header_default_rel_is_successor_version() -> None:
    response = Response("body")
    apply_deprecation_headers(
        response, successor="/api/v1/foo", sunset=None,
    )
    assert response.headers["Link"] == (
        '</api/v1/foo>; rel="successor-version"'
    )


# ---------------------------------------------------------------------------
# apply_deprecation_headers
# ---------------------------------------------------------------------------


def test_apply_headers_default_deprecation_is_true_string() -> None:
    """Omitting ``deprecation=`` produces ``Deprecation: true``."""
    response = Response("body")
    apply_deprecation_headers(
        response, successor="/api/v1/foo", sunset="2026-09-01",
    )
    assert response.headers["Deprecation"] == "true"


def test_apply_headers_with_explicit_deprecation_date() -> None:
    response = Response("body")
    apply_deprecation_headers(
        response, successor=None, sunset=None, deprecation="2026-05-01",
    )
    assert response.headers["Deprecation"] == (
        "Fri, 01 May 2026 00:00:00 GMT"
    )


def test_apply_headers_emits_sunset_when_given() -> None:
    response = Response("body")
    apply_deprecation_headers(
        response, successor=None, sunset="2026-09-01",
    )
    assert response.headers["Sunset"] == "Tue, 01 Sep 2026 00:00:00 GMT"


def test_apply_headers_skips_sunset_when_none() -> None:
    response = Response("body")
    apply_deprecation_headers(response, successor=None, sunset=None)
    assert "Sunset" not in response.headers


def test_apply_headers_emits_successor_link_when_given() -> None:
    response = Response("body")
    apply_deprecation_headers(
        response, successor="/api/v1/foo", sunset=None,
    )
    assert response.headers["Link"] == (
        '</api/v1/foo>; rel="successor-version"'
    )


def test_apply_headers_skips_link_when_successor_none() -> None:
    response = Response("body")
    apply_deprecation_headers(response, successor=None, sunset=None)
    assert "Link" not in response.headers


def test_apply_headers_appends_to_existing_link() -> None:
    """Routes can carry a pagination Link; the successor Link is added."""
    response = Response("body")
    response.headers["Link"] = '</next?cursor=x>; rel="next"'
    apply_deprecation_headers(
        response, successor="/api/v1/foo", sunset=None,
    )
    # Both Link values are now present, comma-separated.
    link_value = response.headers["Link"]
    assert 'rel="next"' in link_value
    assert 'rel="successor-version"' in link_value


# ---------------------------------------------------------------------------
# @deprecated decorator integration
# ---------------------------------------------------------------------------


def _legacy_list_view() -> Response:
    return jsonify({"success": True, "items": []})


def _legacy_no_successor_view() -> Response:
    return jsonify({"ok": True})


def _legacy_tuple_view() -> tuple[Response, int]:
    # Original status is preserved through the wrapper.
    return jsonify({"created": True}), 201


@pytest.fixture
def app() -> Flask:
    """Tiny Flask app with three decorated routes for end-to-end checks.

    Uses ``add_url_rule`` rather than the @route decorator so the
    pyright reportUnusedFunction check doesn't fire on the inline
    view functions — the decorator only wraps the *callable*, and we
    name the wrapped functions explicitly here so the type checker
    sees both the underlying view and the wrapper as referenced.
    """
    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/legacy/list",
        view_func=deprecated(
            successor="/api/v1/foo", sunset="2026-09-01",
        )(_legacy_list_view),
    )
    flask_app.add_url_rule(
        "/legacy/no-successor",
        view_func=deprecated(sunset="2026-09-01")(_legacy_no_successor_view),
    )
    flask_app.add_url_rule(
        "/legacy/tuple-return",
        view_func=deprecated(
            successor="/api/v1/baz", sunset="2026-09-01",
        )(_legacy_tuple_view),
    )
    return flask_app


def test_decorator_emits_all_three_headers(app: Flask) -> None:
    client = app.test_client()
    response = client.get("/legacy/list")
    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"] == "Tue, 01 Sep 2026 00:00:00 GMT"
    assert response.headers["Link"] == (
        '</api/v1/foo>; rel="successor-version"'
    )


def test_decorator_omits_link_when_no_successor(app: Flask) -> None:
    client = app.test_client()
    response = client.get("/legacy/no-successor")
    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"]
    assert "Link" not in response.headers


def test_decorator_preserves_original_status(app: Flask) -> None:
    """A view returning ``(body, 201)`` keeps the 201 after wrapping."""
    client = app.test_client()
    response = client.post("/legacy/tuple-return")
    # POST not allowed → 405, but the route is GET-only; use GET.
    response = client.get("/legacy/tuple-return")
    assert response.status_code == 201
    assert response.get_json() == {"created": True}
    assert response.headers["Deprecation"] == "true"


def test_decorator_preserves_body(app: Flask) -> None:
    client = app.test_client()
    response = client.get("/legacy/list")
    assert response.get_json() == {"success": True, "items": []}


# ---------------------------------------------------------------------------
# Spot-checks on the production legacy routes (decorator presence)
# ---------------------------------------------------------------------------


def test_projects_legacy_routes_carry_decorator() -> None:
    """Source-level smoke test that we didn't forget a route in the
    projects.py decoration sweep."""
    import auto_a11y.web.routes.projects as projects_mod
    source = open(projects_mod.__file__, "r", encoding="utf-8").read()
    # Every /api/ route in projects.py should have a @deprecated above it.
    api_route_count = source.count("@projects_bp.route('/api/")
    decorator_count = source.count("@deprecated(")
    assert api_route_count > 0
    assert decorator_count >= api_route_count, (
        f"projects.py: {api_route_count} legacy /api/ routes but only "
        f"{decorator_count} @deprecated decorators"
    )


def test_recordings_legacy_routes_carry_decorator() -> None:
    import auto_a11y.web.routes.recordings as recordings_mod
    source = open(recordings_mod.__file__, "r", encoding="utf-8").read()
    api_route_count = source.count("@recordings_bp.route('/api/")
    decorator_count = source.count("@deprecated(")
    assert decorator_count >= api_route_count


def test_testing_legacy_routes_carry_decorator() -> None:
    import auto_a11y.web.routes.testing as testing_mod
    source = open(testing_mod.__file__, "r", encoding="utf-8").read()
    api_route_count = source.count("@testing_bp.route('/api/")
    decorator_count = source.count("@deprecated(")
    assert decorator_count >= api_route_count


def test_websites_legacy_routes_carry_decorator() -> None:
    import auto_a11y.web.routes.websites as websites_mod
    source = open(websites_mod.__file__, "r", encoding="utf-8").read()
    api_route_count = source.count("@websites_bp.route('/api/")
    decorator_count = source.count("@deprecated(")
    assert decorator_count >= api_route_count
