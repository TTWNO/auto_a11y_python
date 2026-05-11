# SPA Click-Based Discovery Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-website opt-in flag that lets discovery click anchors whose `href` is unusable (`#`, empty, `javascript:`) and record the resulting `window.location`, so single-page-app routes can be discovered.

**Architecture:** One new boolean on `ScrapingConfig` (mirrored on `DiscoveryRun`). When set, `_extract_links` in the scraper additionally calls a new helper, `_extract_links_via_clicking`, that collects no-usable-href anchors, filters destructive ones by text regex, caps the count, and clicks each one in turn — re-navigating to the parent URL between clicks to keep state fresh — and pushes the resulting URLs through the same filter pipeline used for href-based links.

**Tech Stack:** Python 3.11, Playwright (async), Flask/Jinja2, MongoDB, Project Fluent (.ftl) for i18n, pytest with `pytest-asyncio` + `unittest.mock.AsyncMock` for unit tests.

**Spec:** `docs/superpowers/specs/2026-05-11-spa-click-discovery-design.md`

---

## Context & ground rules the implementer must know

1. **Strict typing is mandatory.** All new code must pass `mypy`, `pyright`, and `ty` in strict mode. No `# type: ignore`, no `Any`, no `cast(Any, ...)`. The pre-commit hook runs all three on every commit. Configuration lives in `pyproject.toml`. To run manually:

   ```bash
   .venv/bin/python -m mypy
   .venv/bin/python -m pyright
   .venv/bin/python -m ty check
   ```

2. **Bilingual FTL strings.** Every new user-visible string in templates must use `{{ ftl('message-id') }}`. The message-ID must exist in **both** `auto_a11y/web/translations/en/websites.ftl` **and** `auto_a11y/web/translations/fr/websites.ftl`. Validation runs via `python tests/validate_translations.py`. Keys are alphabetically sorted within each file (the validator does not enforce this but the convention is consistent).

3. **No Bootstrap colour classes.** `alert-info` is a project-custom class (defined in `auto_a11y/web/static/public/css/style.css`) — fine to use. `alert-warning`, `alert-primary`, `text-warning`, etc. are prohibited. See CLAUDE.md "Colour System" section.

4. **Git workflow.** No `git commit --amend`, no `git rebase -i`, no `git reset --hard`. Every fix is a new commit. Local pre-commit hooks must succeed; do not bypass with `--no-verify`. If GPG signing times out, the user has told us to commit with `git -c commit.gpgsign=false commit ...`.

5. **FTL key naming convention.** Two parallel patterns exist:
   - **Distinct-name pattern:** `websites-follow-external` = "Follow External:" (dt label) and `websites-follow-external-links` = "Follow external links" (checkbox label) — different keys for different surfaces.
   - **`-2` suffix pattern:** `websites-max-pages` = "Max Pages:" (dt with colon) and `websites-max-pages-2` = "Max Pages" (form/inline without colon).

   For SPA discovery we will use the distinct-name pattern (clearer for English readers).

6. **Test style.** Look at `tests/test_scraping_job_auto_fetch_pdfs.py` for the canonical pattern: `unittest.mock.MagicMock` + `AsyncMock`, `@pytest.mark.asyncio`, narrow tests of a single function. No real browser, no HTTP server. We follow this style for the new helper.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `auto_a11y/models/website.py` | Add one field to `ScrapingConfig`; round-trip through `to_dict`/`from_dict`. |
| `auto_a11y/models/discovery_run.py` | Add one field to `DiscoveryRun`; round-trip through `to_dict`/`from_dict`. |
| `auto_a11y/core/scraper.py` | Module constants, new helper `_extract_links_via_clicking`, glue inside `_extract_links`, populate `DiscoveryRun.spa_click_discovery` in `discover_website`. |
| `auto_a11y/web/routes/websites.py` | One line in the edit-website handler to map form checkbox → config attr. |
| `auto_a11y/web/templates/websites/edit.html` | One new checkbox + help text inside the scraping-config fieldset. |
| `auto_a11y/web/templates/websites/view.html` | Conditional `alert alert-info` notice inside the discovery modal. |
| `auto_a11y/web/templates/websites/discovery_run.html` | One new `<dt>`/`<dd>` pair in the discovery-parameters card. |
| `auto_a11y/web/translations/en/websites.ftl` | Four new English message IDs. |
| `auto_a11y/web/translations/fr/websites.ftl` | Four new French message IDs. |
| `tests/test_spa_click_discovery.py` (new) | All unit tests for the new helper and config plumbing. |

---

## Phase 1 — Data model & UI groundwork

Cosmetic-feeling tasks, but they enable the rest of the work and are easy to verify in isolation. Run after Phase 1: the UI changes are visible on the website-edit page, but the helper isn't wired yet, so behavior is unchanged.

### Task 1: Add `spa_click_discovery` to `ScrapingConfig`

**Files:**
- Modify: `auto_a11y/models/website.py:14-47`
- Test: `tests/test_spa_click_discovery.py` (new)

- [ ] **Step 1: Create the test file with the first failing test**

Create `tests/test_spa_click_discovery.py`:

```python
"""Tests for SPA click-based discovery feature."""
from __future__ import annotations

from auto_a11y.models.website import ScrapingConfig


class TestScrapingConfigSpaClickDiscovery:
    def test_default_is_false(self) -> None:
        cfg = ScrapingConfig()
        assert cfg.spa_click_discovery is False

    def test_round_trip_preserves_true(self) -> None:
        cfg = ScrapingConfig(spa_click_discovery=True)
        restored = ScrapingConfig.from_dict(cfg.to_dict())
        assert restored.spa_click_discovery is True

    def test_from_dict_missing_key_defaults_false(self) -> None:
        restored = ScrapingConfig.from_dict({"max_pages": 100})
        assert restored.spa_click_discovery is False
```

- [ ] **Step 2: Run to verify all three fail**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py -v
```

Expected: 3 failures — `AttributeError: ScrapingConfig has no attribute 'spa_click_discovery'` (or similar) on the first test, the rest unreachable.

- [ ] **Step 3: Add the field to `ScrapingConfig`**

In `auto_a11y/models/website.py`, add the field at the end of the existing list:

```python
@dataclass
class ScrapingConfig:
    """Configuration for website scraping"""
    max_pages: int = 999999
    max_depth: int = 10
    follow_external: bool = False
    include_subdomains: bool = True
    respect_robots: bool = True
    request_delay: float = 1.0
    allowed_paths: list[str] = field(default_factory=lambda: [])
    excluded_paths: list[str] = field(default_factory=lambda: [])
    auto_fetch_pdfs: bool = True
    spa_click_discovery: bool = False
```

And add to `to_dict` (after `'auto_fetch_pdfs': self.auto_fetch_pdfs,`):

```python
'spa_click_discovery': self.spa_click_discovery,
```

`from_dict` already filters unknown keys via `fields(cls)`, so no change there.

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py -v
```

Expected: 3 passes.

- [ ] **Step 5: Run type checks**

```bash
.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check
```

All three must succeed.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/models/website.py tests/test_spa_click_discovery.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(model): add spa_click_discovery flag to ScrapingConfig

Default False preserves existing crawl behavior. No DB migration
required: from_dict already filters unknown keys.

Spec: docs/superpowers/specs/2026-05-11-spa-click-discovery-design.md
EOF
)"
```

---

### Task 2: Add `spa_click_discovery` to `DiscoveryRun`

**Files:**
- Modify: `auto_a11y/models/discovery_run.py:22-148`
- Test: `tests/test_spa_click_discovery.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_spa_click_discovery.py`:

```python
from auto_a11y.models.discovery_run import DiscoveryRun


class TestDiscoveryRunSpaClickDiscovery:
    def test_default_is_false(self) -> None:
        run = DiscoveryRun(website_id="wid-1")
        assert run.spa_click_discovery is False

    def test_round_trip_preserves_true(self) -> None:
        run = DiscoveryRun(website_id="wid-1", spa_click_discovery=True)
        restored = DiscoveryRun.from_dict(run.to_dict())
        assert restored.spa_click_discovery is True

    def test_from_dict_missing_key_defaults_false(self) -> None:
        restored = DiscoveryRun.from_dict({"website_id": "wid-1"})
        assert restored.spa_click_discovery is False
```

- [ ] **Step 2: Run to verify failures**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py::TestDiscoveryRunSpaClickDiscovery -v
```

Expected: failures on the new tests.

- [ ] **Step 3: Add the field to `DiscoveryRun`**

In `auto_a11y/models/discovery_run.py`, in the `@dataclass DiscoveryRun:` definition (after `respect_robots: bool = True` at line 35), add:

```python
spa_click_discovery: bool = False
```

In `to_dict` (after `'respect_robots': self.respect_robots,` at line 103), add:

```python
'spa_click_discovery': self.spa_click_discovery,
```

In `from_dict` (after `respect_robots=data.get('respect_robots', True),` at line 133), add:

```python
spa_click_discovery=data.get('spa_click_discovery', False),
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py -v
```

All 6 tests should pass.

- [ ] **Step 5: Run type checks**

```bash
.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check
```

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/models/discovery_run.py tests/test_spa_click_discovery.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(model): record spa_click_discovery on DiscoveryRun

Discovery-run history now reflects whether each run used click-based
discovery, so the discovery_run detail page can display it.
EOF
)"
```

---

### Task 3: Add FTL strings (English + French)

**Files:**
- Modify: `auto_a11y/web/translations/en/websites.ftl`
- Modify: `auto_a11y/web/translations/fr/websites.ftl`

- [ ] **Step 1: Add English strings**

Insert into `auto_a11y/web/translations/en/websites.ftl` in alphabetical order (between existing `websites-respect-robots-txt` and any later `websites-s...` keys; `websites-spa-...` sits near the bottom of the `websites-s*` block). Add **four** keys exactly as below:

```ftl
websites-spa-click-discovery = Click-based discovery:
websites-spa-click-discovery-toggle = Click-based discovery (for single-page apps)
websites-spa-click-discovery-help = Discovers SPA pages by clicking links instead of reading their href. Required when an SPA uses href="#" with JavaScript routing. Significantly slower — full discovery may take many times longer than normal.
websites-spa-click-discovery-warning = This website uses click-based discovery. Expect runs to take considerably longer than normal.
```

- [ ] **Step 2: Add French translations**

Insert into `auto_a11y/web/translations/fr/websites.ftl` at the corresponding alphabetical position:

```ftl
websites-spa-click-discovery = Découverte par clic :
websites-spa-click-discovery-toggle = Découverte par clic (pour applications monopage)
websites-spa-click-discovery-help = Découvre les pages d'une application monopage en cliquant sur les liens au lieu de lire leur attribut href. Nécessaire lorsqu'une application monopage utilise href="#" avec un routage JavaScript. Beaucoup plus lent — une découverte complète peut prendre plusieurs fois plus de temps que la normale.
websites-spa-click-discovery-warning = Ce site utilise la découverte par clic. Prévoyez des durées d'exécution sensiblement plus longues que la normale.
```

Note the French typographic space before `:` per French typography rules.

- [ ] **Step 3: Validate translation coverage**

```bash
.venv/bin/python tests/validate_translations.py
```

Expected: no missing keys reported. If the validator complains about the new keys not being used in templates, that's fine for now — Tasks 4–6 add the usages.

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/translations/en/websites.ftl auto_a11y/web/translations/fr/websites.ftl
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
i18n(websites): add SPA click-discovery strings (en + fr)

Four new keys cover: form-label (toggle), help text, dt label (display),
and the slowness warning notice.
EOF
)"
```

---

### Task 4: Wire the checkbox into the website-edit form and route handler

**Files:**
- Modify: `auto_a11y/web/templates/websites/edit.html:91-98` (after the `respect_robots` block)
- Modify: `auto_a11y/web/routes/websites.py:193-198`

- [ ] **Step 1: Add the checkbox to the edit template**

Open `auto_a11y/web/templates/websites/edit.html`. After the existing `respect_robots` `form-check` block (which ends with `</div>` at line 97), and **before** the closing `</div>` of the surrounding `mb-3` wrapper (line 98), add:

```html
<div class="form-check">
    <input class="form-check-input" type="checkbox" id="spa_click_discovery"
           name="spa_click_discovery"
           {% if website.scraping_config.spa_click_discovery %}checked{% endif %}
           aria-describedby="spa-click-discovery-help">
    <label class="form-check-label" for="spa_click_discovery">
        {{ ftl('websites-spa-click-discovery-toggle') }}
    </label>
    <div id="spa-click-discovery-help" class="form-text">
        {{ ftl('websites-spa-click-discovery-help') }}
    </div>
</div>
```

- [ ] **Step 2: Add the route handler line**

In `auto_a11y/web/routes/websites.py`, after the existing `website.scraping_config.request_delay = ...` line (around line 198), add:

```python
website.scraping_config.spa_click_discovery = (
    request.form.get('spa_click_discovery') == 'on'
)
```

- [ ] **Step 3: Type-check**

```bash
.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check
```

- [ ] **Step 4: Manual smoke test**

```bash
.venv/bin/python run.py
```

In the browser:
1. Navigate to an existing website's edit page (`/websites/<id>/edit`).
2. Confirm the new checkbox appears under "Respect robots.txt" with the help text below it.
3. Check the box, click "Update Website".
4. Re-open the edit page. Confirm the checkbox is still checked.
5. Uncheck, save, reload — confirm it's unchecked.

If MongoDB isn't running or the UI fails to render, fix before continuing.

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/templates/websites/edit.html auto_a11y/web/routes/websites.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(websites): add click-discovery checkbox to website-edit form

Per-website opt-in for SPA click-based discovery, wired through to
ScrapingConfig.spa_click_discovery.
EOF
)"
```

---

### Task 5: Show the mode used on the discovery-run detail page

**Files:**
- Modify: `auto_a11y/web/templates/websites/discovery_run.html:186-191`

- [ ] **Step 1: Add the dt/dd pair**

Open `auto_a11y/web/templates/websites/discovery_run.html`. Inside the second `<dl class="row">` (the right column, starting at line 186), after the existing `respect_robots` dt/dd at lines 189–190 and **before** `</dl>` at line 191, add:

```html
<dt class="col-sm-4">{{ ftl('websites-spa-click-discovery') }}</dt>
<dd class="col-sm-8">{{ ftl('websites-yes') if discovery_run.spa_click_discovery else ftl('websites-no') }}</dd>
```

- [ ] **Step 2: Manual smoke test**

Trigger a discovery run, then open its detail page (linked from the website view's discovery history). Confirm the new row appears at the bottom of the parameters card showing "No" (since no run yet has the flag set — Task 9 will set it from the website config).

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/websites/discovery_run.html
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(websites): show click-discovery mode on discovery-run detail
EOF
)"
```

---

### Task 6: Show the slowness warning when starting discovery

**Files:**
- Modify: `auto_a11y/web/templates/websites/view.html:494-522` (the existing discoveryModal)

- [ ] **Step 1: Add the conditional alert inside the discovery modal**

Open `auto_a11y/web/templates/websites/view.html`. Inside the `discoveryForm` `<form>` (starting at line 501), inside `<div class="modal-body">`, after the existing `<div class="alert alert-info">…websites-discovery-will-crawl-your-website-starting-from…</div>` at lines 509-511, append:

```html
{% if website.scraping_config.spa_click_discovery %}
<div class="alert alert-info mt-2" role="status">
    <i class="bi bi-info-circle" aria-hidden="true"></i>
    {{ ftl('websites-spa-click-discovery-warning') }}
</div>
{% endif %}
```

The `mt-2` adds a small vertical gap from the existing alert.

- [ ] **Step 2: Manual smoke test**

1. Open a website's view page.
2. Click "Discover Pages" → the discovery modal opens.
3. If `spa_click_discovery` is off for that website: only the standard info alert shows.
4. Enable `spa_click_discovery` on the edit page, save, return to the view page, open the modal again: both alerts now show, the warning underneath the standard one.

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/templates/websites/view.html
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(websites): warn about slowness when starting click-based discovery

Shown in the discovery-start modal only when the website has
spa_click_discovery enabled.
EOF
)"
```

---

## Phase 2 — Crawler helper

The substance of the feature. Each task is a single TDD cycle that adds one capability to `_extract_links_via_clicking`. Tests use mocked `PlaywrightPage` and a stub `BrowserManager` per `tests/test_scraping_job_auto_fetch_pdfs.py` style.

### Task 7: Add module constants and helper skeleton

**Files:**
- Modify: `auto_a11y/core/scraper.py:1-55` (top of module)
- Modify: `auto_a11y/core/scraper.py` (insert helper method after `_extract_links` ends around line 998)

- [ ] **Step 1: Write a failing skeleton test**

Append to `tests/test_spa_click_discovery.py`:

```python
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from auto_a11y.core.scraper import (
    DESTRUCTIVE_ANCHOR_PATTERN,
    MAX_CLICK_CANDIDATES_PER_PAGE,
    CLICK_TIMEOUT_MS,
    POST_CLICK_SETTLE_MS,
    ScrapingEngine,
)
from auto_a11y.models.website import ScrapingConfig, Website


def _make_engine() -> ScrapingEngine:
    """ScrapingEngine with mocked database + browser manager."""
    db = MagicMock()
    engine = ScrapingEngine(database=db, browser_config={})
    engine.browser_manager = MagicMock()
    engine.browser_manager.goto = AsyncMock()
    return engine


def _make_website(*, spa_click: bool = True) -> Website:
    cfg = ScrapingConfig(spa_click_discovery=spa_click)
    w = Website(project_id="pid", url="https://example.com/", name="ex",
                scraping_config=cfg)
    w._id = "wid"  # type: ignore[assignment]  # NOT ALLOWED — see step 2
    return w
```

Wait — the helper above uses `# type: ignore`, which is forbidden. Drop the helper for now; we'll fix it before running anything. Instead, add this single test that exercises constants only (no engine construction yet):

```python
class TestSpaClickModuleConstants:
    def test_constants_exist_and_have_sane_values(self) -> None:
        assert MAX_CLICK_CANDIDATES_PER_PAGE == 50
        assert CLICK_TIMEOUT_MS == 5000
        assert POST_CLICK_SETTLE_MS == 1500

    def test_destructive_pattern_matches_common_actions(self) -> None:
        for word in ["Logout", "log out", "Sign Out", "sign out",
                     "Delete account", "Remove user", "Submit form",
                     "Unsubscribe"]:
            assert DESTRUCTIVE_ANCHOR_PATTERN.search(word) is not None

    def test_destructive_pattern_does_not_match_benign(self) -> None:
        for word in ["Logbook", "Sign in", "Login", "Item details",
                     "Home", "Dashboard"]:
            assert DESTRUCTIVE_ANCHOR_PATTERN.search(word) is None
```

Note: the regex `\b(log ?out|sign ?out|delete|remove|submit|unsubscribe)\b` will NOT match "Login" because `login` is not in the alternation. Good. It will match "log out" (with space) and "logout" (without). Good.

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py::TestSpaClickModuleConstants -v
```

Expected: `ImportError` — the constants don't exist yet.

- [ ] **Step 3: Add the constants to `scraper.py`**

In `auto_a11y/core/scraper.py`, after the existing `_EXPECTED_SKIP_REASON_PREFIXES` block (around line 42) and before `def _is_expected_skip`, add:

```python
# SPA click-discovery constants
MAX_CLICK_CANDIDATES_PER_PAGE = 50
CLICK_TIMEOUT_MS = 5000
POST_CLICK_SETTLE_MS = 1500
DESTRUCTIVE_ANCHOR_PATTERN = re.compile(
    r"\b(log ?out|sign ?out|delete|remove|submit|unsubscribe)\b",
    re.IGNORECASE,
)
```

(`re` is already imported at the top of the module.)

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py::TestSpaClickModuleConstants -v
```

Expected: 3 passes.

- [ ] **Step 5: Type-check**

```bash
.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check
```

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/core/scraper.py tests/test_spa_click_discovery.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(scraper): introduce SPA click-discovery module constants

Tuneables for the new _extract_links_via_clicking helper added in
follow-up commits.
EOF
)"
```

---

### Task 8: Implement candidate collection (no clicking yet)

**Files:**
- Modify: `auto_a11y/core/scraper.py` (add helper method)
- Modify: `tests/test_spa_click_discovery.py`

The helper at this stage:
1. Runs `page.evaluate` to collect anchor candidates.
2. Filters out anchors with usable href.
3. Filters out destructive anchors by regex.
4. Caps at `MAX_CLICK_CANDIDATES_PER_PAGE`.
5. Returns an empty set (no clicking yet).

This lets us test (1)–(4) in isolation before adding the click loop.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_spa_click_discovery.py`:

```python
def _engine_with_mocked_page(candidates: list[dict[str, object]]) -> tuple[
    ScrapingEngine, MagicMock
]:
    """Build an engine + mock PlaywrightPage that returns ``candidates`` from evaluate."""
    engine = _make_engine()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=candidates)
    page.click = AsyncMock()
    page.url = "https://example.com/parent"
    return engine, page


@pytest.mark.asyncio
async def test_candidate_collection_filters_anchors_with_usable_href() -> None:
    engine, page = _engine_with_mocked_page([
        {"hasUsableHref": True,  "text": "Home",      "ariaLabel": "", "selector": "/html/body/a[1]"},
        {"hasUsableHref": False, "text": "Dashboard", "ariaLabel": "", "selector": "/html/body/a[2]"},
    ])
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()  # noqa  -- see imports below

    # At this stage the helper returns an empty set (no click loop yet).
    # We assert that the evaluate JS was run exactly once.
    result = await engine._extract_links_via_clicking(
        page=page, current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == set()
    page.evaluate.assert_called_once()


@pytest.mark.asyncio
async def test_candidate_collection_filters_destructive_anchors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, page = _engine_with_mocked_page([
        {"hasUsableHref": False, "text": "Logout", "ariaLabel": "", "selector": "/html/body/a[1]"},
        {"hasUsableHref": False, "text": "Settings", "ariaLabel": "", "selector": "/html/body/a[2]"},
    ])
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()

    with caplog.at_level(logging.INFO, logger="auto_a11y.core.scraper"):
        await engine._extract_links_via_clicking(
            page=page, current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    # The destructive filter should log one info skip for "Logout".
    assert any("destructive" in r.message.lower() and "logout" in r.message.lower()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_candidate_collection_caps_at_max(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, page = _engine_with_mocked_page([
        {"hasUsableHref": False, "text": f"Link {i}", "ariaLabel": "",
         "selector": f"/html/body/a[{i}]"}
        for i in range(MAX_CLICK_CANDIDATES_PER_PAGE + 10)
    ])
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()

    with caplog.at_level(logging.INFO, logger="auto_a11y.core.scraper"):
        await engine._extract_links_via_clicking(
            page=page, current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    # The "Starting click-based discovery" info log line should report exactly the cap.
    starting_lines = [r.message for r in caplog.records
                      if "Starting click-based discovery" in r.message]
    assert len(starting_lines) == 1
    assert f"{MAX_CLICK_CANDIDATES_PER_PAGE} candidates" in starting_lines[0]
```

And add the `ObjectId` import at the top of the test file (it's already needed because `Website._id` is typed `ObjectId | None`):

```python
from bson import ObjectId
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py -v -k candidate
```

Expected: `AttributeError: ScrapingEngine has no attribute '_extract_links_via_clicking'`.

- [ ] **Step 3: Implement the skeleton helper**

In `auto_a11y/core/scraper.py`, immediately after the closing of `_extract_links` (the existing method ending around line 997), add:

```python
async def _extract_links_via_clicking(
    self,
    page: PlaywrightPage,
    current_url: str,
    website: Website,
    base_domain: str,
    base_path: str = "",
) -> set[str]:
    """
    Discover SPA links by clicking anchors with no usable href.

    For sites where navigation happens via JavaScript (href="#",
    href="javascript:..."), the standard href extraction misses real
    routes. This helper clicks each such anchor, reads the resulting
    page.url, and runs it through the same filter pipeline as the
    href path.

    Returns the set of newly discovered, in-scope URLs.
    """
    discovered: set[str] = set()
    try:
        raw_candidates = await page.evaluate(_CLICK_CANDIDATES_JS)
    except Exception as e:
        logger.warning(f"Failed to collect click candidates on {current_url}: {e}")
        return discovered

    # Coerce JS-returned values to typed Python objects.
    candidates: list[dict[str, str]] = []
    for c in raw_candidates if isinstance(raw_candidates, list) else []:
        if not isinstance(c, dict):
            continue
        if c.get("hasUsableHref"):
            continue
        text = str(c.get("text", "")).strip()
        aria = str(c.get("ariaLabel", "")).strip()
        selector = str(c.get("selector", ""))
        if not selector or (not text and not aria):
            continue
        # Destructive-anchor filter
        if DESTRUCTIVE_ANCHOR_PATTERN.search(f"{text} {aria}"):
            logger.info(
                f"Skipped destructive anchor '{text or aria}' at {current_url} "
                f"(matched filter)"
            )
            continue
        candidates.append({"text": text, "aria": aria, "selector": selector})

    # Effort cap
    if len(candidates) > MAX_CLICK_CANDIDATES_PER_PAGE:
        candidates = candidates[:MAX_CLICK_CANDIDATES_PER_PAGE]

    logger.info(
        f"Starting click-based discovery for {current_url}: "
        f"{len(candidates)} candidates after filtering"
    )

    # Click loop is added in subsequent tasks.
    return discovered
```

And at module top, near the constants from Task 7, add the JS source as a module-level constant:

```python
# Collects every <a> on the page and computes whether its href is
# usable (real navigation target) or not (#, empty, javascript:).
_CLICK_CANDIDATES_JS = """
() => {
  const here = window.location.href;
  function xpathOf(el) {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      let sib = cur, idx = 1;
      while ((sib = sib.previousElementSibling)) {
        if (sib.nodeName === cur.nodeName) idx++;
      }
      parts.unshift(cur.nodeName.toLowerCase() + '[' + idx + ']');
      cur = cur.parentElement;
    }
    return '/html/' + parts.join('/');
  }
  function isUsable(a) {
    const raw = a.getAttribute('href');
    if (raw === null || raw === '' || raw.startsWith('javascript:')) return false;
    // Anchors whose resolved href equals the current URL (typical of href="#")
    // are not directly navigable.
    return a.href !== here;
  }
  return Array.from(document.querySelectorAll('a')).map(a => ({
    hasUsableHref: isUsable(a),
    text: (a.textContent || '').trim(),
    ariaLabel: a.getAttribute('aria-label') || '',
    selector: xpathOf(a),
  }));
}
"""
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py -v
```

The new tests should pass. Existing tests should still pass.

- [ ] **Step 5: Type-check**

```bash
.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check
```

If `page.evaluate` returns `object` (since the JS return is unknown) and pyright complains about indexing a `dict[Any, Any]`, you'll need to narrow with `isinstance` checks (already done above). If issues remain, narrow further with explicit `if isinstance(c.get("hasUsableHref"), bool):` etc. **Do not** use `cast(Any, ...)` or `# type: ignore`.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/core/scraper.py tests/test_spa_click_discovery.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(scraper): collect SPA click candidates with destructive-text filter

_extract_links_via_clicking now collects anchors with no usable href,
discards destructive labels via a hardcoded regex, and caps the count.
Click loop comes in the next commit.
EOF
)"
```

---

### Task 9: Implement the click loop with re-navigation, URL capture, and post-click filtering

**Files:**
- Modify: `auto_a11y/core/scraper.py` (extend `_extract_links_via_clicking`)
- Modify: `tests/test_spa_click_discovery.py`

This is the meat. Implement the click loop, including:
- Re-navigate to `current_url` before each click.
- Locate the anchor by XPath; skip if not found.
- Strip `target` attribute via JS before clicking.
- Click with `CLICK_TIMEOUT_MS` timeout.
- Sleep `POST_CLICK_SETTLE_MS / 1000` seconds.
- Read `page.url`.
- If unchanged → skip + warning.
- Otherwise filter through `_normalize_url`, domain check, base_path check, excluded/allowed paths, document-extension skip — same filters as the existing `_extract_links` does for href-discovered URLs.
- Catch per-click exceptions and continue.

- [ ] **Step 1: Write failing tests covering all click-loop branches**

Append to `tests/test_spa_click_discovery.py`. The tests use a fake `page` whose `goto`-on-`browser_manager` is mocked and whose `url` attribute is updated programmatically to simulate SPA navigation:

```python
class _FakePage:
    """Minimal Playwright-like page for click-loop tests.

    Tracks navigation calls and lets the test script the URL each click ends at.
    """
    def __init__(self, candidates_payload: list[dict[str, object]]) -> None:
        self._candidates = candidates_payload
        self.url = "https://example.com/parent"
        self.click_calls: list[tuple[str, int]] = []
        self.eval_calls: list[str] = []
        # Sequence of URLs to return after each click; popped left-to-right.
        self.url_after_click: list[str] = []

    async def evaluate(self, source: str, *args: object) -> object:
        self.eval_calls.append(source)
        # First call: return the candidate list.
        if "querySelectorAll('a')" in source:
            return self._candidates
        # Subsequent calls: target-strip helper. Returns None.
        return None

    async def click(self, selector: str, timeout: int = 0) -> None:
        self.click_calls.append((selector, timeout))
        # Simulate the SPA navigation: update self.url to the next scripted URL.
        if self.url_after_click:
            self.url = self.url_after_click.pop(0)

    async def query_selector(self, selector: str) -> object:
        # Anchor located unless the test sets self.missing_selectors.
        if selector in getattr(self, "missing_selectors", set()):
            return None
        return MagicMock()


@pytest.mark.asyncio
async def test_click_loop_captures_url_after_pushstate() -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Dashboard",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    page.url_after_click = ["https://example.com/parent/dashboard"]
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()

    result = await engine._extract_links_via_clicking(
        page=page,  # type: ignore[arg-type]  -- see step 3 fix
        current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == {"https://example.com/parent/dashboard"}
    # Re-navigated to parent once (one click → one renavigation).
    assert engine.browser_manager.goto.await_count == 1
    assert page.click_calls and page.click_calls[0][0] == "/html/body/a[1]"
```

The `# type: ignore` above is a placeholder — strict typing forbids it. We'll fix this at step 3 by defining a `Protocol` for the page surface, or by using `cast` to a Playwright `Page` type with the relevant attributes typed. **Do not commit the `type: ignore`.**

Continue with more cases:

```python
@pytest.mark.asyncio
async def test_click_loop_skips_when_url_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Open menu",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    # No URL change after click: page.url stays at parent.
    page.url_after_click = ["https://example.com/parent"]
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()

    with caplog.at_level(logging.WARNING, logger="auto_a11y.core.scraper"):
        result = await engine._extract_links_via_clicking(
            page=page,
            current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    assert result == set()
    assert any("no URL change" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_click_loop_skips_anchor_not_found_after_renavigation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Phantom",
         "ariaLabel": "", "selector": "/html/body/a[99]"},
    ])
    page.missing_selectors = {"/html/body/a[99]"}  # type: ignore[attr-defined]
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()

    with caplog.at_level(logging.WARNING, logger="auto_a11y.core.scraper"):
        result = await engine._extract_links_via_clicking(
            page=page,
            current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    assert result == set()
    assert any("could not locate anchor" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_click_loop_excludes_off_domain_post_click_urls() -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "External",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    page.url_after_click = ["https://evil.example.org/something"]
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(
                          spa_click_discovery=True, follow_external=False))
    website._id = ObjectId()

    result = await engine._extract_links_via_clicking(
        page=page,
        current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == set()  # Off-domain URL filtered out.


@pytest.mark.asyncio
async def test_click_loop_respects_excluded_paths() -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Admin panel",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    page.url_after_click = ["https://example.com/admin/dashboard"]
    cfg = ScrapingConfig(spa_click_discovery=True,
                         excluded_paths=["/admin"])
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=cfg)
    website._id = ObjectId()

    result = await engine._extract_links_via_clicking(
        page=page,
        current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == set()


@pytest.mark.asyncio
async def test_click_loop_strips_target_blank_before_click() -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Pop",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
    ])
    page.url_after_click = ["https://example.com/popped"]
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()

    result = await engine._extract_links_via_clicking(
        page=page,
        current_url="https://example.com/parent",
        website=website, base_domain="example.com", base_path="",
    )
    assert result == {"https://example.com/popped"}
    # Confirm a target-stripping evaluate call ran *before* the click.
    target_strip_calls = [s for s in page.eval_calls
                          if "removeAttribute" in s and "target" in s]
    assert target_strip_calls, "Expected a target-strip JS call before clicking"


@pytest.mark.asyncio
async def test_click_loop_continues_after_per_click_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _make_engine()
    page = _FakePage([
        {"hasUsableHref": False, "text": "Breaks",
         "ariaLabel": "", "selector": "/html/body/a[1]"},
        {"hasUsableHref": False, "text": "Works",
         "ariaLabel": "", "selector": "/html/body/a[2]"},
    ])
    # Make the first click raise; second click navigates normally.
    original_click = page.click
    async def click_side_effect(selector: str, timeout: int = 0) -> None:
        if selector == "/html/body/a[1]":
            raise RuntimeError("simulated click failure")
        await original_click(selector, timeout)
    page.click = click_side_effect  # type: ignore[assignment]
    page.url_after_click = ["https://example.com/works"]
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()

    with caplog.at_level(logging.WARNING, logger="auto_a11y.core.scraper"):
        result = await engine._extract_links_via_clicking(
            page=page,
            current_url="https://example.com/parent",
            website=website, base_domain="example.com", base_path="",
        )
    assert result == {"https://example.com/works"}
    assert any("simulated click failure" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run to verify failures**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py -v
```

Expected: the new click-loop tests fail (return value is empty set, no goto called, no target-strip eval).

- [ ] **Step 3: Implement the click loop**

In `auto_a11y/core/scraper.py`, expand the `_extract_links_via_clicking` body so the section currently labelled "Click loop is added in subsequent tasks." becomes the real loop. Replace that placeholder with:

```python
    stealth_mode = self.browser_manager.config.get("stealth_mode", False)
    wait_until = "networkidle" if stealth_mode else "domcontentloaded"
    nav_timeout = 40000 if stealth_mode else 20000

    for c in candidates:
        selector = c["selector"]
        text = c["text"] or c["aria"]
        try:
            # 1) Restore parent state before each click.
            await self.browser_manager.goto(
                page=page, url=current_url,
                wait_until=wait_until, timeout=nav_timeout,
            )

            # 2) Locate the anchor; skip if the DOM has changed.
            element = await page.query_selector(selector)
            if element is None:
                logger.warning(
                    f"Could not locate anchor at {selector} after "
                    f"re-navigating to {current_url}; skipping"
                )
                continue

            # 3) Strip target so the click navigates in-place.
            await page.evaluate(
                """(sel) => {
                    const el = document.evaluate(sel, document, null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                    if (el) el.removeAttribute('target');
                }""",
                selector,
            )

            # 4) Click and settle.
            await page.click(selector, timeout=CLICK_TIMEOUT_MS)
            await asyncio.sleep(POST_CLICK_SETTLE_MS / 1000)

            final_url = page.url
            if not isinstance(final_url, str):
                continue

            if final_url == current_url:
                logger.warning(
                    f"Click on '{text}' at {current_url} produced no URL "
                    f"change — SPA may not be URL-routed"
                )
                continue

            # 5) Run through the same filters as the href path.
            filtered = self._filter_post_click_url(
                final_url, current_url, website, base_domain, base_path,
            )
            if filtered is not None:
                discovered.add(filtered)
        except Exception as e:
            logger.warning(
                f"Error clicking anchor '{text}' on {current_url}: {e}"
            )
            continue

    return discovered
```

Add the URL-filter helper as a new sibling method (after `_extract_links_via_clicking`). It encapsulates the same filtering logic the href path uses, returning the normalized URL or None:

```python
def _filter_post_click_url(
    self,
    url: str,
    current_url: str,
    website: Website,
    base_domain: str,
    base_path: str,
) -> str | None:
    """Apply the standard scope/excluded/allowed filters to a click-discovered URL.

    Returns the normalized URL if it should be queued, else None.
    """
    normalized = self._normalize_url(url, current_url)
    if not normalized:
        return None
    parsed = urlparse(normalized)

    if not website.scraping_config.follow_external:
        if parsed.netloc != base_domain:
            if not (website.scraping_config.include_subdomains
                    and parsed.netloc.endswith(f".{base_domain}")):
                return None
        if base_path:
            if (not parsed.path.startswith(base_path + "/")
                    and parsed.path != base_path):
                return None

    path = parsed.path
    # Skip non-HTML resources the existing extraction also skips.
    if path.endswith((
        ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
        ".jpg", ".jpeg", ".png", ".gif", ".exe", ".dmg", ".mp4", ".mp3",
    )):
        return None
    if website.scraping_config.excluded_paths:
        if any(path.startswith(p)
               for p in website.scraping_config.excluded_paths):
            return None
    if website.scraping_config.allowed_paths:
        if not any(path.startswith(p)
                   for p in website.scraping_config.allowed_paths):
            return None
    return normalized
```

For the typing issue with the test's `_FakePage`: rather than allow `# type: ignore` in tests, define the helper signature using a `Protocol` so `_FakePage` satisfies it structurally. Add at the top of `scraper.py` (near other imports):

```python
from typing import Protocol


class _ClickablePage(Protocol):
    """Subset of the Playwright page API used by click-discovery."""
    url: str
    async def evaluate(self, source: str, *args: object) -> object: ...
    async def click(self, selector: str, timeout: int = ...) -> None: ...
    async def query_selector(self, selector: str) -> object: ...
```

Then change the helper signature so `page` is typed as `_ClickablePage` (covariant fit: real `PlaywrightPage` satisfies this protocol). Update the call site in `_extract_links` accordingly when wiring it in Task 11.

Remove the `# type: ignore[arg-type]` comments from the tests once the protocol is in place.

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py -v
```

All click-loop tests should pass.

- [ ] **Step 5: Type-check**

```bash
.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check
```

There must be **zero** `# type: ignore` in the new code. If pyright complains about `page.evaluate` returning `object`, narrow with `isinstance` checks instead of casts.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/core/scraper.py tests/test_spa_click_discovery.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(scraper): implement SPA click-discovery click loop

For each candidate anchor: re-navigate to parent, locate by XPath,
strip target, click, settle, capture page.url, filter through the
same scope/excluded/allowed rules as the href path. Per-click
failures are isolated.

Tests cover: happy path, no-URL-change skip, anchor-not-found,
off-domain filter, excluded_paths filter, target stripping, and
exception isolation.
EOF
)"
```

---

### Task 10: Wire the helper into `_extract_links`

**Files:**
- Modify: `auto_a11y/core/scraper.py:854-997` (the existing `_extract_links`)
- Modify: `tests/test_spa_click_discovery.py`

The flag-off path must continue to behave exactly as today. When the flag is on, the helper is called and its return union'd into `valid_links`.

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_spa_click_discovery.py`:

```python
@pytest.mark.asyncio
async def test_extract_links_does_not_call_helper_when_flag_off() -> None:
    engine = _make_engine()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[])  # No href anchors.
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=False))
    website._id = ObjectId()

    # Spy on the helper.
    engine._extract_links_via_clicking = AsyncMock(return_value={"unused"})  # type: ignore[method-assign]

    result = await engine._extract_links(
        page=page, current_url="https://example.com/",
        website=website, base_domain="example.com", base_path="",
    )
    engine._extract_links_via_clicking.assert_not_called()
    assert result == set()


@pytest.mark.asyncio
async def test_extract_links_unions_helper_result_when_flag_on() -> None:
    engine = _make_engine()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[])  # No href anchors.
    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()

    engine._extract_links_via_clicking = AsyncMock(  # type: ignore[method-assign]
        return_value={"https://example.com/spa-route"}
    )

    result = await engine._extract_links(
        page=page, current_url="https://example.com/",
        website=website, base_domain="example.com", base_path="",
    )
    engine._extract_links_via_clicking.assert_awaited_once()
    assert "https://example.com/spa-route" in result
```

The `# type: ignore[method-assign]` on these two lines is acceptable **only** if the strict-typing tools accept that pattern for test-time monkey-patching of methods. If they don't, replace with `setattr(engine, "_extract_links_via_clicking", AsyncMock(...))`. **Verify before committing** that no `type: ignore` remains.

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py::test_extract_links_unions_helper_result_when_flag_on -v
```

Expected: failure — helper isn't called yet.

- [ ] **Step 3: Wire the helper**

In `auto_a11y/core/scraper.py`, near the end of `_extract_links` — immediately before `return valid_links` (around line 993) — add:

```python
            if website.scraping_config.spa_click_discovery:
                try:
                    click_links = await self._extract_links_via_clicking(
                        page=page,
                        current_url=current_url,
                        website=website,
                        base_domain=base_domain,
                        base_path=base_path,
                    )
                    valid_links.update(click_links)
                except Exception as e:
                    logger.warning(
                        f"Click-based discovery failed for {current_url}: {e}"
                    )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py -v
```

- [ ] **Step 5: Type-check**

```bash
.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check
```

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/core/scraper.py tests/test_spa_click_discovery.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(scraper): wire click-discovery helper into _extract_links

When website.scraping_config.spa_click_discovery is True, the helper
is invoked and its results are unioned into the link set. Helper
exceptions are caught and logged — never abort link extraction.
EOF
)"
```

---

### Task 11: Populate `DiscoveryRun.spa_click_discovery` from the website config

**Files:**
- Modify: `auto_a11y/core/scraper.py:100-110` (the `DiscoveryRun(...)` construction in `discover_website`)
- Modify: `tests/test_spa_click_discovery.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_spa_click_discovery.py`:

```python
@pytest.mark.asyncio
async def test_discover_website_records_click_discovery_mode_on_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the DiscoveryRun created at the start carries the flag through."""
    from auto_a11y.models.discovery_run import DiscoveryRun

    captured: list[DiscoveryRun] = []
    engine = _make_engine()

    def fake_create_discovery_run(run: DiscoveryRun) -> str:
        captured.append(run)
        return "run-1"

    engine.db.create_discovery_run = fake_create_discovery_run  # type: ignore[attr-defined]
    engine.db.get_discovery_runs = MagicMock(return_value=[])  # type: ignore[attr-defined]
    engine.db.mark_pages_not_in_latest_discovery = MagicMock()  # type: ignore[attr-defined]

    # Cut the loop short by raising in browser startup so we only test the
    # DiscoveryRun creation block.
    engine.browser_manager.ensure_running = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("stop here")
    )

    website = Website(project_id="p", url="https://example.com/",
                      scraping_config=ScrapingConfig(spa_click_discovery=True))
    website._id = ObjectId()

    with pytest.raises(Exception):
        await engine.discover_website(website=website)

    assert captured, "DiscoveryRun should have been created before browser startup"
    assert captured[0].spa_click_discovery is True
```

The placement of `mark_pages_not_in_latest_discovery` vs `ensure_running` in the existing `discover_website` matters — verify the source order so the test triggers the right early-exit. Adjust the side-effect target if needed (e.g. `engine.db.mark_pages_not_in_latest_discovery` if that's called first).

If the `type: ignore` comments are required for mocking `db` attributes, replace with `setattr(engine.db, "create_discovery_run", ...)` etc. **No `type: ignore` in committed code.**

- [ ] **Step 2: Run to verify failure**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py::test_discover_website_records_click_discovery_mode_on_run -v
```

Expected: assertion failure — `captured[0].spa_click_discovery is False` (default).

- [ ] **Step 3: Pass the flag through in `discover_website`**

In `auto_a11y/core/scraper.py`, in the `DiscoveryRun(...)` construction in `discover_website` (around lines 100–110), add one new kwarg:

```python
discovery_run = DiscoveryRun(
    website_id=website_id,
    started_at=datetime.now(),
    status=DiscoveryStatus.RUNNING,
    max_pages=website.scraping_config.max_pages,
    max_depth=website.scraping_config.max_depth,
    follow_external=website.scraping_config.follow_external,
    respect_robots=website.scraping_config.respect_robots,
    spa_click_discovery=website.scraping_config.spa_click_discovery,
    triggered_by=job.user_id if job and hasattr(job, 'user_id') else 'manual',
    job_id=job.job_id if job and hasattr(job, 'job_id') else None
)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_spa_click_discovery.py -v
```

- [ ] **Step 5: Type-check**

```bash
.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check
```

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/core/scraper.py tests/test_spa_click_discovery.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(scraper): persist click-discovery mode on DiscoveryRun

The DiscoveryRun created at the start of discovery now carries the
flag from the website's ScrapingConfig, so the discovery-run detail
page can show whether each historical run used click mode.
EOF
)"
```

---

## Phase 3 — Verification

### Task 12: Full-stack manual verification

**Files:** None (manual smoke test only).

This is the integration check. Without a real SPA fixture, we verify by configuring discovery against a known site that has `href="#"` anchors and observing logs.

- [ ] **Step 1: Bring up the app**

```bash
.venv/bin/python run.py --debug
```

- [ ] **Step 2: Configure a test website**

In the browser:
1. Create a new website pointing at any SPA the team has access to (e.g. a Vue/React demo site) — or a static page where you've manually added an `<a href="#" onclick="history.pushState({}, '', '/spa-route'); event.preventDefault()">Demo</a>`.
2. Enable "Click-based discovery (for single-page apps)" on the edit page. Save.
3. Open the website view page. Click "Discover Pages".
4. Confirm both info alerts show in the modal.
5. Start discovery.

- [ ] **Step 3: Watch logs**

In the terminal running `run.py`, look for:

```
INFO:auto_a11y.core.scraper:Starting click-based discovery for https://...: N candidates after filtering
```

If the site has destructive anchor text, also:

```
INFO:auto_a11y.core.scraper:Skipped destructive anchor 'Logout' at https://... (matched filter)
```

If clicks produce no URL change:

```
WARNING:auto_a11y.core.scraper:Click on '...' at https://... produced no URL change — SPA may not be URL-routed
```

If clicks produce a URL change, the new URL should appear in the discovered-pages list once discovery completes.

- [ ] **Step 4: Verify the discovery-run detail page**

After the run completes, open the discovery-run detail page from the website's history. The parameters card should show:

```
Click-based discovery:   Yes
```

- [ ] **Step 5: Verify the OFF path is unchanged**

Disable the checkbox on the edit page. Re-run discovery. Confirm:
- No "Starting click-based discovery" log line appears.
- The detail page now shows "Click-based discovery: No".
- Pages discovered match what href-only discovery would find.

- [ ] **Step 6: Record findings in plan log (optional)**

If issues are found, file them as follow-up items; do not amend committed code via `--amend`.

---

### Task 13: Final clean-up — README mention

**Files:**
- Modify: `README.md` (only if discovery configuration is documented there)
- Modify: `README.fr.md` (same; must stay in sync)

- [ ] **Step 1: Check whether discovery config is documented**

```bash
grep -n -i "scraping\|discovery\|spa\|max_pages\|follow_external" README.md | head -20
```

- [ ] **Step 2: If documented, add one-paragraph note**

If the README enumerates the scraping-config options, add a paragraph for `spa_click_discovery`. **Update `README.fr.md` in the same commit** — CLAUDE.md mandates they stay in sync. If the README only references the UI, no change is needed.

- [ ] **Step 3: Commit (if changes made)**

```bash
git add README.md README.fr.md
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
docs(readme): document SPA click-based discovery option
EOF
)"
```

---

## Final acceptance gate

Before declaring the feature done:

- [ ] `pytest tests/test_spa_click_discovery.py -v` — all tests pass.
- [ ] `pytest tests/ -v` — full test suite passes (no regressions in any other test file).
- [ ] `.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check` — all three pass with zero suppressions.
- [ ] `python tests/validate_translations.py` — passes.
- [ ] Manual smoke test from Task 12 confirms end-to-end flow.
- [ ] No `# type: ignore`, no `Any`, no `cast(Any, ...)`, no `# pyright: ignore`, no `# ty: ignore` anywhere in the new code.
- [ ] All commits authored normally (no `--amend`, no history rewrite).

## Out of scope (do NOT add)

- Configurable destructive-text regex
- Clicking non-anchor elements
- Per-discovery-run override of the toggle
- Synthetic URL recording for no-URL-change clicks
- Mid-run abort heuristics based on click rate
