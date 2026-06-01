"""Tests for per-project/website permission system."""
from __future__ import annotations

import os
os.environ.setdefault('RUN_AI_ANALYSIS', 'false')

from auto_a11y.models.project_member import ProjectMember
from auto_a11y.models.app_user import UserRole
from auto_a11y.models.project import Project
from auto_a11y.models.website import Website


class TestProjectMember:
    def test_to_dict(self) -> None:
        member = ProjectMember(user_id="abc123", group_ids=["g1", "g2"])
        d = member.to_dict()
        assert d == {"user_id": "abc123", "group_ids": ["g1", "g2"]}

    def test_to_dict_empty_groups(self) -> None:
        member = ProjectMember(user_id="abc123")
        d = member.to_dict()
        assert d == {"user_id": "abc123", "group_ids": []}

    def test_from_dict(self) -> None:
        member = ProjectMember.from_dict({"user_id": "abc123", "group_ids": ["g1"]})
        assert member.user_id == "abc123"
        assert member.group_ids == ["g1"]

    def test_from_dict_missing_groups_defaults_empty(self) -> None:
        member = ProjectMember.from_dict({"user_id": "abc123"})
        assert member.group_ids == []

    def test_from_dict_legacy_role_format(self) -> None:
        """Old format with 'role' field should be handled gracefully."""
        member = ProjectMember.from_dict({"user_id": "abc123", "role": "auditor"})
        assert member.user_id == "abc123"
        assert member.group_ids == []


class TestProjectMembers:
    def test_project_to_dict_includes_members(self) -> None:
        p = Project(name="Test")
        p.members = [ProjectMember(user_id="u1", group_ids=["g1"])]
        d = p.to_dict()
        assert d["members"] == [{"user_id": "u1", "group_ids": ["g1"]}]

    def test_project_from_dict_parses_members(self) -> None:
        d = {"name": "Test", "members": [{"user_id": "u1", "group_ids": ["g1"]}]}
        p = Project.from_dict(d)
        assert len(p.members) == 1
        assert p.members[0].user_id == "u1"
        assert p.members[0].group_ids == ["g1"]

    def test_project_from_dict_missing_members_defaults_empty(self) -> None:
        d = {"name": "Test"}
        p = Project.from_dict(d)
        assert p.members == []


class TestWebsiteMembers:
    def test_website_to_dict_includes_members(self) -> None:
        w = Website(project_id="p1", url="https://example.com")
        w.members = [ProjectMember(user_id="u1", group_ids=["g1"])]
        d = w.to_dict()
        assert d["members"] == [{"user_id": "u1", "group_ids": ["g1"]}]

    def test_website_from_dict_parses_members(self) -> None:
        d = {"project_id": "p1", "url": "https://example.com",
             "members": [{"user_id": "u1", "group_ids": ["g1"]}]}
        w = Website.from_dict(d)
        assert len(w.members) == 1
        assert w.members[0].user_id == "u1"

    def test_website_from_dict_missing_members_defaults_empty(self) -> None:
        d = {"project_id": "p1", "url": "https://example.com"}
        w = Website.from_dict(d)
        assert w.members == []


from unittest.mock import MagicMock, patch
import types
import pytest


class _Obj(types.SimpleNamespace):
    """Trivial attribute holder for seeding the fake db."""


class _FakeDb:
    """Minimal in-memory db exposing the accessors used by _resolve_project_id."""

    def __init__(
        self,
        project_users: dict[str, _Obj] | None = None,
        scripts: dict[str, _Obj] | None = None,
        results: dict[str, _Obj] | None = None,
        pages: dict[str, _Obj] | None = None,
        websites: dict[str, _Obj] | None = None,
    ) -> None:
        self._project_users = project_users or {}
        self._scripts = scripts or {}
        self._results = results or {}
        self._pages = pages or {}
        self._websites = websites or {}

    def get_project_user(self, user_id: str) -> _Obj | None:
        return self._project_users.get(user_id)

    def get_page_setup_script(self, script_id: str) -> _Obj | None:
        return self._scripts.get(script_id)

    def get_test_result(self, result_id: str) -> _Obj | None:
        return self._results.get(result_id)

    def get_page(self, page_id: str) -> _Obj | None:
        return self._pages.get(page_id)

    def get_website(self, website_id: str) -> _Obj | None:
        return self._websites.get(website_id)


def test_resolve_project_id_from_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(project_users={"u1": _Obj(project_id="p1")})
    monkeypatch.setattr("auto_a11y.core.permissions._get_db", lambda: db)
    from auto_a11y.core.permissions import resolve_project_id as _resolve_project_id
    assert _resolve_project_id(user_id="u1") == "p1"


def test_resolve_project_id_from_script_id(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(scripts={"s1": _Obj(page_id="pg1")},
                 pages={"pg1": _Obj(website_id="w1")},
                 websites={"w1": _Obj(project_id="p1")})
    monkeypatch.setattr("auto_a11y.core.permissions._get_db", lambda: db)
    from auto_a11y.core.permissions import resolve_project_id as _resolve_project_id
    assert _resolve_project_id(script_id="s1") == "p1"


def test_resolve_project_id_from_result_id(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(results={"r1": _Obj(page_id="pg1")},
                 pages={"pg1": _Obj(website_id="w1")},
                 websites={"w1": _Obj(project_id="p1")})
    monkeypatch.setattr("auto_a11y.core.permissions._get_db", lambda: db)
    from auto_a11y.core.permissions import resolve_project_id as _resolve_project_id
    assert _resolve_project_id(result_id="r1") == "p1"


def test_resolve_project_id_unknown_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr("auto_a11y.core.permissions._get_db", lambda: db)
    from auto_a11y.core.permissions import resolve_project_id as _resolve_project_id
    assert _resolve_project_id(user_id="missing") is None
    assert _resolve_project_id(script_id="missing") is None
    assert _resolve_project_id(result_id="missing") is None
    assert _resolve_project_id() is None


def _make_user(role: UserRole = UserRole.AUDITOR, user_id: str = "user1") -> MagicMock:
    """Create a mock AppUser."""
    user = MagicMock()
    user.role = role
    user.get_id.return_value = user_id
    user.is_admin.return_value = (role == UserRole.ADMIN)
    user.is_superadmin = (role == UserRole.ADMIN)
    user.is_authenticated = True
    return user


class TestGetEffectiveRole:
    def test_global_admin_always_returns_admin(self) -> None:
        from auto_a11y.web.routes.auth import get_effective_role
        user = _make_user(role=UserRole.ADMIN)
        result = get_effective_role(user, None, project_id="proj1")
        assert result == UserRole.ADMIN

    def test_no_project_id_returns_none(self) -> None:
        from auto_a11y.web.routes.auth import get_effective_role
        user = _make_user(user_id="u1")
        user.is_superadmin = False
        with patch("auto_a11y.web.routes.auth._get_db"):
            result = get_effective_role(user, None)
        assert result is None
