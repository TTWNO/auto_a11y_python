# OpenAPI Schema Tightening TODOs

This inventory tracks `dict[str, object]` and `RootModel[dict[...]]`
placeholders in `auto_a11y/web/api/schemas/`. The OpenAPI rollout (#27)
preserved wire shapes byte-for-byte; modeling every nested field was
deferred so the rollout could land on a single branch.

Each entry below names a schema, the placeholder field, why it's loose,
and what would need to happen to tighten it. Tightening these is safe
to do incrementally — each change is one file plus a spec regen.

## Free-form request bodies (RootModel placeholders)

These accept arbitrary keys to preserve legacy 400 error envelopes
(`unknown_field`, `invalid_type`, `invalid_value`) that Pydantic's
`extra='forbid'` / `Literal` / typed validators would emit differently
(`extra_forbidden`, `literal_error`). Tightening means accepting the
envelope change.

| File | Class | Endpoint | Why loose |
|---|---|---|---|
| `admin.py` | `GenericSectionPatchIn` | PATCH `/admin/settings/<section_id>` | Per-section field set comes from `CONFIG_SECTIONS`; would force a discriminated-union model per section type. |
| `orchestration.py` | `TestingConfigPatchIn` | PUT `/testing/config` | Same: per-key validators emit `unknown_field` envelopes. |
| `recordings.py` | `RecordingIssuePatchIn` | PATCH `/recording-issues/<id>` | Custom error codes for `status`/`severity` enum values. |

## Free-form response bodies (RootModel)

| File | Class | Endpoint | Why loose |
|---|---|---|---|
| `test_runs.py` | `TestResultOut` | GET `/test-results/<id>` and list endpoints | Raw `TestResult.to_dict()` — deeply nested violations, AI findings, JS results, page state. Modeling would duplicate `auto_a11y/models/test_result.py` plus `violation.py` plus `ai_finding.py`. |

## Nested fields typed as `dict[str, object]` or `list[dict[str, object]]`

### projects.py (§5.1)
- `ProjectDictOut.lived_experience_testers`, `.test_supervisors`,
  `.members`, `.config`, `.statistics` — all `dict[str, object]` or
  `list[dict[str, object]]`. Modeling these would duplicate dataclasses
  in `auto_a11y/models/project.py`. Members are now typed by F1c's
  `people.py`; the project GET still serializes via `project.to_dict()`.

### pages.py (§5.3)
- `PageViolationsOut.violations`, `.warnings`, `.info`, `.discovery`,
  `.ai_findings` — `list[dict[str, object]]` because each item is a
  `Violation.to_dict()` or `AIFinding.to_dict()` payload. Would require
  modeling `Violation` (`category`, `code`, `xpath`, `html`, `impact`,
  `wcag_criteria`, `wcag_levels`, plus optional AI fields) and
  `AIFinding`.
- `PageMatrixIn.combinations` and `PageMatrixOut.combinations` —
  `list[dict[str, str]]`. The map keys are arbitrary script IDs;
  Pydantic can't constrain value enums on a free-form map at schema
  generation. Value-level enum (`before|after|none`) is enforced by
  `_validate_matrix_combinations` at runtime.
- `DiscoveredPage*.document_links` — `list[dict[str, object]]`; legacy
  parser only does shape-presence validation.

### test_users.py (followup F1b)
- `TestUserIn.additional_steps`, `TestUserOut.additional_steps` —
  `list[dict[str, object]]`. Each step is a `{action, selector?,
  wait_ms?, value?, ...}` object — modeling would require a
  discriminated union by `action` field.

### test_runs.py (§5.4)
- `TestResultListOut.results` — `list[dict[str, object]]`; same reason as
  `TestResultOut` above.
- `TestResultCompareOut.comparison` — `dict[str, object]`; deeply nested
  two-summary + three-violation-list shape.
- `TestStateEntryOut.page_state`, `PageTestSessionStateOut.page_state` —
  `dict[str, object]`; keys depend on what triggered the state.
- `PageTestStatesOut.states` — `dict[str, TestStateEntryOut]` keyed by
  stringified integers (Flask JSON encoder coerces).

### drupal.py (§5.12)
- `DrupalSyncStatusOut.issues_by_severity` etc. — typed `DrupalSyncBucketCounts`
  but the action endpoints'  `errors: list[dict[str, object]]` is loose
  because upstream Drupal occasionally returns non-string `error` values.

### admin.py (§5.14)
- `SettingsSectionOut.values` — `dict[str, object]`; per-section
  field set varies (see GenericSectionPatchIn note above).

### fixtures.py (§5.16)
- `FixtureTestStatusEntry.notes` — narrowed to `Optional[str]`; OK.
- Bulk view is `dict[str, FixtureTestStatusEntry]` keyed by error code;
  this is fine — well-typed despite the dict.

## Tightening checklist

When tightening any of the above:
1. Read the legacy serializer (`<Model>.to_dict()`) to lift the exact
   key set.
2. Define a typed nested `StrictModel` in the same file.
3. Replace the `dict[str, object]` field with the new model.
4. Run `pytest tests/api/` to confirm no wire breakage.
5. Run `scripts/generate_openapi.py` and inspect the diff in
   `docs/api/openapi.yaml` — the field should now appear as a `$ref`.
6. Type-check with mypy + pyright + ty.

## Out of scope for this doc

- Adding new endpoints (covered by route plan).
- Tightening input validation that would change the 400 envelope shape
  (a separate v2 breaking change).
