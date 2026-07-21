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
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

REPO = Path(__file__).resolve().parent.parent
GUIDE = REPO / "docs" / "USER_GUIDE.md"
REFERENCE = REPO / "docs" / "templates" / "reference.docx"
LOGO = REPO / "docs" / "images" / "cnib-access-labs-logo.png"
OUT_DOCX = REPO / "docs" / "Auto_A11y_User_Guide.docx"
OUT_PDF = REPO / "docs" / "Auto_A11y_User_Guide.pdf"

LO_DIR = "/Applications/LibreOffice.app/Contents"
SOFFICE = f"{LO_DIR}/MacOS/soffice"
LO_PYTHON = f"{LO_DIR}/Resources/python"
BUILD_TOC = REPO / "scripts" / "lo_build_toc.py"
UNO_PORT = 2002

PAGE_BREAK = '\n```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```\n'

# A real Word table-of-contents field. `\o "1-3"` lists Heading 1-3, `\h`
# makes entries hyperlinks, `\u` uses outline levels, and w:dirty="true"
# tells the reader (LibreOffice, during PDF conversion) to recompute it —
# which fills in the real page numbers. The "Contents" label uses outline
# level 9 (body text) so it does not list itself.
TOC_BLOCK = r'''
```{=openxml}
<w:p>
  <w:pPr><w:outlineLvl w:val="9"/><w:spacing w:after="240"/></w:pPr>
  <w:r><w:rPr><w:b/><w:sz w:val="40"/><w:color w:val="2E5496"/></w:rPr><w:t>Contents</w:t></w:r>
</w:p>
<w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/><w:docPartUnique/></w:docPartObj></w:sdtPr><w:sdtContent>
<w:p>
  <w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>
  <w:r><w:instrText xml:space="preserve"> TOC \o "1-3" \h \z \u </w:instrText></w:r>
  <w:r><w:fldChar w:fldCharType="separate"/></w:r>
  <w:r><w:t>Update this field (select all, then F9) to build the table of contents.</w:t></w:r>
  <w:r><w:fldChar w:fldCharType="end"/></w:r>
</w:p>
</w:sdtContent></w:sdt>
```
'''


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

    # Replace the hand-written Contents list (kept in the Markdown so GitHub
    # anchor links work) with a real, auto-updating Word TOC field. The list
    # runs from the "## Contents" heading to just before the next "## ".
    body = re.sub(
        r"^## Contents\n.*?(?=^## )",
        lambda _: TOC_BLOCK.strip("\n") + "\n\n",
        body,
        count=1,
        flags=re.DOTALL | re.MULTILINE,
    )

    # The Markdown keeps manual "N." prefixes on section headings so GitHub
    # anchor links resolve; pandoc's --number-sections adds its own numbers,
    # so strip the manual prefix here to avoid "1 1." double numbering.
    body = re.sub(r"^(#{2,})\s+\d+\.\s+", r"\1 ", body, flags=re.MULTILINE)

    # Remove the horizontal rules — page breaks take over the separation
    body = re.sub(r"^---$\n", "", body, flags=re.MULTILINE)

    # Page break before every top-level section, and before the TOC block so
    # the table of contents starts on its own page after the title page.
    body = re.sub(r"^## ", PAGE_BREAK + "## ", body, flags=re.MULTILINE)
    body = body.replace("```{=openxml}\n<w:p>\n  <w:pPr><w:outlineLvl",
                        PAGE_BREAK + "\n```{=openxml}\n<w:p>\n  <w:pPr><w:outlineLvl", 1)
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

    # Build the TOC field (real page numbers + hyperlinks) into the docx and
    # export the tagged PDF. A plain `soffice --convert-to` leaves the TOC as
    # an unbuilt placeholder, so we drive LibreOffice over a UNO socket to
    # update the document indexes first (scripts/lo_build_toc.py).
    with _soffice_listener():
        subprocess.run(
            [LO_PYTHON, str(BUILD_TOC), str(OUT_DOCX), str(OUT_PDF)],
            check=True,
        )
    print(f"wrote {OUT_PDF}")


@contextmanager
def _soffice_listener() -> Iterator[None]:
    """Run a headless soffice UNO listener for the duration of the block."""
    profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    proc = subprocess.Popen(
        [
            SOFFICE, "--headless", "--invisible", "--nologo",
            "--nofirststartwizard", "--norestore",
            f"-env:UserInstallation=file://{profile}",
            f"--accept=socket,host=localhost,port={UNO_PORT};urp;"
            "StarOffice.ComponentContext",
        ]
    )
    try:
        time.sleep(6)  # give the listener time to bind the socket
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
