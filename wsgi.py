"""WSGI entry point for production (Gunicorn / Render)."""
from __future__ import annotations

from typing import cast
from collections.abc import Callable

from flask import Flask

from config import config
from auto_a11y.web import app as _app_module

# create_app is not yet annotated (Phase 3 Group 3+), cast to typed callable.
_create_app: Callable[..., Flask] = cast(
    Callable[..., Flask], getattr(_app_module, 'create_app')
)

app: Flask = _create_app(config)
