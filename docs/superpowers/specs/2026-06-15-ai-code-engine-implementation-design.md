# Wiring the AI analyzer to emit the fixtures' codes — design

**Date:** 2026-06-15
**Status:** Approved (design)
**Author:** audit follow-up to `Fixtures/FIXTURE_AUDIT_2026-06-15.md`

## Problem

The 2026-06-15 fixture audit found that **117 of 124 `AI_` fixtures reference detection
codes the AI analyzer cannot emit.** The analyzer (`auto_a11y/ai/analysis_modules.py`) hard-codes
a fixed vocabulary of 22 AI codes across 6 prompt modules; the fixtures use 62 distinct AI codes,
of which only 3 overlap. The mismatch is invisible because every AI fixture runs `ai-skipped`
(no `CLAUDE_API_KEY` in CI), and the codes have no emission path even *with* a key.

This design wires the engine to emit the codes the fixtures expect, so the fixtures become real
coverage when AI analysis runs in production.

## Decisions (locked)

- **Depth:** full pipeline — analyzer prompt + per-code touchpoint + `ISSUE_CATALOG.md` + rich
  `issue_descriptions_enhanced.py` entry + **EN and FR** Fluent translations.
- **Coverage:** implement the 53 codes that genuinely need vision/AI; do **not** wire the 6
  codes that are reliably DOM-determinable.

## How the AI engine works (established by reading the source + spec review)

- `analysis_modules.py` defines analyzer classes; each holds a Claude prompt that lists `ISSUE
  CODES` and a JSON `issues[]` shape (20 distinct codes across 6 prompts today). There is **no
  whitelist** — `ClaudeAnalyzer._create_finding()` emits whatever `err` string the model returns
  and wraps it in a `Violation`.
- `claude_analyzer.py` instantiates the analyzers, runs the enabled ones in `analyze_page()`,
  and maps each finding to a touchpoint via `ai_to_touchpoint_map` (keyed by **analysis type**).
- An analysis type runs when it appears in the analysis lists that drive the runner. The real edit
  sites are **four**, distinct in purpose (verified):
  - default lists passed to `analyze_page(analyses=…)`: `test_runner.py:709` and `:1030`;
  - per-project `set_ai_test_enabled` config loops: `test_runner.py:591` and `:942`.
  (`:1485` is a call-site, not a list; the `ai_tests_to_run = project.config.get('ai_tests', [])`
  reads at 692/1028/1463 pull from project config.)
- `test_config.py` registration is **optional for emission**: `is_ai_test_enabled` returns
  default-True for unknown analysis types (`test_config.py:256`). Editing `ai_tests.analyses` only
  affects the enable/disable UI and the config-mapping loops — new types already run without it.
- Unregistered codes do **not** crash: `get_detailed_issue_description()` derives readable text
  from the code name. Registration improves report/UI quality; it is not required for emission.

## Architecture

### Module organization — 5 new analyzers + 7 extensions

Each analyzer remains a single focused unit (prompt in → `issues[]` out), matching the existing
pattern. New analyzer classes go in `analysis_modules.py`; each is instantiated in
`ClaudeAnalyzer.__init__`, given a task in `analyze_page()`, registered in the
`ai_tests_to_run` lists and `test_config.py`, and added to the per-code touchpoint map.

New analyzers: `WidgetARIAAnalyzer` (custom widgets without ARIA), `LandmarkAIAnalyzer`
(redundant role on native landmark + missing label), `MediaAnalyzer` (video/audio captions &
transcripts), `LiveRegionAnalyzer` (dynamic announcements), `StructuralAnalyzer` (skip link, time
limit, complex table).

Extensions add `ISSUE CODES` lines + JSON examples to existing prompts: `HeadingAnalyzer`,
`ReadingOrderAnalyzer`, `ModalAnalyzer`, `LanguageAnalyzer`, `AnimationAnalyzer`,
`InteractiveAnalyzer`.

Note: `modals` and `animations` already exist as analyzers but are **absent from the default
`ai_tests_to_run` list** (`['headings','reading_order','language','interactive']`). Part of this
work is adding every analysis type — existing and new — to those lists so all AI codes actually run.

### Engine refinement: per-code touchpoint resolution

`WidgetARIAAnalyzer` emits codes spanning `event_handling`, `forms`, and `navigation`, so a
single per-analyzer touchpoint is insufficient. **Reuse the existing per-code map** rather than
inventing a new one: `core/touchpoints.py` already has `ERROR_CODE_TO_TOUCHPOINT` (consulted via
`get_touchpoint_for_error(code)`) and already maps two AI codes
(`AI_InfoContentOrder`, `AI_WarnPossibleReadingOrderIssue` → `HEADINGS`, `touchpoints.py:576-577`).
Plan:
- Add all 53 codes to `ERROR_CODE_TO_TOUCHPOINT` (single source of truth).
- In `_create_finding()`, consult `get_touchpoint_for_error(issue_code)` first, then fall back to
  the per-analysis-type `ai_to_touchpoint_map`.
- **Reconcile the conflict:** the existing two entries say `HEADINGS`; the draft table said
  `focus_management`. Adopt the existing `HEADINGS` mapping for those two (don't fight the existing
  source of truth) and note it; everything else uses the table's touchpoint.

Every code maps to an **existing** `TouchpointID` — no new touchpoints are introduced.

### `result_processor.py` — AI "checks performed" surface

`result_processor.py` has its OWN per-analysis-type maps that the analyzer's touchpoint does not
feed: two `ai_to_touchpoint_map` dicts (`:272`, `:319`), `check_descriptions` (`:293`, `:338`), and
`_get_ai_wcag_criteria` (`:648`), all keyed by analysis_type. For the 5 NEW analysis types these
return defaults, degrading the report's "checks performed" summary and WCAG attribution (issue
grouping itself is fine — it uses the Violation's own touchpoint). Add the 5 new analysis types to
these maps (description + representative WCAG per analyzer).

### Per-code registration — TWO translation pipelines (full pipeline)

The codebase has **two independent translation pipelines** (this was the spec review's biggest
correction):
- **Report pipeline:** `issue_descriptions_enhanced.py` (EN source of truth) +
  `auto_a11y/reporting/issue_translations_fr.json` (FR, keyed by full code with
  `title/what/why/who/remediation/what_generic`). French *reports* read the JSON, NOT the FTL; a
  missing JSON key silently falls back to English (`issue_descriptions_translated.py:255`).
- **UI pipeline:** `auto_a11y/web/translations/{en,fr}/issues.ftl`, message-id = the full code with
  `.title/.what/.why/.who/.remediation/.what-generic` attributes (no `impact`/`wcag`).
  These carry an "auto-generated" header, but the generator script
  (`scripts/migrate_issues_to_ftl.py`) **no longer exists**, so they are now hand-maintained —
  author the new entries directly, matching the existing format.

For each of the 53 codes:
1. `ISSUE CODES` line + JSON example in its analyzer prompt (detection intent sourced from the
   code's own fixture pair: `_001_violations` shows the failure, `_002_correct` shows the fix).
2. Entry in `ERROR_CODE_TO_TOUCHPOINT` (`core/touchpoints.py`).
3. `ISSUE_CATALOG.md` entry (ID/Type/Impact/WCAG/Touchpoint/Description/Why/Who/How).
4. **Report EN:** `issue_descriptions_enhanced.py` entry
   (`title`/`what`/`why`/`who`/`impact`/`wcag`/`remediation`), reusing `{element_tag}`/`{element_text}`
   placeholders where useful.
5. **Report FR:** matching entry in `issue_translations_fr.json` (accurate French, not a copy).
6. **UI EN + FR:** `en/issues.ftl` + `fr/issues.ftl` entries (accurate French).

~10 codes already have partial registration across these surfaces; those are completed, not
duplicated. `inline-issues.ftl` (text-hash-keyed, also formerly generated from `enhanced.py`) is a
secondary inline surface — note but not required for a code to function.

## Codes to implement (53)

Source of truth: `/tmp/fixaudit/impl_plan.json` (regenerable). Touchpoints are existing
`TouchpointID` values.

#### Animation (extend)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_ErrFlashingContent` | Err | animation | 2 |
| `AI_ErrMotionWithoutControl` | Err | animation | 2 |

#### Heading (extend)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_ErrSkippedHeading` | Err | headings | 4 |

#### Interactive (extend)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_ErrMissingFocusIndicator` | Err | focus_management | 2 |

#### LandmarkAI (new)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_ErrLandmarkWithoutLabel` | Err | landmarks | 2 |
| `AI_WarnBannerRoleOnHeader` | Warn | landmarks | 2 |
| `AI_WarnComplementaryRoleOnAside` | Warn | landmarks | 2 |
| `AI_WarnContentinfoRoleOnFooter` | Warn | landmarks | 2 |
| `AI_WarnFormRoleOnForm` | Warn | landmarks | 2 |
| `AI_WarnMainRoleOnMain` | Warn | landmarks | 2 |
| `AI_WarnNavigationRoleOnNav` | Warn | landmarks | 2 |
| `AI_WarnRegionWithoutLabel` | Warn | landmarks | 2 |

#### Language (extend)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_WarnMixedLanguage` | Warn | language | 2 |

#### LiveRegion (new)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_ErrAlertWithoutARIA` | Err | event_handling | 2 |
| `AI_ErrFormErrorNotAnnounced` | Err | forms | 2 |
| `AI_ErrLoadingStateNotAnnounced` | Err | event_handling | 2 |
| `AI_ErrMissingLiveRegion` | Err | event_handling | 2 |
| `AI_ErrNotificationWithoutARIA` | Err | event_handling | 2 |

#### Media (new)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_ErrAudioWithoutTranscript` | Err | videos | 2 |
| `AI_ErrAutoplayMedia` | Err | videos | 2 |
| `AI_ErrVideoWithoutCaptions` | Err | videos | 2 |
| `AI_WarnVideoWithoutCaptions` | Warn | videos | 2 |
| `AI_WarnVideoWithoutTranscript` | Warn | videos | 2 |

#### Modal (extend)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_ErrDialogWithoutARIA` | Err | dialogs | 1 |
| `AI_ErrModalFocusTrap` | Err | dialogs | 2 |
| `AI_ErrModalWithoutARIA` | Err | dialogs | 2 |
| `AI_WarnModalMissingLabel` | Warn | dialogs | 2 |
| `AI_WarnModalWithoutFocusTrap` | Warn | dialogs | 2 |

#### ReadingOrder (extend)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_InfoContentOrder` | Info | headings | 1 |
| `AI_InfoVisualCue` | Info | headings | 2 |
| `AI_WarnPossibleReadingOrderIssue` | Warn | headings | 1 |

#### Structural (new)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_ErrMissingSkipLink` | Err | navigation | 2 |
| `AI_ErrTimeLimitNoWarning` | Err | timers | 2 |
| `AI_WarnTableWithComplexStructure` | Warn | tables | 2 |

#### WidgetARIA (new)
| Code | Kind | Touchpoint | Fixtures |
|---|---|---|---|
| `AI_ErrAutocompleteWithoutARIA` | Err | forms | 2 |
| `AI_ErrBreadcrumbsWithoutARIA` | Err | navigation | 2 |
| `AI_ErrCardWithoutARIA` | Err | event_handling | 2 |
| `AI_ErrCheckboxGroupWithoutARIA` | Err | forms | 2 |
| `AI_ErrDatePickerWithoutARIA` | Err | forms | 2 |
| `AI_ErrDisclosureWithoutARIA` | Err | event_handling | 2 |
| `AI_ErrFeedWithoutARIA` | Err | event_handling | 2 |
| `AI_ErrMeterWithoutARIA` | Err | event_handling | 2 |
| `AI_ErrPaginationWithoutARIA` | Err | navigation | 2 |
| `AI_ErrProgressBarWithoutARIA` | Err | event_handling | 2 |
| `AI_ErrRadioGroupWithoutARIA` | Err | forms | 2 |
| `AI_ErrSearchWithoutARIA` | Err | forms | 2 |
| `AI_ErrSliderWithoutARIA` | Err | forms | 2 |
| `AI_ErrSpinbuttonWithoutARIA` | Err | forms | 2 |
| `AI_ErrTabsWithoutARIA` | Err | event_handling | 2 |
| `AI_ErrToggleWithoutARIA` | Err | event_handling | 2 |
| `AI_ErrToggleWithoutState` | Err | event_handling | 2 |
| `AI_ErrTreeViewWithoutARIA` | Err | event_handling | 2 |
| `AI_WarnSearchRoleOnForm` | Warn | forms | 2 |

## Codes NOT implemented as AI (6 DOM-redundant)

| Code | Why not AI | Disposition |
|---|---|---|
| `AI_ErrMissingPageTitle` | DOM `ErrNoPageTitle` (test_page.py) already detects it | Remap fixture → DOM code |
| `AI_ErrIframeWithoutTitle` | DOM `ErrIframeWithNoTitleAttr` (test_title_attribute.py) | Remap fixture → DOM code |
| `AI_ErrZoomDisabled` | static `<meta viewport user-scalable=no>` | Quarantine fixture; recommend new DOM test |
| `AI_ErrOrientationLocked` | static CSS/meta orientation lock | Quarantine fixture; recommend new DOM test |
| `AI_ErrTargetSizeTooSmall` | computed geometry, measurable in-browser (SC 2.5.8) | Quarantine fixture; recommend new DOM test |
| `AI_ErrTextSpacingIssue` | CSS-override probe (SC 1.4.12) | Quarantine fixture; recommend new DOM test |

These 6 dispositions touch **fixtures**, not the engine. They will be done only after explicit
confirmation, separately from the engine work, and are out of scope for the core implementation.

## Files touched

Engine / wiring:
- `auto_a11y/ai/analysis_modules.py` — 5 new analyzer classes; 6 extended prompts.
- `auto_a11y/ai/claude_analyzer.py` — instantiate + task-wire new analyzers; per-code touchpoint
  resolution via `get_touchpoint_for_error`; fallback codes for new analysis types.
- `auto_a11y/core/touchpoints.py` — add 53 codes to `ERROR_CODE_TO_TOUCHPOINT` (single source of
  truth for per-code touchpoint).
- `auto_a11y/testing/result_processor.py` — add 5 new analysis types to the two
  `ai_to_touchpoint_map` dicts, `check_descriptions`, and `_get_ai_wcag_criteria`.
- `auto_a11y/testing/test_runner.py` — add all analysis types to the 4 edit sites (709, 1030
  default lists; 591, 942 `set_ai_test_enabled` loops).
- `auto_a11y/config/test_config.py` — register new analysis types in `ai_tests.analyses`
  (UI-completeness; not required for emission).
- `auto_a11y/config/touchpoint_tests.py` — verify/extend `TOUCHPOINT_TEST_MAPPING` if the
  fixture-status/test-enumeration UI must surface the new codes.

Content / registration (×53):
- `ISSUE_CATALOG.md` — 53 entries (complete the ~10 partial).
- `auto_a11y/reporting/issue_descriptions_enhanced.py` — 53 rich EN entries (report source of truth).
- `auto_a11y/reporting/issue_translations_fr.json` — 53 FR report entries (load-bearing for French
  reports; absent ⇒ silent English fallback).
- `auto_a11y/web/translations/en/issues.ftl` + `fr/issues.ftl` — 53 EN + 53 FR UI entries
  (hand-authored; generator removed).

## Build & verification approach

- **Structural edits** (analyzer classes, orchestrator wiring, config, touchpoint map) done
  directly and held in one reviewable change.
- **Per-code content** (catalog + description + EN/FR translation, ×53) fanned out with a Workflow:
  one agent per code, each reading its fixture pair to author accurate detection intent,
  description, and an accurate French translation. Output is integrated by the main loop.
- **Verification** (no API key here, so the fixtures are the spec):
  - Static completeness check (a small script): every one of the 53 codes appears as an `err` in a
    prompt module; and is present as a key in `ERROR_CODE_TO_TOUCHPOINT`, `ISSUE_CATALOG.md`,
    `issue_descriptions_enhanced.py`, `issue_translations_fr.json`, `en/issues.ftl`, and
    `fr/issues.ftl`. **Note:** `tests/validate_translations.py` only checks EN-vs-FR *FTL* parity —
    it does NOT cover `issue_translations_fr.json`, so the script must assert the FR-report JSON key
    explicitly or French reports can leak English while the validator still passes.
  - `python tests/validate_translations.py` passes (EN/FR FTL parity).
  - `mypy` + `pyright` + `ty` strict all green.
  - AI fixtures remain `ai-skipped` in this environment; passing them requires a manual run with
    `CLAUDE_API_KEY`, called out in the PR.

## Risks / non-goals

- Detection *quality* of the new prompts cannot be validated without an API key; the fixtures
  define expected behavior and the prompts are written to match them. A keyed run is a follow-up.
- Not changing the AI fixtures (other than the 6 DOM dispositions, gated on confirmation).
- Not adding new touchpoints, new WCAG mappings beyond what each code needs, or refactoring
  unrelated analyzer code.
