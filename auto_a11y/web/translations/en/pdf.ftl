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
