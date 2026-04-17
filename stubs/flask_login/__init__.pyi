from collections.abc import Callable
from datetime import timedelta

from flask import Flask
from werkzeug.local import LocalProxy

class LoginManager:
    login_view: str | None
    login_message: str
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

current_user: LocalProxy

def login_required(func: Callable[..., object]) -> Callable[..., object]: ...

def login_user(
    user: object,
    remember: bool = ...,
    duration: timedelta | None = ...,
    force: bool = ...,
    fresh: bool = ...,
) -> bool: ...

def logout_user() -> bool: ...
