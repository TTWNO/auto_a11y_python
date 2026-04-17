from collections.abc import Callable
from datetime import timedelta
from typing import Protocol

from flask import Flask


class CurrentUserProtocol(Protocol):
    """Minimal protocol for the user object exposed by ``current_user``.

    Flask-Login's ``current_user`` is a ``LocalProxy`` that delegates all
    attribute access to the underlying user (or an anonymous sentinel).
    Typing it as a ``Protocol`` instead of ``LocalProxy[object]`` lets
    callers access standard flask-login attributes without ``getattr``
    or ``# type: ignore``.
    """
    is_authenticated: bool
    is_active: bool
    is_anonymous: bool
    is_superadmin: bool  # project-specific extension
    def get_id(self) -> str | None: ...


class LoginManager:
    login_view: str | None
    login_message: str | object  # can be a lazy string
    login_message_category: str
    blueprint_login_views: dict[str, str]
    session_protection: str | None

    def __init__(
        self, app: Flask | None = ..., add_context_processor: bool = ...
    ) -> None: ...
    def init_app(self, app: Flask, add_context_processor: bool = ...) -> None: ...
    def user_loader(
        self, callback: Callable[[str], object | None]
    ) -> Callable[[str], object | None] | None: ...
    def request_loader(self, callback: Callable[..., object | None]) -> Callable[..., object | None] | None: ...
    def unauthorized_handler(self, callback: Callable[..., object]) -> Callable[..., object]: ...
    def unauthorized(self) -> object: ...

current_user: CurrentUserProtocol

def login_required(func: Callable[..., object]) -> Callable[..., object]: ...

def login_user(
    user: object,
    remember: bool = ...,
    duration: timedelta | None = ...,
    force: bool = ...,
    fresh: bool = ...,
) -> bool: ...

def logout_user() -> bool: ...
