# NEXT.md Fixes Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three issues tracked in NEXT.md: a `node.matches` TypeError in test_lists.py, missing global rate limiting for test workers, and corrupted/missing French translations.

**Architecture:** Three independent fixes — a JS guard clause, an asyncio-based per-domain throttle shared across workers, and .po file corrections with recompilation.

**Tech Stack:** Python (asyncio), JavaScript (browser-executed), GNU gettext (.po/.mo)

---

## Task 1: Fix `node.matches is not a function` TypeError

**Files:**
- Modify: `auto_a11y/testing/touchpoint_tests/test_lists.py:233`

**Root cause:** `Array.from(parent.childNodes)` yields all node types (text, comment, processing instruction). The `.filter()` calls `node.matches()` which only exists on Element nodes (`nodeType === 1`). When a comment node (type 8) or other non-element/non-text node is encountered, it fails the `nodeType === 3` check and falls through to `.matches()`, which doesn't exist.

- [ ] **Step 1: Fix the guard clause**

In `auto_a11y/testing/touchpoint_tests/test_lists.py:233`, change:

```javascript
// BEFORE (line 233):
.filter(node => node !== icon && (node.nodeType === 3 || !node.matches('i, span[class*="icon"], span[class*="fa-"], span[class*="material-"]')))

// AFTER:
.filter(node => node !== icon && (node.nodeType === 3 || (node.nodeType === 1 && !node.matches('i, span[class*="icon"], span[class*="fa-"], span[class*="material-"]'))))
```

This ensures `.matches()` is only called on Element nodes. Non-element, non-text nodes (comments, etc.) are excluded by failing both checks.

- [ ] **Step 2: Run fixture tests to validate**

Run: `python test_fixtures.py --type Disco --category Lists`

If no Lists category exists, run: `python test_fixtures.py --type Disco`

Expected: No regressions. The fix only adds a guard — existing behavior for text nodes (included) and element nodes (filtered by selector) is unchanged.

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/testing/touchpoint_tests/test_lists.py
git commit -m "fix: guard node.matches() call against non-Element childNodes in test_lists"
```

---

## Task 2: Add global per-domain rate limiting for test workers

**Files:**
- Modify: `auto_a11y/core/testing_job.py:347-393`

**Root cause:** Test workers only have a one-time stagger at launch (`worker_id * stagger_seconds`). After starting, all workers pull pages from the queue and test them as fast as possible with no inter-page delay. Multiple workers hitting the same domain rapidly can trigger anti-bot measures.

**Approach:** Add a shared `asyncio.Lock` + timestamp that enforces a minimum delay between any two page navigations across all workers. Reuse the website's existing `scraping_config.request_delay` value (default 1.0s) so it's configurable per-website in the UI.

- [ ] **Step 1: Add the shared throttle inside `run()`**

In `auto_a11y/core/testing_job.py`, after line 346 (the `page_queue` setup), add:

```python
            # Global rate limiter: enforce minimum delay between page navigations
            # Uses the website's configured request_delay (same as scraping)
            request_delay = website.scraping_config.request_delay
            throttle_lock = asyncio.Lock()
            last_request_time = {'t': 0.0}  # mutable container for closure
```

- [ ] **Step 2: Add `import time` to the top of the file**

In `auto_a11y/core/testing_job.py`, add `import time` with the other imports at the top of the file (after line 7 `from datetime import datetime`).

- [ ] **Step 3: Add throttle acquisition in the worker loop**

In `auto_a11y/core/testing_job.py`, inside `_test_worker`, after the try/except block that dequeues from the queue (after `except asyncio.QueueEmpty: return` on line 385) and before the page is marked as testing (line 388 `page.status = PageStatus.TESTING`), add:

```python
                        # Respect global rate limit across all workers
                        async with throttle_lock:
                            now = time.monotonic()
                            elapsed = now - last_request_time['t']
                            if elapsed < request_delay:
                                await asyncio.sleep(request_delay - elapsed)
                            last_request_time['t'] = time.monotonic()
```

**Note:** This reuses the website's `scraping_config.request_delay` (default 1.0s), so testing and scraping share the same rate config. If independent control is needed later, a separate `test_delay` field can be added to `ScrapingConfig`.

- [ ] **Step 4: Test manually**

Start the app, create a small website with 4+ pages, and run "Test All Pages". Check logs for interleaved worker output — workers should no longer fire page tests simultaneously. Look for the stagger pattern in timestamps.

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/core/testing_job.py
git commit -m "feat: add global per-domain rate limiting across test workers"
```

---

## Task 3: Fix French translations for website page buttons

**Files:**
- Modify: `auto_a11y/web/translations/fr/LC_MESSAGES/messages.po:8330-8336`
- Recompile: `auto_a11y/web/translations/fr/LC_MESSAGES/messages.mo`

**Root cause:** Several entries in the website view region of `messages.po` have wrong translations (likely from a bad merge). The "Test All Pages" entry (line 8330) has a `#, fuzzy` flag and merge-conflict remnants. "Test Untested Pages" has no entry at all.

- [ ] **Step 1: Fix wrong translations in the website view region (lines 8315-8341)**

In `messages.po`, fix these entries:

**Line 8316-8317 — "Abort" translated as "Les deux" (Both):**
```po
# BEFORE:
msgid "Abort"
msgstr "Les deux"

# AFTER:
msgid "Abort"
msgstr "Abandonner"
```

**Line 8320-8321 — "Select users to test:" translated as "for discovery":**
```po
# BEFORE:
msgid "Select users to test:"
msgstr "Sélectionner les utilisateurs pour la découverte :"

# AFTER:
msgid "Select users to test:"
msgstr "Sélectionner les utilisateurs à tester :"
```

**Lines 8330-8336 — "Test All Pages" with merge conflict:**
```po
# BEFORE:
#, fuzzy
msgid "Test All Pages"
msgstr ""
"#-#-#-#-#  messages.po (PROJECT VERSION)  #-#-#-#-#\n"
"Tester tous les sites\n"
"#-#-#-#-#  messages.po (PROJECT VERSION)  #-#-#-#-#\n"
"Tester toutes les pages"

# AFTER:
msgid "Test All Pages"
msgstr "Tester toutes les pages"
```

Remove the `#, fuzzy` flag and merge-conflict markers. Use "Tester toutes les pages" (not "Tester tous les sites" which means "Test all websites").

**Lines 8340-8341 — "Discovery History" translated as "Découverte démarrée" (Discovery started):**
```po
# BEFORE:
msgid "Discovery History"
msgstr "Découverte démarrée"

# AFTER:
msgid "Discovery History"
msgstr "Historique de découverte"
```

- [ ] **Step 2: Add the missing "Test Untested Pages" entry**

Insert a new entry after the "Test All Pages" block (after the corrected line 8336):

```po
#: auto_a11y/web/templates/websites/view.html:105
#: auto_a11y/web/templates/websites/view.html:553
msgid "Test Untested Pages"
msgstr "Tester les pages non testées"
```

- [ ] **Step 3: Recompile the .mo file**

Run: `msgfmt -o auto_a11y/web/translations/fr/LC_MESSAGES/messages.mo auto_a11y/web/translations/fr/LC_MESSAGES/messages.po`

Expected: No errors. If `msgfmt` is not available, use: `pybabel compile -d auto_a11y/web/translations`

- [ ] **Step 4: Verify in browser**

Start the app, switch language to French using the language selector, navigate to a website page. Verify:
- "Test All Pages" button shows "Tester toutes les pages"
- "Test Untested Pages" button shows "Tester les pages non testées"

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/translations/fr/LC_MESSAGES/messages.po auto_a11y/web/translations/fr/LC_MESSAGES/messages.mo
git commit -m "fix: correct French translations on website view page"
```
