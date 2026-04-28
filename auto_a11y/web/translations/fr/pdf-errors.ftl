### TODO_FR ###
### This file contains placeholder ENGLISH text for every Fluent ID.
### Each entry MUST be translated by a human francophone before this
### file is considered complete. The Phase 7.4 coverage test (pending)
### will refuse to pass while any English placeholder remains.
###
### Generated: 2026-04-28T13:02:00+00:00
###

pdf-error-corrupt-pdf = This PDF could not be opened. The file may be corrupt or password-protected: { $reason }
pdf-error-not-a-pdf = The uploaded file is not a valid PDF (missing %PDF- magic bytes).
pdf-error-pdf-too-large = This PDF is too large ({ $size } bytes; the limit is { $limit } bytes).
pdf-error-fetch-failed = Could not fetch the PDF from the source URL: { $reason }
pdf-error-ghostscript-missing = Ghostscript is not installed on this server, so contrast analysis cannot run. Reach out to the administrator. Searched paths: { $searched }
pdf-error-pdf-document-not-found = PDF document not found: { $doc_id }
pdf-error-cannot-audit-fetch-failed-document = This PDF document failed to fetch and cannot be audited until it is refetched.
pdf-error-generic = An error occurred while processing the PDF: { $reason }

# Web-route-only errors (Phase 9.3) — TODO_FR placeholder English text
pdf-error-website-required = Please choose a website for this PDF.
pdf-error-website-not-in-project = The selected website does not belong to this project.
pdf-error-pdf-runner-not-configured = The PDF audit subsystem is not configured on this server.
pdf-error-url-fetch-not-implemented = Adding a PDF by URL is not yet implemented.
pdf-error-file-missing-on-disk = The stored PDF file is missing from disk.
pdf-error-invalid-image-name = The requested image name is invalid.
pdf-error-image-missing = The requested image is not available.
