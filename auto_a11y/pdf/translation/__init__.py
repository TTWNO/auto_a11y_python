"""Translation layer between the PDF audit engine and the rest of auto_a11y.

The audit engine (``auto_a11y.pdf.audit``) emits :class:`CheckResult`
objects shaped for pdfMax's reporting conventions. The rest of auto_a11y
consumes :class:`auto_a11y.models.test_result.Violation` objects keyed
by stable error IDs and Fluent message IDs.

This package owns that mapping:

* :data:`auto_a11y.pdf.translation.check_mapper.CHECK_CATALOGUE` lists
  every ``(name, result)`` pair the audit engine emits along with its
  stable PdfErr/PdfWarn/PdfInfo ID, touchpoint, and WCAG criteria;
* :func:`auto_a11y.pdf.translation.check_mapper.to_violation` converts
  one CheckResult into a Violation;
* :mod:`auto_a11y.pdf.translation._extract_check_names` is a one-shot
  helper used at development time to refresh the catalogue when new
  checks are added.
"""
