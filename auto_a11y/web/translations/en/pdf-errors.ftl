# PDF user-facing error messages — English
# One Fluent message per exception class in auto_a11y/pdf/errors.py.
# Used to render friendly versions of the underlying PdfError
# subclasses in the web UI and flash messages.

pdf-error-corrupt-pdf = This PDF could not be opened. The file may be corrupt or password-protected: { $reason }
pdf-error-not-a-pdf = The uploaded file is not a valid PDF (missing %PDF- magic bytes).
pdf-error-pdf-too-large = This PDF is too large ({ $size } bytes; the limit is { $limit } bytes).
pdf-error-fetch-failed = Could not fetch the PDF from the source URL: { $reason }
pdf-error-ghostscript-missing = Ghostscript is not installed on this server, so contrast analysis cannot run. Reach out to the administrator. Searched paths: { $searched }
pdf-error-pdf-document-not-found = PDF document not found: { $doc_id }
pdf-error-cannot-audit-fetch-failed-document = This PDF document failed to fetch and cannot be audited until it is refetched.
pdf-error-generic = An error occurred while processing the PDF: { $reason }

# Web-route-only errors (Phase 9.3)
pdf-error-website-required = Please choose a website for this PDF.
pdf-error-website-not-in-project = The selected website does not belong to this project.
pdf-error-pdf-runner-not-configured = The PDF audit subsystem is not configured on this server.
pdf-error-url-fetch-not-implemented = Adding a PDF by URL is not yet implemented.
pdf-error-file-missing-on-disk = The stored PDF file is missing from disk.
pdf-error-invalid-image-name = The requested image name is invalid.
pdf-error-image-missing = The requested image is not available.
