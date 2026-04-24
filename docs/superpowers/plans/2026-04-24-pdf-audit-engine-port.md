# PDF Audit Engine Port Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port pdfMax's Python PDF accessibility audit engine into auto_a11y as a native testing path, with a new `PdfDocument` model, polymorphic `TestResult`, full Fluent EN/FR coverage, strict-typed code, and fixture-gated production enablement — so that when auto_a11y encounters a PDF (upload, URL entry, or `test_page` hitting a PDF response), it runs the audit and surfaces results as first-class `Violation`/`AIFinding` data.

**Architecture:** New `auto_a11y/pdf/` package holds the ported engine split into focused submodules (data collectors, per-touchpoint check modules, AI analysis, pipeline orchestrator). `PdfDocument` lives in its own collection with `(website_id, sha256)` dedup. `TestResult` gains polymorphic `target_type`/`target_id`. `PdfRunner` is the async wrapper that calls the sync audit via `loop.run_in_executor` on a dedicated thread pool. Scraper's PDF-language helper is refactored to share logic with the audit. Fixture tests extend `test_fixtures.py` to discover and run `Fixtures/PDF/**`. Web UI adds `/projects/<id>/pdfs` + `/pdfs/<id>` with inline iframe PDF viewer and `#page=N` deep links.

**Tech Stack:** Python 3.11, Flask, MongoDB (pymongo), `pikepdf`, `pdfminer.six`, `Pillow`, `wcag-contrast-ratio`, `anthropic`, `PyYAML`, Playwright (existing), `aiohttp` (existing), `fluent-compiler` (existing), Ghostscript (system dependency).

**Spec:** [2026-04-24-pdf-audit-engine-port-design.md](../specs/2026-04-24-pdf-audit-engine-port-design.md)

---

## File Inventory

### Files to Create

| File | Responsibility |
|------|----------------|
| `auto_a11y/pdf/__init__.py` | Package marker |
| `auto_a11y/pdf/errors.py` | Typed exceptions: `GhostscriptMissing`, `CorruptPdf`, `FetchFailed`, `PdfTooLarge`, `NotAPdf`, `PdfDocumentNotFound`, `CannotAuditFetchFailedDocument` |
| `auto_a11y/pdf/models.py` | Internal dataclasses: `CheckResult`, `TagElement`, `AuditContext`, `AuditResult`, `AIAnalysisResult` |
| `auto_a11y/pdf/language.py` | Shared PDF language helper (scraper + audit) |
| `auto_a11y/pdf/storage.py` | Filesystem storage: allocate, write, path lookup, cascading delete |
| `auto_a11y/pdf/fixtures.py` | PDF fixture sidecar YAML parsing + validation |
| `auto_a11y/pdf/audit/__init__.py` | Public `run_audit()` entry point |
| `auto_a11y/pdf/audit/pipeline.py` | Orchestrator: opens PDF, runs collectors + checks, returns `AuditResult` |
| `auto_a11y/pdf/audit/pikepdf_helpers.py` | Typed narrowing wrappers over pikepdf's dynamic API |
| `auto_a11y/pdf/audit/ghostscript.py` | Sole subprocess caller: `render_pdf_page_to_image` + detection |
| `auto_a11y/pdf/audit/structure.py` | `walk_structure_tree`, `populate_element_text`, tag tree |
| `auto_a11y/pdf/audit/content_streams.py` | MCID mapping, content stream text extraction |
| `auto_a11y/pdf/audit/colors.py` | `extract_text_colors`, `extract_form_field_colors` |
| `auto_a11y/pdf/audit/images.py` | `extract_images`, image inventory |
| `auto_a11y/pdf/audit/fonts.py` | Font analysis: size, face, italic, rotation, spacing, alignment |
| `auto_a11y/pdf/audit/reading_order.py` | Visual vs tag-tree reading order analysis |
| `auto_a11y/pdf/audit/checks/__init__.py` | Check registry + re-exports |
| `auto_a11y/pdf/audit/checks/document_properties.py` | Title, language, PDF/UA id, XMP, page labels, XFA, permissions |
| `auto_a11y/pdf/audit/checks/tagging_structure.py` | Tagged, role map, MCIDs, nesting, suspect tags, artifact boundaries |
| `auto_a11y/pdf/audit/checks/annotations.py` | Tagged annotations, alt descriptions, tab order, multimedia |
| `auto_a11y/pdf/audit/checks/lists.py` | List structure, nesting, empty lists, labels |
| `auto_a11y/pdf/audit/checks/tables.py` | Headers, scope, regularity, captions, complex headers |
| `auto_a11y/pdf/audit/checks/headings.py` | Hierarchy, size hierarchy |
| `auto_a11y/pdf/audit/checks/images_alt_text.py` | Alt on Figure/Art, adequacy, redundant role, images-of-text |
| `auto_a11y/pdf/audit/checks/links_navigation.py` | Link content, descriptiveness, bookmarks, label-in-name |
| `auto_a11y/pdf/audit/checks/forms.py` | Labels, required indicators, unique names, tab order |
| `auto_a11y/pdf/audit/checks/interactive.py` | Tab order, target size, focus indicator |
| `auto_a11y/pdf/audit/checks/color_contrast.py` | WCAG contrast evaluation |
| `auto_a11y/pdf/audit/checks/fonts.py` | Size, face, ratio, rotation, italic, spacing, alignment |
| `auto_a11y/pdf/audit/checks/language.py` | BCP 47 validation, language of parts, abbreviations |
| `auto_a11y/pdf/audit/ai/__init__.py` | AI sub-package marker |
| `auto_a11y/pdf/audit/ai/semantic.py` | `run_claude_semantic_analysis` refactored |
| `auto_a11y/pdf/audit/ai/executive_summary.py` | Structured summary schema + call |
| `auto_a11y/pdf/audit/ai/finding_mapper.py` | pdfMax AI output → `list[AIFinding]` |
| `auto_a11y/pdf/translation/__init__.py` | Sub-package marker |
| `auto_a11y/pdf/translation/check_mapper.py` | `CHECK_CATALOGUE` + `CheckResult` → `Violation` |
| `auto_a11y/pdf/translation/remediation_guide.py` | Stable-id → Fluent-key mapping (populated by porting script) |
| `auto_a11y/models/pdf_document.py` | `PdfDocument` dataclass + `PdfDocumentStatus` enum |
| `auto_a11y/testing/pdf_runner.py` | Async wrapper; dedicated `ThreadPoolExecutor`; fetch pipeline |
| `auto_a11y/web/routes/pdf.py` | Flask blueprint: list, detail, add, audit, file, images, delete |
| `auto_a11y/web/view_models/__init__.py` | Sub-package marker |
| `auto_a11y/web/view_models/target.py` | `TestTargetView` dataclass + factories |
| `auto_a11y/web/templates/pdf/list.html` | PDF list view (project- and website-scoped) |
| `auto_a11y/web/templates/pdf/add.html` | Upload + URL entry form |
| `auto_a11y/web/templates/pdf/detail.html` | Two-pane: iframe + test result |
| `auto_a11y/web/templates/pdf/_audit_progress.html` | SSE progress partial |
| `auto_a11y/web/templates/pdf/_empty_state.html` | "No PDFs yet" CTA |
| `auto_a11y/web/templates/_target_header.html` | Shared target header partial |
| `auto_a11y/web/templates/_target_result_summary.html` | Shared target result summary partial |
| `auto_a11y/web/static/js/pdf_viewer.js` | Iframe `#page=N` deep-link handler |
| `auto_a11y/web/translations/en/pdf.ftl` | UI strings |
| `auto_a11y/web/translations/fr/pdf.ftl` | UI strings |
| `auto_a11y/web/translations/en/pdf-checks.ftl` | Per-check strings (name/short_title/what/why/who) |
| `auto_a11y/web/translations/fr/pdf-checks.ftl` | Per-check strings |
| `auto_a11y/web/translations/en/pdf-remediation.ftl` | Per-check remediation |
| `auto_a11y/web/translations/fr/pdf-remediation.ftl` | Per-check remediation |
| `auto_a11y/web/translations/en/pdf-touchpoints.ftl` | New PDF touchpoint labels |
| `auto_a11y/web/translations/fr/pdf-touchpoints.ftl` | New PDF touchpoint labels |
| `auto_a11y/web/translations/en/pdf-progress.ftl` | Audit progress step names |
| `auto_a11y/web/translations/fr/pdf-progress.ftl` | Audit progress step names |
| `auto_a11y/web/translations/en/pdf-errors.ftl` | User-facing error messages |
| `auto_a11y/web/translations/fr/pdf-errors.ftl` | User-facing error messages |
| `auto_a11y/web/translations/en/pdf-status.ftl` | `PdfDocumentStatus` + `PageStatus.IS_PDF` labels |
| `auto_a11y/web/translations/fr/pdf-status.ftl` | Status labels |
| `stubs/wcag_contrast_ratio/__init__.pyi` | Stubs |
| *(no `stubs/pdfminer/`)* | pdfminer.six ships `py.typed` from 20231228+ — use upstream types directly. |
| `fixture_generation/pdf/__init__.py` | Package marker |
| `fixture_generation/pdf/generate_pdf_fixtures.py` | CLI entry + registry |
| `fixture_generation/pdf/builders.py` | Shared PDF-construction helpers |
| `fixture_generation/pdf/metadata.py` | Sidecar YAML writer |
| `fixture_generation/pdf/generators/__init__.py` | Package marker |
| `fixture_generation/pdf/generators/document_properties.py` | Generators for `PdfDocumentProperties` checks |
| `fixture_generation/pdf/generators/tagging_structure.py` | Generators for `PdfTagging` checks |
| `fixture_generation/pdf/generators/annotations.py` | Generators for `PdfAnnotations` checks |
| `fixture_generation/pdf/generators/lists.py` | Generators for `Lists` checks |
| `fixture_generation/pdf/generators/tables.py` | Generators for `Tables` checks |
| `fixture_generation/pdf/generators/headings.py` | Generators for `Headings` checks |
| `fixture_generation/pdf/generators/images_alt_text.py` | Generators for `Images` checks |
| `fixture_generation/pdf/generators/links_navigation.py` | Generators for `Navigation`/`Links` checks |
| `fixture_generation/pdf/generators/forms.py` | Generators for `Forms` checks |
| `fixture_generation/pdf/generators/interactive.py` | Generators for `Focus`/interactive checks |
| `fixture_generation/pdf/generators/color_contrast.py` | Generators for `ColorAndContrast` checks |
| `fixture_generation/pdf/generators/fonts.py` | Generators for `Fonts` checks |
| `fixture_generation/pdf/generators/language.py` | Generators for `Language` checks |
| `scripts/port_pdfmax_remediation_guide.py` | One-off porting of pdfMax's `remediation_guide.py` → Fluent |
| `tests/pdf/__init__.py` | Test package marker |
| `tests/pdf/test_errors.py` | Tests for typed exceptions |
| `tests/pdf/test_models.py` | Tests for internal dataclasses |
| `tests/pdf/test_language.py` | Tests for shared language helper |
| `tests/pdf/test_storage.py` | Tests for filesystem storage |
| `tests/pdf/test_fixtures_parser.py` | Tests for sidecar YAML parsing |
| `tests/pdf/test_ghostscript.py` | Tests for Ghostscript detection + rendering |
| `tests/pdf/test_pikepdf_helpers.py` | Tests for typed narrowing wrappers |
| `tests/pdf/test_structure.py` | Tests for tag-tree walking |
| `tests/pdf/test_content_streams.py` | Tests for MCID extraction |
| `tests/pdf/test_colors.py` | Tests for colour extraction |
| `tests/pdf/test_images.py` | Tests for image extraction |
| `tests/pdf/test_fonts.py` | Tests for font analysis |
| `tests/pdf/test_reading_order.py` | Tests for reading order |
| `tests/pdf/test_checks_document_properties.py` | Tests for document-properties checks |
| `tests/pdf/test_checks_tagging_structure.py` | Tests for tagging/structure checks |
| `tests/pdf/test_checks_annotations.py` | Tests for annotation checks |
| `tests/pdf/test_checks_lists.py` | Tests for list checks |
| `tests/pdf/test_checks_tables.py` | Tests for table checks |
| `tests/pdf/test_checks_headings.py` | Tests for heading checks |
| `tests/pdf/test_checks_images_alt_text.py` | Tests for image/alt-text checks |
| `tests/pdf/test_checks_links_navigation.py` | Tests for link/navigation checks |
| `tests/pdf/test_checks_forms.py` | Tests for form checks |
| `tests/pdf/test_checks_interactive.py` | Tests for interactive-element checks |
| `tests/pdf/test_checks_color_contrast.py` | Tests for colour contrast |
| `tests/pdf/test_checks_fonts.py` | Tests for font checks |
| `tests/pdf/test_checks_language.py` | Tests for language checks |
| `tests/pdf/test_ai_semantic.py` | Tests for AI analysis wiring (mocked) |
| `tests/pdf/test_ai_finding_mapper.py` | Tests for AI output → `AIFinding` |
| `tests/pdf/test_pipeline.py` | Tests for `run_audit()` end-to-end |
| `tests/pdf/test_check_mapper.py` | Tests for `CheckResult` → `Violation` + catalogue completeness |
| `tests/pdf/test_pdf_document_model.py` | Tests for `PdfDocument` dataclass |
| `tests/pdf/test_pdf_runner.py` | Tests for async wrapper |
| `tests/pdf/test_test_runner_pdf_branch.py` | Tests for `test_page` opportunistic PDF detection |
| `tests/pdf/test_pdf_routes.py` | Tests for Flask routes |
| `tests/pdf/test_target_view_model.py` | Tests for `TestTargetView` |
| `tests/pdf/test_translations_coverage.py` | Coverage test: every code has Fluent IDs in both locales |
| `Fixtures/PDF/README.md` | Overview of PDF fixture layout + regeneration instructions |

### Files to Modify

| File | What Changes |
|------|--------------|
| `requirements.txt` | Add `pikepdf`, `pdfminer.six`, `Pillow`, `wcag-contrast-ratio`, `PyYAML`, `types-PyYAML`, `markdown-it-py`; remove `pypdf2` after verification |
| `pyproject.toml` (`[tool.mypy]` `files`) | `stubs/wcag_contrast_ratio/` covered via existing `stubs/` include; add `fixture_generation/pdf` and `scripts/port_pdfmax_remediation_guide.py` |
| `pyproject.toml` (`[tool.pyright]` `include`) | Same additions |
| `pyproject.toml` (`[tool.ty.src]` `exclude`) | Remove the blanket `fixture_generation/` exclude and the `scripts/` exclude, replace with narrower excludes preserving current behaviour while opting `fixture_generation/pdf/` and `scripts/port_pdfmax_remediation_guide.py` in |
| `config.py` | Add `PDF_STORAGE_DIR`, `PDF_MAX_SIZE_MB`, `PDF_DOWNLOAD_TIMEOUT_SECONDS`, `PDF_AUDIT_MAX_PARALLEL`, `GHOSTSCRIPT_PATH` |
| `auto_a11y/models/__init__.py` | Export `PdfDocument`, `PdfDocumentStatus`, `TargetType` |
| `auto_a11y/models/page.py` | Add `PageStatus.IS_PDF`; add `linked_pdf_document_id: str \| None = None` on `Page`; update `to_dict`/`from_dict` |
| `auto_a11y/models/test_result.py` | Add `TargetType` enum; add `target_type`/`target_id` fields; non-destructive read path |
| `auto_a11y/core/database.py` | Add `pdf_documents` collection accessor + indexes; add CRUD methods: `create_pdf_document`, `get_pdf_document`, `update_pdf_document`, `delete_pdf_document`, `find_pdf_document_by_sha256`, `get_pdf_documents`; cascade handling on website/project delete; `get_test_results` variant filtering by `target_type` |
| `auto_a11y/core/touchpoints.py` | Add `PDF_TAGGING`, `PDF_DOCUMENT_PROPERTIES`, `PDF_ANNOTATIONS` to `Touchpoint` enum; register in `TOUCHPOINTS` dict; add WCAG criteria mappings |
| `auto_a11y/core/scraper.py` (lines 1050-1137) | Replace `_detect_pdf_language` body with delegation to `auto_a11y.pdf.language` |
| `auto_a11y/testing/test_runner.py` (lines 55-525) | Add opportunistic PDF detection branch before `wait_for_selector('body')`; add `async def test_pdf(pdf_document_id, ...)` |
| `auto_a11y/testing/__init__.py` | Export `PdfRunner` (if not already imported from sub-module) |
| `auto_a11y/web/routes/__init__.py` | Import + export `pdf_bp` |
| `auto_a11y/web/routes/pages.py` | Page listings render "→ PDF" badge when `status=IS_PDF` |
| `auto_a11y/web/routes/projects.py` | Project detail page gains "PDFs" link + count badge |
| `auto_a11y/web/routes/websites.py` | Website detail page gains "PDFs" section + count badge |
| `auto_a11y/web/templates/projects/view.html` | "PDFs" link with count |
| `auto_a11y/web/templates/websites/view.html` | "PDFs" section with count |
| `auto_a11y/web/templates/pages/view.html` | `status=IS_PDF` renders badge + link |
| `auto_a11y/web/translations/en/testing.ftl` | Add "PDF Checks" tab labels + any new fixture-status strings |
| `auto_a11y/web/translations/fr/testing.ftl` | Same |
| `test_fixtures.py` | Add `run_pdf_fixture`, `--pdf-only`, `--html-only`, `--target`, `--code`, `--touchpoint` flags; enumerate `Fixtures/PDF/**` |
| `tests/validate_translations.py` (if present) or new test | Add coverage checks for per-check Fluent IDs |
| `Dockerfile` | Add `ghostscript` to apt install line; create `data/pdfs` directory |
| `docker-compose.yml` | Add `data/pdfs` volume mount |
| `render.yaml` | Add `ghostscript` to build command |
| `README.md` | Document PDF auditing feature + Ghostscript system requirement |
| `README.fr.md` | Same in French |
| `CLAUDE.md` | Document partial typecheck carve-out for `fixture_generation/pdf/**`; document Ghostscript runtime requirement |

### No Code Changes Required

- `auto_a11y/models/document_reference.py` — `DocumentReference` stays untouched (spec #2's concern).
- `pdfMax/python/checker/pdf_fix.py` — deferred to sub-project #4.
- pdfMax's React / Electron / webpack configs — deferred to sub-project #5.

---

## Conventions used in this plan

- **Branch:** Work on the existing `pdfmax-integration` branch. Never rewrite history (CLAUDE.md rule). Never `git commit --no-verify`.
- **Commits:** One logical unit per commit. Signed commits are disabled for this effort (user instruction); use `--no-gpg-sign` if GPG prompts stall.
- **TDD:** Every code task pairs a failing test (RED) with a minimal implementation (GREEN). Tests live under `tests/pdf/`.
- **Strict typecheck:** `mypy`, `pyright`, `ty` must all pass after each commit (pre-commit hook enforces this). No `# type: ignore`, no `cast(Any, ...)`, no `-> Any`.
- **Fluent EN/FR:** Every user-visible string lands in **both** `en/` and `fr/`. Missing either side fails the translation-coverage test.
- **Colour tokens:** No Bootstrap colour classes in any new template/CSS — use the custom token classes from `style.css`.
- **Native HTML elements:** No explicit `role`/`tabIndex` on elements with native semantics; use real `<button>`, `<a>`, `<ul>`, `<fieldset>`, etc.
- **Dependencies:** Every new dep is added in the **same commit** as its first `import`; any untyped dep gets its stub committed in that same commit.
- **Scale-out tasks:** Where a task applies a single pattern to ~200 check codes (fixtures, Fluent strings, catalogue rows), the plan gives one concrete worked example + a "sweep" task with the repeat count. The sweep task's Done criterion is the coverage test passing, not manual enumeration.

## Spec-writing open items resolved during planning

The spec listed these "confirm during planning":

- **Touchpoint names** — confirmed from `auto_a11y/core/touchpoints.py`: `TABLES` (not "DataTables"), `NAVIGATION` (not "LinksAndNavigation"), `FONTS` (not "Typography"), `IMAGES`, `HEADINGS`, `FORMS`, `LANGUAGE`, `COLORS` (not "ColorAndContrast"), `FOCUS_MANAGEMENT` (not "Focus"). **Spec section 3's touchpoint list is updated here**: use `COLORS` instead of `ColorAndContrast`, `FOCUS_MANAGEMENT` instead of `Focus`, `LINKS` + `NAVIGATION` split (pdfMax's "Links & Navigation" maps onto `LINKS` for link-content checks and `NAVIGATION` for bookmarks).
- **HTTP client** — `aiohttp==3.13.3` is already in `requirements.txt`. Manual-URL and opportunistic-fallback fetches use it.
- **Markdown filter** — no markdown library currently in `requirements.txt`. This plan adds `markdown-it-py` (fast, pure-Python, has `py.typed`, already in the dependency ecosystem of `mdformat`/many Flask apps) for rendering remediation text.
- **`CHECK_CATALOGUE` enumeration** — dedicated Phase 6 (`Task 6.1`).
- **`PyYAML` presence** — not in `requirements.txt`. This plan adds it + `types-PyYAML`.
- **`pypdf2` other callers** — confirmed: only `auto_a11y/core/scraper.py:1103` imports `PyPDF2`. Safe to remove after Phase 2.
- **Ghostscript health endpoint** — no existing `/setup-check` endpoint found; this plan adds a `GET /api/health/pdf` endpoint (Phase 1) that reports Ghostscript + storage writability.

---

## Prerequisites

- Virtualenv active: `source venv/bin/activate`
- MongoDB running locally for tests that touch the DB: `mongod` (or Docker Compose)
- Ghostscript installed on host: `gs --version` should print a version ≥ 10.
- Pre-commit hooks active: `python run.py --install-hooks` (one-time)
- Baseline typecheck/test green:

```bash
python -m pytest tests/ -x --ignore=tests/pdf 2>&1 | tail -5
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

All must pass before starting Phase 0. If any fail, stop and investigate first.

---

# Phase 0: Prerequisites & Baseline

## Task 0.1: Verify baseline is green

**Files:** None (read-only).

- [ ] **Step 0.1.1: Run existing tests**

```bash
python -m pytest tests/ -x --ignore=tests/pdf -q 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 0.1.2: Run typecheckers**

```bash
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

Expected: no errors from any tool.

- [ ] **Step 0.1.3: Verify Ghostscript availability on dev host**

```bash
gs --version 2>&1
```

Expected: a version string like `10.x`. If missing: `sudo apt-get install ghostscript` (Debian/Ubuntu) or `brew install ghostscript` (macOS).

- [ ] **Step 0.1.4: Verify pdfMax source tree is reachable**

```bash
test -f ../pdfMax/python/checker/pdf_accessibility_audit.py && \
  wc -l ../pdfMax/python/checker/pdf_accessibility_audit.py \
  ../pdfMax/python/checker/remediation_guide.py \
  ../pdfMax/python/checker/pdf_fix.py \
  ../pdfMax/tests/generate_test_pdfs.py 2>&1
```

Expected: `pdf_accessibility_audit.py ≈ 12500 lines`, `remediation_guide.py ≈ 5500 lines`, `pdf_fix.py ≈ 4500 lines`, `generate_test_pdfs.py` present. If missing: stop; the port can't proceed without the source.

---

# Phase 1: Foundation — Models, Storage, Config, Errors, Dependencies, Stubs

## Task 1.1: Add Python dependencies (part 1 — non-typed libs need stubs first)

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1.1.1: Append new dependencies**

Append to `requirements.txt` (alphabetic where convenient; current file mixes orderings so just append at the bottom):

```
# PDF audit engine (added for sub-project #1 of pdfMax integration)
pikepdf==10.3.0
pdfminer.six==20231228
Pillow==12.1.1
wcag-contrast-ratio==0.9
PyYAML==6.0.2
types-PyYAML==6.0.12.20240917
markdown-it-py==3.0.0
```

- [ ] **Step 1.1.2: Install**

```bash
pip install -r requirements.txt 2>&1 | tail -5
```

Expected: new packages installed; no conflicts with existing deps.

- [ ] **Step 1.1.3: Verify imports**

```bash
python -c "import pikepdf, pdfminer.high_level, PIL, wcag_contrast_ratio, yaml, markdown_it; print('ok')"
```

Expected: `ok`. If any fails: investigate before proceeding.

- [ ] **Step 1.1.4: Do NOT commit yet — stubs in next task must land in the same commit as these deps**

The CLAUDE.md rule is: any new dep without `py.typed` must have its stubs committed *in the same commit*. Pooling deps + stubs into one commit requires Task 1.2 to complete first.

## Task 1.2: Write stubs for `wcag-contrast-ratio`

**Rationale:** `wcag-contrast-ratio` has no `py.typed`, no `types-*` package. CLAUDE.md requires hand-written stubs for every symbol we import. `pdfminer.six` was *previously thought* to need stubs, but verification showed it ships `py.typed` from version 20231228 onwards — shadowing the upstream types would be strictly worse, so no pdfminer stubs are written.

**Files:**
- Create: `stubs/wcag_contrast_ratio/__init__.pyi`

> **Correction note:** the earlier draft of this plan called for hand stubs in `stubs/pdfminer/`. Verification during implementation showed pdfminer.six ships `py.typed` from version 20231228 onwards, so local stubs would shadow (and degrade) the upstream types. No pdfminer stubs are written. The steps below pertain to the wcag_contrast_ratio stub only.

- [ ] **Step 1.2.1: Identify the symbols pdfMax actually imports (wcag_contrast_ratio only)**

```bash
grep -hE "^from wcag_contrast_ratio|^import wcag_contrast" \
  ../pdfMax/python/checker/pdf_accessibility_audit.py | sort -u
```

The stubs must expose every imported symbol with precise types — no `Any`.

- [ ] **Step 1.2.2: Create `stubs/wcag_contrast_ratio/__init__.pyi`**

Tiny library; full shape:

```python
# Hand-written stub — wcag-contrast-ratio does not ship py.typed.

def rgb(
    rgb1: tuple[float, float, float],
    rgb2: tuple[float, float, float],
) -> float:
    """Return the WCAG contrast ratio between two RGB colours (0-1 floats)."""
    ...


def passes_AA(contrast_ratio: float, large: bool = ...) -> bool: ...


def passes_AAA(contrast_ratio: float, large: bool = ...) -> bool: ...
```

(Confirm exact signatures against the library source at install time.)

- [ ] **Step 1.2.7: Verify typecheckers still pass with the stubs but no importers yet**

```bash
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

Expected: all green. If a stub has a syntax error or internal inconsistency, fix before proceeding.

- [ ] **Step 1.2.8: Commit deps + stubs together**

```bash
git add requirements.txt stubs/pdfminer/ stubs/wcag_contrast_ratio/
git commit --no-gpg-sign -m "$(cat <<'EOF'
Add PDF-audit dependencies with hand-written stubs

Adds pikepdf, pdfminer.six, Pillow, wcag-contrast-ratio, PyYAML,
types-PyYAML, and markdown-it-py for the pdfMax audit engine port.
Hand-written stubs for pdfminer.six and wcag-contrast-ratio cover
every symbol imported by the port, with no Any.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: pre-commit hook runs, all three typecheckers pass, commit created.

## Task 1.3: Update typecheck scope in `pyproject.toml`

**Rationale:** `fixture_generation/pdf/**` and `scripts/port_pdfmax_remediation_guide.py` must opt *in* to strict typing while the rest of `fixture_generation/` and `scripts/` stay excluded.

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1.3.1: Update `[tool.ty.src]` `exclude`**

Current:
```toml
exclude = [
    "archive/",
    "demo_site/",
    "fixture_generation/",
    ...
    "scripts/",
]
```

New:
```toml
exclude = [
    "archive/",
    "demo_site/",
    # fixture_generation/ is mostly excluded, but fixture_generation/pdf/ is opted in
    "fixture_generation/ai_generated/",
    "fixture_generation/cvcb/",
    "fixture_generation/drupal/",
    # ... (enumerate existing subdirs except pdf/); if the list grows unwieldy,
    # use a glob like "fixture_generation/[!p]*/" instead
    ...
    # scripts/ is mostly excluded, but scripts/port_pdfmax_remediation_guide.py is opted in
    "scripts/drupal*.py",
    "scripts/recording*.py",
    # ... (enumerate existing scripts except the remediation porter)
]
```

Before editing, inspect actual subdirs:

```bash
ls fixture_generation/ 2>&1
ls scripts/ 2>&1
```

Pick the narrowest correct exclusion list that keeps current untyped files excluded while admitting `fixture_generation/pdf/` and `scripts/port_pdfmax_remediation_guide.py`.

- [ ] **Step 1.3.2: `[tool.mypy]` and `[tool.pyright]` — nothing to change**

Because those tools use `files`/`include` (allowlist) rather than `exclude`, we add the new paths when they exist. At this point `fixture_generation/pdf/` and `scripts/port_pdfmax_remediation_guide.py` don't exist yet — skip for now. They'll be added to `files`/`include` in Phase 10 / Phase 7 respectively when the files land.

- [ ] **Step 1.3.3: Run typecheckers to confirm no regression**

```bash
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

Expected: all green, same as before (the `exclude` change is a no-op until new files appear).

- [ ] **Step 1.3.4: Commit**

```bash
git add pyproject.toml
git commit --no-gpg-sign -m "Narrow ty exclude list to allow fixture_generation/pdf/ and PDF porting script

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.4: Config additions

**Files:**
- Modify: `config.py`

- [ ] **Step 1.4.1: Locate the Config class + existing env-var loading pattern**

```bash
grep -n "class Config\|os.environ\|os.getenv" config.py | head -20
```

- [ ] **Step 1.4.2: Add PDF-specific config**

Append to `Config` class (match the existing style — whether they're class attributes reading from env, `@property`, or instance attributes set in `__init__`):

```python
    # PDF audit engine — sub-project #1 of pdfMax integration
    PDF_STORAGE_DIR: str = os.environ.get('PDF_STORAGE_DIR', 'data/pdfs')
    PDF_MAX_SIZE_MB: int = int(os.environ.get('PDF_MAX_SIZE_MB', '100'))
    PDF_DOWNLOAD_TIMEOUT_SECONDS: int = int(os.environ.get('PDF_DOWNLOAD_TIMEOUT_SECONDS', '60'))
    PDF_AUDIT_MAX_PARALLEL: int = int(os.environ.get('PDF_AUDIT_MAX_PARALLEL', '2'))
    GHOSTSCRIPT_PATH: str | None = os.environ.get('GHOSTSCRIPT_PATH') or None
```

(Adjust to match the exact pattern used by neighbouring config keys.)

- [ ] **Step 1.4.3: Write a minimal test**

Create `tests/pdf/__init__.py` (empty).

Create `tests/pdf/test_config.py`:

```python
"""Tests for PDF-specific Config entries."""
from __future__ import annotations

from config import Config


def test_config_exposes_pdf_defaults() -> None:
    cfg = Config()
    assert cfg.PDF_STORAGE_DIR == 'data/pdfs'
    assert cfg.PDF_MAX_SIZE_MB == 100
    assert cfg.PDF_DOWNLOAD_TIMEOUT_SECONDS == 60
    assert cfg.PDF_AUDIT_MAX_PARALLEL == 2
    assert cfg.GHOSTSCRIPT_PATH is None
```

- [ ] **Step 1.4.4: Run the test**

```bash
python -m pytest tests/pdf/test_config.py -v
```

Expected: pass.

- [ ] **Step 1.4.5: Commit**

```bash
git add config.py tests/pdf/__init__.py tests/pdf/test_config.py
git commit --no-gpg-sign -m "Add PDF audit config keys

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.5: Create typed errors module

**Files:**
- Create: `auto_a11y/pdf/__init__.py` (empty)
- Create: `auto_a11y/pdf/errors.py`
- Create: `tests/pdf/test_errors.py`

- [ ] **Step 1.5.1: Write test first (RED)**

Create `tests/pdf/test_errors.py`:

```python
"""Tests for typed PDF exceptions."""
from __future__ import annotations

import pytest

from auto_a11y.pdf.errors import (
    CannotAuditFetchFailedDocument,
    CorruptPdf,
    FetchFailed,
    GhostscriptMissing,
    NotAPdf,
    PdfDocumentNotFound,
    PdfError,
    PdfTooLarge,
)


def test_all_errors_inherit_pdf_error() -> None:
    assert issubclass(GhostscriptMissing, PdfError)
    assert issubclass(CorruptPdf, PdfError)
    assert issubclass(FetchFailed, PdfError)
    assert issubclass(PdfTooLarge, PdfError)
    assert issubclass(NotAPdf, PdfError)
    assert issubclass(PdfDocumentNotFound, PdfError)
    assert issubclass(CannotAuditFetchFailedDocument, PdfError)


def test_ghostscript_missing_carries_searched_paths() -> None:
    err = GhostscriptMissing(searched=['gs', 'gswin64c'])
    assert 'gs' in str(err)
    assert err.searched == ['gs', 'gswin64c']


def test_fetch_failed_carries_url_and_reason() -> None:
    err = FetchFailed(url='https://example.com/doc.pdf', reason='HTTP 403')
    assert 'https://example.com/doc.pdf' in str(err)
    assert 'HTTP 403' in str(err)


def test_pdf_too_large_carries_size_and_limit() -> None:
    err = PdfTooLarge(size_bytes=200_000_000, limit_bytes=100_000_000)
    assert err.size_bytes == 200_000_000
    assert err.limit_bytes == 100_000_000
```

- [ ] **Step 1.5.2: Run test (expect failure: module missing)**

```bash
python -m pytest tests/pdf/test_errors.py -v
```

Expected: `ModuleNotFoundError: No module named 'auto_a11y.pdf'`.

- [ ] **Step 1.5.3: Create `auto_a11y/pdf/__init__.py`**

Empty file (package marker).

- [ ] **Step 1.5.4: Implement `auto_a11y/pdf/errors.py`**

```python
"""Typed exceptions for the PDF audit subsystem."""
from __future__ import annotations


class PdfError(Exception):
    """Base class for PDF subsystem errors."""


class GhostscriptMissing(PdfError):
    """Ghostscript binary could not be located."""

    def __init__(self, searched: list[str]) -> None:
        self.searched = searched
        super().__init__(
            f"Ghostscript not found on PATH (searched: {', '.join(searched)}). "
            f"Install Ghostscript or set GHOSTSCRIPT_PATH in config."
        )


class CorruptPdf(PdfError):
    """pikepdf could not open the PDF."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Cannot open PDF at {path}: {reason}")


class FetchFailed(PdfError):
    """Downloading the PDF from a URL failed."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"Failed to fetch {url}: {reason}")


class PdfTooLarge(PdfError):
    """PDF exceeds configured maximum size."""

    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"PDF size {size_bytes} bytes exceeds limit {limit_bytes} bytes."
        )


class NotAPdf(PdfError):
    """Bytes don't start with the %PDF- magic header."""


class PdfDocumentNotFound(PdfError):
    """No PdfDocument exists with the given id."""

    def __init__(self, pdf_document_id: str) -> None:
        self.pdf_document_id = pdf_document_id
        super().__init__(f"PdfDocument not found: {pdf_document_id}")


class CannotAuditFetchFailedDocument(PdfError):
    """Attempted to audit a document whose initial fetch failed."""
```

- [ ] **Step 1.5.5: Run test (expect pass)**

```bash
python -m pytest tests/pdf/test_errors.py -v
```

Expected: all pass.

- [ ] **Step 1.5.6: Run typecheckers**

```bash
python -m mypy auto_a11y/pdf/errors.py tests/pdf/test_errors.py 2>&1 | tail -3
python -m pyright auto_a11y/pdf/errors.py tests/pdf/test_errors.py 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

Expected: all green.

- [ ] **Step 1.5.7: Commit**

```bash
git add auto_a11y/pdf/__init__.py auto_a11y/pdf/errors.py tests/pdf/test_errors.py
git commit --no-gpg-sign -m "Add typed PDF subsystem exceptions

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.6: Create `PdfDocument` model + `PdfDocumentStatus` enum

**Files:**
- Create: `auto_a11y/models/pdf_document.py`
- Modify: `auto_a11y/models/__init__.py`
- Create: `tests/pdf/test_pdf_document_model.py`

- [ ] **Step 1.6.1: Write test first**

Create `tests/pdf/test_pdf_document_model.py`:

```python
"""Tests for the PdfDocument model."""
from __future__ import annotations

from datetime import datetime

import pytest
from bson import ObjectId

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus


def test_pdf_document_round_trips_via_dict() -> None:
    now = datetime(2026, 4, 24, 12, 0, 0)
    doc = PdfDocument(
        website_id='w1',
        project_id='p1',
        source_url='https://example.com/doc.pdf',
        source_type='manual_url',
        discovered_from_page_id=None,
        discovered_from_user_id='u1',
        sha256='a' * 64,
        file_size_bytes=1024,
        storage_relpath='w1/d1/pdf.pdf',
        images_relpath='w1/d1/images/',
        original_filename='doc.pdf',
        pdf_version='1.7',
        page_count=10,
        declared_lang='en',
        detected_lang='en',
        lang_confidence=0.95,
        status=PdfDocumentStatus.AUDITED,
        error_reason=None,
        last_audit_result_id='r1',
        discovered_at=now,
        last_audited_at=now,
    )
    as_dict = doc.to_dict()
    assert as_dict['sha256'] == 'a' * 64
    assert as_dict['status'] == 'audited'
    round_tripped = PdfDocument.from_dict(as_dict)
    assert round_tripped.sha256 == doc.sha256
    assert round_tripped.status == PdfDocumentStatus.AUDITED


def test_pdf_document_minimal_fields() -> None:
    now = datetime.now()
    doc = PdfDocument(
        website_id='w1',
        project_id='p1',
        source_url=None,
        source_type='uploaded',
        discovered_from_page_id=None,
        discovered_from_user_id='u1',
        sha256='b' * 64,
        file_size_bytes=1,
        storage_relpath='w1/d1/pdf.pdf',
        images_relpath='w1/d1/images/',
        original_filename='x.pdf',
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=PdfDocumentStatus.PENDING,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=now,
        last_audited_at=None,
    )
    assert doc.id is None


def test_pdf_document_id_property_from_objectid() -> None:
    doc = PdfDocument(
        website_id='w1',
        project_id='p1',
        source_url=None,
        source_type='uploaded',
        discovered_from_page_id=None,
        discovered_from_user_id='u1',
        sha256='c' * 64,
        file_size_bytes=1,
        storage_relpath='w1/d1/pdf.pdf',
        images_relpath='w1/d1/images/',
        original_filename='x.pdf',
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=PdfDocumentStatus.PENDING,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
        _id=ObjectId(),
    )
    assert isinstance(doc.id, str)
    assert len(doc.id) == 24


@pytest.mark.parametrize("status", list(PdfDocumentStatus))
def test_status_enum_round_trips(status: PdfDocumentStatus) -> None:
    assert PdfDocumentStatus(status.value) is status
```

- [ ] **Step 1.6.2: Run test (expect failure)**

```bash
python -m pytest tests/pdf/test_pdf_document_model.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 1.6.3: Implement `auto_a11y/models/pdf_document.py`**

Copy `auto_a11y/models/document_reference.py` as a reference shape; the new model follows the same dataclass + `to_dict` / `from_dict` / `id` / `mongo_id` conventions. Full content:

```python
"""PdfDocument model for downloaded, auditable PDF artefacts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from bson import ObjectId


class PdfDocumentStatus(Enum):
    """Lifecycle states for a PdfDocument."""

    PENDING = "pending"           # created; no audit has run yet
    FETCHING = "fetching"         # downloading the bytes
    FETCH_FAILED = "fetch_failed" # download or auth error
    AUDITING = "auditing"         # audit in progress
    AUDITED = "audited"           # at least one successful audit
    AUDIT_FAILED = "audit_failed" # last audit raised before completion


SourceType = Literal["uploaded", "manual_url", "opportunistic"]


@dataclass
class PdfDocument:
    """A downloaded, audit-ready PDF artefact."""

    website_id: str
    project_id: str                         # denormalized for project-level queries
    source_url: str | None
    source_type: SourceType
    discovered_from_page_id: str | None
    discovered_from_user_id: str | None

    sha256: str                             # 64-char hex; dedup key per website
    file_size_bytes: int
    storage_relpath: str                    # relative to Config.PDF_STORAGE_DIR
    images_relpath: str                     # relative; trailing slash

    original_filename: str | None
    pdf_version: str | None
    page_count: int | None
    declared_lang: str | None
    detected_lang: str | None
    lang_confidence: float | None

    status: PdfDocumentStatus
    error_reason: str | None
    last_audit_result_id: str | None

    discovered_at: datetime
    last_audited_at: datetime | None

    _id: ObjectId | None = None

    @property
    def id(self) -> str | None:
        return str(self._id) if self._id else None

    @property
    def mongo_id(self) -> ObjectId | None:
        return self._id

    @mongo_id.setter
    def mongo_id(self, value: ObjectId | None) -> None:
        self._id = value

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'website_id': self.website_id,
            'project_id': self.project_id,
            'source_url': self.source_url,
            'source_type': self.source_type,
            'discovered_from_page_id': self.discovered_from_page_id,
            'discovered_from_user_id': self.discovered_from_user_id,
            'sha256': self.sha256,
            'file_size_bytes': self.file_size_bytes,
            'storage_relpath': self.storage_relpath,
            'images_relpath': self.images_relpath,
            'original_filename': self.original_filename,
            'pdf_version': self.pdf_version,
            'page_count': self.page_count,
            'declared_lang': self.declared_lang,
            'detected_lang': self.detected_lang,
            'lang_confidence': self.lang_confidence,
            'status': self.status.value,
            'error_reason': self.error_reason,
            'last_audit_result_id': self.last_audit_result_id,
            'discovered_at': self.discovered_at,
            'last_audited_at': self.last_audited_at,
        }
        if self._id:
            data['_id'] = self._id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PdfDocument:
        source_type_raw: str = data['source_type']
        # Use `match` to narrow to the Literal type without cast/ignore.
        match source_type_raw:
            case "uploaded" | "manual_url" | "opportunistic":
                source_type: SourceType = source_type_raw
            case _:
                raise ValueError(f"Unknown source_type: {source_type_raw}")
        return cls(
            website_id=data['website_id'],
            project_id=data['project_id'],
            source_url=data.get('source_url'),
            source_type=source_type,
            discovered_from_page_id=data.get('discovered_from_page_id'),
            discovered_from_user_id=data.get('discovered_from_user_id'),
            sha256=data['sha256'],
            file_size_bytes=data['file_size_bytes'],
            storage_relpath=data['storage_relpath'],
            images_relpath=data['images_relpath'],
            original_filename=data.get('original_filename'),
            pdf_version=data.get('pdf_version'),
            page_count=data.get('page_count'),
            declared_lang=data.get('declared_lang'),
            detected_lang=data.get('detected_lang'),
            lang_confidence=data.get('lang_confidence'),
            status=PdfDocumentStatus(data['status']),
            error_reason=data.get('error_reason'),
            last_audit_result_id=data.get('last_audit_result_id'),
            discovered_at=data.get('discovered_at', datetime.now()),
            last_audited_at=data.get('last_audited_at'),
            _id=data.get('_id'),
        )
```

The `match` statement exhaustively narrows `source_type_raw` to the `SourceType` Literal without any `cast` or `# type: ignore`.

- [ ] **Step 1.6.4: Register in `auto_a11y/models/__init__.py`**

Add to the import block:

```python
from .pdf_document import PdfDocument, PdfDocumentStatus
```

Add to `__all__`:

```python
    'PdfDocument', 'PdfDocumentStatus',
```

- [ ] **Step 1.6.5: Run test**

```bash
python -m pytest tests/pdf/test_pdf_document_model.py -v
```

Expected: all pass.

- [ ] **Step 1.6.6: Typecheck**

```bash
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

All green.

- [ ] **Step 1.6.7: Commit**

```bash
git add auto_a11y/models/pdf_document.py auto_a11y/models/__init__.py tests/pdf/test_pdf_document_model.py
git commit --no-gpg-sign -m "Add PdfDocument model + PdfDocumentStatus enum

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.7: Add `PageStatus.IS_PDF` + `linked_pdf_document_id` to Page

**Files:**
- Modify: `auto_a11y/models/page.py`
- Modify: `tests/` (add or extend existing Page tests)

- [ ] **Step 1.7.1: Locate existing Page tests**

```bash
find tests -name "test_page*.py" -o -name "*test*page*.py" 2>&1 | head -5
```

- [ ] **Step 1.7.2: Write a failing test for the new field**

In the appropriate existing test file (or new `tests/pdf/test_page_pdf_link.py` if none fits):

```python
def test_page_linked_pdf_document_id_round_trips() -> None:
    page = Page(
        website_id='w1',
        url='https://example.com/doc',
        status=PageStatus.IS_PDF,
        linked_pdf_document_id='pdf-abc',
    )
    data = page.to_dict()
    assert data['status'] == 'is_pdf'
    assert data['linked_pdf_document_id'] == 'pdf-abc'
    restored = Page.from_dict(data)
    assert restored.status is PageStatus.IS_PDF
    assert restored.linked_pdf_document_id == 'pdf-abc'


def test_page_linked_pdf_document_id_defaults_none_on_old_records() -> None:
    page = Page.from_dict({
        'website_id': 'w1',
        'url': 'https://example.com/',
        'status': 'tested',
    })
    assert page.linked_pdf_document_id is None
```

- [ ] **Step 1.7.3: Run test (expect failure)**

```bash
python -m pytest tests/pdf/test_page_pdf_link.py -v
```

Expected: `PageStatus.IS_PDF` doesn't exist / `linked_pdf_document_id` missing.

- [ ] **Step 1.7.4: Add `IS_PDF` to `PageStatus` enum**

`auto_a11y/models/page.py`:

```python
class PageStatus(Enum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    TESTING = "testing"
    TESTED = "tested"
    ERROR = "error"
    SKIPPED = "skipped"
    DISCOVERY_FAILED = "discovery_failed"
    IS_PDF = "is_pdf"  # Page URL serves a PDF; result is on linked PdfDocument
```

- [ ] **Step 1.7.5: Add field to `Page` dataclass + `to_dict`/`from_dict`**

In the `Page` dataclass body (after existing fields):

```python
    linked_pdf_document_id: str | None = None
```

In `to_dict`:

```python
            'linked_pdf_document_id': self.linked_pdf_document_id,
```

In `from_dict`:

```python
            linked_pdf_document_id=data.get('linked_pdf_document_id'),
```

- [ ] **Step 1.7.6: Run tests + typecheck**

```bash
python -m pytest tests/pdf/test_page_pdf_link.py -v
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

All green.

- [ ] **Step 1.7.7: Commit**

```bash
git add auto_a11y/models/page.py tests/pdf/test_page_pdf_link.py
git commit --no-gpg-sign -m "Add PageStatus.IS_PDF and Page.linked_pdf_document_id

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.8: Add polymorphic target to `TestResult`

**Files:**
- Modify: `auto_a11y/models/test_result.py`
- Modify: `auto_a11y/models/__init__.py` (export `TargetType`)
- Create: `tests/pdf/test_test_result_polymorphism.py`

- [ ] **Step 1.8.1: Write tests first**

```python
"""Tests for TestResult polymorphic target_type/target_id."""
from __future__ import annotations

from auto_a11y.models.test_result import TargetType, TestResult


def test_test_result_new_writes_set_both_fields() -> None:
    tr = TestResult(page_id='p1', website_id='w1', target_type=TargetType.PAGE, target_id='p1')
    d = tr.to_dict()
    assert d['target_type'] == 'page'
    assert d['target_id'] == 'p1'
    assert d['page_id'] == 'p1'  # retained for back-compat


def test_test_result_pdf_target() -> None:
    tr = TestResult(
        page_id=None,
        website_id='w1',
        target_type=TargetType.PDF_DOCUMENT,
        target_id='pdf-42',
    )
    d = tr.to_dict()
    assert d['target_type'] == 'pdf_document'
    assert d['target_id'] == 'pdf-42'
    assert d['page_id'] is None


def test_test_result_old_record_infers_page_target() -> None:
    # Simulate a record written before this change
    data = {
        'page_id': 'p1',
        'website_id': 'w1',
        # no target_type, no target_id
    }
    tr = TestResult.from_dict(data)
    assert tr.target_type is TargetType.PAGE
    assert tr.target_id == 'p1'
```

- [ ] **Step 1.8.2: Run test (expect failure)**

```bash
python -m pytest tests/pdf/test_test_result_polymorphism.py -v
```

- [ ] **Step 1.8.3: Implement in `test_result.py`**

Read the current `TestResult` class carefully. Add:

```python
class TargetType(Enum):
    PAGE = "page"
    PDF_DOCUMENT = "pdf_document"
```

To the `TestResult` dataclass add fields (maintain back-compat by making them have default values derived from `page_id`):

```python
    target_type: TargetType = TargetType.PAGE
    target_id: str = ""   # filled by __post_init__ from page_id when page-targeted
```

Add `__post_init__`:

```python
    def __post_init__(self) -> None:
        if self.target_type is TargetType.PAGE and not self.target_id and self.page_id:
            self.target_id = self.page_id
        # Explicit runtime guard: a PDF-targeted result must have an id.
        if self.target_type is TargetType.PDF_DOCUMENT and not self.target_id:
            raise ValueError(
                "TestResult with target_type=PDF_DOCUMENT requires target_id"
            )
```

Update `to_dict` to include both new fields. Update `from_dict` to infer when missing:

```python
            target_type=TargetType(data['target_type']) if 'target_type' in data else TargetType.PAGE,
            target_id=data.get('target_id', data.get('page_id', '')),
```

- [ ] **Step 1.8.4: Export `TargetType` in `auto_a11y/models/__init__.py`**

Add `TargetType` to the `test_result` import line and to `__all__`.

- [ ] **Step 1.8.5: Run test + typecheck + existing TestResult tests**

```bash
python -m pytest tests/pdf/test_test_result_polymorphism.py -v
python -m pytest tests/ -k test_result -v
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

Expected: all green, including any existing TestResult tests (non-destructive change).

- [ ] **Step 1.8.6: Commit**

```bash
git add auto_a11y/models/test_result.py auto_a11y/models/__init__.py tests/pdf/test_test_result_polymorphism.py
git commit --no-gpg-sign -m "Add polymorphic target_type/target_id to TestResult

Non-destructive: old records without these fields are read as target_type=PAGE
with target_id inferred from page_id.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.9: Add new PDF touchpoints to `Touchpoint` enum

**Files:**
- Modify: `auto_a11y/core/touchpoints.py`
- Create: `tests/pdf/test_pdf_touchpoints.py`

- [ ] **Step 1.9.1: Write tests first**

```python
"""Tests for new PDF-specific touchpoints."""
from __future__ import annotations

from auto_a11y.core.touchpoints import Touchpoint, get_all_touchpoints


def test_pdf_touchpoints_exist() -> None:
    assert Touchpoint.PDF_TAGGING.value == 'pdf_tagging'
    assert Touchpoint.PDF_DOCUMENT_PROPERTIES.value == 'pdf_document_properties'
    assert Touchpoint.PDF_ANNOTATIONS.value == 'pdf_annotations'


def test_pdf_touchpoints_appear_in_registry() -> None:
    all_tp = get_all_touchpoints()
    ids = {tp['id'] for tp in all_tp}
    assert 'pdf_tagging' in ids
    assert 'pdf_document_properties' in ids
    assert 'pdf_annotations' in ids
```

- [ ] **Step 1.9.2: Run test (expect failure)**

- [ ] **Step 1.9.3: Add enum members + registry entries**

In `auto_a11y/core/touchpoints.py`:
- Add `PDF_TAGGING = "pdf_tagging"`, `PDF_DOCUMENT_PROPERTIES = "pdf_document_properties"`, `PDF_ANNOTATIONS = "pdf_annotations"` to the enum (keep alphabetical ordering of the enum — insert at the right place).
- Extend the `TOUCHPOINTS` dict with three entries mirroring the existing entries' shape (confirm exact keys by reading the current file; typically `name`, `description`, `wcag_criteria`).
- Extend `get_wcag_criteria_for_touchpoint` if it's hand-coded; otherwise the `TOUCHPOINTS` dict entry carries the criteria.

Initial WCAG mappings:
- `PDF_TAGGING`: `['1.3.1', '1.3.2', '4.1.2']`
- `PDF_DOCUMENT_PROPERTIES`: `['2.4.2', '3.1.1', '1.4.8']`
- `PDF_ANNOTATIONS`: `['1.3.1', '2.4.3', '4.1.2']`

- [ ] **Step 1.9.4: Run test + typecheck + existing touchpoint tests**

```bash
python -m pytest tests/pdf/test_pdf_touchpoints.py tests/ -k touchpoint -v
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

- [ ] **Step 1.9.5: Commit**

```bash
git add auto_a11y/core/touchpoints.py tests/pdf/test_pdf_touchpoints.py
git commit --no-gpg-sign -m "Add PdfTagging, PdfDocumentProperties, PdfAnnotations touchpoints

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.10: Database CRUD for `pdf_documents` collection

**Files:**
- Modify: `auto_a11y/core/database.py`
- Create: `tests/pdf/test_database_pdf_documents.py`

- [ ] **Step 1.10.1: Write tests first**

Create `tests/pdf/test_database_pdf_documents.py`:

```python
"""Tests for pdf_documents collection CRUD."""
from __future__ import annotations

from datetime import datetime

import pytest
from bson import ObjectId

from auto_a11y.core.database import Database
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from config import Config


@pytest.fixture
def db() -> Database:
    cfg = Config()
    database = Database(cfg.MONGODB_URI, cfg.DATABASE_NAME + "_test")
    database.db.pdf_documents.delete_many({})
    yield database
    database.db.pdf_documents.delete_many({})


def _make_doc(sha256: str = 'a' * 64) -> PdfDocument:
    return PdfDocument(
        website_id='w1',
        project_id='p1',
        source_url='https://example.com/doc.pdf',
        source_type='manual_url',
        discovered_from_page_id=None,
        discovered_from_user_id='u1',
        sha256=sha256,
        file_size_bytes=1024,
        storage_relpath='w1/xxx/pdf.pdf',
        images_relpath='w1/xxx/images/',
        original_filename='doc.pdf',
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=PdfDocumentStatus.PENDING,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
    )


def test_create_and_get_pdf_document(db: Database) -> None:
    doc_id = db.create_pdf_document(_make_doc())
    fetched = db.get_pdf_document(doc_id)
    assert fetched is not None
    assert fetched.sha256 == 'a' * 64


def test_dedup_by_website_and_sha(db: Database) -> None:
    doc_id = db.create_pdf_document(_make_doc(sha256='d' * 64))
    found = db.find_pdf_document_by_sha256('w1', 'd' * 64)
    assert found is not None
    assert found.id == doc_id
    not_found = db.find_pdf_document_by_sha256('w2', 'd' * 64)
    assert not_found is None


def test_update_pdf_document(db: Database) -> None:
    doc = _make_doc(sha256='e' * 64)
    doc_id = db.create_pdf_document(doc)
    fetched = db.get_pdf_document(doc_id)
    assert fetched is not None
    fetched.status = PdfDocumentStatus.AUDITED
    fetched.page_count = 42
    db.update_pdf_document(fetched)
    reloaded = db.get_pdf_document(doc_id)
    assert reloaded is not None
    assert reloaded.status is PdfDocumentStatus.AUDITED
    assert reloaded.page_count == 42


def test_delete_pdf_document(db: Database) -> None:
    doc_id = db.create_pdf_document(_make_doc(sha256='f' * 64))
    assert db.delete_pdf_document(doc_id) is True
    assert db.get_pdf_document(doc_id) is None


def test_get_pdf_documents_by_website(db: Database) -> None:
    db.create_pdf_document(_make_doc(sha256='1' * 64))
    db.create_pdf_document(_make_doc(sha256='2' * 64))
    docs = db.get_pdf_documents(website_id='w1')
    assert len(docs) == 2
```

- [ ] **Step 1.10.2: Run test (expect failure — methods don't exist)**

- [ ] **Step 1.10.3: Implement the methods**

In `auto_a11y/core/database.py`:

1. In `__init__`, add collection binding:
```python
    self.pdf_documents: Collection[dict[str, Any]] = self.db.pdf_documents
```

2. In `create_indexes` (or the indexes method, find it at ~line 229):
```python
    self.pdf_documents.create_index(
        [("website_id", 1), ("sha256", 1)], unique=True, name="pdf_documents_website_sha_unique"
    )
    self.pdf_documents.create_index([("project_id", 1), ("discovered_at", -1)])
    self.pdf_documents.create_index([("website_id", 1), ("discovered_at", -1)])
    self.pdf_documents.create_index("status")
```

3. Add CRUD methods (place near other collection methods):

```python
    def create_pdf_document(self, doc: PdfDocument) -> str:
        data = doc.to_dict()
        data.pop('_id', None)
        result = self.pdf_documents.insert_one(data)
        return str(result.inserted_id)

    def get_pdf_document(self, pdf_document_id: str) -> PdfDocument | None:
        try:
            obj_id = ObjectId(pdf_document_id)
        except Exception:
            return None
        doc = self.pdf_documents.find_one({"_id": obj_id})
        return PdfDocument.from_dict(doc) if doc else None

    def update_pdf_document(self, doc: PdfDocument) -> bool:
        if doc._id is None:
            raise ValueError("Cannot update a PdfDocument without _id")
        data = doc.to_dict()
        data.pop('_id', None)
        result = self.pdf_documents.update_one({"_id": doc._id}, {"$set": data})
        return result.modified_count > 0

    def delete_pdf_document(self, pdf_document_id: str) -> bool:
        try:
            obj_id = ObjectId(pdf_document_id)
        except Exception:
            return False
        # Cascade: delete associated test results
        self.test_results.delete_many({"target_type": "pdf_document", "target_id": pdf_document_id})
        result = self.pdf_documents.delete_one({"_id": obj_id})
        return result.deleted_count > 0

    def find_pdf_document_by_sha256(self, website_id: str, sha256: str) -> PdfDocument | None:
        doc = self.pdf_documents.find_one({"website_id": website_id, "sha256": sha256})
        return PdfDocument.from_dict(doc) if doc else None

    def get_pdf_documents(
        self,
        *,
        website_id: str | None = None,
        project_id: str | None = None,
        status: PdfDocumentStatus | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[PdfDocument]:
        query: dict[str, Any] = {}
        if website_id is not None:
            query['website_id'] = website_id
        if project_id is not None:
            query['project_id'] = project_id
        if status is not None:
            query['status'] = status.value
        docs = self.pdf_documents.find(query).sort("discovered_at", -1).skip(skip).limit(limit)
        return [PdfDocument.from_dict(d) for d in docs]
```

4. Update the website/project cascading delete methods (locate them via `grep -n "delete_website\|cascade\|delete_project" auto_a11y/core/database.py`) to also delete associated `PdfDocument`s.

- [ ] **Step 1.10.4: Run test + typecheck**

```bash
python -m pytest tests/pdf/test_database_pdf_documents.py -v
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

- [ ] **Step 1.10.5: Commit**

```bash
git add auto_a11y/core/database.py tests/pdf/test_database_pdf_documents.py
git commit --no-gpg-sign -m "Add pdf_documents collection CRUD with dedup index

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.11: Ghostscript detection module

**Files:**
- Create: `auto_a11y/pdf/audit/__init__.py` (empty for now)
- Create: `auto_a11y/pdf/audit/ghostscript.py`
- Create: `tests/pdf/test_ghostscript.py`

- [ ] **Step 1.11.1: Write test first**

```python
"""Tests for Ghostscript detection + rendering."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from auto_a11y.pdf.audit.ghostscript import (
    detect_ghostscript,
    invalidate_detection_cache,
)
from auto_a11y.pdf.errors import GhostscriptMissing


def test_detect_ghostscript_finds_installed_binary() -> None:
    invalidate_detection_cache()
    path = detect_ghostscript()
    assert path is not None
    assert Path(path).name.startswith(('gs', 'gswin'))


def test_detect_ghostscript_raises_when_missing() -> None:
    invalidate_detection_cache()
    with patch('auto_a11y.pdf.audit.ghostscript.shutil.which', return_value=None):
        with pytest.raises(GhostscriptMissing):
            detect_ghostscript(raise_if_missing=True)


def test_detect_ghostscript_returns_none_when_missing_without_raise() -> None:
    invalidate_detection_cache()
    with patch('auto_a11y.pdf.audit.ghostscript.shutil.which', return_value=None):
        assert detect_ghostscript(raise_if_missing=False) is None


def test_detect_ghostscript_honours_config_override(tmp_path: Path) -> None:
    invalidate_detection_cache()
    fake_gs = tmp_path / "gs"
    fake_gs.write_text("#!/bin/sh\necho fake\n")
    fake_gs.chmod(0o755)
    path = detect_ghostscript(override=str(fake_gs))
    assert path == str(fake_gs)
```

- [ ] **Step 1.11.2: Run test (expect failure)**

- [ ] **Step 1.11.3: Implement**

Create `auto_a11y/pdf/audit/__init__.py` (empty).

Create `auto_a11y/pdf/audit/ghostscript.py`:

```python
"""Ghostscript detection and page rendering.

Sole subprocess caller in the PDF audit package. All other audit modules
that need a raster image of a page go through `render_page_to_png`.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from auto_a11y.pdf.errors import GhostscriptMissing

_GS_SEARCH_NAMES = ['gs', 'gswin64c', 'gswin32c']
_cached_path: str | None = None
_cache_populated: bool = False


def invalidate_detection_cache() -> None:
    """Clear the detection cache. Useful for tests."""
    global _cached_path, _cache_populated
    _cached_path = None
    _cache_populated = False


def detect_ghostscript(
    *,
    override: str | None = None,
    raise_if_missing: bool = False,
) -> str | None:
    """Locate the Ghostscript binary on PATH.

    Args:
        override: If set, skip detection and use this path as-is.
        raise_if_missing: If True, raise GhostscriptMissing when not found.

    Returns:
        Path to the ghostscript executable, or None if not found.
    """
    global _cached_path, _cache_populated

    if override:
        return override

    if _cache_populated:
        if _cached_path is None and raise_if_missing:
            raise GhostscriptMissing(searched=_GS_SEARCH_NAMES)
        return _cached_path

    for name in _GS_SEARCH_NAMES:
        path = shutil.which(name)
        if path:
            _cached_path = path
            _cache_populated = True
            return path

    _cached_path = None
    _cache_populated = True
    if raise_if_missing:
        raise GhostscriptMissing(searched=_GS_SEARCH_NAMES)
    return None


def render_page_to_png(
    pdf_path: Path,
    page_num: int,
    *,
    dpi: int = 150,
    timeout_seconds: int = 30,
    gs_path_override: str | None = None,
) -> bytes | None:
    """Render a single PDF page to PNG bytes via Ghostscript.

    Args:
        pdf_path: Path to the PDF file.
        page_num: Zero-based page index.
        dpi: Rendering DPI.
        timeout_seconds: Subprocess timeout.
        gs_path_override: If set, use this Ghostscript path instead of detection.

    Returns:
        PNG bytes, or None on failure.
    """
    gs_path = detect_ghostscript(override=gs_path_override, raise_if_missing=True)
    assert gs_path is not None  # detect_ghostscript raised if not found
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        result = subprocess.run(
            [
                gs_path,
                "-dNOPAUSE",
                "-dBATCH",
                "-dSAFER",
                "-sDEVICE=png16m",
                f"-r{dpi}",
                f"-dFirstPage={page_num + 1}",
                f"-dLastPage={page_num + 1}",
                f"-sOutputFile={tmp_path}",
                str(pdf_path),
            ],
            capture_output=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0 or not tmp_path.exists():
            return None
        return tmp_path.read_bytes()
    except subprocess.TimeoutExpired:
        print(f"Warning: Ghostscript timed out on page {page_num}", file=sys.stderr)
        return None
    except FileNotFoundError:
        raise GhostscriptMissing(searched=[gs_path])
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
```

- [ ] **Step 1.11.4: Run tests + typecheck**

```bash
python -m pytest tests/pdf/test_ghostscript.py -v
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

- [ ] **Step 1.11.5: Commit**

```bash
git add auto_a11y/pdf/audit/__init__.py auto_a11y/pdf/audit/ghostscript.py tests/pdf/test_ghostscript.py
git commit --no-gpg-sign -m "Add Ghostscript detection + page-rendering helper

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.12: Storage module

**Files:**
- Create: `auto_a11y/pdf/storage.py`
- Create: `tests/pdf/test_storage.py`

- [ ] **Step 1.12.1: Write tests first**

```python
"""Tests for PdfStorage filesystem operations."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.storage import PdfStorage


@pytest.fixture
def storage(tmp_path: Path) -> PdfStorage:
    return PdfStorage(base_dir=tmp_path)


def _make_doc(website_id: str = 'w1', doc_id: str = '507f1f77bcf86cd799439011') -> PdfDocument:
    return PdfDocument(
        website_id=website_id, project_id='p1',
        source_url=None, source_type='uploaded',
        discovered_from_page_id=None, discovered_from_user_id='u1',
        sha256='a' * 64, file_size_bytes=1024,
        storage_relpath=f'{website_id}/{doc_id}/pdf.pdf',
        images_relpath=f'{website_id}/{doc_id}/images/',
        original_filename='x.pdf',
        pdf_version=None, page_count=None,
        declared_lang=None, detected_lang=None, lang_confidence=None,
        status=PdfDocumentStatus.PENDING, error_reason=None, last_audit_result_id=None,
        discovered_at=datetime.now(), last_audited_at=None,
    )


def test_allocate_and_write(storage: PdfStorage, tmp_path: Path) -> None:
    slot = storage.allocate_pdf(website_id='w1', pdf_document_id='abc')
    storage.write_pdf_bytes(slot, b'%PDF-1.7\ndata')
    assert (tmp_path / 'w1' / 'abc' / 'pdf.pdf').read_bytes().startswith(b'%PDF-')
    assert (tmp_path / 'w1' / 'abc' / 'images').is_dir()


def test_local_path(storage: PdfStorage, tmp_path: Path) -> None:
    doc = _make_doc()
    expected = tmp_path / doc.storage_relpath
    assert storage.local_path(doc) == expected


def test_delete_removes_whole_dir(storage: PdfStorage, tmp_path: Path) -> None:
    slot = storage.allocate_pdf(website_id='w1', pdf_document_id='xyz')
    storage.write_pdf_bytes(slot, b'%PDF-data')
    doc = _make_doc(doc_id='xyz')
    storage.delete(doc)
    assert not (tmp_path / 'w1' / 'xyz').exists()


def test_delete_website_cascades(storage: PdfStorage, tmp_path: Path) -> None:
    slot1 = storage.allocate_pdf(website_id='w2', pdf_document_id='doc1')
    slot2 = storage.allocate_pdf(website_id='w2', pdf_document_id='doc2')
    storage.write_pdf_bytes(slot1, b'%PDF-a')
    storage.write_pdf_bytes(slot2, b'%PDF-b')
    storage.delete_website('w2')
    assert not (tmp_path / 'w2').exists()


def test_write_is_atomic(storage: PdfStorage, tmp_path: Path) -> None:
    """The final file should only exist after a complete write."""
    slot = storage.allocate_pdf(website_id='w3', pdf_document_id='atomic')
    storage.write_pdf_bytes(slot, b'%PDF-1.7\nabc')
    final = tmp_path / 'w3' / 'atomic' / 'pdf.pdf'
    assert final.exists()
    # No lingering .tmp files
    leftover = list((tmp_path / 'w3' / 'atomic').glob('*.tmp'))
    assert leftover == []
```

- [ ] **Step 1.12.2: Run test (expect failure)**

- [ ] **Step 1.12.3: Implement `auto_a11y/pdf/storage.py`**

```python
"""Filesystem storage for PDF artefacts."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from auto_a11y.models.pdf_document import PdfDocument


@dataclass(frozen=True)
class AllocatedSlot:
    """Allocation result: where to write the PDF + images."""

    pdf_path: Path
    images_dir: Path


class PdfStorage:
    """Filesystem-backed PDF storage.

    Layout: <base_dir>/<website_id>/<pdf_document_id>/pdf.pdf
            <base_dir>/<website_id>/<pdf_document_id>/images/

    Atomicity: write-temp-then-rename with fsync'd parent directory.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def allocate_pdf(self, *, website_id: str, pdf_document_id: str) -> AllocatedSlot:
        parent = self.base_dir / website_id / pdf_document_id
        parent.mkdir(parents=True, exist_ok=True)
        images_dir = parent / "images"
        images_dir.mkdir(exist_ok=True)
        return AllocatedSlot(pdf_path=parent / "pdf.pdf", images_dir=images_dir)

    def write_pdf_bytes(self, slot: AllocatedSlot, data: bytes) -> None:
        tmp_path = slot.pdf_path.with_suffix('.pdf.tmp')
        with open(tmp_path, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, slot.pdf_path)
        # fsync the parent dir so the rename is durable
        dir_fd = os.open(slot.pdf_path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def local_path(self, doc: PdfDocument) -> Path:
        return self.base_dir / doc.storage_relpath

    def images_dir(self, doc: PdfDocument) -> Path:
        return self.base_dir / doc.images_relpath

    def delete(self, doc: PdfDocument) -> None:
        doc_dir = self.local_path(doc).parent
        if doc_dir.is_dir():
            shutil.rmtree(doc_dir, ignore_errors=True)

    def delete_website(self, website_id: str) -> None:
        website_dir = self.base_dir / website_id
        if website_dir.is_dir():
            shutil.rmtree(website_dir, ignore_errors=True)
```

- [ ] **Step 1.12.4: Run test + typecheck**

- [ ] **Step 1.12.5: Commit**

```bash
git add auto_a11y/pdf/storage.py tests/pdf/test_storage.py
git commit --no-gpg-sign -m "Add PdfStorage module for filesystem-backed PDF artefacts

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 1.13: Health endpoint for Ghostscript + storage writability

**Files:**
- Modify: `auto_a11y/web/routes/api.py` (or create a new route if it's large already)
- Create: `tests/pdf/test_health_endpoint.py`

- [ ] **Step 1.13.1: Inspect existing api.py for conventions**

```bash
head -50 auto_a11y/web/routes/api.py
```

Match the blueprint, route-decorator, and permissions patterns used.

- [ ] **Step 1.13.2: Write test**

```python
"""Tests for /api/health/pdf endpoint."""
from __future__ import annotations

# Assuming auto_a11y provides a Flask test-client fixture named `client` somewhere;
# if not, bootstrap one inline following the pattern of the nearest existing route test.
```

(If there are no route tests at all, this task also adds a minimal test-client fixture.)

- [ ] **Step 1.13.3: Implement the endpoint**

Inside `auto_a11y/web/routes/api.py` (or a new sub-blueprint), add:

```python
@api_bp.route('/health/pdf', methods=['GET'])
def pdf_health() -> tuple[dict[str, Any], int]:
    """Report PDF-audit subsystem health: Ghostscript + storage dir."""
    from pathlib import Path
    from auto_a11y.pdf.audit.ghostscript import detect_ghostscript
    from auto_a11y.web.typed_app import get_app_config

    cfg = get_app_config()
    gs_path = detect_ghostscript(override=cfg.GHOSTSCRIPT_PATH)
    storage_dir = Path(cfg.PDF_STORAGE_DIR)
    storage_writable = False
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        probe = storage_dir / '.probe'
        probe.touch()
        probe.unlink()
        storage_writable = True
    except OSError:
        pass

    status = 200 if gs_path and storage_writable else 503
    return (
        {
            "ghostscript": {"found": gs_path is not None, "path": gs_path},
            "storage": {"dir": str(storage_dir), "writable": storage_writable},
        },
        status,
    )
```

- [ ] **Step 1.13.4: Run test + typecheck**

- [ ] **Step 1.13.5: Commit**

```bash
git add auto_a11y/web/routes/api.py tests/pdf/test_health_endpoint.py
git commit --no-gpg-sign -m "Add /api/health/pdf endpoint reporting Ghostscript + storage status

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Phase 2: Shared PDF language helper

## Task 2.1: Create `auto_a11y/pdf/language.py`

**Files:**
- Create: `auto_a11y/pdf/language.py`
- Create: `tests/pdf/test_language.py`

- [ ] **Step 2.1.1: Gather test fixtures**

We need at least three sample PDFs to drive tests: one with `/Lang` set, one without `/Lang` (language inferable from text), one unreadable.

```bash
# Use pdfMax's existing fixtures as starting samples
ls ../pdfMax/tests/*.pdf 2>&1 | head -5
```

Copy two to a fixtures-for-tests location: `tests/pdf/data/`.

- [ ] **Step 2.1.2: Write tests**

```python
"""Tests for the shared PDF language helper."""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_a11y.pdf.language import LanguageDetection, detect_pdf_language


FIXTURES = Path(__file__).parent / "data"


def test_detect_declared_language_from_catalog(tmp_path: Path) -> None:
    # Build a minimal PDF with /Lang set using pikepdf
    import pikepdf
    pdf_path = tmp_path / "en.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root.Lang = pikepdf.String("en-US")
    pdf.save(pdf_path)
    result = detect_pdf_language(pdf_path)
    assert result.declared_lang == "en-US"
    assert result.method == "catalog"


def test_detect_inferred_language(tmp_path: Path) -> None:
    import pikepdf
    pdf_path = tmp_path / "nolang.pdf"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    # (pikepdf doesn't easily write visible text; rely on pdfminer to find nothing.
    # This test covers the "no declared, no inferable" branch.)
    pdf.save(pdf_path)
    result = detect_pdf_language(pdf_path)
    assert result.declared_lang is None
    assert result.detected_lang is None


def test_detect_handles_corrupt_pdf(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    result = detect_pdf_language(bad)
    assert result.declared_lang is None
    assert result.detected_lang is None
    assert result.error is not None
```

- [ ] **Step 2.1.3: Run test (expect failure)**

- [ ] **Step 2.1.4: Implement the helper**

```python
"""Shared PDF language detection: used by both the scraper (cheap hints)
and the audit engine (thorough analysis)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

import pikepdf

logger = logging.getLogger(__name__)


DetectionMethod = Literal['catalog', 'info_dict', 'word_frequency', 'ai', 'none']


@dataclass(frozen=True)
class LanguageDetection:
    """Result of detecting language on a PDF."""

    declared_lang: str | None         # from /Lang in catalog
    detected_lang: str | None         # inferred if declared is missing
    confidence: float | None          # 0.0-1.0 for detected_lang
    method: DetectionMethod
    error: str | None


def detect_pdf_language(source: Path | IO[bytes]) -> LanguageDetection:
    """Detect language from a PDF.

    Tries in order:
    1. Declared: /Lang in the PDF catalog.
    2. Info-dict fallback (rare).
    3. Text-sample word-frequency analysis (only if thorough=True; default True).
    """
    try:
        if isinstance(source, Path):
            pdf = pikepdf.open(source)
        else:
            pdf = pikepdf.open(source)
    except Exception as exc:
        return LanguageDetection(None, None, None, 'none', str(exc))

    try:
        lang = pdf.Root.get(pikepdf.Name("/Lang"))
        if lang is not None:
            return LanguageDetection(
                declared_lang=str(lang),
                detected_lang=None,
                confidence=None,
                method='catalog',
                error=None,
            )
        # Fall through: no declared lang. Word-frequency detection would go here;
        # keep simple for now — auditing fills in more.
        return LanguageDetection(None, None, None, 'none', None)
    finally:
        pdf.close()
```

(Note: the full word-frequency + AI fallback ports to this module during Phase 3 alongside the AI audit port. For now, a minimal implementation that matches what the scraper needs is enough.)

- [ ] **Step 2.1.5: Run tests + typecheck**

- [ ] **Step 2.1.6: Commit**

```bash
git add auto_a11y/pdf/language.py tests/pdf/test_language.py tests/pdf/data/
git commit --no-gpg-sign -m "Add shared PDF language helper

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 2.2: Refactor `scraper._detect_pdf_language` to delegate

**Files:**
- Modify: `auto_a11y/core/scraper.py` (lines ~1092-1137)

- [ ] **Step 2.2.1: Run existing scraper tests for baseline**

```bash
python -m pytest tests/ -k scraper -v
```

- [ ] **Step 2.2.2: Replace `_detect_pdf_language` body**

The existing method (around line 1092) uses `PyPDF2`. Replace the body with a delegation:

```python
    async def _detect_pdf_language(self, content: bytes) -> dict[str, Any] | None:
        """Detect the language of a PDF document from bytes."""
        from io import BytesIO

        from auto_a11y.pdf.language import detect_pdf_language

        try:
            result = detect_pdf_language(BytesIO(content))
        except Exception as exc:
            logger.warning(f"PDF language detection failed: {exc}")
            return None
        if result.declared_lang is None and result.detected_lang is None:
            return None
        return {
            'language': result.declared_lang or result.detected_lang,
            'confidence': result.confidence,
            'method': result.method,
        }
```

Remove the `import PyPDF2` line at line 1103.

- [ ] **Step 2.2.3: Run scraper tests + typecheck**

- [ ] **Step 2.2.4: Commit**

```bash
git add auto_a11y/core/scraper.py
git commit --no-gpg-sign -m "Refactor scraper._detect_pdf_language to use shared helper

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

## Task 2.3: Remove `pypdf2` from dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 2.3.1: Confirm no remaining callers**

```bash
grep -rn "PyPDF2\|pypdf2\|import pypdf" auto_a11y/ tests/ test_fixtures.py 2>&1
```

Expected: no hits (should be removed after Task 2.2).

- [ ] **Step 2.3.2: Remove from requirements.txt**

Delete the `pypdf2==3.0.1` line.

- [ ] **Step 2.3.3: Uninstall (optional, for cleanliness)**

```bash
pip uninstall -y pypdf2
```

- [ ] **Step 2.3.4: Run full tests + typecheck**

```bash
python -m pytest tests/ -x --ignore-glob='**/recordings_integration_test*' -q 2>&1 | tail -10
python -m mypy 2>&1 | tail -3
python -m pyright 2>&1 | tail -3
python -m ty check 2>&1 | tail -3
```

- [ ] **Step 2.3.5: Commit**

```bash
git add requirements.txt
git commit --no-gpg-sign -m "Remove unused pypdf2 dependency

Shared auto_a11y/pdf/language.py (pikepdf-backed) now handles all
language-detection callers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Phase 3: Engine port — Data collectors (pure-function modules)

Each module in this phase is a near-direct transplant of pdfMax code, refactored into a focused file and brought up to strict typecheck.

## Task 3.1: `pikepdf_helpers.py` — the narrowing wrapper layer

**Rationale:** pdfMax's code accesses pikepdf objects via dynamic attribute / subscript (`pdf.Root["/Lang"]`, `obj.StructTreeRoot`, etc.). Strict typecheckers can't narrow these returns without helpers that wrap common accesses with explicit return types.

**Files:**
- Create: `auto_a11y/pdf/audit/pikepdf_helpers.py`
- Create: `tests/pdf/test_pikepdf_helpers.py`

- [ ] **Step 3.1.1: Write tests first** — cover `get_name`, `get_string`, `get_array`, `get_dict`, `get_int`, `resolve_object`, `as_string`.

- [ ] **Step 3.1.2: Run tests (expect failure)**

- [ ] **Step 3.1.3: Implement helpers**

(See spec section 8 for exact signatures. Expected ~80-120 lines of narrowing helpers with thorough docstrings and no `Any`.)

- [ ] **Step 3.1.4: Run tests + typecheck**

- [ ] **Step 3.1.5: Commit**

## Task 3.2: `audit/structure.py` — tag-tree walking

**Files:**
- Create: `auto_a11y/pdf/audit/structure.py`
- Create: `tests/pdf/test_structure.py`
- Reference: pdfMax's `walk_structure_tree`, `populate_element_text`, `build_tag_tree_text` — search `pdf_accessibility_audit.py` for these function names.

- [ ] **Step 3.2.1: Write tests** — use synthetic tagged PDFs from pikepdf to validate the tag tree structure.

- [ ] **Step 3.2.2: Copy and refactor the pdfMax functions**, typing every parameter and return, using `pikepdf_helpers`.

- [ ] **Step 3.2.3: Tests + typecheck + commit**

## Tasks 3.3 – 3.8: Remaining collectors

Apply the same TDD-and-port pattern to each collector in turn, one commit each:

| Task | Module | Source in pdfMax |
|------|--------|-------------------|
| 3.3 | `audit/content_streams.py` | `extract_mcid_text_map_from_content_streams` |
| 3.4 | `audit/colors.py` | `extract_text_colors`, `extract_form_field_colors` |
| 3.5 | `audit/images.py` | `extract_images` |
| 3.6 | `audit/fonts.py` | `extract_font_analysis` |
| 3.7 | `audit/reading_order.py` | `build_visual_reading_order_report` |

Each task follows Task 3.2's template: write tests → port → type strictly → pass checkers → commit.

---

# Phase 4: Engine port — check modules

Each check module owns the FAIL/WARN/INFO detection logic for one conceptual group.

## Task 4.X (worked example): `audit/checks/document_properties.py`

**Files:**
- Create: `auto_a11y/pdf/audit/checks/__init__.py` (check registry exports)
- Create: `auto_a11y/pdf/audit/checks/document_properties.py`
- Create: `tests/pdf/test_checks_document_properties.py`

- [ ] **Step 4.X.1: Write tests** — use `pikepdf` in tests to build tiny PDFs with/without `/Title`, `/Lang`, etc., and assert that each check function produces the expected `CheckResult`.

- [ ] **Step 4.X.2: Implement the check functions**

Each check is a function taking `AuditContext` and returning `list[CheckResult]`:

```python
def check_document_title(ctx: AuditContext) -> list[CheckResult]:
    title = pikepdf_helpers.get_string(ctx.pdf.Root, "/Title")
    if title is None:
        return [CheckResult(name="Document title set", result="FAIL",
                            standard="WCAG 2.4.2", details="No /Title in catalog.")]
    if not title.strip():
        return [CheckResult(name="Document title set", result="WARN",
                            standard="WCAG 2.4.2", details="Title is empty string.")]
    return [CheckResult(name="Document title set", result="PASS", ...)]
```

- [ ] **Step 4.X.3: Register in `checks/__init__.py`**:

```python
from . import document_properties

DOCUMENT_PROPERTIES_CHECKS = [
    document_properties.check_document_title,
    document_properties.check_document_language,
    document_properties.check_pdfua_identifier,
    # ... one entry per check function
]
```

- [ ] **Step 4.X.4: Tests + typecheck + commit**

## Tasks 4.1 – 4.13: All check modules

Repeat Task 4.X for each check module:

| Task | Module | Approx. number of checks |
|------|--------|---------------------------|
| 4.1 | `document_properties.py` | ~14 |
| 4.2 | `tagging_structure.py` | ~20 |
| 4.3 | `annotations.py` | ~9 |
| 4.4 | `lists.py` | ~5 |
| 4.5 | `tables.py` | ~7 |
| 4.6 | `headings.py` | ~2 |
| 4.7 | `images_alt_text.py` | ~5 |
| 4.8 | `links_navigation.py` | ~5 |
| 4.9 | `forms.py` | ~6 |
| 4.10 | `interactive.py` | ~3 |
| 4.11 | `color_contrast.py` | ~2-4 |
| 4.12 | `fonts.py` | ~7 |
| 4.13 | `language.py` | ~5 |

After Task 4.13 the total check count should land at approximately 100-120 unique code paths before AI checks are layered on (AI adds another ~5-10).

---

# Phase 5: AI analysis + pipeline orchestrator

## Task 5.1: `audit/ai/semantic.py`

**Files:**
- Create: `auto_a11y/pdf/audit/ai/__init__.py`
- Create: `auto_a11y/pdf/audit/ai/semantic.py`
- Create: `auto_a11y/pdf/audit/ai/executive_summary.py`
- Create: `auto_a11y/pdf/audit/ai/finding_mapper.py`
- Create: `tests/pdf/test_ai_semantic.py`
- Create: `tests/pdf/test_ai_finding_mapper.py`
- Reference: pdfMax's `run_claude_semantic_analysis` and `EXECUTIVE_SUMMARY_SCHEMA`.

- [ ] **Step 5.1.1: Invoke superpowers `claude-api` skill** for up-to-date Anthropic SDK patterns, prompt caching, extended thinking.

- [ ] **Step 5.1.2: Write tests** — mock the Anthropic client; assert that the AI module produces correctly-shaped `AIFinding` objects.

- [ ] **Step 5.1.3: Port the functions**:
- Replace pdfMax's hardcoded model with `cfg.CLAUDE_MODEL`.
- Add **prompt caching** for the shared large context (PDF structure dump).
- Route output through `finding_mapper` → `list[AIFinding]`.
- Accept `locale` parameter; prompt Claude to respond in that locale.

- [ ] **Step 5.1.4: Tests + typecheck + commit**

## Task 5.2: Internal dataclasses in `models.py`

**Files:**
- Create: `auto_a11y/pdf/models.py`
- Create: `tests/pdf/test_internal_models.py`

Dataclasses: `CheckResult`, `TagElement`, `AuditContext`, `AuditResult`, `AIAnalysisResult`, `ProgressCallback` type alias.

## Task 5.3: `audit/pipeline.py` — the orchestrator

**Files:**
- Create: `auto_a11y/pdf/audit/pipeline.py`
- Create: `tests/pdf/test_pipeline.py`

- [ ] **Step 5.3.1: Write an end-to-end test** with a tiny synthetic PDF that triggers at least one FAIL, assert `run_audit` produces the expected `AuditResult`.

- [ ] **Step 5.3.2: Implement `run_audit`**:

```python
def run_audit(
    pdf_path: Path,
    *,
    wcag_level: Literal["AA", "AAA"] = "AA",
    run_ai: bool = False,
    ai_api_key: str | None = None,
    locale: str = "en",
    images_out_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    gs_path_override: str | None = None,
) -> AuditResult:
    """Audit a PDF. Single sync entry point."""
    ...
```

Orchestrates: open PDF (`pikepdf.open` with error → `CorruptPdf`), walk structure, run all check modules from the registry, optionally run AI analysis, return `AuditResult`.

Wraps every individual check in `try/except`; a crashing check synthesises `CheckResult(name="Internal check failure: ...", result="FAIL", ...)` and audit continues.

- [ ] **Step 5.3.3: Expose via `auto_a11y/pdf/audit/__init__.py`**:

```python
from .pipeline import run_audit

__all__ = ['run_audit']
```

- [ ] **Step 5.3.4: Tests + typecheck + commit**

---

# Phase 6: Check catalogue enumeration

## Task 6.1: Build `CHECK_CATALOGUE`

**Rationale:** Single source of truth for every check code's stable ID, touchpoint, impact, WCAG criteria, and remediation Fluent key. The spec defers full enumeration to this implementation task.

**Files:**
- Create: `auto_a11y/pdf/translation/__init__.py` (empty)
- Create: `auto_a11y/pdf/translation/check_mapper.py`
- Create: `tests/pdf/test_check_mapper.py`

- [ ] **Step 6.1.1: Enumerate every check name emitted by the ported engine**

Scrape the check names from the registry in `auto_a11y/pdf/audit/checks/__init__.py`:

```bash
grep -rE "CheckResult\(name=" auto_a11y/pdf/audit/checks/ | \
  sed -E 's/.*name="([^"]+)".*/\1/' | sort -u > /tmp/check-names.txt
wc -l /tmp/check-names.txt
```

Expected: ~100-120 unique names.

- [ ] **Step 6.1.2: Build catalogue rows**

Create `auto_a11y/pdf/translation/check_mapper.py`:

```python
"""Maps pdfMax CheckResult output onto auto_a11y Violation objects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

from auto_a11y.core.touchpoints import Touchpoint
from auto_a11y.models.test_result import ImpactLevel


class CatalogueRow(TypedDict):
    pdfmax_check_name: str
    pdfmax_result: Literal["FAIL", "WARN"]
    stable_id: str
    touchpoint: Touchpoint
    impact_override: ImpactLevel | None
    wcag_criteria: list[str]
    generator_version: int


# Populated row-by-row. Each row = one (check, result) pair.
CHECK_CATALOGUE: list[CatalogueRow] = [
    {
        'pdfmax_check_name': 'Document title set',
        'pdfmax_result': 'FAIL',
        'stable_id': 'PdfErrDocumentTitleNotSet',
        'touchpoint': Touchpoint.PDF_DOCUMENT_PROPERTIES,
        'impact_override': None,
        'wcag_criteria': ['2.4.2'],
        'generator_version': 1,
    },
    {
        'pdfmax_check_name': 'Document title set',
        'pdfmax_result': 'WARN',
        'stable_id': 'PdfWarnDocumentTitleSuspect',
        'touchpoint': Touchpoint.PDF_DOCUMENT_PROPERTIES,
        'impact_override': ImpactLevel.MEDIUM,
        'wcag_criteria': ['2.4.2'],
        'generator_version': 1,
    },
    # ... ~200 more rows
]
```

Implementation process (mechanical):
1. For each name in `/tmp/check-names.txt`, determine whether it emits FAIL only, WARN only, or both.
2. For each (name, result) pair, derive a stable ID: `Pdf{Err|Warn|Info|Disco}{PascalCaseSummary}`.
3. Assign touchpoint from the `CHECK_GROUPS` structure in pdfMax's audit file (look at the existing groupings; map `"Document Properties"` → `Touchpoint.PDF_DOCUMENT_PROPERTIES`, `"Tagging & Structure"` → `Touchpoint.PDF_TAGGING`, etc.).
4. Assign WCAG criteria from pdfMax's `standard` string on each `CheckResult` + WCAG-2.2 cross-reference.
5. Leave `impact_override = None` except where the default rule (see spec section 3) is clearly wrong.

- [ ] **Step 6.1.3: Add `CheckResult → Violation` converter**

```python
def to_violation(check_result: 'CheckResult', *, pdf_doc_id: str, locale: str) -> Violation:
    row = _lookup_row(check_result.name, check_result.result)
    stable_id = row['stable_id']
    impact = row['impact_override'] or _default_impact(row['pdfmax_result'], row['wcag_criteria'])
    return Violation(
        id=stable_id,
        impact=impact,
        touchpoint=row['touchpoint'].value,
        description=f"pdf-check-{stable_id}-name",  # RESOLVED AT RENDER TIME
        short_title=f"pdf-check-{stable_id}-short-title",
        what=f"pdf-check-{stable_id}-what",
        why=f"pdf-check-{stable_id}-why",
        who=f"pdf-check-{stable_id}-who",
        remediation=f"pdf-remediation-{stable_id}",
        wcag_criteria=row['wcag_criteria'],
        metadata={
            'pdf_page': check_result.page,
            'pdf_bbox': check_result.bbox,
            'pdf_mcid': check_result.mcid,
            'pdf_element_ref': check_result.element_ref,
            'pdfmax_original_details': check_result.details,
        },
        source_type='automated',
        detection_method='pdf_audit',
    )
```

**Note:** The string fields hold **Fluent message IDs**, not resolved text. Templates call `ftl(v.description)` at render time. This matches the spec's "persist locale-free" convention.

- [ ] **Step 6.1.4: Write completeness test**

```python
def test_every_check_emitted_by_engine_has_catalogue_entry() -> None:
    """Regression test: if a check function emits a CheckResult name not in
    the catalogue, this test fails — forcing us to update the catalogue."""
    from auto_a11y.pdf.audit.checks import ALL_CHECKS_REGISTRY
    # Walk every check function, feed it a permissive AuditContext, and verify
    # every CheckResult name+result is in CHECK_CATALOGUE.
    ...
```

- [ ] **Step 6.1.5: Tests + typecheck + commit**

---

# Phase 7: Translation layer — Fluent files + remediation guide porting

## Task 7.1: Port `remediation_guide.py` via one-off script

**Files:**
- Create: `scripts/port_pdfmax_remediation_guide.py`
- Create: `auto_a11y/web/translations/en/pdf-remediation.ftl` (generated)
- Create: `auto_a11y/pdf/translation/remediation_guide.py` (generated)

- [ ] **Step 7.1.1: Write the porting script**

The script reads pdfMax's `remediation_guide.py` (a dict mapping check names → multi-paragraph markdown strings) and emits:
1. `auto_a11y/web/translations/en/pdf-remediation.ftl` with one entry per stable-ID, text from pdfMax's dict.
2. `auto_a11y/pdf/translation/remediation_guide.py` with the stable-ID → Fluent-key mapping table.

Usage: `python -m scripts.port_pdfmax_remediation_guide ../pdfMax/python/checker/remediation_guide.py`.

- [ ] **Step 7.1.2: Run the script**

- [ ] **Step 7.1.3: Create the French counterpart `auto_a11y/web/translations/fr/pdf-remediation.ftl`**

This requires human translation. Populate with the same IDs as the English file, each mapped to the translated French text. Do **not** commit placeholder English strings — the spec is explicit that machine-translation placeholders are not acceptable and the coverage test (Task 7.4) must fail if any French string is missing. If a human translator is not immediately available, block this phase — do not proceed to Task 7.4's coverage test until the French file is genuinely translated. Untranslated French is a release blocker, not a "fill in later" item.

- [ ] **Step 7.1.4: Commit the script, generated files, and translations together**

## Task 7.2: Create other Fluent files

**Files:**
- Create: `auto_a11y/web/translations/en/pdf.ftl` and `fr/pdf.ftl` (UI strings)
- Create: `en/pdf-touchpoints.ftl` + `fr/pdf-touchpoints.ftl`
- Create: `en/pdf-progress.ftl` + `fr/`
- Create: `en/pdf-errors.ftl` + `fr/`
- Create: `en/pdf-status.ftl` + `fr/`

- [ ] **Step 7.2.1: Populate each file** with strings from spec section 7 as a starting point. Expand as subsequent tasks discover more strings.

- [ ] **Step 7.2.2: Commit**

## Task 7.3: Build `pdf-checks.ftl`

**Files:**
- Create: `auto_a11y/web/translations/en/pdf-checks.ftl`
- Create: `auto_a11y/web/translations/fr/pdf-checks.ftl`

- [ ] **Step 7.3.1: For each row in `CHECK_CATALOGUE`, emit six message IDs** (`-name`, `-short-title`, `-what`, `-why`, `-who`; remediation is already in `pdf-remediation.ftl`):

```
pdf-check-PdfErrDocumentTitleNotSet-name = Document title not set
pdf-check-PdfErrDocumentTitleNotSet-short-title = Missing document title
pdf-check-PdfErrDocumentTitleNotSet-what = The PDF's document catalog has no /Title entry...
pdf-check-PdfErrDocumentTitleNotSet-why = Screen readers announce the document title...
pdf-check-PdfErrDocumentTitleNotSet-who = Blind and low-vision users using screen readers...
```

Source text:
- `-name` = pdfMax's check name (e.g., "Document title not set").
- `-short-title` = a shortened version (max ~40 chars) — author manually.
- `-what` / `-why` / `-who` = extracted from pdfMax's `remediation_guide.py` entries where possible; otherwise author from WCAG criterion + auto_a11y's existing HTML-check conventions.

- [ ] **Step 7.3.2: French parity** — same keys in `fr/pdf-checks.ftl`, translated.

## Task 7.4: Translation-coverage test

**Files:**
- Create: `tests/pdf/test_translations_coverage.py`

- [ ] **Step 7.4.1: Write the test**

```python
"""Coverage test: every catalogue row has all expected Fluent IDs in both locales."""
from __future__ import annotations

from pathlib import Path

import pytest

from auto_a11y.pdf.translation.check_mapper import CHECK_CATALOGUE

TRANSLATIONS_DIR = Path(__file__).parents[2] / 'auto_a11y' / 'web' / 'translations'

_REQUIRED_SUFFIXES = ('name', 'short-title', 'what', 'why', 'who')


def _read_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if '=' in line:
            ids.add(line.split('=', 1)[0].strip())
    return ids


@pytest.mark.parametrize("locale", ["en", "fr"])
@pytest.mark.parametrize("suffix", _REQUIRED_SUFFIXES)
def test_every_catalogue_row_has_pdf_checks_id(locale: str, suffix: str) -> None:
    ftl = TRANSLATIONS_DIR / locale / 'pdf-checks.ftl'
    ids = _read_ids(ftl)
    missing = [
        f"pdf-check-{row['stable_id']}-{suffix}"
        for row in CHECK_CATALOGUE
        if f"pdf-check-{row['stable_id']}-{suffix}" not in ids
    ]
    assert not missing, f"Missing {len(missing)} IDs in {locale}/pdf-checks.ftl: {missing[:10]}"


@pytest.mark.parametrize("locale", ["en", "fr"])
def test_every_catalogue_row_has_remediation_id(locale: str) -> None:
    ftl = TRANSLATIONS_DIR / locale / 'pdf-remediation.ftl'
    ids = _read_ids(ftl)
    missing = [
        f"pdf-remediation-{row['stable_id']}"
        for row in CHECK_CATALOGUE
        if f"pdf-remediation-{row['stable_id']}" not in ids
    ]
    assert not missing, f"Missing {len(missing)} IDs in {locale}/pdf-remediation.ftl"


def test_pdf_checks_ftl_has_no_orphans() -> None:
    """Any pdf-check-* ID in the .ftl must correspond to a catalogue row."""
    expected = {
        f"pdf-check-{row['stable_id']}-{s}"
        for row in CHECK_CATALOGUE
        for s in _REQUIRED_SUFFIXES
    }
    en_ids = {i for i in _read_ids(TRANSLATIONS_DIR / 'en' / 'pdf-checks.ftl') if i.startswith('pdf-check-')}
    orphans = en_ids - expected
    assert not orphans, f"Orphan IDs (not in CHECK_CATALOGUE): {sorted(orphans)[:10]}"
```

- [ ] **Step 7.4.2: Run the test — expected to catch gaps**

Any missing rows in `pdf-checks.ftl` or `pdf-remediation.ftl` are reported. Add them until the test passes.

- [ ] **Step 7.4.3: Commit**

---

# Phase 8: Async wrapper + testing-pipeline integration

## Task 8.1: `PdfRunner` — async wrapper + fetch pipeline

**Files:**
- Create: `auto_a11y/testing/pdf_runner.py`
- Create: `tests/pdf/test_pdf_runner.py`

- [ ] **Step 8.1.1: Write tests** — mock `run_audit`; assert `PdfRunner.audit_pdf_document` transitions status correctly and maps results via `check_mapper`.

- [ ] **Step 8.1.2: Implement**

See spec section 5 for the full method body. Dedicated `ThreadPoolExecutor(max_workers=cfg.PDF_AUDIT_MAX_PARALLEL)`. Status transitions: `AUDITING` before `run_in_executor`, `AUDITED` or `AUDIT_FAILED` after.

- [ ] **Step 8.1.3: Tests + typecheck + commit**

## Task 8.2: `TestRunner.test_pdf` + opportunistic branch in `test_page`

**Files:**
- Modify: `auto_a11y/testing/test_runner.py`
- Create: `tests/pdf/test_test_runner_pdf_branch.py`

- [ ] **Step 8.2.1: Write tests** — mock Playwright responses; assert that:
  - A `application/pdf` response triggers the opportunistic branch before `wait_for_selector('body')`.
  - The `Page` is marked `IS_PDF` with `linked_pdf_document_id` set.
  - The `TestResult` attaches to the `PdfDocument`, not the `Page`.
  - A failing download sets `Page.status=ERROR` and creates no `PdfDocument`.

- [ ] **Step 8.2.2: Implement** — add the early branch in `test_page` (around line 150, after `response = await browser_manager.goto(...)`):

```python
                content_type = response.headers.get("content-type", "").lower() if response else ""
                if response and (
                    content_type.startswith("application/pdf")
                    or (content_type.startswith("application/octet-stream") and page.url.lower().endswith(".pdf"))
                ):
                    try:
                        pdf_bytes = await response.body()
                    except Exception:
                        # Fall back to aiohttp fetch seeded with Playwright cookies
                        pdf_bytes = await _aiohttp_fetch_with_cookies(page.url, browser_page)
                    if not pdf_bytes.startswith(b"%PDF-"):
                        raise NotAPdf(f"Response did not contain %PDF- magic bytes")
                    # Dedup, store, create PdfDocument, audit
                    pdf_doc = await self._pdf_runner.create_or_find_pdf_document(
                        pdf_bytes,
                        website_id=page.website_id,
                        source_type='opportunistic',
                        discovered_from_page_id=page.id,
                        original_filename=page.url.rsplit('/', 1)[-1] or 'document.pdf',
                        source_url=page.url,
                    )
                    page.status = PageStatus.IS_PDF
                    page.linked_pdf_document_id = pdf_doc.id
                    self.db.update_page(page)
                    return await self._pdf_runner.audit_pdf_document(
                        pdf_doc.id,
                        run_ai=run_ai_analysis,
                        ai_api_key=ai_api_key,
                        wcag_level='AA',
                    )
```

Add `async def test_pdf(self, pdf_document_id: str, ...) -> TestResult:` as a thin delegate to `PdfRunner.audit_pdf_document`.

- [ ] **Step 8.2.3: Tests + typecheck + commit**

---

# Phase 9: Web UI + routes

## Task 9.1: Shared `TestTargetView` view model

**Files:**
- Create: `auto_a11y/web/view_models/__init__.py`
- Create: `auto_a11y/web/view_models/target.py`
- Create: `tests/pdf/test_target_view_model.py`

Follow Section 6 of the spec verbatim.

## Task 9.2: Shared partials

**Files:**
- Create: `auto_a11y/web/templates/_target_header.html`
- Create: `auto_a11y/web/templates/_target_result_summary.html`

## Task 9.3: `pdf.py` route blueprint

**Files:**
- Create: `auto_a11y/web/routes/pdf.py`
- Create: `tests/pdf/test_pdf_routes.py`
- Modify: `auto_a11y/web/routes/__init__.py` (import + export)

Implement all routes per spec section 6. Permissions via `project_role_required` (or the existing equivalent).

## Task 9.4: PDF templates

**Files:**
- Create: `auto_a11y/web/templates/pdf/list.html`
- Create: `auto_a11y/web/templates/pdf/add.html`
- Create: `auto_a11y/web/templates/pdf/detail.html`
- Create: `auto_a11y/web/templates/pdf/_audit_progress.html`
- Create: `auto_a11y/web/templates/pdf/_empty_state.html`

Every string via Fluent. Every colour via custom tokens. Native elements (`<button>`, `<a>`, etc.).

## Task 9.5: Static JS for iframe `#page=N` handling

**Files:**
- Create: `auto_a11y/web/static/js/pdf_viewer.js`

Handles click on a `[data-pdf-page]` button → updates the iframe's `src` fragment.

## Task 9.6: Refactor `test_result.html` to consume `TestTargetView`

## Task 9.7: Navigation integration

- Link PDF list from project detail.
- Link PDF list from website detail.
- Render `→ PDF` badge on pages with `status=IS_PDF`.

---

# Phase 10: Fixture generation + test_fixtures.py

## Task 10.1: Copy and adapt pdfMax's `generate_test_pdfs.py`

**Files:**
- Create: `fixture_generation/pdf/generate_pdf_fixtures.py`
- Create: `fixture_generation/pdf/builders.py`
- Create: `fixture_generation/pdf/metadata.py` (sidecar YAML writer)
- Modify: `pyproject.toml` — add `fixture_generation/pdf` to `[tool.mypy]` `files` and `[tool.pyright]` `include`.

## Tasks 10.2 – 10.14: One generator module per touchpoint

Repeat per module (13 touchpoints × ~8 checks/module = ~100 generator functions). Follow the template in spec section 4.

After each module is complete, run:

```bash
python -m fixture_generation.pdf.generate_pdf_fixtures --touchpoint DocumentProperties
python test_fixtures.py --target pdf --touchpoint DocumentProperties
```

## Task 10.15: Extend `test_fixtures.py`

**Files:**
- Modify: `test_fixtures.py`

Add PDF fixture discovery, sidecar parsing, `run_pdf_fixture`, CLI flags.

## Task 10.16: `/testing/fixture-status` separate PDF tab

**Files:**
- Modify: `auto_a11y/web/routes/testing.py` (or equivalent)
- Modify: the fixture-status template
- Modify: `en/testing.ftl` and `fr/testing.ftl`

---

# Phase 11: Deployment + documentation

## Task 11.1: Docker + render.yaml

**Files:**
- Modify: `Dockerfile` — add `ghostscript`, create `/app/data/pdfs` dir
- Modify: `docker-compose.yml` — add `data/pdfs` volume
- Modify: `render.yaml` — add `apt-get install ghostscript` to build command

## Task 11.2: CLAUDE.md updates

**Files:**
- Modify: `CLAUDE.md`

Document:
- Partial typecheck carve-out: `fixture_generation/pdf/**` and `scripts/port_pdfmax_remediation_guide.py` are in strict scope; the rest of `fixture_generation/` and `scripts/` remain excluded.
- Ghostscript as a runtime system dependency.
- PDF fixture layout under `Fixtures/PDF/` and regeneration command.

## Task 11.3: README updates

**Files:**
- Modify: `README.md`
- Modify: `README.fr.md`

Add PDF-audit feature overview + Ghostscript install instructions. Both files stay in sync.

## Task 11.4: Final full-system smoke test

- [ ] Upload a sample PDF via `/projects/<id>/pdfs/add` → verify audit runs and results render.
- [ ] Paste a PDF URL → verify fetch + audit.
- [ ] Run `test_page` against a URL that redirects to PDF → verify opportunistic branch fires, PDF is audited, Page is marked `IS_PDF`.
- [ ] Run `python test_fixtures.py --target pdf` → expect 100% pass.
- [ ] Run `python test_fixtures.py --target html` → expect no regressions.
- [ ] Run `python -m mypy && python -m pyright && python -m ty check` → all green.
- [ ] Visit `/api/health/pdf` → expect `{"ghostscript": {"found": true, ...}, "storage": {"writable": true, ...}}`.

## Task 11.5: Create PR

```bash
git push -u origin pdfmax-integration
gh pr create --title "PDF audit engine port (pdfMax sub-project #1)" --body "$(cat <<'EOF'
## Summary
- Ports pdfMax's Python PDF accessibility audit engine into auto_a11y.
- New `PdfDocument` model, polymorphic `TestResult`, full Fluent EN/FR coverage.
- Opportunistic PDF audit during `test_page` when URL returns `application/pdf`.
- ~200 fixture PDFs gate check enablement identical to HTML rule.

Spec: [2026-04-24-pdf-audit-engine-port-design.md](docs/superpowers/specs/2026-04-24-pdf-audit-engine-port-design.md)
Plan: [2026-04-24-pdf-audit-engine-port.md](docs/superpowers/plans/2026-04-24-pdf-audit-engine-port.md)

## Test plan
- [ ] `python test_fixtures.py --target pdf` → 100% pass
- [ ] `python test_fixtures.py --target html` → no regressions
- [ ] `python -m mypy && python -m pyright && python -m ty check` → all green
- [ ] Manual: upload PDF via /projects/<id>/pdfs/add, confirm results render
- [ ] Manual: paste PDF URL, confirm fetch + audit
- [ ] Manual: test_page against redirect-to-PDF URL confirms opportunistic path

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Post-merge follow-ups (captured as tickets, not part of this plan)

- **Sub-project #2**: Scraper PDF discovery (`DocumentReference` → `PdfDocument` link, automated queueing).
- **Sub-project #3**: Web-based PDF viewer (canvas/text/annotation).
- **Sub-project #4**: `pdf_fix.py` remediation port.
- **Sub-project #5**: Retire pdfMax Electron shell.
- **AI fixture system**: Deterministic testing for AI-generated findings.
- **Translation-on-demand** for AI finding text.
- **GridFS/S3 storage adapter** for stateless deployments (e.g. Render).
