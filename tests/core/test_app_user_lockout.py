"""Tests for AppUser account-lockout behavior."""
from __future__ import annotations

import os
os.environ.setdefault('RUN_AI_ANALYSIS', 'false')

from datetime import datetime, timedelta

from auto_a11y.models.app_user import AppUser, UserRole


def _make_user() -> AppUser:
    return AppUser(
        email="lockout@example.com",
        password_hash="x",
        role=UserRole.CLIENT,
    )


class TestAppUserLockout:
    def test_five_consecutive_failures_locks(self) -> None:
        """Five consecutive failed logins lock the account."""
        user = _make_user()
        for _ in range(5):
            user.record_login(success=False)
        assert user.failed_login_count == 5
        assert user.is_locked() is True

    def test_failure_after_expired_lock_does_not_relock(self) -> None:
        """After the lockout window expires, a single failure must not re-lock."""
        user = _make_user()
        for _ in range(5):
            user.record_login(success=False)
        assert user.is_locked() is True

        # Simulate the lockout window having elapsed (datetime.now() is naive).
        user.locked_until = datetime.now() - timedelta(minutes=1)
        assert user.is_locked() is False

        # One more failure after waiting out the lock: fresh allotment, not re-locked.
        user.record_login(success=False)
        assert user.failed_login_count == 1
        assert user.is_locked() is False

    def test_relock_requires_five_fresh_failures_after_expiry(self) -> None:
        """After an expired lock, it again takes five failures to re-lock."""
        user = _make_user()
        for _ in range(5):
            user.record_login(success=False)
        user.locked_until = datetime.now() - timedelta(minutes=1)

        # Four fresh failures should not lock yet.
        for _ in range(4):
            user.record_login(success=False)
        assert user.is_locked() is False
        # Fifth fresh failure re-locks.
        user.record_login(success=False)
        assert user.failed_login_count == 5
        assert user.is_locked() is True

    def test_consecutive_failures_within_window_still_lock(self) -> None:
        """Consecutive failures within the window preserve the 5->15min policy."""
        user = _make_user()
        for _ in range(4):
            user.record_login(success=False)
        assert user.is_locked() is False
        user.record_login(success=False)
        assert user.is_locked() is True
        # locked_until is ~15 minutes out.
        assert user.locked_until is not None
        assert user.locked_until > datetime.now() + timedelta(minutes=14)

    def test_successful_login_resets_counter(self) -> None:
        """A successful login clears the failure counter and lock."""
        user = _make_user()
        for _ in range(3):
            user.record_login(success=False)
        assert user.failed_login_count == 3
        user.record_login(success=True)
        assert user.failed_login_count == 0
        assert user.locked_until is None
        assert user.login_count == 1
        assert user.is_locked() is False
