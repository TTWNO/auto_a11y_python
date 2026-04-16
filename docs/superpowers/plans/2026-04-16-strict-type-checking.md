# Strict Type Checking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install and enforce strict type checking by `mypy`, `pyright`, and `ty` across the main application, its tests, and its entry points — with zero escape hatches, local `.githooks/` enforcement, and blocking CI.

**Architecture:** Five-phase rollout. Phase 0 installs non-enforcing infrastructure. Phases 1–3 produce a clean baseline (inventory, stubs, annotations). Phase 4 flips enforcement on. The three tools share a single `pyproject.toml` config; all untyped third-party libraries get local `.pyi` stubs under `stubs/` (unless upstream types or a `types-*` package exists). No `# type: ignore` is permitted anywhere under the zero-escape-hatch policy.

**Tech Stack:** Python 3.11 + 3.12, `mypy`, `pyright` (via the `pyright` PyPI wrapper), `ty` (pinned pre-release), Bash (`.githooks/pre-commit`), GitHub Actions YAML.

**Spec:** `docs/superpowers/specs/2026-04-16-strict-type-checking-design.md`

## Branch & execution ground rules

- **Start from a clean branch off `main`.** Do NOT pile this work onto whatever branch was active when the plan was written. Before Task 1, run:
  ```bash
  git checkout main
  git pull
  git checkout -b typecheck/phase-0-infrastructure
  ```
  Phase 0 lands as a single PR on this branch. Phase 1 (baseline doc) goes on its own branch, then Phase 2 and Phase 3 work can stream into short-lived branches (one per library/module) off `main`.
- **Do not run `python run.py --install-hooks` on your own clone** until Phase 4 (Task 17). Running it earlier would activate the not-yet-passing hook and block your Phase 2/3 commits.
- **The `.venv/` here is Python 3.12 only** (no `python3.11` binary). Anywhere the plan shows a `3.11` vs `3.12` check locally, use mypy's `--python-version`, pyright's `--pythonversion`, or ty's equivalent CLI flag against the single available interpreter; otherwise rely on the CI matrix. Do not invoke a non-existent `python3.11` binary.

---

## File Structure

### Files to create

- `pyproject.toml` — root config for `[tool.mypy]`, `[tool.pyright]`, `[tool.ty]`, and a project-namespaced `[tool.auto_a11y_typecheck]` block that controls the `ty_enabled` recovery flag.
- `auto_a11y/py.typed` — empty marker so downstream consumers of this package see its type info.
- `stubs/` — new top-level directory for local `.pyi` stubs (populated incrementally during Phase 2).
- `docs/superpowers/working/type-check-baseline-2026-04-16.md` — working document capturing the Phase 1 baseline error inventory. Kept in-repo so the inventory is reviewable.

### Files to modify

- `requirements.txt` — add `pyright`, `ty`, pin both.
- `run.py` — add `--install-hooks` argparse flag and handler.
- `.githooks/pre-commit` — **replace** the existing translation-validation hook (stale; references `.po`/`.mo` files no longer present) with the new type-check hook.
- `.github/workflows/ci.yml` — add a new `typecheck` job with a `3.11`/`3.12` matrix, pyright Node cache, and (initially) `continue-on-error: true`.
- `CLAUDE.md` — add a new `## Type Checking (MANDATORY)` section; add a no-`--no-verify` line to the existing `### Git Workflow` section.
- `README.md` — add a new `## Type Checking` section under Development setup.
- `README.fr.md` — add the same section in French (bilingual policy).
- `.gitignore` — ensure `.mypy_cache/`, `.pyright/`, and any `ty` cache directories are ignored.

### Files NOT to modify

- `auto_a11y/scripts/` — JavaScript files, not Python.
- Top-level one-off scripts (`add_*.py`, `cleanup_*.py`, `migrate_*.py`, `translate_*.py`, `debug_*.py`, `diagnose_*.py`, `investigate_*.py`, `analyze_*.py`, `create_phase*.py`, `delete_*.py`, `clear_*.py`, `show_*.py`, `examine_*.py`, `revert_*.py`, `merge_*.py`, `generate_fixtures*.py`, `download_chromium.py`, `gunicorn.conf.py`).
- `archive/`, `demo_site/`, `fixture_generation/`.

---

## Phase 0 — Infrastructure (non-enforcing)

Goal: land every piece of tooling, config, hook, and CI wiring in one reviewable block, without activating enforcement. Developers can commit normally; CI runs the three tools but flags them `continue-on-error: true`.

### Task 1: Add `pyright` and `ty` to requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Discover the latest stable `pyright` wrapper version**

Run: `.venv/bin/pip index versions pyright 2>&1 | head -5`
Expected: a version list. Pick the newest non-prerelease.

- [ ] **Step 2: Discover the latest available `ty` version**

Run: `.venv/bin/pip index versions ty 2>&1 | head -5`
Expected: a version list (likely pre-release suffixes like `0.0.1a…`). Pick the newest.

- [ ] **Step 3: Add both to `requirements.txt` alphabetically**

In `requirements.txt`, add:
```
pyright==<version from step 1>
ty==<version from step 2>
```
Place alphabetically (`pyright` between `python-dotenv` and `pytokens`; `ty` between `typst` and `tzlocal`).

- [ ] **Step 4: Install them into the venv**

Run: `.venv/bin/pip install -r requirements.txt`
Expected: successful install of both.

- [ ] **Step 5: Verify each tool invokes**

Run all three (expect non-zero exit is fine; we just want the tools themselves to start):
```
.venv/bin/mypy --version
.venv/bin/pyright --version
.venv/bin/ty --version
```
Expected: each prints its version and exits.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt
git commit -m "chore(typecheck): pin pyright and ty"
```

### Task 2: Create `pyproject.toml` with all three tools

**Files:**
- Create: `pyproject.toml`

- [ ] **Step 1: Create `pyproject.toml` at repo root**

Contents:
```toml
# =========================================================================
# Strict type-checking config.
# Spec: docs/superpowers/specs/2026-04-16-strict-type-checking-design.md
# Plan: docs/superpowers/plans/2026-04-16-strict-type-checking.md
# =========================================================================

# Project-namespaced flag: lets the .githooks/pre-commit script and the
# CI workflow skip `ty` in the narrow case of an upstream ty bug that no
# refactor can satisfy. MUST NOT be used for any other purpose. Flip
# back on the next ty release that fixes the upstream bug.
[tool.auto_a11y_typecheck]
ty_enabled = true

[tool.mypy]
python_version = "3.11"
files = [
    "auto_a11y",
    "tests",
    "stubs",
    "config.py",
    "run.py",
    "wsgi.py",
    "test_fixtures.py",
]
mypy_path = "stubs"
namespace_packages = true
explicit_package_bases = true

# Strict mode and everything above it
strict = true
disallow_any_unimported = true
warn_unused_ignores = true
warn_redundant_casts = true
strict_equality = true
extra_checks = true
implicit_reexport = false

[tool.pyright]
pythonVersion = "3.11"
include = [
    "auto_a11y",
    "tests",
    "stubs",
    "config.py",
    "run.py",
    "wsgi.py",
    "test_fixtures.py",
]
stubPath = "stubs"
typeCheckingMode = "strict"

reportMissingTypeStubs = "error"
reportImplicitOverride = "error"
reportUninitializedInstanceVariable = "error"
reportUnknownParameterType = "error"
reportUnknownVariableType = "error"
reportUnknownArgumentType = "error"
reportUnknownMemberType = "error"
reportUnnecessaryTypeIgnoreComment = "error"
reportUnusedExpression = "error"
reportImplicitStringConcatenation = "error"
reportShadowedImports = "error"

[tool.ty]
# ty is pre-alpha; the authoritative config keys change release-to-release.
# The IMPLEMENTER must consult `ty --help` / `ty check --help` at the
# pinned version and set the strictest equivalent of mypy/pyright's
# strict mode here. Do NOT leave any strictness knob at a more permissive
# level than its mypy/pyright counterpart.
python-version = "3.11"
```

- [ ] **Step 2: Write the initial `[tool.ty]` strictest-mode settings**

Consult `.venv/bin/ty --help`, `.venv/bin/ty check --help`, and (if present) `.venv/bin/ty --help=config` at the pinned version. Translate every strictness knob available in `ty` to its strictest value. Document the config in inline comments.

**Bounded research:** spend no more than 30 minutes on this step. If `ty`'s CLI/config surface at the pinned pre-alpha version exposes no strictness-relevant knobs beyond the default checks, commit `[tool.ty]` with `python-version = "3.11"` only, and add an inline comment: `# ty pinned at version X.Y.Z; no further strictness levers exposed at this release. Revisit on upgrade.`

If `ty` has no equivalent of a particular mypy setting, add `# no ty equivalent: <setting-name>` rather than omit silently.

- [ ] **Step 3: Smoke-run each tool against the config**

Each should start and report errors (expected — the codebase is unannotated). The goal is that each tool finds and parses the config successfully:
```
.venv/bin/mypy 2>&1 | tail -3
.venv/bin/pyright 2>&1 | tail -3
.venv/bin/ty check 2>&1 | tail -3
```
Expected: each runs to completion (non-zero exit is fine), no "config not found" / "unknown key" / "invalid section" errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore(typecheck): add pyproject.toml strict config for mypy, pyright, and ty"
```

### Task 3: Create stubs directory and py.typed marker

**Files:**
- Create: `stubs/.gitkeep` (so the empty directory is tracked)
- Create: `stubs/README.md` (one-paragraph explanation + policy link)
- Create: `auto_a11y/py.typed`

- [ ] **Step 1: Make the directory and keep-file**

Run: `mkdir -p stubs && touch stubs/.gitkeep`

- [ ] **Step 2: Write `stubs/README.md`**

Contents:
```markdown
# Local type stubs

`.pyi` stubs for third-party libraries that do not ship type information
and for which no `types-*` package exists on PyPI. Every stub covers only
the symbols we actually import — no `Any` in signatures.

Policy: see `docs/superpowers/specs/2026-04-16-strict-type-checking-design.md`.

Before adding a new stub here, **first** check:
1. Does a newer version of the library ship `py.typed`?
2. Is there a `types-<package>` or `<package>-stubs` on PyPI?
3. Is the package in `typeshed`?

If any of the above, use it instead of writing a local stub.
```

- [ ] **Step 3: Create the py.typed marker**

Run: `touch auto_a11y/py.typed`

- [ ] **Step 4: Commit**

```bash
git add stubs/.gitkeep stubs/README.md auto_a11y/py.typed
git commit -m "chore(typecheck): add stubs/ directory and auto_a11y/py.typed marker"
```

### Task 4: Replace `.githooks/pre-commit` with the new type-check hook

**Files:**
- Modify (replace): `.githooks/pre-commit`

- [ ] **Step 1: Read the existing hook**

Run: `cat .githooks/pre-commit`
Confirm it is the stale translation hook referencing `scripts/validate_translations.py`, `messages.po`, `messages.mo`. The spec authorises full replacement — the live translation validator is already run by CI via `tests/validate_translations.py`.

- [ ] **Step 2: Write the new hook**

Replace the entire contents of `.githooks/pre-commit` with:
```bash
#!/usr/bin/env bash
#
# Pre-commit hook: block commits that do not pass strict type checking.
#
# Runs mypy, pyright, and ty against the scoped paths. All three run even
# if earlier ones fail so the developer sees the full picture in one shot.
#
# Install: python run.py --install-hooks
#      or: git config core.hooksPath .githooks
#
# Policy: docs/superpowers/specs/2026-04-16-strict-type-checking-design.md
#

set -uo pipefail

# ---------------------------------------------------------------------------
# Locate Python — prefer .venv
# ---------------------------------------------------------------------------

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    PYTHON="python"
fi

# ---------------------------------------------------------------------------
# Read [tool.auto_a11y_typecheck] ty_enabled from pyproject.toml
# ---------------------------------------------------------------------------

TY_ENABLED=$("$PYTHON" - <<'PYEOF'
import sys
try:
    import tomllib  # 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[import-not-found]
try:
    with open("pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    print("true" if data.get("tool", {}).get("auto_a11y_typecheck", {}).get("ty_enabled", True) else "false")
except Exception:
    print("true")  # default-on if config cannot be read
PYEOF
)

# ---------------------------------------------------------------------------
# Run all three checkers. Capture failures; report all at end.
# ---------------------------------------------------------------------------

STATUS=0

echo "==> mypy"
if ! "$PYTHON" -m mypy; then
    STATUS=1
fi

echo
echo "==> pyright"
if ! "$PYTHON" -m pyright; then
    STATUS=1
fi

if [ "$TY_ENABLED" = "true" ]; then
    echo
    echo "==> ty"
    if ! "$PYTHON" -m ty check; then
        STATUS=1
    fi
else
    echo
    echo "==> ty SKIPPED ([tool.auto_a11y_typecheck] ty_enabled = false)"
fi

if [ $STATUS -ne 0 ]; then
    echo
    echo "COMMIT BLOCKED: strict type checking failed."
    echo "Fix the errors above. Suppression comments are NOT permitted."
    echo "Policy: docs/superpowers/specs/2026-04-16-strict-type-checking-design.md"
    exit 1
fi

exit 0
```

- [ ] **Step 3: Make it executable**

Run: `chmod +x .githooks/pre-commit`

- [ ] **Step 4: Dry-run the hook directly (without activating it)**

Run: `bash .githooks/pre-commit 2>&1 | tail -20`
Expected: will exit non-zero (code is unannotated), but the three sections (`==> mypy`, `==> pyright`, `==> ty`) must all appear. This confirms the hook dispatches to all three tools.

- [ ] **Step 5: Commit**

```bash
git add .githooks/pre-commit
git commit -m "chore(typecheck): replace .githooks/pre-commit with mypy+pyright+ty hook"
```

### Task 5: Add `--install-hooks` flag to `run.py`

**Files:**
- Modify: `run.py:135-145` (argparse block) and the flag-handler block near `run.py:175-195`
- Create: `tests/conftest.py` (if it does not already exist at time of execution)
- Create: `tests/test_install_hooks.py`

- [ ] **Step 1: Ensure `tests/conftest.py` exists so pytest resolves `from run import …`**

Check: `ls tests/conftest.py`. If it exists, read it and skip this step. If missing, create:
```python
"""Pytest configuration: add the repo root to sys.path so top-level modules import."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_install_hooks.py`:
```python
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
```

No `# type: ignore` needed: `run.py` will be annotated by the time any checker sees this file, and the `conftest.py` mechanism replaces the fragile `sys.path` manipulation. (If in an intermediate state during Phase 3 annotations break this import temporarily, the test file is in the scoped set and the error will surface immediately — fix it there, don't paper over with an ignore.)

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_install_hooks.py -v`
Expected: `ImportError` — `install_hooks` does not exist yet.

- [ ] **Step 4: Implement `install_hooks` in `run.py`**

`run.py` already imports `logging` and `Path` at the top — reuse them. Add a new top-level function near the other helpers (above `main()`):
```python
def install_hooks(cwd: Path | None = None) -> bool:
    """Configure the given git worktree (default: repo root) to use .githooks/.

    Returns True if configured, False if the directory is not a git worktree
    (in which case this is a no-op).
    """
    import subprocess

    # Default to the directory containing run.py so the command works from any CWD.
    target_dir = cwd if cwd is not None else Path(__file__).resolve().parent
    git_dir = target_dir / ".git"
    if not git_dir.exists():
        logging.info("Not a git worktree; skipping hook install.")
        return False

    subprocess.run(
        ["git", "config", "--local", "core.hooksPath", ".githooks"],
        cwd=target_dir,
        check=True,
    )
    logging.info("Configured git core.hooksPath -> .githooks")
    return True
```

- [ ] **Step 5: Wire the argparse flag**

In `run.py`, add to the parser in `main()` (next to the other `action='store_true'` flags):
```python
    parser.add_argument('--install-hooks', action='store_true',
                        help='Configure git to use .githooks/ for this worktree')
```

And add a handler block near the other early-return flags (`--test-db`, `--download-browser`), before the setup block:
```python
    if args.install_hooks:
        if install_hooks():
            logger.info("✓ Hooks installed")
            return
        else:
            logger.error("✗ Not a git worktree; hooks not installed")
            sys.exit(1)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_install_hooks.py -v`
Expected: both tests PASS.

- [ ] **Step 7: Manual smoke test**

Run: `.venv/bin/python run.py --install-hooks`
Then: `git config --local core.hooksPath`
Expected: prints `.githooks`.

Then **undo it** (so your clone does not run the still-failing hook during Phase 2/3):
```
git config --local --unset core.hooksPath
```

- [ ] **Step 8: Commit**

```bash
git add run.py tests/test_install_hooks.py tests/conftest.py
git commit -m "feat(typecheck): add --install-hooks flag to run.py"
```
(If `tests/conftest.py` was not newly created in Step 1, omit it from `git add`.)

### Task 6: Add informational `typecheck` job to CI

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Discover the pinned pyright version to pass via env**

```bash
grep '^pyright==' requirements.txt
```
Expected: one line like `pyright==X.Y.Z`. Record the `X.Y.Z`; use it in the `env.PYRIGHT_PYTHON_FORCE_VERSION` below.

- [ ] **Step 2: Add the job**

At the top level of `jobs:` (alongside the existing `test:` job), add:
```yaml
  typecheck:
    runs-on: ubuntu-latest
    # Phase 0: each step is marked continue-on-error so the three tools all
    # run and their failures surface as annotations, while the job itself
    # passes green (so PR authors do not see a spurious red X on a check
    # that is not yet intended to be blocking). In Phase 4 the step-level
    # continue-on-error lines are removed so failures fail the job, AND
    # branch protection is configured to require the job.
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12"]
    env:
      # Pin the pyright Node binary to the wrapper's version so CI and local
      # resolve identical tools. Replace X.Y.Z with the value recorded in Step 1.
      PYRIGHT_PYTHON_FORCE_VERSION: "X.Y.Z"

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: 'pip'

      - name: Cache pyright Node binary
        uses: actions/cache@v4
        with:
          path: ~/.cache/pyright-python
          key: pyright-${{ runner.os }}-${{ env.PYRIGHT_PYTHON_FORCE_VERSION }}

      - name: Install Python dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run mypy
        run: python -m mypy --python-version ${{ matrix.python-version }}
        continue-on-error: true

      - name: Run pyright
        run: python -m pyright --pythonversion ${{ matrix.python-version }}
        continue-on-error: true

      - name: Run ty
        run: python -m ty check
        continue-on-error: true
```

Why step-level only (not job-level) `continue-on-error`:
- With only step-level `continue-on-error: true`, each tool step runs to completion, failures appear as inline annotations, and the job overall exits 0 → the status check reports green.
- A job-level `continue-on-error` would only tell GitHub Actions not to fail the workflow; the job's own status check would still report red. That is not what "informational" should mean in this plan.
- In Phase 4 (Task 16) the step-level `continue-on-error: true` is removed from all three steps; the job then fails if any tool fails, which is what blocking-via-branch-protection requires.

(If `ty` does not accept `check` as the subcommand at the pinned version, or does not support explicit Python version selection, adjust the final step per the pinned `ty --help` output. The `ty check` invocation matches the hook script for consistency.)

- [ ] **Step 3: Lint the YAML syntax**

Run: `.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: no output (valid YAML).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(typecheck): add informational typecheck job (mypy+pyright+ty, 3.11/3.12 matrix)"
```

### Task 7: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md:80-89` (Git Workflow section) and add new top-level section after "Colour System (MANDATORY)".

- [ ] **Step 1: Update Git Workflow**

In `CLAUDE.md`'s `### Git Workflow` section, add a new bullet adjacent to the "NEVER use" list:
```markdown
- **NEVER use `git commit --no-verify`** — bypassing the pre-commit hook circumvents required type-checking enforcement. CI re-runs the same checks, so bypassing locally only delays the failure. Fix the errors before committing.
```

- [ ] **Step 2: Add the new Type Checking section**

Insert **after the last subsection of `## Colour System (MANDATORY)` and before the next top-level `##` heading** (at time of writing, the next heading is `## Common Gotchas`). Do not rely on a literal line number — the file may have shifted. Use the top-level heading names to locate the insertion point.

Add the new top-level section:
```markdown
## Type Checking (MANDATORY)

**All in-scope Python code MUST pass `mypy` strict mode, `pyright` strict mode, and `ty` in its strictest available mode.** This is a hard requirement, not optional. Type errors are fixed in code — never suppressed.

### Scope

Enforced on:
- `auto_a11y/**/*.py`
- `tests/**/*.py`
- `stubs/**/*.pyi`
- `config.py`, `run.py`, `wsgi.py`, `test_fixtures.py`

Excluded: `archive/`, `demo_site/`, `fixture_generation/`, `electron/`, top-level one-off migration/debug/translation scripts, and `auto_a11y/scripts/` (JavaScript).

### Setup (one time per clone)

```bash
python run.py --install-hooks
# or equivalently:
git config core.hooksPath .githooks
```

### Zero-escape-hatch policy

- **No `# type: ignore`** (any tool). `warn_unused_ignores`/equivalent is on, so leftover ignores are themselves errors.
- **No `# pyright: ignore`**.
- **No `# ty: ignore`**.
- **No `cast(Any, ...)`** as a workaround for a type error.
- **No `-> Any` return types**. Use `object`, a `TypeVar`, or an explicit union.
- **No `git commit --no-verify`**. CI re-runs the same checks; bypassing locally just delays the failure.
- **No `# type: ignore[...]` even with a code**. If a checker has a bug, the workaround is a code refactor, a stub patch, or a tool version pin — never a suppression comment.

### Adding a new dependency

If a new library lacks type information, you MUST (in the same commit that introduces the dependency):
1. Check for upstream `py.typed` in a newer version.
2. Check for a `types-<package>` or `<package>-stubs` PyPI package.
3. Check `typeshed`.
4. If none of the above, write a minimal fully-typed `.pyi` stub under `stubs/<package>/`. Only the symbols you import. No `Any`.

Commits that add an untyped import without stubs will fail the hook and CI.

### Modifying the checked scope

Adding a new module to the enforced set requires updating the `files`/`include` lists in `pyproject.toml` for **all three tools consistently**. Keep the lists in sync.

### Tool disagreement

If two checkers disagree:
1. First try to satisfy all three by refactoring or adding narrowing annotations.
2. If impossible, the authority order is `mypy` > `pyright` > `ty`. The less-authoritative tool's objection is treated as its bug; work around it in code.
3. If a three-way irreconcilable conflict emerges, surface it to the repo owner — do not land the code.

### `ty` recovery procedure

`ty` is pre-alpha. In the narrow case of a `ty` bug that no refactor or version pin can resolve, set `[tool.auto_a11y_typecheck] ty_enabled = false` in `pyproject.toml`. This is a repo-wide, review-visible configuration downgrade — **not** a suppression comment, and **not** permitted for `mypy` or `pyright`. File an upstream issue; flip the flag back on the next `ty` release.

### Running checks manually

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
```

### Branch protection

The `typecheck` CI job (both `3.11` and `3.12` matrix variants) must be a required status check on `main`. This is configured in the GitHub repo settings by the repo owner.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(typecheck): add Type Checking (MANDATORY) section to CLAUDE.md"
```

### Task 8: Update README.md and README.fr.md

**Files:**
- Modify: `README.md`
- Modify: `README.fr.md`

- [ ] **Step 1: Add a Type Checking section to `README.md`**

Insert after the existing `## Testing` section, before `## API Usage`:
```markdown
## Type Checking

This project enforces strict static type checking via three complementary tools: **mypy**, **pyright**, and **ty**. All three must pass on every commit and in CI.

### One-time setup (per clone)

After cloning:
```bash
python run.py --install-hooks
```

This configures `git` to use the repo's `.githooks/` directory. The pre-commit hook runs all three type checkers against the scoped paths and blocks the commit on any failure.

### Running checks manually

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
```

### Policy

No `# type: ignore` or equivalent suppression comments. No `cast(Any, ...)` workarounds. No `git commit --no-verify`. Type errors are fixed, not suppressed. See [CLAUDE.md](./CLAUDE.md#type-checking-mandatory) for the full policy.
```

- [ ] **Step 2: Add the French mirror to `README.fr.md`**

Insert the matching French translation at the equivalent position (same neighbouring sections as in `README.md`). Match section structure and code blocks exactly — code blocks are not translated, only the prose and section headings.

Use terminology consistent with the existing `auto_a11y/web/translations/fr/*.ftl` catalogue. Do not invent new French terms when one already exists in the project.

Key headings / phrases:
- "Type Checking" → **"Vérification des types"**
- "One-time setup (per clone)" → **"Configuration unique (par clone)"**
- "Running checks manually" → **"Exécution manuelle des vérifications"**
- "Policy" → **"Politique"**
- "This project enforces strict static type checking via three complementary tools" → **"Ce projet impose une vérification stricte des types au moyen de trois outils complémentaires"**
- "All three must pass on every commit and in CI." → **"Les trois doivent réussir à chaque commit et dans la CI."**
- "This configures `git` to use the repo's `.githooks/` directory." → **"Cela configure `git` pour utiliser le répertoire `.githooks/` du dépôt."**
- "The pre-commit hook runs all three type checkers against the scoped paths and blocks the commit on any failure." → **"Le crochet pre-commit exécute les trois vérificateurs de types sur les chemins ciblés et bloque le commit en cas d'échec."**
- "No `# type: ignore` or equivalent suppression comments. No `cast(Any, ...)` workarounds. No `git commit --no-verify`. Type errors are fixed, not suppressed." → **"Aucun commentaire `# type: ignore` ni équivalent. Aucun contournement `cast(Any, ...)`. Aucun `git commit --no-verify`. Les erreurs de typage doivent être corrigées, pas supprimées."**
- "See CLAUDE.md for the full policy." → **"Voir CLAUDE.md pour la politique complète."**

Commit the French translation in the same commit as the English change. The PR description should flag it for native-speaker review — this is the standard bilingual-contribution workflow on this project and does not block merging.

- [ ] **Step 3: Verify the section ordering in both files matches**

Run: `grep -n '^## ' README.md && echo --- && grep -n '^## ' README.fr.md`
Expected: the new `## Type Checking` / `## Vérification des types` appears at the corresponding position in each file, with the same neighbours.

- [ ] **Step 4: Commit**

```bash
git add README.md README.fr.md
git commit -m "docs(typecheck): add Type Checking section to README (en + fr)"
```

### Task 9: Update `.gitignore`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Probe the real ty cache path**

Run once to populate whatever cache `ty` creates at the pinned version:
```bash
.venv/bin/python -m ty check > /dev/null 2>&1 || true
```
Then:
```bash
ls -la | grep -Ei '\.ty|ty_cache|ty\.json'
```
Record whatever appears (e.g., `.ty_cache/`, `.ty/`, `ty.lock`). If nothing appears, `ty` does not currently use an on-disk cache; move on.

- [ ] **Step 2: Add discovered caches to `.gitignore`**

Append:
```
# Type checker caches
.mypy_cache/
.pyright/
```
Plus whatever `ty` entries Step 1 produced. If none, add a comment: `# ty currently has no on-disk cache at the pinned version`.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore(typecheck): ignore mypy/pyright/ty cache directories"
```

### Task 10: Phase 0 verification

- [ ] **Step 1: Full Phase 0 smoke test**

```bash
bash .githooks/pre-commit; echo "exit=$?"
```

Expected: Each of the three sections (`==> mypy`, `==> pyright`, `==> ty`) appears. Exit is non-zero (code is unannotated). This is fine — Phase 0 is non-enforcing.

- [ ] **Step 2: Confirm CI config parses**

```
.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
Expected: no output.

- [ ] **Step 3: Open the draft PR for Phase 0**

Push the `remove-bootstrap-colours` branch (or the currently active branch) and open a draft PR titled `Phase 0: Strict type-checking infrastructure (non-enforcing)`. Phase 0 is complete when the informational `typecheck` job appears in the PR's CI output for both `3.11` and `3.12`.

---

## Phase 1 — Baseline inventory

Goal: produce a single, reviewable record of every type-check error in the codebase, categorised so the stubs sprint (Phase 2) and annotation sprint (Phase 3) can be planned.

### Task 11: Capture baseline output for each tool

**Files:**
- Create: `docs/superpowers/working/type-check-baseline-2026-04-16.md`

- [ ] **Step 1: Make the working directory**

Run: `mkdir -p docs/superpowers/working`

- [ ] **Step 2: Capture each tool's output**

```bash
.venv/bin/python -m mypy    > /tmp/mypy-baseline.txt 2>&1    ; echo "exit=$?"
.venv/bin/python -m pyright > /tmp/pyright-baseline.txt 2>&1 ; echo "exit=$?"
.venv/bin/python -m ty check > /tmp/ty-baseline.txt 2>&1    ; echo "exit=$?"
```

Expected: each exits non-zero with error output captured.

- [ ] **Step 3: Extract the official error count from each tool's own summary**

Do NOT use raw `grep "error"`: tools print "0 errors" summary lines and contextual text that inflate the count.

```bash
# mypy prints a final line like: "Found 1234 errors in 42 files (checked 151 source files)"
grep -oE "Found [0-9]+ errors?" /tmp/mypy-baseline.txt | tail -1

# pyright: capture --outputjson next time; for this baseline,
# use its summary line: "X errors, Y warnings, Z informations"
grep -oE "[0-9]+ errors?" /tmp/pyright-baseline.txt | tail -1

# ty: consult ty --help for its preferred output format. Prefer JSON/--summary
# if available; otherwise count lines matching ty's own error prefix exactly
# (e.g., "error[..." or "error:" at line start).
grep -cE "^error(\[|:)" /tmp/ty-baseline.txt
```

If any tool's format is uncooperative, re-run that tool with `--output=json` (or equivalent) and use `jq` to count. Do not ship a baseline whose error counts are approximate.

- [ ] **Step 4: Write the baseline document**

Create `docs/superpowers/working/type-check-baseline-2026-04-16.md` with the following structure:
```markdown
# Strict Type-Check Baseline — 2026-04-16

**Tool versions:**
- mypy: <version from `.venv/bin/mypy --version`>
- pyright: <version from `.venv/bin/pyright --version`>
- ty: <version from `.venv/bin/ty --version`>

**Python versions tested:** 3.11 (matches the pyproject.toml setting)

## Summary

| Tool | Error count |
|------|-------------|
| mypy | N |
| pyright | N |
| ty | N |

## Missing-stubs errors (Phase 2 target)

Every third-party import that produced a "missing stubs" or
"untyped import" error. One entry per distinct package.

| Package | import-from code (mypy / pyright / ty) | Upstream `py.typed`? | `types-*` on PyPI? | Action |
|---------|----------------------------------------|----------------------|--------------------|--------|
| apscheduler | … | no | no | write local stub |
| flask-wtf | … | no | no | write local stub |
| … | | | | |

## Missing-annotations errors (Phase 3 target)

By file, bucketed by severity. Summarise — do NOT list every individual line.

| File | mypy errors | pyright errors | ty errors |
|------|-------------|----------------|-----------|
| auto_a11y/utils/foo.py | 42 | 51 | 38 |
| … | | | |

## Real type bugs (fix in Phase 3 as they surface)

Genuine bugs the tools discovered — wrong argument types, nullability violations,
unreachable branches, etc. Flag these so they are investigated, not just annotated over.

| File:line | Tool | Message | Suspected bug |
|-----------|------|---------|---------------|

## Tool disagreements

Where two or three tools disagree about the same line. Resolve per tiebreaker rule
(mypy > pyright > ty) during Phase 3.

| File:line | mypy says | pyright says | ty says |
|-----------|-----------|--------------|---------|
```

- [ ] **Step 5: Populate the baseline document**

Walk `/tmp/*.txt` with a script or by eye; fill in the tables. The goal is completeness — every unique third-party package that generates a missing-stubs error MUST appear in the Phase-2 table. For Phase-3 files, aggregate counts (`sort | uniq -c`) — do not enumerate every line.

- [ ] **Step 6: Commit the baseline document**

```bash
git add docs/superpowers/working/type-check-baseline-2026-04-16.md
git commit -m "docs(typecheck): Phase 1 baseline error inventory"
```

---

## Phase 2 — Stubs sprint

Goal: every third-party import resolves to real type information, either via upstream/`types-*` packages (preferred) or a local stub under `stubs/`. After this phase, missing-stubs errors should be zero.

### Task 12 (template, repeat per untyped package)

The following template applies to each row of the "Missing-stubs errors" table in the baseline document. Work through one package per commit.

**Files:**
- Modify: `requirements.txt` (if adding a `types-*` package)
- Create: `stubs/<package>/__init__.pyi` (and deeper paths as needed, if writing a local stub)

- [ ] **Step 1: Pick the next package from the baseline table**

Choose the top row of "Missing-stubs errors" that has not been struck through.

- [ ] **Step 2: Check for upstream types**

```bash
.venv/bin/python - <<'PY'
import importlib.resources, sys
pkg = "<package>"
try:
    marker = importlib.resources.files(pkg) / "py.typed"
    print(f"{pkg}: py.typed exists = {marker.is_file()}")
except ModuleNotFoundError:
    print(f"{pkg}: not installed", file=sys.stderr)
    sys.exit(1)
PY
```
Expected: prints `py.typed exists = True` or `... = False`.

If `True`, upstream has types and the tool's error must be stale/mis-diagnosed. Re-run the three tools; if the error is gone, strike through the baseline row and skip to Step 6. If the error persists despite `py.typed = True`, the package ships a marker but its actual stubs are incomplete — treat as if it had no stubs and proceed.

- [ ] **Step 3: Check for a `types-*` PyPI package**

```bash
.venv/bin/pip index versions types-<package> 2>&1 | head -3
.venv/bin/pip index versions <package>-stubs 2>&1 | head -3
```
If one exists, add it to `requirements.txt`, `pip install -r requirements.txt`, and proceed to step 5.

- [ ] **Step 4: Write a local stub**

Create `stubs/<package>/__init__.pyi` (for flat packages) or `stubs/<package>/<module>.pyi` (for nested). Only annotate the symbols that are actually imported by our code. Example:

```python
# stubs/apscheduler/schedulers/background.pyi
from collections.abc import Callable
from datetime import datetime
from typing import Any

class BackgroundScheduler:
    def __init__(self, *, timezone: str | None = ..., job_defaults: dict[str, Any] | None = ...) -> None: ...
    def start(self) -> None: ...
    def shutdown(self, wait: bool = ...) -> None: ...
    def add_job(
        self,
        func: Callable[..., Any],
        trigger: str,
        *,
        id: str | None = ...,
        name: str | None = ...,
        next_run_time: datetime | None = ...,
        # ...only kwargs the project actually uses
    ) -> Any: ...
```

Rules:
- **No `Any` in public signatures.** Use generics, unions, or `object` for truly opaque values.
- **Only cover what we import.** Unused symbols are omitted, not stubbed.
- Match the symbol's true runtime signature — consult the library's source.

- [ ] **Step 5: Re-run the three checkers**

```bash
.venv/bin/python -m mypy    2>&1 | grep "<package>" | head -10
.venv/bin/python -m pyright 2>&1 | grep "<package>" | head -10
.venv/bin/python -m ty check 2>&1 | grep "<package>" | head -10
```
Expected: no missing-stubs errors for `<package>`. New errors may appear (call sites that were previously `Any` are now type-checked) — that is expected and will be handled in Phase 3.

- [ ] **Step 6: Strike through the package in the baseline table**

Edit `docs/superpowers/working/type-check-baseline-2026-04-16.md` and change the row to `~~apscheduler~~`.

- [ ] **Step 7: Commit**

Choose one:
```bash
# If local stub:
git add stubs/<package>/ docs/superpowers/working/type-check-baseline-2026-04-16.md
git commit -m "stubs(typecheck): add local stubs for <package>"

# If types-* package:
git add requirements.txt docs/superpowers/working/type-check-baseline-2026-04-16.md
git commit -m "deps(typecheck): add types-<package> stub package"
```

### Task 13: Phase 2 exit check

- [ ] **Step 1: Re-run all three tools and grep for any remaining missing-stubs errors**

```bash
.venv/bin/python -m mypy    2>&1 | grep -iE "stub|import-untyped|missing-imports" | head -20
.venv/bin/python -m pyright 2>&1 | grep -iE "missingTypeStubs" | head -20
.venv/bin/python -m ty check 2>&1 | grep -iE "stub|missing" | head -20
```
Expected: no output from any of the three.

- [ ] **Step 2: If anything remains**

Add it to the baseline table, loop Task 12 again. Phase 2 is not complete until all three greps produce zero lines.

---

## Phase 3 — Annotation sprint

Goal: every file in the scoped paths passes all three checkers. This is the long phase — weeks, potentially. Work module-by-module from leaves (fewest imports of project code) toward entry points, so downstream code sees typed inputs as it is annotated.

### Task 14 (template, repeat per module)

One commit per module. Commits must monotonically reduce the total error count (check the baseline document after each commit).

**Files:**
- Modify: the target module file(s), all under the scoped paths.

- [ ] **Step 1: Pick the next module from the baseline**

From the "Missing-annotations errors" table, choose a module with the fewest imports of other **unannotated** project code. Starting candidates: `auto_a11y/utils/*`, `auto_a11y/models/*`, `auto_a11y/core/touchpoints.py`.

- [ ] **Step 2: Run the three checkers on the target module only**

```bash
.venv/bin/python -m mypy    --follow-imports=silent <file> 2>&1 | head -40
.venv/bin/python -m pyright <file> 2>&1 | head -40
.venv/bin/python -m ty check <file> 2>&1 | head -40
```

- [ ] **Step 3: Add annotations**

For each function, method, class attribute, and module-level variable that the checkers flag:
- Function signatures: add parameter types and a return type. No `Any`; no `-> Any`.
- Instance variables: either assign in `__init__` with an explicit annotation, or declare at class level with `ClassVar` or a plain annotation.
- Module-level variables: annotate if their type is not obvious from the literal.

If a checker flags a real bug (not just a missing annotation): **fix the bug** as part of the same commit. Call this out in the commit message. Do not paper over real bugs with broader types.

- [ ] **Step 4: Run all three checkers on the module again**

```bash
.venv/bin/python -m mypy    <file>
.venv/bin/python -m pyright <file>
.venv/bin/python -m ty check <file>
```
Expected: zero errors on this file.

- [ ] **Step 5: Run the full test suite**

```bash
.venv/bin/python -m pytest tests/ -x -q
```
Expected: all tests still pass. Annotation work must not break behaviour.

- [ ] **Step 6: Update the baseline document**

Strike through the module row in the "Missing-annotations errors" table. Record any real bugs in the "Real type bugs" table with resolution status.

- [ ] **Step 7: Commit**

```bash
git add <modified files> docs/superpowers/working/type-check-baseline-2026-04-16.md
git commit -m "types: annotate auto_a11y/<module> (N bugs fixed: <short descriptions>)"
```

(If no real bugs were fixed, drop the parenthetical.)

### Task 15: Phase 3 exit check

- [ ] **Step 1: Run each checker at full scope against both target Python versions**

The `.venv/` provides a single interpreter; pass the target version to each tool via its CLI flag rather than invoking a non-existent `pythonX.Y` binary.

```bash
.venv/bin/python -m mypy    --python-version 3.11 ; echo "mypy 3.11 exit=$?"
.venv/bin/python -m mypy    --python-version 3.12 ; echo "mypy 3.12 exit=$?"
.venv/bin/python -m pyright --pythonversion 3.11  ; echo "pyright 3.11 exit=$?"
.venv/bin/python -m pyright --pythonversion 3.12  ; echo "pyright 3.12 exit=$?"
.venv/bin/python -m ty check                      ; echo "ty exit=$?"
# If the pinned ty version supports per-run python version selection
# (consult `ty check --help`), run it twice with both values; otherwise
# rely on the CI matrix for ty's 3.11/3.12 coverage.
```

Expected: every `echo` reports `exit=0`. If `ty` lacks per-run version selection, its two matrix variants in CI are the authoritative check — Phase 3 is not complete until both `typecheck (3.11)` and `typecheck (3.12)` pass in CI.

- [ ] **Step 2: Remove any remaining transitional suppression comments**

Search, scoped to the checked paths only (not the one-off scripts at repo root):
```bash
SCOPE="auto_a11y tests stubs config.py run.py wsgi.py test_fixtures.py"
grep -rn "# type: ignore"    $SCOPE
grep -rn "# pyright: ignore" $SCOPE
grep -rn "# ty: ignore"      $SCOPE
grep -rn "cast(Any"          $SCOPE
```

Expected: no output from any of the four greps. `warn_unused_ignores` (and equivalents) already catch orphaned suppressions, but this is a free belt-and-braces check.

- [ ] **Step 3: Run the full test suite one more time**

```bash
.venv/bin/python -m pytest tests/ -v
```
Expected: all tests pass.

- [ ] **Step 4: Commit the cleanup**

If any transitional ignores or other cleanup was needed:
```bash
git add <files>
git commit -m "types: remove transitional suppressions; Phase 3 complete"
```

---

## Phase 4 — Cutover (enforcement on)

Goal: flip local hooks and CI from informational to blocking.

### Task 16: Flip CI to blocking

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Remove step-level `continue-on-error: true` from each of the three tool steps**

Delete the three lines `continue-on-error: true` under the `Run mypy`, `Run pyright`, and `Run ty` steps. Leave the rest of the job structure intact. (Per Phase 0 design, there is no job-level `continue-on-error` to remove.)

- [ ] **Step 2: Lint the YAML**

```bash
.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
Expected: no output.

- [ ] **Step 3: Push and verify**

Push the branch. In the PR's CI output, the `typecheck (3.11)` and `typecheck (3.12)` jobs should both go green because Phase 3 is complete.

Confirm they are now failing-closed: on a disposable scratch branch, introduce an intentional type error (e.g., add `x: int = "string"` to the top of `tests/test_install_hooks.py`), `git add tests/test_install_hooks.py`, `git commit --no-verify -m "scratch"` (locally), push. Watch the `typecheck` job fail. Then discard: `git restore --source=HEAD~1 --staged --worktree tests/test_install_hooks.py && git reset --hard HEAD~1 && git push --force-with-lease` on the scratch branch (never on a shared branch). Delete the scratch branch.

(NB: this is the one place the plan explicitly authorises `--no-verify`, and only on a disposable scratch branch, to test that CI blocks.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(typecheck): flip typecheck job to blocking (Phase 4 cutover)"
```

### Task 17: Enable local hooks

- [ ] **Step 1: Run the installer**

```bash
.venv/bin/python run.py --install-hooks
```
Expected: `✓ Hooks installed`.

- [ ] **Step 2: Verify**

```bash
git config --local core.hooksPath
```
Expected: prints `.githooks`.

- [ ] **Step 3: Test the hook blocks**

```bash
# Introduce a deliberate type error
echo 'x: int = "string"' >> tests/test_install_hooks.py
git add tests/test_install_hooks.py
git commit -m "scratch: should be blocked"
```
Expected: commit fails; output shows `==> mypy`, `==> pyright`, `==> ty` sections and `COMMIT BLOCKED`.

Then **fully revert** (important: restore both staged and worktree state):
```bash
git restore --staged tests/test_install_hooks.py
git restore tests/test_install_hooks.py
# Verify clean state:
git status tests/test_install_hooks.py
# Expected: no output (file matches HEAD).
```

### Task 18: Repo-owner manual steps

- [ ] **Step 1: Announce to the repo owner**

The repo owner (not Claude) must:
1. In GitHub → Settings → Branches → Branch protection rules for `main`: add `typecheck (3.11)` and `typecheck (3.12)` as required status checks.
2. Optionally announce the cutover to contributors so they run `python run.py --install-hooks` in their own clones.

Phase 4 is complete when branch protection is updated.

### Task 19: Retire the working baseline document

**Files:**
- Delete: `docs/superpowers/working/type-check-baseline-2026-04-16.md`

- [ ] **Step 1: Confirm the baseline document has no un-struck data rows**

A data row (as opposed to a header or separator) is a `|`-delimited line that does NOT start the table, is NOT a `|---|` separator, and does NOT already contain `~~` (our strike-through marker).

```bash
.venv/bin/python - <<'PY'
import re, sys
path = "docs/superpowers/working/type-check-baseline-2026-04-16.md"
with open(path) as fh:
    lines = fh.readlines()
data_row = re.compile(r"^\s*\|.+\|\s*$")
separator = re.compile(r"^\s*\|\s*-+\s*(\|\s*-+\s*)*\|\s*$")
header_names = {"Package", "File", "File:line", "Tool"}
leftovers = []
for i, line in enumerate(lines, start=1):
    if not data_row.match(line): continue
    if separator.match(line): continue
    if "~~" in line: continue
    # Header rows contain our column names
    cells = [c.strip() for c in line.strip("|\n ").split("|")]
    if cells and cells[0] in header_names: continue
    leftovers.append((i, line.rstrip()))
if leftovers:
    print("Un-struck data rows remain:")
    for i, l in leftovers[:20]: print(f"  {i}: {l}")
    sys.exit(1)
print("All data rows struck through; baseline is ready to retire.")
PY
```
Expected: `All data rows struck through; baseline is ready to retire.`

- [ ] **Step 2: Delete the working document**

It served its purpose in Phases 1–3. No longer needed.

```bash
git rm docs/superpowers/working/type-check-baseline-2026-04-16.md
git commit -m "docs(typecheck): retire Phase 1–3 baseline document; cutover complete"
```

---

## Skills to invoke during execution

- @superpowers:test-driven-development — for Task 5 (`--install-hooks`), which has real behaviour.
- @superpowers:verification-before-completion — before marking Phases 0, 2, 3, or 4 complete. Do not claim a phase is done without the exit-check output.
- @superpowers:receiving-code-review — when PR review comments arrive on the Phase 0 PR or the Phase 4 cutover PR.
- @superpowers:systematic-debugging — if a `ty` crash blocks a commit before the recovery procedure is needed.

## Definition of done

- All three tools report zero errors at both Python matrix versions in CI.
- `.githooks/pre-commit` blocks commits containing intentional type errors.
- `grep -rn "# type: ignore\|# pyright: ignore\|# ty: ignore" auto_a11y tests *.py` produces no output.
- `docs/superpowers/working/type-check-baseline-2026-04-16.md` is deleted.
- CLAUDE.md, README.md, README.fr.md all document the policy.
- Branch protection on `main` requires the `typecheck` job (manual, repo owner).
