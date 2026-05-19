# AudioA11y Video-Processing Integration — Design Spec

**Date:** 2026-05-19
**Branch:** `audioA11y-integration` (off `main`)
**Status:** Draft

## Goal

Port the `pythonAudioA11y` video-processing pipeline into `auto_a11y_python` so that uploading an MP4 audit recording produces an auto-imported `Recording` (with issues, painpoints, takeaways, assertions) without launching a separate tool. The video, the transcripts, and the analysis outputs all live inside auto_a11y. A recording is a self-contained artifact; it is never cross-linked into page/website rendering (that integration was tried on the abandoned `dictaphone-tester-integration` branch and is explicitly out of scope here — future merging happens via a separate reporting layer).

## Non-goals

- Cross-rendering of recording issues inside the tester area, page detail, or website detail. **Forbidden by design.**
- New aggregate counters that fold recording issues into project / website / page rollups.
- Real-time stream processing. Videos are uploaded as whole files; processing is batch.
- Modifying the underlying `audioA11y/src/audioA11y.js` Node project or the `pythonAudioA11y` standalone Python project. Those stay where they are; this branch is a one-direction port.
- A Camtasia XML round-trip (the pythonAudioA11y feature that writes markers back into Camtasia projects). Out of scope.
- Drupal ticket creation. Out of scope; covered by the existing Drupal exporter for finished recordings.

## Current state (verified before writing this spec)

- `auto_a11y/models/recording.py` and `recording_issue.py` exist and carry `page_ids: list[str]`, `page_urls: list[str]`, `discovered_page_ids: list[str]` fields that are **about to be removed** (Phase 0 of this spec). These fields existed before this branch and were the basis for the abandoned cross-page rendering. Removing them enforces the "recordings stay separate" rule at the data layer.
- `auto_a11y/importers/dictaphone_importer.py` (lines 237-243) propagates the soon-to-be-removed fields from the parent `Recording` onto each `RecordingIssue`. That propagation goes away in Phase 0.
- `auto_a11y/web/routes/recordings.py` upload form accepts JSON + HTML content files today; we extend it to also accept MP4 in Phase 7.
- `auto_a11y/web/templates/recordings/upload.html` carries the page-URL textarea and discovered-page checkboxes today; both removed in Phase 0.
- `auto_a11y/core/job_manager.py` runs PDF audits and test scrapes today. We add `JobType.VIDEO_PROCESSING` (Phase 6) and reuse the worker pool, concurrency knob, and orphan-recovery hook.
- `auto_a11y/pdf/storage.py` and `auto_a11y/pdf/runner.py` are the reference patterns for the new `auto_a11y/audio/storage.py` and `runner.py`. The pdfMax effort proved the pattern works.
- `pythonAudioA11y/` (sibling repo at `~/Documents/cnib/code/pythonAudioA11y`) is the **source** for the port: `audio_a11y.py` (374 lines, end-to-end orchestrator), `transcription.py` (Deepgram + diarization), `vtt_processor.py` (merge + speaker remap), `speaker_identification.py` (pyannote.audio + agglomerative clustering), `analysis.py` (Claude analysis × 4 passes × 3 contexts × 2 languages), `audio_processor.py` (ffmpeg silence-aware segmentation), `video_processor.py` (callouts overlay). Plus `DESIGN.md`, `DEEPGRAM_OPTIMIZATIONS.md`, `SPEAKER_REMAPPING.md`. JSON output schema is already compatible with `DictaphoneImporter`.

## Decisions locked in (from brainstorm Q&A)

| Decision | Value |
|---|---|
| Scope | Full port — speaker remapping, NaviLens mode, callouts video output, EN+FR bilingual, `--extended-context` Opus 1M flag |
| Branch base | `main` |
| Dependencies | Required in main `requirements.txt` (no extras group) |
| Job trigger | Auto-process on upload (after cost-estimate confirm step) |
| Storage | Local filesystem under `data/recordings/<recording_id>/` |
| Cost guardrail | Show estimate at upload; no hard limit |
| Progress UI | Live progress page (meta-refresh every 3 s) |
| Speaker remap default | ON (matches pythonAudioA11y default) |
| Claude model | Opus 4.7 |
| Project linkage | Yes — `Recording.project_id` stays; upload form has a project picker |
| Recording ID | Auto-generated, format `REC-YYYYMMDDHHMMSS-{6 hex chars}` |
| Cross-rendering | None. Recordings never appear in tester / page / website views. |
| Desktop-app preflight | Fail-soft via Settings Recovery (Phase 10) |
| Settings restart | User-quit-and-reopen; in-process restart is out of scope |

## Architecture

The pipeline lives as a new top-level Python package `auto_a11y/audio/`, mirroring how `auto_a11y/pdf/` houses the pdfMax port.

```
auto_a11y/
├── audio/                              # NEW
│   ├── __init__.py
│   ├── config.py                       # API-key + model-name loaders (reads from auto_a11y.core.config)
│   ├── ffmpeg.py                       # binary detection (mirrors pdf/ghostscript.py), audio extraction, silence detection
│   ├── segmenter.py                    # 600 s-target silence-aware splitting → ./audio/*.m4a
│   ├── transcription.py                # Deepgram client + per-segment .vtt + .words
│   ├── vtt_processor.py                # merge segments with timestamp offsets, write final captions/<title>.vtt
│   ├── speaker_identification.py       # pyannote.audio embeddings + agglomerative clustering
│   ├── analysis.py                     # Claude Opus 4.7 calls — 4 passes × 3 contexts × 2 languages
│   ├── cost.py                         # ported from audio_a11y.py:9-63 — input/output/cache cost calc
│   ├── prompts/                        # prompt text as separate .txt files (snapshot-tested)
│   │   ├── audit_issues.txt
│   │   ├── audit_painpoints.txt
│   │   ├── audit_takeaways.txt
│   │   ├── audit_assertions.txt
│   │   ├── livedexperience_issues.txt   # + painpoints, takeaways, assertions
│   │   ├── navilens_issues.txt          # + painpoints, takeaways, assertions
│   │   └── heuristics.txt              # shared WCAG/a11y heuristics injected into every prompt
│   ├── callouts.py                     # optional: ffmpeg drawtext overlay → annotated mp4
│   ├── storage.py                      # per-Recording dir layout under data/recordings/<id>/
│   ├── pipeline.py                     # top-level orchestrator: video_path → Recording, sequential A→G
│   └── runner.py                       # async wrapper used by the job system (analogous to pdf/runner.py)
auto_a11y/
└── core/
    └── preflight.py                    # NEW — central check registry, returns PreflightResult
auto_a11y/web/
├── routes/
│   └── recovery.py                     # NEW (Phase 10) — Settings Recovery blueprint
└── templates/
    └── recovery/
        ├── index.html
        ├── check_card.html
        └── settings_form.html
```

Each file does one thing. `pipeline.py` is the only module that knows the stage order; it stitches the stages. Every other module is testable in isolation against fixtures.

**Why a new top-level package, not `auto_a11y/recordings/`:** Recordings is the *domain* (models + routes already under `auto_a11y/models/recording*.py` and `auto_a11y/web/routes/recordings.py`). The audio/video *pipeline* is a separate concern that produces recordings. Mirrors how `auto_a11y/pdf/` separates "audit the PDF" from `auto_a11y/models/pdf_document.py`.

## Data flow

End-to-end from upload click to "issues visible in `/recordings/<id>`":

```
1. POST /recordings/upload  (multipart)
     │  fields: video=<MP4>, title, project_id, recording_type,
     │          audit_context (audit|livedExperience|navilens),
     │          language ([en], [fr], [en,fr]), callouts (bool),
     │          extended_context (bool), skip_speaker_remap (bool),
     │          auditor_name, auditor_role
     ▼
2. recordings.upload_video()
     │  - validate (MIME, size cap, project access, language non-empty)
     │  - ffprobe duration; reject 0-byte / non-video
     │  - auto-generate recording_id  REC-YYYYMMDDHHMMSS-{6 hex}
     │  - PathStorage.allocate(recording_id) → data/recordings/<id>/
     │  - move upload to data/recordings/<id>/source.mp4
     │  - create Recording row (status="uploaded", media_file_path set)
     │  - cost.estimate(duration_s, contexts, languages, extended_context, callouts) → CostEstimate
     │  - persist on Recording.estimated_cost_usd + cost_breakdown
     │  - render confirm page (estimate + edit-config button)
     │  ▼   user clicks "Process now"
     │  - Recording.status="processing"
     │  - JobManager.submit(VIDEO_PROCESSING, payload={"recording_id": rec.id})
     │  - redirect → /recordings/<id>  (progress page renders)
     ▼
3. VideoRunner.run(recording_id)  (audio/runner.py)
     │  Stage A — segmenter.split(source.mp4) → audio/segment-N.m4a + segments.json
     │  Stage B — transcription.transcribe_all(segments) → vtt/segment-N.vtt + .words
     │  Stage C — speaker_identification.remap(vtt/, words/) → captions/<id>.speaker-map.json
     │             (optional; skipped if speaker_remap_enabled=False)
     │  Stage D — vtt_processor.merge(vtt/, speaker-map) → captions/<id>.vtt
     │  Stage E — analysis.run(vtt, context, language) × 4 passes × language(s)
     │             → json/<id>.{issues,painpoints,takeaways,assertions}{,.fr}.json
     │             + html/<id>.{issues,...}{,.fr}.html
     │             (each pass appends to Recording.cost_breakdown and bumps actual_cost_usd)
     │  Stage F — callouts.render(source.mp4, issues.json) → video/<id>.callouts.mp4
     │             (optional; skipped if callouts_requested=False)
     │  Stage G — DictaphoneImporter.import_in_place(recording, json/)
     │             writes recording_issues collection
     │             writes user_painpoints, key_takeaways, user_assertions onto Recording
     │  Stage H — Recording.status="complete", finished_at set
     ▼
4. /recordings/<id>  (live progress while running, full results when done)
```

Each stage writes an atomic on-disk artifact before signalling progress. A crash at any stage leaves a partial directory the user can inspect. **Resume from a partial directory is out of scope for this branch** — a rerun re-does the work.

Progress flows through Mongo: each stage updates `Recording.progress = {"stage": str, "current": int, "total": int, "started_at": dt, "elapsed_ms": int}`. The progress page renders that dict; meta-refresh every 3 s reloads it.

Cancellation: a "Cancel" button on the progress page sets `Recording.status="cancelling"`. The runner checks status at every stage boundary and exits cleanly between stages — never mid-stage.

## Storage layout

Per-recording directory under `data/recordings/<recording_id>/`. Atomic write-then-rename, same pattern as `auto_a11y/pdf/storage.py`. Single source of truth: `audio/storage.py` returns paths via accessors (`vtt_dir(rec)`, `json_path(rec, kind="issues", lang="en")`, etc.). No string concatenation in pipeline modules.

```
data/recordings/REC-20260519143022-a1b2c3/
├── source.mp4                          # the uploaded video (kept; reruns can avoid re-upload)
├── audio/
│   ├── segment-0.m4a
│   ├── segment-1.m4a
│   └── segments.json                   # [{index, start_s, end_s, duration_s}, …] from silence detection
├── vtt/
│   ├── segment-0.vtt
│   ├── segment-0.words
│   ├── segment-1.vtt
│   └── segment-1.words
├── captions/
│   ├── REC-….vtt                      # merged, speaker-remapped, FINAL artifact analysis consumes
│   └── REC-….speaker-map.json
├── json/
│   ├── REC-….issues.json               # dictaphone-shape; imported into Mongo
│   ├── REC-….issues.fr.json
│   ├── REC-….painpoints.json
│   ├── REC-….painpoints.fr.json
│   ├── REC-….takeaways.json
│   ├── REC-….takeaways.fr.json
│   ├── REC-….assertions.json
│   └── REC-….assertions.fr.json
├── html/
│   └── REC-….{issues,painpoints,takeaways,assertions}{,.fr}.html
├── video/
│   └── REC-….callouts.mp4              # only if callouts_requested
├── manifest.json                       # produced-by versions: pipeline version, Anthropic model id, Deepgram model
└── job.log                             # human-readable per-stage timings + retries + costs
```

**Worst-case disk:** 1-hour MP4 audit with callouts ≈ 4 GB. Cleanup is **manual** in this branch (admin deletes via the `/recordings/<id>/delete` route which now cascades to the filesystem). Path-traversal guard: `recording_id` is validated against `^REC-[0-9]{14}-[a-f0-9]{6}$` before any path is constructed.

## Models + DB changes

### Phase 0 cleanup — remove cross-page linking

| Field | Where | Action |
|---|---|---|
| `Recording.page_ids: list[str]` | `auto_a11y/models/recording.py` | **Remove** + `to_dict` / `from_dict` |
| `Recording.page_urls: list[str]` | `auto_a11y/models/recording.py` | **Remove** + `to_dict` / `from_dict` |
| `Recording.discovered_page_ids: list[str]` | `auto_a11y/models/recording.py` | **Remove** |
| `RecordingIssue.page_ids: list[str]` | `auto_a11y/models/recording_issue.py` | **Remove** |
| `RecordingIssue.page_urls: list[str]` | `auto_a11y/models/recording_issue.py` | **Remove** |
| Propagation in importer | `auto_a11y/importers/dictaphone_importer.py:237-243` | **Remove** |
| Upload form fields | `routes/recordings.py:263-290` + `templates/recordings/upload.html` | **Remove** the page-URL textarea + discovered-page checkboxes |
| Edit form | `routes/recordings.py:500-…` + `templates/recordings/edit.html` | **Remove** the page-linking inputs |
| OpenAPI spec | `docs/openapi.yaml` recording schemas | **Update** to match |
| API endpoints | `routes/api/recordings.py` PATCH schemas | **Remove** the fields |
| Tests | `tests/api/test_recordings.py`, `test_recording_content.py` | **Remove** assertions exercising deleted fields |

**Migration script** `scripts/migrate_remove_recording_page_fields.py` runs `$unset` against every doc in `recordings` and `recording_issues`. Idempotent. Logs how many docs were touched. Tidiness, not correctness — the `from_dict` methods simply won't read the fields after the model edits, and Mongo doesn't enforce schema.

### New persisted state on `Recording`

| Field | Type | Purpose |
|---|---|---|
| `source_video_path` | `str \| None` | Relative path under `data/recordings/<id>/` (POSIX, no leading slash). Set on upload. |
| `audit_context` | `Literal["audit","livedExperience","navilens"]` | Default `"audit"`. |
| `analysis_languages` | `list[Literal["en","fr"]]` | At least one element. |
| `extended_context` | `bool` | Whether Opus 1M was used. |
| `speaker_remap_enabled` | `bool` | Default True; user opt-out at upload. |
| `callouts_requested` | `bool` | Did the user request the callouts video? |
| `callouts_status` | `Literal["not-requested","pending","complete","failed"]` | Tracks the optional Stage F separately so the main job can complete even if callouts fail. |
| `status` | `Literal["uploaded","processing","complete","failed","cancelling","cancelled"]` | Replaces ad-hoc state. **Indexed.** |
| `progress` | `dict[str, object] \| None` | `{"stage": str, "current": int, "total": int, "started_at": dt, "elapsed_ms": int}` — overwritten as the runner advances. |
| `estimated_cost_usd` | `float \| None` | Computed at upload. |
| `actual_cost_usd` | `float \| None` | Sum of all Anthropic + Deepgram costs. |
| `cost_breakdown` | `dict[str, object] \| None` | Per-stage `{stage_name: {usd: float, tokens: int, cache_hit_pct: float}}`. |
| `error_message` | `str \| None` | Free-text on `failed`. Stack signature for the operator; truncated for the UI. |
| `manifest_version` | `str` | Pipeline version that produced this recording. Bumped on each release. |
| `started_at` / `finished_at` | `datetime \| None` | Job lifecycle timestamps. |

**No new collections.** A Recording is always 1:1 with its job; storing job state separately would add a join with no upside.

**New index:** `recordings.status` (single field, ascending). The recording-list view filters in-flight jobs by status.

## UI changes

### Upload form — `templates/recordings/upload.html`

Single file input with `accept=".mp4,.json,.html"`. Server inspects MIME + extension and branches:

- **MP4 path:** new processing flow described in Data Flow.
- **JSON / HTML path:** existing dictaphone import — unchanged behaviour.

New fields visible when MP4 is selected:
- Project picker (required; lists projects user has access to)
- Title (required)
- Audit context radio: Audit / Lived experience / NaviLens (default Audit)
- Languages checkbox group: English, French (at least one required)
- Speaker remapping (default checked)
- Extended context (Opus 1M) (default unchecked)
- Render callouts video (default unchecked)
- Auditor name / Auditor role (existing fields)

After "Upload": cost-estimate confirm step shows `Estimated cost: $X.XX (Y min of video, Z Claude passes)` + "Process now" / "Cancel" buttons. No hard limit.

### Recording detail — `templates/recordings/detail.html`

When `Recording.status` ≠ `complete`, the top of the page renders a **progress card** instead of the issue list. When `complete`, the existing issue rendering shows (unchanged) plus a new **cost panel** in the sidebar.

The progress card auto-refreshes via `<meta http-equiv="refresh" content="3">`. When the page first sees `status="complete"`, the meta-refresh is removed and the full results render.

```
┌──────────────────────────────────────────┐
│ Recording: REC-20260519143022-a1b2c3     │
│ "Main Website Screen Reader Audit"       │
├──────────────────────────────────────────┤
│ ╭──────────────────────────────────╮     │
│ │ Status: PROCESSING               │     │
│ │ Stage: Transcribing (3 / 7)      │     │
│ │ ████████░░░░░░░░░░ 42%          │     │
│ │ Elapsed: 4 min 12 s              │     │
│ │ Estimated cost so far: $0.43     │     │
│ │                       [Cancel]   │     │
│ ╰──────────────────────────────────╯     │
└──────────────────────────────────────────┘
```

### Cost panel (sidebar on detail.html, status=complete)

```
Cost
─────────────────
Deepgram:        $0.18
Claude (en):     $0.74
Claude (fr):     $0.71
Speaker ID:      $0.00
Total:           $1.63
Estimated was:   $1.55
```

Each row sourced from `Recording.cost_breakdown`.

### Recording list — `templates/recordings/list.html`

In-flight recordings render a status pill (`PROCESSING 42%`). Recently-failed render `FAILED — view details`. Existing complete rows unchanged.

### What we are NOT changing

- The `/api/v1/recordings/...` REST endpoints — schemas gain optional fields (status, progress, costs).
- No new entry points in the test-result page, page detail, website detail, or testing dashboard.
- The dictaphone JSON import path stays as a fallback for users who run pythonAudioA11y externally.

## Background job system

Reuses `auto_a11y/core/job_manager.py`. New `JobType.VIDEO_PROCESSING`. Same worker pool. Same `MAX_CONCURRENT_JOBS` cap.

`audio/runner.py::VideoRunner.run(recording_id)` is the only module that knows about both the pipeline AND the DB / JobManager. `pipeline.py` is pure over disk paths.

### Cancellation

The runner checks `Recording.status` at every stage boundary. If `"cancelling"`:
1. Finishes the in-flight stage's atomic write (no partial files).
2. Sets `Recording.status="cancelled"`.
3. Returns cleanly. JobManager marks job CANCELLED.

Existing artifacts stay on disk. A future "resume" feature could use them; not in scope.

### Retry strategy

| Stage | Failure mode | Behaviour |
|---|---|---|
| A (segment) | ffmpeg crash | Retry once with verbose logging. Second failure → fail job. |
| B (transcribe) | Deepgram 5xx, timeout | Per-segment retry: 1 s → 2 s → 4 s exponential backoff. Permanent → fail job. |
| B (transcribe) | Deepgram 4xx (key, quota) | Fail immediately. Clear user-facing message. |
| C (speaker remap) | torch / pyannote crash | Fall back to skip-remap path automatically; log warning. Don't fail the whole job. |
| D (merge) | VTT parse error | Fail job (corrupt earlier-stage output). |
| E (analysis) | Anthropic 5xx / overloaded | Retry per `anthropic` SDK defaults (already handles backoff). Permanent → fail job. |
| E (analysis) | Anthropic 4xx | Fail job. Surface message. |
| E (analysis) | Anthropic refusal / safety block | Permanent. Log full prompt + response for the operator. |
| F (callouts, optional) | ffmpeg crash | Set `callouts_status="failed"`; don't fail the whole job. UI degrades gracefully. |
| G (import) | DB write error | Retry once. Permanent → fail job. |

### Orphan recovery on server restart

`JobManager.recover_orphans()` finds Recordings stuck in `status="processing"` and either re-queues them (source.mp4 still exists) or marks them `failed` with reason `"server restart"` (files missing). Same pattern the PDF runner already uses.

## Cost calculation

Port `audio_a11y.py:9-63` into `auto_a11y/audio/cost.py`. Single source of truth for pricing constants.

**Constants** (as of 2026-05-19):

| Token / unit | Rate |
|---|---|
| Opus 4.7 input (≤200K tokens) | $5 / M tokens |
| Opus 4.7 output (≤200K) | $25 / M tokens |
| Opus 4.7 input (>200K, extended context) | $10 / M tokens |
| Opus 4.7 output (>200K, extended context) | $37.50 / M tokens |
| Cache read | 10 % of standard input |
| Cache write | 125 % of standard input |
| Deepgram nova-3 with diarization | $0.0043 / minute |

`cost.py` carries an `AS_OF: date = date(2026, 5, 19)` constant. The cost panel renders "Pricing as of YYYY-MM-DD; rates may have changed." No live API lookup.

**Estimation:**

```python
def estimate(
    duration_s: float,
    contexts: list[Literal["audit","livedExperience","navilens"]],
    languages: list[Literal["en","fr"]],
    extended_context: bool,
    callouts: bool,
) -> CostEstimate:  # frozen dataclass: total_usd, breakdown by stage
```

Token budget heuristic: `tokens ≈ duration_min × 200` (per `DEEPGRAM_OPTIMIZATIONS.md`).

**Actual cost** is the sum of per-call `cost.from_anthropic_response(message)` writes; the running total updates `actual_cost_usd` as the pipeline progresses. The final breakdown appears in the cost panel (Section UI changes).

## i18n

All new user-visible strings via Fluent.

- `auto_a11y/web/translations/en/audio.ftl` — upload form labels, status names, stage descriptions, error messages, cost-panel labels, progress strings.
- `auto_a11y/web/translations/fr/audio.ftl` — direct French (no TODO_FR placeholder).

Estimated count: ~40 new IDs.

**Prompt text itself stays English.** It's the input to Claude, not user-visible. The French analysis pass uses a prompt that *instructs Claude to produce French output*; the prompt itself is in English (matches what pythonAudioA11y does today).

## Testing strategy

**Unit tests** (fast, hermetic):

| Module | Tests against |
|---|---|
| `audio/ffmpeg.py` | Binary detection — mock `subprocess.run`. Silence-detection parser — fixtures from real ffmpeg output. |
| `audio/segmenter.py` | Given a fake silence-detection output, produces the right segment boundaries. No real ffmpeg run. |
| `audio/cost.py` | Pure-function math against known token counts. |
| `audio/vtt_processor.py` | Fixture VTTs → correct merged VTT (timestamps offset, speakers remapped). |
| `audio/storage.py` | Path generation, atomic write semantics, traversal guards. |
| `audio/prompts/*.txt` | Snapshot tests: each prompt ends with `END_OF_PROMPT` marker and matches a hash captured at commit time. |
| `audio/analysis.py` | Mock Anthropic SDK; assert prompts sent with right context / language / heuristics. |

**Integration tests** (require real binaries / network):

| Test | Marked with | Skipped when |
|---|---|---|
| `test_ffmpeg_extracts_audio_from_fixture_mp4` | `@pytest.mark.ffmpeg` | `which ffmpeg` returns nothing |
| `test_deepgram_transcribes_short_clip` | `@pytest.mark.network` + `@pytest.mark.deepgram` | `DEEPGRAM_API_KEY` not set |
| `test_anthropic_returns_dictaphone_shape` | `@pytest.mark.network` + `@pytest.mark.anthropic` | `ANTHROPIC_API_KEY` not set |
| `test_full_pipeline_against_30s_fixture` | `@pytest.mark.slow` | any of the above |

Pre-commit hook runs **only unit tests**. CI runs `@pytest.mark.ffmpeg` (ffmpeg installed in CI) but skips network ones unless secrets are wired up. Contributors running plain `pytest` see only unit tests.

**Web/route tests** (Mongo + Flask test client; mirror Phase 3 of the abandoned dictaphone-tester-integration's pattern):

- `test_upload_video_creates_recording_and_submits_job`
- `test_upload_estimates_cost_correctly`
- `test_progress_page_renders_each_stage_label`
- `test_cancel_cleanly_transitions_status`
- `test_orphan_recovery_on_startup`

**Fixtures.** A 30-second `tests/audio/fixtures/short_audit.mp4`. Decision deferred to Phase 1: probably a `scripts/fetch_audio_fixtures.py` that downloads from a known URL on first run (avoids LFS).

## Error handling

Already captured in the retry-strategy table. Additions:

**Validation at upload (before storage allocation):**
- MIME / extension check (`.mp4` only; `application/octet-stream` allowed with extension fallback).
- Size cap (default 5 GB, configurable). Hard fail above → 413 + clear message.
- Duration check via `ffprobe`. Rejects 0-byte / non-video files (~100 ms cost).
- At least one language selected.
- Project exists and user has write access.

**Surfacing failures.** `Recording.error_message` is free-text. The detail page renders it inside a `role="alert"` region when `status="failed"`. No stack traces in the UI; the full traceback is logged to `job.log` on disk and the application logger.

## Desktop-app fail-soft recovery

A first-class section because the audioA11y dependencies (ffmpeg, Deepgram key, Anthropic key, pyannote.audio) make startup failure modes acute.

### Failure classification at startup

| Class | Examples | App behaviour |
|---|---|---|
| Recoverable-via-settings | Bad / missing API keys, ffmpeg not in PATH, unreachable `MONGODB_URI`, write-protected `data/` dir | Boot into **Settings Recovery** screen instead of the dashboard. Server runs only enough to serve the recovery UI. |
| Recoverable-via-install | `pyannote.audio` / `torch` not importable | Recovery screen with "speaker remap will be unavailable — continue without?" option that flips `Recording.speaker_remap_enabled=False` defaults and proceeds. |
| Soft-degraded | Optional binary missing (e.g., MP4Box for chapter embedding) | App boots normally; the dependent feature is disabled in the UI with a tooltip. |
| Truly fatal | Cannot bind to configured port, write-protected user-settings folder with no fallback | Crash dialog with copy-able diagnostic + "Open settings folder" button. App exits. |

### Settings Recovery screen

Minimal Flask blueprint at `auto_a11y/web/routes/recovery.py`:
- ONLY blueprint registered when preflight fails. Every other URL 302s to `/recovery/`.
- Shows each failed check: ✗ icon, plain-English description, current value, edit form.
- "Test" button per setting (hits dedicated endpoints, reports inline).
- "Save settings" writes to user-settings file then displays "Settings saved. Please quit and reopen the application." **No in-process restart** (user-quit-and-reopen is the model).

### Where settings live

Order of precedence (highest wins):

1. **User settings file** (writeable by recovery UI): `~/.config/auto_a11y/settings.json` (Linux/macOS); `%APPDATA%\auto_a11y\settings.json` (Windows). New file, owned by the desktop wrapper, gitignored.
2. **`.env`** (devs and Docker users).
3. **Built-in defaults**.

The desktop app never ships with a `.env` baked in. Recovery UI writes only to layer (1). Developers running Flask directly use `.env`; recovery UI doesn't appear unless preflight fails in the desktop bundle.

### Cross-platform binary paths

Desktop app bundles ffmpeg / ffprobe inside the .app / .exe (existing `audioA11y/src/audioA11y.js:48-50` uses `@ffmpeg-installer/ffmpeg` — we'll do the equivalent via PyInstaller hooks). Preflight prefers bundled binary; if missing or unrunnable (Gatekeeper, AV-quarantine), recovery UI prompts the user to point at a system-installed ffmpeg. Same for MP4Box.

For the Python desktop bundle, bundled binaries live under `Contents/Resources/bin/` (macOS) or `_internal/bin/` (Windows). `ffmpeg.detect()` checks those locations first, then PATH.

### In scope here vs. deferred

In scope this branch:
- Preflight check module (`auto_a11y/core/preflight.py`) — `Check` and `PreflightResult` dataclasses, central registry, called at startup.
- `audio/ffmpeg.py` + `audio/config.py` register their checks via the preflight registry.
- Recovery blueprint + templates + test endpoints (ffmpeg, ffprobe, Mongo, Deepgram, Anthropic).
- User-settings file read/write helper.
- 302-everything-else-to-recovery middleware.

Out of scope:
- Bundling new binaries into the desktop build (separate sub-project; .pkg / .dmg / .exe wrappers).
- Auto-update mechanism for ffmpeg.
- A "Diagnostics" page accessible from the running app.

## Phase breakdown

Twelve phases. Each ends in a commit; phases are sequenced so every commit leaves the branch green.

| # | Phase | What lands |
|---|---|---|
| 0 | Cleanup | Remove `page_ids` / `page_urls` / `discovered_page_ids` from models, importer, upload + edit forms, OpenAPI, REST schemas, existing tests. Migration script `scripts/migrate_remove_recording_page_fields.py`. |
| 1 | Foundation | `auto_a11y/audio/` package skeleton (`__init__.py`, `config.py`, `storage.py`, `ffmpeg.py` detection). `auto_a11y/core/preflight.py` (`Check`, `PreflightResult`, registry). |
| 2 | Audio extraction + segmentation | `audio/segmenter.py` — silence-aware splitting (600 s target, ±30 s window). Tests against fixture ffmpeg output + a real 30-second MP4 (`@pytest.mark.ffmpeg`). |
| 3 | Deepgram transcription + VTT | `audio/transcription.py` (Deepgram nova-3, diarization on, retry 1 s → 2 s → 4 s) and `audio/vtt_processor.py` (per-segment VTT, merge with offsets, `.words` metadata). |
| 4 | Speaker remap | `audio/speaker_identification.py` (pyannote.audio + agglomerative clustering @ 0.7 cosine; `--skip-speaker-remap` short-circuit). Adds `torch`, `torchaudio`, `pyannote.audio`, `scikit-learn` to `requirements.txt`. Optional `HF_TOKEN`. |
| 5 | Claude analysis + prompts + cost | `audio/analysis.py` (4 passes × 3 contexts × 2 languages; Opus 4.7; `extended_context` toggles betas). `audio/prompts/{audit,livedExperience,navilens}_{issues,painpoints,takeaways,assertions}.txt` + `heuristics.txt` (snapshot-tested). `audio/cost.py` with `AS_OF` constant and frozen `CostEstimate` dataclass. |
| 6 | Pipeline + runner + JobManager | `audio/pipeline.py` (A → G stage orchestration over disk paths; pure logic). `audio/runner.py` (`VideoRunner`). `JobType.VIDEO_PROCESSING`. `JobManager.recover_orphans()` extension. New `Recording` fields. Index on `recordings.status`. |
| 7 | Upload form + cost estimate | `templates/recordings/upload.html` accepts MP4 + cost-estimate confirm step. Auto-generated `recording_id`. Validation (MIME, size cap, ffprobe duration). Auto-submit job on confirm. |
| 8 | Progress page + recording detail + cancel | `_progress_card.html` partial (stage + percent + elapsed + running cost; meta-refresh 3 s). Status pill on list view. Cancel button → `status="cancelling"`. Cost panel on detail page when complete. |
| 9 | Callouts video output | `audio/callouts.py` (ffmpeg `drawtext` overlays + chapter embedding via MP4Box-with-ffmpeg-fallback). Download link on detail page. |
| 10 | Settings Recovery | `auto_a11y/web/routes/recovery.py` + templates. Test endpoints. User-settings file at `~/.config/auto_a11y/settings.json`. 302-everything-else middleware. Tests at `tests/web/test_recovery_flow.py`. |
| 11 | Final verification + manual walkthrough | Full pytest run, type checks, css-a11y. End-to-end walkthrough with a 30-second fixture MP4. Screen-reader pass on upload + progress page. |

Phase 0 first so the new code never accidentally relies on the dead linking model.

Estimated commits on branch: ~50 (each phase typically 3-5 TDD commits + code-review followups).

Estimated calendar effort: several days of focused work.

## Risk + rollout

- **Heavy dependencies.** `torch` (~2 GB) and `pyannote.audio` make `pip install` slower. Accepted (user picked "required, in main requirements.txt").
- **Cost guardrails.** Estimate is informational, not enforced. A user uploading a 4-hour video accidentally could incur ~$10+ in API costs. Documented in upload UI; no further mitigation.
- **API keys for tests.** Network-marked tests are skipped without keys. CI without keys passes; CI with keys exercises the full integration test surface.
- **Disk-space accumulation.** No automatic cleanup. Tracked as a follow-up sub-project ("Recording artifacts retention policy").
- **Single-host filesystem.** No object-storage support. Acceptable for an on-prem accessibility-testing tool; future cloud deployment would need a separate effort.

## Branch + commit policy

- Branch: `audioA11y-integration` off `main`.
- **Never** `--no-verify`, **never** `git commit --amend`, **never** rewrite history.
- Use `--no-gpg-sign` per local convention.
- Activate `.venv` before every commit so pyright resolves third-party deps (known project gotcha).
- Pre-commit hook runs mypy / pyright / ty / css-a11y / openapi drift / translation coverage. All must pass.

## Open follow-ups (out of scope for this spec)

- **Resume from partial output directory.** If Stage E partially completed, a rerun should pick up from the missing JSON files instead of redoing transcription. Designed but not built.
- **Persisted disk-usage rollup on the recording-list page.** "Free up 23 GB by deleting these 8 oldest recordings."
- **Background re-process button** that uses a different model / context without re-uploading the MP4.
- **Camtasia XML round-trip** (markers + callouts back into the source project).
- **Drupal ticket creation per issue** — the existing Drupal exporter handles whole recordings; per-issue export is a future enhancement.
- **Object storage (S3 / Azure / GCS)** for `source.mp4` and the artifacts directory. Required if auto_a11y ever runs in a multi-host or autoscaling cloud deployment.
- **NaviLens scoring rubric and best-practice catalogue.** Today the prompt just labels things "NaviLens Best Practice"; a structured catalogue would make those issues searchable.
