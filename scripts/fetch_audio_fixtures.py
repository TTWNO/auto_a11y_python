"""Downloads the audioA11y test-fixture MP4 to tests/audio/fixtures/.

Idempotent. Skipped if the file already exists. Run once after cloning.
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "audio" / "fixtures"
FIXTURE_NAME = "short_audit.mp4"
# A 30-second clip from pythonAudioA11y's tmp/ directory; first commit a
# copy somewhere fetchable and point this URL there. Until then, error.
FIXTURE_URL = os.environ.get(
    "AUDIOA11Y_FIXTURE_URL",
    "",  # blank means "no fixture available; integration tests will skip"
)
EXPECTED_SHA256 = os.environ.get(
    "AUDIOA11Y_FIXTURE_SHA256",
    "",
)


def main() -> int:
    target = FIXTURE_DIR / FIXTURE_NAME
    if target.exists():
        print(f"already present: {target}")
        return 0
    if not FIXTURE_URL:
        print(
            "fixture URL not configured; set AUDIOA11Y_FIXTURE_URL"
            + " (and AUDIOA11Y_FIXTURE_SHA256 for verification)."
            + " Integration tests will skip in the meantime."
        )
        return 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading {FIXTURE_URL} -> {target}")
    urllib.request.urlretrieve(FIXTURE_URL, str(target))
    if EXPECTED_SHA256:
        h = hashlib.sha256(target.read_bytes()).hexdigest()
        if h != EXPECTED_SHA256:
            target.unlink()
            print(f"sha256 mismatch: got {h}, expected {EXPECTED_SHA256}", file=sys.stderr)
            return 1
    print(f"fetched: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
