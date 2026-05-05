"""
System settings stored in MongoDB.

Provides a singleton document under the ``system_settings`` collection that
holds runtime overrides for environment variables (e.g. Drupal credentials).
Each "section" is a top-level key under the singleton doc — for example
``drupal``, ``smtp``, ``microsoft_sso``.

The pattern is: features try the DB section first, fall back to env vars,
and surface a friendly error message pointing the user at the in-app
settings page if neither source has the values.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from auto_a11y.core.database import Database

logger = logging.getLogger(__name__)

SINGLETON_ID = "singleton"


class SystemSettings:
    """Read/write persisted system-wide settings.

    Settings are stored as a single document with ``_id="singleton"`` in the
    ``system_settings`` collection. Each known feature owns a top-level key
    (e.g. ``drupal``) holding its config dict. ``set_section`` upserts that
    subkey while leaving sibling sections untouched.
    """

    def __init__(self, db: Database) -> None:
        self._collection = db.db.system_settings

    def get_section(self, key: str) -> dict[str, Any] | None:
        """Return the section dict, or ``None`` if unset.

        Empty dicts are also treated as unset so that an admin who saved a
        blank form does not deactivate the env-var fallback.
        """
        doc = self._collection.find_one({"_id": SINGLETON_ID})
        if not doc:
            return None
        section_any: Any = doc.get(key)
        if not isinstance(section_any, dict):
            return None
        section: dict[str, Any] = cast("dict[str, Any]", section_any)
        if not section:
            return None
        return section

    def set_section(
        self,
        key: str,
        values: dict[str, Any],
        updated_by: str | None = None,
    ) -> None:
        """Upsert ``values`` under ``key`` in the singleton settings doc."""
        self._collection.update_one(
            {"_id": SINGLETON_ID},
            {
                "$set": {
                    key: values,
                    "updated_at": datetime.now(timezone.utc),
                    "updated_by": updated_by,
                }
            },
            upsert=True,
        )
        logger.info("Updated system_settings section %r (by=%s)", key, updated_by)

    def clear_section(self, key: str) -> None:
        """Remove a section entirely so the env-var fallback applies again."""
        self._collection.update_one(
            {"_id": SINGLETON_ID},
            {"$unset": {key: ""}},
        )
        logger.info("Cleared system_settings section %r", key)
