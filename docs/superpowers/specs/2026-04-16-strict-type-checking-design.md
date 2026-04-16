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

**Stubs:** a repo-root `stubs/` directory holds local `.pyi` stubs for every dependency that does not ship type information. `MYPYPATH`, pyright's `stubPath`, and ty's equivalent all point here. Stubs are **themselves type-checked** — `stubs/**/*.pyi` is included in every tool's scope so stub bugs surface in the stub file rather than at the call site.

**`py.typed` marker.** An empty `auto_a11y/py.typed` file is added as part of Phase 0 so that this package, when installed as a dependency elsewhere, exposes its annotations to downstream type checkers.

**Formatting.** The project already pins `black` and `flake8` in `requirements.txt`. Annotation-heavy commits (Phases 2 and 3) are expected to pass existing formatter/lint rules — contributors run `black` before committing. No new formatter is introduced by this work.

### Scope

Checked paths (enforced identically by all three tools):

```
auto_a11y/**/*.py
tests/**/*.py
stubs/**/*.pyi
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

This command is documented in CLAUDE.md/README.md as a required setup step. It is also executed automatically by a new `python run.py --install-hooks` command (not folded into `--setup`, which is application/database-focused and runs in Docker contexts where there is no `.git` directory). `--install-hooks` is a no-op outside a git worktree.

**Existing hook.** The repository already ships `.githooks/pre-commit` — a translation-validation hook from the old `.po`/`.mo` system that predates the current Fluent (`.ftl`) setup. That hook's active logic refers to files no longer present (`scripts/validate_translations.py`, `messages.po`, `messages.mo`) and is effectively dead. The existing hook is **removed and fully replaced** by the new type-checking hook (Phase 0). The historical translation checks are not reintroduced; current translation validation is enforced by `tests/validate_translations.py` invoked from CI.

**File:** `.githooks/pre-commit` — a shell script that:

1. Runs `mypy` against the scoped paths.
2. Runs `pyright` against the scoped paths.
3. Runs `ty check` against the scoped paths.

All three run regardless of individual failures so the developer sees the full picture in one go. The script exits non-zero if any of the three failed, blocking the commit.

**Runtime expectations.** Expected cold-cache time for the combined hook is 30–90 seconds on the current codebase; warm-cache incremental is 5–20 seconds. `mypy` uses its `.mypy_cache/` by default (already gitignored); `pyright` is incremental per-process; `ty` currently has limited incrementality. If the hook exceeds ~60 seconds warm, this is a signal to investigate tool caching rather than to scope down the hook.

**Tool-disagreement tiebreaker.** When two tools disagree about whether code is valid, priority is:

1. Fix the code so all three accept it (the common case — usually a missing annotation).
2. If genuinely impossible, treat the disagreement as a bug in the *less-authoritative* tool in the order `mypy` > `pyright` > `ty`. Work around it by refactoring code, adjusting stubs, or pinning tool versions. **Never** by suppression.
3. If a three-way irreconcilable conflict somehow emerges, the implementer must surface the case to the repo owner — do not land the code.

**`ty` recovery procedure.** `ty` is pre-alpha. A `ty` crash or false positive that no refactor can satisfy puts the zero-escape-hatch policy into direct conflict with the "ty must pass" rule. The escape path is a deliberate, logged, reversible config change: a project-defined flag in `pyproject.toml` (e.g., `[tool.auto_a11y_typecheck] ty_enabled = false` — a project-namespaced key, not a `ty`-native option) is read by both the hook script and the CI workflow to skip the `ty` step. Flipping this flag requires filing a tracking issue linking to the upstream `ty` bug; the flag must be flipped back on the next `ty` release that fixes it. This is not a suppression comment — it is a configuration downgrade applied repo-wide, visible to all reviewers in every diff until reverted. It MUST NOT be used for `mypy` or `pyright` (no equivalent flag exists for them by design) and MUST NOT be used to avoid fixing real type errors.

No `pre-commit` library dependency. Tools are invoked directly from the project `.venv`.

### CI integration

A new `typecheck` job is added to `.github/workflows/ci.yml`, running in parallel with the existing `test` job. The typecheck job does not need MongoDB, Playwright, or browser setup — it is pure static analysis, which makes its install step substantially leaner than `test`'s and gives visible wall-clock savings under normal conditions.

**Python version.** The typecheck job runs on a **matrix of 3.11 and 3.12** so that whichever version a contributor uses locally (`.venv` is 3.12 for some contributors; CI pytest still uses 3.11), both code paths are validated. Typing syntax that is 3.12-only (PEP 695 generics, `type` statement) is permitted because the matrix catches 3.11 incompatibility. Pyright's `pythonVersion` and mypy's `python_version` options are parameterised by the matrix value.

Structure:

```yaml
typecheck:
  runs-on: ubuntu-latest
  strategy:
    matrix:
      python-version: ["3.11", "3.12"]
  steps:
    - checkout
    - setup-python ${{ matrix.python-version }} (with pip cache)
    - cache ~/.cache/pyright-python (the Node binary; key includes pyright version)
    - pip install -r requirements.txt
    - run: mypy (scoped paths)
    - run: pyright (scoped paths)
    - run: ty check (scoped paths)
```

The `pyright` PyPI wrapper downloads a Node.js runtime + the pyright distribution at install time. To keep the Node payload off the hot path, the CI job caches `~/.cache/pyright-python` with a key derived from the pinned pyright version. The wrapper's version is frozen via the `PYRIGHT_PYTHON_FORCE_VERSION` environment variable so contributors and CI resolve identical binaries.

All three checkers run even if one fails, so a single CI run surfaces every failure at once. The job fails if any of the three exited non-zero. Each tool appears as its own step in the GitHub Actions UI so failures are attributable.

Branch protection on `main` is configured (manually, by the repo owner) to require the `typecheck` job (both matrix variants). This is noted in CLAUDE.md as a required setup step.

### Documentation

**CLAUDE.md** — new top-level section titled **"Type Checking (MANDATORY)"**, placed adjacent to the existing "Colour System (MANDATORY)" and "Bilingual Translation Requirements (MANDATORY)" sections for consistency. Contents:

- Policy: all in-scope code must pass `mypy --strict`, `pyright --strict`, and `ty` strictest mode.
- Zero-ignore rule: no suppression comments, no `cast(Any, ...)`, no `Any` returns, no `--no-verify`.
- Setup: one-time `git config core.hooksPath .githooks` (or `python run.py --install-hooks`).
- Workflow: hooks run on every commit; failures are fixed, not suppressed.
- Dependency changes: new untyped libraries require stubs in the same commit.
- Scope changes: adding a new module to checked set updates all three tools' configs.
- No escape hatch. Tool bugs are worked around, not suppressed.

The Git Workflow section is updated with an explicit prohibition on `--no-verify`.

**README.md** — new **Type Checking** section under the Development setup area. Contents: brief intro, install reminder, one-time hook setup (`python run.py --install-hooks` or `git config core.hooksPath .githooks`), manual-invocation commands (`mypy`, `pyright`, `ty check`), link to CLAUDE.md.

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
2. **For each untyped import, first check whether types already exist upstream** before writing a local stub. Check order: (a) upstream `py.typed` marker in a newer version of the library, (b) `types-<package>` or `<package>-stubs` on PyPI, (c) `typeshed`. If any upstream option exists, use it (add the stubs package to `requirements.txt`) instead of writing a local stub.
3. For libraries with no upstream types, write a minimal fully-typed `.pyi` under `stubs/`. Only the symbols we import. No `Any` in signatures.
4. Re-run checkers; import-related errors should drop to zero.

**Phase 3 — Annotations:**

1. Annotate module by module, starting with leaf modules (fewest imports *of project code*) and working toward entry points so downstream code sees typed inputs.
2. Fix real type bugs as they surface.
3. CI's informational typecheck job gives visible progress (error count monotonically decreases PR over PR).

**Phase 4 — Flip the switch:**

1. Zero errors from all three tools on both the 3.11 and 3.12 matrix entries.
2. Contributors enable `core.hooksPath` via `python run.py --install-hooks` (or by running the one-line git config manually).
3. Change the CI typecheck job to blocking (remove any informational flag / `continue-on-error: true`).
4. Add the typecheck job (both matrix variants) to branch protection (manual, by repo owner).
5. Announce the cutover via CLAUDE.md and README updates.

During Phase 3, new feature PRs must annotate whatever they touch, so the baseline shrinks rather than grows. Contributors who bypass this (via unannotated additions) lose time in Phase 4 when the baseline must be re-closed.

## Open questions

None. All decisions are locked above.

## Risks

- **`ty` instability.** `ty` is pre-alpha. Upgrades may introduce new errors, crashes, or config changes. A crash or false positive combined with the zero-escape-hatch policy could deadlock all commits. Mitigation: pin the version; the documented `ty`-specific config-downgrade escape path in "Commit hook" above allows recovery from unrecoverable `ty` bugs without suppressing the other two checkers.
- **Phase 3 scope.** Thousands-to-tens-of-thousands of errors is possible. Realistic timeline is weeks, not days. Mitigation: leaf-to-root order keeps each module's work bounded; CI informational runs surface progress.
- **Tool disagreement.** Three strict checkers will sometimes disagree. The tiebreaker rule (mypy > pyright > ty, refactor to satisfy all three before anything else) is in "Commit hook" above.
- **Stub drift.** Locally-written stubs for third-party libraries can become inaccurate when the library updates. Mitigation: pin library versions; re-validate stubs when bumping. Prefer upstream types or `types-*` packages where they exist (see Phase 2).
- **New untyped dependencies slip in.** A contributor could add a dependency without adding its stub. Mitigation: the hook and CI will catch it (the import is an error until the stub exists); CLAUDE.md documents the rule.
- **Python version drift.** CI previously used only 3.11, while contributors' `.venv` is 3.12. Strict-mode typing may expose syntax-level differences (e.g. PEP 695 generics in 3.12). Mitigation: the CI typecheck matrix runs both 3.11 and 3.12, so local-only syntax usage is caught upstream.
- **`pyright` Node binary caching.** The `pyright` PyPI wrapper pulls a Node binary at install time. On CI this adds time on a cold cache; in airgapped dev environments it blocks setup. Mitigation: CI caches `~/.cache/pyright-python` keyed on pinned version; `PYRIGHT_PYTHON_FORCE_VERSION` pins the binary version.

## Testing strategy

- Phase 0 infrastructure is validated by running each tool manually and confirming it reports errors against the current codebase (i.e., it is actually running, not silently skipping).
- Phase 2 is validated by running each tool after the stub sprint and confirming zero import-related errors.
- Phase 3 is validated by monotonically decreasing error counts recorded in the informational CI job.
- Phase 4 is validated by a clean run of all three tools, the hook blocking a test commit with an intentional type error, and the CI job failing on an intentional type error in a test PR.
