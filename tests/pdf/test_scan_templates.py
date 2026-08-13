"""Template guards for the standalone scan flow.

Every state-changing form in this app posts one of two ways: through JS to
the csrf-exempt ``/api/v1`` blueprint, or as a plain server-rendered form to
a normal blueprint. The scan flow does the latter, so its forms must carry a
hidden ``csrf_token`` — without it Flask-WTF answers "Bad Request: The CSRF
token is missing" and the user never gets to scan anything.

That is exactly what shipped in the first DMG: both forms were written
without a token, and the end-to-end check that should have caught it had
``WTF_CSRF_ENABLED = False`` set, so it exercised a configuration no user
ever runs.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "auto_a11y" / "web" / "templates" / "pdf_scan"

_FORM_RE = re.compile(r"<form\b.*?</form>", re.S | re.I)
_METHOD_POST_RE = re.compile(r'method="POST"', re.I)
_TOKEN_RE = re.compile(r'name="csrf_token"')


@pytest.mark.parametrize("template", ["select.html", "results.html"])
def test_every_post_form_carries_a_csrf_token(template: str) -> None:
    html = (TEMPLATES / template).read_text(encoding="utf-8")
    post_forms = [f for f in _FORM_RE.findall(html) if _METHOD_POST_RE.search(f)]

    assert post_forms, f"{template} has no POST form — did the flow change?"
    for form in post_forms:
        action = re.search(r'action="([^"]*)"', form)
        assert _TOKEN_RE.search(form), (
            f"POST form in {template} "
            f"(action={action.group(1) if action else '?'}) is missing its "
            "hidden csrf_token input; Flask-WTF will reject the submission"
        )


def test_the_scan_form_posts_a_file() -> None:
    """Guards the enctype — a multipart form without it submits no bytes."""
    html = (TEMPLATES / "select.html").read_text(encoding="utf-8")
    form = next(
        f for f in _FORM_RE.findall(html) if 'id="pdf-scan-form"' in f
    )
    assert 'enctype="multipart/form-data"' in form
    assert 'type="file"' in form


# ---------------------------------------------------------------------------
# File-select layout
# ---------------------------------------------------------------------------


_CSS = (
    REPO_ROOT / "auto_a11y" / "web" / "static" / "css" / "pdf_scan.css"
)
_SELECT = TEMPLATES / "select.html"

#: Anything below this is under the 12px floor the repo's CSS linter
#: enforces — and this is a product for people who need text larger, not
#: smaller. ``--font-size-sm`` is 0.875rem.
_LITERAL_FONT_SIZE = re.compile(r"font-size:\s*([0-9.]+)(rem|px|em)\s*;")


def test_no_font_size_is_a_literal_rather_than_a_token() -> None:
    """Sizes come from tokens.css so the whole app scales together.

    The transcription arrived with pdfMax's own scale — 1.8rem, 0.9rem,
    0.82rem, and on the results screen 0.68rem, around 11px.
    """
    literals = _LITERAL_FONT_SIZE.findall(_CSS.read_text(encoding="utf-8"))

    assert not literals, f"hardcoded font sizes: {literals}"


def test_the_form_centres_its_own_children() -> None:
    """What put the drop zone off-centre.

    The drop zone is ``max-width: 480px`` inside a form as wide as its
    widest hint paragraph. A plain block form leaves it against the left
    edge however well the column around it is centred.
    """
    css = _CSS.read_text(encoding="utf-8")
    rule = css.split(".file-select-form {", 1)[1].split("}", 1)[0]

    assert "align-items: center" in rule
    assert "flex-direction: column" in rule


def test_the_scan_history_is_an_aside() -> None:
    """Related material, not part of the task of choosing a file."""
    html = _SELECT.read_text(encoding="utf-8")

    assert "<aside class=\"file-select-history\"" in html
    assert 'aria-labelledby="pdf-scan-recent-heading"' in html


def test_history_rows_do_not_push_name_and_date_apart() -> None:
    """A gap that grows with the column is a long track at 400% zoom."""
    html = _SELECT.read_text(encoding="utf-8")
    css = _CSS.read_text(encoding="utf-8")
    rule = css.split(".file-select-history-item {", 1)[1].split("}", 1)[0]

    assert "justify-content-between" not in html
    assert "flex-direction: column" in rule


def test_the_two_columns_reflow_into_one() -> None:
    """SC 1.4.10 — 400% zoom shrinks the viewport the same way a narrow
    window does, so the aside has to stop being a column."""
    css = _CSS.read_text(encoding="utf-8")

    assert "@media (max-width: 62rem)" in css
    reflow = css.split("@media (max-width: 62rem) {", 1)[1]
    assert ".file-select-layout" in reflow
    assert "flex-direction: column" in reflow


# ---------------------------------------------------------------------------
# Results sidebar
# ---------------------------------------------------------------------------

_RESULTS = TEMPLATES / "results.html"


def test_the_sidebar_is_a_single_list() -> None:
    """Splitting it would announce as several unrelated lists."""
    html = _RESULTS.read_text(encoding="utf-8")

    assert html.count('id="results-sections-list"') == 1


def test_the_front_matter_links_come_before_the_verdict_marker() -> None:
    """The sidebar reads in the same order as the page.

    Metadata and the summary lead the report, so their links lead the
    list. They used to sit after the verdict headings, which put them
    mid-list while the sections themselves were at the very top.
    """
    html = _RESULTS.read_text(encoding="utf-8")
    nav = html.split('id="results-sections-list"', 1)[1].split("</ul>", 1)[0]

    metadata = nav.index("pdf-report-nav-metadata")
    summary = nav.index("pdf-inventory-exec-summary-heading")
    marker = nav.index("results-sections-verdict-marker")
    first_inventory = nav.index("pdf-inventory-tag-tree-heading")

    assert metadata < summary < marker < first_inventory


def test_the_verdict_headings_are_inserted_at_the_marker() -> None:
    """Appending would put Failures and Warnings after the inventories."""
    js = (
        REPO_ROOT / "auto_a11y" / "web" / "static" / "js"
        / "pdf_scan_results.js"
    ).read_text(encoding="utf-8")

    assert "results-sections-verdict-marker" in js
    assert "insertBefore" in js
