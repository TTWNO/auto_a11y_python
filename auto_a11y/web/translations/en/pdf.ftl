# PDF UI strings — English
# Page titles, buttons, form labels, empty states, flash messages,
# breadcrumbs, result-list labels, metadata labels, source-type
# labels for the PDF document feature.

# Page titles
pdf-list-page-title = PDF documents
pdf-detail-page-title = PDF document details
pdf-add-page-title = Add a PDF document

# Buttons
pdf-add-button = Upload PDF
pdf-add-from-url-button = Add from URL
pdf-audit-button = Audit
pdf-reaudit-button = Re-audit
pdf-delete-button = Delete

# Form labels
pdf-upload-file-label = PDF file
pdf-source-url-label = Source URL
pdf-source-url-help = Direct link to the PDF (must be publicly accessible or use a configured WebsiteUser).
pdf-original-filename-label = Original filename

# Empty states
pdf-list-empty = No PDF documents have been added to this website yet.
pdf-list-empty-cta = Upload your first PDF to get started.

# Flash messages
pdf-uploaded-success = PDF uploaded and queued for audit.
pdf-uploaded-duplicate = PDF already exists in this website (deduplicated).
pdf-deleted-success = PDF deleted.
pdf-audit-queued = Audit queued.
pdf-audit-already-running = An audit is already in progress for this PDF.

# Breadcrumb segment
pdf-breadcrumb = PDFs

# Result-list labels
pdf-result-summary-violations = { $count ->
    [one] { $count } violation
   *[other] { $count } violations
}
pdf-result-summary-warnings = { $count ->
    [one] { $count } warning
   *[other] { $count } warnings
}

# PDF metadata labels (detail page)
pdf-meta-page-count = Pages
pdf-meta-pdf-version = PDF version
pdf-meta-declared-language = Declared language
pdf-meta-detected-language = Detected language
pdf-meta-source = Source
pdf-meta-discovered-at = Discovered
pdf-meta-last-audited-at = Last audited
pdf-meta-sha256 = Content hash (SHA-256)
pdf-meta-file-size-bytes = File size

# Source-type labels
pdf-source-uploaded = Uploaded
pdf-source-manual-url = Manual URL
pdf-source-opportunistic = Discovered while testing a page

# List view (Phase 9.4)
pdf-list-filter-aria-label = Filter PDF documents
pdf-list-filter-has-issues = Only show PDFs with issues
pdf-row-actions-aria-label = Actions for this PDF

# Add/upload form (Phase 9.4)
pdf-add-select-website = Choose a website…
pdf-add-website-help = The PDF will be linked to this website for tracking and reporting.
pdf-add-source-legend = Source
pdf-add-source-upload-radio = Upload a PDF from your computer
pdf-add-source-url-radio = Add a PDF by URL
pdf-upload-file-help = Choose a .pdf file (must contain the %PDF- magic bytes).

# Audit progress (Phase 9.4)
pdf-audit-in-progress = Audit in progress
pdf-audit-progress-waiting = Waiting for the next audit stage…

# Result summary (Phase 9.4)
pdf-result-summary-not-audited = This PDF has not been audited yet.
pdf-result-summary-aria-label = Audit summary
pdf-result-summary-info = { $count ->
    [one] { $count } informational note
   *[other] { $count } informational notes
}

# Detail view (Phase 9.4)
pdf-detail-actions-aria-label = PDF document actions
pdf-detail-iframe-title = Embedded PDF viewer
pdf-detail-no-inline-viewer = No inline viewer is available for this PDF.
pdf-detail-metadata-heading = Document metadata
pdf-detail-results-heading = Latest audit results
pdf-detail-violations-aria-label = Violations from the latest audit
pdf-detail-no-violations = No violations were found in the latest audit.
pdf-detail-remediation-summary = How to fix this
pdf-jump-to-page = Jump to page { $page }
pdf-delete-confirm = Are you sure you want to delete this PDF? This cannot be undone.

# Navigation integration (Phase 9.7)
pdf-nav-link = PDFs
pdf-nav-link-count-aria = { $count ->
    [one] { $count } PDF
   *[other] { $count } PDFs
}
pdf-page-is-pdf-badge = → PDF
pdf-page-is-pdf-badge-aria = This page served a PDF; view the PDF document instead

# PDF audit progress counter (project + website detail nav cards)
pdf-nav-audit-progress = { $audited } of { $total } audited
pdf-nav-audit-running = { $count ->
    [one] { $count } running
   *[other] { $count } running
}
pdf-nav-audit-failed = { $count ->
    [one] { $count } failed
   *[other] { $count } failed
}

# Cancel a stuck or stale audit (commit aXXXXXX)
pdf-cancel-button = Cancel audit
pdf-cancel-confirm = Cancel this audit? Any in-progress work will be discarded and the document marked as failed.
pdf-audit-cancelled = Audit cancelled. You can re-trigger the audit when ready.

# Full issue card (pdfMax-style)
pdf-violation-section-what = What this checks
pdf-violation-section-why = Why it matters
pdf-violation-section-who = Who is affected
pdf-violation-section-remediation = How to fix
pdf-violation-section-technical = Technical details
pdf-violation-section-wcag = WCAG criteria
pdf-violation-result-fail = Error
pdf-violation-result-warn = Warning
pdf-violation-result-info = Info

# Filter chips on the audit results pane
pdf-violation-filters-aria-label = Filter audit findings
pdf-violation-filter-level-heading = Severity
pdf-violation-filter-impact-heading = Impact
pdf-violation-filter-touchpoint-heading = Touchpoint
pdf-violation-filter-search-label = Search findings
pdf-violation-filter-search-placeholder = Search by title, description, or rationale…
pdf-violation-filter-all = All
pdf-violation-filter-errors = Errors ({ $count })
pdf-violation-filter-warnings = Warnings ({ $count })
pdf-violation-filter-info = Info ({ $count })
pdf-violation-filter-impact-high = High
pdf-violation-filter-impact-medium = Medium
pdf-violation-filter-impact-low = Low
pdf-violation-filter-empty = No findings match the active filters.

# Audit report — overview header
pdf-report-overview-heading = Audit overview
pdf-report-overview-aria-label = Audit overview
pdf-report-overview-errors = Errors
pdf-report-overview-warnings = Warnings
pdf-report-overview-info = Info
pdf-report-overview-passes = Passes
pdf-report-overview-conformance-heading = Conformance verdict
pdf-report-overview-conformance-fail = Document does not conform to PDF/UA. { $count ->
    [one] { $count } error
   *[other] { $count } errors
} must be resolved before this PDF can be considered accessible.
pdf-report-overview-conformance-warn = Document has no errors but { $count ->
    [one] { $count } warning
   *[other] { $count } warnings
} should be reviewed before publishing.
pdf-report-overview-conformance-pass = No errors or warnings detected. The PDF appears to conform to the audited checks.
pdf-report-overview-audited-at = Last audited
pdf-report-overview-page-count = Pages
pdf-report-overview-pdf-version = PDF version
pdf-report-overview-language = Language

# Section navigation (TOC)
pdf-report-nav-aria-label = Audit report sections
pdf-report-nav-heading = On this page
pdf-report-nav-overview = Overview
pdf-report-nav-issues = Findings
pdf-report-nav-appendix-wcag = WCAG mapping
pdf-report-nav-appendix-touchpoints = Touchpoint summary

# Touchpoint groups
pdf-report-issues-heading = Findings by touchpoint
pdf-report-issues-aria-label = Audit findings grouped by touchpoint
pdf-report-issues-empty = No findings — all checks passed.
pdf-report-group-count = { $count ->
    [one] { $count } finding
   *[other] { $count } findings
}
pdf-report-group-empty = No findings in this group match the active filters.

# Appendix — WCAG mapping
pdf-report-appendix-wcag-heading = WCAG criteria mapping
pdf-report-appendix-wcag-empty = No WCAG criteria are referenced by the current findings.
pdf-report-appendix-wcag-col-criterion = Criterion
pdf-report-appendix-wcag-col-count = Findings
pdf-report-appendix-wcag-col-issues = Issues

# Appendix — Touchpoint summary
pdf-report-appendix-touchpoints-heading = Touchpoint summary
pdf-report-appendix-touchpoints-col-name = Touchpoint
pdf-report-appendix-touchpoints-col-errors = Errors
pdf-report-appendix-touchpoints-col-warnings = Warnings
pdf-report-appendix-touchpoints-col-info = Info
pdf-report-appendix-touchpoints-col-total = Total

# Per-card jump anchor label
pdf-report-jump-to-issue = Jump to finding

# Font inventory table (per-font size analysis on the
# "Font sizes accessible" check)
pdf-violation-font-table-heading = Font inventory
pdf-violation-font-table-thresholds = Recommended minimum body text: { $body }pt. Absolute minimum: { $minimum }pt.
pdf-violation-font-table-col-name = Font face
pdf-violation-font-table-col-size = Smallest size
pdf-violation-font-table-col-chars = Characters
pdf-violation-font-table-col-pct = % of document
pdf-violation-font-table-col-verdict = Verdict
pdf-violation-font-table-verdict-fail = Below minimum
pdf-violation-font-table-verdict-warn = Below recommended
pdf-violation-font-table-verdict-ok = OK
pdf-violation-font-table-total = Total characters

# Document inventory sections (pdfMax §2-13)
pdf-inventory-master-heading = Document inventory
pdf-inventory-master-intro = Per-section data the audit collected from the PDF, intended for human review alongside the automated check results above.
pdf-inventory-empty = No data collected for this section.
pdf-inventory-stale-banner = This audit ran before the document inventory feature shipped. Click "Re-audit" above to populate the new sections (tag tree, heading map, font analysis, WCAG mapping, and the rest).

pdf-inventory-tag-tree-heading = Tag tree
pdf-inventory-tag-tree-intro = The structure tree as read by assistive technology. Every content element should appear here with an appropriate tag.
pdf-inventory-tag-tree-total = { $count ->
    [one] { $count } structure element
   *[other] { $count } structure elements
}
pdf-inventory-tag-tree-col-tag = Tag
pdf-inventory-tag-tree-col-text = Text preview
pdf-inventory-tag-tree-col-alt = Alt
pdf-inventory-tag-tree-col-lang = Lang

pdf-inventory-reading-order-heading = Reading order
pdf-inventory-reading-order-intro = Sequential text in tag-tree order. Compare this against the visual PDF to verify content flows logically.
pdf-inventory-reading-order-col-num = #
pdf-inventory-reading-order-col-tag = Tag
pdf-inventory-reading-order-col-text = Text

pdf-inventory-image-inventory-heading = Image inventory
pdf-inventory-image-inventory-intro = Every Figure and Formula element with its alt text. Look for missing alt text, placeholder text, or alt text that does not match the image.
pdf-inventory-image-inventory-col-tag = Tag
pdf-inventory-image-inventory-col-alt = Alt text
pdf-inventory-image-inventory-col-actual = Actual text
pdf-inventory-image-inventory-col-lang = Lang
pdf-inventory-image-inventory-extracted-heading = Extracted images
pdf-inventory-image-inventory-extracted-col-num = #
pdf-inventory-image-inventory-extracted-col-name = Filename
pdf-inventory-image-inventory-extracted-col-size = Size

pdf-inventory-heading-map-heading = Heading map
pdf-inventory-heading-map-intro = The H1-H6 outline. Screen reader users rely on this skeleton to navigate the document.
pdf-inventory-heading-map-col-level = Level
pdf-inventory-heading-map-col-text = Heading
pdf-inventory-heading-map-gaps-heading = Hierarchy gaps
pdf-inventory-heading-map-gap = Skipped from H{ $from } to H{ $to } at "{ $text }"

pdf-inventory-full-alt-text-heading = Full alt text
pdf-inventory-full-alt-text-intro = Complete alt text for every image element, without truncation. Verify each entry conveys what the image communicates.
pdf-inventory-full-alt-text-col-tag = Tag
pdf-inventory-full-alt-text-col-alt = Alt text
pdf-inventory-full-alt-text-col-actual = Actual text
pdf-inventory-full-alt-text-missing = (no alt text set)

pdf-inventory-color-contrast-heading = Color and contrast
pdf-inventory-color-contrast-intro = Foreground / background color pairs detected in the PDF. WCAG 1.4.3 AA requires 4.5:1 for normal text and 3:1 for large text (≥18pt or ≥14pt bold).
pdf-inventory-color-contrast-col-fg = Foreground
pdf-inventory-color-contrast-col-bg = Background
pdf-inventory-color-contrast-col-ratio = Ratio
pdf-inventory-color-contrast-col-sizes = Sizes (pt)
pdf-inventory-color-contrast-col-count = Chars
pdf-inventory-color-contrast-col-pages = Pages
pdf-inventory-color-contrast-col-sample = Sample

pdf-inventory-language-analysis-heading = Language of parts
pdf-inventory-language-analysis-intro = When a document mixes languages, each foreign-language passage needs a /Lang attribute so screen readers switch pronunciation.
pdf-inventory-language-analysis-declared = Document language: { $lang }
pdf-inventory-language-analysis-declared-missing = Document language: NOT SET
pdf-inventory-language-analysis-spans-heading = Language spans
pdf-inventory-language-analysis-col-lang = Lang
pdf-inventory-language-analysis-col-tag = Tag
pdf-inventory-language-analysis-col-text = Text
pdf-inventory-language-analysis-no-spans = No language-tagged spans found.

pdf-inventory-font-analysis-heading = Font analysis
pdf-inventory-font-analysis-intro = Font choices affect readability for everyone. Look for text below 9pt, insufficient line spacing, and decorative or italic-heavy fonts.
pdf-inventory-font-analysis-inventory-heading = Font inventory
pdf-inventory-font-analysis-col-name = Font face
pdf-inventory-font-analysis-col-size = Size range
pdf-inventory-font-analysis-col-chars = Chars
pdf-inventory-font-analysis-col-pct = % of doc
pdf-inventory-font-analysis-col-pages = Pages
pdf-inventory-font-analysis-col-style = Style
pdf-inventory-font-analysis-col-category = Category
pdf-inventory-font-analysis-style-italic = Italic
pdf-inventory-font-analysis-style-bold = Bold
pdf-inventory-font-analysis-rotations-heading = Text rotation
pdf-inventory-font-analysis-rotations-col-angle = Angle
pdf-inventory-font-analysis-rotations-col-page = Page
pdf-inventory-font-analysis-rotations-col-sample = Sample
pdf-inventory-font-analysis-italic-runs-heading = Italic text usage
pdf-inventory-font-analysis-italic-runs-col-words = Words
pdf-inventory-font-analysis-italic-runs-col-page = Page
pdf-inventory-font-analysis-italic-runs-col-text = Text
pdf-inventory-font-analysis-line-spacings-heading = Line spacing (leading)
pdf-inventory-font-analysis-line-spacings-col-size = Size
pdf-inventory-font-analysis-line-spacings-col-leading = Leading
pdf-inventory-font-analysis-line-spacings-col-ratio = Ratio
pdf-inventory-font-analysis-alignments-heading = Text alignment
pdf-inventory-font-analysis-alignments-col-page = Page
pdf-inventory-font-analysis-alignments-col-alignment = Alignment
pdf-inventory-font-analysis-alignments-col-lines = Lines

# §5 Link inventory
pdf-inventory-link-inventory-heading = Link inventory
pdf-inventory-link-inventory-intro = Links must have meaningful accessible names that indicate their destination — not generic text like "click here" or bare URLs. Each link should make sense out of context.
pdf-inventory-link-inventory-tagged-heading = Tagged links (Link struct elements)
pdf-inventory-link-inventory-annotation-heading = Link annotations
pdf-inventory-link-inventory-col-text = Link text / alt
pdf-inventory-link-inventory-col-url = URL
pdf-inventory-link-inventory-col-lang = Lang
pdf-inventory-link-inventory-col-page = Page
pdf-inventory-link-inventory-col-contents = /Contents
pdf-inventory-link-inventory-no-url = (URL not extracted)

# §6 Form field inventory
pdf-inventory-form-inventory-heading = Form field inventory
pdf-inventory-form-inventory-intro = Interactive form fields must be accessible to keyboard and screen reader users. Every field needs an accessible name (/TU tooltip) so screen readers can announce what the field is for.
pdf-inventory-form-inventory-summary = { $total } field(s) total, { $labeled } with accessible names, { $required } marked required.
pdf-inventory-form-inventory-col-name = Field name (/T)
pdf-inventory-form-inventory-col-type = Type (/FT)
pdf-inventory-form-inventory-col-accessible-name = Accessible name (/TU)
pdf-inventory-form-inventory-col-required = Required
pdf-inventory-form-inventory-col-readonly = Read-only
pdf-inventory-form-inventory-col-value = Value / default
pdf-inventory-form-inventory-missing-tu = Missing
pdf-inventory-form-inventory-yes = Yes
pdf-inventory-form-inventory-no-fields = No interactive form fields found in this document.

# §12 WCAG mapping
pdf-inventory-wcag-mapping-heading = WCAG 2.2 compliance mapping
pdf-inventory-wcag-mapping-intro = Maps the automated check results to specific WCAG 2.2 success criteria. Use this to understand compliance at the standard level and identify which WCAG requirements need remediation.
pdf-inventory-wcag-mapping-col-criterion = Criterion
pdf-inventory-wcag-mapping-col-level = Level
pdf-inventory-wcag-mapping-col-result = Result
pdf-inventory-wcag-mapping-col-checks = Related checks
pdf-inventory-wcag-mapping-verdict-pass = Pass
pdf-inventory-wcag-mapping-verdict-warn = Warning
pdf-inventory-wcag-mapping-verdict-fail = Fail
pdf-inventory-wcag-mapping-verdict-info = Info
pdf-inventory-wcag-mapping-verdict-manual = Manual review
pdf-inventory-wcag-mapping-verdict-not-tested = Not tested

# §16 PDF version & structure recommendations
pdf-inventory-version-recommendations-heading = PDF version and structural recommendations
pdf-inventory-version-recommendations-intro = Different PDF versions support different accessibility features. PDF 2.0 introduced improved structure tags and better support for accessibility metadata.
pdf-inventory-version-recommendations-pdf2 = This document uses PDF { $version }, which supports enhanced accessibility tags (Em, Strong, Aside, Title, FENote, Sub).
pdf-inventory-version-recommendations-pdf2-used = PDF 2.0 tags in use: { $tags }.
pdf-inventory-version-recommendations-pdf2-unused = No PDF 2.0-specific tags are being used. Consider using Em, Strong, and Aside where the document has emphasis, bold text, or sidebar content.
pdf-inventory-version-recommendations-pdf17 = This document uses PDF { $version }.
pdf-inventory-version-recommendations-upgrade = PDF 2.0 (ISO 32000-2) provides significant accessibility improvements over earlier versions, including dedicated tags for emphasis, sidebars, footnotes, and section labels.
pdf-inventory-version-recommendations-sect-heading = Using Sect for document structure
pdf-inventory-version-recommendations-sect-current = Current usage: { $count ->
    [0] No Sect elements found. The document relies entirely on headings for structural navigation.
    [one] { $count } Sect element found.
   *[other] { $count } Sect elements found.
}
pdf-inventory-version-recommendations-sect-suggested = Based on the document's content regions, the following top-level Sect structure is recommended (each Sect should carry a /Title attribute):

# Executive summary (Phase C front matter)
pdf-inventory-exec-summary-heading = At a glance
pdf-inventory-exec-summary-verdict-pass = This PDF conforms on all { $examined } checks that applied to it.
pdf-inventory-exec-summary-verdict-warn = No errors detected, but { $warn } warning(s) and { $fail } error(s) should be reviewed.
pdf-inventory-exec-summary-verdict-fail = This PDF does not conform: { $fail } error(s) and { $warn } warning(s) need attention.
pdf-inventory-exec-summary-verdict-not-tested = No automated checks ran for this audit.
pdf-inventory-exec-summary-not-applicable =
    { $count ->
        [one] { $count } further check did not apply to this document — it had nothing of that kind to examine.
       *[other] { $count } further checks did not apply to this document — there was nothing of those kinds to examine.
    }
pdf-inventory-exec-summary-top-issues = Top issues
pdf-inventory-exec-summary-fail-count = { $count } fail
pdf-inventory-exec-summary-warn-count = { $count } warn

# §14 Visual reading order
pdf-inventory-visual-reading-order-heading = Visual reading order analysis
pdf-inventory-visual-reading-order-intro = Compares the tag-tree reading sequence against text positions on each page. When the structure order differs from the visual order, screen-reader users hear content in a different sequence than sighted readers see.
pdf-inventory-visual-reading-order-col-struct = Tag tree order
pdf-inventory-visual-reading-order-col-visual = Visual order
pdf-inventory-visual-reading-order-no-mismatches = No mismatches
pdf-inventory-visual-reading-order-clean = Tag-tree order matches visual layout for every nearby element pair tested.

# §9 Exported images gallery
pdf-inventory-exported-images-heading = Exported images
pdf-inventory-exported-images-intro = Bitmaps extracted from the PDF, paired with the alt text from their matching Figure or Formula element. Verify each alt text accurately describes what the image communicates.
pdf-inventory-exported-images-no-alt = (no alt text on the matching tag)
pdf-inventory-exported-images-empty = No image XObjects were extracted from this PDF.

# §15 Images of text
pdf-inventory-images-of-text-heading = Images of text analysis
pdf-inventory-images-of-text-intro = WCAG 1.4.5 requires real text instead of images of text wherever possible. Detecting embedded text inside an image requires OCR or AI analysis.
pdf-inventory-images-of-text-ai-required = AI required
pdf-inventory-images-of-text-not-configured = This analysis is not yet configured for this deployment. When AI analysis is enabled, each extracted image will be checked for embedded text content.

# Verbatim pdfMax report viewer (calls pdf_accessibility_audit.py via subprocess)
pdfmax-report-link = View pdfMax report
pdfmax-report-link-aria = Open the verbatim pdfMax accessibility report for this document
pdfmax-report-breadcrumb-aria-label = Navigation
pdfmax-report-back-to-detail = Back to PDF detail
pdfmax-report-subtitle = This page renders the unmodified Markdown report produced by pdfMax's pdf_accessibility_audit.py. The HTML matches what you would see in the pdfMax desktop app.
pdfmax-report-loading = Rendering report…
pdfmax-report-error-heading = pdfMax run failed

# Save / export the report
pdf-report-export-aria-label = Save the audit report
pdf-report-export-html = Save as HTML
pdf-report-export-markdown = Save as Markdown
pdf-report-export-saved-html = Report saved as HTML
pdf-report-export-saved-markdown = Report saved as Markdown
pdf-report-export-failed = Failed to save the report
pdf-report-export-footer = Generated by Auto A11y. Automated checks are machine-verifiable; detailed sections provide data for human review.

# In-page PDF viewer (pdf_viewer_app.js + detail.html)
# Replaces the previous Chromium-iframe view; renders with PDF.js,
# overlays issue bboxes from the cached pdfMax issue_map.json, draws
# SVG connector lines between page overlays and issue cards, and emits
# a visually-hidden semantic HTML tree for screen-reader navigation.
pdf-viewer-loading = Loading PDF…
pdf-viewer-error-load = Failed to load the PDF.
pdf-viewer-toolbar-aria-label = PDF viewer controls
pdf-viewer-prev-page = Previous page
pdf-viewer-next-page = Next page
pdf-viewer-page-input-label = Page number
pdf-viewer-zoom-in = Zoom in
pdf-viewer-zoom-out = Zoom out
pdf-viewer-zoom-reset = Reset zoom
pdf-viewer-overlay-layer-aria-label = Issue locations on this page
pdf-viewer-issues-heading = Issues found by the visual audit
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

# Fix input labels — see auto_a11y/pdf/fix/catalogue.py
pdf-fix-field-title = Document title
pdf-fix-field-title-placeholder = e.g. Annual Report 2025
pdf-fix-field-title-help = Leave blank to derive a title from the filename.
pdf-fix-field-language = Language
pdf-fix-field-language-help = The language the document is written in. Leave unset to derive it from the document's own text.
pdf-fix-field-language-custom = Other language code
pdf-fix-field-language-custom-placeholder = e.g. pt-BR
pdf-fix-language-english = English
pdf-fix-language-french = French
pdf-fix-language-spanish = Spanish
pdf-fix-language-other = Other…
pdf-fix-field-label-style = List label style
pdf-fix-field-label-style-help = Only choose a bullet or numbering if that is what the page actually shows. A structural label adds the markup without claiming a symbol the reader cannot see.
pdf-fix-label-style-structural = Structural only (no announced label)
pdf-fix-label-style-bullet = Bullet (•)
pdf-fix-label-style-numbered = Numbered (1. 2. 3.)

# Native audit report — see auto_a11y/pdf/report_markdown.py
pdf-report-title = Accessibility report: { $filename }
pdf-report-pages = Pages
pdf-report-version = PDF version
pdf-report-language = Language
pdf-report-column-outcome = Outcome
pdf-report-column-count = Checks
pdf-report-count-failed = Failed
pdf-report-count-warnings = Warnings
pdf-report-count-passed = Passed
pdf-report-count-not-applicable = Not applicable
pdf-report-not-applicable-note =
    { $count ->
        [one] { $count } check did not apply to this document — it had nothing of that kind to examine, so it is counted separately from the checks that passed.
       *[other] { $count } checks did not apply to this document — there was nothing of those kinds to examine, so they are counted separately from the checks that passed.
    }
pdf-report-section-failures = Failures
pdf-report-section-warnings = Warnings
pdf-report-section-info = For information
pdf-report-section-passed = Passed
pdf-report-section-not-applicable = Not applicable
pdfmax-report-not-audited = This PDF has not been audited yet. Run the audit to produce a report.
pdfmax-report-no-verdicts = This PDF was audited before reports were generated in-app. Re-audit it to produce one.

# ---------------------------------------------------------------------
# Standalone PDF scan — pdfMax's own flow (PDFs → Scan a PDF file).
# Not project-scoped: these strings belong to a one-off scan that has no
# website, project or PdfDocument behind it.
# ---------------------------------------------------------------------

# Menu + file-select screen
pdf-scan-menu-item = Scan a PDF file
pdf-scan-page-title = Scan a PDF file
pdf-scan-title = PDF Accessibility Checker
pdf-scan-subtitle = { $checks } automated checks • { $fixes } auto-fixes • PDF/UA • WCAG 2.2
pdf-scan-drop-text = Drop PDF file here
pdf-scan-drop-or = or
pdf-scan-select-btn = Select PDF File
pdf-scan-submit-btn = Scan this PDF
pdf-scan-file-help = The file is checked against every automated rule and is not added to any project.
pdf-scan-recent-heading = Recent scans

# Auditing state
pdf-scan-auditing-title = Analyzing PDF…
pdf-scan-auditing-step = Running accessibility checks

# Results view
pdf-scan-results-page-title = Scan results
pdf-scan-summary-aria-label = Scan summary
pdf-scan-badge-pass = Pass
pdf-scan-badge-fail = Fail
pdf-scan-badge-warn = Warn
pdf-scan-tablist-aria-label = Results view
pdf-scan-tab-report = Report
pdf-scan-tab-viewer = Viewer
pdf-scan-sections-aria-label = Report sections
pdf-scan-sections-heading = Sections
pdf-scan-new-scan = Scan another PDF
pdf-scan-delete = Delete
pdf-scan-delete-confirm = Delete this scan and its stored file? This cannot be undone.
pdf-scan-deleted = Scan deleted.
pdf-scan-viewer-unavailable = The viewer is available once the scan has completed successfully.

# Errors
pdf-scan-error-no-file = Choose a PDF file to scan.
pdf-scan-error-corrupt = This file could not be opened as a PDF. It may be corrupt or encrypted.
pdf-scan-error-failed = The scan did not finish. The file may be malformed.
