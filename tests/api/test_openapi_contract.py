"""Auto-iterating contract test suite for the documented /api/v1/* surface.

Two tiers:

(a) **Structural tests** walk the ``@document`` REGISTRY without requiring
    MongoDB. They assert the registry is populated by the route-module
    side effects and that every documented endpoint has at least one
    documented response or error. These always run.

(b) **Per-endpoint contract tests** fire requests through Flask's test
    client and validate response bodies against the Pydantic model the
    endpoint declared in ``@document(response_200=...)``. Public,
    unauthenticated endpoints (``/health``, ``/health/pdf``) are the
    seed contract targets — they work today without MongoDB because the
    handlers swallow database exceptions. Auth-required endpoints will
    join the contract surface as fixture infrastructure grows; until
    MongoDB is available in the test environment they skip cleanly.

The 80 % coverage gate from the rollout plan is relaxed at landing time
to a permissive 1 % floor — its job is to assert "at least *something*
is contract-tested" so a future regression that drops the seed entries
still trips a failure. The threshold should tighten as fixtures land
(see ``tests/api/conftest.py`` and the §5.x suites).

Environment notes
-----------------
- MongoDB is not required for the structural tier or for the public
  ``/health*`` contract checks.
- MongoDB **is** required for any auth-gated endpoint added to
  ``_MINIMAL_REQUESTS`` later. Those entries should call
  ``pytest.importorskip`` / consult :func:`_mongo_available` and skip
  cleanly when the daemon is unreachable.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient
from auto_a11y.web.api import register_api_error_handlers
from auto_a11y.web.api.openapi.document import register_documented_views
from auto_a11y.web.api.openapi.registry import (
    EndpointDoc,
    REGISTRY,
    registry_snapshot,
    reset_registry_for_tests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mongo_available() -> bool:
    """Return ``True`` iff a local MongoDB daemon answers a ping promptly.

    The contract tier needs MongoDB for any endpoint that touches
    ``current_app.db`` outside a defensive ``try / except`` block. Public
    health probes are an exception — they catch ``Exception`` and degrade
    gracefully — but everything else demands a live database.
    """
    try:
        from pymongo import MongoClient

        # MongoClient is generic in pymongo's stubs; bind the document type
        # so mypy can infer the client's type without needing a separate alias.
        client: MongoClient[dict[str, object]] = MongoClient(
            "mongodb://localhost:27017/",
            serverSelectionTimeoutMS=500,
        )
        client.server_info()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def contract_app() -> Iterator[Flask]:
    """Flask app with the full documented surface registered.

    Side effects:
        * Imports ``auto_a11y.web.routes.api`` and
          ``auto_a11y.web.routes.v1_openapi`` so every ``@document``
          decorator runs at module load.
        * Calls :func:`register_documented_views` to insert each view's
          ``EndpointDoc`` into the module-level ``REGISTRY`` keyed by
          ``(method, rule)`` — the registry is the source of truth that
          every test below iterates over.

    The registry is reset on fixture entry so a previous test module's
    state cannot leak in. We do **not** reset on teardown — later
    test modules in the same session expect the registry to remain
    populated.
    """
    reset_registry_for_tests()

    # Import for decorator side effects — every @document call runs here.
    from auto_a11y.web.routes import api as api_routes
    from auto_a11y.web.routes import v1_openapi

    # Silence "imported but unused".
    _ = (api_routes.api_bp, v1_openapi.openapi_json, v1_openapi.openapi_yaml)

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    # Attach the production Config so handlers that read ``current_app.app_config``
    # (e.g. /health/pdf reading PDF_STORAGE_DIR / GHOSTSCRIPT_PATH) can run
    # without falling over with AttributeError. We do **not** attach a Mongo
    # ``db`` — handlers that need one must skip when MongoDB is unavailable.
    from config import Config

    setattr(flask_app, "app_config", Config())

    register_api_error_handlers(flask_app)
    flask_app.register_blueprint(api_routes.api_bp, url_prefix="/api/v1")
    register_documented_views(flask_app)

    yield flask_app


@pytest.fixture(scope="module")
def contract_client(contract_app: Flask) -> FlaskClient:
    """Test client paired with the module-scoped ``contract_app``."""
    return contract_app.test_client()


# ---------------------------------------------------------------------------
# Tier (a) — structural tests over the REGISTRY
# ---------------------------------------------------------------------------


def test_registry_is_populated(contract_app: Flask) -> None:
    """The ``@document`` registry holds the full /api/v1/* surface.

    A floor of 100 documented routes is conservative — the §5.1–§5.16
    rollout (Tasks 11-26) registered ~140 entries. Dropping below 100
    means a route module's decorators stopped firing, almost certainly
    a regression in the import-side-effect chain that drives
    documentation.
    """
    # contract_app is required so the fixture's import side effects ran.
    _ = contract_app
    assert len(REGISTRY) > 100, f"expected >100 documented routes, got {len(REGISTRY)}"


def test_every_documented_endpoint_has_at_least_one_response_or_error(
    contract_app: Flask,
) -> None:
    """Every ``@document`` call must declare a success response or error code.

    Otherwise the generated OpenAPI operation has no ``responses`` block,
    which is invalid against OpenAPI 3.1 (every operation must define
    at least one response). Catching this here is cheaper than waiting
    for the spec-validation gate.
    """
    _ = contract_app
    snapshot: dict[tuple[str, str], EndpointDoc] = registry_snapshot()
    missing: list[tuple[str, str]] = [
        key for key, doc in snapshot.items() if not doc.responses and not doc.errors
    ]
    assert not missing, (
        f"endpoints with no documented responses or errors: {missing}"
    )


# ---------------------------------------------------------------------------
# Tier (b) — per-endpoint contract tests
# ---------------------------------------------------------------------------


# Seed contract targets. Each entry maps (method, registry-rule) to the
# request payload used by the test client. Public endpoints come first
# because they require neither MongoDB nor authentication.
#
# Adding an entry here is enough to turn a documented endpoint into a
# contract-tested one — the test below picks it up automatically and
# validates the response body against ``doc.responses[status]``.
_MINIMAL_REQUESTS: dict[tuple[str, str], dict[str, object]] = {
    ("GET", "/health"): {},
    ("GET", "/health/pdf"): {},
}


@pytest.mark.parametrize(
    "method,rule",
    list(_MINIMAL_REQUESTS.keys()),
    ids=lambda value: (
        f"{value[0]}_{value[1]}".replace("/", "_")
        if isinstance(value, tuple)
        else str(value)
    ),
)
def test_endpoint_response_matches_documented_schema(
    contract_app: Flask,
    contract_client: FlaskClient,
    method: str,
    rule: str,
) -> None:
    """Fire a real request and validate the response against the documented model.

    For every seed entry in :data:`_MINIMAL_REQUESTS`:

    1. Look up the endpoint in the registry. Skip if absent.
    2. Issue the request via the Flask test client at ``/api/v1{rule}``.
    3. Pull the response model from ``doc.responses[status]``.
    4. Run ``model.model_validate(body)`` — passes iff the wire bytes
       exactly satisfy the documented Pydantic schema (``extra='forbid'``
       on ``StrictModel`` makes this strict-in-the-API sense).
    """
    _ = contract_app

    doc = REGISTRY.get((method, rule))
    if doc is None:
        pytest.skip(f"{method} {rule} not in registry — endpoint not @documented yet")

    if doc.security != "public" and not _mongo_available():
        pytest.skip(
            f"{method} {rule} requires authentication; MongoDB not available to seed test users"
        )

    url = "/api/v1" + rule
    payload = _MINIMAL_REQUESTS[(method, rule)]

    if method == "GET":
        response = contract_client.get(url)
    elif method == "POST":
        response = contract_client.post(url, json=payload)
    else:
        pytest.skip(f"method {method} not yet supported by contract test")

    # Both 200 and 503 are documented for /health/pdf; pick whichever
    # model the endpoint declared for the status we actually got.
    response_model = doc.responses.get(response.status_code)
    if response_model is None:
        # The status code might be a documented error code (e.g. 503) for
        # which only a Problem body is expected. In that case the
        # response body is RFC 7807 and not in doc.responses — accept it
        # as a documented error path without further schema checking
        # here (the Problem shape is exercised by tests/api/test_errors.py).
        assert response.status_code in doc.errors, (
            f"{method} {rule} returned {response.status_code}; not in "
            f"documented responses {sorted(doc.responses)} or errors {doc.errors}"
        )
        return

    body = response.get_json()
    assert body is not None, f"{method} {rule}: response body was not JSON"
    # ``model_validate`` raises ValidationError on mismatch — that bubbles
    # up to pytest as a test failure with a precise diff.
    response_model.model_validate(body)


# ---------------------------------------------------------------------------
# Coverage gate
# ---------------------------------------------------------------------------


def test_contract_coverage_meets_threshold(contract_app: Flask) -> None:
    """At least ``THRESHOLD`` of model-returning endpoints are contract-tested.

    "Model-returning" means the endpoint declared at least one response
    whose schema is a Pydantic ``BaseModel`` subclass — the only kind of
    response shape we can validate. Endpoints that document only error
    codes (no 2xx body) are excluded from the denominator.

    The threshold is intentionally permissive at rollout (1 %). Each
    fixture-bearing endpoint added to :data:`_MINIMAL_REQUESTS` ratchets
    the realised coverage upward; once auth-bearing fixtures land the
    threshold can be tightened — first to 25 %, ultimately to 80 % per
    the rollout plan. The gate exists so a future change that removes
    every seed entry trips a hard failure, not so it constrains the
    rollout pace.
    """
    _ = contract_app

    # ``EndpointDoc.responses`` is typed ``Mapping[int, type[BaseModel]]``,
    # so any non-empty responses mapping is by construction a set of
    # ``BaseModel`` subclasses. No runtime isinstance check needed.
    eligible: list[tuple[str, str]] = [
        key for key, doc in REGISTRY.items() if doc.responses
    ]

    covered = sum(1 for key in eligible if key in _MINIMAL_REQUESTS)
    total = len(eligible)
    ratio = covered / total if total else 1.0

    threshold = 0.01
    assert ratio >= threshold, (
        f"contract coverage is {covered}/{total} = {ratio:.2%}; "
        f"required floor is {threshold:.2%}"
    )
