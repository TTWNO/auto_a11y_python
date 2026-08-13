"""Which part of a document each check is about.

The executive summary's "issues by area" chart needs to group findings
into something a reader can act on — "six problems with your tables" is
a task, "six problems" is not.

pdfMax keeps a hand-written name-to-area dict (line ~9004). A third of
its checks are missing from it and land in an "Other" bucket that is
routinely the largest bar on its own chart. This map instead follows the
engine's own module structure — every check already lives in exactly one
subject module, so the grouping is one that cannot drift from where the
code is. :func:`area_for` covers every check the engine emits, and
``tests/pdf/test_check_areas.py`` fails if a new one arrives without a
home.
"""
from __future__ import annotations

__all__ = ["AREA_LABELS", "CHECK_AREAS", "area_for"]

#: Area key to its Fluent message id. Keys are stable; the labels they
#: resolve to are translated.
AREA_LABELS: dict[str, str] = {
    "document-setup": "pdf-report-area-document-setup",
    "structure": "pdf-report-area-structure",
    "headings": "pdf-report-area-headings",
    "lists": "pdf-report-area-lists",
    "tables": "pdf-report-area-tables",
    "images": "pdf-report-area-images",
    "links": "pdf-report-area-links",
    "forms": "pdf-report-area-forms",
    "annotations": "pdf-report-area-annotations",
    "interactive": "pdf-report-area-interactive",
    "language": "pdf-report-area-language",
    "fonts": "pdf-report-area-fonts",
    "colour": "pdf-report-area-colour",
}

#: Check name to area key. Generated from the check modules; see
#: the module docstring for why this is not pdfMax's dict.
CHECK_AREAS: dict[str, str] = {
    'Abbreviations have /E expansion': "language",
    'Accessibility permission not restricted': "document-setup",
    'Accessible authentication': "forms",
    'All content is tagged or artifact': "structure",
    'All content tagged': "structure",
    'All fonts embedded': "fonts",
    'Alt text does not hide interactive elements': "images",
    'Alt text free of redundant role text': "images",
    'Alt text on all Figure/Art tags': "images",
    'Annotation contents language determinable': "language",
    'Annotation tab order on all annotated pages': "annotations",
    'Artifact classification subtypes': "structure",
    'Artifact not inside tagged content': "structure",
    'Associated Files property on embedded content': "structure",
    'Bookmarks present': "links",
    'CID font GID mapping': "fonts",
    'CMap WMode consistency': "fonts",
    'CMap resources valid': "fonts",
    'Complex table headers association': "tables",
    'Correct nesting': "structure",
    'Cross-language link targets identified': "links",
    'Document has a text layer': "document-setup",
    'Document language set': "language",
    'Document metadata language determinable': "language",
    'Document title set': "document-setup",
    'Dragging movement alternatives': "interactive",
    'Embedded files have F and UF keys': "structure",
    'Figure elements have BBox attribute': "images",
    'File attachment annotations valid': "annotations",
    'Focus indicator visibility': "interactive",
    'Focus not obscured': "interactive",
    'Font encoding consistency': "fonts",
    'Font faces readable': "fonts",
    'Font glyph widths consistent': "fonts",
    'Font size ratio (magnification)': "fonts",
    'Font sizes accessible': "fonts",
    'Form XObjects with MCIDs not reused': "structure",
    'Form field names unique': "forms",
    'Form field tooltip language determinable': "language",
    'Form fields labeled': "forms",
    'Form fields tagged in structure': "forms",
    'Form page tab order': "forms",
    'Formula Unicode mapping valid': "structure",
    'Formula elements have alt text or ActualText': "structure",
    'Heading hierarchy valid': "headings",
    'Heading size hierarchy': "headings",
    'Identity CMap has ToUnicode': "fonts",
    'Interactive element target size': "interactive",
    'Italic text usage': "fonts",
    'Label in Name': "interactive",
    'Lang values are valid BCP 47': "language",
    'Language of parts markup': "language",
    'Line height accessible': "fonts",
    'Link alt text is descriptive': "links",
    'Link annotations have Contents key': "annotations",
    'Link annotations have content': "annotations",
    'Link annotations inside Link tags': "annotations",
    'List item labels': "lists",
    'List nesting valid': "lists",
    'List structure valid': "lists",
    'MathML associated with Formula elements': "structure",
    'Media clip alt text present': "annotations",
    'Media clip annotations have alt text': "annotations",
    'Media clip content type present': "annotations",
    'Metadata completeness': "document-setup",
    'Multimedia annotations tagged': "annotations",
    'New PDF 2.0 structure elements used correctly': "document-setup",
    'No .notdef glyph references': "fonts",
    'No .notdef in Differences array': "fonts",
    'No Reference XObjects': "structure",
    'No TrapNet annotations': "annotations",
    'No XFA forms present': "forms",
    'No circular role mappings': "structure",
    'No empty lists': "lists",
    'No empty tables': "tables",
    'No empty tags': "structure",
    'No mixed heading tag types': "headings",
    'No multiple headings per node': "headings",
    'No non-standard annotation subtypes': "annotations",
    'No suspect tags': "document-setup",
    'Non-link/widget annotations tagged': "annotations",
    'Non-symbolic TrueType Latin mapping': "fonts",
    'Non-text contrast sufficient': "colour",
    'Note tags have unique IDs': "structure",
    'Optional content groups have Name': "structure",
    'Optional content has no AS entry': "structure",
    'PDF 2.0 namespace in structure tree': "document-setup",
    'PDF header and catalog version consistent': "document-setup",
    'PDF is tagged': "document-setup",
    'PDF/UA identifier': "document-setup",
    'PDF/UA-2 accessibility declarations in XMP': "document-setup",
    'PDF/UA-2 heading hierarchy': "structure",
    'PDF/UA-2 requires PDF 2.0': "document-setup",
    'Page labels consistent': "document-setup",
    'PrinterMark annotations not in structure': "annotations",
    'Pronunciation hints for abbreviations': "language",
    'Reading order matches visual layout': "structure",
    'Redundant entry in forms': "forms",
    'Required fields flagged': "forms",
    'Required fields visually indicated': "forms",
    'Role mapping valid': "structure",
    'Ruby structure valid': "structure",
    'Standard tags not remapped': "structure",
    'Structure destinations for intra-document links': "structure",
    'Structure tree exists': "structure",
    'TOC structure valid': "structure",
    'Tab order follows structure': "structure",
    'Table captions': "tables",
    'Table header scope defined': "tables",
    'Table headers defined': "tables",
    'Table regularity': "tables",
    'Table structure sections': "tables",
    'Tagged content not inside artifact': "structure",
    'Text alignment accessible': "fonts",
    'Text contrast (WCAG AA)': "colour",
    'Text contrast (WCAG AAA)': "colour",
    'Text rotation accessible': "fonts",
    'Unicode mapping (ToUnicode)': "fonts",
    'Untagged lists detected': "lists",
    'Valid Unicode values': "fonts",
    'Visible annotations have alt descriptions': "annotations",
    'Warichu structure valid': "structure",
    'Widget annotations inside Form tags': "forms",
    'XMP dc:title present': "document-setup",
    'XMP metadata stream present': "document-setup",
}


def area_for(check_name: str) -> str | None:
    """The area a check belongs to, or ``None`` if it has no home.

    ``None`` rather than a catch-all: a check with no area is a gap in
    this map, and burying it in "Other" is how pdfMax's chart came to be
    dominated by a bucket that means nothing.
    """
    return CHECK_AREAS.get(check_name)
