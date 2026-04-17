"""Test that `python run.py --install-hooks` configures git to use .githooks/."""
import subprocess
from pathlib import Path

from run import install_hooks


def test_install_hooks_sets_hookspath(tmp_path: Path) -> None:
    """In a git worktree, --install-hooks sets core.hooksPath to .githooks."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".githooks").mkdir()

    assert install_hooks(cwd=tmp_path) is True

    result = subprocess.run(
        ["git", "config", "--local", "core.hooksPath"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ".githooks"


def test_install_hooks_noop_outside_git(tmp_path: Path) -> None:
    """Outside a git worktree, --install-hooks is a no-op that returns False."""
    assert install_hooks(cwd=tmp_path) is False
