"""Project membership for per-resource access control.

Note: This is distinct from ProjectUser/WebsiteUser which store test
credentials for sites under test. ProjectMember controls which platform
users can see/manage a project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProjectMember:
    """A user's group memberships on a specific project."""
    user_id: str          # AppUser._id as string
    group_ids: list[str] = field(default_factory=lambda: [])  # PermissionGroup._id as strings

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "group_ids": [str(gid) for gid in self.group_ids],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectMember:
        # Handle old format with 'role' field (pre-migration)
        if 'role' in data and 'group_ids' not in data:
            return cls(user_id=data["user_id"], group_ids=[])
        return cls(
            user_id=data["user_id"],
            group_ids=[str(gid) for gid in data.get("group_ids", [])],
        )
