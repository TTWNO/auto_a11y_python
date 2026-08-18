"""
Regression tests for two LOW code-audit findings.

Bug A (auto_a11y/core/email.py): the SMTP send path had no implicit-TLS
(SMTP_SSL / port 465) support, and when SMTP_USE_TLS was False it would call
``server.login`` over an UNENCRYPTED plain SMTP connection. The fix adds an
implicit-SSL path (SMTP_USE_SSL or port 465) and keeps STARTTLS for the TLS
path; implicit SSL and STARTTLS are mutually exclusive.

Bug B (auto_a11y/core/scraper.py): ``_can_fetch`` never fetched the real
robots.txt -- it hardcoded ``rp.parse(['User-agent: *', 'Allow: /'])`` so
``can_fetch`` always returned True regardless of the site's actual rules, even
when ``respect_robots`` was enabled. The fix fetches and parses the live
robots.txt (off the event loop) and honours its directives.
"""

from __future__ import annotations

from typing import Any, Awaitable, Protocol, cast
from unittest.mock import MagicMock, patch

import pytest

from auto_a11y.core.email import send_email
from auto_a11y.core.scraper import ScrapingEngine


# --------------------------------------------------------------------------- #
# Bug A: SMTP TLS / SSL handling.
# --------------------------------------------------------------------------- #


def _make_smtp_config(
    *,
    use_tls: bool,
    use_ssl: bool,
    port: int,
    username: str = "user@example.com",
) -> MagicMock:
    """Minimal stand-in for config.Config with the SMTP_ attributes used."""
    config = MagicMock()
    config.SMTP_ENABLED = True
    config.SMTP_HOST = "smtp.example.com"
    config.SMTP_PORT = port
    config.SMTP_USE_TLS = use_tls
    config.SMTP_USE_SSL = use_ssl
    config.SMTP_USERNAME = username
    config.SMTP_PASSWORD = "secret"
    config.SMTP_FROM_EMAIL = "noreply@example.com"
    config.SMTP_FROM_NAME = "Test"
    return config


def _send(config: MagicMock) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Invoke send_email with smtplib mocked. Returns (SMTP, SMTP_SSL, server)."""
    with patch("smtplib.SMTP") as smtp_cls, patch("smtplib.SMTP_SSL") as smtp_ssl_cls:
        # Both classes return the same recording server so we can inspect calls
        # regardless of which one was chosen.
        server = MagicMock()
        smtp_cls.return_value = server
        smtp_ssl_cls.return_value = server
        result = send_email(
            config, "to@example.com", "Subject", "text body", "<p>html</p>"
        )
        assert result is True
    return smtp_cls, smtp_ssl_cls, server


def test_implicit_ssl_uses_smtp_ssl_and_no_starttls() -> None:
    config = _make_smtp_config(use_tls=False, use_ssl=True, port=465)
    smtp_cls, smtp_ssl_cls, server = _send(config)

    smtp_ssl_cls.assert_called_once()
    smtp_cls.assert_not_called()
    server.starttls.assert_not_called()
    server.login.assert_called_once_with("user@example.com", "secret")
    server.sendmail.assert_called_once()


def test_port_465_implies_implicit_ssl() -> None:
    # Even if the SSL flag is not explicitly set, port 465 means implicit TLS.
    config = _make_smtp_config(use_tls=False, use_ssl=False, port=465)
    smtp_cls, smtp_ssl_cls, server = _send(config)

    smtp_ssl_cls.assert_called_once()
    smtp_cls.assert_not_called()
    server.starttls.assert_not_called()


def test_starttls_path_uses_smtp_and_starttls() -> None:
    config = _make_smtp_config(use_tls=True, use_ssl=False, port=587)
    smtp_cls, smtp_ssl_cls, server = _send(config)

    smtp_cls.assert_called_once()
    smtp_ssl_cls.assert_not_called()
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("user@example.com", "secret")


def test_plain_path_uses_neither_ssl_nor_starttls() -> None:
    config = _make_smtp_config(use_tls=False, use_ssl=False, port=25)
    smtp_cls, smtp_ssl_cls, server = _send(config)

    smtp_cls.assert_called_once()
    smtp_ssl_cls.assert_not_called()
    server.starttls.assert_not_called()


# --------------------------------------------------------------------------- #
# Bug B: robots.txt enforcement must reflect the live robots.txt.
# --------------------------------------------------------------------------- #


class _CanFetch(Protocol):
    def __call__(self, url: str) -> Awaitable[bool]: ...


def _make_engine() -> ScrapingEngine:
    """Build a ScrapingEngine without touching Database / BrowserManager."""
    engine = ScrapingEngine.__new__(ScrapingEngine)
    engine.robots_cache = {}
    # The robots fetch presents the crawler's user agent; a bare __new__ has no
    # attributes, and the resulting AttributeError would be swallowed by
    # _can_fetch's allow-on-error guard, quietly turning every case below into a
    # pass.
    setattr(engine, "_user_agent", "TestCrawler/1.0")
    return engine


def _can_fetch(engine: ScrapingEngine) -> _CanFetch:
    """Typed shim around the private ``_can_fetch``.

    Accessing the underscore-prefixed method directly trips pyright's
    ``reportPrivateUsage``; this is a deliberate unit test of the
    implementation, so we go through ``getattr`` to keep the access typed
    without a suppression comment (matching the pattern in
    ``tests/test_script_executor_audit.py``).
    """
    return cast(_CanFetch, getattr(engine, "_can_fetch"))


_ROBOTS_DISALLOW = "User-agent: *\nDisallow: /private/\n"
_ROBOTS_ALLOW_ALL = "User-agent: *\nDisallow:\n"


def _serving(robots_body: str) -> Any:
    """Patch urlopen to serve the given robots.txt body.

    The seam is urlopen rather than ``RobotFileParser.read``: the crawler does
    its own fetch so it can send its own User-Agent and treat a 4xx as "no
    rules retrieved" instead of "crawl nothing". See
    ``tests/core/test_robots_fetch.py``.
    """

    class _Response:
        def read(self, _amount: int | None = None) -> bytes:
            return robots_body.encode()

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    def fake_urlopen(*_a: object, **_k: object) -> _Response:
        return _Response()

    return patch("urllib.request.urlopen", fake_urlopen)


@pytest.mark.asyncio
async def test_disallowed_path_blocked_when_robots_fetched() -> None:
    engine = _make_engine()
    with _serving(_ROBOTS_DISALLOW):
        allowed = await _can_fetch(engine)("https://example.com/private/secret")
    assert allowed is False


@pytest.mark.asyncio
async def test_allowed_path_permitted_when_robots_fetched() -> None:
    engine = _make_engine()
    with _serving(_ROBOTS_DISALLOW):
        allowed = await _can_fetch(engine)("https://example.com/public/page")
    assert allowed is True


@pytest.mark.asyncio
async def test_robots_fetch_failure_defaults_to_allow() -> None:
    engine = _make_engine()

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("network down")

    with patch("urllib.request.urlopen", boom):
        allowed = await _can_fetch(engine)("https://example.com/anything")
    assert allowed is True


@pytest.mark.asyncio
async def test_robots_parsed_off_event_loop() -> None:
    # The blocking robots fetch must run via asyncio.to_thread so it does not
    # block the event loop.
    engine = _make_engine()
    with _serving(_ROBOTS_ALLOW_ALL):
        with patch(
            "auto_a11y.core.scraper.asyncio.to_thread",
            wraps=__import__("asyncio").to_thread,
        ) as to_thread:
            allowed = await _can_fetch(engine)("https://example.com/page")
    assert allowed is True
    to_thread.assert_called()
