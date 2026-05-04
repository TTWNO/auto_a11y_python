# Mouse/Keyboard Handler Three-State Test Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the binary `ErrMouseOnlyHandler` check with a three-state per-element test (pass / `WarnMouseHandlerKeyboardOnAncestor` / `ErrMouseOnlyHandler`) and add a separate page-level `WarnGlobalKeyboardHandlerPresent` discovery test.

**Architecture:** Extract handler collection in `test_event_handlers.py` into a single JS function `collectHandlerMap()` returning per-element handler sets and a global-handler bucket. Two independent consumers run on its output: Test 1 walks each candidate element's ancestry to the nearest focusable ancestor and emits one of three outcomes; Test 2 emits a single warn if any keyboard handler is attached to `document`/`window`/`<body>`. Detection broadened to recognize `addEventListener` against `document`, `window`, `body`, plus `var = querySelector('#id'|'.class'|'tag')` resolution. **Two-phase evaluation:** the first `page.evaluate()` collects DOM info (script tags, modal elements) without emitting any results; Python fetches external scripts; a second `page.evaluate()` receives the combined inline+external script text and runs `collectHandlerMap()`, Test 1, Test 2, and the existing `ErrMissingTabindex` check. This guarantees Test 1 sees handlers from external scripts, matching the spec's "scripts are scanned in DOM order (inline scripts in document order, then fetched external scripts in the order their URLs appear)" requirement.

**Tech Stack:** Python 3.8+, Playwright, regex-based JS parsing, MongoDB-backed fixture test runner, Fluent (`.ftl`) translations, mypy/pyright/ty strict type checking.

**Spec:** `docs/superpowers/specs/2026-05-04-mouse-keyboard-handler-three-state-design.md`

**Scope ground rules (apply to every task):**
- Strict type checking is mandatory. Run `.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check` after each Python edit.
- Never bypass the pre-commit hook (no `--no-verify`, no `--no-gpg-sign`).
- Every user-visible string requires both EN and FR Fluent entries.
- Bootstrap colour classes are forbidden — but this plan touches no templates.
- Each task ends with a commit. Frequent commits, small diffs.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `auto_a11y/testing/touchpoint_tests/test_event_handlers.py` | Modify | Replace lines ~247–337 with extracted `collectHandlerMap()` JS function + Test 1 (three-state per-element) + Test 2 (global-handler discovery). Update `TEST_DOCUMENTATION`. |
| `auto_a11y/config/touchpoint_tests.py` | Modify | Add the two new codes to the `event_handling` list. |
| `auto_a11y/core/touchpoints.py` | Modify | Map both new codes to `TouchpointID.EVENT_HANDLING`. |
| `auto_a11y/reporting/issue_descriptions_enhanced.py` | Modify | Add full description entries for the two new codes; update `ErrMouseOnlyHandler` description. |
| `auto_a11y/web/translations/en/issues.ftl` | Modify | Add EN translations for the two new codes; update `ErrMouseOnlyHandler` `.what`/`.why`/`.remediation`. |
| `auto_a11y/web/translations/fr/issues.ftl` | Modify | Same FR translations. |
| `ISSUE_CATALOG.md` | Modify | Add catalog entries for the two new codes; update `ErrMouseOnlyHandler` description. |
| `Fixtures/EventHandling/WarnMouseHandlerKeyboardOnAncestor_001_ancestor_button.html` | Create | 1 warn fixture: `<button>` wraps `<div onclick>`. |
| `Fixtures/EventHandling/WarnMouseHandlerKeyboardOnAncestor_002_ancestor_tabindex.html` | Create | 1 warn fixture: `<div tabindex="0">` with keydown wraps `<span onclick>`. |
| `Fixtures/EventHandling/WarnMouseHandlerKeyboardOnAncestor_003_correct_descendant_has_key.html` | Create | 0 warns: descendant has its own keyboard handler. |
| `Fixtures/EventHandling/WarnGlobalKeyboardHandlerPresent_001_document_keydown.html` | Create | 1 warn: `document.addEventListener('keydown', …)`. |
| `Fixtures/EventHandling/WarnGlobalKeyboardHandlerPresent_002_window_and_body.html` | Create | 1 warn (deduped): `window` + `<body onkeydown>`. |
| `Fixtures/EventHandling/WarnGlobalKeyboardHandlerPresent_003_correct_no_global.html` | Create | 0 warns: no global handler present. |

---

## Task 1: Add metadata for `WarnMouseHandlerKeyboardOnAncestor`

**Files:**
- Modify: `auto_a11y/core/touchpoints.py` (add to code-to-touchpoint map after line 511)
- Modify: `auto_a11y/config/touchpoint_tests.py` (append to `event_handling` list around line 251)
- Modify: `auto_a11y/reporting/issue_descriptions_enhanced.py` (add entry directly after `ErrMouseOnlyHandler` at line 1957)
- Modify: `auto_a11y/web/translations/en/issues.ftl` (add entry directly after `ErrMouseOnlyHandler` ending at line 1456)
- Modify: `auto_a11y/web/translations/fr/issues.ftl` (add entry directly after `ErrMouseOnlyHandler` ending at line 1658)
- Modify: `ISSUE_CATALOG.md` (add entry after the `ErrMouseOnlyHandler` block at line 1773)

- [ ] **Step 1.1: Add to touchpoint map**

In `auto_a11y/core/touchpoints.py`, in the dict starting at line 510 (`# Event handling errors`), add the line directly after `'ErrMouseOnlyHandler': TouchpointID.EVENT_HANDLING,`:

```python
        'WarnMouseHandlerKeyboardOnAncestor': TouchpointID.EVENT_HANDLING,
```

- [ ] **Step 1.2: Add to enabled touchpoint tests**

In `auto_a11y/config/touchpoint_tests.py`, in the `'event_handling'` list (around line 250), add `'WarnMouseHandlerKeyboardOnAncestor',` directly after `'ErrMouseOnlyHandler',`.

- [ ] **Step 1.3: Add description**

In `auto_a11y/reporting/issue_descriptions_enhanced.py`, insert this entry directly after the `'ErrMouseOnlyHandler'` block (which ends at line 1957):

```python
        'WarnMouseHandlerKeyboardOnAncestor': {
            'title': "Mouse handler delegates to ancestor keyboard handler — manual verification required",
            'what': "Element has a mouse handler but no keyboard handler on itself; the nearest focusable ancestor has a keyboard handler that may receive bubbled events.",
            'why': "Automated testing cannot verify whether the ancestor's keyboard handler actually triggers the same interaction as the element's mouse handler. The widget may or may not be keyboard-accessible in practice.",
            'who': "Keyboard users, screen reader users, users with motor disabilities.",
            'impact': ImpactScale.MEDIUM.value,
            'wcag': ['2.1.1'],
            'remediation': "Either (a) add a keyboard handler directly on the element to make the keyboard equivalence explicit, or (b) manually verify that activating the ancestor with the keyboard performs the same action as clicking the element. Document the keyboard interaction in code comments."
        },
```

- [ ] **Step 1.4: Add EN translation**

In `auto_a11y/web/translations/en/issues.ftl`, insert directly after the `ErrMouseOnlyHandler` block (which ends at line 1456):

```ftl
WarnMouseHandlerKeyboardOnAncestor =
    .title = Mouse handler delegates to ancestor keyboard handler — manual verification required
    .what = Element has a mouse handler but no keyboard handler on itself; the nearest focusable ancestor has a keyboard handler that may receive bubbled events.
    .why = Automated testing cannot verify whether the ancestor's keyboard handler actually triggers the same interaction as the element's mouse handler. The widget may or may not be keyboard-accessible in practice.
    .who = Keyboard users, screen reader users, users with motor disabilities.
    .remediation = Either (a) add a keyboard handler directly on the element to make the keyboard equivalence explicit, or (b) manually verify that activating the ancestor with the keyboard performs the same action as clicking the element. Document the keyboard interaction in code comments.
```

- [ ] **Step 1.5: Add FR translation**

In `auto_a11y/web/translations/fr/issues.ftl`, insert directly after the `ErrMouseOnlyHandler` block (ending at line 1658):

```ftl
WarnMouseHandlerKeyboardOnAncestor =
    .title = Le gestionnaire souris délègue à un gestionnaire clavier d'un ancêtre — vérification manuelle requise
    .what = L'élément a un gestionnaire souris mais aucun gestionnaire clavier sur lui-même ; l'ancêtre focalisable le plus proche possède un gestionnaire clavier qui peut recevoir les événements remontés.
    .why = Les tests automatisés ne peuvent pas vérifier si le gestionnaire clavier de l'ancêtre déclenche réellement la même interaction que le gestionnaire souris de l'élément. Le widget peut être ou ne pas être accessible au clavier en pratique.
    .who = Utilisateurs de clavier, utilisateurs de lecteurs d'écran, utilisateurs avec des handicaps moteurs.
    .remediation = Soit (a) ajouter un gestionnaire clavier directement sur l'élément pour rendre l'équivalence clavier explicite, soit (b) vérifier manuellement que l'activation de l'ancêtre au clavier effectue la même action qu'un clic sur l'élément. Documenter l'interaction clavier dans des commentaires de code.
    .what-generic = L'élément a un gestionnaire souris mais aucun gestionnaire clavier sur lui-même ; l'ancêtre focalisable le plus proche possède un gestionnaire clavier qui peut recevoir les événements remontés.
```

- [ ] **Step 1.6: Add to ISSUE_CATALOG.md**

In `ISSUE_CATALOG.md`, insert directly after the `ErrMouseOnlyHandler` block (the `---` separator at line 1773):

```markdown
ID: WarnMouseHandlerKeyboardOnAncestor
Type: Warning
Impact: Medium
WCAG: 2.1.1 Keyboard (Level A)
Touchpoint: event_handling
Description: Mouse handler delegates to ancestor keyboard handler — manual verification required
Why it matters: Automated testing cannot verify whether the ancestor's keyboard handler actually triggers the same interaction as the element's mouse handler.
Who it affects: Keyboard users, screen reader users, users with motor disabilities.
How to fix: Add a keyboard handler directly on the element, or manually verify keyboard equivalence works through the ancestor.

---
```

- [ ] **Step 1.7: Type check**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check`
Expected: All three report success.

- [ ] **Step 1.8: Validate translations**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python tests/validate_translations.py`
Expected: PASS for `WarnMouseHandlerKeyboardOnAncestor` (both EN and FR present).

- [ ] **Step 1.9: Commit**

```bash
git add auto_a11y/core/touchpoints.py auto_a11y/config/touchpoint_tests.py \
        auto_a11y/reporting/issue_descriptions_enhanced.py \
        auto_a11y/web/translations/en/issues.ftl \
        auto_a11y/web/translations/fr/issues.ftl \
        ISSUE_CATALOG.md
git commit -m "feat(event-handlers): add WarnMouseHandlerKeyboardOnAncestor metadata"
```

---

## Task 2: Add metadata for `WarnGlobalKeyboardHandlerPresent`

**Files:** Same six files as Task 1, with the same insertion-point pattern (immediately after the `WarnMouseHandlerKeyboardOnAncestor` entries you just added).

- [ ] **Step 2.1: Add to touchpoint map**

In `auto_a11y/core/touchpoints.py`, directly after the `WarnMouseHandlerKeyboardOnAncestor` line you added in Task 1.1:

```python
        'WarnGlobalKeyboardHandlerPresent': TouchpointID.EVENT_HANDLING,
```

- [ ] **Step 2.2: Add to enabled touchpoint tests**

In `auto_a11y/config/touchpoint_tests.py`, append `'WarnGlobalKeyboardHandlerPresent',` directly after `'WarnMouseHandlerKeyboardOnAncestor',`.

- [ ] **Step 2.3: Add description**

In `auto_a11y/reporting/issue_descriptions_enhanced.py`, insert directly after the `WarnMouseHandlerKeyboardOnAncestor` block:

```python
        'WarnGlobalKeyboardHandlerPresent': {
            'title': "Page-level keyboard handler detected — keyboard accessibility cannot be verified automatically",
            'what': "Page has keyboard event handler(s) attached to {targets}.",
            'why': "This test looks for evidence of keyboard event handling at the document/window/body level but cannot verify whether those handlers provide keyboard equivalents for any specific mouse-driven interaction on the page. Manual review required.",
            'who': "Keyboard users, screen reader users, users with motor disabilities.",
            'impact': ImpactScale.MEDIUM.value,
            'wcag': ['2.1.1'],
            'remediation': "Manually test that every mouse-driven interaction on the page can be triggered using only the keyboard. Document keyboard equivalents and ensure any global shortcut handlers do not conflict with assistive-technology key bindings."
        },
```

- [ ] **Step 2.4: Add EN translation**

In `auto_a11y/web/translations/en/issues.ftl`, directly after the `WarnMouseHandlerKeyboardOnAncestor` block:

```ftl
WarnGlobalKeyboardHandlerPresent =
    .title = Page-level keyboard handler detected — keyboard accessibility cannot be verified automatically
    .what = Page has keyboard event handler(s) attached to { $targets }.
    .why = This test looks for evidence of keyboard event handling at the document/window/body level but cannot verify whether those handlers provide keyboard equivalents for any specific mouse-driven interaction on the page. Manual review required.
    .who = Keyboard users, screen reader users, users with motor disabilities.
    .remediation = Manually test that every mouse-driven interaction on the page can be triggered using only the keyboard. Document keyboard equivalents and ensure any global shortcut handlers do not conflict with assistive-technology key bindings.
```

- [ ] **Step 2.5: Add FR translation**

In `auto_a11y/web/translations/fr/issues.ftl`, directly after the `WarnMouseHandlerKeyboardOnAncestor` block:

```ftl
WarnGlobalKeyboardHandlerPresent =
    .title = Gestionnaire de clavier au niveau de la page détecté — l'accessibilité au clavier ne peut pas être vérifiée automatiquement
    .what = La page a des gestionnaires d'événements clavier attachés à { $targets }.
    .why = Ce test recherche des preuves de gestion d'événements clavier au niveau document/window/body mais ne peut pas vérifier si ces gestionnaires fournissent des équivalents clavier pour une interaction spécifique pilotée par la souris sur la page. Une révision manuelle est requise.
    .who = Utilisateurs de clavier, utilisateurs de lecteurs d'écran, utilisateurs avec des handicaps moteurs.
    .remediation = Tester manuellement que chaque interaction pilotée par la souris sur la page peut être déclenchée en utilisant uniquement le clavier. Documenter les équivalents clavier et s'assurer que les gestionnaires de raccourcis globaux n'entrent pas en conflit avec les raccourcis clavier des technologies d'assistance.
    .what-generic = La page a des gestionnaires d'événements clavier au niveau document/window/body.
```

- [ ] **Step 2.6: Add to ISSUE_CATALOG.md**

Directly after the `WarnMouseHandlerKeyboardOnAncestor` block:

```markdown
ID: WarnGlobalKeyboardHandlerPresent
Type: Warning
Impact: Medium
WCAG: 2.1.1 Keyboard (Level A)
Touchpoint: event_handling
Description: Page-level keyboard handler detected — keyboard accessibility cannot be verified automatically
Why it matters: This test looks for evidence of keyboard event handling at the document/window/body level but cannot verify whether those handlers provide keyboard equivalents for any specific mouse-driven interaction.
Who it affects: Keyboard users, screen reader users, users with motor disabilities.
How to fix: Manually test that every mouse-driven interaction on the page can be triggered using only the keyboard.

---
```

- [ ] **Step 2.7: Type check + translation validation**

Run both:
- `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check`
- `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python tests/validate_translations.py`
Expected: Both pass.

- [ ] **Step 2.8: Commit**

```bash
git add auto_a11y/core/touchpoints.py auto_a11y/config/touchpoint_tests.py \
        auto_a11y/reporting/issue_descriptions_enhanced.py \
        auto_a11y/web/translations/en/issues.ftl \
        auto_a11y/web/translations/fr/issues.ftl \
        ISSUE_CATALOG.md
git commit -m "feat(event-handlers): add WarnGlobalKeyboardHandlerPresent metadata"
```

---

## Task 3: Tighten `ErrMouseOnlyHandler` description

The existing `ErrMouseOnlyHandler` description does not yet reflect the tightened scope ("no focusable ancestor with a keyboard handler either"). Update it now so the catalog and translations stay accurate even if Task 4's runtime change lands separately.

**Files:**
- Modify: `auto_a11y/reporting/issue_descriptions_enhanced.py` line 1951 (`'what'` field)
- Modify: `auto_a11y/web/translations/en/issues.ftl` line 1453 (`.what`)
- Modify: `auto_a11y/web/translations/fr/issues.ftl` line 1654 (`.what`)
- Modify: `ISSUE_CATALOG.md` line 1769 (`Description:`/`Why it matters:`)

- [ ] **Step 3.1: Update Python description**

In `auto_a11y/reporting/issue_descriptions_enhanced.py`, replace the `'what'` value of `ErrMouseOnlyHandler` (line 1951) with:

```python
            'what': "Element has a mouse handler, no keyboard handler on itself, and no focusable ancestor with a keyboard handler — keyboard users cannot trigger the interaction.",
```

Leave all other fields unchanged.

- [ ] **Step 3.2: Update EN translation**

In `auto_a11y/web/translations/en/issues.ftl`, replace the `.what` line of `ErrMouseOnlyHandler` (line 1453) with:

```
    .what = Element has a mouse handler, no keyboard handler on itself, and no focusable ancestor with a keyboard handler — keyboard users cannot trigger the interaction.
```

- [ ] **Step 3.3: Update FR translation**

In `auto_a11y/web/translations/fr/issues.ftl`, replace the `.what` line of `ErrMouseOnlyHandler` (line 1654) with:

```
    .what = L'élément a un gestionnaire souris, aucun gestionnaire clavier sur lui-même, et aucun ancêtre focalisable avec un gestionnaire clavier — les utilisateurs de clavier ne peuvent pas déclencher l'interaction.
```

- [ ] **Step 3.4: Update ISSUE_CATALOG.md**

For the `ErrMouseOnlyHandler` block (line 1763), replace the `Description:` line with:

```
Description: Element has a mouse handler, no keyboard handler on itself, and no focusable ancestor with a keyboard handler — keyboard users cannot trigger the interaction.
```

- [ ] **Step 3.5: Type check + translation validation**

Run both as in Step 2.7. Both must pass.

- [ ] **Step 3.6: Commit**

```bash
git add auto_a11y/reporting/issue_descriptions_enhanced.py \
        auto_a11y/web/translations/en/issues.ftl \
        auto_a11y/web/translations/fr/issues.ftl \
        ISSUE_CATALOG.md
git commit -m "feat(event-handlers): tighten ErrMouseOnlyHandler description for new scope"
```

---

## Task 4: Implement the runtime logic — extracted handler map + Test 1 + Test 2

This is the load-bearing task. Modifies a single Python file (`test_event_handlers.py`) but replaces a block of inline JS plus the modal-escape-handler check that follows it.

**Files:**
- Modify: `auto_a11y/testing/touchpoint_tests/test_event_handlers.py` lines ~247–337 (and update the Python-side glue around the existing modal-escape check at lines ~451–506).

### Approach

Inside the existing `await page.evaluate(r''' () => { … } ''')` block at line 79, the current code interleaves three concerns:
1. JS variable→event-type parsing (lines 247–273)
2. Per-element handler check + `ErrMouseOnlyHandler` emission + `ErrMissingTabindex` emission (lines 275–337)
3. Modal escape collection (lines 339–380)

We split this into a **two-phase evaluation** so that handler-detection sees external scripts:

- **Phase 1 (existing first `page.evaluate`):** strip out concerns (1) and (2) entirely; keep tab-order checks, focusable-element discovery, and modal collection (concern 3). Return `inlineJsCode`, `externalScriptUrls`, modal info, and tab-order results.
- **Python fetch step (existing aiohttp loop):** unchanged — fetches external scripts and concatenates them into `all_js_code`.
- **Phase 2 (NEW second `page.evaluate`):** takes `all_js_code` as a parameter, defines `collectHandlerMap()` inside the arrow body, runs `ErrMissingTabindex`, Test 1, and Test 2 logic, returns new error/warning entries. Python merges them into the `results` dict.

The `collectHandlerMap()` JS lives inside Phase 2's `() => { … }` arrow.

### Step-by-step

- [ ] **Step 4.1: Create a unit fixture for Test 1 — pass case (intrinsic interactive)**

Create `Fixtures/EventHandling/MouseHandlerKeyboardCheck_pass_001_intrinsic_button.html` (NEW fixture file) covering the simple pass case so we can drive the test logic via fixtures rather than ad-hoc Python unit tests:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>MouseHandlerKeyboardCheck — pass (intrinsic interactive)</title>
    <script type="application/json" id="test-metadata">
{
    "id": "MouseHandlerKeyboardCheck_pass_001_intrinsic_button",
    "issueId": "ErrMouseOnlyHandler",
    "expectedViolationCount": 0,
    "expectedPassCount": 1,
    "description": "Native button with click and keydown handlers — fully accessible",
    "wcag": "2.1.1",
    "impact": "High"
}
    </script>
</head>
<body>
    <button id="b" data-expected-pass="true" data-pass-reason="Native button receives keyboard activation automatically">Activate</button>
    <script>
        const b = document.getElementById('b');
        b.addEventListener('click', () => console.log('click'));
        b.addEventListener('keydown', () => console.log('key'));
    </script>
</body>
</html>
```

(The fixture runner uses `expectedViolationCount: 0` to assert no `ErrMouseOnlyHandler` for the page; this exercises the "intrinsic interactive — never tested" path.)

- [ ] **Step 4.2: Run that one fixture against the unmodified code to capture baseline**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python test_fixtures.py --code ErrMouseOnlyHandler 2>&1 | tail -40`
Expected: PASS (the existing implementation already handles the intrinsic-interactive path correctly — this is just baseline).

- [ ] **Step 4.3: Phase 1 — strip handler-detection / mouse-only / ErrMissingTabindex from the existing `page.evaluate`**

In `auto_a11y/testing/touchpoint_tests/test_event_handlers.py`, locate lines 247–337 (the block from `// Parse JavaScript to find programmatic event listeners` through the closing of the `allElements.forEach(...)` loop that emits `ErrMouseOnlyHandler`). **Delete that entire block.** Phase 1 no longer collects per-element handlers; Phase 2 will. The modal-collection block immediately following at line 339+ stays as-is.

The Phase 1 evaluate now ends with the modal-collection result fields (`results._inlineJsCode`, `results._externalScriptUrls`, `results._modals`) and the existing tab-order checks. Confirm the discovery `DiscoFoundJS` blocks at lines ~401–443 still work — they reference `document.querySelectorAll('script[src], script:not([src])')` and `Array.from(document.querySelectorAll('*')).filter(...)` and do **not** depend on `elementEventMap`. Leave them unchanged.

- [ ] **Step 4.4: Phase 2 — add a second `page.evaluate` that takes combined script text and runs the new logic**

In the same file, **immediately after** the existing comment block stripping comments from `all_js_code` (around line 470, just after `all_js_code = re.sub(r'/\*[\s\S]*?\*/', '', all_js_code)`), insert this new Phase-2 evaluate:

```python
        # Phase 2: handler map + ErrMissingTabindex + Test 1 + Test 2.
        # Receives combined inline + external JS text so detection sees handlers
        # registered from external scripts.
        phase2_results: dict[str, Any] = await page.evaluate(r'''
            (combinedScriptText) => {
                const out = {errors: [], warnings: [], elements_passed: 0, elements_failed: 0,
                             globalHandlers: {document: [], window: [], body: []}};

                function getFullXPath(element) {
                    if (!element) return '';
                    function getElementIdx(el) {
                        let count = 1;
                        for (let sib = el.previousSibling; sib; sib = sib.previousSibling) {
                            if (sib.nodeType === 1 && sib.tagName === el.tagName) count++;
                        }
                        return count;
                    }
                    let path = '';
                    while (element && element.nodeType === 1) {
                        const idx = getElementIdx(element);
                        path = `/${element.tagName.toLowerCase()}[${idx}]${path}`;
                        element = element.parentNode;
                    }
                    return path;
                }
                function isIntrinsicInteractive(element) {
                    const interactiveTags = ['a', 'button', 'input', 'select', 'textarea', 'details', 'summary'];
                    const interactiveRoles = ['button', 'link', 'menuitem', 'tab', 'checkbox', 'radio', 'switch'];
                    return interactiveTags.includes(element.tagName.toLowerCase()) ||
                           (element.getAttribute('role') &&
                            interactiveRoles.includes(element.getAttribute('role')));
                }

                const MOUSE_EVENTS = ['click', 'mousedown', 'mouseup', 'mouseover', 'mouseout', 'dblclick', 'contextmenu'];
                const KEY_EVENTS = ['keydown', 'keyup', 'keypress'];

                // collectHandlerMap — Approach 3 hinge point.
                // Heuristic regex parsing of combined inline + external scripts. Limitations:
                //   - querySelectorAll handlers are ignored (ambiguous which element)
                //   - framework handlers (React, Vue, Svelte) are not detected
                //   - var reassignment: last assignment wins, scope is not modeled
                function collectHandlerMap(text) {
                    const elementHandlers = new Map();
                    const globalHandlers = {document: new Set(), window: new Set(), body: new Set()};
                    function ensureEntry(el) {
                        if (!elementHandlers.has(el)) {
                            elementHandlers.set(el, {mouseEvents: new Set(), keyEvents: new Set()});
                        }
                        return elementHandlers.get(el);
                    }
                    function recordEvent(target, eventType) {
                        const t = eventType.toLowerCase();
                        if (MOUSE_EVENTS.includes(t)) target.mouseEvents.add(t);
                        else if (KEY_EVENTS.includes(t)) target.keyEvents.add(t);
                    }

                    // 1. Inline on* attributes on every element.
                    Array.from(document.querySelectorAll('*')).forEach(el => {
                        const entry = ensureEntry(el);
                        Array.from(el.attributes).forEach(attr => {
                            if (!attr.name.startsWith('on')) return;
                            recordEvent(entry, attr.name.slice(2));
                        });
                        if (el === document.body) {
                            entry.keyEvents.forEach(e => globalHandlers.body.add(e));
                        }
                    });

                    // 2. Build var-to-element table.
                    const varTable = new Map();
                    const declRe = /(?:const|let|var)\s+(\w+)\s*=\s*document\.(getElementById|querySelector)\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
                    let m;
                    while ((m = declRe.exec(text)) !== null) {
                        const [, varName, fn, arg] = m;
                        let el = null;
                        if (fn === 'getElementById') {
                            el = document.getElementById(arg);
                        } else {
                            try { el = document.querySelector(arg); } catch (_) { el = null; }
                        }
                        if (el) varTable.set(varName, el);
                    }

                    // 3. Parse addEventListener calls.
                    const addRe = /(\w+(?:\.\w+)?)\.addEventListener\s*\(\s*['"](\w+)['"]/g;
                    while ((m = addRe.exec(text)) !== null) {
                        const target = m[1];
                        const eventType = m[2];
                        if (target === 'document') {
                            const t = eventType.toLowerCase();
                            if (KEY_EVENTS.includes(t)) globalHandlers.document.add(t);
                        } else if (target === 'window') {
                            const t = eventType.toLowerCase();
                            if (KEY_EVENTS.includes(t)) globalHandlers.window.add(t);
                        } else if (target === 'document.body') {
                            const t = eventType.toLowerCase();
                            if (KEY_EVENTS.includes(t)) globalHandlers.body.add(t);
                        } else if (varTable.has(target)) {
                            recordEvent(ensureEntry(varTable.get(target)), eventType);
                        }
                    }
                    return {elementHandlers, globalHandlers,
                            getEntry(el) { return elementHandlers.get(el); }};
                }

                const handlerMap = collectHandlerMap(combinedScriptText);

                // Helpers for Test 1.
                function isFocusable(el) {
                    if (!el || el === document.body) return false;
                    const tag = el.tagName.toLowerCase();
                    if (['a', 'button', 'input', 'select', 'textarea', 'details', 'summary'].includes(tag)) return true;
                    const tabindex = el.getAttribute('tabindex');
                    if (tabindex !== null && parseInt(tabindex) >= 0) return true;
                    const role = el.getAttribute('role');
                    if (role && ['button', 'link', 'menuitem', 'tab', 'checkbox', 'radio', 'switch'].includes(role)) return true;
                    return false;
                }
                function findFocusableAncestor(el) {
                    let cur = el.parentElement;
                    while (cur && cur !== document.body) {
                        if (isFocusable(cur)) return cur;
                        cur = cur.parentElement;
                    }
                    return null;
                }

                // ErrMissingTabindex — preserved logic, sourced from handlerMap.
                Array.from(document.querySelectorAll('*')).forEach(element => {
                    const entry = handlerMap.getEntry(element);
                    if (!entry) return;
                    if (entry.mouseEvents.size === 0 && entry.keyEvents.size === 0) return;
                    if (isIntrinsicInteractive(element) || element.hasAttribute('tabindex')) return;
                    const tagName = element.tagName.toLowerCase();
                    out.errors.push({
                        err: 'ErrMissingTabindex',
                        type: 'err',
                        cat: 'event_handling',
                        element: tagName,
                        xpath: getFullXPath(element),
                        html: element.outerHTML.substring(0, 200),
                        description: `<${tagName}> with event handler is not keyboard accessible - missing tabindex`,
                        elementTag: tagName,
                        hasOnclick: element.hasAttribute('onclick'),
                        hasOtherHandlers: element.hasAttribute('onmousedown') ||
                                          element.hasAttribute('onmouseup') ||
                                          element.hasAttribute('ondblclick'),
                    });
                    out.elements_failed++;
                });

                // Test 1 — three-state per-element check.
                Array.from(document.querySelectorAll('*')).forEach(element => {
                    const entry = handlerMap.getEntry(element);
                    if (!entry || entry.mouseEvents.size === 0) return;
                    if (isIntrinsicInteractive(element) || element.hasAttribute('tabindex')) return;

                    if (entry.keyEvents.size > 0) {
                        out.elements_passed++;
                        return;
                    }
                    const ancestor = findFocusableAncestor(element);
                    const ancEntry = ancestor ? handlerMap.getEntry(ancestor) : null;
                    if (ancestor && ancEntry && ancEntry.keyEvents.size > 0) {
                        out.warnings.push({
                            err: 'WarnMouseHandlerKeyboardOnAncestor',
                            type: 'warn',
                            cat: 'event_handling',
                            element: element.tagName,
                            xpath: getFullXPath(element),
                            html: element.outerHTML.substring(0, 200),
                            description: 'Element has mouse handler but no keyboard handler; nearest focusable ancestor has a keyboard handler — manual verification required',
                            ancestorTag: ancestor.tagName.toLowerCase(),
                            ancestorXpath: getFullXPath(ancestor),
                            ancestorKeyEvents: Array.from(ancEntry.keyEvents),
                        });
                    } else {
                        out.errors.push({
                            err: 'ErrMouseOnlyHandler',
                            type: 'err',
                            cat: 'event_handling',
                            element: element.tagName,
                            xpath: getFullXPath(element),
                            html: element.outerHTML.substring(0, 200),
                            description: 'Element has mouse handler, no keyboard handler on itself, and no focusable ancestor with a keyboard handler',
                        });
                        out.elements_failed++;
                    }
                });

                // Test 2 — global keyboard handler discovery.
                out.globalHandlers = {
                    document: Array.from(handlerMap.globalHandlers.document).filter(e => KEY_EVENTS.includes(e)),
                    window: Array.from(handlerMap.globalHandlers.window).filter(e => KEY_EVENTS.includes(e)),
                    body: Array.from(handlerMap.globalHandlers.body).filter(e => KEY_EVENTS.includes(e)),
                };

                return out;
            }
        ''', all_js_code)

        # Merge phase-2 results into the main results dict.
        results['errors'].extend(phase2_results.get('errors', []))
        results['warnings'].extend(phase2_results.get('warnings', []))
        results['elements_passed'] = results.get('elements_passed', 0) + phase2_results.get('elements_passed', 0)
        results['elements_failed'] = results.get('elements_failed', 0) + phase2_results.get('elements_failed', 0)

        # Test 2 emission — single deduped warn per page.
        global_handlers = phase2_results.get('globalHandlers', {'document': [], 'window': [], 'body': []})
        targets_with_keys: list[str] = []
        events_seen: set[str] = set()
        for target_name in ('document', 'window', 'body'):
            evs = global_handlers.get(target_name, [])
            if evs:
                targets_with_keys.append(target_name)
                events_seen.update(evs)

        if targets_with_keys:
            results['warnings'].append({
                'err': 'WarnGlobalKeyboardHandlerPresent',
                'type': 'warn',
                'cat': 'event_handling',
                'element': 'html',
                'xpath': '/html[1]',
                'html': '<html>',
                'description': f"Page-level keyboard handler(s) detected on {', '.join(targets_with_keys)}. Manual verification required.",
                'targets': targets_with_keys,
                'events': sorted(events_seen),
            })
```

**Notes:**
- Phase 2 receives `all_js_code` (combined inline + external, comments already stripped by Phase 1's existing logic at line ~468). External scripts are now visible to handler detection.
- The two `getFullXPath` and `isIntrinsicInteractive` helpers are duplicated inside Phase 2 because each `page.evaluate` runs in an isolated context. This is acceptable code duplication for the architectural clarity of two-phase evaluation; both copies must stay in sync.
- `ErrMissingTabindex` data source changes: previously matched programmatic handlers only by JS variable name == element id; now matches via the broader varTable resolution (`getElementById` + `querySelector('#id'|'.class'|'tag')`). This is a deliberate behavior expansion — verified in Step 4.7 by re-running the `Keyboard/ErrMissingTabindex_*` fixtures.

- [ ] **Step 4.5: Update `TEST_DOCUMENTATION`**

In the same file, in the `TEST_DOCUMENTATION["tests"]` list (lines 21–64), replace the existing `"id": "mouse-only"` entry with:

```python
        {
            "id": "mouse-handler-keyboard-check",
            "name": "Mouse handler keyboard equivalence (three-state)",
            "description": "For each element with a mouse handler that is not intrinsically interactive: pass if the element has its own keyboard handler; warn if a focusable ancestor has a keyboard handler (manual verification needed); fail if no keyboard handler exists on the element or any focusable ancestor.",
            "impact": "high",
            "wcagCriteria": ["2.1.1"],
        },
        {
            "id": "global-keyboard-handler-discovery",
            "name": "Global Keyboard Handler Discovery",
            "description": "Detects keyboard handlers attached to document/window/body. Emits a warning because automated testing cannot tie a global handler to any specific mouse-driven widget; manual verification required.",
            "impact": "medium",
            "wcagCriteria": ["2.1.1"],
        },
```

- [ ] **Step 4.6: Type check**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check`
Expected: All three pass. (Most likely failure mode: a missing type annotation on `targets_with_keys` or `events_seen` — fix in code, never with `# type: ignore`.)

- [ ] **Step 4.7: Smoke test against existing fixtures (both `ErrMouseOnlyHandler` and `ErrMissingTabindex`)**

Run all three commands; each must succeed:

```bash
cd /home/tait/Documents/cnib/code/auto_a11y_python
.venv/bin/python test_fixtures.py --code ErrMouseOnlyHandler 2>&1 | tail -50
.venv/bin/python test_fixtures.py --code ErrMissingTabindex 2>&1 | tail -30
.venv/bin/python test_fixtures.py --code MouseHandlerKeyboardCheck_pass_001_intrinsic_button 2>&1 | tail -20
```

Expected:
- All ten existing `ErrMouseOnlyHandler_*` fixtures still pass.
- All `Keyboard/ErrMissingTabindex_*` fixtures still pass (sanity check — the data-source change for `ErrMissingTabindex` widens detection to include class/tag-selector resolved variables; existing fixtures use `getElementById` so should be unaffected, but verify).
- The new `MouseHandlerKeyboardCheck_pass_001_intrinsic_button.html` fixture passes (0 violations).

If any fixture now emits a warn instead of an err, that fixture is described in the spec's "Audit" section — move it to a new `WarnMouseHandlerKeyboardOnAncestor_*` filename and update its `expectedViolationCount` accordingly. Document any such moves in the commit message.

- [ ] **Step 4.8: Commit**

```bash
git add auto_a11y/testing/touchpoint_tests/test_event_handlers.py \
        Fixtures/EventHandling/MouseHandlerKeyboardCheck_pass_001_intrinsic_button.html
git commit -m "feat(event-handlers): three-state mouse/keyboard test + global handler discovery

Replaces the binary ErrMouseOnlyHandler check with a per-element three-state
test (pass / WarnMouseHandlerKeyboardOnAncestor / ErrMouseOnlyHandler) plus a
new page-level WarnGlobalKeyboardHandlerPresent discovery warning. Handler
detection broadened to recognize document/window/body addEventListener calls
and varName resolution via querySelector('#id'|'.class'|'tag')."
```

---

## Task 5: Add Test 1 fixtures

**Files:** Three new HTML fixtures under `Fixtures/EventHandling/`.

- [ ] **Step 5.1: Create `WarnMouseHandlerKeyboardOnAncestor_001_ancestor_button.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>WarnMouseHandlerKeyboardOnAncestor — ancestor button</title>
    <script type="application/json" id="test-metadata">
{
    "id": "WarnMouseHandlerKeyboardOnAncestor_001_ancestor_button",
    "issueId": "WarnMouseHandlerKeyboardOnAncestor",
    "expectedViolationCount": 1,
    "expectedPassCount": 0,
    "description": "Mouse-only span nested inside button with keyboard handler — keyboard delegation cannot be verified",
    "wcag": "2.1.1",
    "impact": "Medium"
}
    </script>
</head>
<body>
    <!-- A <span> is phrasing content and is valid inside <button> per HTML5,
         unlike <div> which the parser would extract. The button is the
         focusable ancestor with the keyboard handler. -->
    <button id="outer">
        <span id="inner"
              data-expected-violation="true"
              data-violation-id="WarnMouseHandlerKeyboardOnAncestor"
              data-violation-reason="Inner span has onclick; ancestor button has keydown via addEventListener — automated testing cannot verify keyboard delegation works">
            Click me
        </span>
    </button>
    <script>
        const outer = document.getElementById('outer');
        outer.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                document.getElementById('inner').click();
            }
        });
        const inner = document.getElementById('inner');
        inner.addEventListener('click', () => console.log('clicked'));
    </script>
</body>
</html>
```

- [ ] **Step 5.2: Create `WarnMouseHandlerKeyboardOnAncestor_002_ancestor_tabindex.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>WarnMouseHandlerKeyboardOnAncestor — ancestor div tabindex</title>
    <script type="application/json" id="test-metadata">
{
    "id": "WarnMouseHandlerKeyboardOnAncestor_002_ancestor_tabindex",
    "issueId": "WarnMouseHandlerKeyboardOnAncestor",
    "expectedViolationCount": 1,
    "expectedPassCount": 0,
    "description": "Mouse-only span inside div[tabindex=0] with keydown — keyboard delegation cannot be verified",
    "wcag": "2.1.1",
    "impact": "Medium"
}
    </script>
</head>
<body>
    <div id="wrap" tabindex="0" role="button" aria-label="Toggle">
        <span id="leaf"
              data-expected-violation="true"
              data-violation-id="WarnMouseHandlerKeyboardOnAncestor"
              data-violation-reason="Span has onmousedown; focusable ancestor has keydown — manual verification required">
            Toggle
        </span>
    </div>
    <script>
        const wrap = document.getElementById('wrap');
        wrap.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') console.log('toggled via keyboard');
        });
        const leaf = document.getElementById('leaf');
        leaf.addEventListener('mousedown', () => console.log('toggled via mouse'));
    </script>
</body>
</html>
```

- [ ] **Step 5.3: Create `WarnMouseHandlerKeyboardOnAncestor_003_correct_descendant_has_key.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>WarnMouseHandlerKeyboardOnAncestor — descendant has its own keyboard handler</title>
    <script type="application/json" id="test-metadata">
{
    "id": "WarnMouseHandlerKeyboardOnAncestor_003_correct_descendant_has_key",
    "issueId": "WarnMouseHandlerKeyboardOnAncestor",
    "expectedViolationCount": 0,
    "expectedPassCount": 1,
    "description": "Descendant has its own keyboard handler — Test 1 passes outright (no warn, no err)",
    "wcag": "2.1.1",
    "impact": "Medium"
}
    </script>
</head>
<body>
    <button id="outer">
        <div id="inner" tabindex="0"
             data-expected-pass="true"
             data-pass-reason="Inner element has both onclick and onkeydown directly">
            Activate
        </div>
    </button>
    <script>
        const inner = document.getElementById('inner');
        inner.addEventListener('click', () => console.log('clicked'));
        inner.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') console.log('keyed');
        });
    </script>
</body>
</html>
```

Note: this fixture's `<div id="inner" tabindex="0">` won't trigger Test 1 anyway (Test 1 skips elements with `tabindex`), so the expected outcome is silence — no warn, no err. The `expectedPassCount: 1` is informational; the gate is `expectedViolationCount: 0`.

- [ ] **Step 5.4: Run Test 1 fixtures**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python test_fixtures.py --code WarnMouseHandlerKeyboardOnAncestor 2>&1 | tail -40`
Expected: All three fixtures pass. If a fixture fails:
- Look at the runner output for the actual issue codes emitted vs expected.
- If detection misses the ancestor handler: most likely `varTable` didn't pick up the variable assignment — verify the assignment matches the regex `(?:const|let|var)\s+(\w+)\s*=\s*document\.getElementById\(…\)`.
- If detection over-fires: confirm `findFocusableAncestor()` stops at `<body>`.

- [ ] **Step 5.5: Re-run the existing `ErrMouseOnlyHandler` fixtures**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python test_fixtures.py --code ErrMouseOnlyHandler 2>&1 | tail -40`
Expected: All ten existing fixtures still pass. (Sanity check that the warn additions didn't break the err path.)

- [ ] **Step 5.6: Commit**

```bash
git add Fixtures/EventHandling/WarnMouseHandlerKeyboardOnAncestor_*.html
git commit -m "test(event-handlers): fixtures for WarnMouseHandlerKeyboardOnAncestor"
```

---

## Task 6: Add Test 2 fixtures

**Files:** Three new HTML fixtures under `Fixtures/EventHandling/`.

- [ ] **Step 6.1: Create `WarnGlobalKeyboardHandlerPresent_001_document_keydown.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>WarnGlobalKeyboardHandlerPresent — document.keydown</title>
    <script type="application/json" id="test-metadata">
{
    "id": "WarnGlobalKeyboardHandlerPresent_001_document_keydown",
    "issueId": "WarnGlobalKeyboardHandlerPresent",
    "expectedViolationCount": 1,
    "expectedPassCount": 0,
    "description": "Page registers a document-level keydown listener — manual verification required",
    "wcag": "2.1.1",
    "impact": "Medium"
}
    </script>
</head>
<body data-expected-violation="true"
      data-violation-id="WarnGlobalKeyboardHandlerPresent"
      data-violation-reason="document.addEventListener('keydown', ...) detected at page level">
    <p>Press any key to log it (this is a discovery-style test).</p>
    <script>
        document.addEventListener('keydown', (e) => console.log('global key', e.key));
    </script>
</body>
</html>
```

- [ ] **Step 6.2: Create `WarnGlobalKeyboardHandlerPresent_002_window_and_body.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>WarnGlobalKeyboardHandlerPresent — window + body</title>
    <script type="application/json" id="test-metadata">
{
    "id": "WarnGlobalKeyboardHandlerPresent_002_window_and_body",
    "issueId": "WarnGlobalKeyboardHandlerPresent",
    "expectedViolationCount": 1,
    "expectedPassCount": 0,
    "description": "window keydown + inline onkeydown on body — single deduped warning",
    "wcag": "2.1.1",
    "impact": "Medium"
}
    </script>
</head>
<body onkeydown="console.log('body key')"
      data-expected-violation="true"
      data-violation-id="WarnGlobalKeyboardHandlerPresent"
      data-violation-reason="Both window.addEventListener('keydown') and inline body onkeydown — emits one combined warning">
    <p>Listening for keys on both window and body.</p>
    <script>
        window.addEventListener('keydown', (e) => console.log('window key', e.key));
    </script>
</body>
</html>
```

- [ ] **Step 6.3: Create `WarnGlobalKeyboardHandlerPresent_003_correct_no_global.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>WarnGlobalKeyboardHandlerPresent — no global handlers</title>
    <script type="application/json" id="test-metadata">
{
    "id": "WarnGlobalKeyboardHandlerPresent_003_correct_no_global",
    "issueId": "WarnGlobalKeyboardHandlerPresent",
    "expectedViolationCount": 0,
    "expectedPassCount": 0,
    "description": "Page has only element-scoped handlers — no global warning emitted",
    "wcag": "2.1.1",
    "impact": "Medium"
}
    </script>
</head>
<body>
    <button id="b">Click</button>
    <script>
        const b = document.getElementById('b');
        b.addEventListener('click', () => console.log('clicked'));
        b.addEventListener('keydown', () => console.log('keyed'));
    </script>
</body>
</html>
```

- [ ] **Step 6.4: Run Test 2 fixtures**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python test_fixtures.py --code WarnGlobalKeyboardHandlerPresent 2>&1 | tail -40`
Expected: All three pass.

- [ ] **Step 6.5: Run Task 4's intrinsic-pass fixture explicitly**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python test_fixtures.py --category EventHandling 2>&1 | tail -60`
Expected: All EventHandling fixtures pass — both new and existing.

- [ ] **Step 6.6: Commit**

```bash
git add Fixtures/EventHandling/WarnGlobalKeyboardHandlerPresent_*.html
git commit -m "test(event-handlers): fixtures for WarnGlobalKeyboardHandlerPresent"
```

---

## Task 7: Final validation pass

- [ ] **Step 7.1: Full type check**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check`
Expected: All pass with zero errors.

- [ ] **Step 7.2: Translation validation**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python tests/validate_translations.py`
Expected: PASS with both new codes covered in EN and FR.

- [ ] **Step 7.3: Run the entire EventHandling fixture category**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python test_fixtures.py --category EventHandling 2>&1 | tail -80`
Expected: 100% pass for all `EventHandling/*` fixtures (existing + new).

- [ ] **Step 7.4: Update fixture-status web view (manual sanity check)**

Open `http://localhost:5001/testing/fixture-status` if the dev server is running, and verify:
- `ErrMouseOnlyHandler` shows all green.
- `WarnMouseHandlerKeyboardOnAncestor` and `WarnGlobalKeyboardHandlerPresent` are listed and show all green.

This step is informational only — no commit required if the dev server isn't running.

- [ ] **Step 7.5: Final commit (only if any further fixes were needed)**

If steps 7.1–7.3 surfaced any issue, fix it and commit. Otherwise no commit needed.

---

## Skills referenced
- @superpowers:test-driven-development — every task adds tests (fixtures) before/alongside the runtime code.
- @superpowers:verification-before-completion — every task ends with a `test_fixtures.py` run before commit.
- @superpowers:requesting-code-review — recommended after Task 4 lands (the load-bearing change).

## Out of scope (explicitly)
- Replacing `collectHandlerMap()` with a Playwright `add_init_script` runtime probe. Documented as future work in the spec.
- Detecting framework handlers (React, Vue, Svelte). Documented limitation.
- Modifying any other test in `test_event_handlers.py` (tab order, escape handler, focus indicators).
