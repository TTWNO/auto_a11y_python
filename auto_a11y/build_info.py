"""Build/version provenance for the running app.

The packaged desktop app ships a frozen source tree with **no ``.git``
directory** (``build-{mac,linux,windows}.sh`` rsync the tree excluding
``.git``), so a bundle cannot ask git which commit it was built from. To
make "which build is this?" answerable from a user's logs, the build
scripts stamp the short SHA into ``auto_a11y/BUILD_COMMIT`` right after
copying the source. In a dev checkout that file is absent and we ask git
directly.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_COMMIT_FILE = Path(__file__).resolve().parent / "BUILD_COMMIT"


def get_build_commit() -> str:
    """Return the git commit the running code was built from.

    Resolution order:

    1. ``auto_a11y/BUILD_COMMIT`` — stamped at package-build time. This is
       the only source available inside the packaged ``.app`` / AppImage,
       which carries no ``.git``.
    2. ``git rev-parse --short HEAD`` — for a dev checkout running from a
       working tree.
    3. ``"unknown"`` — neither available (e.g. a source copy with no git
       and no stamp).
    """
    try:
        stamped = _COMMIT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        stamped = ""
    if stamped:
        return stamped

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_COMMIT_FILE.parent),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"

    if result.returncode == 0:
        sha = result.stdout.strip()
        if sha:
            return sha
    return "unknown"
