"""Tests for scripts/generate_openapi.py CLI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "generate_openapi.py"


def _run(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    import os
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(REPO), env=env,
    )


def test_generate_writes_yaml(tmp_path: Path) -> None:
    out = tmp_path / "spec.yaml"
    r = _run(env_extra={"OPENAPI_OUT": str(out)})
    assert r.returncode == 0, r.stderr
    assert out.exists()
    assert "openapi: 3.1.0" in out.read_text()


def test_check_passes_for_current_committed_spec() -> None:
    r = _run("--check")
    assert r.returncode == 0, r.stderr


def test_check_fails_on_drift(tmp_path: Path) -> None:
    stale = tmp_path / "stale.yaml"
    stale.write_text("openapi: 9.9.9\n")
    r = _run("--check", env_extra={"OPENAPI_OUT": str(stale)})
    assert r.returncode != 0
    assert "stale" in r.stderr.lower() or "drift" in r.stderr.lower()
