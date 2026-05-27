# Accessibility Statement page
# Message IDs use the `a11y-statement-` prefix.

a11y-statement-page-title = Accessibility Statement
a11y-statement-heading = Accessibility Statement
a11y-statement-intro = The Canadian National Institute for the Blind (CNIB) is committed to ensuring digital accessibility for people with disabilities. Because Auto A11y is itself an accessibility-testing platform, we hold its own interface to the standards it measures. This statement describes the accessibility of Auto A11y, the steps we take to keep it accessible, its known limitations, and how to reach us with feedback.
a11y-statement-last-reviewed-label = Last reviewed
a11y-statement-last-reviewed-date = 27 May 2026

# Conformance status
a11y-statement-conformance-heading = Conformance status
a11y-statement-conformance-standard = Auto A11y aims to conform to the Web Content Accessibility Guidelines (WCAG) 2.2 at Level AA. WCAG defines requirements for designers and developers to improve accessibility for people with disabilities.
a11y-statement-conformance-level = Auto A11y is partially conformant with WCAG 2.2 Level AA. "Partially conformant" means that some parts of the content do not yet fully conform to the accessibility standard; the specific areas are listed under Known limitations below.
a11y-statement-conformance-note = We treat any gap between our interface and WCAG 2.2 AA as a defect, not an acceptable trade-off, and we prioritise fixing it.

# Measures to support accessibility
a11y-statement-measures-heading = Measures we take to support accessibility
a11y-statement-measures-intro = Accessibility is considered in the design phase of every change, not added afterward. Concretely, we:
a11y-statement-measures-tokens = Use a single design-token colour system in which all text meets a contrast ratio of at least 4.5:1 and all non-text user-interface components meet at least 3:1, in both light and dark modes (WCAG 2.2 SC 1.4.3 and 1.4.11).
a11y-statement-measures-contrast-lint = Run an automated contrast and colour-use linter on every commit, which blocks changes that introduce insufficient contrast, prohibited colour utilities, removed focus indicators, or text below 12 pixels.
a11y-statement-measures-semantics = Prefer native HTML elements (buttons, links, form controls, dialogs) over custom widgets, and add ARIA only where no native element fits.
a11y-statement-measures-keyboard = Ensure every interactive element is reachable and operable with the keyboard alone, with a visible focus indicator at all times.
a11y-statement-measures-live-regions = Announce dynamic changes — loading, validation errors, toasts, and items appearing or disappearing — through live regions so they are conveyed to screen-reader users.
a11y-statement-measures-bilingual = Provide the full interface in both English and French.
a11y-statement-measures-reflow = Support reflow and resizing so the layout works at 200% zoom and down to a 320-pixel viewport without loss of content or horizontal scrolling.

# Compatibility
a11y-statement-compatibility-heading = Compatibility with browsers and assistive technology
a11y-statement-compatibility-intro = Auto A11y is designed to be compatible with recent versions of the following:
a11y-statement-compatibility-browsers = Current versions of Firefox, Chrome, Edge, and Safari on desktop.
a11y-statement-compatibility-screenreaders = NVDA and JAWS with Firefox or Chrome on Windows, VoiceOver with Safari on macOS and iOS, and Orca with Firefox on Linux.
a11y-statement-compatibility-note = Auto A11y may not work optimally with browser or assistive-technology versions older than the most recent two major releases.

# Technical specifications
a11y-statement-tech-heading = Technical specifications
a11y-statement-tech-intro = Accessibility of Auto A11y relies on the following technologies to work with your browser and any assistive technologies installed:
a11y-statement-tech-list = HTML, WAI-ARIA, CSS, and JavaScript.
a11y-statement-tech-note = These technologies are relied upon for conformance with WCAG 2.2 Level AA.

# Known limitations
a11y-statement-limitations-heading = Known limitations
a11y-statement-limitations-intro = Despite our efforts, some parts of Auto A11y may have limitations. The following are known issues we are working to resolve:
a11y-statement-limitations-pdf = Generated PDF reports are produced by an external rendering engine and have not yet been audited to the same standard as the web interface. Use the HTML report format for the most accessible output.
a11y-statement-limitations-screenshots = Screenshots of tested pages are visual artefacts of the sites under test; their accessibility reflects the source site, not Auto A11y, and meaningful alternative text for arbitrary captured pages cannot always be generated automatically.
a11y-statement-limitations-tested-content = Reports describe and quote the HTML of the pages you test. That third-party content is reproduced as-is for diagnostic purposes and is not modified to be accessible.
a11y-statement-limitations-manual-testing = Automated checks within the interface, and the test engine itself, do not replace manual screen-reader testing. Some interactive widgets have not yet been verified end-to-end with every supported assistive technology.
a11y-statement-limitations-charts = Some data visualisations and charts convey information primarily visually; we are adding text alternatives and tabular equivalents.

# Assessment
a11y-statement-assessment-heading = How we assess accessibility
a11y-statement-assessment-text = CNIB assessed the accessibility of Auto A11y by self-evaluation, combining the project's own automated contrast and colour linting, code review with accessibility checkpoints, and manual testing with keyboard and screen readers.

# Feedback and contact
a11y-statement-feedback-heading = Feedback and contact
a11y-statement-feedback-intro = We welcome your feedback on the accessibility of Auto A11y. If you encounter a barrier or need information in an alternative format, please let us know:
a11y-statement-feedback-email-label = Email
a11y-statement-feedback-email = accessibility@cnib.ca
a11y-statement-feedback-org-label = Organization
a11y-statement-feedback-org = CNIB Access Labs
a11y-statement-feedback-response = We try to respond to accessibility feedback within five business days.

# Sidebar
a11y-statement-sidebar-heading = At a glance
a11y-statement-sidebar-target = Target standard
a11y-statement-sidebar-target-value = WCAG 2.2 Level AA
a11y-statement-sidebar-status = Status
a11y-statement-sidebar-status-value = Partially conformant
a11y-statement-related-heading = Related pages
