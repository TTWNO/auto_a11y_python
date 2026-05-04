---
title: PDF Viewer issue-card detail parity + project/site PDF rollup
date: 2026-05-01
status: draft
branch: pdfmax-integration
related:
  - docs/superpowers/specs/2026-04-24-pdf-audit-engine-port-design.md
---

# PDF Viewer issue-card detail parity + project/site PDF rollup

## Background

Two gaps in the in-progress pdfMax → auto_a11y absorption:

1. The right-hand issues panel on `/pdfs/<id>` shows a strict subset of
   the fields that the pdfMax Electron app's Viewer tab shows. The
   underlying `*_issue_map.json` already carries every field; the
   regression is purely in the JS card builder.
2. PDF documents do not contribute to the violation / warning totals
   shown on the website-detail and project-detail pages. HTML pages
   roll up; PDFs are reported only as a status-bucketed count badge.
   Per-PDF totals are never persisted — they are computed on demand
   from the cached issue-map JSON for the per-PDF page — so the only
   change needed is teaching the website / project aggregation passes
   to do the same on-demand parse and add to the global totals.

Both changes land on `pdfmax-integration`. No new branch.

## Goals

- The Viewer right-hand panel renders the same fields, in the same
  shape, as the pdfMax Electron Viewer tab — with one targeted
  adaptation for the "View in report" button (auto_a11y has no
  sibling tab; the button cross-page-navigates to the markdown report
  route and scrolls to the matching check).
- Website-detail and project-detail aggregation totals
  (`total_violations`, `total_warnings`) include audited PDFs.
- No new persisted fields. The issue-map JSON remains the single
  source of truth.

## Non-goals

- Not introducing a `violation_count` / `warning_count` field on
  `PdfDocument`, in `pdf_documents`, or on the PDF audit
  `TestResult`. Those counts stay derived.
- Not merging the auto_a11y rich report and the pdfMax markdown
  report. They continue to disagree by design and are presented
  side-by-side.
- Not touching how the PDF viewer overlays themselves are drawn on
  the canvas. Connector lines, semantic layer, overlap grouping —
  unchanged.
- Not back-filling the in-page tabs that pdfMax has (Viewer / Report
  / Outline / Layers / etc). auto_a11y stays single-page.
- Not changing the `/pdfs/<id>/issue-map` route or the JSON shape it
  serves.

## Non-goals (explicit decisions made during brainstorming)

- The user explicitly chose "calculate on page load from all the
  attached documents" over persisting per-PDF totals. This rules out
  a denormalised cache column and rules out a write-time hook in
  `PdfAuditJob.run()` that updates `pdf_documents`.

---

## Part 1: Viewer issue-card detail parity

### Field inventory

The pdfMax `IssueAnnotation` shape (from
`pdfMax/src/utils/useChecker.ts:23-34`) is what the issue-map JSON
already carries, verbatim:

```
{
  id: string
  check_name: string
  check_result: "FAIL" | "WARN"
  element_index: int | null
  element_tag: string | null            // e.g. "H2", "Link", "Heading1"
  detail: string                        // markdown
  page: int | null                      // 1-based; null = document-level
  bbox: [x0, y0, x1, y1] | null         // PDF user-space; null = no location
}
```

`/pdfs/<id>/issue-map` streams this verbatim. No backend change to
the JSON.

### Card layout (target)

Single (ungrouped) issue card — matches
`pdfMax/src/components/ViewerSidebar.tsx:255-292`:

```
┌────────────────────────────────────────────────────────────┐
│ [FAIL]  <check-name>                              p.<page> │
│         [<element_index>] <element_tag>                    │
│         (or "Document-level" if page null AND bbox null)   │
│                                                            │
│         <detail, rendered as inline markdown>              │
│                                                            │
│         [View in report ↗]                                 │
└────────────────────────────────────────────────────────────┘
```

Multi-element grouped card — matches
`pdfMax/src/components/ViewerSidebar.tsx:299-405` and
`pdfMax/src/utils/issueGrouping.ts`:

```
┌────────────────────────────────────────────────────────────┐
│ ▸ [FAIL]  <check-name>           <N> elements              │
│           [View in report ↗]                               │
└────────────────────────────────────────────────────────────┘
```

When expanded:

```
┌────────────────────────────────────────────────────────────┐
│ ▾ [FAIL]  <check-name>           <N> elements              │
│           [View in report ↗]                               │
│                                                            │
│   • [<element_index>] <element_tag>                p.<pg>  │
│   • [<element_index>] <element_tag>                p.<pg>  │
│   • …                                                      │
└────────────────────────────────────────────────────────────┘
```

### Grouping rule

Port `groupIssues()` from
`pdfMax/src/utils/issueGrouping.ts:12-28` 1:1:

- Bucket key is `check_name + "\x00" + detail`.
- 1 issue in a bucket → standalone card; the `[<index>] <tag>` and
  page line render directly on the card.
- 2+ issues in a bucket → accordion card. Group header shows check
  name, severity badge, and "{N} elements". Expanded body lists each
  child issue's `[<index>] <tag>` and `p.<page>` on its own row.
- All groups start collapsed (matches pdfMax: `expandedGroups.has`
  is empty by default).

### "View in report" button

pdfMax target: switch to the sibling Report tab and scroll to the
matching check.

auto_a11y has no sibling tab — the markdown report lives at a
separate route, `/pdfs/<id>/pdfmax-report`. The rendered markdown
already carries `data-check-name="..."` attributes on `<details>`
elements (see `auto_a11y/web/templates/pdf/pdfmax_report.html:170-173`
and the markdown emitted by pdfMax itself). The button therefore:

1. Navigates to `/pdfs/<id>/pdfmax-report#check=<urlencoded>`.
   (The `#check=...` hash is preferred over `?check=...` because we
   stay within the same origin and don't need it server-side; the
   markdown renderer is client-only.)
2. The pdfmax_report page's existing inline script gains a hash
   handler that, after markdown sanitisation completes, runs
   `target.querySelector('[data-check-name="' + name + '"]')`
   (with the value compared post-decode against the literal value of
   `data-check-name`, which `marked` + DOMPurify already preserve),
   scrolls it into view (`{behavior: 'smooth', block: 'start'}`),
   opens the `<details>` if collapsed, and focuses the `<summary>`.
3. Falls back gracefully when no match is found: the page just loads
   without scrolling, no error.
4. A live region announces "Showing report section: {check_name}" via
   the existing `[role="status"]` element on the report page so
   screen reader users get the same affordance pdfMax provides via
   `requestAnimationFrame` + focus management.

The hash form (`#check=<name>`) is chosen over `#<slug>` because:

- The check name is already canonicalised inside the JSON. No
  slugifier round-trip risk.
- The check name can contain spaces and punctuation. URL encoding
  handles that without a slugger living in two places (the markdown
  emitter in pdfMax and the JS in auto_a11y).
- A future audit version that changes the rendered heading slug
  doesn't break the link.

### File changes (Part 1)

- `auto_a11y/web/static/js/pdf_viewer_app.js`
  - Add `_groupIssues(issues)` mirroring `issueGrouping.ts`.
  - Rewrite `_buildIssueCard()` to handle the standalone-card path
    and the new `_buildIssueGroupCard()` for multi-element groups.
  - Both paths emit `[<element_index>] <element_tag>` when both are
    non-null, "Document-level" when `page` and `bbox` are both null,
    and a "View in report" button that builds the report URL from
    `data-pdfmax-report-url` (a new attribute we'll set on the
    container so the JS doesn't hardcode `/pdfs/<id>/pdfmax-report`).
  - Pre-existing severity → badge class mapping (`badge-high`,
    `badge-medium`) is reused for the new group cards.
  - The "Document-level" badge uses `badge-neutral`.
  - **Remove the existing per-card "Page N" jump button**
    (`pdf_viewer_app.js:524-532`). pdfMax's Viewer card has no such
    button — clicks on the card / its overlay handle navigation, and
    the "View in report" button replaces the bottom-row affordance.
    The `p.<page>` label stays in the card header.
- `auto_a11y/web/templates/pdf/detail.html`
  - Add `data-pdfmax-report-url="{{ url_for('pdf.pdfmax_report',
    pdf_document_id=pdf.id) }}"` to the issue-list container.
- `auto_a11y/web/templates/pdf/pdfmax_report.html`
  - Add an inline script that, after markdown sanitisation, parses
    `location.hash`, looks for `check=<urlencoded>`, runs the
    querySelector + scrollIntoView + open + focus + SR-announce
    sequence described above. Pure additive; the existing render
    flow is unchanged.
- `auto_a11y/web/translations/en/pdf.ftl`
  - `pdf-viewer-issue-element` — `[{ $index }] { $tag }`
  - `pdf-viewer-issue-document-level` — `Document-level`
  - `pdf-viewer-issue-group-count` — element-count plural
    selector: `{ $count -> [one] 1 element *[other] { $count }
    elements }`
  - `pdf-viewer-issue-view-in-report` — `View in report`
  - `pdf-viewer-issue-view-in-report-aria` — `View "{ $check }" in
    the pdfMax report`
  - `pdfmax-report-jumped-to-check` — `Showing report section: {
    $check }` (live-region copy)
  - **Remove** the now-dead `pdf-viewer-jump-to-page-template`
    message (currently consumed only by the per-card "Page N" button
    that this spec deletes).
- `auto_a11y/web/translations/fr/pdf.ftl`
  - Six matching IDs under `### TODO_FR ###` per the existing
    release-gate convention. Strings copied verbatim from EN.
  - **Remove** the FR `pdf-viewer-jump-to-page-template` entry too.
- The `window.pdfViewerI18n` block in `detail.html:9-17` follows
  the existing kebab-case convention — JS reads keys with the
  feature prefix stripped. New keys to add (keeping that convention,
  matching the existing entries like `"issue-result-fail"`):
  - `"issue-element"` ← `pdf-viewer-issue-element`
  - `"issue-document-level"` ← `pdf-viewer-issue-document-level`
  - `"issue-group-count"` ← `pdf-viewer-issue-group-count`
  - `"issue-view-in-report"` ← `pdf-viewer-issue-view-in-report`
  - `"issue-view-in-report-aria"` ← `pdf-viewer-issue-view-in-report-aria`
  - `"jumped-to-check"` ← `pdfmax-report-jumped-to-check`
  - **Remove** `"jump-to-page": ftl('pdf-viewer-jump-to-page-template')`
    from the block (line 14 today).
- `auto_a11y/web/static/js/pdf_viewer_app.js` reads strings off
  `window.pdfViewerI18n` (a flat object, not namespaced — see
  `detail.html:9` and `pdf_viewer_app.js:31`). The new keys are
  added to that flat object inside `detail.html`'s existing
  `window.pdfViewerI18n = {…}` block, populated via
  `{{ ftl(...) | tojson }}` per the standard pattern in CLAUDE.md.

### Tests (Part 1)

JS testing in this repo is via Python-side fixture HTML + DOM
assertions (see existing `tests/pdf/test_pdf_routes.py` patterns).
For the JS card builder we add unit-style tests that drive
`pdf_viewer_app.js` in a JSDOM-like harness *only if* one already
exists; if not, we test at the route + template seam plus an
integration test that asserts the rendered fixture page loads the
JS file with the expected i18n object shape. Concretely:

- New `tests/pdf/test_pdf_viewer_issue_cards.py`:
  - Fixture: a small `*_issue_map.json` written into a tmp PDF
    storage layout with 5 issues — 1 standalone with full element
    info, 1 standalone document-level (page null, bbox null), 3 with
    duplicate `(check_name, detail)` to exercise grouping.
  - Hits `/pdfs/<id>/issue-map`, asserts JSON shape passes through
    each field (regression guard against accidental field stripping
    in the route).
  - Loads `/pdfs/<id>` and asserts the i18n object embedded in the
    page contains every new key (no missing-translation surprises
    at runtime).
- New `tests/pdf/test_pdfmax_report_anchor.py`:
  - Loads `/pdfs/<id>/pdfmax-report` with a fake markdown payload
    that contains `<details data-check-name="X">…</details>`.
  - Asserts the response HTML embeds the new hash-handler script.
  - Asserts the script references `data-check-name`. (Pure source
    presence — actually exercising the scroll requires Playwright,
    which we don't run for unit tests.)
- A Playwright smoke test is **not** added in this pass. The
  scroll/focus dance can be manually verified per the existing
  "Manual browser verification still owed" entry in the project
  memory; we keep that owed-status until Phase 11 introduces a
  general Playwright smoke harness for the PDF surface.

---

## Part 2: PDF rollup into website / project totals

### Source of truth

`auto_a11y/core/pdf_audit_job.py:280-329` writes the cache:

- `<pdf-stem>_accessibility_report.md`
- `<pdf-stem>_issue_map.json`
- extracted images

both into `pdf_path.parent / 'pdfmax-report'`. The route
`/pdfs/<id>/issue-map` (`auto_a11y/web/routes/pdf.py:778-816`)
already resolves this path via `_get_storage().local_path(pdf)`.

A PDF is "audited" when:

- `pdf.status == PdfDocumentStatus.AUDITED`, **and**
- the cache directory exists with a non-empty `*_issue_map.json`.

If the file is missing despite the status, treat as
`(violations=0, warnings=0)`. We log a warning at WARNING level so
the operator sees the inconsistency. We accept that a website with
N missing-cache PDFs will produce N warnings per request — that's
acceptable signal volume for an inconsistency that should be rare;
if it grows, the cure is fixing the audit job, not silencing the
log. We do **not** queue a re-audit from the request path.

### New helper module

`auto_a11y/pdf/issue_map_counts.py`

```python
@dataclass(frozen=True)
class PdfIssueCounts:
    violations: int  # check_result == "FAIL"
    warnings: int    # check_result == "WARN"


def count_issues(pdf: PdfDocument, storage: PdfStorage) -> PdfIssueCounts:
    """Read cached issue_map.json and tally FAIL / WARN.

    Returns PdfIssueCounts(0, 0) when the PDF has not been audited,
    when the cache file is missing, when JSON is malformed, or when
    the file's "issues" key is absent. Logs a warning in the
    cache-missing-but-status-AUDITED case and in the malformed-JSON
    case.

    Expected JSON shape: ``{"version": 1, "issues": [...]}`` per
    pdfMax's ``pdf_accessibility_audit.py:3603-3607``. Any other
    top-level shape (bare list, missing "issues" key, non-list
    value at "issues") is treated as malformed → 0/0 + warning.
    Within the list, only entries where ``check_result`` is exactly
    ``"FAIL"`` or ``"WARN"`` count toward the totals; anything else
    is ignored silently.
    """
```

Implementation detail: the existing route in `pdf.py` resolves the
path via `pdf_path = storage.local_path(pdf); cache_dir =
pdf_path.parent / 'pdfmax-report'; sorted(cache_dir.glob(
'*_issue_map.json'))[0]`. The helper uses the exact same recipe so
the two stay locked. Refactoring that into a single
`storage.issue_map_path(pdf)` is tempting but **out of scope** for
this spec — call it a tracked followup. The issue-map route reads
its file via `send_file`; this helper reads via `Path.read_bytes()`
+ `json.loads(...)`. The two paths must agree.

`PdfIssueCounts` is a `@dataclass(frozen=True)` with `__add__` so
the aggregation passes can sum a list of them.

The function's return type is `PdfIssueCounts` (never `None`). The
"unaudited" / "cache missing" cases collapse into the zero
instance. Callers do not branch on missing data; they sum what's
there.

### New per-request memoization

The website-detail page often touches a given PDF zero times; the
project-detail page touches each PDF in each website once. There's
no double-counting risk in the current call sites, so we do **not**
add a per-request cache yet. If a future report endpoint joins both
views in one request, we can introduce a `dict[ObjectId,
PdfIssueCounts]` cache passed explicitly through the call chain.
This is called out so an over-eager implementer doesn't add
`functools.lru_cache` (which would key on `pdf` identity and survive
across requests in long-lived workers — wrong shape for our
process-per-request model).

### Aggregation site changes

`auto_a11y/web/routes/websites.py:69-100` (current shape):

```python
pipeline = [
    {'$match': {'website_id': website_id}},
    {'$group': {
        '_id': None,
        'total_pages': {'$sum': 1},
        'total_violations': {'$sum': '$violation_count'},
        'total_warnings': {'$sum': '$warning_count'},
    }}
]
# ... runs against the `pages` collection or test_results aggregate
```

Add immediately after this aggregation:

```python
storage = _get_storage()
pdf_totals = PdfIssueCounts(0, 0)
for pdf in db.get_pdf_documents(website_id=website_id, limit=10000):
    if pdf.status == PdfDocumentStatus.AUDITED:
        pdf_totals = pdf_totals + count_issues(pdf, storage)
stats['total_violations'] += pdf_totals.violations
stats['total_warnings']   += pdf_totals.warnings
```

`auto_a11y/web/routes/projects.py:523-551`: same pattern, but inside
the per-website loop, so each website's row in the project page
shows the rolled-up totals (HTML + PDF).

### Database read shape

`db.get_pdf_documents(website_id=..., limit=10000)` is already used
by the existing PDF count badge code at `websites.py:119` and
`projects.py:572`, and inside `database.py:403`. The 10000 cap
matches those call sites — passing it explicitly is **load-bearing**:
the function's default `limit` is 100, which would silently truncate
rollups for any website with more than 100 PDFs and the under-count
would be invisible. Always pass `limit=10000`. (Replacing this with a
paginated iterator is a tracked Phase 2 followup in the project
memory; not in scope here.) No new query helper.

### Edge cases

- PDF with status `AUDITING` (in-flight) → contributes 0 / 0. The
  page already shows it in the "auditing" status bucket; the
  violation total reflects only finished audits.
- PDF with status `ERROR` → contributes 0 / 0. The error is already
  surfaced via status badge.
- Cache directory exists but no `*_issue_map.json` → log once at
  WARNING, contribute 0 / 0.
- Malformed JSON → log once at WARNING, contribute 0 / 0. Do **not**
  raise — a single bad cache file should not 500 the website
  detail page.
- Issue with `check_result` outside {"FAIL", "WARN"} → ignored by
  both counters. (Should not happen given the check enum, but
  defensive on bad/old caches.)

### Tests (Part 2)

- New `tests/pdf/test_issue_map_counts.py`:
  - Counts 0 / 0 for missing cache dir.
  - Counts 0 / 0 for empty cache dir.
  - Counts 0 / 0 for malformed JSON (and asserts a logged warning).
  - Counts FAIL / WARN correctly on a fixture with mixed
    severities.
  - Ignores unknown `check_result` values.
- Extend `tests/pdf/test_pdf_routes.py` (or a new
  `test_pdf_rollup_routes.py`):
  - Website detail page with N HTML pages (existing fixture) and 2
    audited PDFs (new fixture) — assert `total_violations` and
    `total_warnings` reflect the sum of both sources.
  - Project detail page with 2 websites, each with audited PDFs —
    assert per-website rows and the aggregate include PDF totals.
  - Audited PDF whose cache file is missing — assert page renders
    successfully and the rollup excludes it.
- No DB-bound tests are required; both `PdfDocument` listing and
  the storage path are exercised via the existing real-Mongo
  fixture pattern (skipped cleanly when Mongo is down — see
  project-memory note).

---

## Type discipline

- `count_issues` returns `PdfIssueCounts` — no `Optional`, no
  `tuple[int, int]`. Caller code stays branch-free.
- The JSON parse uses `json.loads(...)` and narrows via
  `isinstance(...)` checks before reading fields. No `cast`, no
  `# type: ignore`.
- The frontend JS adds a small number of typed `i18n` keys; no
  TypeScript involved (auto_a11y's vanilla JS).
- The Fluent strings are validated by the existing
  `tests/validate_translations.py` and the FR-translation xfail
  gate.

## Colour system

- Existing `badge-high` / `badge-medium` for severity.
- New "Document-level" tag uses `badge-neutral`. No Bootstrap colour
  classes added.
- The expand/collapse chevron is rendered via existing `<details>`
  CSS; no new colour token.

## Risks and rollback

- **Read-amplification**: each website detail page now opens N
  files (one per audited PDF) on the worker filesystem. For the
  largest websites in the dataset this is a few hundred small JSON
  reads per request. The cache files are O(KB) each; OS page cache
  absorbs the cost on hot workers. If this becomes a measurable
  hotspot, the followup is the deferred persisted-counts approach
  (a `violation_count` / `warning_count` column on `pdf_documents`,
  written at audit completion). That is explicitly **not** done
  here per user direction.
- **JSON parse cost**: malformed files log + return zeros. A bad
  audit run cannot 500 the website page.
- **Rollback**: revert the route changes — the helper module
  becomes dead code but harmless. Revert the JS changes — the panel
  reverts to the current 4-field rendering. No DB migration.

## Open followups (not in scope)

- Single source for the issue-map path. Currently in two places
  (the route and the helper). Track as a refactor.
- A general `auto_a11y/pdf/audit_cache.py` that owns the cache-dir
  layout and exposes `issue_map_path()`, `markdown_path()`, etc.
  Out of scope.
- Persisted per-PDF counts as a write-time hook, behind a config
  flag, if read-amplification becomes a real problem. Out of scope.
- Playwright smoke for the scroll-to-anchor and the grouping
  accordion. Tracked in Phase 11 (deployment + smoke harness).
