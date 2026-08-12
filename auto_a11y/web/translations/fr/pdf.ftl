### TODO_FR ###
### This file contains placeholder ENGLISH text for every Fluent ID.
### Each entry MUST be translated by a human francophone before this
### file is considered complete. The Phase 7.4 coverage test (pending)
### will refuse to pass while any English placeholder remains.
###
### Generated: 2026-04-28T13:02:00+00:00
###

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

# List view (Phase 9.4) — TODO_FR placeholder English
pdf-list-filter-aria-label = Filter PDF documents
pdf-list-filter-has-issues = Only show PDFs with issues
pdf-row-actions-aria-label = Actions for this PDF

# Add/upload form (Phase 9.4) — TODO_FR placeholder English
pdf-add-select-website = Choose a website…
pdf-add-website-help = The PDF will be linked to this website for tracking and reporting.
pdf-add-source-legend = Source
pdf-add-source-upload-radio = Upload a PDF from your computer
pdf-add-source-url-radio = Add a PDF by URL
pdf-upload-file-help = Choose a .pdf file (must contain the %PDF- magic bytes).

# Audit progress (Phase 9.4) — TODO_FR placeholder English
pdf-audit-in-progress = Audit in progress
pdf-audit-progress-waiting = Waiting for the next audit stage…

# Result summary (Phase 9.4) — TODO_FR placeholder English
pdf-result-summary-not-audited = This PDF has not been audited yet.
pdf-result-summary-aria-label = Audit summary
pdf-result-summary-info = { $count ->
    [one] { $count } informational note
   *[other] { $count } informational notes
}

# Detail view (Phase 9.4) — TODO_FR placeholder English
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

# Navigation integration (Phase 9.7) — TODO_FR placeholder English
pdf-nav-link = PDFs
pdf-nav-link-count-aria = { $count ->
    [one] { $count } PDF
   *[other] { $count } PDFs
}
pdf-page-is-pdf-badge = → PDF
pdf-page-is-pdf-badge-aria = This page served a PDF; view the PDF document instead

# PDF audit progress counter (project + website detail nav cards)
# TODO_FR — placeholder English text pending human translation.
pdf-nav-audit-progress = { $audited } of { $total } audited
pdf-nav-audit-running = { $count ->
    [one] { $count } running
   *[other] { $count } running
}
pdf-nav-audit-failed = { $count ->
    [one] { $count } failed
   *[other] { $count } failed
}

# Cancel a stuck or stale audit (TODO_FR)
pdf-cancel-button = Cancel audit
pdf-cancel-confirm = Cancel this audit? Any in-progress work will be discarded and the document marked as failed.
pdf-audit-cancelled = Audit cancelled. You can re-trigger the audit when ready.

# Full issue card (pdfMax-style) — TODO_FR placeholder English
pdf-violation-section-what = What this checks
pdf-violation-section-why = Why it matters
pdf-violation-section-who = Who is affected
pdf-violation-section-remediation = How to fix
pdf-violation-section-technical = Technical details
pdf-violation-section-wcag = WCAG criteria
pdf-violation-result-fail = Error
pdf-violation-result-warn = Warning
pdf-violation-result-info = Info

# Filter chips on the audit results pane — TODO_FR placeholder English
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

# Audit report — overview header — TODO_FR placeholder English
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

# Section navigation (TOC) — TODO_FR placeholder English
pdf-report-nav-aria-label = Audit report sections
pdf-report-nav-heading = On this page
pdf-report-nav-overview = Overview
pdf-report-nav-issues = Findings
pdf-report-nav-appendix-wcag = WCAG mapping
pdf-report-nav-appendix-touchpoints = Touchpoint summary

# Touchpoint groups — TODO_FR placeholder English
pdf-report-issues-heading = Findings by touchpoint
pdf-report-issues-aria-label = Audit findings grouped by touchpoint
pdf-report-issues-empty = No findings — all checks passed.
pdf-report-group-count = { $count ->
    [one] { $count } finding
   *[other] { $count } findings
}
pdf-report-group-empty = No findings in this group match the active filters.

# Appendix — WCAG mapping — TODO_FR placeholder English
pdf-report-appendix-wcag-heading = WCAG criteria mapping
pdf-report-appendix-wcag-empty = No WCAG criteria are referenced by the current findings.
pdf-report-appendix-wcag-col-criterion = Criterion
pdf-report-appendix-wcag-col-count = Findings
pdf-report-appendix-wcag-col-issues = Issues

# Appendix — Touchpoint summary — TODO_FR placeholder English
pdf-report-appendix-touchpoints-heading = Touchpoint summary
pdf-report-appendix-touchpoints-col-name = Touchpoint
pdf-report-appendix-touchpoints-col-errors = Errors
pdf-report-appendix-touchpoints-col-warnings = Warnings
pdf-report-appendix-touchpoints-col-info = Info
pdf-report-appendix-touchpoints-col-total = Total

# Per-card jump anchor label — TODO_FR placeholder English
pdf-report-jump-to-issue = Jump to finding

# Font inventory table (per-font size analysis) — TODO_FR placeholder English
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

# Document inventory sections (pdfMax §2-13) — TODO_FR placeholder English
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

# §5 Link inventory — TODO_FR placeholder English
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

# §6 Form field inventory — TODO_FR placeholder English
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

# §12 WCAG mapping — TODO_FR placeholder English
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

# §16 PDF version & structure recommendations — TODO_FR placeholder English
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

# Executive summary (Phase C front matter) — TODO_FR placeholder English
pdf-inventory-exec-summary-heading = At a glance
pdf-inventory-exec-summary-verdict-pass = Ce PDF est conforme pour les { $examined } vérifications qui s'y appliquaient.
pdf-inventory-exec-summary-verdict-warn = No errors detected, but { $warn } warning(s) and { $fail } error(s) should be reviewed.
pdf-inventory-exec-summary-verdict-fail = This PDF does not conform: { $fail } error(s) and { $warn } warning(s) need attention.
pdf-inventory-exec-summary-verdict-not-tested = No automated checks ran for this audit.
pdf-inventory-exec-summary-not-applicable =
    { $count ->
        [one] { $count } autre vérification ne s'appliquait pas à ce document — il n'y avait rien de ce type à examiner.
       *[other] { $count } autres vérifications ne s'appliquaient pas à ce document — il n'y avait rien de ces types à examiner.
    }
pdf-inventory-exec-summary-top-issues = Top issues
pdf-inventory-exec-summary-fail-count = { $count } fail
pdf-inventory-exec-summary-warn-count = { $count } warn

# §14 Visual reading order — TODO_FR placeholder English
pdf-inventory-visual-reading-order-heading = Visual reading order analysis
pdf-inventory-visual-reading-order-intro = Compares the tag-tree reading sequence against text positions on each page. When the structure order differs from the visual order, screen-reader users hear content in a different sequence than sighted readers see.
pdf-inventory-visual-reading-order-col-struct = Tag tree order
pdf-inventory-visual-reading-order-col-visual = Visual order
pdf-inventory-visual-reading-order-no-mismatches = No mismatches
pdf-inventory-visual-reading-order-clean = Tag-tree order matches visual layout for every nearby element pair tested.

# §9 Exported images gallery — TODO_FR placeholder English
pdf-inventory-exported-images-heading = Exported images
pdf-inventory-exported-images-intro = Bitmaps extracted from the PDF, paired with the alt text from their matching Figure or Formula element. Verify each alt text accurately describes what the image communicates.
pdf-inventory-exported-images-no-alt = (no alt text on the matching tag)
pdf-inventory-exported-images-empty = No image XObjects were extracted from this PDF.

# §15 Images of text — TODO_FR placeholder English
pdf-inventory-images-of-text-heading = Images of text analysis
pdf-inventory-images-of-text-intro = WCAG 1.4.5 requires real text instead of images of text wherever possible. Detecting embedded text inside an image requires OCR or AI analysis.
pdf-inventory-images-of-text-ai-required = AI required
pdf-inventory-images-of-text-not-configured = This analysis is not yet configured for this deployment. When AI analysis is enabled, each extracted image will be checked for embedded text content.

# Verbatim pdfMax report viewer — TODO_FR placeholder English
pdfmax-report-link = View pdfMax report
pdfmax-report-link-aria = Open the verbatim pdfMax accessibility report for this document
pdfmax-report-breadcrumb-aria-label = Navigation
pdfmax-report-back-to-detail = Back to PDF detail
pdfmax-report-subtitle = This page renders the unmodified Markdown report produced by pdfMax's pdf_accessibility_audit.py. The HTML matches what you would see in the pdfMax desktop app.
pdfmax-report-loading = Rendering report…
pdfmax-report-error-heading = pdfMax run failed

# Save / export the report — TODO_FR placeholder English
pdf-report-export-aria-label = Save the audit report
pdf-report-export-html = Save as HTML
pdf-report-export-markdown = Save as Markdown
pdf-report-export-saved-html = Report saved as HTML
pdf-report-export-saved-markdown = Report saved as Markdown
pdf-report-export-failed = Failed to save the report
pdf-report-export-footer = Generated by Auto A11y. Automated checks are machine-verifiable; detailed sections provide data for human review.

# In-page PDF viewer (pdf_viewer_app.js + detail.html) — TODO_FR placeholder English
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
pdf-viewer-overlay-cluster-aria-template = {"{count}"} issues at this location
pdf-viewer-semantic-layer-aria-template = Page {"{page}"} content

# Issue-card detail parity (2026-05-01 spec) — TODO_FR placeholder English
pdf-viewer-issue-element = [{"{INDEX}"}] {"{TAG}"}
pdf-viewer-issue-document-level = Document-level
pdf-viewer-issue-group-count = {"{COUNT}"} elements
pdf-viewer-issue-view-in-report = View in report
pdf-viewer-issue-view-in-report-aria = View "{"{CHECK}"}" in the pdfMax report
pdfmax-report-jumped-to-check = Showing report section: {"{CHECK}"}

# Étiquettes des champs de correction — voir auto_a11y/pdf/fix/catalogue.py
pdf-fix-field-title = Titre du document
pdf-fix-field-title-placeholder = p. ex. Rapport annuel 2025
pdf-fix-field-title-help = Laisser vide pour déduire un titre à partir du nom de fichier.
pdf-fix-field-language = Langue
pdf-fix-field-language-help = La langue dans laquelle le document est rédigé. Laisser vide pour la déduire du texte du document.
pdf-fix-field-language-custom = Autre code de langue
pdf-fix-field-language-custom-placeholder = p. ex. pt-BR
pdf-fix-language-english = Anglais
pdf-fix-language-french = Français
pdf-fix-language-spanish = Espagnol
pdf-fix-language-other = Autre…
pdf-fix-field-label-style = Style d'étiquette de liste
pdf-fix-field-label-style-help = Ne choisir une puce ou une numérotation que si c'est bien ce que la page affiche. Une étiquette structurelle ajoute le balisage sans annoncer un symbole que le lecteur ne peut pas voir.
pdf-fix-label-style-structural = Structurelle seulement (aucune étiquette annoncée)
pdf-fix-label-style-bullet = Puce (•)
pdf-fix-label-style-numbered = Numérotée (1. 2. 3.)

# Rapport d'audit natif — voir auto_a11y/pdf/report_markdown.py
pdf-report-title = Rapport d'accessibilité : { $filename }
pdf-report-pages = Pages
pdf-report-version = Version PDF
pdf-report-language = Langue
pdf-report-column-outcome = Résultat
pdf-report-column-count = Vérifications
pdf-report-count-failed = En échec
pdf-report-count-warnings = Avertissements
pdf-report-count-passed = Réussies
pdf-report-count-not-applicable = Sans objet
pdf-report-not-applicable-note =
    { $count ->
        [one] { $count } vérification ne s'appliquait pas à ce document — il n'y avait rien de ce type à examiner, elle est donc comptée séparément des vérifications réussies.
       *[other] { $count } vérifications ne s'appliquaient pas à ce document — il n'y avait rien de ces types à examiner, elles sont donc comptées séparément des vérifications réussies.
    }
pdf-report-section-failures = Échecs
pdf-report-section-warnings = Avertissements
pdf-report-section-info = Pour information
pdf-report-section-passed = Réussies
pdf-report-section-not-applicable = Sans objet
pdfmax-report-not-audited = Ce PDF n'a pas encore été audité. Lancer l'audit pour produire un rapport.
pdfmax-report-no-verdicts = Ce PDF a été audité avant que les rapports ne soient générés dans l'application. Le auditer de nouveau pour en produire un.

# ---------------------------------------------------------------------
# Analyse PDF autonome — le flux propre à pdfMax (PDF → Analyser un
# fichier PDF). Hors projet : ces chaînes concernent une analyse
# ponctuelle, sans site Web, sans projet et sans PdfDocument.
# ---------------------------------------------------------------------

# Menu + écran de sélection de fichier
pdf-scan-menu-item = Analyser un fichier PDF
pdf-scan-page-title = Analyser un fichier PDF
pdf-scan-title = Vérificateur d'accessibilité PDF
pdf-scan-subtitle = { $checks } vérifications automatisées • { $fixes } correctifs automatiques • PDF/UA • WCAG 2.2
pdf-scan-drop-text = Déposez le fichier PDF ici
pdf-scan-drop-or = ou
pdf-scan-select-btn = Sélectionner un fichier PDF
pdf-scan-submit-btn = Analyser ce PDF
pdf-scan-file-help = Le fichier est soumis à toutes les règles automatisées et n'est ajouté à aucun projet.
pdf-scan-recent-heading = Analyses récentes

# État d'analyse
pdf-scan-auditing-title = Analyse du PDF en cours…
pdf-scan-auditing-step = Exécution des vérifications d'accessibilité

# Vue des résultats
pdf-scan-results-page-title = Résultats de l'analyse
pdf-scan-summary-aria-label = Sommaire de l'analyse
pdf-scan-badge-pass = Réussite
pdf-scan-badge-fail = Échec
pdf-scan-badge-warn = Avertissement
pdf-scan-tablist-aria-label = Affichage des résultats
pdf-scan-tab-report = Rapport
pdf-scan-tab-viewer = Visionneuse
pdf-scan-sections-aria-label = Sections du rapport
pdf-scan-sections-heading = Sections
pdf-scan-new-scan = Analyser un autre PDF
pdf-scan-delete = Supprimer
pdf-scan-delete-confirm = Supprimer cette analyse et le fichier stocké ? Cette action est irréversible.
pdf-scan-deleted = Analyse supprimée.
pdf-scan-viewer-unavailable = La visionneuse est accessible une fois l'analyse terminée avec succès.

# Erreurs
pdf-scan-error-no-file = Choisissez un fichier PDF à analyser.
pdf-scan-error-corrupt = Ce fichier n'a pas pu être ouvert en tant que PDF. Il est peut-être corrompu ou chiffré.
pdf-scan-error-failed = L'analyse ne s'est pas terminée. Le fichier est peut-être mal formé.

# Analyse par IA — la case « Inclure l'analyse par l'IA Claude » et la
# section du rapport qu'elle produit. Voir auto_a11y/pdf/audit/ai/.
pdf-scan-ai-toggle = Inclure l'analyse par l'IA Claude
pdf-scan-ai-help = Ajoute une revue sémantique, une évaluation des textes de remplacement, la détection des images de texte et l'analyse de l'usage de la couleur. Utilise l'API Claude : chaque analyse est donc payante et prend plus de temps.
pdf-scan-ai-no-key = L'analyse par IA n'est pas disponible : aucune clé d'API Claude n'est configurée.
pdf-scan-ai-badge = IA

pdf-report-ai-heading = Analyse par IA
pdf-report-ai-model = Analysé par { $model }.
pdf-report-ai-no-findings = L'analyse par IA n'a relevé aucun problème supplémentaire.
pdf-report-ai-severity-high = Critique
pdf-report-ai-severity-medium = Important
pdf-report-ai-severity-low = Recommandation
pdf-report-ai-severity-info = Pour information
pdf-report-ai-page = Page { $page }
pdf-report-ai-element = Élément { $index }
