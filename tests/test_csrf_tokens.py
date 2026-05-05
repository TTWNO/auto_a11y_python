"""Static guard ensuring every POST form template includes a CSRF token.

Flask-WTF's CSRFProtect extension rejects any POST/PUT/PATCH/DELETE that
does not carry a valid `csrf_token`. A template author who forgets the
hidden input ships a form that always 400s with "Missing CSRF Token" —
a class of bug we have hit before. This test scans every Jinja template
under `auto_a11y/web/templates/` and fails if any non-exempt POST form
omits the token.
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / 'auto_a11y' / 'web' / 'templates'

# Blueprints that are exempted from CSRF in `auto_a11y/web/app.py` — templates
# rendered exclusively by these blueprints don't need a token. Keep in sync
# with the `csrf.exempt(...)` calls in app.py.
EXEMPT_TEMPLATE_DIRS: frozenset[str] = frozenset({'public', 'demo'})

_FORM_RE = re.compile(
    r'<form\b[^>]*\bmethod\s*=\s*["\']?post["\']?[^>]*>(.*?)</form>',
    re.IGNORECASE | re.DOTALL,
)
_CSRF_RE = re.compile(r'name=["\']csrf_token["\']', re.IGNORECASE)


def _is_exempt(template: Path) -> bool:
    """Return True if the template lives under a CSRF-exempt blueprint dir."""
    rel = template.relative_to(TEMPLATES_DIR)
    return len(rel.parts) > 1 and rel.parts[0] in EXEMPT_TEMPLATE_DIRS


def _find_offending_forms(template: Path) -> list[int]:
    """Return 1-based line numbers of POST forms in `template` missing csrf_token."""
    text = template.read_text(encoding='utf-8')
    offenders: list[int] = []
    for match in _FORM_RE.finditer(text):
        body = match.group(1)
        if _CSRF_RE.search(body):
            continue
        line = text.count('\n', 0, match.start()) + 1
        offenders.append(line)
    return offenders


def test_every_post_form_has_csrf_token() -> None:
    """Every <form method="post"> in a non-exempt template has a csrf_token field.

    Why: Flask-WTF CSRFProtect is enabled globally in app.py and only the
    `api`, `demo`, and `public` blueprints are exempted. Any other POST form
    that omits `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>`
    will 400 at submit with "Missing CSRF Token" — the recordings upload
    regression that prompted this test.
    """
    assert TEMPLATES_DIR.is_dir(), f'templates dir not found: {TEMPLATES_DIR}'

    failures: list[str] = []
    for template in sorted(TEMPLATES_DIR.rglob('*.html')):
        if _is_exempt(template):
            continue
        for line in _find_offending_forms(template):
            rel = template.relative_to(TEMPLATES_DIR.parent.parent.parent)
            failures.append(f'{rel}:{line}')

    if failures:
        joined = '\n  '.join(failures)
        snippet = '<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>'
        message = (
            f'POST form(s) missing `<input name="csrf_token" ...>`.'
            + f' Add `{snippet}` inside each form, or exempt the blueprint in app.py:'
            + f'\n  {joined}'
        )
        raise AssertionError(message)
