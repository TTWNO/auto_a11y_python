"""Load a .docx over a UNO socket, build its table-of-contents field, and
save it back — so the TOC is baked in with real page numbers and hyperlinks
instead of a "right-click to update" placeholder.

Run with LibreOffice's bundled Python against a soffice instance started with
`--accept="socket,host=localhost,port=2002;urp;StarOffice.ComponentContext"`:

    /Applications/LibreOffice.app/Contents/Resources/python \
        scripts/lo_build_toc.py file.docx [file.pdf]

Passing a second argument also exports a tagged PDF/UA copy.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import uno
from com.sun.star.beans import PropertyValue


def _prop(name: str, value: object) -> PropertyValue:
    p = PropertyValue()
    p.Name = name
    p.Value = value
    return p


def connect(port: int = 2002, tries: int = 20):
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local
    )
    url = (
        f"uno:socket,host=localhost,port={port};urp;"
        "StarOffice.ComponentContext"
    )
    last: Exception | None = None
    for _ in range(tries):
        try:
            return resolver.resolve(url)
        except Exception as exc:  # noqa: BLE001 — retry until listener is up
            last = exc
            time.sleep(0.5)
    raise SystemExit(f"could not connect to soffice on {port}: {last}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    docx = Path(sys.argv[1]).resolve()
    pdf = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None

    ctx = connect()
    smgr = ctx.ServiceManager
    desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)

    doc = desktop.loadComponentFromURL(
        docx.as_uri(), "_blank", 0, (_prop("Hidden", True),)
    )
    try:
        try:
            doc.refresh()
        except Exception:
            pass
        indexes = doc.getDocumentIndexes()
        for i in range(indexes.getCount()):
            indexes.getByIndex(i).update()

        doc.store()  # save the docx with the built TOC
        print(f"updated TOC in {docx}")

        if pdf is not None:
            filter_data = uno.Any(
                "[]com.sun.star.beans.PropertyValue",
                (_prop("UseTaggedPDF", True), _prop("PDFUACompliance", True)),
            )
            doc.storeToURL(
                pdf.as_uri(),
                (
                    _prop("FilterName", "writer_pdf_Export"),
                    _prop("Overwrite", True),
                    _prop("FilterData", filter_data),
                ),
            )
            print(f"wrote {pdf}")
    finally:
        doc.close(False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
