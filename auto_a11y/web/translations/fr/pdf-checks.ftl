### TODO_FR ###
### This file contains placeholder ENGLISH text for every Fluent ID.
### Each entry MUST be translated by a human francophone before this
### file is considered complete. The Phase 7.4 coverage test (pending)
### will refuse to pass while any English placeholder remains.
###
### Generated: 2026-04-28T20:53:01+00:00
###

pdf-check-PdfErrDocumentTitleNotSet-name =
    Document title set

pdf-check-PdfErrDocumentTitleNotSet-short-title =
    Document title set

pdf-check-PdfErrDocumentTitleNotSet-what =
    Screen readers announce the document title when a user opens a PDF.

pdf-check-PdfErrDocumentTitleNotSet-why =
    Without a meaningful title, users hear the filename (e.g., 'scan_2025_v3.pdf'), which gives no indication of the document's content or purpose. The title also appears in browser tabs and task switchers, helping all users orient themselves.

pdf-check-PdfErrDocumentTitleNotSet-who =
    All users navigating the document.

pdf-check-PdfWarnDocumentTitleNotDisplayed-name =
    Document title set (warning)

pdf-check-PdfWarnDocumentTitleNotDisplayed-short-title =
    Document title set

pdf-check-PdfWarnDocumentTitleNotDisplayed-what =
    Screen readers announce the document title when a user opens a PDF.

pdf-check-PdfWarnDocumentTitleNotDisplayed-why =
    Without a meaningful title, users hear the filename (e.g., 'scan_2025_v3.pdf'), which gives no indication of the document's content or purpose. The title also appears in browser tabs and task switchers, helping all users orient themselves.

pdf-check-PdfWarnDocumentTitleNotDisplayed-who =
    All users navigating the document.

pdf-check-PdfErrPdfNotTagged-name =
    PDF is tagged

pdf-check-PdfErrPdfNotTagged-short-title =
    PDF is tagged

pdf-check-PdfErrPdfNotTagged-what =
    Tags are the foundation of PDF accessibility.

pdf-check-PdfErrPdfNotTagged-why =
    Without tags, a PDF is just a visual layout — screen readers cannot determine what is a heading, paragraph, image, or list. An untagged PDF is essentially inaccessible to assistive technology users.

pdf-check-PdfErrPdfNotTagged-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfWarnSuspectTags-name =
    No suspect tags (warning)

pdf-check-PdfWarnSuspectTags-short-title =
    No suspect tags

pdf-check-PdfWarnSuspectTags-what =
    When a PDF's tags are auto-generated (e.g., by OCR or automatic tagging), the /Suspects flag warns that tags may be inaccurate.

pdf-check-PdfWarnSuspectTags-why =
    Assistive technology may treat the document with reduced confidence, and some validators flag it as non-conformant.

pdf-check-PdfWarnSuspectTags-who =
    Users of assistive technology.

pdf-check-PdfWarnNoPdfUaIdentifier-name =
    PDF/UA identifier (warning)

pdf-check-PdfWarnNoPdfUaIdentifier-short-title =
    PDF/UA identifier

pdf-check-PdfWarnNoPdfUaIdentifier-what =
    The PDF/UA identifier in XMP metadata signals to assistive technology and validators that this document claims conformance to the PDF/UA standard.

pdf-check-PdfWarnNoPdfUaIdentifier-why =
    Without it, tools may not apply PDF/UA-specific processing or may report the document as non-conformant.

pdf-check-PdfWarnNoPdfUaIdentifier-who =
    Users of assistive technology.

pdf-check-PdfErrNoXmpMetadataStream-name =
    XMP metadata stream present

pdf-check-PdfErrNoXmpMetadataStream-short-title =
    XMP metadata stream present

pdf-check-PdfErrNoXmpMetadataStream-what =
    XMP metadata is the standard container for document metadata in modern PDFs.

pdf-check-PdfErrNoXmpMetadataStream-why =
    It carries the PDF/UA identifier, document title (dc:title), author information, and other properties that assistive technology and PDF validators depend on. Without a /Metadata stream, the document cannot declare PDF/UA conformance.

pdf-check-PdfErrNoXmpMetadataStream-who =
    Users of assistive technology.

pdf-check-PdfErrXmpDcTitleMissing-name =
    XMP dc:title present

pdf-check-PdfErrXmpDcTitleMissing-short-title =
    XMP dc:title present

pdf-check-PdfErrXmpDcTitleMissing-what =
    The dc:title property in XMP metadata is the authoritative document title for PDF/UA validators and assistive technology.

pdf-check-PdfErrXmpDcTitleMissing-why =
    While the /Info/Title field provides a basic title, dc:title in XMP is the standard location that PDF/UA requires.

pdf-check-PdfErrXmpDcTitleMissing-who =
    Users of assistive technology.

pdf-check-PdfWarnMetadataIncomplete-name =
    Metadata completeness (warning)

pdf-check-PdfWarnMetadataIncomplete-short-title =
    Metadata completeness

pdf-check-PdfWarnMetadataIncomplete-what =
    Complete metadata helps users identify the document, its author, and its purpose.

pdf-check-PdfWarnMetadataIncomplete-why =
    Search engines, document management systems, and assistive technology all use metadata to present and catalog documents.

pdf-check-PdfWarnMetadataIncomplete-who =
    Users of assistive technology.

pdf-check-PdfErrPageLabelsInconsistent-name =
    Page labels consistent

pdf-check-PdfErrPageLabelsInconsistent-short-title =
    Page labels consistent

pdf-check-PdfErrPageLabelsInconsistent-what =
    Page labels define the logical page numbering shown to users (e.g., i, ii, 1, 2…).

pdf-check-PdfErrPageLabelsInconsistent-why =
    If /PageLabels exists but is misconfigured, screen readers and the viewer UI announce wrong page numbers, disorienting users.

pdf-check-PdfErrPageLabelsInconsistent-who =
    Users of assistive technology.

pdf-check-PdfErrPdfVersionMismatch-name =
    PDF header and catalog version consistent

pdf-check-PdfErrPdfVersionMismatch-short-title =
    PDF header and catalog version consistent

pdf-check-PdfErrPdfVersionMismatch-what =
    TODO_AUTHOR: pdfMax has no remediation entry for 'PDF header and catalog version consistent' (stable id: PdfErrPdfVersionMismatch); fill in by hand.

pdf-check-PdfErrPdfVersionMismatch-why =
    TODO_AUTHOR: pdfMax has no remediation entry for 'PDF header and catalog version consistent' (stable id: PdfErrPdfVersionMismatch); fill in by hand.

pdf-check-PdfErrPdfVersionMismatch-who =
    Users of assistive technology.

pdf-check-PdfWarnPdf20TagsRemapped-name =
    New PDF 2.0 structure elements used correctly (warning)

pdf-check-PdfWarnPdf20TagsRemapped-short-title =
    New PDF 2.0 structure elements used correctly

pdf-check-PdfWarnPdf20TagsRemapped-what =
    PDF 2.0 introduced semantic elements like Em (emphasis), Strong, Aside, FENote, and Title.

pdf-check-PdfWarnPdf20TagsRemapped-why =
    If these are remapped via /RoleMap to older tags (e.g., Em→Span), their semantic meaning is lost and assistive technology cannot convey the intended emphasis.

pdf-check-PdfWarnPdf20TagsRemapped-who =
    Users of assistive technology.

pdf-check-PdfWarnPdfUa2DeclarationsIncomplete-name =
    PDF/UA-2 accessibility declarations in XMP (warning)

pdf-check-PdfWarnPdfUa2DeclarationsIncomplete-short-title =
    PDF/UA-2 accessibility declarations in XMP

pdf-check-PdfWarnPdfUa2DeclarationsIncomplete-what =
    The PDF/UA-2 identifier in XMP metadata declares that the document conforms to ISO 14289-2.

pdf-check-PdfWarnPdfUa2DeclarationsIncomplete-why =
    Without this declaration, PDF viewers and validators cannot determine that the document is intended to be accessible, and may not apply accessibility features or validation rules.

pdf-check-PdfWarnPdfUa2DeclarationsIncomplete-who =
    Users of assistive technology.

pdf-check-PdfErrPdf20NamespaceMissing-name =
    PDF 2.0 namespace in structure tree

pdf-check-PdfErrPdf20NamespaceMissing-short-title =
    PDF 2.0 namespace in structure tree

pdf-check-PdfErrPdf20NamespaceMissing-what =
    PDF 2.0 introduced a formal namespace system for structure elements.

pdf-check-PdfErrPdf20NamespaceMissing-why =
    Without the correct namespace declarations, PDF 2.0 viewers may not recognize standard tags, causing the entire structure tree to be misinterpreted or ignored by assistive technology.

pdf-check-PdfErrPdf20NamespaceMissing-who =
    Users of assistive technology.

pdf-check-PdfErrPdfUa2RequiresPdf20-name =
    PDF/UA-2 requires PDF 2.0

pdf-check-PdfErrPdfUa2RequiresPdf20-short-title =
    PDF/UA-2 requires PDF 2.0

pdf-check-PdfErrPdfUa2RequiresPdf20-what =
    PDF/UA-2 (ISO 14289-2) is built on PDF 2.0 (ISO 32000-2).

pdf-check-PdfErrPdfUa2RequiresPdf20-why =
    A document claiming PDF/UA-2 conformance but using an older PDF version (1.4–1.7) cannot meet the specification's requirements, as many PDF/UA-2 features depend on PDF 2.0 capabilities.

pdf-check-PdfErrPdfUa2RequiresPdf20-who =
    Users of assistive technology.

pdf-check-PdfErrStructureTreeMissing-name =
    Structure tree exists

pdf-check-PdfErrStructureTreeMissing-short-title =
    Structure tree exists

pdf-check-PdfErrStructureTreeMissing-what =
    The structure tree is the backbone of PDF accessibility — it defines the logical hierarchy of the document (headings, paragraphs, lists, tables, images).

pdf-check-PdfErrStructureTreeMissing-why =
    Without it, assistive technology cannot present the document in a meaningful way.

pdf-check-PdfErrStructureTreeMissing-who =
    Users of assistive technology.

pdf-check-PdfErrRoleMapInvalid-name =
    Role mapping valid

pdf-check-PdfErrRoleMapInvalid-short-title =
    Role mapping valid

pdf-check-PdfErrRoleMapInvalid-what =
    Custom tag names (like 'BodyText' or 'CompanyName') are meaningless to screen readers unless they map to standard PDF tags.

pdf-check-PdfErrRoleMapInvalid-why =
    Invalid role mappings cause assistive technology to treat content as generic containers, losing all semantic meaning.

pdf-check-PdfErrRoleMapInvalid-who =
    Users of assistive technology.

pdf-check-PdfErrRoleMapCircular-name =
    No circular role mappings

pdf-check-PdfErrRoleMapCircular-short-title =
    No circular role mappings

pdf-check-PdfErrRoleMapCircular-what =
    Circular role mappings (e.g., /CustomA maps to /CustomB which maps back to /CustomA) create infinite loops when assistive technology tries to resolve a tag's role.

pdf-check-PdfErrRoleMapCircular-why =
    This makes the affected tags completely unresolvable, so screen readers cannot determine what type of content the element represents.

pdf-check-PdfErrRoleMapCircular-who =
    Users of assistive technology.

pdf-check-PdfErrStandardTagsRemapped-name =
    Standard tags not remapped

pdf-check-PdfErrStandardTagsRemapped-short-title =
    Standard tags not remapped

pdf-check-PdfErrStandardTagsRemapped-what =
    Standard PDF tags like H1, P, Table, and Span have universal meanings that assistive technology relies on.

pdf-check-PdfErrStandardTagsRemapped-why =
    If a standard tag appears as a key in /RoleMap (e.g., /H1 remapped to /P), its meaning is overridden, causing screen readers to misinterpret the content. For example, remapping H1 to P turns all first-level headings into plain paragraphs.

pdf-check-PdfErrStandardTagsRemapped-who =
    Users of assistive technology.

pdf-check-PdfErrTabOrderNotStructure-name =
    Tab order follows structure

pdf-check-PdfErrTabOrderNotStructure-short-title =
    Tab order follows structure

pdf-check-PdfErrTabOrderNotStructure-what =
    When tab order does not follow the document structure, keyboard users encounter interactive elements (links, form fields) in an unpredictable sequence.

pdf-check-PdfErrTabOrderNotStructure-why =
    A link that visually appears at the top of the page might receive focus last, causing confusion and disorientation.

pdf-check-PdfErrTabOrderNotStructure-who =
    Users with motor disabilities.

pdf-check-PdfErrTocStructureInvalid-name =
    TOC structure valid

pdf-check-PdfErrTocStructureInvalid-short-title =
    TOC structure valid

pdf-check-PdfErrTocStructureInvalid-what =
    A Table of Contents (TOC) in the tag structure enables screen reader users to navigate the document by section.

pdf-check-PdfErrTocStructureInvalid-why =
    An invalid TOC structure — with incorrect nesting, missing TOCI (TOC Item) children, or wrong tag types — prevents assistive technology from presenting the table of contents as a navigable list.

pdf-check-PdfErrTocStructureInvalid-who =
    Users of assistive technology.

pdf-check-PdfErrRubyStructureInvalid-name =
    Ruby structure valid

pdf-check-PdfErrRubyStructureInvalid-short-title =
    Ruby structure valid

pdf-check-PdfErrRubyStructureInvalid-what =
    Ruby annotations are used in CJK (Chinese, Japanese, Korean) text to show pronunciation guides (e.g., furigana in Japanese) above or beside base characters.

pdf-check-PdfErrRubyStructureInvalid-why =
    An invalid Ruby structure means screen readers cannot associate the pronunciation guide with the base text, losing essential reading assistance for CJK users.

pdf-check-PdfErrRubyStructureInvalid-who =
    Users of assistive technology.

pdf-check-PdfErrWarichuStructureInvalid-name =
    Warichu structure valid

pdf-check-PdfErrWarichuStructureInvalid-short-title =
    Warichu structure valid

pdf-check-PdfErrWarichuStructureInvalid-what =
    Warichu is a Japanese typographic convention where a small annotation is split across two half-height lines within the main text flow.

pdf-check-PdfErrWarichuStructureInvalid-why =
    An invalid Warichu structure means screen readers cannot properly read the inline annotation, potentially presenting text in the wrong order or omitting it entirely.

pdf-check-PdfErrWarichuStructureInvalid-who =
    Users of assistive technology.

pdf-check-PdfErrNoteIdsNotUnique-name =
    Note tags have unique IDs

pdf-check-PdfErrNoteIdsNotUnique-short-title =
    Note tags have unique IDs

pdf-check-PdfErrNoteIdsNotUnique-what =
    Footnotes and endnotes tagged as Note elements need unique /ID attributes so that reference links (e.g., superscript numbers) can link to the correct note.

pdf-check-PdfErrNoteIdsNotUnique-why =
    Without unique IDs, footnote navigation is broken.

pdf-check-PdfErrNoteIdsNotUnique-who =
    Users of assistive technology.

pdf-check-PdfErrFormulaMissingAlt-name =
    Formula elements have alt text or ActualText

pdf-check-PdfErrFormulaMissingAlt-short-title =
    Formula elements have alt text or ActualText

pdf-check-PdfErrFormulaMissingAlt-what =
    Mathematical formulas rendered as images or special fonts are meaningless to screen readers without a text alternative.

pdf-check-PdfErrFormulaMissingAlt-why =
    Users who cannot see the formula need a textual description or the actual mathematical expression.

pdf-check-PdfErrFormulaMissingAlt-who =
    Users of assistive technology.

pdf-check-PdfWarnFormulaWithoutMathMl-name =
    MathML associated with Formula elements (warning)

pdf-check-PdfWarnFormulaWithoutMathMl-short-title =
    MathML associated with Formula elements

pdf-check-PdfWarnFormulaWithoutMathMl-what =
    Formula elements represent mathematical expressions.

pdf-check-PdfWarnFormulaWithoutMathMl-why =
    Without an associated MathML representation (via /AF associated files or inline markup), assistive technology can only read the alt text, losing the mathematical structure and meaning.

pdf-check-PdfWarnFormulaWithoutMathMl-who =
    Users of assistive technology.

pdf-check-PdfWarnAssociatedFilesMissing-name =
    Associated Files property on embedded content (warning)

pdf-check-PdfWarnAssociatedFilesMissing-short-title =
    Associated Files property on embedded content

pdf-check-PdfWarnAssociatedFilesMissing-what =
    PDF 2.0 introduced the /AF (Associated Files) mechanism to formally associate supplementary files with document elements.

pdf-check-PdfWarnAssociatedFilesMissing-why =
    Without /AF, embedded files, alternate representations (e.g., HTML, MathML), and source data are undiscoverable by assistive technology and validators.

pdf-check-PdfWarnAssociatedFilesMissing-who =
    Users of assistive technology.

pdf-check-PdfErrOptionalContentGroupMissingName-name =
    Optional content groups have Name

pdf-check-PdfErrOptionalContentGroupMissingName-short-title =
    Optional content groups have Name

pdf-check-PdfErrOptionalContentGroupMissingName-what =
    Optional content groups (layers) without a /Name attribute cannot be identified by assistive technology or the viewer UI.

pdf-check-PdfErrOptionalContentGroupMissingName-why =
    Users cannot discover, toggle, or navigate unnamed layers.

pdf-check-PdfErrOptionalContentGroupMissingName-who =
    Users of assistive technology.

pdf-check-PdfErrOptionalContentHasAsEntry-name =
    Optional content has no AS entry

pdf-check-PdfErrOptionalContentHasAsEntry-short-title =
    Optional content has no AS entry

pdf-check-PdfErrOptionalContentHasAsEntry-what =
    The /AS (auto-state) entry in optional content configuration allows layers to change visibility automatically based on zoom level or other conditions.

pdf-check-PdfErrOptionalContentHasAsEntry-why =
    This can hide content without the user's knowledge, making the document unreliable for AT.

pdf-check-PdfErrOptionalContentHasAsEntry-who =
    Users of assistive technology.

pdf-check-PdfErrEmbeddedFileMissingFKeys-name =
    Embedded files have F and UF keys

pdf-check-PdfErrEmbeddedFileMissingFKeys-short-title =
    Embedded files have F and UF keys

pdf-check-PdfErrEmbeddedFileMissingFKeys-what =
    Embedded file specifications must have both /F (file name) and /UF (Unicode file name) keys so assistive technology can identify and present the attachment to users.

pdf-check-PdfErrEmbeddedFileMissingFKeys-why =
    Without these keys, embedded files are effectively unnamed.

pdf-check-PdfErrEmbeddedFileMissingFKeys-who =
    Users of assistive technology.

pdf-check-PdfErrReferenceXObjectsPresent-name =
    No Reference XObjects

pdf-check-PdfErrReferenceXObjectsPresent-short-title =
    No Reference XObjects

pdf-check-PdfErrReferenceXObjectsPresent-what =
    Reference XObjects point to content in external PDF files.

pdf-check-PdfErrReferenceXObjectsPresent-why =
    They are prohibited by PDF/UA because the document is not self-contained — the external content may be unavailable, making the document incomplete for assistive technology.

pdf-check-PdfErrReferenceXObjectsPresent-who =
    Users of assistive technology.

pdf-check-PdfErrFormXObjectMcidReused-name =
    Form XObjects with MCIDs not reused

pdf-check-PdfErrFormXObjectMcidReused-short-title =
    Form XObjects with MCIDs not reused

pdf-check-PdfErrFormXObjectMcidReused-what =
    When a Form XObject containing marked content IDs (MCIDs) is used on multiple pages, the same MCID appears in multiple page content streams.

pdf-check-PdfErrFormXObjectMcidReused-why =
    This creates conflicting references in the structure tree, making the tag structure ambiguous.

pdf-check-PdfErrFormXObjectMcidReused-who =
    Users of assistive technology.

pdf-check-PdfWarnNonStructureDestinations-name =
    Structure destinations for intra-document links (warning)

pdf-check-PdfWarnNonStructureDestinations-short-title =
    Structure destinations for intra-document links

pdf-check-PdfWarnNonStructureDestinations-what =
    Intra-document links (GoTo actions) navigate to page locations.

pdf-check-PdfWarnNonStructureDestinations-why =
    Structure destinations (/SD) additionally identify the target structure element, allowing assistive technology to navigate directly to the tagged content rather than just a page coordinate.

pdf-check-PdfWarnNonStructureDestinations-who =
    Users of assistive technology.

pdf-check-PdfWarnPdfUa2HeadingHierarchy-name =
    PDF/UA-2 heading hierarchy (warning)

pdf-check-PdfWarnPdfUa2HeadingHierarchy-short-title =
    PDF/UA-2 heading hierarchy

pdf-check-PdfWarnPdfUa2HeadingHierarchy-what =
    PDF/UA-2 requires numbered headings (H1–H6) rather than the generic H tag.

pdf-check-PdfWarnPdfUa2HeadingHierarchy-why =
    Generic H tags rely on a /headinglevel attribute that many assistive technologies ignore, leaving users without a navigable heading structure.

pdf-check-PdfWarnPdfUa2HeadingHierarchy-who =
    Users of assistive technology.

pdf-check-PdfErrReadingOrderMismatch-name =
    Reading order matches visual layout

pdf-check-PdfErrReadingOrderMismatch-short-title =
    Reading order matches visual layout

pdf-check-PdfErrReadingOrderMismatch-what =
    If the structure tree order doesn't match the visual layout, screen reader users hear content in a different sequence than sighted users see it.

pdf-check-PdfErrReadingOrderMismatch-why =
    For example, a footer might be read before the main content, or a right column before a left column, creating confusion about the document's flow.

pdf-check-PdfErrReadingOrderMismatch-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfWarnReadingOrderUnverified-name =
    Reading order matches visual layout (warning)

pdf-check-PdfWarnReadingOrderUnverified-short-title =
    Reading order matches visual layout

pdf-check-PdfWarnReadingOrderUnverified-what =
    If the structure tree order doesn't match the visual layout, screen reader users hear content in a different sequence than sighted users see it.

pdf-check-PdfWarnReadingOrderUnverified-why =
    For example, a footer might be read before the main content, or a right column before a left column, creating confusion about the document's flow.

pdf-check-PdfWarnReadingOrderUnverified-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfErrHeadingHierarchyInvalid-name =
    Heading hierarchy valid

pdf-check-PdfErrHeadingHierarchyInvalid-short-title =
    Heading hierarchy valid

pdf-check-PdfErrHeadingHierarchyInvalid-what =
    Screen reader users rely on headings as their primary navigation method in documents — they can jump from heading to heading to scan the document structure.

pdf-check-PdfErrHeadingHierarchyInvalid-why =
    Skipped heading levels (e.g., H1 → H3 with no H2) break this navigation model and make users think they've missed a section.

pdf-check-PdfErrHeadingHierarchyInvalid-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfErrMultipleHeadingsPerNode-name =
    No multiple headings per node

pdf-check-PdfErrMultipleHeadingsPerNode-short-title =
    No multiple headings per node

pdf-check-PdfErrMultipleHeadingsPerNode-what =
    When a single structure element (such as a Sect or Div) contains more than one heading, screen readers cannot determine which heading labels that section.

pdf-check-PdfErrMultipleHeadingsPerNode-why =
    Users navigating by headings may hear duplicate or contradictory announcements, making it impossible to understand the document's logical outline.

pdf-check-PdfErrMultipleHeadingsPerNode-who =
    Users of assistive technology.

pdf-check-PdfErrMixedHeadingTagTypes-name =
    No mixed heading tag types

pdf-check-PdfErrMixedHeadingTagTypes-short-title =
    No mixed heading tag types

pdf-check-PdfErrMixedHeadingTagTypes-what =
    PDF supports two heading schemes: generic H tags (with a /headinglevel attribute) and numbered H1–H6 tags.

pdf-check-PdfErrMixedHeadingTagTypes-why =
    Mixing both in one document confuses assistive technology — screen readers may not correlate the two systems, causing headings to appear out of order or at incorrect nesting levels.

pdf-check-PdfErrMixedHeadingTagTypes-who =
    Users of assistive technology.

pdf-check-PdfErrDocumentLanguageNotSet-name =
    Document language set

pdf-check-PdfErrDocumentLanguageNotSet-short-title =
    Document language set

pdf-check-PdfErrDocumentLanguageNotSet-what =
    Screen readers use the document language to select the correct pronunciation engine.

pdf-check-PdfErrDocumentLanguageNotSet-why =
    Without it, a French document may be read with English pronunciation rules, making the content unintelligible. This affects every word in the document.

pdf-check-PdfErrDocumentLanguageNotSet-who =
    Screen-reader users and translation tools.

pdf-check-PdfErrLangValuesInvalidBcp47-name =
    Lang values are valid BCP 47

pdf-check-PdfErrLangValuesInvalidBcp47-short-title =
    Lang values are valid BCP 47

pdf-check-PdfErrLangValuesInvalidBcp47-what =
    Language tags must follow the BCP 47 standard (e.g., 'en', 'en-US', 'fr-CA') so assistive technology can select the correct speech synthesizer.

pdf-check-PdfErrLangValuesInvalidBcp47-why =
    Invalid tags like 'english' or 'en_US' cause AT to fall back to a default voice.

pdf-check-PdfErrLangValuesInvalidBcp47-who =
    Users of assistive technology.

pdf-check-PdfErrAnnotationLanguageIndeterminable-name =
    Annotation contents language determinable

pdf-check-PdfErrAnnotationLanguageIndeterminable-short-title =
    Annotation contents language determinable

pdf-check-PdfErrAnnotationLanguageIndeterminable-what =
    The /Contents text of annotations (tooltips, alt text, descriptions) must have a determinable language so screen readers pronounce it correctly.

pdf-check-PdfErrAnnotationLanguageIndeterminable-why =
    Without a language attribute, annotation text may be read with the wrong pronunciation engine.

pdf-check-PdfErrAnnotationLanguageIndeterminable-who =
    Users of assistive technology.

pdf-check-PdfErrFormFieldTooltipLanguageIndeterminable-name =
    Form field tooltip language determinable

pdf-check-PdfErrFormFieldTooltipLanguageIndeterminable-short-title =
    Form field tooltip language determinable

pdf-check-PdfErrFormFieldTooltipLanguageIndeterminable-what =
    Form field tooltips (/TU key) are read aloud by screen readers to describe the purpose of each field.

pdf-check-PdfErrFormFieldTooltipLanguageIndeterminable-why =
    If the tooltip language cannot be determined, the screen reader may mispronounce it, confusing users trying to fill out the form.

pdf-check-PdfErrFormFieldTooltipLanguageIndeterminable-who =
    Users of assistive technology.

pdf-check-PdfErrDocumentMetadataLanguageIndeterminable-name =
    Document metadata language determinable

pdf-check-PdfErrDocumentMetadataLanguageIndeterminable-short-title =
    Document metadata language determinable

pdf-check-PdfErrDocumentMetadataLanguageIndeterminable-what =
    XMP metadata fields (title, description, author) may be presented to users by screen readers or displayed in document properties dialogs.

pdf-check-PdfErrDocumentMetadataLanguageIndeterminable-why =
    If the language of this metadata is not determinable, it may be mispronounced or misinterpreted.

pdf-check-PdfErrDocumentMetadataLanguageIndeterminable-who =
    Users of assistive technology.

pdf-check-PdfWarnAbbreviationsMissingExpansion-name =
    Abbreviations have /E expansion (warning)

pdf-check-PdfWarnAbbreviationsMissingExpansion-short-title =
    Abbreviations have /E expansion

pdf-check-PdfWarnAbbreviationsMissingExpansion-what =
    Abbreviations and acronyms (e.g., 'PDF', 'WCAG', 'CNIB') are often mispronounced by screen readers.

pdf-check-PdfWarnAbbreviationsMissingExpansion-why =
    The /E (expansion) attribute provides the full text so AT can announce the expanded form.

pdf-check-PdfWarnAbbreviationsMissingExpansion-who =
    Users of assistive technology.

pdf-check-PdfErrFigureMissingAltText-name =
    Alt text on all Figure/Art tags

pdf-check-PdfErrFigureMissingAltText-short-title =
    Alt text on all Figure/Art tags

pdf-check-PdfErrFigureMissingAltText-what =
    Images without alternative text are invisible to screen reader users — they are either skipped entirely or announced as 'image' with no description.

pdf-check-PdfErrFigureMissingAltText-why =
    If the image conveys important information (logos, charts, diagrams, signatures), that information is completely lost.

pdf-check-PdfErrFigureMissingAltText-who =
    Blind, low-vision, and colour-blind users.

pdf-check-PdfWarnAltTextRedundantRoleText-name =
    Alt text free of redundant role text (warning)

pdf-check-PdfWarnAltTextRedundantRoleText-short-title =
    Alt text free of redundant role text

pdf-check-PdfWarnAltTextRedundantRoleText-what =
    Screen readers already announce the element type ('image', 'link', 'button').

pdf-check-PdfWarnAltTextRedundantRoleText-why =
    If the alt text also starts with 'Image of...' or 'Link to...', users hear it twice: 'Image: Image of the CNIB logo'. This is redundant and clutters the experience.

pdf-check-PdfWarnAltTextRedundantRoleText-who =
    Blind, low-vision, and colour-blind users.

pdf-check-PdfErrAltTextHidesInteractive-name =
    Alt text does not hide interactive elements

pdf-check-PdfErrAltTextHidesInteractive-short-title =
    Alt text does not hide interactive elements

pdf-check-PdfErrAltTextHidesInteractive-what =
    When a parent element has alt text, screen readers may read only the alt text and skip all children — including links, buttons, and form fields inside it.

pdf-check-PdfErrAltTextHidesInteractive-why =
    This effectively hides interactive elements, making them unreachable.

pdf-check-PdfErrAltTextHidesInteractive-who =
    Assistive-technology users.

pdf-check-PdfErrFigureMissingBBox-name =
    Figure elements have BBox attribute

pdf-check-PdfErrFigureMissingBBox-short-title =
    Figure elements have BBox attribute

pdf-check-PdfErrFigureMissingBBox-what =
    Figure elements without a bounding box (/BBox) cannot be properly positioned by assistive technology.

pdf-check-PdfErrFigureMissingBBox-why =
    The BBox tells AT where the figure appears on the page, which is needed for reflow, zoom, and spatial navigation.

pdf-check-PdfErrFigureMissingBBox-who =
    Users of assistive technology.

pdf-check-PdfErrLinkAnnotationEmpty-name =
    Link annotations have content

pdf-check-PdfErrLinkAnnotationEmpty-short-title =
    Link annotations have content

pdf-check-PdfErrLinkAnnotationEmpty-what =
    Links without accessible text are announced by screen readers as just 'link' with no indication of where the link goes.

pdf-check-PdfErrLinkAnnotationEmpty-why =
    Users must guess or skip the link entirely. This is one of the most common and frustrating accessibility barriers in PDFs.

pdf-check-PdfErrLinkAnnotationEmpty-who =
    All users navigating the document.

pdf-check-PdfErrLinkAnnotationMissingContents-name =
    Link annotations have Contents key

pdf-check-PdfErrLinkAnnotationMissingContents-short-title =
    Link annotations have Contents key

pdf-check-PdfErrLinkAnnotationMissingContents-what =
    The /Contents key of a link annotation provides the text that screen readers announce when a user encounters the link.

pdf-check-PdfErrLinkAnnotationMissingContents-why =
    Without it, assistive technology may read the raw URL or nothing at all, leaving users unable to understand the link's purpose or destination.

pdf-check-PdfErrLinkAnnotationMissingContents-who =
    Users of assistive technology.

pdf-check-PdfErrLinkAnnotationNotInLinkTag-name =
    Link annotations inside Link tags

pdf-check-PdfErrLinkAnnotationNotInLinkTag-short-title =
    Link annotations inside Link tags

pdf-check-PdfErrLinkAnnotationNotInLinkTag-what =
    Link annotations must be enclosed in Link structure elements so assistive technology can identify them as hyperlinks.

pdf-check-PdfErrLinkAnnotationNotInLinkTag-why =
    Without this nesting, users cannot distinguish links from regular text.

pdf-check-PdfErrLinkAnnotationNotInLinkTag-who =
    Users of assistive technology.

pdf-check-PdfErrAnnotationMissingAltDescription-name =
    Visible annotations have alt descriptions

pdf-check-PdfErrAnnotationMissingAltDescription-short-title =
    Visible annotations have alt descriptions

pdf-check-PdfErrAnnotationMissingAltDescription-what =
    Visible annotations without a /Contents description provide no text for screen readers to announce.

pdf-check-PdfErrAnnotationMissingAltDescription-why =
    The user may see a visual indicator but cannot understand what the annotation says.

pdf-check-PdfErrAnnotationMissingAltDescription-who =
    Users of assistive technology.

pdf-check-PdfErrNonLinkWidgetAnnotationUntagged-name =
    Non-link/widget annotations tagged

pdf-check-PdfErrNonLinkWidgetAnnotationUntagged-short-title =
    Non-link/widget annotations tagged

pdf-check-PdfErrNonLinkWidgetAnnotationUntagged-what =
    Annotations (comments, highlights, stamps, etc.) that are not included in the document's structure tree are invisible to assistive technology.

pdf-check-PdfErrNonLinkWidgetAnnotationUntagged-why =
    Screen reader users will not know these annotations exist.

pdf-check-PdfErrNonLinkWidgetAnnotationUntagged-who =
    Users of assistive technology.

pdf-check-PdfErrMultimediaAnnotationUntagged-name =
    Multimedia annotations tagged

pdf-check-PdfErrMultimediaAnnotationUntagged-short-title =
    Multimedia annotations tagged

pdf-check-PdfErrMultimediaAnnotationUntagged-what =
    Multimedia content (video, audio, 3D, rich media) embedded as annotations must be tagged in the structure tree.

pdf-check-PdfErrMultimediaAnnotationUntagged-why =
    Without tagging, assistive technology cannot discover or navigate to the media content.

pdf-check-PdfErrMultimediaAnnotationUntagged-who =
    Users of assistive technology.

pdf-check-PdfErrMediaClipAnnotationMissingAlt-name =
    Media clip annotations have alt text

pdf-check-PdfErrMediaClipAnnotationMissingAlt-short-title =
    Media clip annotations have alt text

pdf-check-PdfErrMediaClipAnnotationMissingAlt-what =
    Embedded media (video, audio, interactive content) without text descriptions are completely inaccessible to users who cannot see or hear the media content.

pdf-check-PdfErrMediaClipAnnotationMissingAlt-why =
    Embedded media (video, audio, interactive content) without text descriptions are completely inaccessible to users who cannot see or hear the media content.

pdf-check-PdfErrMediaClipAnnotationMissingAlt-who =
    Users of assistive technology.

pdf-check-PdfErrMediaClipAltMissing-name =
    Media clip alt text present

pdf-check-PdfErrMediaClipAltMissing-short-title =
    Media clip alt text present

pdf-check-PdfErrMediaClipAltMissing-what =
    Embedded media (audio, video) in a PDF needs alternative text so users who cannot perceive the media understand its content.

pdf-check-PdfErrMediaClipAltMissing-why =
    Screen reader users rely on alt text to know what the media contains and whether it is relevant.

pdf-check-PdfErrMediaClipAltMissing-who =
    Users of assistive technology.

pdf-check-PdfErrMediaClipContentTypeMissing-name =
    Media clip content type present

pdf-check-PdfErrMediaClipContentTypeMissing-short-title =
    Media clip content type present

pdf-check-PdfErrMediaClipContentTypeMissing-what =
    The /CT (content type) key tells PDF readers and assistive technology what type of media is embedded (e.g., video/mp4, audio/mpeg).

pdf-check-PdfErrMediaClipContentTypeMissing-why =
    Without it, the viewer may not be able to play the media, and screen readers cannot announce the media type.

pdf-check-PdfErrMediaClipContentTypeMissing-who =
    Users of assistive technology.

pdf-check-PdfErrAnnotationTabOrderInvalid-name =
    Annotation tab order on all annotated pages

pdf-check-PdfErrAnnotationTabOrderInvalid-short-title =
    Annotation tab order on all annotated pages

pdf-check-PdfErrAnnotationTabOrderInvalid-what =
    Pages with annotations must have a /Tabs entry specifying the tab order.

pdf-check-PdfErrAnnotationTabOrderInvalid-why =
    Without it, keyboard users cannot predictably navigate between annotations using Tab.

pdf-check-PdfErrAnnotationTabOrderInvalid-who =
    Users of assistive technology.

pdf-check-PdfErrNonStandardAnnotationSubtype-name =
    No non-standard annotation subtypes

pdf-check-PdfErrNonStandardAnnotationSubtype-short-title =
    No non-standard annotation subtypes

pdf-check-PdfErrNonStandardAnnotationSubtype-what =
    PDF readers and assistive technologies are designed to handle standard annotation subtypes (Link, Widget, Text, Highlight, etc.).

pdf-check-PdfErrNonStandardAnnotationSubtype-why =
    Non-standard subtypes may be ignored entirely, causing users to miss interactive content or important notes.

pdf-check-PdfErrNonStandardAnnotationSubtype-who =
    Users of assistive technology.

pdf-check-PdfErrTrapNetAnnotationPresent-name =
    No TrapNet annotations

pdf-check-PdfErrTrapNetAnnotationPresent-short-title =
    No TrapNet annotations

pdf-check-PdfErrTrapNetAnnotationPresent-what =
    TrapNet annotations are pre-press production artifacts used for color trapping.

pdf-check-PdfErrTrapNetAnnotationPresent-why =
    They are prohibited in PDF/UA because they serve no user-facing purpose and can confuse assistive technology.

pdf-check-PdfErrTrapNetAnnotationPresent-who =
    Users of assistive technology.

pdf-check-PdfErrPrinterMarkAnnotationInStructure-name =
    PrinterMark annotations not in structure

pdf-check-PdfErrPrinterMarkAnnotationInStructure-short-title =
    PrinterMark annotations not in structure

pdf-check-PdfErrPrinterMarkAnnotationInStructure-what =
    PrinterMark annotations (crop marks, registration marks, color bars) are pre-press production marks.

pdf-check-PdfErrPrinterMarkAnnotationInStructure-why =
    If included in the structure tree, they appear as content to screen readers, confusing users.

pdf-check-PdfErrPrinterMarkAnnotationInStructure-who =
    Users of assistive technology.

pdf-check-PdfErrFileAttachmentAnnotationInvalid-name =
    File attachment annotations valid

pdf-check-PdfErrFileAttachmentAnnotationInvalid-short-title =
    File attachment annotations valid

pdf-check-PdfErrFileAttachmentAnnotationInvalid-what =
    File attachment annotations embed files within the PDF.

pdf-check-PdfErrFileAttachmentAnnotationInvalid-why =
    Without proper structure (description, filename, relationship), assistive technology users cannot discover or understand attached files. The attachment may be completely invisible to screen readers.

pdf-check-PdfErrFileAttachmentAnnotationInvalid-who =
    Users of assistive technology.

pdf-check-PdfWarnLinkAltTextNotDescriptive-name =
    Link alt text is descriptive (warning)

pdf-check-PdfWarnLinkAltTextNotDescriptive-short-title =
    Link alt text is descriptive

pdf-check-PdfWarnLinkAltTextNotDescriptive-what =
    When link text is a raw URL like 'https://www.cnib.ca/en/donate', screen readers spell out every character.

pdf-check-PdfWarnLinkAltTextNotDescriptive-why =
    Users hear a long string of characters instead of a meaningful description. This is one of the most irritating accessibility issues.

pdf-check-PdfWarnLinkAltTextNotDescriptive-who =
    All users navigating the document.

pdf-check-PdfWarnBookmarksMissing-name =
    Bookmarks present (warning)

pdf-check-PdfWarnBookmarksMissing-short-title =
    Bookmarks present

pdf-check-PdfWarnBookmarksMissing-what =
    Bookmarks (also called outlines) let users jump directly to sections in longer documents.

pdf-check-PdfWarnBookmarksMissing-why =
    Without bookmarks, screen reader users must read through the entire document sequentially to find the section they need. Sighted users also benefit from the navigation panel.

pdf-check-PdfWarnBookmarksMissing-who =
    All users navigating the document.

pdf-check-PdfWarnCrossLanguageLinksUnverified-name =
    Cross-language link targets identified (warning)

pdf-check-PdfWarnCrossLanguageLinksUnverified-short-title =
    Cross-language link targets identified

pdf-check-PdfWarnCrossLanguageLinksUnverified-what =
    When a link leads to content in a different language than the document, users should be warned before following it.

pdf-check-PdfWarnCrossLanguageLinksUnverified-why =
    A French-speaking user clicking a link expecting French content but arriving at an English page has a poor experience.

pdf-check-PdfWarnCrossLanguageLinksUnverified-who =
    Screen-reader users and translation tools.

pdf-check-PdfErrCrossLanguageLinksUnidentified-name =
    Cross-language link targets identified

pdf-check-PdfErrCrossLanguageLinksUnidentified-short-title =
    Cross-language link targets identified

pdf-check-PdfErrCrossLanguageLinksUnidentified-what =
    When a link leads to content in a different language than the document, users should be warned before following it.

pdf-check-PdfErrCrossLanguageLinksUnidentified-why =
    A French-speaking user clicking a link expecting French content but arriving at an English page has a poor experience.

pdf-check-PdfErrCrossLanguageLinksUnidentified-who =
    Screen-reader users and translation tools.

pdf-check-PdfErrListStructureInvalid-name =
    List structure valid

pdf-check-PdfErrListStructureInvalid-short-title =
    List structure valid

pdf-check-PdfErrListStructureInvalid-what =
    Screen readers announce list structure to help users understand grouped content: 'List with 5 items'.

pdf-check-PdfErrListStructureInvalid-why =
    If lists are improperly structured (missing LI, Lbl, or LBody), screen readers may not announce the list at all, or may read items incorrectly.

pdf-check-PdfErrListStructureInvalid-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfErrEmptyList-name =
    No empty lists

pdf-check-PdfErrEmptyList-short-title =
    No empty lists

pdf-check-PdfErrEmptyList-what =
    Empty list elements with no list items are confusing artifacts.

pdf-check-PdfErrEmptyList-why =
    Screen readers announce 'List with 0 items' which interrupts the reading flow and confuses users about the document structure.

pdf-check-PdfErrEmptyList-who =
    Users of assistive technology.

pdf-check-PdfWarnListNestingDeep-name =
    List nesting valid (warning)

pdf-check-PdfWarnListNestingDeep-short-title =
    List nesting valid

pdf-check-PdfWarnListNestingDeep-what =
    Nested lists (sub-lists) must be placed inside the LBody of a parent LI element.

pdf-check-PdfWarnListNestingDeep-why =
    When a nested L is placed directly inside L or directly inside LI (without LBody), screen readers cannot correctly convey the list hierarchy to users.

pdf-check-PdfWarnListNestingDeep-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfWarnListItemLabelsInconsistent-name =
    List item labels (warning)

pdf-check-PdfWarnListItemLabelsInconsistent-short-title =
    List item labels

pdf-check-PdfWarnListItemLabelsInconsistent-what =
    The Lbl (label) element inside a list item provides the bullet character, number, or letter marker.

pdf-check-PdfWarnListItemLabelsInconsistent-why =
    Without Lbl, screen readers cannot distinguish the marker from the content, reducing navigability.

pdf-check-PdfWarnListItemLabelsInconsistent-who =
    Users of assistive technology.

pdf-check-PdfErrTableHeadersMissing-name =
    Table headers defined

pdf-check-PdfErrTableHeadersMissing-short-title =
    Table headers defined

pdf-check-PdfErrTableHeadersMissing-what =
    Screen readers use table header cells (TH) to announce column or row labels as users navigate between data cells.

pdf-check-PdfErrTableHeadersMissing-why =
    Without headers, users hear raw data values with no context — they cannot tell which column or row a value belongs to.

pdf-check-PdfErrTableHeadersMissing-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfErrTableHeaderScopeMissing-name =
    Table header scope defined

pdf-check-PdfErrTableHeaderScopeMissing-short-title =
    Table header scope defined

pdf-check-PdfErrTableHeaderScopeMissing-what =
    The /Scope attribute on TH cells tells screen readers whether a header applies to its column, its row, or both.

pdf-check-PdfErrTableHeaderScopeMissing-why =
    Without Scope, screen readers may not correctly associate headers with data cells, especially in complex tables.

pdf-check-PdfErrTableHeaderScopeMissing-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfWarnTableSectionsMissing-name =
    Table structure sections (warning)

pdf-check-PdfWarnTableSectionsMissing-short-title =
    Table structure sections

pdf-check-PdfWarnTableSectionsMissing-what =
    THead and TBody section wrappers help assistive technology distinguish header rows from data rows.

pdf-check-PdfWarnTableSectionsMissing-why =
    PDF 2.0 (PDF/UA-2) requires them. Without these wrappers, screen readers may not correctly repeat headers when scrolling through long tables.

pdf-check-PdfWarnTableSectionsMissing-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfWarnTableIrregular-name =
    Table regularity (warning)

pdf-check-PdfWarnTableIrregular-short-title =
    Table regularity

pdf-check-PdfWarnTableIrregular-what =
    When rows have different numbers of cells, screen readers lose track of which column a cell belongs to.

pdf-check-PdfWarnTableIrregular-why =
    This makes the table data meaningless to users who cannot see the visual layout.

pdf-check-PdfWarnTableIrregular-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfErrEmptyTable-name =
    No empty tables

pdf-check-PdfErrEmptyTable-short-title =
    No empty tables

pdf-check-PdfErrEmptyTable-what =
    Empty table elements with no data cells are confusing artifacts.

pdf-check-PdfErrEmptyTable-why =
    Screen readers announce 'Table with 0 rows, 0 columns' which interrupts the reading flow and confuses users about the document structure.

pdf-check-PdfErrEmptyTable-who =
    Users of assistive technology.

pdf-check-PdfWarnTableCaptionMissing-name =
    Table captions (warning)

pdf-check-PdfWarnTableCaptionMissing-short-title =
    Table captions

pdf-check-PdfWarnTableCaptionMissing-what =
    A Caption element provides a brief, accessible summary of a table's purpose.

pdf-check-PdfWarnTableCaptionMissing-why =
    Screen readers announce the caption before the user enters the table, giving context about what data to expect. Without a caption, users must navigate into the table and read cells to understand its purpose.

pdf-check-PdfWarnTableCaptionMissing-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfErrWidgetAnnotationNotInFormTag-name =
    Widget annotations inside Form tags

pdf-check-PdfErrWidgetAnnotationNotInFormTag-short-title =
    Widget annotations inside Form tags

pdf-check-PdfErrWidgetAnnotationNotInFormTag-what =
    Form field widgets (text boxes, checkboxes, radio buttons) must be enclosed in Form structure elements so assistive technology can identify them as interactive form controls and provide appropriate interaction cues.

pdf-check-PdfErrWidgetAnnotationNotInFormTag-why =
    Form field widgets (text boxes, checkboxes, radio buttons) must be enclosed in Form structure elements so assistive technology can identify them as interactive form controls and provide appropriate interaction cues.

pdf-check-PdfErrWidgetAnnotationNotInFormTag-who =
    Users of assistive technology.

pdf-check-PdfErrXfaFormsPresent-name =
    No XFA forms present

pdf-check-PdfErrXfaFormsPresent-short-title =
    No XFA forms present

pdf-check-PdfErrXfaFormsPresent-what =
    XFA (XML Forms Architecture) is a proprietary Adobe technology that creates dynamic forms inaccessible to assistive technology.

pdf-check-PdfErrXfaFormsPresent-why =
    Screen readers cannot identify, navigate, or interact with XFA form fields. XFA forms are prohibited in PDF/UA and deprecated in PDF 2.0.

pdf-check-PdfErrXfaFormsPresent-who =
    Users of assistive technology.

pdf-check-PdfErrFormFieldUnlabeled-name =
    Form fields labeled

pdf-check-PdfErrFormFieldUnlabeled-short-title =
    Form fields labeled

pdf-check-PdfErrFormFieldUnlabeled-what =
    Screen readers identify form fields by their accessible name.

pdf-check-PdfErrFormFieldUnlabeled-why =
    Without one, users hear only the field type ('edit text', 'checkbox') with no indication of what information to enter. This makes forms impossible to complete independently.

pdf-check-PdfErrFormFieldUnlabeled-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfWarnRequiredFieldsNotFlagged-name =
    Required fields flagged (warning)

pdf-check-PdfWarnRequiredFieldsNotFlagged-short-title =
    Required fields flagged

pdf-check-PdfWarnRequiredFieldsNotFlagged-what =
    Sighted users can see asterisks or 'required' labels next to mandatory fields.

pdf-check-PdfWarnRequiredFieldsNotFlagged-why =
    Screen reader users need the same information conveyed semantically through the /Ff (field flags) Required bit, so their software can announce 'required' automatically.

pdf-check-PdfWarnRequiredFieldsNotFlagged-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfErrFormFieldNotInStructure-name =
    Form fields tagged in structure

pdf-check-PdfErrFormFieldNotInStructure-short-title =
    Form fields tagged in structure

pdf-check-PdfErrFormFieldNotInStructure-what =
    Form fields must be tagged in the document structure tree with <Form> elements so screen readers can discover them when navigating by structure.

pdf-check-PdfErrFormFieldNotInStructure-why =
    Untagged fields exist only as visual widgets -- invisible to the document's logical structure.

pdf-check-PdfErrFormFieldNotInStructure-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfWarnFormFieldStructureUnverified-name =
    Form fields tagged in structure (warning)

pdf-check-PdfWarnFormFieldStructureUnverified-short-title =
    Form fields tagged in structure

pdf-check-PdfWarnFormFieldStructureUnverified-what =
    Form fields must be tagged in the document structure tree with <Form> elements so screen readers can discover them when navigating by structure.

pdf-check-PdfWarnFormFieldStructureUnverified-why =
    Untagged fields exist only as visual widgets -- invisible to the document's logical structure.

pdf-check-PdfWarnFormFieldStructureUnverified-who =
    Screen-reader and assistive-technology users.

pdf-check-PdfErrFormTabOrderInvalid-name =
    Form page tab order

pdf-check-PdfErrFormTabOrderInvalid-short-title =
    Form page tab order

pdf-check-PdfErrFormTabOrderInvalid-what =
    Keyboard users press Tab to move between form fields.

pdf-check-PdfErrFormTabOrderInvalid-why =
    If the tab order does not follow the visual layout, users end up jumping unpredictably around the page. Setting /Tabs to /S (Structure) ensures tab order follows the tag tree.

pdf-check-PdfErrFormTabOrderInvalid-who =
    Users with motor disabilities.

pdf-check-PdfWarnFormFieldNamesNotUnique-name =
    Form field names unique (warning)

pdf-check-PdfWarnFormFieldNamesNotUnique-short-title =
    Form field names unique

pdf-check-PdfWarnFormFieldNamesNotUnique-what =
    When multiple fields share the same /T name, they are treated as the same field by the PDF viewer.

pdf-check-PdfWarnFormFieldNamesNotUnique-why =
    Entering data in one silently overwrites the other. This causes data loss and confusion for all users, not just those using assistive technology.

pdf-check-PdfWarnFormFieldNamesNotUnique-who =
    Assistive-technology users.

pdf-check-PdfWarnFormRedundantEntry-name =
    Redundant entry in forms (warning)

pdf-check-PdfWarnFormRedundantEntry-short-title =
    Redundant entry in forms

pdf-check-PdfWarnFormRedundantEntry-what =
    Requiring users to re-enter the same information (such as name, email, or address) on multiple pages of a form creates a significant burden for people with cognitive, memory, or motor disabilities.

pdf-check-PdfWarnFormRedundantEntry-why =
    Each additional entry increases the chance of errors and fatigue.

pdf-check-PdfWarnFormRedundantEntry-who =
    Users of assistive technology.

pdf-check-PdfWarnAuthenticationNotAccessible-name =
    Accessible authentication (warning)

pdf-check-PdfWarnAuthenticationNotAccessible-short-title =
    Accessible authentication

pdf-check-PdfWarnAuthenticationNotAccessible-what =
    Password fields that block paste or auto-fill prevent people from using password managers and assistive technology to authenticate.

pdf-check-PdfWarnAuthenticationNotAccessible-why =
    This creates a barrier for users with cognitive disabilities who cannot memorise complex passwords, and for users with motor disabilities who struggle to type long strings accurately.

pdf-check-PdfWarnAuthenticationNotAccessible-who =
    Users of assistive technology.

pdf-check-PdfErrLabelInNameMismatch-name =
    Label in Name

pdf-check-PdfErrLabelInNameMismatch-short-title =
    Label in Name

pdf-check-PdfErrLabelInNameMismatch-what =
    Voice control users (Dragon NaturallySpeaking, Voice Control on Mac) speak what they see to activate controls.

pdf-check-PdfErrLabelInNameMismatch-why =
    If the accessible name doesn't contain the visible text, the voice command won't work. For example, seeing 'Donate Now' but the accessible name being 'Make a contribution' means saying 'click Donate Now' fails.

pdf-check-PdfErrLabelInNameMismatch-who =
    Users of assistive technology.

pdf-check-PdfWarnTargetSizeInsufficient-name =
    Interactive element target size (warning)

pdf-check-PdfWarnTargetSizeInsufficient-short-title =
    Interactive element target size

pdf-check-PdfWarnTargetSizeInsufficient-what =
    Small click/tap targets are difficult for users with motor impairments, tremors, or limited dexterity.

pdf-check-PdfWarnTargetSizeInsufficient-why =
    A tiny link target means users must position their cursor or finger with high precision, increasing errors and frustration.

pdf-check-PdfWarnTargetSizeInsufficient-who =
    Users with motor disabilities.

pdf-check-PdfWarnFocusIndicatorUnverified-name =
    Focus indicator visibility (warning)

pdf-check-PdfWarnFocusIndicatorUnverified-short-title =
    Focus indicator visibility

pdf-check-PdfWarnFocusIndicatorUnverified-what =
    Keyboard users need a visible indicator showing which element currently has focus.

pdf-check-PdfWarnFocusIndicatorUnverified-why =
    Without visible borders on link annotations, users tabbing through the document have no idea where they are.

pdf-check-PdfWarnFocusIndicatorUnverified-who =
    Users of assistive technology.

pdf-check-PdfErrFocusObscured-name =
    Focus not obscured

pdf-check-PdfErrFocusObscured-short-title =
    Focus not obscured

pdf-check-PdfErrFocusObscured-what =
    When an interactive element (form field, link, or button) is completely covered by another element, keyboard users cannot see the focus indicator when they tab to it.

pdf-check-PdfErrFocusObscured-why =
    This makes the element effectively invisible and unusable for anyone who relies on keyboard navigation.

pdf-check-PdfErrFocusObscured-who =
    Users of assistive technology.

pdf-check-PdfWarnDraggingNoAlternative-name =
    Dragging movement alternatives (warning)

pdf-check-PdfWarnDraggingNoAlternative-short-title =
    Dragging movement alternatives

pdf-check-PdfWarnDraggingNoAlternative-what =
    Some people cannot perform dragging motions due to motor disabilities, tremors, or the use of alternative input devices.

pdf-check-PdfWarnDraggingNoAlternative-why =
    If a form widget requires a drag gesture (such as a slider), there must be a single-pointer or keyboard alternative to perform the same action.

pdf-check-PdfWarnDraggingNoAlternative-who =
    Users with motor disabilities.

pdf-check-PdfErrTextContrastBelowAa-name =
    Text contrast (WCAG AA)

pdf-check-PdfErrTextContrastBelowAa-short-title =
    Text contrast (WCAG AA)

pdf-check-PdfErrTextContrastBelowAa-what =
    TODO_AUTHOR: pdfMax has no remediation entry for 'Text contrast (WCAG AA)' (stable id: PdfErrTextContrastBelowAa); fill in by hand.

pdf-check-PdfErrTextContrastBelowAa-why =
    TODO_AUTHOR: pdfMax has no remediation entry for 'Text contrast (WCAG AA)' (stable id: PdfErrTextContrastBelowAa); fill in by hand.

pdf-check-PdfErrTextContrastBelowAa-who =
    Blind, low-vision, and colour-blind users.

pdf-check-PdfInfoTextContrastNoData-name =
    Text contrast (WCAG AA) (no data)

pdf-check-PdfInfoTextContrastNoData-short-title =
    Text contrast (WCAG AA)

pdf-check-PdfInfoTextContrastNoData-what =
    TODO_AUTHOR: pdfMax has no remediation entry for 'Text contrast (WCAG AA)' (stable id: PdfInfoTextContrastNoData); fill in by hand.

pdf-check-PdfInfoTextContrastNoData-why =
    TODO_AUTHOR: pdfMax has no remediation entry for 'Text contrast (WCAG AA)' (stable id: PdfInfoTextContrastNoData); fill in by hand.

pdf-check-PdfInfoTextContrastNoData-who =
    Blind, low-vision, and colour-blind users.

pdf-check-PdfWarnTextContrastBelowAaa-name =
    Text contrast (WCAG AAA) (warning)

pdf-check-PdfWarnTextContrastBelowAaa-short-title =
    Text contrast (WCAG AAA)

pdf-check-PdfWarnTextContrastBelowAaa-what =
    TODO_AUTHOR: pdfMax has no remediation entry for 'Text contrast (WCAG AAA)' (stable id: PdfWarnTextContrastBelowAaa); fill in by hand.

pdf-check-PdfWarnTextContrastBelowAaa-why =
    TODO_AUTHOR: pdfMax has no remediation entry for 'Text contrast (WCAG AAA)' (stable id: PdfWarnTextContrastBelowAaa); fill in by hand.

pdf-check-PdfWarnTextContrastBelowAaa-who =
    Blind, low-vision, and colour-blind users.

pdf-check-PdfInfoTextContrastAaaNoData-name =
    Text contrast (WCAG AAA) (no data)

pdf-check-PdfInfoTextContrastAaaNoData-short-title =
    Text contrast (WCAG AAA)

pdf-check-PdfInfoTextContrastAaaNoData-what =
    TODO_AUTHOR: pdfMax has no remediation entry for 'Text contrast (WCAG AAA)' (stable id: PdfInfoTextContrastAaaNoData); fill in by hand.

pdf-check-PdfInfoTextContrastAaaNoData-why =
    TODO_AUTHOR: pdfMax has no remediation entry for 'Text contrast (WCAG AAA)' (stable id: PdfInfoTextContrastAaaNoData); fill in by hand.

pdf-check-PdfInfoTextContrastAaaNoData-who =
    Blind, low-vision, and colour-blind users.

pdf-check-PdfErrFontsNotEmbedded-name =
    All fonts embedded

pdf-check-PdfErrFontsNotEmbedded-short-title =
    All fonts embedded

pdf-check-PdfErrFontsNotEmbedded-what =
    When fonts are not embedded, the PDF viewer substitutes a different font, which can change character spacing, line breaks, and even make some characters display incorrectly.

pdf-check-PdfErrFontsNotEmbedded-why =
    This affects both visual readability and text extraction used by screen readers and search tools.

pdf-check-PdfErrFontsNotEmbedded-who =
    Users of assistive technology.

pdf-check-PdfErrFontSizeTooSmall-name =
    Font sizes accessible

pdf-check-PdfErrFontSizeTooSmall-short-title =
    Font sizes accessible

pdf-check-PdfErrFontSizeTooSmall-what =
    Text below 9pt is extremely difficult to read, even for users with typical vision.

pdf-check-PdfErrFontSizeTooSmall-why =
    For users with low vision who rely on magnification, small text requires extreme zoom levels that make the document unusable. PDF text cannot be resized by users — unlike web content, the authored size is final.

pdf-check-PdfErrFontSizeTooSmall-who =
    Users of assistive technology.

pdf-check-PdfWarnFontSizeBorderline-name =
    Font sizes accessible (warning)

pdf-check-PdfWarnFontSizeBorderline-short-title =
    Font sizes accessible

pdf-check-PdfWarnFontSizeBorderline-what =
    Text below 9pt is extremely difficult to read, even for users with typical vision.

pdf-check-PdfWarnFontSizeBorderline-why =
    For users with low vision who rely on magnification, small text requires extreme zoom levels that make the document unusable. PDF text cannot be resized by users — unlike web content, the authored size is final.

pdf-check-PdfWarnFontSizeBorderline-who =
    Users of assistive technology.

pdf-check-PdfWarnFontFaceReadability-name =
    Font faces readable (warning)

pdf-check-PdfWarnFontFaceReadability-short-title =
    Font faces readable

pdf-check-PdfWarnFontFaceReadability-what =
    Script, decorative, narrow, and blackletter fonts are significantly harder to read for users with dyslexia, low vision, or cognitive disabilities.

pdf-check-PdfWarnFontFaceReadability-why =
    These fonts reduce reading speed and comprehension for all users.

pdf-check-PdfWarnFontFaceReadability-who =
    Users of assistive technology.

pdf-check-PdfWarnFontMagnificationRatio-name =
    Font size ratio (magnification) (warning)

pdf-check-PdfWarnFontMagnificationRatio-short-title =
    Font size ratio (magnification)

pdf-check-PdfWarnFontMagnificationRatio-what =
    When the ratio between the largest and smallest font sizes is too great, magnification users face a dilemma: zoom enough to read the small text and the large text extends far beyond the viewport, or zoom for the large text and the small text remains unreadable.

pdf-check-PdfWarnFontMagnificationRatio-why =
    When the ratio between the largest and smallest font sizes is too great, magnification users face a dilemma: zoom enough to read the small text and the large text extends far beyond the viewport, or zoom for the large text and the small text remains unreadable.

pdf-check-PdfWarnFontMagnificationRatio-who =
    Users of assistive technology.

pdf-check-PdfWarnTextRotated-name =
    Text rotation accessible (warning)

pdf-check-PdfWarnTextRotated-short-title =
    Text rotation accessible

pdf-check-PdfWarnTextRotated-what =
    Rotated text (vertical, diagonal, upside-down) is difficult to read for everyone and particularly challenging for users with cognitive or visual disabilities.

pdf-check-PdfWarnTextRotated-why =
    Magnification users may not even realize rotated text exists if it falls outside their viewport.

pdf-check-PdfWarnTextRotated-who =
    Users of assistive technology.

pdf-check-PdfWarnItalicTextOveruse-name =
    Italic text usage (warning)

pdf-check-PdfWarnItalicTextOveruse-short-title =
    Italic text usage

pdf-check-PdfWarnItalicTextOveruse-what =
    Long passages of italic text are significantly harder to read, especially for users with dyslexia or low vision.

pdf-check-PdfWarnItalicTextOveruse-why =
    Italic characters have less distinct letter shapes, reducing recognition speed. Extended italic runs slow reading for all users.

pdf-check-PdfWarnItalicTextOveruse-who =
    Users of assistive technology.

pdf-check-PdfWarnLineHeightInsufficient-name =
    Line height accessible (warning)

pdf-check-PdfWarnLineHeightInsufficient-short-title =
    Line height accessible

pdf-check-PdfWarnLineHeightInsufficient-what =
    Tight line spacing (leading) causes text lines to appear crowded, making it difficult to track from one line to the next.

pdf-check-PdfWarnLineHeightInsufficient-why =
    Users with visual processing disorders, dyslexia, and low vision are most affected. Unlike web content, PDF line spacing cannot be adjusted by the user — the authored value is final.

pdf-check-PdfWarnLineHeightInsufficient-who =
    Users of assistive technology.

pdf-check-PdfErrLineHeightInsufficient-name =
    Line height accessible

pdf-check-PdfErrLineHeightInsufficient-short-title =
    Line height accessible

pdf-check-PdfErrLineHeightInsufficient-what =
    Tight line spacing (leading) causes text lines to appear crowded, making it difficult to track from one line to the next.

pdf-check-PdfErrLineHeightInsufficient-why =
    Users with visual processing disorders, dyslexia, and low vision are most affected. Unlike web content, PDF line spacing cannot be adjusted by the user — the authored value is final.

pdf-check-PdfErrLineHeightInsufficient-who =
    Users of assistive technology.

pdf-check-PdfWarnTextAlignmentNonOptimal-name =
    Text alignment accessible (warning)

pdf-check-PdfWarnTextAlignmentNonOptimal-short-title =
    Text alignment accessible

pdf-check-PdfWarnTextAlignmentNonOptimal-what =
    Justified text creates uneven word spacing — 'rivers of white space' — that disrupts reading flow.

pdf-check-PdfWarnTextAlignmentNonOptimal-why =
    Centered text makes the left edge unpredictable, so magnification users lose their position at the start of each line. Left-aligned text provides a consistent left edge that readers can anchor to.

pdf-check-PdfWarnTextAlignmentNonOptimal-who =
    Users of assistive technology.

pdf-check-PdfErrFontMissingToUnicode-name =
    Unicode mapping (ToUnicode)

pdf-check-PdfErrFontMissingToUnicode-short-title =
    Unicode mapping (ToUnicode)

pdf-check-PdfErrFontMissingToUnicode-what =
    The /ToUnicode CMap tells PDF readers how to convert internal character codes to Unicode text.

pdf-check-PdfErrFontMissingToUnicode-why =
    Without it, text cannot be copied, searched, or read by screen readers — characters may appear as gibberish or empty strings. This is one of the most common causes of inaccessible PDFs.

pdf-check-PdfErrFontMissingToUnicode-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingToUnicode-name =
    Unicode mapping (ToUnicode) (no data)

pdf-check-PdfInfoFontMetadataMissingToUnicode-short-title =
    Unicode mapping (ToUnicode)

pdf-check-PdfInfoFontMetadataMissingToUnicode-what =
    The /ToUnicode CMap tells PDF readers how to convert internal character codes to Unicode text.

pdf-check-PdfInfoFontMetadataMissingToUnicode-why =
    Without it, text cannot be copied, searched, or read by screen readers — characters may appear as gibberish or empty strings. This is one of the most common causes of inaccessible PDFs.

pdf-check-PdfInfoFontMetadataMissingToUnicode-who =
    Users of assistive technology.

pdf-check-PdfErrCidFontGidMappingMissing-name =
    CID font GID mapping

pdf-check-PdfErrCidFontGidMappingMissing-short-title =
    CID font GID mapping

pdf-check-PdfErrCidFontGidMappingMissing-what =
    CIDFontType2 fonts (TrueType-based CID fonts) use a /CIDToGIDMap to translate character IDs to glyph IDs in the TrueType font program.

pdf-check-PdfErrCidFontGidMappingMissing-why =
    Without this mapping, the PDF viewer cannot correctly select glyphs, potentially rendering wrong characters or blanks.

pdf-check-PdfErrCidFontGidMappingMissing-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingCidGidMapping-name =
    CID font GID mapping (no data)

pdf-check-PdfInfoFontMetadataMissingCidGidMapping-short-title =
    CID font GID mapping

pdf-check-PdfInfoFontMetadataMissingCidGidMapping-what =
    CIDFontType2 fonts (TrueType-based CID fonts) use a /CIDToGIDMap to translate character IDs to glyph IDs in the TrueType font program.

pdf-check-PdfInfoFontMetadataMissingCidGidMapping-why =
    Without this mapping, the PDF viewer cannot correctly select glyphs, potentially rendering wrong characters or blanks.

pdf-check-PdfInfoFontMetadataMissingCidGidMapping-who =
    Users of assistive technology.

pdf-check-PdfErrCmapResourcesInvalid-name =
    CMap resources valid

pdf-check-PdfErrCmapResourcesInvalid-short-title =
    CMap resources valid

pdf-check-PdfErrCmapResourcesInvalid-what =
    Type 0 (composite) fonts reference a CMap resource that defines how character codes map to CID values.

pdf-check-PdfErrCmapResourcesInvalid-why =
    If the CMap is a non-standard name that isn't embedded as a stream, the PDF viewer has no way to decode the text, resulting in garbled or missing content.

pdf-check-PdfErrCmapResourcesInvalid-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingCmapResources-name =
    CMap resources valid (no data)

pdf-check-PdfInfoFontMetadataMissingCmapResources-short-title =
    CMap resources valid

pdf-check-PdfInfoFontMetadataMissingCmapResources-what =
    Type 0 (composite) fonts reference a CMap resource that defines how character codes map to CID values.

pdf-check-PdfInfoFontMetadataMissingCmapResources-why =
    If the CMap is a non-standard name that isn't embedded as a stream, the PDF viewer has no way to decode the text, resulting in garbled or missing content.

pdf-check-PdfInfoFontMetadataMissingCmapResources-who =
    Users of assistive technology.

pdf-check-PdfErrToUnicodeInvalidValues-name =
    Valid Unicode values

pdf-check-PdfErrToUnicodeInvalidValues-short-title =
    Valid Unicode values

pdf-check-PdfErrToUnicodeInvalidValues-what =
    ToUnicode CMaps that map characters to U+0000 (null), U+FEFF (byte order mark), or U+FFFE (non-character) produce invalid Unicode text.

pdf-check-PdfErrToUnicodeInvalidValues-why =
    Screen readers may skip these characters, read them as blanks, or behave unpredictably. Search and copy-paste also fail.

pdf-check-PdfErrToUnicodeInvalidValues-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingValidUnicode-name =
    Valid Unicode values (no data)

pdf-check-PdfInfoFontMetadataMissingValidUnicode-short-title =
    Valid Unicode values

pdf-check-PdfInfoFontMetadataMissingValidUnicode-what =
    ToUnicode CMaps that map characters to U+0000 (null), U+FEFF (byte order mark), or U+FFFE (non-character) produce invalid Unicode text.

pdf-check-PdfInfoFontMetadataMissingValidUnicode-why =
    Screen readers may skip these characters, read them as blanks, or behave unpredictably. Search and copy-paste also fail.

pdf-check-PdfInfoFontMetadataMissingValidUnicode-who =
    Users of assistive technology.

pdf-check-PdfErrFontNotdefReferenced-name =
    No .notdef glyph references

pdf-check-PdfErrFontNotdefReferenced-short-title =
    No .notdef glyph references

pdf-check-PdfErrFontNotdefReferenced-what =
    When a font's encoding maps characters to .notdef, those characters render as blank or replacement symbols and cannot be extracted as text.

pdf-check-PdfErrFontNotdefReferenced-why =
    Screen readers skip or misread these characters, losing document content.

pdf-check-PdfErrFontNotdefReferenced-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingNotdef-name =
    No .notdef glyph references (no data)

pdf-check-PdfInfoFontMetadataMissingNotdef-short-title =
    No .notdef glyph references

pdf-check-PdfInfoFontMetadataMissingNotdef-what =
    When a font's encoding maps characters to .notdef, those characters render as blank or replacement symbols and cannot be extracted as text.

pdf-check-PdfInfoFontMetadataMissingNotdef-why =
    Screen readers skip or misread these characters, losing document content.

pdf-check-PdfInfoFontMetadataMissingNotdef-who =
    Users of assistive technology.

pdf-check-PdfErrFontGlyphWidthsInconsistent-name =
    Font glyph widths consistent

pdf-check-PdfErrFontGlyphWidthsInconsistent-short-title =
    Font glyph widths consistent

pdf-check-PdfErrFontGlyphWidthsInconsistent-what =
    If a font's /Widths array length doesn't match the declared character range (LastChar - FirstChar + 1), or a CID font lacks width definitions, text extraction produces garbled spacing.

pdf-check-PdfErrFontGlyphWidthsInconsistent-why =
    Copy-paste and screen reader output become unreliable.

pdf-check-PdfErrFontGlyphWidthsInconsistent-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingGlyphWidths-name =
    Font glyph widths consistent (no data)

pdf-check-PdfInfoFontMetadataMissingGlyphWidths-short-title =
    Font glyph widths consistent

pdf-check-PdfInfoFontMetadataMissingGlyphWidths-what =
    If a font's /Widths array length doesn't match the declared character range (LastChar - FirstChar + 1), or a CID font lacks width definitions, text extraction produces garbled spacing.

pdf-check-PdfInfoFontMetadataMissingGlyphWidths-why =
    Copy-paste and screen reader output become unreliable.

pdf-check-PdfInfoFontMetadataMissingGlyphWidths-who =
    Users of assistive technology.

pdf-check-PdfErrNotdefInDifferences-name =
    No .notdef in Differences array

pdf-check-PdfErrNotdefInDifferences-short-title =
    No .notdef in Differences array

pdf-check-PdfErrNotdefInDifferences-what =
    The .notdef glyph is a placeholder for missing characters (often displayed as a blank rectangle).

pdf-check-PdfErrNotdefInDifferences-why =
    If a font's /Differences array references .notdef, it means a character code is explicitly mapped to a missing glyph — text at that position will be blank or unreadable for both visual and assistive technology users.

pdf-check-PdfErrNotdefInDifferences-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingDifferencesNotdef-name =
    No .notdef in Differences array (no data)

pdf-check-PdfInfoFontMetadataMissingDifferencesNotdef-short-title =
    No .notdef in Differences array

pdf-check-PdfInfoFontMetadataMissingDifferencesNotdef-what =
    The .notdef glyph is a placeholder for missing characters (often displayed as a blank rectangle).

pdf-check-PdfInfoFontMetadataMissingDifferencesNotdef-why =
    If a font's /Differences array references .notdef, it means a character code is explicitly mapped to a missing glyph — text at that position will be blank or unreadable for both visual and assistive technology users.

pdf-check-PdfInfoFontMetadataMissingDifferencesNotdef-who =
    Users of assistive technology.

pdf-check-PdfErrIdentityCmapMissingToUnicode-name =
    Identity CMap has ToUnicode

pdf-check-PdfErrIdentityCmapMissingToUnicode-short-title =
    Identity CMap has ToUnicode

pdf-check-PdfErrIdentityCmapMissingToUnicode-what =
    Identity-H and Identity-V CMaps use raw glyph IDs as character codes.

pdf-check-PdfErrIdentityCmapMissingToUnicode-why =
    Without a /ToUnicode map, there is no way to convert these glyph IDs to meaningful text. Screen readers will be silent or read meaningless values, and text cannot be searched or copied.

pdf-check-PdfErrIdentityCmapMissingToUnicode-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingIdentityCmap-name =
    Identity CMap has ToUnicode (no data)

pdf-check-PdfInfoFontMetadataMissingIdentityCmap-short-title =
    Identity CMap has ToUnicode

pdf-check-PdfInfoFontMetadataMissingIdentityCmap-what =
    Identity-H and Identity-V CMaps use raw glyph IDs as character codes.

pdf-check-PdfInfoFontMetadataMissingIdentityCmap-why =
    Without a /ToUnicode map, there is no way to convert these glyph IDs to meaningful text. Screen readers will be silent or read meaningless values, and text cannot be searched or copied.

pdf-check-PdfInfoFontMetadataMissingIdentityCmap-who =
    Users of assistive technology.

pdf-check-PdfErrCmapWmodeInconsistent-name =
    CMap WMode consistency

pdf-check-PdfErrCmapWmodeInconsistent-short-title =
    CMap WMode consistency

pdf-check-PdfErrCmapWmodeInconsistent-what =
    The WMode (writing mode) value in a CMap determines whether text is laid out horizontally (0) or vertically (1).

pdf-check-PdfErrCmapWmodeInconsistent-why =
    If the CMap's WMode does not match the font's actual writing direction, text extraction and screen reader output will be garbled or characters will appear in the wrong order.

pdf-check-PdfErrCmapWmodeInconsistent-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingCmapWmode-name =
    CMap WMode consistency (no data)

pdf-check-PdfInfoFontMetadataMissingCmapWmode-short-title =
    CMap WMode consistency

pdf-check-PdfInfoFontMetadataMissingCmapWmode-what =
    The WMode (writing mode) value in a CMap determines whether text is laid out horizontally (0) or vertically (1).

pdf-check-PdfInfoFontMetadataMissingCmapWmode-why =
    If the CMap's WMode does not match the font's actual writing direction, text extraction and screen reader output will be garbled or characters will appear in the wrong order.

pdf-check-PdfInfoFontMetadataMissingCmapWmode-who =
    Users of assistive technology.

pdf-check-PdfErrNonSymbolicTrueTypeLatinMapping-name =
    Non-symbolic TrueType Latin mapping

pdf-check-PdfErrNonSymbolicTrueTypeLatinMapping-short-title =
    Non-symbolic TrueType Latin mapping

pdf-check-PdfErrNonSymbolicTrueTypeLatinMapping-what =
    Non-symbolic TrueType fonts (standard text fonts like Arial, Calibri) must use standard encoding so that character codes map predictably to glyphs.

pdf-check-PdfErrNonSymbolicTrueTypeLatinMapping-why =
    Incorrect mapping causes text extraction to produce wrong characters, breaking screen reader output and copy-paste.

pdf-check-PdfErrNonSymbolicTrueTypeLatinMapping-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingNonSymbolicTrueType-name =
    Non-symbolic TrueType Latin mapping (no data)

pdf-check-PdfInfoFontMetadataMissingNonSymbolicTrueType-short-title =
    Non-symbolic TrueType Latin mapping

pdf-check-PdfInfoFontMetadataMissingNonSymbolicTrueType-what =
    Non-symbolic TrueType fonts (standard text fonts like Arial, Calibri) must use standard encoding so that character codes map predictably to glyphs.

pdf-check-PdfInfoFontMetadataMissingNonSymbolicTrueType-why =
    Incorrect mapping causes text extraction to produce wrong characters, breaking screen reader output and copy-paste.

pdf-check-PdfInfoFontMetadataMissingNonSymbolicTrueType-who =
    Users of assistive technology.

pdf-check-PdfInfoFontMetadataMissingEncodingConsistency-name =
    Font encoding consistency (no data)

pdf-check-PdfInfoFontMetadataMissingEncodingConsistency-short-title =
    Font encoding consistency

pdf-check-PdfInfoFontMetadataMissingEncodingConsistency-what =
    When a font's declared encoding does not match the actual glyph mapping, text extraction produces wrong characters.

pdf-check-PdfInfoFontMetadataMissingEncodingConsistency-why =
    Screen readers read gibberish, search fails, and copy-paste yields incorrect text — effectively making the content inaccessible.

pdf-check-PdfInfoFontMetadataMissingEncodingConsistency-who =
    Users of assistive technology.

pdf-check-PdfWarnEmptyTags-name =
    Aucune balise vide

pdf-check-PdfWarnEmptyTags-short-title =
    Balises vides

pdf-check-PdfWarnEmptyTags-what =
    Certains éléments de structure sont des balises terminales sans texte, sans texte de remplacement et sans contenu balisé en dessous.

pdf-check-PdfWarnEmptyTags-why =
    Une balise vide n'annonce rien tout en imposant un arrêt lors de la navigation par élément, et une personne utilisant un lecteur d'écran ne peut pas distinguer un paragraphe vide d'un paragraphe dont le texte ne lui est pas parvenu.

pdf-check-PdfWarnEmptyTags-who =
    Les personnes utilisant un lecteur d'écran qui naviguent élément par élément.

pdf-check-PdfErrIncorrectNesting-name =
    Imbrication correcte

pdf-check-PdfErrIncorrectNesting-short-title =
    Imbrication incorrecte

pdf-check-PdfErrIncorrectNesting-what =
    Des éléments de structure se trouvent dans des parents que la spécification n'autorise pas : un élément imbriqué dans un autre de même type, ou un élément positionnel comme une cellule de tableau en dehors de la ligne à laquelle elle appartient.

pdf-check-PdfErrIncorrectNesting-why =
    Un élément imbriqué dans un autre de même type empêche de savoir où un bloc se termine et où le suivant commence. Une cellule hors d'une ligne, ou une ligne hors d'un tableau, brise la grille sur laquelle repose la navigation, et le contenu environnant perd la structure qui lui donnait son sens.

pdf-check-PdfErrIncorrectNesting-who =
    Les personnes utilisant un lecteur d'écran, et toute personne naviguant dans les tableaux ou les listes par la structure.

pdf-check-PdfErrArtifactInsideTagged-name =
    Aucun artéfact dans du contenu balisé

pdf-check-PdfErrArtifactInsideTagged-short-title =
    Artéfact dans du contenu balisé

pdf-check-PdfErrArtifactInsideTagged-what =
    Une section marquée comme artéfact est imbriquée dans du contenu balisé réel.

pdf-check-PdfErrArtifactInsideTagged-why =
    Un artéfact désigne un élément que le lecteur peut ignorer. En déclarer un au milieu de ce qu'une personne est en train de lire supprime du texte ou l'interrompt, selon la façon dont son logiciel résout la contradiction.

pdf-check-PdfErrArtifactInsideTagged-who =
    Les personnes utilisant un lecteur d'écran.

pdf-check-PdfErrTaggedInsideArtifact-name =
    Aucun contenu balisé dans un artéfact

pdf-check-PdfErrTaggedInsideArtifact-short-title =
    Contenu balisé dans un artéfact

pdf-check-PdfErrTaggedInsideArtifact-what =
    Du contenu balisé réel est imbriqué dans une section marquée comme artéfact.

pdf-check-PdfErrTaggedInsideArtifact-why =
    Le contenu situé dans un artéfact est du contenu que le lecteur est invité à ignorer : il n'est donc jamais annoncé. Le texte est sur la page et inaccessible, sans aucune indication qu'il a été omis.

pdf-check-PdfErrTaggedInsideArtifact-who =
    Les personnes utilisant un lecteur d'écran.

pdf-check-PdfErrUntaggedContent-name =
    Tout le contenu est balisé ou artéfact

pdf-check-PdfErrUntaggedContent-short-title =
    Contenu non balisé

pdf-check-PdfErrUntaggedContent-what =
    Du texte ou des images se trouvent en dehors de toute section balisée ou d'artéfact : ils n'appartiennent donc à aucune des deux catégories.

pdf-check-PdfErrUntaggedContent-why =
    Un contenu qui n'est ni balisé ni déclaré comme artéfact est visible sur la page et invisible pour les technologies d'assistance, sans aucune indication de l'omission. Un document numérisé en est le cas extrême : chaque page est une seule image non balisée, sans aucun texte.

pdf-check-PdfErrUntaggedContent-who =
    Les personnes utilisant un lecteur d'écran, et toute personne dépendant de l'extraction de texte, de la recherche ou du redimensionnement.

pdf-check-PdfErrNoTextLayer-name =
    Le document comporte une couche de texte

pdf-check-PdfErrNoTextLayer-short-title =
    Aucune couche de texte

pdf-check-PdfErrNoTextLayer-what =
    Aucune page de ce document ne contient de texte. Les mots de la page n'existent que sous forme d'images de mots.

pdf-check-PdfErrNoTextLayer-why =
    Une numérisation est une photographie d'une page. Un lecteur d'écran n'y trouve rien à lire, et le texte ne peut être ni recherché, ni sélectionné, ni agrandi, ni redimensionné. Aucun balisage n'y change quoi que ce soit, car les mots ne sont pas dans le fichier en tant que mots.

pdf-check-PdfErrNoTextLayer-who =
    Les personnes utilisant un lecteur d'écran, et toute personne ayant besoin de rechercher, copier, agrandir ou redimensionner le texte.

pdf-check-PdfWarnUntaggedListsDetected-name =
    Listes non balisées détectées (avertissement)

pdf-check-PdfWarnUntaggedListsDetected-short-title =
    Listes non balisées détectées

pdf-check-PdfWarnUntaggedListsDetected-what =
    Des paragraphes consécutifs commencent par des puces, des chiffres ou des lettres, mais sont balisés P plutôt qu'en liste (L contenant des LI).

pdf-check-PdfWarnUntaggedListsDetected-why =
    Un lecteur d'écran annonce une véritable liste avec son nombre d'éléments et permet de passer de l'un à l'autre. Le même texte en paragraphes isolés n'offre ni l'un ni l'autre : la personne ignore combien d'éléments existent et ne peut pas les parcourir.

pdf-check-PdfWarnUntaggedListsDetected-who =
    Les personnes utilisant un lecteur d'écran, qui perdent la navigation par liste et le décompte des éléments.

pdf-check-PdfWarnHeadingSizeHierarchy-name =
    Hiérarchie visuelle des titres (avertissement)

pdf-check-PdfWarnHeadingSizeHierarchy-short-title =
    Hiérarchie visuelle des titres

pdf-check-PdfWarnHeadingSizeHierarchy-what =
    La taille visuelle des titres ne suit pas leur niveau de balisage : un titre de niveau inférieur est plus grand que celui du dessus, deux niveaux ont la même taille, ou un même niveau varie fortement.

pdf-check-PdfWarnHeadingSizeHierarchy-why =
    Les personnes voyantes se repèrent au poids visuel, pas aux balises. Un arbre de balises correct sans différence de taille visible ne leur offre aucune structure à parcourir, et un ordre de tailles inversé les induit activement en erreur.

pdf-check-PdfWarnHeadingSizeHierarchy-who =
    Les personnes voyantes, dont celles ayant des troubles cognitifs qui s'appuient sur une structure visuelle claire.

pdf-check-PdfInfoHeadingSizeNoFontData-name =
    Hiérarchie visuelle des titres (non évaluée)

pdf-check-PdfInfoHeadingSizeNoFontData-short-title =
    Tailles des titres non évaluées

pdf-check-PdfInfoHeadingSizeNoFontData-what =
    Les données de police n'ont pas pu être recueillies pour ce document ; les tailles des titres n'ont donc pas pu être comparées à leur niveau.

pdf-check-PdfInfoHeadingSizeNoFontData-why =
    La vérification n'a pas pu être exécutée. Ce n'est pas une réussite : rien ne démontre que la hiérarchie visuelle des titres est correcte.

pdf-check-PdfInfoHeadingSizeNoFontData-who =
    Indéterminé — la vérification n'a pas été exécutée.

pdf-check-PdfErrAccessibilityPermissionRestricted-name =
    Permission d'accessibilité restreinte (échec)

pdf-check-PdfErrAccessibilityPermissionRestricted-short-title =
    Extraction pour l'accessibilité bloquée

pdf-check-PdfErrAccessibilityPermissionRestricted-what =
    Le document est chiffré avec des permissions qui interdisent l'extraction de son contenu à des fins d'accessibilité.

pdf-check-PdfErrAccessibilityPermissionRestricted-why =
    Les technologies d'assistance lisent un PDF en extrayant son texte. Cette permission refusée, le document est illisible pour un lecteur d'écran, quelle que soit la qualité de son balisage.

pdf-check-PdfErrAccessibilityPermissionRestricted-who =
    Les personnes utilisant un lecteur d'écran, un afficheur braille ou la synthèse vocale, qui n'ont aucun accès au contenu.

pdf-check-PdfErrPageContentUntagged-name =
    Tout le contenu est balisé (échec)

pdf-check-PdfErrPageContentUntagged-short-title =
    Contenu de page non balisé

pdf-check-PdfErrPageContentUntagged-what =
    Une ou plusieurs pages affichent du texte sans déclarer le moindre identifiant de contenu marqué : rien sur ces pages n'est accessible depuis l'arbre de structure.

pdf-check-PdfErrPageContentUntagged-why =
    Un lecteur d'écran suit l'arbre de structure. Du texte sans contenu marqué n'y figure pas : ces pages sont lues comme vides, quel que soit ce qu'elles affichent.

pdf-check-PdfErrPageContentUntagged-who =
    Les personnes utilisant un lecteur d'écran, ainsi que celles qui utilisent la redistribution, la lecture à voix haute ou l'export de texte.

pdf-check-PdfWarnArtifactSubtypeMissing-name =
    Sous-types de classification des artefacts (avertissement)

pdf-check-PdfWarnArtifactSubtypeMissing-short-title =
    Artefacts sans sous-type

pdf-check-PdfWarnArtifactSubtypeMissing-what =
    Des artefacts sont marqués comme tels mais n'indiquent pas leur nature — /Pagination, /Layout, /Page ou /Background.

pdf-check-PdfWarnArtifactSubtypeMissing-why =
    Les lecteurs proposent d'ignorer les artefacts de façon sélective, par exemple les en-têtes courants mais pas les filets. Un artefact non classé ne peut être ignoré qu'en bloc, ou pas du tout.

pdf-check-PdfWarnArtifactSubtypeMissing-who =
    Les personnes utilisant un lecteur d'écran qui parcourent page par page de longs documents.

pdf-check-PdfWarnFormulaUnicodePrivateUse-name =
    Correspondance Unicode des formules valide (avertissement)

pdf-check-PdfWarnFormulaUnicodePrivateUse-short-title =
    Polices de formule vers la zone à usage privé

pdf-check-PdfWarnFormulaUnicodePrivateUse-what =
    Le document contient des éléments Formula et au moins une police fait correspondre ses glyphes à des points de code de la zone à usage privé d'Unicode plutôt qu'à de vrais caractères.

pdf-check-PdfWarnFormulaUnicodePrivateUse-why =
    Un point de code à usage privé n'a aucun sens hors de la police qui le définit. Un lecteur d'écran qui le lit ne produit rien, et le texte copié arrive sous forme de caractères de remplacement.

pdf-check-PdfWarnFormulaUnicodePrivateUse-who =
    Les personnes utilisant un lecteur d'écran, et quiconque copie de la notation mathématique depuis le document.

pdf-check-PdfWarnPronunciationHintsMissing-name =
    Indications de prononciation des abréviations (avertissement)

pdf-check-PdfWarnPronunciationHintsMissing-short-title =
    Abréviations sans indication de prononciation

pdf-check-PdfWarnPronunciationHintsMissing-what =
    De courtes suites de majuscules apparaissent sans /E, /Phoneme ni /PhoneticAlphabet pour indiquer comment les lire.

pdf-check-PdfWarnPronunciationHintsMissing-why =
    Sans indication, un lecteur choisit au hasard entre épeler l'abréviation et la prononcer comme un mot, et son choix varie d'un endroit à l'autre du même document.

pdf-check-PdfWarnPronunciationHintsMissing-who =
    Les personnes utilisant un lecteur d'écran, ainsi que les outils de lecture à voix haute et de synthèse vocale.

pdf-check-PdfErrLanguageOfPartsUnmarked-name =
    Balisage de la langue des parties (échec)

pdf-check-PdfErrLanguageOfPartsUnmarked-short-title =
    Passages en langue étrangère non balisés

pdf-check-PdfErrLanguageOfPartsUnmarked-what =
    Des passages semblent rédigés dans une langue autre que celle du document et ne portent pas de /Lang propre — ou le document ne déclare aucune langue et aucune n'a pu être déduite de son texte.

pdf-check-PdfErrLanguageOfPartsUnmarked-why =
    Un lecteur d'écran prononce chaque passage avec la voix du document sauf indication contraire. Un paragraphe français lu avec une voix anglaise va du comique à l'inintelligible.

pdf-check-PdfErrLanguageOfPartsUnmarked-who =
    Les personnes utilisant un lecteur d'écran, en particulier dans les documents bilingues.

pdf-check-PdfWarnComplexTableHeadersMissing-name =
    Association des en-têtes de tableaux complexes (avertissement)

pdf-check-PdfWarnComplexTableHeadersMissing-short-title =
    Cellules de tableau complexe sans /Headers

pdf-check-PdfWarnComplexTableHeadersMissing-what =
    Un tableau dont les en-têtes courent à la fois en ligne et en colonne, ou comportant des cellules fusionnées, contient des cellules de données qui ne nomment pas leurs en-têtes au moyen de l'attribut /Headers.

pdf-check-PdfWarnComplexTableHeadersMissing-why =
    Dans une grille simple, un lecteur déduit de la position quel en-tête régit une cellule. Dès que les en-têtes courent dans les deux sens ou que des cellules fusionnent, la position ne suffit plus et la cellule est annoncée sans les libellés qui lui donnent son sens.

pdf-check-PdfWarnComplexTableHeadersMissing-who =
    Les personnes utilisant un lecteur d'écran pour consulter des données tabulaires.

pdf-check-PdfErrRequiredFieldsNotVisuallyIndicated-name =
    Champs obligatoires indiqués visuellement (échec)

pdf-check-PdfErrRequiredFieldsNotVisuallyIndicated-short-title =
    Champs obligatoires non signalés visuellement

pdf-check-PdfErrRequiredFieldsNotVisuallyIndicated-what =
    Des champs que le formulaire marque comme obligatoires au moyen de l'indicateur /Ff Required ne comportent aucun repère visible — ni astérisque, ni mention « obligatoire », ni autre signal.

pdf-check-PdfErrRequiredFieldsNotVisuallyIndicated-why =
    L'indicateur Required n'est lu que par les technologies d'assistance. Une personne voyante qui remplit le formulaire n'a aucun moyen de savoir quels champs sont obligatoires avant l'échec de l'envoi.

pdf-check-PdfErrRequiredFieldsNotVisuallyIndicated-who =
    Les personnes voyantes, y compris celles ayant un handicap cognitif, pour qui un échec d'envoi inexpliqué est le plus difficile à surmonter.

pdf-check-PdfWarnRequiredFieldsIndicatorUnverified-name =
    Champs obligatoires indiqués visuellement (avertissement)

pdf-check-PdfWarnRequiredFieldsIndicatorUnverified-short-title =
    Repères des champs obligatoires à vérifier

pdf-check-PdfWarnRequiredFieldsIndicatorUnverified-what =
    Les champs obligatoires ne comportent ni astérisque ni mention « obligatoire » dans leur nom ou leur infobulle : l'existence d'un repère visible n'a pas pu être établie à partir des seules métadonnées. Ou bien des champs paraissent obligatoires sans porter l'indicateur sémantique.

pdf-check-PdfWarnRequiredFieldsIndicatorUnverified-why =
    Un repère dessiné sur la page satisfait l'exigence tout en restant invisible à une vérification fondée sur les métadonnées ; un repère purement visuel satisfait les personnes voyantes et laisse les technologies d'assistance dans l'ignorance. Dans les deux cas, il faut confronter les deux moitiés.

pdf-check-PdfWarnRequiredFieldsIndicatorUnverified-who =
    Les personnes voyantes et celles utilisant un lecteur d'écran, selon la moitié qui manque.

pdf-check-PdfErrNonTextContrastBelowMinimum-name =
    Contraste non textuel suffisant (échec)

pdf-check-PdfErrNonTextContrastBelowMinimum-short-title =
    Contraste non textuel inférieur à 3:1

pdf-check-PdfErrNonTextContrastBelowMinimum-what =
    Les bordures ou les limites des champs de formulaire, ou des graphiques tracés, n'atteignent pas le rapport de contraste de 3:1 exigé par le critère WCAG 1.4.11 par rapport à la couleur adjacente.

pdf-check-PdfErrNonTextContrastBelowMinimum-why =
    Un contrôle que personne ne voit est un contrôle que personne n'utilise. Il en va de même d'une barre de graphique ou d'un séparateur porteur de sens : s'il ne se distingue pas de son fond, l'information qu'il porte est perdue.

pdf-check-PdfErrNonTextContrastBelowMinimum-who =
    Les personnes malvoyantes, et quiconque lit en pleine lumière ou sur un écran de piètre qualité.

pdf-check-PdfWarnNonTextContrastConcern-name =
    Contraste non textuel suffisant (avertissement)

pdf-check-PdfWarnNonTextContrastConcern-short-title =
    Contraste non textuel préoccupant

pdf-check-PdfWarnNonTextContrastConcern-what =
    Le contraste mesuré est au seuil ou tout juste au-dessus, ou l'analyse visuelle a signalé des éléments dont la visibilité est douteuse sans être franchement inférieure à 3:1.

pdf-check-PdfWarnNonTextContrastConcern-why =
    Un élément qui atteint tout juste le seuil échoue dès que l'écran, l'éclairage ou l'impression change. C'est la marge qui rend la conception robuste plutôt que conforme sur le papier.

pdf-check-PdfWarnNonTextContrastConcern-who =
    Les personnes malvoyantes, et quiconque lit dans des conditions imparfaites.

pdf-check-PdfWarnAltTextInadequate-name =
    Pertinence du texte de remplacement (avertissement)

pdf-check-PdfWarnAltTextInadequate-short-title =
    Texte de remplacement inadéquat

pdf-check-PdfWarnAltTextInadequate-what =
    Les figures possèdent un texte de remplacement, mais l'analyse par IA l'a jugé inexact, incomplet ou inutile dans le contexte où il apparaît.

pdf-check-PdfWarnAltTextInadequate-why =
    Un texte de remplacement qui existe sans décrire l'image est pire qu'un texte manifestement absent : rien n'indique à la personne qui lit qu'il lui manque quelque chose.

pdf-check-PdfWarnAltTextInadequate-who =
    Les personnes utilisant un lecteur d'écran, et quiconque consulte le document sans les images.

pdf-check-PdfInfoAltTextAdequacyNotAssessed-name =
    Pertinence du texte de remplacement (non évaluée)

pdf-check-PdfInfoAltTextAdequacyNotAssessed-short-title =
    Pertinence du texte de remplacement non évaluée

pdf-check-PdfInfoAltTextAdequacyNotAssessed-what =
    La qualité des textes de remplacement n'a pas été évaluée, car l'analyse par IA n'a pas été exécutée pour ce document.

pdf-check-PdfInfoAltTextAdequacyNotAssessed-why =
    Seul un lecteur capable de voir l'image — humain ou modèle — peut dire si sa description est exacte. Un audit structurel peut confirmer la présence d'un texte de remplacement, rien de plus.

pdf-check-PdfInfoAltTextAdequacyNotAssessed-who =
    Les personnes utilisant un lecteur d'écran, dont cet audit ne peut décrire l'expérience des images.

pdf-check-PdfWarnImagesOfTextDetected-name =
    Images de texte accompagnées d'un texte de remplacement correspondant (avertissement)

pdf-check-PdfWarnImagesOfTextDetected-short-title =
    Images de texte détectées

pdf-check-PdfWarnImagesOfTextDetected-what =
    Du texte a été trouvé à l'intérieur d'images, sans être repris dans le texte de remplacement des figures qui le contiennent.

pdf-check-PdfWarnImagesOfTextDetected-why =
    Le texte incrusté dans une image ne se redimensionne pas, ne se redistribue pas, ne change pas de style et ne réagit pas à un thème à fort contraste ; un lecteur d'écran ne peut pas le lire du tout, sauf si le texte de remplacement le répète.

pdf-check-PdfWarnImagesOfTextDetected-who =
    Les personnes utilisant un lecteur d'écran, celles qui agrandissent le texte et celles qui emploient des thèmes de couleurs personnalisés.

pdf-check-PdfInfoImagesOfTextNotAssessed-name =
    Images de texte accompagnées d'un texte de remplacement correspondant (non évaluées)

pdf-check-PdfInfoImagesOfTextNotAssessed-short-title =
    Images de texte non évaluées

pdf-check-PdfInfoImagesOfTextNotAssessed-what =
    La présence de texte dans les images n'a pas été évaluée, car l'analyse par IA n'a pas été exécutée pour ce document.

pdf-check-PdfInfoImagesOfTextNotAssessed-why =
    Lire le texte d'une image exige un lecteur capable de la voir. Rien dans la structure du document n'indique si une image contient du texte.

pdf-check-PdfInfoImagesOfTextNotAssessed-who =
    Les personnes utilisant un lecteur d'écran et celles qui agrandissent le texte.

pdf-check-PdfErrColorSoleIndicator-name =
    La couleur n'est pas le seul indicateur (échec)

pdf-check-PdfErrColorSoleIndicator-short-title =
    La couleur est le seul indicateur

pdf-check-PdfErrColorSoleIndicator-what =
    De l'information est transmise par la seule couleur — un état signalé uniquement en rouge ou en vert, un champ obligatoire marqué par une simple bordure colorée, une série de graphique distinguée par la seule teinte.

pdf-check-PdfErrColorSoleIndicator-why =
    La couleur n'est pas accessible à tout le monde. Les personnes daltoniennes, celles qui utilisent un thème à fort contraste et quiconque lit une impression en noir et blanc reçoivent la mise en page sans le sens.

pdf-check-PdfErrColorSoleIndicator-who =
    Les personnes ayant une déficience de la vision des couleurs, celles qui utilisent des thèmes personnalisés, et quiconque lit une copie en noir et blanc.

pdf-check-PdfWarnColorPrimaryIndicator-name =
    La couleur n'est pas le seul indicateur (avertissement)

pdf-check-PdfWarnColorPrimaryIndicator-short-title =
    La couleur est l'indicateur principal

pdf-check-PdfWarnColorPrimaryIndicator-what =
    La couleur est le principal moyen de transmettre certaines informations, un second signal existant mais restant faible — une différence de forme ou de formulation qui risque de passer inaperçue.

pdf-check-PdfWarnColorPrimaryIndicator-why =
    Un signal secondaire n'aide que si on le remarque. Une distinction qui existe techniquement mais se lit comme un hasard laisse la personne dépendante de la couleur malgré tout.

pdf-check-PdfWarnColorPrimaryIndicator-who =
    Les personnes ayant une déficience de la vision des couleurs et celles qui utilisent des thèmes personnalisés.

pdf-check-PdfInfoColorUseNotAssessed-name =
    La couleur n'est pas le seul indicateur (non évalué)

pdf-check-PdfInfoColorUseNotAssessed-short-title =
    Usage de la couleur non évalué

pdf-check-PdfInfoColorUseNotAssessed-what =
    La question de savoir si la couleur porte seule du sens n'a pas été évaluée, car l'analyse par IA n'a pas été exécutée pour ce document.

pdf-check-PdfInfoColorUseNotAssessed-why =
    En juger exige un lecteur capable de voir la page : le même texte rouge constitue un échec dans un document et une décoration dans un autre, et seul le contexte tranche.

pdf-check-PdfInfoColorUseNotAssessed-who =
    Les personnes ayant une déficience de la vision des couleurs et celles qui utilisent des thèmes personnalisés.
