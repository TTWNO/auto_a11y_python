"""Convert a .docx to a tagged PDF/UA, updating table-of-contents fields.

Run with LibreOffice's bundled Python (which has the `uno` bridge), NOT the
project interpreter:

    /Applications/LibreOffice.app/Contents/Resources/python \
        scripts/lo_docx_to_pdf.py input.docx output.pdf

Plain `soffice --convert-to pdf` does not recompute Word TOC fields, so the
table of contents renders as a "right-click to update" placeholder. This
script loads the document through the UNO API, refreshes fields and updates
every document index (so the TOC gets real, hyperlinked page numbers), then
exports with tagged-PDF / PDF-UA enabled.
"""
from __future__ import annotations

import sys
from pathlib import Path

import officehelper
import uno  # noqa: F401 — required to register UNO types
from com.sun.star.beans import PropertyValue


def _prop(name: str, value: object) -> PropertyValue:
    p = PropertyValue()
    p.Name = name
    p.Value = value
    return p


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src = Path(sys.argv[1]).resolve()
    dst = Path(sys.argv[2]).resolve()

    ctx = officehelper.bootstrap()
    smgr = ctx.getServiceManager()
    desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)

    load_props = (_prop("Hidden", True), _prop("ReadOnly", False))
    doc = desktop.loadComponentFromURL(
        src.as_uri(), "_blank", 0, load_props
    )
    try:
        # Refresh fields, then update every index (the TOC is one) so its
        # entries and page numbers are computed from the laid-out document.
        try:
            doc.refresh()
        except Exception:
            pass
        indexes = doc.getDocumentIndexes()
        for i in range(indexes.getCount()):
            indexes.getByIndex(i).update()

        # PDF/UA (tagged) export options — mirrors the writer_pdf_Export
        # filter data used elsewhere.
        filter_data = uno.Any(
            "[]com.sun.star.beans.PropertyValue",
            (_prop("UseTaggedPDF", True), _prop("PDFUACompliance", True)),
        )
        out_props = (
            _prop("FilterName", "writer_pdf_Export"),
            _prop("Overwrite", True),
            _prop("FilterData", filter_data),
        )
        doc.storeToURL(dst.as_uri(), out_props)
    finally:
        doc.close(False)
    print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
