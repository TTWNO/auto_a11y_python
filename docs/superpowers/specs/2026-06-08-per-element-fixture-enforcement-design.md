# Per-element fixture enforcement — design

**Date:** 2026-06-08
**Status:** Implemented (pilot)
**Scope of pilot:** `Fixtures/Colors/ErrPartialTextContrastAA.html` and `…AAA.html`

## Problem

`test_fixtures.py` derives a fixture's expected code from its filename and then runs a
**whole-file binary check**: the fixture passes as soon as the engine emits that code
*once*, anywhere on the page. It never counts detections and never reads the
`data-expected-*` attributes that ~1,500 elements across the fixtures already carry.

`ErrPartialTextContrastAA.html` exposed the consequence. It presents 11 demonstration
cases all labelled `ErrPartialTextContrastAA`, but the engine actually emitted that code
on only **2** of them; 7 emitted the plain `ErrTextContrastAA` and 2 (gradient/image)
emitted neither. The file "passed" because 2 of 11 happened to match — the other 9 were
effectively untested.

## Decisions (agreed with repo owner)

1. **Enforcement model:** keep one file per code, but validate **per element** via the
   existing `data-expected-*` attributes instead of the whole-file binary check.
2. **Strictness:** scoped-strict — a `data-expected-violation` element must emit its
   declared code at its own xpath; a `data-expected-pass` element must not emit that
   code; ambient unrelated codes are ignored.
3. **Corrective intent for the pilot:** make all 11 cases genuinely correct, not merely
   documented. The 7 plain-code cases were fixed by an **engine enhancement** (content
   overflow detection); the 2 gradient/image cases were **re-categorised in-file** as
   `WarnTextContrastCannotCalculate`, which is what they correctly emit.

## Design

### Part A — opt-in per-element enforcement (`test_fixtures.py`)

- Runs **only** when the fixture's `test-metadata` JSON sets
  `"enforceElementExpectations": true`. This is essential: auto-enabling on the mere
  presence of `data-expected-*` would subject hundreds of never-enforced fixtures to
  strict checking at once. Non-opt-in fixtures keep today's binary behaviour.
- After the engine runs, the fixture is re-opened in a fresh Playwright page and a JS
  collector — using the engine's **exact** `getXPath` (including its `id` short-circuit)
  — returns each annotated element's xpath + expected bare code.
- `_check_element_expectations` matches each expectation against the emitted
  `(bare_code, kind, xpath)` set. The fixture passes iff every annotation is satisfied.
- New helpers: `_bare_code`, `_emitted_issues`, `collect_element_expectations`,
  `_check_element_expectations`, and the `_EXPECTATION_COLLECTOR_JS` constant.

### Part B — engine: content-overflow detection (`test_text_contrast.py`)

`checkTextOverflow` previously only detected **box** overflow (the element's layout box
extends beyond its direct parent). It now also detects **content** overflow: on an axis
whose `overflow` is `visible`, `scroll{W,H} − client{W,H} > 1px` means the element's own
text paints beyond its coloured box, so the spilled text sits on an undefined background.
The element itself becomes the overflow container.

**Why it's safe:** content overflow is only consulted inside the existing partial-error
branch, which already requires *computable, failing* contrast. So it only reclassifies an
existing `ErrTextContrastAA` failure into `ErrPartialTextContrastAA`; it never creates a
violation where there wasn't one. Gradient/image text never reaches that branch (its
inside contrast is incomputable → it stays a warning).

### Part C — pilot fixtures

- Added a `test-metadata` block with `enforceElementExpectations: true`.
- Annotated every case with `data-expected-*` on the element the engine flags.
- Cases 1, 2, 10 (and AAA 1, 2) are now genuine partials via the new content-overflow
  detection; cases 4, 5 (and AAA 4, 5) were already box-overflow partials.
- Cases 3, 6, 7, 11 (and AAA 3) had **no real overflow** (their "extends beyond
  container" claims were false), so each was given a genuine overflow (nowrap + width, or
  the box pushed outside its parent) while keeping its mechanism flavour.
- Cases 8, 9 (gradient/image) are annotated `data-expected-warning` →
  `WarnTextContrastCannotCalculate`.

## Validation

- Both pilot fixtures pass per-element enforcement (AA: 9 violations + 2 warnings; AAA:
  5 violations), each matched by xpath.
- `_check_element_expectations` unit-checked to fail on wrong code, wrong xpath, and a
  pass-element that emitted the forbidden code (it is not a no-op).
- Full `Colors` category: 25/25 pass — no `ErrTextContrast{AA,AAA}` fixture lost its code
  or gained an unexpected `Partial`.
- `mypy`, `pyright`, `ty` clean on both edited Python files.

## Rollout note

Enforcement is opt-in per fixture, so the other ~1,170 fixtures are unaffected. Rolling it
out more widely means, per file: add the opt-in flag, ensure annotations match engine
reality, and reconcile any mismatch (fix the fixture or the engine) — exactly the
corrective loop done here. Expect many files' existing annotations to be wrong, since they
have never been enforced.
