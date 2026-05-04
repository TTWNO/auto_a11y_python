# PDF Viewer issue-card detail parity + project/site PDF rollup — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the auto_a11y `/pdfs/<id>` Viewer right-hand panel up to feature parity with pdfMax's Viewer tab (element tag/index, document-level marker, group accordion, "View in report" button), and teach the website-detail and project-detail pages to roll up audited PDFs into the global `total_violations` / `total_warnings` totals — all without persisting any new counts.

**Architecture:**
- **Frontend (Part 1):** Pure JS + template + Fluent additions. The `/pdfs/<id>/issue-map` route already streams every field — the regression is in `pdf_viewer_app.js` which both *drops* fields when loading (`element_index`, `element_tag`, document-level entries with no `page`/`bbox`) and *renders* fewer fields than pdfMax. Fix the loader to retain everything, port `groupIssues()` 1:1 from pdfMax, rewrite the card builder, and add a hash-handler to `pdfmax_report.html` so the new "View in report" button can scroll-to-anchor across pages.
- **Backend (Part 2):** New helper module `auto_a11y/pdf/issue_map_counts.py` that reads each audited PDF's cached `*_issue_map.json` and tallies FAIL/WARN. Aggregation hooks added inside `websites.py:69-100` and `projects.py:523-551` add the rolled-up counts to the existing `stats` dicts before they're rendered. No DB migration. No new fields on `PdfDocument`.

**Tech Stack:**
- Python 3.12 (Flask, MongoDB, dataclasses); enforced strict typing (mypy + pyright + ty), zero escape hatches.
- Vanilla JS (no TS, no bundler) loaded as `<script type="module">`. Translatable strings come from a flat `window.pdfViewerI18n` object populated by Jinja2 from Fluent.
- Fluent (`fluent-compiler`) for i18n; EN authoritative, FR placeholders under `### TODO_FR ###` per release-gate convention.

**Branch:** `pdfmax-integration` (no new branch — this lands as additions to the in-flight integration).

**Pre-commit gotcha:** the hook runs mypy/pyright/ty and needs the venv active to resolve third-party deps. Always run `source .venv/bin/activate` before `git commit`. Never `--no-verify`. GPG signing is disabled for this branch — pass `-c commit.gpgsign=false` to `git`.

---

## File Structure

### Files created

| Path | Purpose |
|---|---|
| `auto_a11y/pdf/issue_map_counts.py` | `PdfIssueCounts` dataclass + `count_issues(pdf, storage)` reader |
| `tests/pdf/test_issue_map_counts.py` | Unit tests for the helper |
| `tests/pdf/test_pdf_viewer_issue_cards.py` | Tests for issue-map JSON pass-through + i18n keys present in `/pdfs/<id>` |
| `tests/pdf/test_pdfmax_report_anchor.py` | Test that `pdfmax_report.html` embeds the new hash-handler script |
| `tests/pdf/test_pdf_rollup_routes.py` | Tests for website-/project-detail rollup including audited PDFs |

### Files modified

| Path | Change |
|---|---|
| `auto_a11y/web/translations/en/pdf.ftl` | Add 6 new IDs; remove `pdf-viewer-jump-to-page-template` |
| `auto_a11y/web/translations/fr/pdf.ftl` | Same |
| `auto_a11y/web/templates/pdf/detail.html` | Update `window.pdfViewerI18n` block; add `data-pdfmax-report-url` attribute |
| `auto_a11y/web/templates/pdf/pdfmax_report.html` | Add inline hash-handler script |
| `auto_a11y/web/static/js/pdf_viewer_app.js` | Fix loader to preserve all fields + load doc-level issues; add `_groupIssues`; rewrite `_buildIssueCard`; add `_buildIssueGroupCard`; remove per-card "Page N" jump button |
| `auto_a11y/web/routes/websites.py` | Add PDF rollup pass after the Mongo aggregation |
| `auto_a11y/web/routes/projects.py` | Add PDF rollup pass inside the per-website loop |

---

## Task ordering rationale

We do Part 1 (frontend) and Part 2 (backend) as serial phases. They don't share code, but interleaving them would obscure the progression. Inside each phase, tasks are TDD-ordered: failing test → minimal code → passing test → commit.

Within Part 1, the order is:
1. **Fluent strings first.** Templates and JS will reference the new IDs; if they're not in the `.ftl` files, even smoke loads of the page raise. The translation-coverage test guards against drift.
2. **Template wiring next.** Add the i18n keys and the `data-pdfmax-report-url` attribute. The JS reads both; doing this before the JS rewrite means the JS can be tested with real strings.
3. **JS loader first, then renderer.** The loader currently drops document-level issues entirely. Fix that before rewriting the card builder so the card-builder tests have data to consume.
4. **Pdfmax_report hash-handler last.** It's independent of the viewer JS and could be done first or last — placed last because the "View in report" button it serves is added in step 3.

---

## Part 1: Viewer issue-card detail parity

### Task 1: Add new Fluent IDs to EN `pdf.ftl`; remove dead one

**Files:**
- Modify: `auto_a11y/web/translations/en/pdf.ftl:431-447`

- [ ] **Step 1: Write the failing test**

Add to `tests/pdf/test_pdf_viewer_issue_cards.py` (this file does not exist yet — create it with this content):

```python
"""Issue-card detail parity tests (Part 1 of the 2026-05-01 spec)."""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
EN_PDF_FTL = REPO_ROOT / "auto_a11y" / "web" / "translations" / "en" / "pdf.ftl"
FR_PDF_FTL = REPO_ROOT / "auto_a11y" / "web" / "translations" / "fr" / "pdf.ftl"


REQUIRED_NEW_IDS = (
    "pdf-viewer-issue-element",
    "pdf-viewer-issue-document-level",
    "pdf-viewer-issue-group-count",
    "pdf-viewer-issue-view-in-report",
    "pdf-viewer-issue-view-in-report-aria",
    "pdfmax-report-jumped-to-check",
)
REMOVED_IDS = ("pdf-viewer-jump-to-page-template",)


@pytest.mark.parametrize("ftl_path", [EN_PDF_FTL, FR_PDF_FTL])
def test_required_ftl_ids_present(ftl_path: Path) -> None:
    body = ftl_path.read_text(encoding="utf-8")
    for ftl_id in REQUIRED_NEW_IDS:
        assert f"\n{ftl_id}" in f"\n{body}", (
            f"missing Fluent ID {ftl_id!r} in {ftl_path}"
        )


@pytest.mark.parametrize("ftl_path", [EN_PDF_FTL, FR_PDF_FTL])
def test_removed_ftl_ids_absent(ftl_path: Path) -> None:
    body = ftl_path.read_text(encoding="utf-8")
    for ftl_id in REMOVED_IDS:
        assert f"\n{ftl_id} " not in f"\n{body}" and f"\n{ftl_id}\n" not in f"\n{body}", (
            f"dead Fluent ID {ftl_id!r} still present in {ftl_path}"
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate
pytest tests/pdf/test_pdf_viewer_issue_cards.py -v
```

Expected: 4 fails — 2 for missing new IDs (EN + FR), 2 for the still-present `pdf-viewer-jump-to-page-template` (EN + FR).

- [ ] **Step 3: Edit `auto_a11y/web/translations/en/pdf.ftl`**

The new IDs use the **literal-placeholder convention** that already exists in this file (e.g. `pdf-viewer-overlay-cluster-aria-template = {"{count}"} issues at this location`). `pdf_viewer_app.js` substitutes the placeholders at call time via simple `String.prototype.replace`, which lets the existing flat-string Fluent loader stay simple. Do NOT use Fluent's `{ $name }` interpolation — it would render at template-build time and the JS could not substitute runtime values.

Replace the existing block at lines 442-447:

```ftl
pdf-viewer-issue-list-empty = The visual audit produced no locatable issues. Run an audit if none has been run yet.
# {"{page}"} / {"{count}"} — literal placeholders that pdf_viewer_app.js
# substitutes at runtime; do not translate the placeholder.
pdf-viewer-jump-to-page-template = Page {"{page}"}
pdf-viewer-overlay-cluster-aria-template = {"{count}"} issues at this location
pdf-viewer-semantic-layer-aria-template = Page {"{page}"} content
```

with:

```ftl
pdf-viewer-issue-list-empty = The visual audit produced no locatable issues. Run an audit if none has been run yet.
# {"{count}"} / {"{page}"} / {"{INDEX}"} / {"{TAG}"} / {"{COUNT}"} / {"{CHECK}"}
# — literal placeholders that pdf_viewer_app.js substitutes at
# runtime via String.replace. Do not translate the placeholders.
pdf-viewer-overlay-cluster-aria-template = {"{count}"} issues at this location
pdf-viewer-semantic-layer-aria-template = Page {"{page}"} content

# Issue-card detail parity (2026-05-01 spec) — pdfMax ViewerSidebar
# fields now mirrored in the auto_a11y right-hand panel. Plural forms
# collapse to "1 elements"/"5 elements" because the JS-side substitution
# can't drive Fluent's plural selector; acceptable tradeoff for the
# reduced complexity. Escalate to a JS plural helper if that ever bites.
pdf-viewer-issue-element = [{"{INDEX}"}] {"{TAG}"}
pdf-viewer-issue-document-level = Document-level
pdf-viewer-issue-group-count = {"{COUNT}"} elements
pdf-viewer-issue-view-in-report = View in report
pdf-viewer-issue-view-in-report-aria = View "{"{CHECK}"}" in the pdfMax report

# Live-region announcement on /pdfs/<id>/pdfmax-report when the page
# loads with a #check=<name> hash and successfully scrolls to a check
# section. Suppressed silently when the matching <details> element
# isn't found.
pdfmax-report-jumped-to-check = Showing report section: {"{CHECK}"}
```

(Removes the dead `pdf-viewer-jump-to-page-template`. Adds the six new IDs.)

- [ ] **Step 4: Run test to verify EN side passes; FR side still fails**

```bash
pytest tests/pdf/test_pdf_viewer_issue_cards.py -v
```

Expected: 2 EN tests pass; 2 FR tests still fail. (Will be fixed in Task 2.)

- [ ] **Step 5: Commit (after Task 2; we commit FR + EN together)**

(Skip the commit step here — Task 2 commits both at once.)

---

### Task 2: Add new Fluent IDs to FR `pdf.ftl`; remove dead one

**Files:**
- Modify: `auto_a11y/web/translations/fr/pdf.ftl:430-445`

- [ ] **Step 1: Edit `auto_a11y/web/translations/fr/pdf.ftl`**

Replace the existing block at lines 442-445:

```ftl
pdf-viewer-issue-list-empty = The visual audit produced no locatable issues. Run an audit if none has been run yet.
pdf-viewer-jump-to-page-template = Page {"{page}"}
pdf-viewer-overlay-cluster-aria-template = {"{count}"} issues at this location
pdf-viewer-semantic-layer-aria-template = Page {"{page}"} content
```

with placeholder-English copies of the same six new entries, under the existing `### TODO_FR ###` header convention (the file remains placeholder English until a francophone translator lands):

```ftl
pdf-viewer-issue-list-empty = The visual audit produced no locatable issues. Run an audit if none has been run yet.
pdf-viewer-overlay-cluster-aria-template = {"{count}"} issues at this location
pdf-viewer-semantic-layer-aria-template = Page {"{page}"} content

# Issue-card detail parity (2026-05-01 spec) — TODO_FR placeholder English
pdf-viewer-issue-element = [{"{INDEX}"}] {"{TAG}"}
pdf-viewer-issue-document-level = Document-level
pdf-viewer-issue-group-count = {"{COUNT}"} elements
pdf-viewer-issue-view-in-report = View in report
pdf-viewer-issue-view-in-report-aria = View "{"{CHECK}"}" in the pdfMax report
pdfmax-report-jumped-to-check = Showing report section: {"{CHECK}"}
```

- [ ] **Step 2: Run the Fluent-coverage validator and the new tests**

```bash
source .venv/bin/activate
python tests/validate_translations.py
pytest tests/pdf/test_pdf_viewer_issue_cards.py -v
```

Expected: validator reports zero missing-EN and zero missing-FR keys; `test_required_ftl_ids_present` and `test_removed_ftl_ids_absent` all pass (4/4).

- [ ] **Step 3: Commit**

```bash
source .venv/bin/activate
git add auto_a11y/web/translations/en/pdf.ftl auto_a11y/web/translations/fr/pdf.ftl tests/pdf/test_pdf_viewer_issue_cards.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(pdf-viewer): add Fluent IDs for issue-card detail parity

Six new IDs (element-tag, document-level, group-count plural,
view-in-report + aria, jumped-to-check live-region) match the
fields pdfMax's ViewerSidebar shows. Removes the now-dead
pdf-viewer-jump-to-page-template, whose only consumer (the per-
card Page N button at pdf_viewer_app.js:524-532) is removed in
Part 1 of the 2026-05-01 spec.

FR placeholders mirror EN under the existing TODO_FR header.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Update `window.pdfViewerI18n` in `detail.html`; add `data-pdfmax-report-url`

**Files:**
- Modify: `auto_a11y/web/templates/pdf/detail.html:9-17, 322-324`

- [ ] **Step 1: Add the failing test**

Append to `tests/pdf/test_pdf_viewer_issue_cards.py`:

```python
DETAIL_HTML = REPO_ROOT / "auto_a11y" / "web" / "templates" / "pdf" / "detail.html"


def test_detail_html_i18n_block_has_new_keys() -> None:
    body = DETAIL_HTML.read_text(encoding="utf-8")
    # Each new JS-side key (kebab-case, prefix stripped) must be wired
    # to its Fluent ID via {{ ftl(...) | tojson }} per CLAUDE.md.
    expected_pairs = [
        ('"issue-element"', "pdf-viewer-issue-element"),
        ('"issue-document-level"', "pdf-viewer-issue-document-level"),
        ('"issue-group-count"', "pdf-viewer-issue-group-count"),
        ('"issue-view-in-report"', "pdf-viewer-issue-view-in-report"),
        ('"issue-view-in-report-aria"', "pdf-viewer-issue-view-in-report-aria"),
        ('"jumped-to-check"', "pdfmax-report-jumped-to-check"),
    ]
    for js_key, ftl_id in expected_pairs:
        # The line shape we expect (whitespace-tolerant): <js_key>: ftl('<id>')
        needle = f"{js_key}:"
        assert needle in body, f"missing JS key {js_key} in window.pdfViewerI18n"
        # Same line should mention the Fluent ID.
        line = next(
            (ln for ln in body.splitlines() if needle in ln),
            "",
        )
        assert ftl_id in line, (
            f"JS key {js_key} not wired to ftl('{ftl_id}'); got line: {line!r}"
        )


def test_detail_html_removes_jump_to_page_key() -> None:
    body = DETAIL_HTML.read_text(encoding="utf-8")
    assert '"jump-to-page"' not in body, (
        "dead JS key 'jump-to-page' still present in window.pdfViewerI18n"
    )
    assert "pdf-viewer-jump-to-page-template" not in body, (
        "dead Fluent ID still referenced in detail.html"
    )


def test_detail_html_has_pdfmax_report_url_attr() -> None:
    body = DETAIL_HTML.read_text(encoding="utf-8")
    # The new attribute carries the URL the View-in-report button
    # navigates to. Read by pdf_viewer_app.js when building cards.
    assert "data-pdfmax-report-url" in body, (
        "issue-list container missing data-pdfmax-report-url attribute"
    )
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/pdf/test_pdf_viewer_issue_cards.py::test_detail_html_i18n_block_has_new_keys tests/pdf/test_pdf_viewer_issue_cards.py::test_detail_html_removes_jump_to_page_key tests/pdf/test_pdf_viewer_issue_cards.py::test_detail_html_has_pdfmax_report_url_attr -v
```

Expected: all 3 fail.

- [ ] **Step 3: Edit `detail.html` — i18n block**

Replace the block at lines 9-17 (the `window.pdfViewerI18n = {…};` literal) with:

```jinja
    window.pdfViewerI18n = {
        "loading": {{ ftl('pdf-viewer-loading') | tojson }},
        "error-load": {{ ftl('pdf-viewer-error-load') | tojson }},
        "issue-result-fail": {{ ftl('pdf-violation-result-fail') | tojson }},
        "issue-result-warn": {{ ftl('pdf-violation-result-warn') | tojson }},
        "overlay-cluster-aria": {{ ftl('pdf-viewer-overlay-cluster-aria-template') | tojson }},
        "semantic-layer-aria": {{ ftl('pdf-viewer-semantic-layer-aria-template') | tojson }},
        "issue-element": {{ ftl('pdf-viewer-issue-element') | tojson }},
        "issue-document-level": {{ ftl('pdf-viewer-issue-document-level') | tojson }},
        "issue-group-count": {{ ftl('pdf-viewer-issue-group-count') | tojson }},
        "issue-view-in-report": {{ ftl('pdf-viewer-issue-view-in-report') | tojson }},
        "issue-view-in-report-aria": {{ ftl('pdf-viewer-issue-view-in-report-aria') | tojson }},
        "jumped-to-check": {{ ftl('pdfmax-report-jumped-to-check') | tojson }}
    };
```

(Removes `"jump-to-page"`. Adds the six new keys. Each new value is a literal placeholder template like `[{INDEX}] {TAG}`; the JS substitutes via `String.replace` at call time — same convention as the existing `overlay-cluster-aria` entry.)

- [ ] **Step 4: Edit `detail.html` — issue-list container**

In the section starting at line 311 (`{# Viewer issue list — populated client-side… #}`), change the `<ol>` element at lines 322-324 from:

```jinja
                <ol class="pdf-viewer-issue-list list-unstyled mb-0"
                    data-pdf-viewer-issue-list
                    aria-labelledby="pdf-viewer-issues-heading"></ol>
```

to:

```jinja
                <ol class="pdf-viewer-issue-list list-unstyled mb-0"
                    data-pdf-viewer-issue-list
                    data-pdfmax-report-url="{{ url_for('pdf.pdfmax_report', pdf_document_id=pdf.id) }}"
                    aria-labelledby="pdf-viewer-issues-heading"></ol>
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/pdf/test_pdf_viewer_issue_cards.py -v
```

Expected: all template-side tests (i18n block has new keys; jump-to-page key removed; data-pdfmax-report-url present) pass.

- [ ] **Step 6: Commit**

```bash
source .venv/bin/activate
git add auto_a11y/web/templates/pdf/detail.html tests/pdf/test_pdf_viewer_issue_cards.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(pdf-viewer): wire new i18n keys + report URL on detail.html

Replaces window.pdfViewerI18n's "jump-to-page" entry with the six
keys consumed by the issue-card rewrite in the next commit
(issue-element, issue-document-level, issue-group-count,
issue-view-in-report, issue-view-in-report-aria, jumped-to-check).

Adds data-pdfmax-report-url on the issue-list container so the
new "View in report" button can build its cross-page URL without
the JS hardcoding the route.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Loader — preserve all fields and load document-level issues

**Files:**
- Modify: `auto_a11y/web/static/js/pdf_viewer_app.js:340-370`

The current `_loadIssueMap` skips any issue that doesn't have both `page` AND `bbox`, and also strips `element_index`/`element_tag` from the loaded record. Both must change. We introduce `this.allIssues` (full records) alongside the existing `this.issuesByPage` (overlay-positioning subset).

- [ ] **Step 1: Read constructor + initial-state setup to find where `issuesByPage` is created**

```bash
grep -n "issuesByPage\|this\.issues\b\|this\.allIssues" auto_a11y/web/static/js/pdf_viewer_app.js | head
```

Expected: locate the constructor; note the line where `this.issuesByPage = new Map();` is set so `this.allIssues = [];` can be added next to it.

- [ ] **Step 2: Add `this.allIssues = [];` next to the `issuesByPage` initialisation**

In the constructor (search for `this.issuesByPage = new Map();`), add:

```javascript
        this.allIssues = [];
```

immediately after.

- [ ] **Step 3: Rewrite `_loadIssueMap` to preserve every field and route correctly**

Replace the body of `PdfViewer.prototype._loadIssueMap` (currently lines 340-370) with:

```javascript
    PdfViewer.prototype._loadIssueMap = function () {
        var self = this;
        if (!this.issueMapUrl) return Promise.resolve();
        return fetch(this.issueMapUrl, { credentials: "same-origin" }).then(function (r) {
            if (!r.ok) return null;
            return r.json();
        }).then(function (data) {
            if (!data || !Array.isArray(data.issues)) return;
            self.pageDimensions = data.page_dimensions || {};
            for (var i = 0; i < data.issues.length; i++) {
                var raw = data.issues[i];
                // Full record — preserves element_index/element_tag and
                // document-level issues (page=null, bbox=null) for the
                // sidebar. Bbox is normalised to {x0,y0,x1,y1} when
                // present so overlay code keeps its current shape.
                var issue = {
                    id: raw.id,
                    check_name: raw.check_name,
                    check_result: raw.check_result,
                    element_index: (raw.element_index === undefined) ? null : raw.element_index,
                    element_tag: raw.element_tag || null,
                    detail: raw.detail || "",
                    page: raw.page || null,
                    bbox: null,
                };
                if (raw.bbox && raw.bbox.length === 4) {
                    issue.bbox = { x0: raw.bbox[0], y0: raw.bbox[1], x1: raw.bbox[2], y1: raw.bbox[3] };
                }
                self.allIssues.push(issue);
                // Only issues with both a page and a bbox get an overlay.
                if (issue.page && issue.bbox) {
                    var arr = self.issuesByPage.get(issue.page);
                    if (!arr) { arr = []; self.issuesByPage.set(issue.page, arr); }
                    arr.push(issue);
                }
            }
        }).then(function () {
            self._renderIssueList();
        }).catch(function (err) {
            // Silent — overlays just won't render. Audit may not have run.
            console.info("[pdf_viewer_app] no issue-map available:", err);
            self._renderIssueList();
        });
    };
```

- [ ] **Step 4: Manual smoke (no test yet — behaviour is exercised in Task 7's test)**

```bash
source .venv/bin/activate
pytest tests/pdf/ -v -x
```

Expected: existing tests still pass. The loader change doesn't alter the wire format; it just keeps more in memory.

- [ ] **Step 5: Commit**

```bash
git add auto_a11y/web/static/js/pdf_viewer_app.js
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
fix(pdf-viewer): preserve all issue fields + load document-level entries

The previous _loadIssueMap dropped element_index/element_tag and
skipped any issue without both page+bbox, which silently hid every
document-level issue from the right-hand panel.

Introduces this.allIssues (full records, all entries) alongside
this.issuesByPage (overlay-positioning subset). Card-builder and
group-builder in the next commits consume allIssues; overlays
keep using issuesByPage unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Port `_groupIssues` from pdfMax

**Files:**
- Modify: `auto_a11y/web/static/js/pdf_viewer_app.js` — add a free function near the other small utilities (e.g. after `clusterOverlappingIssues`, around line ~110)

- [ ] **Step 1: Add a unit-style smoke check (in-file IIFE, runs on load only when window.PDF_VIEWER_TEST=true)**

Skip — JS unit testing isn't set up. The port is exercised end-to-end in Task 7's render test.

- [ ] **Step 2: Add the function**

Insert near the other utilities (right before the `// ---------- Page rendering` block, search for that comment to anchor):

```javascript
    // ---------- Issue grouping (port of pdfMax/src/utils/issueGrouping.ts) ---

    /**
     * Bucket issues by (check_name, detail) so multi-element problems
     * collapse into a single accordion card. Returns an array of
     * {groupKey, checkName, checkResult, detail, issues, isMulti} in
     * insertion order — the first occurrence of each (check, detail)
     * pair anchors the group's position in the rendered list, which
     * matches pdfMax's ViewerSidebar behaviour.
     */
    function groupIssues(issues) {
        var map = new Map();
        var order = [];
        for (var i = 0; i < issues.length; i++) {
            var issue = issues[i];
            var key = (issue.check_name || "") + "\x00" + (issue.detail || "");
            var arr = map.get(key);
            if (arr) {
                arr.push(issue);
            } else {
                arr = [issue];
                map.set(key, arr);
                order.push(key);
            }
        }
        var out = [];
        for (var j = 0; j < order.length; j++) {
            var k = order[j];
            var groupArr = map.get(k);
            out.push({
                groupKey: k,
                checkName: groupArr[0].check_name,
                checkResult: groupArr[0].check_result,
                detail: groupArr[0].detail,
                issues: groupArr,
                isMulti: groupArr.length > 1,
            });
        }
        return out;
    }
```

- [ ] **Step 3: Run existing tests to confirm no regression**

```bash
source .venv/bin/activate
pytest tests/pdf/ -v -x
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add auto_a11y/web/static/js/pdf_viewer_app.js
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(pdf-viewer): port pdfMax groupIssues into pdf_viewer_app.js

1:1 port of pdfMax/src/utils/issueGrouping.ts. Bucket key is
check_name + "\x00" + detail; first-occurrence insertion order is
preserved so the rendered list matches pdfMax's ViewerSidebar.
Group of 1 → standalone card; group of 2+ → isMulti accordion.

Wired up by the _renderIssueList rewrite in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Refactor `_renderIssueList` to consume `allIssues` via `groupIssues`

**Files:**
- Modify: `auto_a11y/web/static/js/pdf_viewer_app.js:380-402`

- [ ] **Step 1: Replace `_renderIssueList` body**

Replace lines 380-402 with:

```javascript
    /**
     * Render the right-pane issue list from issue_map.json. Cards mirror
     * pdfMax's ViewerSidebar 1:1 — single-element groups render as flat
     * cards with element-tag/index lines; multi-element groups collapse
     * into an accordion summary. Cards keep data-issue-id matching the
     * overlay rect on the canvas so the connector-line code resolves
     * both ends.
     *
     * The auto_a11y rich-report violation list below this panel comes
     * from a different audit engine and can disagree; we deliberately
     * don't merge.
     */
    PdfViewer.prototype._renderIssueList = function () {
        if (!this.issueListEl) return;
        this.issueListEl.innerHTML = "";

        if (this.allIssues.length === 0) {
            if (this.issueListEmptyEl) this.issueListEmptyEl.hidden = false;
            return;
        }
        if (this.issueListEmptyEl) this.issueListEmptyEl.hidden = true;

        var groups = groupIssues(this.allIssues);
        for (var g = 0; g < groups.length; g++) {
            var grp = groups[g];
            var node = grp.isMulti
                ? this._buildIssueGroupCard(grp)
                : this._buildIssueCard(grp.issues[0]);
            this.issueListEl.appendChild(node);
        }
    };
```

- [ ] **Step 2: Run existing tests**

```bash
pytest tests/pdf/ -v -x
```

Expected: existing tests still pass. (The rewrite is wired but the new card builders are tested in Task 7.)

- [ ] **Step 3: Commit**

```bash
git add auto_a11y/web/static/js/pdf_viewer_app.js
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
refactor(pdf-viewer): _renderIssueList consumes allIssues via groupIssues

Iterates this.allIssues (every issue, including document-level)
through groupIssues(); single-element groups dispatch to the
existing _buildIssueCard, multi-element groups dispatch to the
new _buildIssueGroupCard added in the next commit.

The previous page-keyed ordering was a pdfMax-divergent artefact
of the old loader filter; pdfMax's order (first-occurrence by
check+detail bucket) is restored here.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Rewrite `_buildIssueCard` and add `_buildIssueGroupCard`

**Files:**
- Modify: `auto_a11y/web/static/js/pdf_viewer_app.js:476-538`

The single-element rewrite removes the per-card "Page N" jump button (lines 524-532) and adds:
- the `[<element_index>] <element_tag>` line below the check name (only when both fields are non-null)
- the "Document-level" badge (only when `page` and `bbox` are both null)
- the "View in report" button below the detail text (always)

The new group-card builder mirrors pdfMax's accordion shape.

- [ ] **Step 1: Add the failing render-shape test**

Append to `tests/pdf/test_pdf_viewer_issue_cards.py`:

```python
JS_VIEWER = REPO_ROOT / "auto_a11y" / "web" / "static" / "js" / "pdf_viewer_app.js"


def test_pdf_viewer_app_has_no_jump_to_page_button() -> None:
    """The per-card 'Page N' jump button is removed in this pass."""
    body = JS_VIEWER.read_text(encoding="utf-8")
    # The previous implementation had a `pdf-viewer-issue-page-btn`
    # class. Spec calls for its removal in favour of a "View in
    # report" button. Asserts both the class name and the dead
    # i18n key are gone from the JS source.
    assert "pdf-viewer-issue-page-btn" not in body, (
        "dead .pdf-viewer-issue-page-btn class still referenced in JS"
    )
    assert '"jump-to-page"' not in body, (
        "dead 'jump-to-page' i18n key still referenced in JS"
    )


def test_pdf_viewer_app_renders_element_tag_and_doclevel() -> None:
    """The card builder uses the new i18n keys and renders the new
    fields. Pure source-presence — actual DOM rendering would need a
    JSDOM harness which we don't run here.
    """
    body = JS_VIEWER.read_text(encoding="utf-8")
    for needle in (
        '"issue-element"',
        '"issue-document-level"',
        '"issue-view-in-report"',
        '"issue-view-in-report-aria"',
        "_buildIssueGroupCard",
        "data-pdfmax-report-url",
    ):
        assert needle in body, f"pdf_viewer_app.js missing reference to {needle}"


def test_pdf_viewer_app_uses_groupissues() -> None:
    body = JS_VIEWER.read_text(encoding="utf-8")
    assert "function groupIssues" in body, "groupIssues port not found in JS source"
    assert "groupIssues(this.allIssues)" in body, (
        "_renderIssueList not wired to groupIssues(this.allIssues)"
    )
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/pdf/test_pdf_viewer_issue_cards.py -v
```

Expected: the three new tests fail (and the earlier ones still pass).

- [ ] **Step 3: Replace `_buildIssueCard` and append `_buildIssueGroupCard`**

Replace `PdfViewer.prototype._buildIssueCard` (currently lines 476-538) and add the new builder. Use:

```javascript
    /**
     * Resolve the report URL once per card render via the data-attribute
     * on the issue-list container. Returns "" when not set, in which
     * case the "View in report" button is suppressed.
     */
    PdfViewer.prototype._reportUrl = function () {
        if (!this.issueListEl) return "";
        return this.issueListEl.getAttribute("data-pdfmax-report-url") || "";
    };

    /**
     * Build the "View in report" button. Returns null when no report
     * URL is available (e.g. test fixtures, or audits that pre-date
     * the pdfmax-report cache).
     */
    PdfViewer.prototype._buildViewInReportButton = function (checkName) {
        var reportUrl = this._reportUrl();
        if (!reportUrl) return null;
        var anchor = document.createElement("a");
        anchor.className = "btn btn-outline-brand btn-sm pdf-viewer-issue-view-in-report";
        anchor.href = reportUrl + "#check=" + encodeURIComponent(checkName);
        anchor.textContent = t("issue-view-in-report", "View in report");
        var ariaTpl = t("issue-view-in-report-aria", 'View "{CHECK}" in the pdfMax report');
        anchor.setAttribute("aria-label", ariaTpl.replace("{CHECK}", checkName));
        return anchor;
    };

    /**
     * Build the `[<index>] <tag>` line for an issue. Returns null when
     * either field is missing (the line is suppressed in that case).
     */
    PdfViewer.prototype._buildElementLine = function (issue) {
        if (issue.element_index == null || !issue.element_tag) return null;
        var span = document.createElement("span");
        span.className = "pdf-viewer-issue-card-element";
        var tpl = t("issue-element", "[{INDEX}] {TAG}");
        span.textContent = tpl
            .replace("{INDEX}", String(issue.element_index))
            .replace("{TAG}", String(issue.element_tag));
        return span;
    };

    PdfViewer.prototype._buildIssueCard = function (issue) {
        var isFail = (issue.check_result === "FAIL");

        var li = document.createElement("li");
        li.className = "pdf-viewer-issue-card " + (isFail ? "is-fail" : "is-warn");
        li.setAttribute("data-issue-id", issue.id);

        var details = document.createElement("details");
        details.className = "pdf-viewer-issue-details";
        details.setAttribute("data-check-result", issue.check_result);
        if (issue.check_name) details.setAttribute("data-check-name", issue.check_name);

        var summary = document.createElement("summary");
        summary.className = "pdf-viewer-issue-summary";

        var resultBadge = document.createElement("span");
        resultBadge.className = "badge " + (isFail ? "badge-high" : "badge-medium");
        resultBadge.textContent = isFail
            ? t("issue-result-fail", "Fail")
            : t("issue-result-warn", "Warn");
        summary.appendChild(resultBadge);

        var name = document.createElement("span");
        name.className = "pdf-viewer-issue-card-name";
        name.textContent = issue.check_name;
        summary.appendChild(name);

        if (issue.page) {
            var pageTag = document.createElement("span");
            pageTag.className = "pdf-viewer-issue-card-page";
            pageTag.textContent = "p." + issue.page;
            summary.appendChild(pageTag);
        }

        details.appendChild(summary);

        var body = document.createElement("div");
        body.className = "pdf-viewer-issue-card-body";

        // Element-tag line OR document-level marker — never both.
        var elementLine = this._buildElementLine(issue);
        if (elementLine) {
            body.appendChild(elementLine);
        } else if (issue.page == null && issue.bbox == null) {
            var docBadge = document.createElement("span");
            docBadge.className = "badge badge-neutral pdf-viewer-issue-card-document-level";
            docBadge.textContent = t("issue-document-level", "Document-level");
            body.appendChild(docBadge);
        }

        if (issue.detail) {
            renderDetailMarkdown(issue.detail, body);
        }

        var viewBtn = this._buildViewInReportButton(issue.check_name);
        if (viewBtn) body.appendChild(viewBtn);

        details.appendChild(body);
        li.appendChild(details);

        return li;
    };

    /**
     * Multi-element accordion card. Header shows severity badge, check
     * name, and "{COUNT} elements". Expanded body shows each child's
     * `[<index>] <tag>` and `p.<page>` on its own row.
     */
    PdfViewer.prototype._buildIssueGroupCard = function (group) {
        var isFail = (group.checkResult === "FAIL");

        var li = document.createElement("li");
        li.className = "pdf-viewer-issue-card pdf-viewer-issue-card-group "
            + (isFail ? "is-fail" : "is-warn");
        li.setAttribute("data-group-key", group.groupKey);

        var details = document.createElement("details");
        details.className = "pdf-viewer-issue-details";
        details.setAttribute("data-check-result", group.checkResult);
        if (group.checkName) details.setAttribute("data-check-name", group.checkName);

        var summary = document.createElement("summary");
        summary.className = "pdf-viewer-issue-summary";

        var resultBadge = document.createElement("span");
        resultBadge.className = "badge " + (isFail ? "badge-high" : "badge-medium");
        resultBadge.textContent = isFail
            ? t("issue-result-fail", "Fail")
            : t("issue-result-warn", "Warn");
        summary.appendChild(resultBadge);

        var name = document.createElement("span");
        name.className = "pdf-viewer-issue-card-name";
        name.textContent = group.checkName;
        summary.appendChild(name);

        var countSpan = document.createElement("span");
        countSpan.className = "pdf-viewer-issue-card-count";
        var countTpl = t("issue-group-count", "{COUNT} elements");
        countSpan.textContent = countTpl.replace("{COUNT}", String(group.issues.length));
        summary.appendChild(countSpan);

        details.appendChild(summary);

        var body = document.createElement("div");
        body.className = "pdf-viewer-issue-card-body";

        // Detail text once at the top of the group (it's identical
        // across all members, since "detail" is part of the group key).
        if (group.detail) {
            renderDetailMarkdown(group.detail, body);
        }

        var viewBtn = this._buildViewInReportButton(group.checkName);
        if (viewBtn) body.appendChild(viewBtn);

        // Per-element rows.
        var ul = document.createElement("ul");
        ul.className = "pdf-viewer-issue-group-children list-unstyled mb-0";
        for (var i = 0; i < group.issues.length; i++) {
            var child = group.issues[i];
            var row = document.createElement("li");
            row.className = "pdf-viewer-issue-group-child";
            row.setAttribute("data-issue-id", child.id);

            var elementLine = this._buildElementLine(child);
            if (elementLine) {
                row.appendChild(elementLine);
            } else if (child.page == null && child.bbox == null) {
                var docBadge = document.createElement("span");
                docBadge.className = "badge badge-neutral pdf-viewer-issue-card-document-level";
                docBadge.textContent = t("issue-document-level", "Document-level");
                row.appendChild(docBadge);
            }

            if (child.page) {
                var pageTag = document.createElement("span");
                pageTag.className = "pdf-viewer-issue-card-page";
                pageTag.textContent = "p." + child.page;
                row.appendChild(pageTag);
            }

            ul.appendChild(row);
        }
        body.appendChild(ul);

        details.appendChild(body);
        li.appendChild(details);

        return li;
    };
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate
pytest tests/pdf/test_pdf_viewer_issue_cards.py -v
```

Expected: all tests pass (Fluent IDs present, detail.html keys present, dead-key absent, JS file references new keys + `_buildIssueGroupCard` + `data-pdfmax-report-url`, `groupIssues` wired into `_renderIssueList`).

- [ ] **Step 5: Run the full PDF test suite**

```bash
pytest tests/pdf/ -v
```

Expected: green. Existing route + template tests unaffected.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/web/static/js/pdf_viewer_app.js tests/pdf/test_pdf_viewer_issue_cards.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(pdf-viewer): rewrite issue card with element-tag, doc-level, accordion, View-in-report

Closes the right-hand panel parity gap. Single-element cards now
render the [<element_index>] <element_tag> line below the check
name, or "Document-level" when page and bbox are both null. Each
card gains a "View in report" button that links to
/pdfs/<id>/pdfmax-report#check=<urlencoded> using the URL exposed
on the new data-pdfmax-report-url attribute.

Multi-element groups (2+ issues sharing check_name+detail) collapse
into a <details> accordion: summary shows severity + check name +
"{N} elements"; expanded body shows each child's element-line and
page indicator.

Removes the per-card "Page N" jump button — pdfMax has no
equivalent and the new "View in report" button takes its place.

Closes Part 1 of the 2026-05-01 spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Add hash-handler to `pdfmax_report.html`

**Files:**
- Modify: `auto_a11y/web/templates/pdf/pdfmax_report.html:127-205`
- Create: `tests/pdf/test_pdfmax_report_anchor.py`

The hash-handler runs after markdown sanitisation (so the rendered DOM exists), parses `location.hash` for `check=<urlencoded>`, finds the matching `<details data-check-name="...">`, opens it, scrolls it into view, focuses the `<summary>`, and announces via the existing `[role="status"]` live region.

- [ ] **Step 1: Create the failing test**

```python
"""Hash-handler tests for /pdfs/<id>/pdfmax-report (Part 1, 2026-05-01 spec)."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PDFMAX_REPORT_HTML = (
    REPO_ROOT / "auto_a11y" / "web" / "templates" / "pdf" / "pdfmax_report.html"
)


def test_pdfmax_report_embeds_hash_handler_script() -> None:
    body = PDFMAX_REPORT_HTML.read_text(encoding="utf-8")
    # The hash-handler is identifiable by:
    #   - a unique marker comment we add
    #   - the hash-parse keyword 'check='
    #   - the data-check-name attribute selector
    #   - the i18n message id 'pdfmax-report-jumped-to-check'
    assert "pdfmax-report-hash-handler" in body, (
        "hash-handler script marker missing from pdfmax_report.html"
    )
    assert "data-check-name" in body, (
        "hash-handler must querySelector by data-check-name"
    )
    assert "pdfmax-report-jumped-to-check" in body, (
        "hash-handler must wire the live-region message"
    )
    assert "check=" in body, (
        "hash-handler must parse a check= hash fragment"
    )
```

- [ ] **Step 2: Run to confirm failure**

```bash
source .venv/bin/activate
pytest tests/pdf/test_pdfmax_report_anchor.py -v
```

Expected: 1 fail.

- [ ] **Step 3: Edit `pdfmax_report.html`**

Inside the existing inline render IIFE (the one that calls `marked.parse` + `DOMPurify.sanitize`), inside the `render` function, after the line `target.hidden = false;` (around line 196), call `applyCheckHash()` and add the two new helper functions at IIFE scope (sibling level to `render`, NOT nested inside it). JavaScript hoists function declarations within their enclosing function scope, so calling `applyCheckHash` from inside `render` works as long as the declaration sits at IIFE scope. Place the helper declarations right after the `render` function's closing `}`. The block looks like:

```html
                if (loading !== null) {
                    loading.hidden = true;
                }
                target.hidden = false;

                // pdfmax-report-hash-handler — when the page is loaded
                // with #check=<urlencoded>, scroll to the matching
                // <details data-check-name="..."> emitted by pdfMax's
                // markdown. Opens the <details>, focuses the <summary>,
                // and announces via the existing [role="status"]
                // live region (the loading element, repurposed once
                // it's hidden).
                applyCheckHash();
            }

            // applyCheckHash and announceJump are siblings of render,
            // declared at IIFE scope so render() can call them via
            // function-declaration hoisting. Do NOT nest them inside
            // render — the existing if (document.readyState ...)
            // block at the bottom of the IIFE only registers `render`,
            // and these helpers must be reachable from there.
            function applyCheckHash() {
                var hash = (window.location.hash || "").replace(/^#/, "");
                if (!hash) return;
                var pairs = hash.split("&");
                var checkName = null;
                for (var i = 0; i < pairs.length; i++) {
                    var eq = pairs[i].indexOf("=");
                    if (eq === -1) continue;
                    var key = pairs[i].slice(0, eq);
                    if (key !== "check") continue;
                    try {
                        checkName = decodeURIComponent(pairs[i].slice(eq + 1));
                    } catch (e) {
                        return;
                    }
                    break;
                }
                if (!checkName) return;
                // CSS-attribute selector tolerates spaces and most
                // punctuation; we still wrap the value in CSS.escape
                // when available to be safe against quote chars.
                var literal = (typeof window.CSS !== "undefined" && typeof window.CSS.escape === "function")
                    ? window.CSS.escape(checkName)
                    : checkName.replace(/"/g, '\\"');
                var match = target.querySelector('[data-check-name="' + literal + '"]');
                if (match === null) return;
                if (match.tagName === "DETAILS") {
                    match.open = true;
                }
                try {
                    match.scrollIntoView({ behavior: "smooth", block: "start" });
                } catch (err) {
                    match.scrollIntoView();
                }
                var summary = match.querySelector("summary");
                if (summary && typeof summary.focus === "function") {
                    summary.focus();
                }
                announceJump(checkName);
            }

            function announceJump(checkName) {
                if (loading === null) return;
                // Reuse the existing role="status" / aria-live="polite"
                // element so SR users hear the jump. Template injects
                // the message with a {CHECK} placeholder.
                var msgTemplate =
                    (window.pdfMaxReportI18n && window.pdfMaxReportI18n["jumped-to-check"])
                        ? window.pdfMaxReportI18n["jumped-to-check"]
                        : "Showing report section: {CHECK}";
                loading.hidden = false;
                loading.textContent = msgTemplate.replace("{CHECK}", checkName);
            }
```

Then, immediately after the existing `<script>window.PDFMAX_REPORT_MARKDOWN = ...</script>` block (around line 100), add a sibling `<script>` to expose the i18n string:

```html
    <script>
        window.pdfMaxReportI18n = {
            "jumped-to-check": {{ ftl('pdfmax-report-jumped-to-check') | tojson }}
        };
    </script>
```

(Mirrors the convention `detail.html` uses for `pdfViewerI18n`.)

- [ ] **Step 4: Run the new test**

```bash
pytest tests/pdf/test_pdfmax_report_anchor.py -v
```

Expected: pass.

- [ ] **Step 5: Run the broader suite to confirm no regressions**

```bash
pytest tests/pdf/ -v
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/web/templates/pdf/pdfmax_report.html tests/pdf/test_pdfmax_report_anchor.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(pdfmax-report): scroll-to-check via #check=<urlencoded> hash

Targets the data-check-name="..." attribute that pdfMax's markdown
already emits on <details>/<summary> blocks (preserved through
DOMPurify via the existing ADD_ATTR allowlist). Opens the
<details>, scrolls into view, focuses the <summary>, and reuses
the existing role="status" live region to announce the jump.

Wired with the "View in report" button added by Task 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Part 2: PDF rollup into website / project totals

### Task 9: Add `auto_a11y/pdf/issue_map_counts.py`

**Files:**
- Create: `auto_a11y/pdf/issue_map_counts.py`
- Create: `tests/pdf/test_issue_map_counts.py`

- [ ] **Step 1: Write the failing tests**

`tests/pdf/test_issue_map_counts.py`:

```python
"""Unit tests for auto_a11y.pdf.issue_map_counts."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest
from bson import ObjectId

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.issue_map_counts import PdfIssueCounts, count_issues
from auto_a11y.pdf.storage import PdfStorage


def _make_doc(*, website_id: str = "w-1", status: PdfDocumentStatus = PdfDocumentStatus.AUDITED) -> PdfDocument:
    oid = ObjectId()
    doc = PdfDocument(
        website_id=website_id,
        project_id="p-1",
        source_url=None,
        source_type="uploaded",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256="a" * 64,
        file_size_bytes=1,
        storage_relpath=f"{website_id}/{oid}/pdf.pdf",
        images_relpath=f"{website_id}/{oid}/images/",
        original_filename="doc.pdf",
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=status,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
    )
    doc.mongo_id = oid
    return doc


def _materialise(storage: PdfStorage, doc: PdfDocument) -> Path:
    pdf_path = storage.local_path(doc)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n%%EOF\n")
    return pdf_path


def test_pdf_issue_counts_zero_default() -> None:
    zero = PdfIssueCounts(0, 0)
    assert zero.violations == 0
    assert zero.warnings == 0


def test_pdf_issue_counts_addition() -> None:
    a = PdfIssueCounts(2, 3)
    b = PdfIssueCounts(4, 5)
    c = a + b
    assert c == PdfIssueCounts(6, 8)
    # Frozen — original unchanged.
    assert a == PdfIssueCounts(2, 3)


def test_count_issues_zero_when_pdf_unaudited(tmp_path: Path) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc(status=PdfDocumentStatus.PENDING)
    assert count_issues(doc, storage) == PdfIssueCounts(0, 0)


def test_count_issues_zero_when_cache_dir_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    _materialise(storage, doc)
    # Status is AUDITED but no pdfmax-report cache exists.
    with caplog.at_level(logging.WARNING, logger="auto_a11y.pdf.issue_map_counts"):
        result = count_issues(doc, storage)
    assert result == PdfIssueCounts(0, 0)
    assert any("missing pdfmax-report cache" in rec.message for rec in caplog.records)


def test_count_issues_zero_when_cache_dir_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    (pdf_path.parent / "pdfmax-report").mkdir()
    with caplog.at_level(logging.WARNING, logger="auto_a11y.pdf.issue_map_counts"):
        result = count_issues(doc, storage)
    assert result == PdfIssueCounts(0, 0)
    assert any("no issue_map.json found" in rec.message for rec in caplog.records)


def test_count_issues_zero_on_malformed_json(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir()
    (cache / "doc_issue_map.json").write_text("{ not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="auto_a11y.pdf.issue_map_counts"):
        result = count_issues(doc, storage)
    assert result == PdfIssueCounts(0, 0)
    assert any("malformed" in rec.message.lower() for rec in caplog.records)


def test_count_issues_zero_on_missing_issues_key(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir()
    (cache / "doc_issue_map.json").write_text(
        json.dumps({"version": 1}), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING, logger="auto_a11y.pdf.issue_map_counts"):
        result = count_issues(doc, storage)
    assert result == PdfIssueCounts(0, 0)


def test_count_issues_tallies_fail_and_warn(tmp_path: Path) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir()
    payload = {
        "version": 1,
        "issues": [
            {"id": "0", "check_result": "FAIL"},
            {"id": "1", "check_result": "FAIL"},
            {"id": "2", "check_result": "WARN"},
            {"id": "3", "check_result": "FAIL"},
            {"id": "4", "check_result": "WARN"},
        ],
    }
    (cache / "doc_issue_map.json").write_text(json.dumps(payload), encoding="utf-8")
    assert count_issues(doc, storage) == PdfIssueCounts(violations=3, warnings=2)


def test_count_issues_ignores_unknown_severity(tmp_path: Path) -> None:
    storage = PdfStorage(base_dir=tmp_path)
    doc = _make_doc()
    pdf_path = _materialise(storage, doc)
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir()
    payload = {
        "version": 1,
        "issues": [
            {"id": "0", "check_result": "FAIL"},
            {"id": "1", "check_result": "INFO"},
            {"id": "2", "check_result": "PASS"},
            {"id": "3", "check_result": "WARN"},
            {"id": "4", "check_result": ""},
            {"id": "5"},  # missing key entirely
        ],
    }
    (cache / "doc_issue_map.json").write_text(json.dumps(payload), encoding="utf-8")
    assert count_issues(doc, storage) == PdfIssueCounts(violations=1, warnings=1)
```

- [ ] **Step 2: Run to verify failure**

```bash
source .venv/bin/activate
pytest tests/pdf/test_issue_map_counts.py -v
```

Expected: ImportError on `auto_a11y.pdf.issue_map_counts` (module doesn't exist).

- [ ] **Step 3: Create `auto_a11y/pdf/issue_map_counts.py`**

```python
"""Tally FAIL/WARN counts from a PDF's cached pdfMax issue_map.json.

Project-detail and website-detail aggregations call this helper for
every audited :class:`~auto_a11y.models.pdf_document.PdfDocument` and
add the result to the page-level Mongo aggregation, so the global
"violations" / "warnings" totals reflect both HTML and PDF audits.

The helper is intentionally read-only and side-effect-free apart from
WARNING-level logging when an audited PDF has no readable cache. The
issue-map JSON remains the single source of truth — no per-PDF count
field is persisted.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.storage import PdfStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PdfIssueCounts:
    """Counts of FAIL (violations) and WARN (warnings) for one PDF.

    Frozen so callers can sum a list without worrying about aliasing;
    ``__add__`` returns a new instance. The "no audit yet" / "missing
    cache" / "malformed JSON" cases all collapse into ``PdfIssueCounts(0, 0)``,
    so callers don't need to branch.
    """

    violations: int
    warnings: int

    def __add__(self, other: "PdfIssueCounts") -> "PdfIssueCounts":
        return PdfIssueCounts(
            violations=self.violations + other.violations,
            warnings=self.warnings + other.warnings,
        )


_ZERO = PdfIssueCounts(0, 0)


def count_issues(pdf: PdfDocument, storage: PdfStorage) -> PdfIssueCounts:
    """Read the cached ``*_issue_map.json`` and tally FAIL/WARN.

    Returns :data:`_ZERO` when the PDF has not been audited, when the
    cache directory or file is missing, when the JSON is malformed, or
    when the file's top-level shape is unexpected. Logs a WARNING in
    the AUDITED-but-missing-cache and malformed-JSON cases so the
    operator notices the inconsistency without the failure 500-ing
    the page.

    Expected JSON shape::

        {"version": 1, "issues": [{"check_result": "FAIL", ...}, ...]}

    Per ``pdfMax/python/checker/pdf_accessibility_audit.py``. Any other
    top-level shape (bare list, missing ``"issues"`` key, non-list
    value at ``"issues"``) is treated as malformed → 0/0 + warning.
    Within the list, only entries where ``check_result`` is exactly
    ``"FAIL"`` or ``"WARN"`` count.
    """
    if pdf.status is not PdfDocumentStatus.AUDITED:
        return _ZERO

    pdf_path = storage.local_path(pdf)
    cache_dir = pdf_path.parent / "pdfmax-report"
    if not cache_dir.is_dir():
        logger.warning(
            "PDF %s is AUDITED but missing pdfmax-report cache at %s",
            pdf.id, cache_dir,
        )
        return _ZERO

    candidates = sorted(cache_dir.glob("*_issue_map.json"))
    if not candidates:
        logger.warning(
            "PDF %s is AUDITED but no issue_map.json found in %s",
            pdf.id, cache_dir,
        )
        return _ZERO

    return _tally(pdf, candidates[0])


def _tally(pdf: PdfDocument, json_path: Path) -> PdfIssueCounts:
    try:
        raw = json_path.read_bytes()
    except OSError as exc:
        logger.warning(
            "PDF %s: failed to read issue_map.json at %s: %s",
            pdf.id, json_path, exc,
        )
        return _ZERO

    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "PDF %s: malformed issue_map.json at %s: %s",
            pdf.id, json_path, exc,
        )
        return _ZERO

    if not isinstance(data, dict):
        logger.warning(
            "PDF %s: malformed issue_map.json at %s "
            "(top-level not an object)",
            pdf.id, json_path,
        )
        return _ZERO

    issues = data.get("issues")
    if not isinstance(issues, list):
        # Missing key, or "issues" is not a list — both treated as zero.
        return _ZERO

    violations = 0
    warnings = 0
    for entry in issues:
        if not isinstance(entry, dict):
            continue
        result = entry.get("check_result")
        if result == "FAIL":
            violations += 1
        elif result == "WARN":
            warnings += 1
    return PdfIssueCounts(violations=violations, warnings=warnings)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/pdf/test_issue_map_counts.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Run typecheckers locally**

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
```

Expected: zero errors. (The pre-commit hook runs all three; running them now catches any issue early.)

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/pdf/issue_map_counts.py tests/pdf/test_issue_map_counts.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(pdf): PdfIssueCounts + count_issues helper for rollup aggregation

Reads the cached pdfMax issue_map.json that PdfAuditJob writes and
tallies FAIL/WARN. Frozen dataclass with __add__ so callers can sum
a list without aliasing. All failure paths (missing cache, malformed
JSON, unexpected top-level shape) collapse to PdfIssueCounts(0, 0)
with a WARNING log; never raises.

Wired into the website-detail and project-detail rollups in the
next two commits per Part 2 of the 2026-05-01 spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Wire rollup into `websites.py`

**Files:**
- Modify: `auto_a11y/web/routes/websites.py:69-100, 119`
- Create: `tests/pdf/test_pdf_rollup_routes.py`

The website-detail handler already fetches `website_pdfs` at line 119 (with `limit=10000`). We extend that block to compute `pdf_totals` and add into the existing `stats` dict before render. No second DB call.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for website-/project-detail rollup including audited PDFs.

Part 2 of the 2026-05-01 spec — verifies that PDFs contribute to the
total_violations / total_warnings counts shown on the website-detail
and project-detail pages, without any new persisted field.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.issue_map_counts import PdfIssueCounts, count_issues
from auto_a11y.pdf.storage import PdfStorage


def _make_doc(
    *,
    website_id: str = "w-1",
    project_id: str = "p-1",
    status: PdfDocumentStatus = PdfDocumentStatus.AUDITED,
) -> PdfDocument:
    oid = ObjectId()
    doc = PdfDocument(
        website_id=website_id,
        project_id=project_id,
        source_url=None,
        source_type="uploaded",
        discovered_from_page_id=None,
        discovered_from_user_id=None,
        sha256="a" * 64,
        file_size_bytes=1,
        storage_relpath=f"{website_id}/{oid}/pdf.pdf",
        images_relpath=f"{website_id}/{oid}/images/",
        original_filename="doc.pdf",
        pdf_version=None,
        page_count=None,
        declared_lang=None,
        detected_lang=None,
        lang_confidence=None,
        status=status,
        error_reason=None,
        last_audit_result_id=None,
        discovered_at=datetime.now(),
        last_audited_at=None,
    )
    doc.mongo_id = oid
    return doc


def _write_cache(storage: PdfStorage, doc: PdfDocument, fail: int, warn: int) -> None:
    pdf_path = storage.local_path(doc)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n%%EOF\n")
    cache = pdf_path.parent / "pdfmax-report"
    cache.mkdir(parents=True, exist_ok=True)
    issues = (
        [{"id": f"f-{i}", "check_result": "FAIL"} for i in range(fail)]
        + [{"id": f"w-{i}", "check_result": "WARN"} for i in range(warn)]
    )
    (cache / "doc_issue_map.json").write_text(
        json.dumps({"version": 1, "issues": issues}), encoding="utf-8"
    )


def test_count_issues_two_pdfs_summed(tmp_path: Path) -> None:
    """Sanity check that PdfIssueCounts.__add__ + count_issues compose
    the way the rollup loop expects."""
    storage = PdfStorage(base_dir=tmp_path)
    a = _make_doc()
    b = _make_doc()
    _write_cache(storage, a, fail=3, warn=4)
    _write_cache(storage, b, fail=1, warn=2)
    total = count_issues(a, storage) + count_issues(b, storage)
    assert total == PdfIssueCounts(violations=4, warnings=6)


def test_count_issues_skips_when_cache_missing(tmp_path: Path) -> None:
    """An AUDITED PDF with no cache file must not 500 the page or
    poison the running total."""
    storage = PdfStorage(base_dir=tmp_path)
    audited = _make_doc()
    cacheless = _make_doc()
    _write_cache(storage, audited, fail=2, warn=1)
    # cacheless intentionally has no cache dir
    pdf_path = storage.local_path(cacheless)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n%%EOF\n")

    total = count_issues(audited, storage) + count_issues(cacheless, storage)
    assert total == PdfIssueCounts(violations=2, warnings=1)


# Route-level tests follow. The websites.py handler does enough Mongo
# work that mocking it cleanly is more involved than the single
# helper-level smoke; we leave the integration assertion at the
# helper level + a smoke test that imports the modified module
# successfully.

def test_websites_route_imports_helper() -> None:
    """Smoke: websites.py imports PdfIssueCounts/count_issues."""
    import auto_a11y.web.routes.websites as websites
    assert hasattr(websites, "_pdf_rollup") or "count_issues" in websites.__dict__ or any(
        name in dir(websites) for name in ("PdfIssueCounts", "count_issues")
    ), "websites.py does not appear to import the rollup helper"


def test_projects_route_imports_helper() -> None:
    """Smoke: projects.py imports PdfIssueCounts/count_issues."""
    import auto_a11y.web.routes.projects as projects
    assert hasattr(projects, "_pdf_rollup") or "count_issues" in projects.__dict__ or any(
        name in dir(projects) for name in ("PdfIssueCounts", "count_issues")
    ), "projects.py does not appear to import the rollup helper"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/pdf/test_pdf_rollup_routes.py -v
```

Expected: the two `_imports_helper` tests fail (modules don't import the helper yet); the count-issues compose tests pass (helper from Task 9 is in place).

- [ ] **Step 3: Edit `auto_a11y/web/routes/websites.py`**

The existing import block at the top of the file does NOT have `Path`, `PdfStorage`, `PdfDocument`, or `PdfDocumentStatus`. Add the four new imports below the existing imports (after line 14, after `import logging`):

```python
from pathlib import Path

from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
from auto_a11y.pdf.issue_map_counts import PdfIssueCounts, count_issues
from auto_a11y.pdf.storage import PdfStorage
```

(`PdfDocument` is unused at function scope but ergonomic for typing if a future change adds an annotation; if mypy/pyright/ty flag the import as unused, drop it. The current rollup loop only references `PdfDocumentStatus` and `count_issues`.)

Then locate the handler block at lines 102-137 and modify it. The current shape is:

```python
    # ... earlier: pages stats aggregated into `stats` dict at lines 85-100

    # Get paginated pages for display ...
    skip = (page_num - 1) * per_page
    pages = get_db().get_pages(website_id, limit=per_page, skip=skip, latest_only=False)

    # ... pagination math ...

    # Get available test users for this project
    project_users = get_db().get_project_users(website.project_id, enabled_only=True)

    # PDF nav badge count (Phase 9.7 — additive)
    website_pdfs = get_db().get_pdf_documents(website_id=website_id, limit=10000)
    pdf_count = len(website_pdfs)
    from auto_a11y.web.routes.projects import summarise_pdf_status
    pdf_status_counts = summarise_pdf_status(website_pdfs)
```

Insert after the `website_pdfs = ...` line and before the rest of the PDF-status calculations, the rollup pass:

```python
    # PDF nav badge count (Phase 9.7 — additive)
    website_pdfs = get_db().get_pdf_documents(website_id=website_id, limit=10000)
    pdf_count = len(website_pdfs)

    # PDF rollup into the global violation/warning totals (2026-05-01
    # spec Part 2). Reads the cached pdfMax issue_map.json for each
    # AUDITED PDF; the helper degrades to (0, 0) on missing/malformed
    # caches with a WARNING log, so a single bad cache never 500s
    # the page.
    storage = PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))
    pdf_totals = PdfIssueCounts(0, 0)
    for pdf in website_pdfs:
        if pdf.status is PdfDocumentStatus.AUDITED:
            pdf_totals = pdf_totals + count_issues(pdf, storage)
    stats['total_violations'] = stats.get('total_violations', 0) + pdf_totals.violations
    stats['total_warnings'] = stats.get('total_warnings', 0) + pdf_totals.warnings

    from auto_a11y.web.routes.projects import summarise_pdf_status
    pdf_status_counts = summarise_pdf_status(website_pdfs)
```

The needed imports — `PdfStorage`, `Path`, `get_app_config`, `PdfDocumentStatus` — should already be available; verify with `grep -n "from pathlib\|PdfStorage\|PdfDocumentStatus\|get_app_config" auto_a11y/web/routes/websites.py` and add anything missing in the standard import block at the top of the file.

- [ ] **Step 4: Run the rollup tests**

```bash
pytest tests/pdf/test_pdf_rollup_routes.py::test_websites_route_imports_helper -v
pytest tests/pdf/ -v
```

Expected: the websites-import smoke passes; the projects-import smoke still fails (Task 11); everything else green.

- [ ] **Step 5: Run the typecheckers**

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
```

Expected: zero errors.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/web/routes/websites.py tests/pdf/test_pdf_rollup_routes.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(websites): roll PDF FAIL/WARN counts into website-detail totals

Iterates the existing website_pdfs list (already fetched with
limit=10000 for the PDF nav badge) and parses each AUDITED PDF's
cached issue_map.json via count_issues. Adds the result to the
existing stats['total_violations'] / stats['total_warnings'] keys
that the page-level Mongo aggregation populates.

No new persisted field. The cache JSON remains the single source
of truth — count_issues degrades to (0, 0) on missing or malformed
caches with a WARNING log.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Wire rollup into `projects.py`

**Files:**
- Modify: `auto_a11y/web/routes/projects.py:520-553, 572`

- [ ] **Step 1: Re-run the rollup tests to confirm projects-import still fails**

```bash
source .venv/bin/activate
pytest tests/pdf/test_pdf_rollup_routes.py -v
```

Expected: `test_projects_route_imports_helper` fails; everything else passes.

- [ ] **Step 2: Edit `auto_a11y/web/routes/projects.py`**

`projects.py` already imports `PdfDocument`, `PdfDocumentStatus`, and `get_app_config`. It does NOT have `Path`, `PdfStorage`, or the rollup helper. Add three new imports near the existing PDF-related imports (after line 17 `from auto_a11y.models.pdf_document import …`):

```python
from pathlib import Path

from auto_a11y.pdf.issue_map_counts import PdfIssueCounts, count_issues
from auto_a11y.pdf.storage import PdfStorage
```

In `view_project` at lines 520-553, the per-website loop already produces `website_stats[website.id] = {'violations': ..., 'warnings': ...}` from the test_results aggregation. We extend it to add audited PDFs for each website.

Build a `project_pdfs_by_website` map once per request, *before* the loop, by scoping a single `get_pdf_documents(project_id=..., limit=10000)` call (the same one the existing PDF-badge code makes at line 572) earlier and bucketing it. Then the per-website loop has O(1) access.

Replace the block at lines 522-553 with:

```python
    # PDF rollup prep (2026-05-01 spec Part 2). One DB call for the
    # whole project; reuse the result for both the per-website
    # violation/warning rollup AND the existing PDF nav badge count.
    project_pdfs = get_db().get_pdf_documents(project_id=project_id, limit=10000)
    pdf_count = len(project_pdfs)
    pdf_status_counts = summarise_pdf_status(project_pdfs)
    storage = PdfStorage(base_dir=Path(get_app_config().PDF_STORAGE_DIR))
    pdfs_by_website: dict[str, list[PdfDocument]] = {}
    for pdf_doc in project_pdfs:
        pdfs_by_website.setdefault(pdf_doc.website_id, []).append(pdf_doc)

    # Calculate stats for each website (violations, warnings, and actual page count)
    website_stats: dict[str | None, dict[str, int]] = {}
    for website in websites:
        if not website.id:
            continue
        pages = get_db().get_pages(website.id)
        tested_page_ids = [p.id for p in pages if p.status == PageStatus.TESTED]

        # Aggregate counts from test_results (source of truth for HTML)
        violations = 0
        warnings = 0
        if tested_page_ids:
            agg_pipeline: list[Mapping[str, Any]] = [
                {'$match': {'page_id': {'$in': tested_page_ids}}},
                {'$sort': {'test_date': -1}},
                {'$group': {
                    '_id': '$page_id',
                    'violation_count': {'$first': {'$ifNull': ['$violation_count', 0]}},
                    'warning_count': {'$first': {'$ifNull': ['$warning_count', 0]}},
                }},
            ]
            for result in get_db().test_results.aggregate(agg_pipeline):
                violations += result.get('violation_count', 0)
                warnings += result.get('warning_count', 0)

        # Roll PDFs into the same total — same source of truth as the
        # website-detail page (auto_a11y/web/routes/websites.py).
        pdf_totals = PdfIssueCounts(0, 0)
        for pdf_doc in pdfs_by_website.get(website.id, []):
            if pdf_doc.status is PdfDocumentStatus.AUDITED:
                pdf_totals = pdf_totals + count_issues(pdf_doc, storage)

        website_stats[website.id] = {
            'violations': violations + pdf_totals.violations,
            'warnings': warnings + pdf_totals.warnings,
        }
        # Use actual page count from DB rather than the cached counter
        website.page_count = len(pages)
```

Then locate the original PDF nav badge block at lines 571-574 and **delete** it (the work is now done above):

```python
    # PDF nav badge count (Phase 9.7 — additive)
    project_pdfs = get_db().get_pdf_documents(project_id=project_id, limit=10000)
    pdf_count = len(project_pdfs)
    pdf_status_counts = summarise_pdf_status(project_pdfs)
```

The `pdf_count` and `pdf_status_counts` variables are still defined (now earlier in the function); the `render_template` call already references them by name and continues to work.

Also add the `PdfDocument` and `PdfDocumentStatus` imports if not already present:

```bash
grep -n "PdfDocument\|PdfDocumentStatus" auto_a11y/web/routes/projects.py | head
```

If missing, add to the existing model-import block:

```python
from auto_a11y.models.pdf_document import PdfDocument, PdfDocumentStatus
```

- [ ] **Step 3: Run the rollup tests**

```bash
pytest tests/pdf/test_pdf_rollup_routes.py -v
```

Expected: all tests pass (including the projects-import smoke).

- [ ] **Step 4: Run the typecheckers**

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
```

Expected: zero errors.

- [ ] **Step 5: Run the full PDF suite**

```bash
pytest tests/pdf/ -v
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add auto_a11y/web/routes/projects.py tests/pdf/test_pdf_rollup_routes.py
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
feat(projects): roll PDF FAIL/WARN counts into project-detail totals

Reuses the existing per-project get_pdf_documents() call (one DB
hit for the whole view, bucketed by website_id) to add each AUDITED
PDF's count_issues result to the per-website violations/warnings
totals shown in website_stats.

The duplicate get_pdf_documents() call that previously lived
between the per-website loop and the render_template (just for
the PDF nav badge) is collapsed into the single up-front call.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final integration check

### Task 12: Full-suite green + browser smoke owed-status

- [ ] **Step 1: Run the entire test suite**

```bash
source .venv/bin/activate
pytest tests/ -v --tb=short
```

Expected: green; only known skips are the Mongo-bound ones called out in the project memory; the FR-translation gate stays xfailed.

- [ ] **Step 2: Run the typecheckers**

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
```

Expected: each reports zero errors.

- [ ] **Step 3: Confirm the Fluent translation validator is happy**

```bash
python tests/validate_translations.py
```

Expected: zero missing-EN, zero missing-FR, FR-side still bears the `### TODO_FR ###` header.

- [ ] **Step 4: Manual browser smoke (DEFERRED — owed status note)**

Per the project-memory note "Manual browser verification still owed", this plan does NOT add Playwright coverage of the in-browser scroll/focus/announce behaviour. The owed-status entry stays open until Phase 11 lands a general PDF-surface smoke harness.

When the human reviewer runs the manual smoke, the checklist is:

1. Navigate to a `/pdfs/<id>` page where an audit has produced an `issue_map.json` with mixed FAIL/WARN, including:
   - One issue with both `element_index` and `element_tag`.
   - One document-level issue (page null + bbox null).
   - Two or more issues sharing the same `(check_name, detail)` to exercise group accordion.
   Expected: each card shows `[<index>] <tag>` line OR "Document-level" badge; the multi-element ones collapse into a `<details>` accordion that reads "{N} elements"; clicking opens it; the per-card "Page N" jump button is gone.
2. Click "View in report" on a card.
   Expected: navigates to `/pdfs/<id>/pdfmax-report#check=<...>`; after markdown loads, the matching `<details>` opens, scrolls into view, and the page's existing `[role="status"]` element announces "Showing report section: …".
3. Open a project detail page that has at least one AUDITED PDF with violations and verify the per-website violation/warning numbers include the PDF totals. Repeat for the website detail page.
4. Open a project detail page after a PDF audit has produced an issue_map.json with FAIL/WARN entries — confirm the project-level `total_violations` / `total_warnings` rendered in the dashboard reflect the sum of HTML pages plus PDFs.

- [ ] **Step 5: Update the project-memory file**

Append to `/home/tait/.claude/projects/-home-tait-Documents-cnib-code/memory/pdfmax_auto_a11y_integration.md` a one-line bullet under the relevant section noting the 2026-05-01 plan landed and the manual browser smoke is still owed for the new card layout + scroll-to-anchor.

---

## Notes for the implementer

- **Subagent dispatch (if using subagent-driven-development):** Tasks 1-2 can run as one subagent (Fluent strings); Task 3 is a separate subagent (template rewiring); Tasks 4-7 share enough context to land in one subagent (JS rewrite); Task 8 separate (independent file); Task 9 separate (helper module); Tasks 10-11 share context (rollup wiring) and can run as one subagent. Don't merge the JS rewrite with the helper module — different reviewer mental model.
- **Each subagent must run `source .venv/bin/activate` before any `git commit`.** The pre-commit hook calls `pyright`, which without the venv emits ~1600 spurious "unknown type" errors on existing code and blocks the commit. This is a project-wide gotcha called out in the project memory.
- **No `--no-verify` ever.** If a hook fails, fix the underlying issue.
- **GPG signing is disabled for this branch.** Use `git -c commit.gpgsign=false commit ...` (already shown in every commit step above) — never `--no-gpg-sign` (rejected by hook policy).
- **No new branch.** Land directly on `pdfmax-integration`.
- **Strict type discipline.** No `Any`, no `cast`, no `# type: ignore` of any flavour, no `-> Any` returns. Every commit must pass mypy + pyright + ty (CLAUDE.md non-negotiable).
- **Bilingual translation.** Every user-visible string must be added in both `en/pdf.ftl` and `fr/pdf.ftl`. FR side stays `### TODO_FR ###` placeholder English until a francophone translator lands.
- **No Bootstrap colour classes.** New badge uses `badge-neutral`; severity classes (`badge-high`, `badge-medium`) are reused. The `btn-outline-brand` already exists in `style.css` for the new button.

