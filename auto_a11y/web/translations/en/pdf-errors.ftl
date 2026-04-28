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
