# Strict Type Checking Infrastructure

**Date:** 2026-04-16
**Status:** Design approved, awaiting implementation plan
**Scope:** Add `mypy`, `pyright`, and `ty` in their strictest modes to both local git hooks and CI, with a zero-escape-hatch policy enforced across the main application code and tests.

## Problem

The project has ~103,000 lines of Python across 269 files with effectively no type annotations (only 5 files import from `typing`). There is no type-checking infrastructure (no `pyproject.toml` config, no `mypy.ini`, no `pyrightconfig.json`, no git hooks). This creates an environment where type-related bugs can accumulate silently and where new code has no static safety net.

## Goals

1. Enforce strict static type checking by three independent checkers — `mypy`, `pyright`, and `ty` — so that type bugs caught by any tool are caught.
2. Enforce checks both locally (via git hooks) and in CI, so that no un-type-checked commit can reach `main`.
3. Zero escape hatches: no `# type: ignore`, no `# pyright: ignore`, no `# ty: ignore`, no `cast(Any, ...)` workarounds, no `--no-verify` bypasses.
4. Document the policy so that all contributors understand that passing type checks is non-negotiable.

## Non-Goals

- Type-checking one-off migration scripts, debug scripts, or archived code.
- Type-checking the `auto_a11y/scripts/` directory (it contains JavaScript, not Python).
- Maintaining backwards compatibility with code that isn't committed yet.

## Design

### Tooling

All three type checkers are pinned via `requirements.txt` and configured via `pyproject.toml`. No separate config files.

| Tool | Current status | Target version |
|------|---------------|----------------|
| `mypy` | Installed (1.19.1) | Pinned to latest stable |
| `pyright` | Not installed | Installed via the `pyright` PyPI wrapper (bundles the Node binary) |
| `ty` | Not installed | Installed via PyPI; pinned to a specific pre-release |

**Configuration in `pyproject.toml`:**

`[tool.mypy]` — `strict = true` plus `disallow_any_unimported = true`, `warn_unused_ignores = true`, `warn_redundant_casts = true`, `strict_equality = true`, `extra_checks = true`. Explicit `files` list restricts scope.

`[tool.pyright]` — `typeCheckingMode = "strict"`, with all relevant `report*` levers cranked to `"error"`: `reportMissingTypeStubs`, `reportImplicitOverride`, `reportUninitializedInstanceVariable`, `reportUnknownParameterType`, `reportUnknownVariableType`, `reportUnknownArgumentType`, `reportUnknownMemberType`, etc. Explicit `include` list restricts scope.

`[tool.ty]` — the strictest available settings for the current `ty` release. Because `ty` is still pre-alpha, the exact config surface will be pinned to the installed version and revisited when upgrading.

**Stubs:** a repo-root `stubs/` directory holds local `.pyi` stubs for every dependency that does not ship type information. `MYPYPATH`, pyright's `stubPath`, and ty's equivalent all point here.

### Scope

Checked paths (enforced identically by all three tools):

```
auto_a11y/**/*.py
tests/**/*.py
config.py
run.py
wsgi.py
test_fixtures.py
```

Explicitly excluded:

- `.venv/`, `env/`, `build/`, `electron/`, `__pycache__/`
- `archive/`, `demo_site/`, `fixture_generation/`
- Top-level one-off scripts: `add_*.py`, `cleanup_*.py`, `migrate_*.py`, `translate_*.py`, `debug_*.py`, `diagnose_*.py`, `investigate_*.py`, `analyze_*.py`, `create_phase*.py`, `delete_*.py`, `clear_*.py`, `show_*.py`, `examine_*.py`, `revert_*.py`, `merge_*.py`, `generate_fixtures*.py`, `download_chromium.py`, `gunicorn.conf.py`
- `auto_a11y/scripts/` — JavaScript test scripts, not Python (already excluded by file extension but worth noting)

### Escape-hatch policy

There is **no escape hatch**. Concretely:

- No `# type: ignore`, `# pyright: ignore`, or `# ty: ignore` comments anywhere in the scoped codebase.
- No `cast(Any, ...)` used as a workaround.
- No `Any` return types.
- No `--no-verify` commits (enforced by policy in CLAUDE.md; CI re-runs the same checks so any bypass is caught upstream).
- `warn_unused_ignores` (and equivalents) are enabled so stray suppression comments are themselves errors.
- If a genuine tool bug blocks progress, it is worked around in code (refactor, stub patch, tool version pin), never suppressed.

### Third-party libraries

Every import from a library lacking type information requires a local stub in `stubs/<package>/`. Stubs cover only the symbols actually used by the project; unused APIs are omitted. No `Any` in stub signatures — every function, attribute, and class member is fully typed. Adding a new dependency requires adding its stub in the same commit (or picking a different library).

Examples of libraries expected to need stubs: `fluent-compiler`, `flask-wtf`, `flask-limiter`, `apscheduler`, `weasyprint`, `pyphen`, `pydyf`, `pytz`, `typst`, `deprecated`, `brotli`, `zopfli`, `xlsxwriter`. The complete list is built from the Phase 1 baseline output.

### Commit hook

Git hooks live in a committed `.githooks/` directory. One-time developer setup:

```bash
git config core.hooksPath .githooks
```

This command is documented in CLAUDE.md/README.md as a required setup step and is also executed automatically by `python run.py --setup`.

**File:** `.githooks/pre-commit` — a shell script that:

1. Runs `mypy` against the scoped paths.
2. Runs `pyright` against the scoped paths.
3. Runs `ty check` against the scoped paths.

All three run regardless of individual failures so the developer sees the full picture in one go. The script exits non-zero if any of the three failed, blocking the commit.

No `pre-commit` library dependency. Tools are invoked directly from the project `.venv`.

### CI integration

A new `typecheck` job is added to `.github/workflows/ci.yml`, running in parallel with the existing `test` job. The typecheck job does not need MongoDB, Playwright, or browser setup — it is pure static analysis.

Structure:

```yaml
typecheck:
  runs-on: ubuntu-latest
  steps:
    - checkout
    - setup-python 3.11 (with pip cache)
    - pip install -r requirements.txt
    - run: mypy (scoped paths)
    - run: pyright (scoped paths)
    - run: ty check (scoped paths)
```

All three checkers run even if one fails, so a single CI run surfaces every failure at once. The job fails if any of the three exited non-zero. Each tool appears as its own step in the GitHub Actions UI so failures are attributable.

Branch protection on `main` is configured (manually, by the repo owner) to require the `typecheck` job. This is noted in CLAUDE.md as a required setup step.

### Documentation

**CLAUDE.md** — new top-level section titled **"Type Checking (MANDATORY)"**, placed adjacent to the existing "Colour System (MANDATORY)" and "Bilingual Translation Requirements (MANDATORY)" sections for consistency. Contents:

- Policy: all in-scope code must pass `mypy --strict`, `pyright --strict`, and `ty` strictest mode.
- Zero-ignore rule: no suppression comments, no `cast(Any, ...)`, no `Any` returns, no `--no-verify`.
- Setup: one-time `git config core.hooksPath .githooks` (or `python run.py --setup`).
- Workflow: hooks run on every commit; failures are fixed, not suppressed.
- Dependency changes: new untyped libraries require stubs in the same commit.
- Scope changes: adding a new module to checked set updates all three tools' configs.
- No escape hatch. Tool bugs are worked around, not suppressed.

The Git Workflow section is updated with an explicit prohibition on `--no-verify`.

**README.md** — new **Type Checking** section under the Development setup area. Contents: brief intro, install reminder, `git config core.hooksPath .githooks` setup, manual-invocation commands (`mypy`, `pyright`, `ty check`), link to CLAUDE.md.

**README.fr.md** — mirrored French translation of the new README section, per the project's bilingual-README policy.

### Bootstrap plan (hard cutover)

Adding strict typing to a 50k+ line unannotated codebase is a multi-week effort. It is executed in phases; the infrastructure is deployed first in non-enforcing mode, then enforcement is flipped on only after all errors are resolved.

**Phase 0 — Infrastructure (non-enforcing):**

1. Install `pyright` and `ty`, pin versions, update `requirements.txt`.
2. Write `pyproject.toml` config sections for all three tools.
3. Create `stubs/` directory (empty).
4. Write `.githooks/pre-commit` but do not set `core.hooksPath` yet.
5. Add the `typecheck` CI job but mark it informational (failures logged, not blocking).
6. Commit as a single "infrastructure only" change.

**Phase 1 — Baseline inventory:**

1. Run all three checkers against the full scope.
2. Dump the output to a working document.
3. Categorise: missing stubs, missing annotations, real type bugs, tool disagreements.

**Phase 2 — Stubs:**

1. Enumerate every untyped import from the baseline.
2. Write a minimal fully-typed `.pyi` for each. Only the symbols we import. No `Any` in signatures.
3. Re-run checkers; import-related errors should drop to zero.

**Phase 3 — Annotations:**

1. Annotate module by module, starting with leaf modules (fewest imports) and working toward entry points so downstream code sees typed inputs.
2. Fix real type bugs as they surface.
3. CI's informational typecheck job gives visible progress.

**Phase 4 — Flip the switch:**

1. Zero errors from all three tools.
2. Enable `core.hooksPath` via `run.py --setup`.
3. Change the CI typecheck job to blocking.
4. Add the typecheck job to branch protection (manual, by repo owner).
5. Announce the cutover via CLAUDE.md and README updates.

During Phase 3, new feature PRs must annotate whatever they touch, so the baseline shrinks rather than grows. Contributors who bypass this (via unannotated additions) lose time in Phase 4 when the baseline must be re-closed.

## Open questions

None. All decisions are locked above.

## Risks

- **`ty` instability.** `ty` is pre-alpha. Upgrades may introduce new errors, crashes, or config changes. Mitigation: pin the version; upgrade only deliberately; document the pinned version.
- **Phase 3 scope.** Thousands-to-tens-of-thousands of errors is possible. Realistic timeline is weeks, not days. Mitigation: leaf-to-root order keeps each module's work bounded; CI informational runs surface progress.
- **Tool disagreement.** Three strict checkers will sometimes disagree. Mitigation: fix the code to satisfy the strictest interpretation; never suppress one to satisfy another.
- **Stub drift.** Locally-written stubs for third-party libraries can become inaccurate when the library updates. Mitigation: pin library versions; re-validate stubs when bumping.
- **New untyped dependencies slip in.** A contributor could add a dependency without adding its stub. Mitigation: the hook and CI will catch it (the import is an error until the stub exists); CLAUDE.md documents the rule.

## Testing strategy

- Phase 0 infrastructure is validated by running each tool manually and confirming it reports errors against the current codebase (i.e., it is actually running, not silently skipping).
- Phase 2 is validated by running each tool after the stub sprint and confirming zero import-related errors.
- Phase 3 is validated by monotonically decreasing error counts recorded in the informational CI job.
- Phase 4 is validated by a clean run of all three tools, the hook blocking a test commit with an intentional type error, and the CI job failing on an intentional type error in a test PR.
