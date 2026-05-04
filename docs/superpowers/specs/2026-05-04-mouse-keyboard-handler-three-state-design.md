# Mouse/Keyboard Handler — Three-State Test + Global Handler Discovery

**Date:** 2026-05-04
**Touchpoint:** `event_handling`
**WCAG:** 2.1.1 (Keyboard, Level A)

## Problem

The current `ErrMouseOnlyHandler` test treats the mouse-handler-without-keyboard-handler check as a binary pass/fail, looking only at the element itself. Two real-world patterns are not modeled:

1. A mouse-driven widget delegates keyboard handling to a focusable ancestor (e.g., a clickable `<div>` inside a `<button>` or `<div tabindex="0">` that owns the keydown listener). Automated DOM testing cannot prove the bubbled keyboard event actually triggers the same behavior the mouse handler triggers, but the page is *plausibly* keyboard-accessible.
2. Pages frequently attach keyboard handlers globally to `document`, `window`, or `<body>` for shortcut/dispatch logic. These are evidence of keyboard handling but cannot be tied to any specific widget.

Conflating these cases with the binary pass/fail produces both false negatives (real mouse-only widgets missed when there's a global keydown listener anywhere on the page) and false positives (keyboard-delegated widgets flagged as fully broken).

## Goals

- Replace the single binary check with a **three-state per-element test** that distinguishes pass / probable-but-unverifiable / fail.
- Add a **separate page-level discovery test** for global keyboard handlers, independent of the per-element test.
- Improve handler detection to recognize `addEventListener` against `document`, `window`, `body`, and class/tag-selector-based queries — feeding both new tests.
- Preserve the existing fixture-driven validation gate: tests only ship in production once all fixtures pass.

## Non-goals

- AST-level JavaScript parsing. Detection remains heuristic regex-based, matching the existing approach in `test_event_handlers.py`.
- Detecting framework-attached handlers (React `onClick`, Vue `@click`, etc.). Out of scope.
- Runtime probing of `addEventListener` via Playwright `add_init_script`. Architecturally noted as a future swap-in (see "Implementation strategy"); not in this delivery.
- Changes to other tests in `test_event_handlers.py` (tab order, escape handler, focus indicators).

## Design

### Two distinct tests

**Test 1 — `MouseHandlerKeyboardCheck` (per-element, three outcomes)**

For every element with a mouse handler, not intrinsically interactive, and no `tabindex` attribute:

| Condition | Outcome | Code |
|---|---|---|
| Element has both a mouse handler AND a keyboard handler on itself | Pass | (none — increments `elements_passed`) |
| Element has a mouse handler, no keyboard handler on itself, and the **nearest focusable ancestor** has a keyboard handler | Warn | `WarnMouseHandlerKeyboardOnAncestor` |
| Element has a mouse handler, no keyboard handler on itself, and the nearest focusable ancestor lacks a keyboard handler — OR no focusable ancestor exists | Fail | `ErrMouseOnlyHandler` (existing code, tightened scope) |

**"Focusable ancestor"** = an ancestor element where `tagName` ∈ `{a, button, input, select, textarea, details, summary}`, OR `tabindex >= 0`, OR `role` ∈ `{button, link, menuitem, tab, checkbox, radio, switch}`. Walk stops at the first focusable ancestor encountered (or `<body>`); ancestors above the first focusable one are not consulted. **`<body>` itself is never considered a focusable ancestor for Test 1** — if the walk reaches `<body>` without finding a qualifying ancestor, the outcome is `ErrMouseOnlyHandler` (fail). Keyboard handlers on `<body>` are surfaced exclusively by Test 2, not by Test 1.

**Test 2 — `GlobalKeyboardHandlerDiscovery` (page-level, single outcome)**

After per-page handler collection completes:

| Condition | Outcome | Code |
|---|---|---|
| Page has a key event listener (`keydown`/`keyup`/`keypress`) on `document`, `window`, or `<body>` | Warn (one per page) | `WarnGlobalKeyboardHandlerPresent` |

Independent of Test 1 — both can fire on the same page.

### Handler detection — single combined map

Both tests consume one structure built once per page:

```js
{
  elementHandlers: Map<Element, { mouseEvents: Set<string>, keyEvents: Set<string> }>,
  globalHandlers: {
    document: Set<string>,
    window:   Set<string>,
    body:     Set<string>
  }
}
```

Built from these sources, in this order:

1. **Inline `on*` attributes** on every element. Mouse events: `click`, `mousedown`, `mouseup`, `mouseover`, `mouseout`, `dblclick`, `contextmenu`. Key events: `keydown`, `keyup`, `keypress`. Inline handlers on `<body>` populate both `elementHandlers[body]` and `globalHandlers.body`.

2. **Var-to-element resolution table** built by scanning all script text (inline + fetched external) for assignment patterns:
   - `(?:const|let|var)\s+(\w+)\s*=\s*document\.getElementById\(['"]([^'"]+)['"]\)` → element by id
   - `(?:const|let|var)\s+(\w+)\s*=\s*document\.querySelector\(['"]#([^'"]+)['"]\)` → element by id
   - `(?:const|let|var)\s+(\w+)\s*=\s*document\.querySelector\(['"]\.([^'"]+)['"]\)` → first element matching class
   - `(?:const|let|var)\s+(\w+)\s*=\s*document\.querySelector\(['"]([a-z][a-z0-9]*)['"]\)` → first element matching tag
   - `querySelectorAll` is **ignored** (ambiguous which element receives the listener; documented limitation).
   - **Reassignment policy:** scripts are scanned in DOM order (inline scripts in document order, then fetched external scripts in the order their URLs appear). If the same `varName` is assigned more than once across the combined text, **the last assignment wins**. JavaScript scope (function/block) is **not** modeled — all assignments share a single flat namespace. Documented heuristic limitation; matches the existing parsing approach in the file.

3. **`addEventListener` calls** parsed from the same script text:
   - `(\w+)\.addEventListener\(['"](\w+)['"]` — `varName` resolved via the table from step 2; populates `elementHandlers`.
   - `document\.addEventListener\(['"](\w+)['"]` → `globalHandlers.document`
   - `window\.addEventListener\(['"](\w+)['"]` → `globalHandlers.window`
   - `document\.body\.addEventListener\(['"](\w+)['"]` → `globalHandlers.body`

Comments are stripped from script text before regex matching (existing logic in the file already does this for the modal-escape check; reused).

### Implementation strategy — Approach 3 (hybrid hinge point)

The handler-collection logic is extracted into a single JavaScript function `collectHandlerMap()` returning the structure above. Test 1 and Test 2 both consume its output. This isolates the heuristic detection in one place, so a future refactor that replaces it with a Playwright `add_init_script`-based runtime probe (Approach 2 from brainstorming) only touches that one function.

### File changes

| File | Change |
|---|---|
| `auto_a11y/testing/touchpoint_tests/test_event_handlers.py` | Replace existing inline mouse-only check (~lines 247–337). Extract `collectHandlerMap()` JS function. Add Test 1 three-state logic and Test 2 global-handler discovery. Update `TEST_DOCUMENTATION.tests` to add the two new entries and tighten the existing `mouse-only` entry. |
| `auto_a11y/config/touchpoint_tests.py` | Add `WarnMouseHandlerKeyboardOnAncestor` and `WarnGlobalKeyboardHandlerPresent` to the `event_handling` list. |
| `auto_a11y/core/touchpoints.py` | Map both new codes to `TouchpointID.EVENT_HANDLING` in the code-to-touchpoint map. |
| `auto_a11y/reporting/issue_descriptions_enhanced.py` | Add full entries for both new codes. Update `ErrMouseOnlyHandler` description to reflect the tightened scope. |
| `auto_a11y/web/translations/en/issues.ftl` | Add EN translations for both new codes; update `ErrMouseOnlyHandler` `.what`/`.why`/`.remediation` for new scope. |
| `auto_a11y/web/translations/fr/issues.ftl` | Same FR translations. |
| `ISSUE_CATALOG.md` | Add catalog entries for both new codes; update `ErrMouseOnlyHandler` description. |
| `Fixtures/EventHandling/` | Add new fixtures (see below); audit existing `ErrMouseOnlyHandler` fixtures for any that now belong under the warn code. |

### Issue payloads

**`WarnMouseHandlerKeyboardOnAncestor`**
- `title`: "Mouse handler delegates to ancestor keyboard handler — manual verification required"
- `what`: Element has a mouse handler but no keyboard handler on itself; the nearest focusable ancestor (`<{ancestorTag}>` at `{ancestorXpath}`) has a keyboard handler that may receive bubbled events.
- `why`: Automated testing cannot verify whether the ancestor's keyboard handler actually triggers the same interaction as the element's mouse handler. The widget may or may not be keyboard-accessible in practice.
- `who`: Keyboard users, screen reader users, users with motor disabilities.
- `impact`: medium
- `wcag`: ['2.1.1']
- `remediation`: Either (a) add a keyboard handler directly on the element to make the keyboard equivalence explicit, or (b) manually verify that activating the ancestor with the keyboard performs the same action as clicking the element. Document the keyboard interaction in code comments.
- Payload includes: `ancestorXpath`, `ancestorTag`, `ancestorKeyEvents` (list).

**`WarnGlobalKeyboardHandlerPresent`**
- `title`: "Page-level keyboard handler detected — keyboard accessibility cannot be verified automatically"
- `what`: Page has keyboard event handler(s) attached to `{targets}` (e.g., `document`, `window`, or `<body>`).
- `why`: This test looks for evidence of keyboard event handling at the document/window/body level but cannot verify whether those handlers provide keyboard equivalents for any specific mouse-driven interaction on the page. Manual review required.
- `who`: Keyboard users, screen reader users, users with motor disabilities.
- `impact`: medium
- `wcag`: ['2.1.1']
- `remediation`: Manually test that every mouse-driven interaction on the page can be triggered using only the keyboard. Document keyboard equivalents and ensure any global shortcut handlers do not conflict with assistive-technology key bindings.
- Single instance per page, attached to `<html>` xpath. Payload includes: `targets` (list of "document"/"window"/"body"), `events` (list of key event types).

**`ErrMouseOnlyHandler` (tightened)**
- `what`: Element has a mouse handler, no keyboard handler on itself, and no focusable ancestor with a keyboard handler — keyboard users cannot trigger the interaction.
- All other fields unchanged.

### Fixture plan

**New fixtures under `Fixtures/EventHandling/`:**

| Fixture | Expected outcome |
|---|---|
| `WarnMouseHandlerKeyboardOnAncestor_001_ancestor_button.html` | `<button>` with `keydown` listener wraps `<div onclick>`. 1 warn. |
| `WarnMouseHandlerKeyboardOnAncestor_002_ancestor_tabindex.html` | `<div tabindex="0">` with `addEventListener('keydown', …)` wraps `<span onclick>`. 1 warn. |
| `WarnMouseHandlerKeyboardOnAncestor_003_correct_descendant_has_key.html` | Both ancestor and descendant have keyboard handlers — descendant passes Test 1. 0 issues. |
| `WarnGlobalKeyboardHandlerPresent_001_document_keydown.html` | `document.addEventListener('keydown', …)` only, no other interactive content. 1 warn (Test 2). |
| `WarnGlobalKeyboardHandlerPresent_002_window_and_body.html` | `window.addEventListener('keydown', …)` and `<body onkeydown>`. 1 warn (single instance). |
| `WarnGlobalKeyboardHandlerPresent_003_correct_no_global.html` | No global handler. 0 issues. |

Each fixture includes the standard `<script type="application/json" id="test-metadata">` block and `data-expected-violation` / `data-expected-pass` markers consistent with existing fixtures.

**Audit of existing `ErrMouseOnlyHandler` fixtures:**

- `001_violations_basic`, `003_violations_inline_onclick`, `004_violations_mousedown_mouseup`, `005_violations_dblclick`, `009_violations_various_elements`, `010_violations_fake_buttons` — descendants with mouse handlers and no ancestor keyboard handlers. Should still pass as `ErrMouseOnlyHandler`. Verify each by re-running `test_fixtures.py --code ErrMouseOnlyHandler`.
- `002_correct_with_keyboard`, `007_correct_inline_handlers`, `008_correct_programmatic_handlers` — both handlers on the same element. Element is intrinsic interactive (`<button>`) so still 0 violations under the new logic. Verify.
- `006_violations_mixed_handlers` — three `<div>`/`<span>` elements with mouse handlers, no ancestor keyboard handlers. Should still produce 3 `ErrMouseOnlyHandler`. Verify.

If any existing fixture turns out to have an unintended ancestor keyboard handler under the new logic, it will be moved to the warn fixture set and replaced.

## Testing

1. `python test_fixtures.py --code ErrMouseOnlyHandler` — all existing fixtures must still pass.
2. `python test_fixtures.py --code WarnMouseHandlerKeyboardOnAncestor` — all new warn fixtures pass.
3. `python test_fixtures.py --code WarnGlobalKeyboardHandlerPresent` — all new global-handler fixtures pass.
4. Type checking: `.venv/bin/python -m mypy && .venv/bin/python -m pyright && .venv/bin/python -m ty check` — must pass on `auto_a11y/testing/touchpoint_tests/test_event_handlers.py`.
5. Translation validation: `python tests/validate_translations.py` — both EN and FR must define both new codes.

## Open questions / known limitations

- **Heuristic detection.** Framework-attached handlers (React, Vue, Svelte) and handlers attached via `querySelectorAll`-iterated NodeLists are not detected. Documented in the JS function's comment. Future work: replace `collectHandlerMap()` with a Playwright init-script runtime probe.
- **Inline handler on `<body>`** double-counts as both a per-element handler on `<body>` and a global handler. Per-element handlers on `<body>` are not flagged by Test 1 anyway (body is the walk-stop boundary), so this is harmless.
- **`ErrMouseOnlyHandler` impact** stays HIGH. `WarnMouseHandlerKeyboardOnAncestor` and `WarnGlobalKeyboardHandlerPresent` are MEDIUM, reflecting their "manual check required" nature.
