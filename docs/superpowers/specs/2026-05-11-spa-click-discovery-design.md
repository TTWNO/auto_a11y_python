# SPA Click-Based Discovery — Design

**Status:** Draft
**Date:** 2026-05-11
**Author:** Tait Hoyem (with Claude)

## Problem

Discovery currently extracts links from a page by running
`document.querySelectorAll('a[href]')` and reading each anchor's `href`
attribute. Anchors whose `href` is `#`, empty, or starts with `javascript:`
are dropped (`auto_a11y/core/scraper.py:880-896`).

This breaks discovery on single-page applications (SPAs). In a typical SPA,
`<a>` tags often look like:

```html
<a href="#" onclick="router.navigate('/dashboard')">Dashboard</a>
<a href="javascript:void(0)" data-route="/settings">Settings</a>
```

The href is meaningless; the real navigation happens via JavaScript, often
ending in a `pushState` or `replaceState` call that updates `window.location`
without a server round-trip. Today, discovery sees `href="#"`, drops the
anchor, and never finds those SPA routes.

## Goal

Add a per-website option that allows discovery to find SPA routes by
actually clicking anchors whose `href` is not directly usable, then reading
the resulting `window.location` after JavaScript has had a chance to navigate.
External links and out-of-scope redirects are filtered after the click using
the existing rules.

## Non-Goals

- Clicking non-anchor elements (`<button>`, `<div role="link">`, generic
  `onclick` handlers). High risk of triggering destructive actions; deferred.
- Recording "page-like" states where clicking an anchor changes the DOM but
  not the URL. Without a URL, the page can't be re-loaded or re-tested, so
  recording it adds noise without value. The user is warned (via a log
  entry) when this happens.
- A per-discovery-run override of the toggle. The setting is per-website
  only; if the user wants to disable it for a single run, they can edit the
  website first.
- User-configurable destructive-text patterns. A hardcoded English regex
  covers the common cases (`logout`, `sign out`, `delete`, `remove`,
  `submit`, `unsubscribe`); we'll add a knob if it turns out to be needed.
- Changes to fixture testing, accessibility tests, AI analysis, or reports.
  None of them care how a URL was discovered.

## Design Overview

A single new boolean lives on `ScrapingConfig`. When enabled, the link
extraction path in `_extract_links` is extended: alongside the normal
href-based extraction, anchors that have no usable href are collected,
filtered against a destructive-text regex, and then clicked one-by-one
inside a helper. Each click is bracketed by a re-navigation to the parent
URL so the page state is fresh; after the click, `page.url` is read and
fed through the existing URL filters.

All other discovery behavior — depth counting, screenshots, browser
restart, authentication, robots.txt — is unchanged. The toggle is purely
an extension of how `_extract_links` produces candidate URLs.

## Data Model Changes

### `ScrapingConfig` (`auto_a11y/models/website.py`)

Add one field:

```python
@dataclass
class ScrapingConfig:
    # ... existing fields ...
    spa_click_discovery: bool = False
```

Default is `False` to preserve current behavior for all existing websites.
Update `to_dict` and `from_dict` accordingly (the existing
`from_dict` already ignores unknown keys, so no DB migration is required).

### `DiscoveryRun` (`auto_a11y/models/discovery_run.py`)

Add the same field so historical runs record the mode they used:

```python
@dataclass
class DiscoveryRun:
    # ... existing fields ...
    spa_click_discovery: bool = False
```

Update `to_dict` and `from_dict`. When `ScrapingEngine.discover_website`
creates a `DiscoveryRun`, populate this from
`website.scraping_config.spa_click_discovery`.

### No schema migration needed

Both models' `from_dict` tolerate missing keys (defaulting to `False`).
Note that `ScrapingConfig.from_dict` filters unknown keys via the
`fields(cls)` set, while `DiscoveryRun.from_dict` uses explicit per-field
`data.get(...)` calls — both styles handle the new field cleanly with a
single-line addition, but the two are not interchangeable. Existing
MongoDB documents work as-is.

## Crawl Mechanics

### Entry point — `_extract_links` (`auto_a11y/core/scraper.py:854`)

Today this method runs a single `page.evaluate` that returns
`[{href, text}, ...]`. New flow:

1. Run the existing extraction. Filter and normalize as today. Build
   `valid_links: set[str]`.
2. If `website.scraping_config.spa_click_discovery is True`, call a new
   helper `_extract_links_via_clicking(page, current_url, website,
   base_domain, base_path)` and union its result into `valid_links`.
3. Return `valid_links` as before.

The helper is only called when the flag is on; the existing path is
untouched when the flag is off, so non-SPA crawls see no behavior change.

### New helper — `_extract_links_via_clicking`

Signature:

```python
async def _extract_links_via_clicking(
    self,
    page: PlaywrightPage,
    current_url: str,
    website: Website,
    base_domain: str,
    base_path: str = ""
) -> set[str]:
```

Flow:

1. **Collect click candidates.** Run a single `page.evaluate` that
   returns, for every `<a>` element on the page:
   ```js
   {
     hasUsableHref: boolean,   // false when href resolves to current
                               // URL, is empty, or starts with "javascript:"
     text: string,             // textContent.trim()
     ariaLabel: string,        // getAttribute("aria-label") || ""
     selector: string          // a stable selector — see below
   }
   ```
   Filter to candidates where `hasUsableHref === false`. Discard candidates
   with no visible text and no aria-label — they're unlikely to be real
   navigation targets and hard to identify safely.

2. **Filter destructive anchors.** For each candidate, lowercase
   `(text + " " + ariaLabel)` and discard if it matches the hardcoded
   regex (`\b(log ?out|sign ?out|delete|remove|submit|unsubscribe)\b`).
   Log each skip at info level with the matched text.

3. **Cap effort.** Truncate the candidate list to at most
   `MAX_CLICK_CANDIDATES_PER_PAGE = 50` (module constant) to prevent
   runaway crawls on heavily-templated pages.

4. **Click loop.** For each candidate `c`:
   - Re-navigate the same `page` to `current_url` to restore state. Use
     the same `wait_until` and timeout settings as `_discover_page`
     already does for the active stealth mode.
   - Locate the anchor via the captured selector. If not found (DOM
     differs from when we extracted), log a warning and skip.
   - Strip the `target` attribute via JS (`el.removeAttribute('target')`)
     so the click navigates in-place, not into a new tab.
   - Click with `page.click(selector, timeout=CLICK_TIMEOUT_MS=5000)`.
   - Wait briefly for navigation/route change: `await
     asyncio.sleep(POST_CLICK_SETTLE_MS / 1000)` (default 1500ms). A
     proper `wait_for_url`/`wait_for_navigation` would be racy on SPAs
     that use `pushState` without firing standard events; a fixed settle
     is simpler and adequate.
   - Read `final_url = page.url`.
   - If `final_url == current_url`: log a warning ("Click on '<text>'
     produced no URL change — SPA may not be URL-routed") and skip.
   - Otherwise, run `final_url` through the existing filters: same
     filtering block as the href path (normalization, `follow_external`,
     `include_subdomains`, `base_path`, `excluded_paths`, `allowed_paths`,
     document-extension detection). Add to the result set if it passes.

5. **Return** the accumulated set of click-discovered URLs.

6. **Failure isolation.** Wrap the body in `try/except`. Any exception in
   the click loop is logged and the partial result is returned — never
   raise into the caller. Anchor-level failures are also caught
   individually and logged.

### Selector capture

To click an anchor after re-navigating to its parent, we need a stable
locator. In the candidate-collection `page.evaluate`, compute an XPath
for each anchor (the codebase already has XPath utilities for tests
elsewhere). Path format: ordinal-indexed XPath from `<html>`, e.g.
`/html/body/nav[1]/ul/li[3]/a[1]`. This survives re-navigation as long
as the SPA renders the same DOM, which it should when we navigate back
to the same URL.

If the anchor cannot be located by the captured XPath after
re-navigation, skip with a warning. We don't attempt to recover by
re-extracting — the page evidently differs, and trying again would just
loop.

### Robots.txt

URLs discovered by clicking are added to the existing `queued_urls`
set, same as href-discovered URLs. The robots.txt check in the main
discovery loop (`scraper.py:356`) applies uniformly — no bypass.

### Depth interaction

The click loop runs inside `_extract_links`, which is called from
`_discover_page` for the current page. URLs it returns are treated
identically to href-discovered URLs — added at `depth+1`. So depth
limits still apply.

### Authenticated mode

The destructive-text regex provides the only built-in guardrail for
authenticated crawls. The implementation plan should call out, in user
docs, that authenticated SPAs should configure `excluded_paths` if they
have non-English logout flows or other destructive routes the regex
misses.

## UI Changes

### Website edit form — `auto_a11y/web/templates/websites/edit.html`

Inside the existing `<fieldset>` for scraping config (after the
`respect_robots` checkbox at line 97), add:

```html
<div class="form-check">
    <input class="form-check-input" type="checkbox" id="spa_click_discovery"
           name="spa_click_discovery"
           {% if website.scraping_config.spa_click_discovery %}checked{% endif %}>
    <label class="form-check-label" for="spa_click_discovery">
        {{ ftl('websites-spa-click-discovery') }}
    </label>
    <div class="form-text">{{ ftl('websites-spa-click-discovery-help') }}</div>
</div>
```

### Route handler — `auto_a11y/web/routes/websites.py:193-198`

Add one line:

```python
website.scraping_config.spa_click_discovery = (
    request.form.get('spa_click_discovery') == 'on'
)
```

### Discovery-run detail page — `auto_a11y/web/templates/websites/discovery_run.html`

In the existing config `<dl>` block (around lines 180–190), add one row
showing whether the run used click-based discovery:

```html
<dt class="col-sm-4">{{ ftl('websites-spa-click-discovery-2') }}</dt>
<dd class="col-sm-8">
    {{ ftl('websites-yes') if discovery_run.spa_click_discovery
       else ftl('websites-no') }}
</dd>
```

### Discovery-start warning — `auto_a11y/web/templates/websites/view.html`

In the discovery-start modal/section (around line 598+), when
`website.scraping_config.spa_click_discovery` is true, render a one-line
`alert alert-info` notice above the start button:

```html
{% if website.scraping_config.spa_click_discovery %}
<div class="alert alert-info" role="status">
    {{ ftl('websites-spa-click-discovery-warning') }}
</div>
{% endif %}
```

The `alert-info` class is a project-custom utility (defined in
`auto_a11y/web/static/public/css/style.css`) and is the correct,
CLAUDE.md-compliant choice here. Do not substitute Bootstrap colour
variants such as `alert-warning` or `alert-primary` — the Colour System
rule in CLAUDE.md prohibits them.

### Translations — `auto_a11y/web/translations/{en,fr}/websites.ftl`

Add four new message IDs:

| ID | English | French |
|----|---------|--------|
| `websites-spa-click-discovery` | Click-based discovery (for single-page apps) | Découverte par clic (pour applications monopage) |
| `websites-spa-click-discovery-help` | Discovers SPA pages by clicking links instead of reading their href. Required when an SPA uses href="#" with JavaScript routing. Significantly slower — full discovery may take many times longer than normal. | Découvre les pages d'une application monopage en cliquant sur les liens au lieu de lire leur attribut href. Nécessaire lorsqu'une application monopage utilise href="#" avec un routage JavaScript. Beaucoup plus lent — une découverte complète peut prendre plusieurs fois plus de temps que la normale. |
| `websites-spa-click-discovery-2` | Click-based discovery | Découverte par clic |
| `websites-spa-click-discovery-warning` | This website uses click-based discovery. Expect runs to take considerably longer than normal. | Ce site utilise la découverte par clic. Prévoyez des durées d'exécution sensiblement plus longues que la normale. |

The `-2` suffix on the discovery-run label follows the project's
existing convention for shorter "display" variants of the same concept
(see `websites-max-pages-2`, `websites-max-depth-2`).

## Logging

Three new log lines emitted from `_extract_links_via_clicking`:

| Level | Message | Purpose |
|-------|---------|---------|
| `info` | `Starting click-based discovery for {url}: {n} candidates after filtering` | Run visibility |
| `warning` | `Click on '{text}' at {url} produced no URL change — SPA may not be URL-routed` | Diagnostic for SPAs the feature can't help |
| `info` | `Skipped destructive anchor '{text}' at {url} (matched filter)` | Audit trail for the safety regex |

Plus the existing failure-isolation log lines (anchor not found, click
timeout, navigation error) at `warning` level.

## Module Constants

Define in `scraper.py` near the top of the module:

```python
# SPA click-discovery
MAX_CLICK_CANDIDATES_PER_PAGE = 50
CLICK_TIMEOUT_MS = 5000
POST_CLICK_SETTLE_MS = 1500
DESTRUCTIVE_ANCHOR_PATTERN = re.compile(
    r"\b(log ?out|sign ?out|delete|remove|submit|unsubscribe)\b",
    re.IGNORECASE,
)
```

## Testing

There is no existing test harness for `ScrapingEngine`; the fixture
system is for accessibility-test correctness, not crawl logic. Tests
for this feature live as new async tests under `tests/`, run against a
local static HTML fixture served by an in-process HTTP server (e.g.
`aiohttp.web` test fixture) plus a minimal JS shim that does
`history.pushState` on click.

Required test cases:

1. **Off by default** — `ScrapingConfig.spa_click_discovery == False` on
   a fresh `ScrapingConfig()`.
2. **Flag off, SPA-style anchors → not discovered** — confirms zero
   regression in current behavior.
3. **Flag on, href="#" with pushState → URL captured** — primary happy
   path.
4. **Flag on, click produces no URL change → URL skipped, warning
   logged** — confirms the "skip + log" rule.
5. **Flag on, anchor text matches destructive regex → skipped, info
   logged** — confirms safety filter.
6. **Flag on, anchor `target="_blank"` → still captured in same tab** —
   confirms target-stripping.
7. **Flag on, `MAX_CLICK_CANDIDATES_PER_PAGE` candidates → only N
   processed** — confirms cap.
8. **Flag on, post-click URL matches `excluded_paths` → not added** —
   confirms post-click filtering pipeline.

Type checking applies to all new code: must pass `mypy`, `pyright`, and
`ty` in strict mode. No `# type: ignore`, no `Any`, no `cast(Any, ...)`.

## Migration & Rollout

- No database migration needed; `from_dict` tolerates missing keys.
- Default value `False` preserves existing crawl behavior for all
  current websites.
- Feature is opt-in per website. Users enable it by checking a box on
  the website edit page.
- No feature flag or staged rollout required — the off-by-default
  config means it has zero impact until explicitly turned on.

## Risks

| Risk | Mitigation |
|------|------------|
| Click triggers destructive action despite filter (non-English label, custom phrasing) | Documented in help text: users should configure `excluded_paths` for authenticated crawls. Filter is hardcoded for now; can become configurable if it bites. |
| SPA changes DOM after first navigation (e.g. lazy-loaded nav) so XPath becomes stale | Skip with warning; don't retry. Acceptable: missing some links is better than infinite loops or wrong clicks. |
| Click opens a modal that blocks further navigation | Re-navigation to parent URL at the start of each iteration restores state, escaping the modal. |
| Per-page click count is large and crawl becomes intractable | `MAX_CLICK_CANDIDATES_PER_PAGE` cap. Existing `max_pages`, `max_depth`, and discovery timeout (2 hours) still apply. |
| User doesn't realize click mode is slow and aborts mid-run thinking it hung | UI warnings on the edit form and discovery-start screen explicitly call out the slowness. |

## File-Level Summary

| File | Change |
|------|--------|
| `auto_a11y/models/website.py` | Add `spa_click_discovery: bool = False` to `ScrapingConfig`; update `to_dict`/`from_dict` |
| `auto_a11y/models/discovery_run.py` | Add `spa_click_discovery: bool = False`; update `to_dict`/`from_dict` |
| `auto_a11y/core/scraper.py` | New module constants; new `_extract_links_via_clicking` helper; minor change in `_extract_links` to call the helper; populate `DiscoveryRun.spa_click_discovery` in `discover_website` |
| `auto_a11y/web/routes/websites.py` | One line in the edit-handler to map form checkbox to config |
| `auto_a11y/web/templates/websites/edit.html` | One new checkbox + help text |
| `auto_a11y/web/templates/websites/view.html` | Conditional info banner on discovery-start UI |
| `auto_a11y/web/templates/websites/discovery_run.html` | One new `<dt>`/`<dd>` row showing the mode used |
| `auto_a11y/web/translations/en/websites.ftl` | Four new message IDs |
| `auto_a11y/web/translations/fr/websites.ftl` | Four new message IDs (translated) |
| `tests/test_spa_click_discovery.py` (new) | Async tests covering the eight cases above |
