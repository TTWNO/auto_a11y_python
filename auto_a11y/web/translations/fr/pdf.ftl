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
