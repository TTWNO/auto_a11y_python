"""One-shot migration: $unset page_ids / page_urls / discovered_page_ids on
every doc in `recordings` and `recording_issues`.

Idempotent. Logs touched-doc counts. Run after deploying the code change;
the code already tolerates absent fields, so running before/after/never
is also fine.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auto_a11y.core.database import Database

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/")
    db_name = os.environ.get("DATABASE_NAME", "auto_a11y")
    db = Database(uri, db_name)
    try:
        recordings_unset = {
            "$unset": {"page_ids": "", "page_urls": "", "discovered_page_ids": ""}
        }
        issues_unset = {"$unset": {"page_ids": "", "page_urls": ""}}
        r1 = db.recordings.update_many({}, recordings_unset)
        r2 = db.recording_issues.update_many({}, issues_unset)
        logger.info(
            "recordings.update_many: matched=%s modified=%s",
            r1.matched_count,
            r1.modified_count,
        )
        logger.info(
            "recording_issues.update_many: matched=%s modified=%s",
            r2.matched_count,
            r2.modified_count,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
