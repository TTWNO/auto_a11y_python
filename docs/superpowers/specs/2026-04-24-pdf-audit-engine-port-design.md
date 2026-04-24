# PDF Audit Engine Port (pdfMax → auto_a11y)

**Date:** 2026-04-24
**Status:** Design in review
**Scope:** Port pdfMax's Python PDF accessibility audit engine (`pdf_accessibility_audit.py`, ~12,500 lines) into auto_a11y as a native testing path. When auto_a11y encounters a PDF (via upload, URL entry, or an HTML test that hits a `Content-Type: application/pdf` response), the engine runs the audit, produces a native `TestResult`/`Violation`/`AIFinding` payload, persists it with a new `PdfDocument` model, and renders results through the existing reporting pipeline.

## Position in the larger plan

The user intends full absorption of pdfMax (audit, viewer, and remediation) into auto_a11y as a single application. That work is decomposed into sequential sub-projects; this spec covers **sub-project #1** only. The remaining sub-projects are *out of scope for this spec* and called out explicitly in the Non-Goals section.

| # | Sub-project | Status |
|---|-------------|--------|
| 1 | **PDF audit engine port** (this spec) | In progress |
| 2 | PDF discovery + download pipeline (scraper changes) | Future |
| 3 | Web-based PDF viewer (canvas/text/annotation layers) | Future |
| 4 | PDF remediation engine (port of `pdf_fix.py`) | Future |
| 5 | Retire pdfMax Electron shell | Future |

## Problem

1. **auto_a11y does not test PDFs.** The scraper explicitly skips `.pdf` URLs (`auto_a11y/core/scraper.py:339`) from page testing and only records them as `DocumentReference`s (metadata + language only, no audit).
2. **pdfMax has a mature PDF auditor but lives in a separate Electron codebase.** Users have to export PDFs, launch pdfMax, audit them one at a time, and copy the results manually. No integration with projects, no persistence, no reporting pipeline, no multi-user workflow, no bilingual UI.
3. **When an auto_a11y-tested URL returns a PDF** (direct serve, redirect, etc.), the current page test errors out because Playwright can't find `body`. The PDF is never audited and the failure is opaque.

## Goals

1. Bring pdfMax's Python audit capabilities into auto_a11y as native functionality — users audit PDFs and read results inside auto_a11y, without pdfMax being installed.
2. Map pdfMax's check results onto auto_a11y's canonical data model (`Violation`, `AIFinding`, `TestResult`, stable codes, touchpoints, WCAG criteria, impact levels) so PDF issues live alongside HTML issues in reports, filters, and analytics with no second-class handling.
3. Preserve auto_a11y's non-negotiable project hygiene through the port: strict `mypy`/`pyright`/`ty`, Fluent EN/FR for every user-visible string, custom colour tokens (no Bootstrap colours), fixture-gated production enablement, accessibility of the new UI itself.
4. Detect PDF responses during HTML page testing (`Content-Type: application/pdf` or redirect-to-PDF) and audit them inline, instead of failing with an opaque error.
5. Store PDF artefacts (bytes + extracted images) durably so downstream sub-projects (viewer, remediation) can reuse them without re-fetching or re-extracting.
6. Never compromise on fixture discipline: every PDF check ships with `fail.pdf` + `pass.pdf` detection fixtures and is gated on fixture success, identical to the HTML rule.

## Non-Goals

- **pdfMax's React PDF viewer** (canvas/text/annotation layers, `IssueConnectorLines`, issue-overlay-on-PDF highlighting) — deferred to sub-project #3. This spec uses the browser's native PDF viewer via `<iframe>` + `#page=N` deep links.
- **`pdf_fix.py` remediation port** — deferred to sub-project #4. Violations in this spec carry `remediation` *text* (translated from pdfMax's `remediation_guide.py`) but no "Fix" button wires up auto-repair.
- **pdfMax Electron shell / React components / webpack configs / `forge.config.ts`** — untouched in this spec; retired in sub-project #5.
- **PDF discovery in the scraper.** Scraper continues to skip PDF links as it does today; spec #2 changes that. This spec only covers (a) manual upload/URL entry and (b) opportunistic audit when a `test_page` invocation hits a PDF response.
- **Non-PDF document types** (Word, Excel, PowerPoint) — remain as `DocumentReference`s only. No audit.
- **Global search integration** for PDF documents — deferred.
- **Public sharing of PDF files** — all `/pdfs/<id>/file` access is project-scoped.
- **AI-fixture tests** for AI-generated findings — deferred to follow-up; fixture tests in this spec run with AI off.
- **Translation-on-demand for AI-generated finding text** — Claude-produced narrative is rendered in whatever locale Claude returned it in; post-translation deferred.
- **Backfill of existing `DocumentReference` records into `PdfDocument`** — there is no implicit migration. Existing PDF references stay as `DocumentReference`s; they can be audited on demand via the manual-URL entry path.

## Design

### 1. Package layout

Everything new under `auto_a11y/pdf/`, organised into focused submodules rather than a single 12.5k-line file.

```
auto_a11y/pdf/
  __init__.py
  audit/
    __init__.py              # public API: run_audit(pdf_path, ...) -> AuditResult
    pipeline.py              # orchestrates all check stages; weighted progress
    structure.py             # walk_structure_tree, populate_element_text, tag tree
    content_streams.py       # MCID extraction and text mapping
    colors.py                # extract_text_colors, extract_form_field_colors, color pairs
    images.py                # extract_images, image inventory
    fonts.py                 # font data, rotation, italic, spacing, alignment
    reading_order.py         # visual reading order analysis
    ghostscript.py           # render_pdf_page_to_image (sole subprocess caller)
    pikepdf_helpers.py       # typed wrappers over pikepdf's dynamic-attribute surface
    checks/
      __init__.py             # check registry + re-exports
      document_properties.py  # title, language, PDF/UA id, metadata
      tagging_structure.py    # tagged, role map, nesting, empty tags, MCIDs
      annotations.py          # tagged annotations, alt descriptions, tab order
      lists.py                # list structure, nesting, empty
      tables.py               # headers, scope, regularity, captions
      headings.py             # hierarchy, size hierarchy
      images_alt_text.py      # alt presence, adequacy, images-of-text
      links_navigation.py     # link content, alt descriptiveness, bookmarks, label-in-name
      forms.py                # labels, required indicators, tab order, unique names
      interactive.py          # tab order, target size, focus indicator
      color_contrast.py       # WCAG contrast evaluation
      fonts.py                # min size, face, ratio, rotation, italics, spacing, alignment
      language.py             # BCP 47 validation, language of parts
    ai/
      __init__.py
      semantic.py              # run_claude_semantic_analysis (refactored)
      executive_summary.py     # structured summary schema
      finding_mapper.py        # pdfMax AI output -> list[AIFinding]
  translation/
    check_mapper.py            # CheckResult -> Violation; contains CHECK_CATALOGUE
    remediation_guide.py       # translated-keyed copy of pdfMax's guide
  language.py                  # shared PDF language helper (scraper + audit)
  storage.py                   # filesystem storage, SHA-256, per-PdfDocument dir
  fixtures.py                  # PDF fixture metadata loading + sidecar parser
  models.py                    # internal dataclasses: CheckResult, AuditResult, TagElement...
  errors.py                    # typed exceptions: GhostscriptMissing, CorruptPdf, FetchFailed...

auto_a11y/models/
  pdf_document.py              # new: PdfDocument model
  test_result.py               # modified: add target_type/target_id polymorphism
  page.py                      # modified: add linked_pdf_document_id + PageStatus.IS_PDF

auto_a11y/testing/
  test_runner.py               # new method: async def test_pdf(pdf_document_id)
                               # modified: test_page() opportunistic PDF branch
  pdf_runner.py                # NEW: async wrapper; run_in_executor; dedicated pool

auto_a11y/web/
  routes/
    pdf.py                     # new: /projects/<id>/pdfs, /pdfs/<id>, /pdfs/<id>/file, ...
  templates/
    _target_header.html        # NEW shared partial used by Page + PdfDocument views
    _target_result_summary.html
    _violation_card.html       # MODIFIED: optional "View at page N" link
    pdf/
      list.html
      add.html
      detail.html
      _audit_progress.html
      _empty_state.html
    test_result.html           # MODIFIED to consume shared target view model
  static/js/
    pdf_viewer.js              # iframe #page=N deep-link handler
  translations/
    {en,fr}/pdf.ftl            # UI strings
    {en,fr}/pdf-checks.ftl     # per-check: name, short_title, what, why, who
    {en,fr}/pdf-remediation.ftl
    {en,fr}/pdf-touchpoints.ftl
    {en,fr}/pdf-progress.ftl
    {en,fr}/pdf-errors.ftl
    {en,fr}/pdf-status.ftl
  view_models/
    target.py                  # TestTargetView dataclass

stubs/
  wcag_contrast_ratio/         # hand-written .pyi stub
  # (pdfminer.six ships py.typed from 20231228+ — no local stubs needed)

Fixtures/PDF/
  DocumentProperties/Err_<Code>/{fail.pdf, fail.meta.yaml, pass.pdf, pass.meta.yaml}
  Tagging/...
  Tables/...
  Images/...
  Forms/...
  Headings/...
  Language/...
  ColorAndContrast/...
  Navigation/...
  Focus/...
  Fonts/...
  Annotations/...

fixture_generation/pdf/
  generate_pdf_fixtures.py     # extended from pdfMax's tests/generate_test_pdfs.py
  builders.py                  # shared low-level PDF-construction helpers
  generators/
    document_properties.py
    tagging_structure.py
    ...                        # one module per touchpoint

scripts/
  port_pdfmax_remediation_guide.py  # one-off porting script; typed
```

#### Not in this spec's tree

No `auto_a11y/pdf/viewer/`, no `auto_a11y/pdf/fix/` or `repair/`, no `auto_a11y/pdf/annotations/`. `pdfMax/python/checker/pdf_fix.py` is not copied.

### 2. Data model

#### New model: `PdfDocument`

Stored in a new `pdf_documents` collection.

```python
@dataclass
class PdfDocument:
    website_id: str
    project_id: str                     # denormalized for fast project-level queries
    source_url: str | None              # None for uploads
    source_type: Literal["uploaded", "manual_url", "opportunistic"]
    discovered_from_page_id: str | None
    discovered_from_user_id: str | None

    sha256: str                         # file content hash; dedup key per website
    file_size_bytes: int
    storage_relpath: str                # "<website_id>/<pdf_document_id>/pdf.pdf"
    images_relpath: str                 # "<website_id>/<pdf_document_id>/images/"

    original_filename: str | None
    pdf_version: str | None             # populated after first audit
    page_count: int | None
    declared_lang: str | None
    detected_lang: str | None
    lang_confidence: float | None

    status: PdfDocumentStatus
    error_reason: str | None
    last_audit_result_id: str | None

    discovered_at: datetime
    last_audited_at: datetime | None

    _id: ObjectId | None


class PdfDocumentStatus(Enum):
    PENDING = "pending"
    FETCHING = "fetching"
    FETCH_FAILED = "fetch_failed"
    AUDITING = "auditing"
    AUDITED = "audited"
    AUDIT_FAILED = "audit_failed"
```

Indexes:
- `{website_id: 1, sha256: 1}` unique — dedup
- `{project_id: 1, discovered_at: -1}` — project listing
- `{website_id: 1, discovered_at: -1}` — website listing
- `{status: 1}` — finding stuck audits

#### Modified model: `TestResult` gains polymorphic target

```python
class TargetType(Enum):
    PAGE = "page"
    PDF_DOCUMENT = "pdf_document"


@dataclass
class TestResult:
    target_type: TargetType           # default PAGE for back-compat reads
    target_id: str                    # _id of the target
    page_id: str | None               # retained; equals target_id iff target_type=PAGE
    # ... all existing fields unchanged
```

- **Backwards-compatible read path**: records without `target_type`/`target_id` are read as `target_type=PAGE`, `target_id=page_id`.
- **Write path**: new writes always set both; HTML results continue setting `page_id` so existing queries keep working.

#### Modified model: `Page` gains PDF link

- New `PageStatus.IS_PDF` value.
- New field `linked_pdf_document_id: str | None`.
- When opportunistic testing converts a page audit into a PDF audit, the `Page` stays as a breadcrumb (`status=IS_PDF`, `linked_pdf_document_id=<pdf_id>`, `error_reason="URL serves a PDF; see linked PDF document."`). The `TestResult` attaches to the `PdfDocument`, not the `Page`.

#### `Violation` metadata conventions (no schema change)

PDF-specific fields ride in the existing `metadata: dict[str, Any]`:

- `metadata['pdf_page']: int | None` — 1-based page number
- `metadata['pdf_bbox']: list[float] | None` — `[x, y, w, h]` (future viewer)
- `metadata['pdf_mcid']: int | None` — MCID reference (future viewer)
- `metadata['pdf_element_ref']: str | None` — inventory sigil `[N]`
- `metadata['pdfmax_original_details']: str` — machine-readable, not user-shown

#### `DocumentReference` — unchanged

Remains for non-PDF documents and for link-tracking where no audit has happened. Sub-project #2 will add a `pdf_document_id` field to link a reference to its audited artefact; this spec leaves `DocumentReference` alone.

#### Cascading deletes

- Delete `PdfDocument` → delete `{storage_relpath, images_relpath}` dir; delete all `TestResult`s with `target_type=PDF_DOCUMENT AND target_id=this._id`.
- Delete `Website` → cascade to all its `PdfDocument`s.
- Delete `Project` → cascade through `Website` → `PdfDocument`.

#### Migration

Non-destructive:
- `pdf_documents` collection auto-created on first insert.
- `Page.linked_pdf_document_id` defaults to `None`.
- `TestResult.target_type`/`target_id` inferred for existing records on read.
- `PageStatus.IS_PDF` added to the enum.
- No backfill job.

### 3. Check-code taxonomy and touchpoint mapping

#### Naming convention

```
<Prefix><Type><ConceptualSubject>
```

- `<Prefix>` = `Pdf`
- `<Type>` ∈ `{Err, Warn, Info, Disco}` per auto_a11y convention
- `<ConceptualSubject>` = PascalCase noun phrase derived from the pdfMax check name

Example codes:

| pdfMax check name | Result | auto_a11y code | Touchpoint | Impact | WCAG |
|---|---|---|---|---|---|
| "Document title set" | FAIL | `PdfErrDocumentTitleNotSet` | `PdfDocumentProperties` | HIGH | 2.4.2 |
| "Document language set" | FAIL | `PdfErrDocumentLanguageNotSet` | `Language` | HIGH | 3.1.1 |
| "PDF is tagged" | FAIL | `PdfErrNotTagged` | `PdfTagging` | HIGH | 1.3.1, 4.1.2 |
| "Alt text on all Figure/Art tags" | FAIL | `PdfErrFigureMissingAlt` | `Images` | HIGH | 1.1.1 |
| "Heading hierarchy valid" | FAIL | `PdfErrHeadingHierarchyBroken` | `Headings` | MEDIUM | 1.3.1, 2.4.6 |
| "Table headers defined" | FAIL | `PdfErrTableNoHeaders` | `Tables` | HIGH | 1.3.1 |
| "Form fields labeled" | FAIL | `PdfErrFormFieldUnlabelled` | `Forms` | HIGH | 1.3.1, 3.3.2, 4.1.2 |
| (AI) "Reading order mismatch" | critical | `PdfErrReadingOrderMismatch` | `PdfTagging` | HIGH | 1.3.2 |

#### Touchpoint mapping rules

**Reuse existing touchpoints** wherever a concept matches:
- `Images` — alt text presence/adequacy, images-of-text
- `Headings` — hierarchy, size hierarchy
- `Tables` — headers, scope, structure (existing name confirmed during implementation)
- `Forms` — field labels, required indicators, unique names
- `Language` — document lang, BCP 47 validity, language of parts
- `ColorAndContrast` — WCAG contrast
- `Navigation` — link content, descriptiveness, bookmarks (existing name confirmed during implementation)
- `Focus` — target size, focus indicator
- `Fonts` — font size, face, italic, spacing, alignment (existing name confirmed during implementation)

**Three new touchpoints** added to `auto_a11y/core/touchpoints.py` for genuinely PDF-only concepts:
- `PdfTagging` — structure tree, role map, MCIDs, nesting, artifact boundaries
- `PdfDocumentProperties` — title, PDF/UA ID, XMP metadata, page labels, accessibility permissions, XFA, PDF version
- `PdfAnnotations` — annotation tagging, alt descriptions, tab order, multimedia annotations

Each new touchpoint gets Fluent name + description in both locales, `get_wcag_criteria_for_touchpoint(...)` support, and is registered in `get_all_touchpoints()`.

#### Impact assignment

Deterministic default, per-check override permitted:

| pdfMax result | WCAG level | Default impact |
|---|---|---|
| FAIL | A | HIGH |
| FAIL | AA | HIGH |
| FAIL | AAA | MEDIUM |
| FAIL | PDF/UA-only | MEDIUM |
| FAIL | best-practice | LOW |
| WARN | any | MEDIUM |
| INFO / DISCO | any | LOW |

The check-catalogue row may override any of these (e.g., "Bookmarks present" is WCAG 2.4.5 Level A but practically MEDIUM impact).

#### AI findings mapping

| pdfMax AI severity | `AIFinding.severity` | Impact |
|---|---|---|
| critical | high | HIGH |
| important | medium | MEDIUM |
| advisory | low | LOW |

AI findings go into `TestResult.ai_findings`, not `violations`. This matches auto_a11y's existing HTML AI convention.

#### Catalogue location

Single source of truth: `auto_a11y/pdf/translation/check_mapper.py` holds `CHECK_CATALOGUE: list[CatalogueRow]`. Row shape:

```python
class CatalogueRow(TypedDict):
    pdfmax_check_name: str
    pdfmax_result: Literal["FAIL", "WARN"]
    stable_id: str                   # PdfErr... / PdfWarn... / PdfInfo... / PdfDisco...
    touchpoint: str
    impact_override: ImpactLevel | None
    wcag_criteria: list[str]
    generator_version: int           # for fixture regeneration
```

Imported by `audit/pipeline.py`, `translation/check_mapper.py` itself, `fixtures.py`, and translation-coverage tests.

Full enumeration (~200 rows) is deferred to implementation. Spec commits to the *process* and *acceptance criterion* ("every emitted `CheckResult` maps to a catalogue row; missing rows fail tests").

### 4. Fixture architecture

Per the project's rule: a check is only enabled in production if all its fixtures pass.

#### Layout

```
Fixtures/PDF/
  {TouchpointDir}/
    {Type}_{StableCode}/
      fail.pdf
      fail.meta.yaml
      pass.pdf
      pass.meta.yaml
```

Touchpoint dir names: `DocumentProperties`, `Tagging`, `Annotations`, `Lists`, `Tables`, `Headings`, `Images`, `Navigation`, `Forms`, `Focus`, `ColorAndContrast`, `Language`, `Fonts`.

#### Sidecar metadata

YAML format (human-authorable, diffable):

```yaml
# fail.meta.yaml
category: DocumentProperties
code: PdfErrDocumentTitleNotSet
type: Err
expected_result: The document has no /Title in its catalog
pdf_version: "1.7"
generator_version: 1
manual: false         # true when the PDF is produced outside the pikepdf generator (e.g., from an .odt/.docx source)
notes: |
  Catalog intentionally has no /Title entry.
```

`expected_result` is either a description string (for `fail.pdf`) or the literal `"pass"` (for `pass.pdf`). `generator_version` is bumped when the generator changes in a regeneration-triggering way. `manual: true` flags a fixture that falls under the escape hatch below.

#### Generator

`fixture_generation/pdf/generate_pdf_fixtures.py` — extended from pdfMax's `tests/generate_test_pdfs.py`. One generator function per stable code, named `generate_{snake_case_code}()`. A registry dict maps every `CHECK_CATALOGUE` code to its generator; a completeness test fails if any catalogue code has no generator.

Helpers in `fixture_generation/pdf/builders.py` for common PDF constructs (tagged scaffold, table with/without headers, figure with/without alt).

Commands:
- `python -m fixture_generation.pdf.generate_pdf_fixtures` — regenerate all
- `python -m fixture_generation.pdf.generate_pdf_fixtures --code <Code>` — regenerate one

All generator code is strict-typed.

#### Escape hatch

When `pikepdf` generation is genuinely impractical (complex visual layouts), a `fail.source.odt` / `fail.source.docx` may be committed alongside `fail.pdf`; the registry entry is marked `manual: true`; a README inside the fixture dir documents manual regeneration. Expectation: <5% of fixtures. Spec notes that exceeding 5% is a signal to reconsider.

#### `test_fixtures.py` extensions

- Enumerates `Fixtures/PDF/*/*/*.pdf`, parses sidecar YAML, invokes `auto_a11y.pdf.audit.run_audit(fixture_path, wcag_level="AA", run_ai=False, progress=None)`.
- For `fail.pdf`: passes if the expected stable code is among detected FAIL codes. For `pass.pdf`: passes if the expected stable code is **not** among detected codes.
- PDF fixture runs use a dedicated thread pool (PDF audits are slower than HTML).
- Fixture tests skip AI analysis (non-deterministic); AI-dependent checks use a follow-up "AI fixture" mechanism deferred to a later spec.
- Results persisted to existing `fixture_tests` collection with `target_type: "pdf"` discriminator.
- New CLI flags: `--pdf-only`, `--html-only`, `--target {html, pdf}`, `--code <StableCode>`, `--touchpoint <Name>`.

#### Production gate

Identical to HTML. A PDF check is enabled in production only when both its `fail.pdf` is detected and its `pass.pdf` is not. The violation filter adds `is_pdf_check_enabled(stable_id)` alongside the existing HTML equivalent, reading from `fixture_tests`.

#### `/testing/fixture-status` UI

Gains a separate "PDF Checks" tab (per question 19 option A). `?target=pdf|html` query param; default `html` for back-compat. Identical table shape to HTML tab. Fluent labels in both locales.

### 5. Testing flow

#### Three entry points converge on one engine

```
    Upload/URL route        test_runner.test_pdf(id)      test_runner.test_page
    (user action)           (explicit re-audit)           (opportunistic branch)
          │                        │                             │
          └────────────────────────┼─────────────────────────────┘
                                   ▼
                 auto_a11y.testing.pdf_runner.PdfRunner
                 async def audit_pdf_document(pdf_document_id, ...)
                                   │
                                   │  loop.run_in_executor (dedicated pool)
                                   ▼
                 auto_a11y.pdf.audit.pipeline.run_audit(path, ...)
                                   │
                                   ▼
                        AuditResult (sync return)
                                   │
                          translation/check_mapper
                                   │
                                   ▼
                          TestResult persisted
```

#### `PdfRunner` (new, in `auto_a11y/testing/pdf_runner.py`)

```python
class PdfRunner:
    def __init__(self, database: Database, storage: PdfStorage,
                 config: PdfConfig, *,
                 max_parallel: int) -> None:
        ...
        self._executor = ThreadPoolExecutor(
            max_workers=max_parallel,
            thread_name_prefix="pdf-audit",
        )

    async def audit_pdf_document(
        self,
        pdf_document_id: str,
        *,
        run_ai: bool,
        ai_api_key: str | None,
        wcag_level: Literal["AA", "AAA"],
        progress_cb: ProgressCallback | None = None,
    ) -> TestResult:
        ...
```

Dedicated `ThreadPoolExecutor` (configurable `PDF_AUDIT_MAX_PARALLEL`, default 2) so PDF audits don't starve the default executor used by HTML test execution.

#### Fetch pipeline

**Upload:**
1. Receive multipart POST.
2. Validate magic bytes (`%PDF-`).
3. Compute SHA-256.
4. Dedup: `(website_id, sha256)`; if hit, return existing.
5. If miss: allocate `_id`, compute paths, atomic write, fsync, insert `PdfDocument` with `status=PENDING`.
6. Enqueue audit job.

**Manual URL:**
1. Receive URL + optional `WebsiteUser` selector.
2. Streaming GET to verify `Content-Type` and size (reject non-PDF, reject > `PDF_MAX_SIZE_MB`). HEAD is avoided as the first step: some servers reject HEAD or return wrong `Content-Type` for HEAD requests.
3. Authenticated fetch via `aiohttp` with cookies seeded from the `WebsiteUser`, if one was specified.
4. Download to tempfile; magic-byte verify.
5. SHA-256 + dedup + store + enqueue (same as upload from step 4 onwards).
6. `source_url` populated; `source_type=manual_url`.

**Opportunistic (from `test_page`):**
1. After Playwright's `goto`, inspect `response.headers["content-type"]`.
2. If `application/pdf` (or `application/octet-stream` with a `.pdf` URL path):
   a. Prefer reading bytes directly from the Playwright response (`response.body()`) to avoid a second round-trip, re-seeding cookie edge cases (one-time-use session tokens), and potentially-divergent redirect outcomes on a second fetch. If `response.body()` is unavailable or fails, fall back to an `aiohttp` fetch seeded with Playwright's cookies (`browser_page.context.cookies()`).
   b. Magic-byte verify.
   c. SHA-256 + dedup under `website_id`.
   d. Create `PdfDocument` with `source_type=opportunistic`, `discovered_from_page_id=page.id`.
   e. Mark `Page.status=IS_PDF`, `Page.linked_pdf_document_id=pdf_doc.id`.
   f. Call `PdfRunner.audit_pdf_document(...)` in the same job.
   g. Return the PDF's `TestResult` (attached to `PdfDocument`, not to `Page`).
3. PDF detection happens **before** `wait_for_selector('body')`. Login automation, page-setup scripts, JS test injection, and multi-state testing are all skipped for PDF responses.
4. On download/magic-byte/size failure: `Page.status=ERROR` with a descriptive reason; no `PdfDocument` created.

#### `PdfStorage` API

```python
class PdfStorage:
    def __init__(self, base_dir: Path) -> None: ...
    def allocate_pdf(self, website_id: str, pdf_document_id: str) -> AllocatedSlot: ...
    def write_pdf_bytes(self, slot: AllocatedSlot, data: bytes) -> None:
        """Atomic write-then-rename; fsync parent directory."""
    def local_path(self, doc: PdfDocument) -> Path: ...
    def images_dir(self, doc: PdfDocument) -> Path: ...
    def delete(self, doc: PdfDocument) -> None: ...
    def delete_website(self, website_id: str) -> None: ...
```

Config surface in `config.py`:
- `PDF_STORAGE_DIR: str` (default `data/pdfs`)
- `PDF_MAX_SIZE_MB: int` (default `100`)
- `PDF_DOWNLOAD_TIMEOUT_SECONDS: int` (default `60`)
- `PDF_AUDIT_MAX_PARALLEL: int` (default `2`)
- `GHOSTSCRIPT_PATH: str | None` (default `None` → autodetect)

#### Dedup semantics

- Key: `(website_id, sha256)` — **not** global. Same PDF on two websites = two `PdfDocument`s and two file copies (simplifies cascading deletes on website removal).
- Same content re-uploaded to the same website = same `PdfDocument`, new `TestResult` appended only when user explicitly requests re-audit.
- URL returning different content on later run = new `PdfDocument` created; old one retained as history.

#### Partial-audit resilience

One misbehaving check must not tank the whole audit. `pipeline.py` wraps each check in try/except, logs, and records a synthetic `PdfErrInternalCheckFailure` violation naming the crashed check. Matches auto_a11y's existing HTML resilience.

#### Retry policy

All retries are user-initiated. No automatic background retries.

| Situation | `PdfDocument.status` | `Page.status` (opportunistic) |
|---|---|---|
| Download fails | `FETCH_FAILED` | `ERROR` |
| Magic-byte mismatch | doc not created | `ERROR` |
| Size overflow | doc not created | `ERROR` |
| Ghostscript missing | fails fast with `GhostscriptMissing` | `ERROR` |
| Corrupt PDF | `AUDIT_FAILED` | `ERROR` |
| Claude API error mid-audit | `AUDITED` + `metadata['ai_error']` | N/A |
| Single check raises | audit continues; synthetic violation recorded | N/A |

#### Progress reporting

pdfMax's `ProgressTracker` is refactored to accept a `Callable[[str, int], None]` callback instead of writing JSON to stdout. `PdfRunner.audit_pdf_document` passes a callback that updates the job's progress via `job_manager.update_progress(...)`, which the existing SSE-based progress UI already consumes. No changes to the job/progress UI layer.

#### State-agnostic testing

PDFs are state-agnostic (question 18 option A). One `TestResult` per `PdfDocument` per test invocation, regardless of the state from which the PDF was discovered. SHA-256 dedup stops redundant audits across multi-state or multi-referrer discovery.

### 6. Web UI and routes

#### Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/projects/<project_id>/pdfs` | List view, filterable |
| GET | `/websites/<website_id>/pdfs` | Website-scoped list |
| GET | `/projects/<project_id>/pdfs/add` | Upload/URL form |
| POST | `/projects/<project_id>/pdfs` | Create (upload or URL) |
| GET | `/pdfs/<pdf_document_id>` | Detail: iframe + test result |
| POST | `/pdfs/<pdf_document_id>/audit` | Enqueue new audit job |
| GET | `/pdfs/<pdf_document_id>/file` | Stream the stored PDF bytes |
| GET | `/pdfs/<pdf_document_id>/images/<image_name>` | Stream extracted image |
| POST | `/pdfs/<pdf_document_id>/delete` | Cascade delete |

All routes check project membership via existing `permissions.py`. `X-Frame-Options: SAMEORIGIN` and `Content-Disposition: inline` on `/file`.

#### Shared view model

`auto_a11y/web/view_models/target.py`:

```python
@dataclass(frozen=True)
class TestTargetView:
    kind: Literal["page", "pdf_document"]
    id: str
    title: str
    source_url: str | None
    breadcrumb: list[Crumb]
    last_audited_at: datetime | None
    latest_result_id: str | None
    page_count: int | None           # PDF only
    file_size_bytes: int | None      # PDF only
    inline_viewer_url: str | None    # PDF only
```

Factories: `target_view_from_page(page)`, `target_view_from_pdf(pdf)`. Templates branch on `target.kind` only for the handful of kind-specific fields; the rest (violations list, AI findings, severity counts) render uniformly.

#### Detail view — `pdf/detail.html`

Two-pane layout (55/45 desktop, stacked on mobile):

- **Left (≈ 55%)**: iframe to `/pdfs/<id>/file#page=1`, with title attribute; above it the breadcrumb, title, "Re-audit" button, "Delete" button; below it a metadata strip (pages, size, PDF version, declared language).
- **Right (≈ 45%)**: `test_result.html` scoped to the latest `TestResult`. Violations with `metadata.pdf_page` render a "View at page N" button (real `<button type="button">`, not a div) that updates the iframe's `src` fragment to `#page={n}&zoom=page-fit` via JS (fragment-only change, no reload).

AI findings section reuses the existing HTML AI findings UI.

#### List view — `pdf/list.html`

Columns: title, website, source (icon), pages, last audited, violation summary (severity pills via custom token classes), status. Per-row actions: View, Re-audit, Delete. Filters: website, status multi-select, has-issues, discovered-from text search. Empty state CTA.

#### Add form — `pdf/add.html`

Two mutually-exclusive input groups: file upload (`accept="application/pdf"`), URL + optional `WebsiteUser` dropdown + optional website dropdown. JS toggles; Python validates. On success redirect to `/pdfs/<id>` with a flash. Dedup hit redirects to the existing PDF with a flash.

#### Navigation integration

- Project detail page gains a "PDFs" link with count badge.
- Website detail page gains a "PDFs" section with count badge.
- Pages with `status=IS_PDF` show a "→ PDF" badge linking to the `PdfDocument`.

#### Accessibility of the new UI

All of the existing auto_a11y UI rules apply: no hardcoded English strings, custom colour tokens only, no Bootstrap colour classes, no explicit `role` attributes on elements with native roles, no explicit `tabIndex` outside permitted cases, no `position: relative/absolute` on semantic elements. Iframe has `title`. Progress announced via `aria-live="polite"`. Violation jump buttons keep keyboard focus on the iframe after navigation.

The iframe's internal accessibility is the browser's native PDF viewer — we don't own that surface. Spec acknowledges and flags that sub-project #3's custom viewer will address it.

#### Progress UI

`/pdfs/<id>` auto-subscribes to the existing job progress SSE stream when `status=AUDITING`. No new real-time plumbing.

### 7. Fluent translation coverage

#### What must be translated

1. Every check's display text: `name`, `short_title`, `what`, `why`, `who`, `remediation` (five message IDs per stable code + one remediation ID).
2. Every new UI string (list, detail, form, flash, buttons, breadcrumbs, empty states).
3. Every new touchpoint name + description.
4. Every new `PdfDocumentStatus` and `PageStatus.IS_PDF` label.
5. Every progress step name.
6. Every user-facing error message.
7. Any PDF/UA-specific WCAG reference not already in `wcag.ftl`.

AI-generated finding text is **not** pre-translatable; Claude is prompted to respond in the user's configured locale. Translation-on-demand for AI findings is deferred.

#### File layout

Under `auto_a11y/web/translations/{en,fr}/`:

```
pdf.ftl                  # UI strings
pdf-checks.ftl           # per-check: name, short_title, what, why, who
pdf-remediation.ftl      # per-check: remediation
pdf-touchpoints.ftl      # three new PDF touchpoints
pdf-progress.ftl         # audit progress step names
pdf-errors.ftl           # user-facing errors
pdf-status.ftl           # PdfDocumentStatus + PageStatus.IS_PDF
```

#### ID convention

Kebab-case, feature-prefixed:

```
pdf-check-PdfErrDocumentTitleNotSet-name         = Document title not set
pdf-check-PdfErrDocumentTitleNotSet-short-title  = Missing document title
pdf-check-PdfErrDocumentTitleNotSet-what         = ...
pdf-check-PdfErrDocumentTitleNotSet-why          = ...
pdf-check-PdfErrDocumentTitleNotSet-who          = ...
pdf-remediation-PdfErrDocumentTitleNotSet        = ...

touchpoint-pdf-tagging-name                      = PDF Tagging & Structure
touchpoint-pdf-tagging-description               = ...

status-pdf-auditing                              = Auditing
status-page-is-pdf                               = Served PDF

pdf-progress-step-analyzing-structure-tree       = Analysing structure tree
pdf-error-ghostscript-missing                    = Ghostscript is not installed...
```

#### Persistence convention

Violations are persisted with the stable code as `id` and **locale-free** metadata. All user-visible display fields are resolved via `ftl(f"pdf-check-{id}-{field}")` at render time, matching auto_a11y's HTML approach.

#### Remediation guide porting

pdfMax's `remediation_guide.py` (~5.5k lines of English markdown) is extracted by a one-off script (`scripts/port_pdfmax_remediation_guide.py`) into `auto_a11y/web/translations/en/pdf-remediation.ftl`. A human translator produces `fr/pdf-remediation.ftl`. Machine-translation placeholders are **not** committed; coverage tests fail if any French string is missing. Markdown inside Fluent values is rendered via auto_a11y's existing markdown filter (or `markdown2` / `mistune` added if none exists — verified during implementation).

#### Coverage verification tests

Added to `tests/validate_translations.py` (or an equivalent):

- Every `CHECK_CATALOGUE` row has all six IDs (`-name`, `-short-title`, `-what`, `-why`, `-who`, and `pdf-remediation-<code>`) in both locales.
- Every progress step, error, status, and touchpoint has both locales.
- No orphan IDs in `pdf-checks.ftl` (IDs not matching any catalogue row).
- `en/pdf-*.ftl` and `fr/pdf-*.ftl` have exactly the same set of IDs.

### 8. Dependencies, stubs, typecheck, Ghostscript

#### New Python dependencies

```
pikepdf>=10.3.0            # PDF reading, structure tree
pdfminer.six>=20231228     # text extraction, content streams, font analysis (ships py.typed)
Pillow>=12.1.1             # image extraction
wcag-contrast-ratio>=0.9   # WCAG contrast
PyYAML>=6.0                # fixture sidecar metadata (confirm already present)
```

Removed:
- `pypdf2==3.0.1` — only used by `scraper._detect_pdf_language`; superseded by `auto_a11y/pdf/language.py`. Confirmed-unused elsewhere during implementation before removal.

#### Type-information per dep

| Package | `py.typed` | Stubs action |
|---|---|---|
| `pikepdf` | Yes | Use directly; local patch stub only if strict-mode walls appear |
| `pdfminer.six` | **Yes** (from 20231228 onwards) | Use directly; **do not** shadow the upstream types with local stubs. Stubs only if an upstream typing bug blocks progress. |
| `Pillow` | Yes | Use directly |
| `wcag-contrast-ratio` | No | **Hand-written `stubs/wcag_contrast_ratio/__init__.pyi`** |
| `PyYAML` | No | Add `types-PyYAML` to dev deps |
| `anthropic` | Yes | Already integrated |

Every stub is committed in the same commit as the corresponding `import`.

#### pikepdf typing strategy

pikepdf's idiomatic dynamic access (`obj.Root`, `obj["/Lang"]`) returns `Object`/`Any`-adjacent types. A typed wrapper module `auto_a11y/pdf/pikepdf_helpers.py` exposes narrowed accessors:

```python
def get_name(obj: pikepdf.Object, key: str) -> pikepdf.Name | None: ...
def get_array(obj: pikepdf.Object, key: str) -> pikepdf.Array | None: ...
def get_dict(obj: pikepdf.Object, key: str) -> pikepdf.Dictionary | None: ...
def as_string(obj: pikepdf.Object) -> str: ...
def resolve_object(obj: pikepdf.Object) -> pikepdf.Object: ...
```

Port refactors pdfMax's raw accessor patterns into helper calls. If pikepdf's own stubs miss a symbol the helpers need, a local patch stub under `stubs/pikepdf/` is added (with a comparison-against-upstream CI flag to catch drift).

Acceptance criterion: no `# type: ignore`, no `cast(Any, ...)`, no `-> Any`, no raw `pikepdf.Object` in public function signatures (only in helper internals).

#### Typecheck scope update

`pyproject.toml` updated consistently in all three `[tool.mypy]`, `[tool.pyright]`, `[tool.ty]` sections:

- Adds `auto_a11y/pdf/**`
- Adds `stubs/wcag_contrast_ratio/**` (pdfminer.six ships py.typed, so no pdfminer stubs)
- Adds `fixture_generation/pdf/**` (the other `fixture_generation/` subtrees stay excluded)
- Adds `scripts/port_pdfmax_remediation_guide.py`

Lists stay in sync across the three tools, per existing policy.

#### anthropic SDK integration

- Model from `CLAUDE_MODEL` config (not pdfMax's hardcoded choice).
- Prompt caching added via auto_a11y's `claude-api` skill conventions (invoked during implementation). pdfMax sends the same large PDF-structure context across multiple calls per audit; caching yields substantial savings.
- Extended thinking / tool use / structured output follow auto_a11y's `auto_a11y/ai/` conventions.
- Sync `anthropic.Anthropic` client inside the executor thread (we're already off the event loop).

#### Ghostscript

**Installation documentation** (README / CONTRIBUTING):
- Debian/Ubuntu: `apt-get install ghostscript`
- macOS: `brew install ghostscript`
- Windows dev: installer from ghostscript.com (`gswin64c.exe`)
- Docker: `ghostscript` added to `Dockerfile`'s `apt install` line
- CI: installed in the CI runner image
- Render.com: added to `render.yaml` build command

**Runtime detection:**
- `auto_a11y/pdf/audit/ghostscript.py` runs `shutil.which("gs")` (Windows variants: `gswin64c`, `gswin32c`) at module import time; caches the result.
- If `GHOSTSCRIPT_PATH` is set in config, overrides detection.
- Audit start raises `GhostscriptMissing` before any audit work if not found — fail fast.
- Existing diagnostic endpoint gains a "Ghostscript" row (path + version, or "NOT FOUND; PDF auditing unavailable").

#### Deployment notes

- **Render.com**: `render.yaml` build command updated; ephemeral filesystem limitation re-flagged — local storage works for dev/self-hosted, not Render's default plans. A future spec can add a GridFS/S3 adapter behind the `PdfStorage` interface.
- **Docker**: `Dockerfile` installs `ghostscript`, `docker-compose.yml` declares a `data/pdfs` volume.

#### Python version

Follows the project's existing floor (3.11 per CI matrix). All new code uses `from __future__ import annotations`.

#### Acceptance criteria for dependency wiring

- `python -m mypy` passes on new scope additions
- `python -m pyright` passes on same
- `python -m ty check` passes on same
- `python -c "from auto_a11y.pdf.audit import run_audit"` imports cleanly
- `python run.py --test-db` still works
- `python -m auto_a11y.pdf.audit.ghostscript --check` prints path + version if installed, exits non-zero otherwise

## Acceptance criteria (whole spec)

### Functional

- Uploading a PDF via the web UI produces a `PdfDocument`, runs an audit, and displays results within the existing reporting UI.
- Pasting a URL produces the same outcome, using the selected `WebsiteUser` for authenticated downloads when provided.
- Running an HTML `test_page` against a URL that serves a PDF (direct or via redirect) detects the response, creates a `PdfDocument`, runs the audit, attaches the `TestResult` to the `PdfDocument`, and marks the `Page` as `IS_PDF`.
- Re-uploading or re-fetching an identical PDF (same SHA-256 within the same website) reuses the existing `PdfDocument` and does not duplicate storage.
- Deleting a `PdfDocument` removes its file(s), images directory, and all associated `TestResult`s.
- Deleting a `Website` or `Project` cascades through to its `PdfDocument`s.
- "View at page N" links on violations scroll the iframe to the correct page via `#page=N` fragment.

### Quality

- Every stable check code in `CHECK_CATALOGUE` has:
  - `fail.pdf` + `pass.pdf` fixtures that both pass `test_fixtures.py`
  - English Fluent strings for `name`, `short_title`, `what`, `why`, `who`, `remediation`
  - French Fluent strings for the same
  - A generator function registered in `fixture_generation/pdf/`
- `test_fixtures.py --pdf-only` reports 100% pass rate before merge.
- `tests/validate_translations.py` passes with the new coverage tests.
- `python -m mypy`, `python -m pyright`, `python -m ty check` all pass with no new suppressions.
- No `# type: ignore`, no `cast(Any, ...)`, no `-> Any`, no Bootstrap colour classes in new code.

### Observability

- Audit progress is reported through existing SSE infrastructure; `/pdfs/<id>` shows live progress for in-flight audits.
- Ghostscript availability is visible on the existing diagnostic endpoint.
- `PdfDocument.status` transitions are logged.

## Implementation phasing (planning-level preview)

Detailed implementation plan is produced by the `writing-plans` skill after this spec is approved. Rough ordering preview only:

1. **Foundation** — new models, storage, config, Ghostscript detection, typed errors, stubs for untyped deps.
2. **Engine port** — `auto_a11y/pdf/audit/` modules ported from pdfMax, refactored into focused units, strict-typed.
3. **Translation layer** — `CHECK_CATALOGUE`, `check_mapper`, remediation guide port, Fluent coverage.
4. **Async wrapper + testing-pipeline integration** — `PdfRunner`, `test_pdf`, opportunistic `test_page` branch.
5. **Web UI** — routes, templates, view models, static JS, navigation integration.
6. **Fixture generation** — migrate and extend pdfMax's generator; produce every check's fixture PDFs.
7. **Fixture integration in `test_fixtures.py` + `/testing/fixture-status`.**
8. **Migration** — non-destructive model additions, `PageStatus.IS_PDF`, scraper's language helper redirected.
9. **Cleanup** — remove `pypdf2` if unused; update `README.md`/`README.fr.md`; update CLAUDE.md to document the partial typecheck carve-out for `fixture_generation/pdf/**` (the rest of `fixture_generation/` stays excluded); document Ghostscript as a runtime system dependency.

### Enumeration pass — a dedicated implementation task

Because the `CHECK_CATALOGUE` enumeration (~200 rows) is the single longest-tailed piece of work in this spec — each row drives a fixture generator, six Fluent IDs per locale, and a translation-coverage assertion — the implementation plan should isolate it into its own sub-task (likely sitting between phases 2 and 3). Combing pdfMax's `pdf_accessibility_audit.py` to produce the catalogue is itself several hours of focused work, independent of any later row-by-row fixture/Fluent work.

## Open items surfaced to implementation phase

Not blockers for this spec; resolved during planning or implementation:

- Confirm exact existing touchpoint names (`Tables` vs `DataTables`, `Navigation` vs `LinksAndNavigation`, `Fonts` vs `Typography`) by reading `auto_a11y/core/touchpoints.py`.
- Confirm which HTTP client auto_a11y currently uses (likely `aiohttp`; verify).
- Confirm markdown filter availability for Fluent remediation strings (`markdown2` / `mistune` / custom).
- Enumerate the full `CHECK_CATALOGUE` (~200 rows) by combing pdfMax's audit code.
- Confirm whether `PyYAML` is already in the dep tree.
- Verify no other caller of `pypdf2` outside the scraper's language detector.
- Verify existing diagnostic endpoint path for the Ghostscript health row.

## References

- pdfMax source: `../pdfMax/python/checker/pdf_accessibility_audit.py`, `pdf_fix.py` (excluded), `remediation_guide.py`, `tests/generate_test_pdfs.py`
- auto_a11y CLAUDE.md — bilingual Fluent rule, colour system rule, strict typecheck rule, fixture rule, no-explicit-role/tabIndex rule
- auto_a11y existing specs: `2026-04-16-strict-type-checking-design.md`, `2026-04-16-fluent-strict-mode-design.md`, `2026-03-18-css-color-system-dark-mode-design.md`
