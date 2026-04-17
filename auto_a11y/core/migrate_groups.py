"""One-time idempotent migration: seed default groups, migrate roles to group_ids, set is_superadmin."""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from auto_a11y.models.permission_group import PermissionGroup, DEFAULT_GROUPS

if TYPE_CHECKING:
    from auto_a11y.core.database import Database

logger = logging.getLogger(__name__)


def run_migration(db: Database) -> None:
    """Run the group permissions migration. Idempotent -- safe to call multiple times."""
    _seed_default_groups(db)
    _migrate_superadmin(db)
    _migrate_project_members(db)


def _seed_default_groups(db: Database) -> None:
    """Create default groups if they don't exist."""
    for name, config in DEFAULT_GROUPS.items():
        existing = db.get_group_by_name(name)
        if existing:
            logger.info(f"Default group '{name}' already exists, skipping")
            continue

        group = PermissionGroup(
            name=name,
            description=config['description'],
            permissions=config['permissions'],
            is_system=True,
        )
        group_id = db.create_group(group)
        logger.info(f"Created default group '{name}' with id {group_id}")


def _migrate_superadmin(db: Database) -> None:
    """Set is_superadmin=True for users with role=admin, False for others.

    Only touches users that don't already have is_superadmin set,
    making this safe to run multiple times.
    """
    result = db.db['app_users'].update_many(
        {"role": "admin", "is_superadmin": {"$exists": False}},
        {"$set": {"is_superadmin": True}}
    )
    if result.modified_count:
        logger.info(f"Set is_superadmin=True on {result.modified_count} admin users")

    result = db.db['app_users'].update_many(
        {"role": {"$ne": "admin"}, "is_superadmin": {"$exists": False}},
        {"$set": {"is_superadmin": False}}
    )
    if result.modified_count:
        logger.info(f"Set is_superadmin=False on {result.modified_count} non-admin users")


def _migrate_project_members(db: Database) -> None:
    """Convert members with 'role' field to 'group_ids' field.

    Looks up the corresponding default group for each role name
    (admin -> Admin, auditor -> Auditor, client -> Client) and
    writes the group ObjectId into group_ids.
    """
    role_to_group: dict[str, str] = {}
    for name in DEFAULT_GROUPS:
        group = db.get_group_by_name(name)
        if group:
            role_to_group[name.lower()] = str(group.mongo_id)

    if not role_to_group:
        logger.warning("No default groups found, skipping member migration")
        return

    # Find all projects with members that have 'role' but no 'group_ids'
    projects = db.db['projects'].find({"members.role": {"$exists": True}})

    migrated = 0
    for project_doc in projects:
        members: list[dict[str, Any]] = project_doc.get('members', [])
        updated = False
        for member in members:
            if 'role' in member and 'group_ids' not in member:
                role: str = member['role']
                group_id: str | None = role_to_group.get(role)
                if group_id:
                    member['group_ids'] = [group_id]
                else:
                    member['group_ids'] = []
                updated = True

        if updated:
            db.db['projects'].update_one(
                {"_id": project_doc["_id"]},
                {"$set": {"members": members}}
            )
            migrated += 1

    if migrated:
        logger.info(f"Migrated members in {migrated} projects from role to group_ids")
