"""Tests for per-project/website permission system."""
import os
os.environ.setdefault('RUN_AI_ANALYSIS', 'false')

import pytest
from auto_a11y.models.project_member import ProjectMember
from auto_a11y.models.app_user import UserRole
from auto_a11y.models.project import Project
from auto_a11y.models.website import Website


class TestProjectMember:
    def test_to_dict(self):
        member = ProjectMember(user_id="abc123", group_ids=["g1", "g2"])
        d = member.to_dict()
        assert d == {"user_id": "abc123", "group_ids": ["g1", "g2"]}

    def test_to_dict_empty_groups(self):
        member = ProjectMember(user_id="abc123")
        d = member.to_dict()
        assert d == {"user_id": "abc123", "group_ids": []}

    def test_from_dict(self):
        member = ProjectMember.from_dict({"user_id": "abc123", "group_ids": ["g1"]})
        assert member.user_id == "abc123"
        assert member.group_ids == ["g1"]

    def test_from_dict_missing_groups_defaults_empty(self):
        member = ProjectMember.from_dict({"user_id": "abc123"})
        assert member.group_ids == []

    def test_from_dict_legacy_role_format(self):
        """Old format with 'role' field should be handled gracefully."""
        member = ProjectMember.from_dict({"user_id": "abc123", "role": "auditor"})
        assert member.user_id == "abc123"
        assert member.group_ids == []


class TestProjectMembers:
    def test_project_to_dict_includes_members(self):
        p = Project(name="Test")
        p.members = [ProjectMember(user_id="u1", group_ids=["g1"])]
        d = p.to_dict()
        assert d["members"] == [{"user_id": "u1", "group_ids": ["g1"]}]

    def test_project_from_dict_parses_members(self):
        d = {"name": "Test", "members": [{"user_id": "u1", "group_ids": ["g1"]}]}
        p = Project.from_dict(d)
        assert len(p.members) == 1
        assert p.members[0].user_id == "u1"
        assert p.members[0].group_ids == ["g1"]

    def test_project_from_dict_missing_members_defaults_empty(self):
        d = {"name": "Test"}
        p = Project.from_dict(d)
        assert p.members == []


class TestWebsiteMembers:
    def test_website_to_dict_includes_members(self):
        w = Website(project_id="p1", url="https://example.com")
        w.members = [ProjectMember(user_id="u1", group_ids=["g1"])]
        d = w.to_dict()
        assert d["members"] == [{"user_id": "u1", "group_ids": ["g1"]}]

    def test_website_from_dict_parses_members(self):
        d = {"project_id": "p1", "url": "https://example.com",
             "members": [{"user_id": "u1", "group_ids": ["g1"]}]}
        w = Website.from_dict(d)
        assert len(w.members) == 1
        assert w.members[0].user_id == "u1"

    def test_website_from_dict_missing_members_defaults_empty(self):
        d = {"project_id": "p1", "url": "https://example.com"}
        w = Website.from_dict(d)
        assert w.members == []


from unittest.mock import MagicMock, patch


def _make_user(role=UserRole.AUDITOR, user_id="user1"):
    """Create a mock AppUser."""
    user = MagicMock()
    user.role = role
    user.get_id.return_value = user_id
    user.is_admin.return_value = (role == UserRole.ADMIN)
    user.is_superadmin = (role == UserRole.ADMIN)
    user.is_authenticated = True
    return user


class TestGetEffectiveRole:
    def test_global_admin_always_returns_admin(self):
        from auto_a11y.web.routes.auth import get_effective_role
        user = _make_user(role=UserRole.ADMIN)
        result = get_effective_role(user, None, project_id="proj1")
        assert result == UserRole.ADMIN

    def test_no_project_id_returns_none(self):
        from auto_a11y.web.routes.auth import get_effective_role
        user = _make_user(user_id="u1")
        user.is_superadmin = False
        with patch("auto_a11y.web.routes.auth._get_db"):
            result = get_effective_role(user, None)
        assert result is None
