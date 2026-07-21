"""Export docs/USER_GUIDE.md to professional Word and tagged PDF documents.

Adds front matter (title page with the CNIB Access Labs logo), numbered
headings, a page break before every top-level section, and the Access Labs
logo as a bottom-right footer watermark on every page (via
docs/templates/reference.docx).

Requires: pandoc, LibreOffice (soffice), and the repo checked out.

Usage:  python scripts/export_user_guide.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUIDE = REPO / "docs" / "USER_GUIDE.md"
REFERENCE = REPO / "docs" / "templates" / "reference.docx"
LOGO = REPO / "docs" / "images" / "cnib-access-labs-logo.png"
OUT_DOCX = REPO / "docs" / "Auto_A11y_User_Guide.docx"
OUT_PDF = REPO / "docs" / "Auto_A11y_User_Guide.pdf"

SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
PDF_FILTER = (
    'pdf:writer_pdf_Export:{"UseTaggedPDF":{"type":"boolean","value":"true"},'
    '"PDFUACompliance":{"type":"boolean","value":"true"}}'
)

PAGE_BREAK = '\n```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```\n'


def build_body(source: str) -> str:
    lines = source.splitlines()
    # Drop the H1 title — the metadata title block replaces it
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    body = "\n".join(lines)

    # The logo sits on the title page, under the metadata title block.
    # The trailing backslash keeps the image inline so pandoc does not
    # promote it to a captioned figure (alt text is preserved).
    logo_md = (
        "![CNIB Access Labs logo](images/cnib-access-labs-logo.png)"
        "{width=2.8in}\\\n\n"
    )
    body = logo_md + body

    # Contents heading is unnumbered so section numbers match its list
    body = body.replace("## Contents\n", "## Contents {.unnumbered}\n", 1)

    # The Markdown keeps manual "N." prefixes on section headings so GitHub
    # anchor links resolve; pandoc's --number-sections adds its own numbers,
    # so strip the manual prefix here to avoid "1 1." double numbering.
    body = re.sub(r"^(#{2,})\s+\d+\.\s+", r"\1 ", body, flags=re.MULTILINE)

    # Remove the horizontal rules — page breaks take over the separation
    body = re.sub(r"^---$\n", "", body, flags=re.MULTILINE)

    # Page break before every top-level section (including Contents)
    body = re.sub(r"^## ", PAGE_BREAK + "## ", body, flags=re.MULTILINE)
    return body


def main() -> None:
    source = GUIDE.read_text(encoding="utf-8")
    body = build_body(source)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(body)
        tmp_md = tf.name

    subprocess.run(
        [
            "pandoc", tmp_md,
            "-o", str(OUT_DOCX),
            "--reference-doc", str(REFERENCE),
            "--number-sections",
            "--shift-heading-level-by=-1",
            "--metadata", "title=Auto A11y User Guide",
            "--metadata", "subtitle=Desktop Application User Manual",
            "--metadata", "author=CNIB Access Labs",
            "--metadata", f"date={date.today().strftime('%B %Y')}",
            "--metadata", "lang=en",
            "--resource-path", str(REPO / "docs"),
        ],
        check=True,
    )
    print(f"wrote {OUT_DOCX}")

    subprocess.run(
        [
            SOFFICE, "--headless",
            "--convert-to", PDF_FILTER,
            "--outdir", str(OUT_PDF.parent),
            str(OUT_DOCX),
        ],
        check=True,
    )
    print(f"wrote {OUT_PDF}")


if __name__ == "__main__":
    sys.exit(main())
