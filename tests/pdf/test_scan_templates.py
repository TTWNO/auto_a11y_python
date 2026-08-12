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
