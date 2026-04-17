"""Typed accessors for Flask current_app dynamic attributes.

Flask's ``current_app`` is typed as ``Flask`` which does not include the
dynamic attributes we attach in ``create_app`` (``db``, ``app_config``,
``test_config``, ``scheduler``).  Accessing them directly causes hundreds
of mypy ``attr-defined`` errors.

This module provides thin typed wrappers so route code can import::

    from auto_a11y.web.typed_app import get_db, get_app_config

and get properly-typed values without needing ``# type: ignore`` markers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from flask import Flask, current_app

if TYPE_CHECKING:
    from auto_a11y.core.database import Database
    from auto_a11y.core.scheduler import SchedulerService
    from config import Config


def get_db() -> Database:
    """Return ``current_app.db`` (a :class:`Database` instance)."""
    return cast("Database", getattr(current_app, "db"))


def get_app_config() -> Config:
    """Return ``current_app.app_config`` (the project configuration object)."""
    return cast("Config", getattr(current_app, "app_config"))


def get_test_config() -> Any:
    """Return ``current_app.test_config``."""
    return getattr(current_app, "test_config")


def get_scheduler() -> SchedulerService | None:
    """Return ``current_app.scheduler`` (may be ``None``)."""
    return cast("SchedulerService | None", getattr(current_app, "scheduler", None))


def get_flask_app() -> Flask:
    """Return the real Flask app object (unwrapped proxy)."""
    app: Any = current_app
    result: Flask = app._get_current_object()
    return result
