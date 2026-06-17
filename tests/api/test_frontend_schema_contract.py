"""Frontend↔backend contract guard.

After the issue-21 cutover from server-rendered POST handlers to
StrictModel-gated v1 JSON endpoints, several forms kept rendering fields
the new request schemas didn't model. Because the request schemas use
``extra='forbid'``, those fields were silently dropped (or 400'd) and the
values never persisted — e.g. ``drupal_audit_name``, ``password_hint``,
``manual_login_wait_seconds``, the website ``spa_*`` flags, and the
project type/identifier fields.

This test pins, per request schema, the set of field names the
corresponding form submits, and asserts every one is modeled by the
schema. It is a regression guard:

- Remove a field from a schema that a form still sends → this test fails.
- Add a field to a form's request body → add it to the schema AND to the
  registry below (the registry is the human-maintained contract).

It also asserts every request schema in the registry is ``extra='forbid'``
so the silent-drop failure mode stays a loud 400 for genuinely unknown
keys rather than a quiet data loss.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from auto_a11y.web.api.schemas.auth import (
    AppUserCreateIn,
    AppUserPatch,
    AuthMePatch,
)
from auto_a11y.web.api.schemas.projects import ProjectIn, ProjectPatch
from auto_a11y.web.api.schemas.test_users import LoginConfigIn
from auto_a11y.web.api.schemas.websites import ScrapingConfigModel

# (schema, fields the corresponding form sends, where it's sent from)
_CONTRACT: list[tuple[type[BaseModel], frozenset[str], str]] = [
    (
        ProjectIn,
        frozenset({
            "name", "description", "config", "drupal_audit_name",
            "project_type", "app_identifier", "device_model", "location",
        }),
        "projects/create.html buildCreateProjectBody",
    ),
    (
        ProjectPatch,
        frozenset({"name", "description", "status", "config", "drupal_audit_name"}),
        "projects/edit.html buildEditProjectBody",
    ),
    (
        AppUserCreateIn,
        frozenset({"email", "password", "display_name", "password_hint"}),
        "auth/user_create.html",
    ),
    (
        AppUserPatch,
        frozenset({"display_name", "is_superadmin", "password", "password_hint"}),
        "auth/user_edit.html (update + reset-password forms)",
    ),
    (
        AuthMePatch,
        frozenset({"display_name", "password", "password_hint"}),
        "auth/profile.html (profile + change-password forms)",
    ),
    (
        LoginConfigIn,
        frozenset({
            "authentication_method", "login_url", "username_field_selector",
            "password_field_selector", "submit_button_selector",
            "success_indicator_selector", "logout_url", "logout_button_selector",
            "logout_success_indicator_selector", "session_timeout_minutes",
            "manual_login_wait_seconds",
        }),
        "project_users/create.html + edit.html loginConfig",
    ),
    (
        ScrapingConfigModel,
        frozenset({
            "max_pages", "max_depth", "request_delay", "follow_external",
            "include_subdomains", "respect_robots", "spa_click_discovery",
            "spa_ready_selector",
        }),
        "websites/edit.html scraping_config",
    ),
]


@pytest.mark.parametrize(
    "schema, frontend_fields, source",
    _CONTRACT,
    ids=[c[0].__name__ for c in _CONTRACT],
)
def test_frontend_fields_are_modeled_by_schema(
    schema: type[BaseModel], frontend_fields: frozenset[str], source: str
) -> None:
    """Every field the frontend submits must exist on the request schema."""
    modeled = set(schema.model_fields.keys())
    missing = frontend_fields - modeled
    assert not missing, (
        f"{schema.__name__} does not model fields sent by {source}: "
        f"{sorted(missing)} — they would be silently dropped on save."
    )


@pytest.mark.parametrize(
    "schema",
    [c[0] for c in _CONTRACT],
    ids=[c[0].__name__ for c in _CONTRACT],
)
def test_request_schemas_forbid_extra(schema: type[BaseModel]) -> None:
    """Request schemas reject unknown keys (loud 400) rather than ignore them."""
    assert schema.model_config.get("extra") == "forbid"
