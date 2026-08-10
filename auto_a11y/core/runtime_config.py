"""Schema-driven env-var override system.

Mirrors the Drupal-config pattern: each section maps to a top-level key in
the ``system_settings`` singleton document. When set, the DB values
override the matching ``os.environ`` values; when unset, the env vars (and
their hard-coded defaults) apply.

The schema (:data:`CONFIG_SECTIONS`) drives both the admin UI (form
rendering) and any runtime code that wants the effective value for a key
(:func:`get_runtime_value`).

Sections in the schema cover the env vars that an operator would
reasonably want to change at runtime. Truly infrastructure-level settings
(``MONGODB_URI``, ``SECRET_KEY``, ``HOST``, ``PORT``, ``DESKTOP_MODE``,
``AUTH_ENABLED``, etc.) are deliberately not exposed here — changing them
via DB would be a chicken-and-egg problem or a security regression.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from auto_a11y.core.system_settings import SystemSettings

FieldValue = str | int | float | bool


class FieldType(str, Enum):
    """How a field is rendered, parsed, and stored."""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    PASSWORD = "password"


@dataclass(frozen=True)
class ConfigField:
    """One env-var-backed field within a section."""

    env_var: str
    db_key: str
    field_type: FieldType
    label_id: str
    help_id: str | None = None
    default: str = ""


@dataclass(frozen=True)
class ConfigSection:
    """A group of related env-var fields shown together in the admin UI."""

    section_id: str
    heading_id: str
    description_id: str
    icon: str
    fields: tuple[ConfigField, ...]
    note_id: str | None = None


def _f(env_var: str, db_key: str, ft: FieldType, label_id: str, *, help_id: str | None = None, default: str = "") -> ConfigField:
    return ConfigField(env_var=env_var, db_key=db_key, field_type=ft, label_id=label_id, help_id=help_id, default=default)


CONFIG_SECTIONS: Final[tuple[ConfigSection, ...]] = (
    ConfigSection(
        section_id="claude_ai",
        heading_id="admin-settings-claude-heading",
        description_id="admin-settings-claude-description",
        icon="bi-robot",
        note_id="admin-settings-restart-note",
        fields=(
            _f("CLAUDE_API_KEY", "api_key", FieldType.PASSWORD, "admin-settings-claude-api-key", help_id="admin-settings-claude-api-key-help"),
            _f("CLAUDE_MODEL", "model", FieldType.STRING, "admin-settings-claude-model", help_id="admin-settings-claude-model-help", default="claude-opus-4-20250514"),
            _f("CLAUDE_MAX_TOKENS", "max_tokens", FieldType.INT, "admin-settings-claude-max-tokens", default="32000"),
            _f("CLAUDE_BUDGET_TOKENS", "budget_tokens", FieldType.INT, "admin-settings-claude-budget-tokens", default="10000"),
            _f("CLAUDE_TEMPERATURE", "temperature", FieldType.FLOAT, "admin-settings-claude-temperature", default="1.0"),
            _f("CLAUDE_USE_THINKING", "use_thinking", FieldType.BOOL, "admin-settings-claude-use-thinking", default="True"),
            _f("RUN_AI_ANALYSIS", "run_ai_analysis", FieldType.BOOL, "admin-settings-run-ai-analysis", help_id="admin-settings-run-ai-analysis-help", default="True"),
        ),
    ),
    ConfigSection(
        section_id="browser",
        heading_id="admin-settings-browser-heading",
        description_id="admin-settings-browser-description",
        icon="bi-window",
        note_id="admin-settings-restart-note",
        fields=(
            _f("BROWSER_MODE", "mode", FieldType.STRING, "admin-settings-browser-mode", help_id="admin-settings-browser-mode-help", default="local"),
            _f("BROWSER_HEADLESS", "headless", FieldType.BOOL, "admin-settings-browser-headless", default="True"),
            _f("BROWSER_TIMEOUT", "timeout", FieldType.INT, "admin-settings-browser-timeout", help_id="admin-settings-browser-timeout-help", default="30000"),
            _f("BROWSER_VIEWPORT_WIDTH", "viewport_width", FieldType.INT, "admin-settings-browser-viewport-width", default="1920"),
            _f("BROWSER_VIEWPORT_HEIGHT", "viewport_height", FieldType.INT, "admin-settings-browser-viewport-height", default="1080"),
        ),
    ),
    ConfigSection(
        section_id="scraping",
        heading_id="admin-settings-scraping-heading",
        description_id="admin-settings-scraping-description",
        icon="bi-globe2",
        note_id="admin-settings-restart-note",
        fields=(
            _f("MAX_PAGES_PER_SITE", "max_pages_per_site", FieldType.INT, "admin-settings-max-pages-per-site", default="50000"),
            _f("MAX_CRAWL_DEPTH", "max_crawl_depth", FieldType.INT, "admin-settings-max-crawl-depth", default="10"),
            _f("REQUEST_DELAY", "request_delay", FieldType.FLOAT, "admin-settings-request-delay", help_id="admin-settings-request-delay-help", default="1.0"),
            _f("USER_AGENT", "user_agent", FieldType.STRING, "admin-settings-user-agent", default="Auto-A11y/1.0 Accessibility Scanner"),
            _f("RESPECT_ROBOTS_TXT", "respect_robots_txt", FieldType.BOOL, "admin-settings-respect-robots-txt", default="True"),
        ),
    ),
    ConfigSection(
        section_id="testing",
        heading_id="admin-settings-testing-heading",
        description_id="admin-settings-testing-description",
        icon="bi-bug",
        note_id="admin-settings-restart-note",
        fields=(
            _f("PARALLEL_TESTS", "parallel_tests", FieldType.INT, "admin-settings-parallel-tests", default="5"),
            _f("TEST_TIMEOUT", "test_timeout", FieldType.INT, "admin-settings-test-timeout", help_id="admin-settings-test-timeout-help", default="60000"),
            _f("MAX_TEST_WORKERS", "max_test_workers", FieldType.INT, "admin-settings-max-test-workers", default="4"),
            _f("WORKER_STAGGER_SECONDS", "worker_stagger_seconds", FieldType.FLOAT, "admin-settings-worker-stagger-seconds", default="1.5"),
            _f("SHOW_ERROR_CODES", "show_error_codes", FieldType.BOOL, "admin-settings-show-error-codes", help_id="admin-settings-show-error-codes-help", default="False"),
            _f("PAGES_PER_PAGE", "pages_per_page", FieldType.INT, "admin-settings-pages-per-page", default="100"),
            _f("MAX_PAGES_PER_PAGE", "max_pages_per_page", FieldType.INT, "admin-settings-max-pages-per-page", default="500"),
        ),
    ),
    ConfigSection(
        section_id="scheduler",
        heading_id="admin-settings-scheduler-heading",
        description_id="admin-settings-scheduler-description",
        icon="bi-calendar-event",
        note_id="admin-settings-restart-note",
        fields=(
            _f("SCHEDULER_ENABLED", "enabled", FieldType.BOOL, "admin-settings-scheduler-enabled", default="True"),
            _f("SCHEDULER_TIMEZONE", "timezone", FieldType.STRING, "admin-settings-scheduler-timezone", help_id="admin-settings-scheduler-timezone-help", default="America/Toronto"),
            _f("SCHEDULER_MAX_INSTANCES", "max_instances", FieldType.INT, "admin-settings-scheduler-max-instances", default="1"),
            _f("SCHEDULER_MISFIRE_GRACE_TIME", "misfire_grace_time", FieldType.INT, "admin-settings-scheduler-misfire-grace-time", help_id="admin-settings-scheduler-misfire-grace-time-help", default="3600"),
            _f("SCHEDULER_COALESCE", "coalesce", FieldType.BOOL, "admin-settings-scheduler-coalesce", default="True"),
        ),
    ),
    ConfigSection(
        section_id="network",
        heading_id="admin-settings-network-heading",
        description_id="admin-settings-network-description",
        icon="bi-shield-lock",
        note_id="admin-settings-restart-note",
        fields=(
            _f("CORS_ORIGINS", "cors_origins", FieldType.STRING, "admin-settings-cors-origins", help_id="admin-settings-cors-origins-help", default=""),
            _f("RATELIMIT_DEFAULT", "ratelimit_default", FieldType.STRING, "admin-settings-ratelimit-default", help_id="admin-settings-ratelimit-default-help", default="60/minute"),
        ),
    ),
    ConfigSection(
        section_id="smtp",
        heading_id="admin-settings-smtp-heading",
        description_id="admin-settings-smtp-description",
        icon="bi-envelope",
        note_id="admin-settings-restart-note",
        fields=(
            _f("SMTP_HOST", "host", FieldType.STRING, "admin-settings-smtp-host"),
            _f("SMTP_PORT", "port", FieldType.INT, "admin-settings-smtp-port", default="587"),
            _f("SMTP_USERNAME", "username", FieldType.STRING, "admin-settings-smtp-username"),
            _f("SMTP_PASSWORD", "password", FieldType.PASSWORD, "admin-settings-smtp-password"),
            _f("SMTP_USE_TLS", "use_tls", FieldType.BOOL, "admin-settings-smtp-use-tls", default="True"),
            _f("SMTP_FROM_EMAIL", "from_email", FieldType.STRING, "admin-settings-smtp-from-email"),
            _f("SMTP_FROM_NAME", "from_name", FieldType.STRING, "admin-settings-smtp-from-name", default="CNIB Access Labs | AutoA11y"),
        ),
    ),
    ConfigSection(
        section_id="microsoft_sso",
        heading_id="admin-settings-microsoft-sso-heading",
        description_id="admin-settings-microsoft-sso-description",
        icon="bi-microsoft",
        note_id="admin-settings-restart-note",
        fields=(
            _f("MICROSOFT_CLIENT_ID", "client_id", FieldType.STRING, "admin-settings-microsoft-client-id"),
            _f("MICROSOFT_CLIENT_SECRET", "client_secret", FieldType.PASSWORD, "admin-settings-microsoft-client-secret"),
            _f("MICROSOFT_TENANT_ID", "tenant_id", FieldType.STRING, "admin-settings-microsoft-tenant-id", help_id="admin-settings-microsoft-tenant-id-help", default="common"),
        ),
    ),
    ConfigSection(
        section_id="google_sso",
        heading_id="admin-settings-google-sso-heading",
        description_id="admin-settings-google-sso-description",
        icon="bi-google",
        note_id="admin-settings-restart-note",
        fields=(
            _f("GOOGLE_CLIENT_ID", "client_id", FieldType.STRING, "admin-settings-google-client-id"),
            _f("GOOGLE_CLIENT_SECRET", "client_secret", FieldType.PASSWORD, "admin-settings-google-client-secret"),
        ),
    ),
    ConfigSection(
        section_id="pdf",
        heading_id="admin-settings-pdf-heading",
        description_id="admin-settings-pdf-description",
        icon="bi-file-earmark-pdf",
        note_id="admin-settings-restart-note",
        fields=(
            _f("PDF_STORAGE_DIR", "storage_dir", FieldType.STRING, "admin-settings-pdf-storage-dir", help_id="admin-settings-pdf-storage-dir-help", default="data/pdfs"),
            _f("PDF_MAX_SIZE_MB", "max_size_mb", FieldType.INT, "admin-settings-pdf-max-size-mb", default="100"),
            _f("PDF_DOWNLOAD_TIMEOUT_SECONDS", "download_timeout_seconds", FieldType.INT, "admin-settings-pdf-download-timeout-seconds", default="60"),
            _f("PDF_AUDIT_MAX_PARALLEL", "audit_max_parallel", FieldType.INT, "admin-settings-pdf-audit-max-parallel", default="2"),
            _f("PDFMAX_CHECKER_DIR", "pdfmax_checker_dir", FieldType.STRING, "admin-settings-pdfmax-checker-dir", help_id="admin-settings-pdfmax-checker-dir-help"),
        ),
    ),
)


SECTIONS_BY_ID: Final[dict[str, ConfigSection]] = {s.section_id: s for s in CONFIG_SECTIONS}


def get_section(section_id: str) -> ConfigSection | None:
    """Return the schema for ``section_id`` or ``None`` if unknown."""
    return SECTIONS_BY_ID.get(section_id)


def parse_bool(raw: str) -> bool:
    """Parse a bool consistently with how :mod:`config` reads env vars."""
    return raw.strip().lower() == "true"


def coerce_value(field: ConfigField, raw: str) -> FieldValue:
    """Coerce a form-submitted string to the field's typed value."""
    if field.field_type is FieldType.INT:
        return int(raw)
    if field.field_type is FieldType.FLOAT:
        return float(raw)
    if field.field_type is FieldType.BOOL:
        return parse_bool(raw)
    return raw


def stringify_value(field: ConfigField, value: FieldValue) -> str:
    """Render a stored value back to a string for display in form inputs."""
    if field.field_type is FieldType.BOOL:
        return "True" if bool(value) else "False"
    return str(value)


def env_value(field: ConfigField) -> str:
    """Read the env var (or default) as a raw string."""
    return os.getenv(field.env_var, field.default)


def get_field_view_value(field: ConfigField, section_data: dict[str, object] | None) -> str:
    """Return the value to pre-fill in the form for a given field.

    DB section value wins over env var; password fields are never echoed
    back (the template uses ``password_set`` to indicate whether one is
    stored).
    """
    if field.field_type is FieldType.PASSWORD:
        return ""
    if section_data is not None and field.db_key in section_data:
        raw = section_data[field.db_key]
        if isinstance(raw, bool):
            return "True" if raw else "False"
        if isinstance(raw, (int, float, str)):
            return str(raw)
    return env_value(field)


def is_password_set(field: ConfigField, section_data: dict[str, object] | None) -> bool:
    """Whether a non-empty password is currently stored or set in the env."""
    if field.field_type is not FieldType.PASSWORD:
        return False
    if section_data is not None:
        raw = section_data.get(field.db_key)
        if isinstance(raw, str) and raw:
            return True
    return bool(os.getenv(field.env_var, ""))


def is_bool_true(field: ConfigField, section_data: dict[str, object] | None) -> bool:
    """Whether a bool field is currently true (DB first, then env, then default)."""
    if field.field_type is not FieldType.BOOL:
        return False
    if section_data is not None and field.db_key in section_data:
        raw = section_data[field.db_key]
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return parse_bool(raw)
    return parse_bool(env_value(field))


def section_source(section: ConfigSection, section_data: dict[str, object] | None) -> str:
    """Classify where the *current* values come from: db / environment / default.

    Used by the template to show the right banner above each section. A
    section is "environment" if any of its env vars are set; otherwise it's
    showing baked-in defaults.
    """
    if section_data is not None:
        return "database"
    for field in section.fields:
        if os.environ.get(field.env_var):
            return "environment"
    return "default"


def get_runtime_value(section_id: str, db_key: str, settings: SystemSettings | None = None) -> FieldValue | None:
    """Return the effective value for ``section_id.db_key``.

    DB-stored value wins; env var is the fallback; field default is the
    final fallback. Returns ``None`` for unknown section/key.

    Most application code can keep reading from :class:`config.Config` —
    this helper is for sites that explicitly want to honour mid-flight
    admin overrides without a process restart.
    """
    section = SECTIONS_BY_ID.get(section_id)
    if section is None:
        return None
    field: ConfigField | None = next((f for f in section.fields if f.db_key == db_key), None)
    if field is None:
        return None

    if settings is not None:
        section_data = settings.get_section(section_id)
        if section_data is not None and db_key in section_data:
            raw = section_data[db_key]
            if field.field_type is FieldType.BOOL and isinstance(raw, bool):
                return raw
            if field.field_type is FieldType.INT and isinstance(raw, int) and not isinstance(raw, bool):
                return raw
            if field.field_type is FieldType.FLOAT and isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return float(raw)
            if isinstance(raw, str):
                try:
                    return coerce_value(field, raw)
                except (ValueError, TypeError):
                    pass

    raw_env = env_value(field)
    if not raw_env and field.field_type is FieldType.STRING:
        return ""
    try:
        return coerce_value(field, raw_env)
    except (ValueError, TypeError):
        return None
