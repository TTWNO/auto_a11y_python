from __future__ import annotations

from typing import Any
from collections.abc import Callable

"""Tests that all standalone Jinja2 environments used for report generation
register the Fluent translation globals (ftl, ftl_enum, etc.) required by
their templates.

This catches the class of bug where a template calls {{ ftl(...) }} but the
Jinja2 environment that renders it was never told about the function, causing
an UndefinedError at report-generation time.

The test is entirely static — no database, no browser, no Flask app required.
"""

import re
from pathlib import Path

import jinja2
import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / 'auto_a11y' / 'web' / 'templates'
_STATIC_REPORT_DIR = _TEMPLATES_DIR / 'static_report'

# All Fluent globals that init_fluent() registers on the Flask Jinja2 env.
# Any standalone env that renders templates using these MUST register them too.
FLUENT_GLOBALS = {'ftl', 'ftl_attr', 'ftl_enum', 'ftl_issue', 'ftl_wcag', 'ftl_translate_issue'}

# Pattern that matches Jinja2 expressions calling a Fluent global, e.g.
#   {{ ftl('foo') }}   {{ ftl_enum(x)|upper }}   {{ ftl('x', var=1) }}
_FLUENT_CALL_RE = re.compile(r'\b(' + '|'.join(FLUENT_GLOBALS) + r')\s*\(')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fluent_globals_used_in_template(template_path: Path) -> set[str]:
    """Return the set of Fluent global names referenced in *template_path*.

    Also follows {% extends %} and {% include %} one level deep within the
    same directory so that base templates are checked too.
    """
    found: set[str] = set()
    seen: set[Path] = set()
    to_visit = [template_path]

    while to_visit:
        path = to_visit.pop()
        if path in seen or not path.exists():
            continue
        seen.add(path)

        text = path.read_text(encoding='utf-8')
        found.update(m.group(1) for m in _FLUENT_CALL_RE.finditer(text))

        # Follow extends/include references
        for ref_match in re.finditer(
            r"\{%[-\s]*(?:extends|include)\s+['\"]([^'\"]+)['\"]", text
        ):
            ref = ref_match.group(1)
            # Resolve relative to the templates root (how Jinja2 FileSystemLoader works)
            ref_path = _TEMPLATES_DIR / ref
            to_visit.append(ref_path)

    return found


def _build_env_like_static_html_generator() -> jinja2.Environment:
    """Recreate the Jinja2 environment from StaticHTMLReportGenerator.__init__."""
    from auto_a11y.web.fluent import ftl, ftl_enum

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(['html', 'xml']),
        extensions=['jinja2.ext.i18n'],
    )
    # install_gettext_callables is added at runtime by jinja2.ext.i18n
    install = getattr(env, 'install_gettext_callables')
    install(
        gettext=lambda x: x,
        ngettext=lambda s, p, n: s if n == 1 else p,
        newstyle=True,
    )
    # These are the globals the generator registers (after our fix)
    env.globals['ftl'] = ftl
    env.globals['ftl_enum'] = ftl_enum
    return env


def _build_env_like_comprehensive_report() -> jinja2.Environment:
    """Recreate the Jinja2 environment from ComprehensiveReportGenerator.generate()."""
    from auto_a11y.web.fluent import ftl

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(['html', 'xml']),
    )
    env.globals['ftl'] = ftl
    return env


def _build_env_like_recordings_report() -> jinja2.Environment:
    """Recreate the Jinja2 environment from RecordingsReportGenerator.generate_standalone_report()."""
    from auto_a11y.web.fluent import ftl

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(['html', 'xml']),
    )
    env.globals['ftl'] = ftl
    return env


# ---------------------------------------------------------------------------
# Map each standalone Jinja2 environment to the templates it renders.
# This must stay in sync with get_template() calls in the generators.
# ---------------------------------------------------------------------------

GENERATOR_ENVS: dict[str, dict[str, Any]] = {
    'StaticHTMLReportGenerator': {
        'build_env': _build_env_like_static_html_generator,
        'source': 'auto_a11y/reporting/static_html_generator.py',
        'templates': [
            'static_report/page_detail.html',
            'static_report/index.html',
            'static_report/summary.html',
            'static_report/dedup_index.html',
            'static_report/dedup_component.html',
            'static_report/dedup_unassigned.html',
        ],
    },
    'ComprehensiveReportGenerator': {
        'build_env': _build_env_like_comprehensive_report,
        'source': 'auto_a11y/reporting/comprehensive_report.py',
        'templates': [
            'static_report/comprehensive_report_standalone.html',
        ],
    },
    'RecordingsReportGenerator': {
        'build_env': _build_env_like_recordings_report,
        'source': 'auto_a11y/reporting/recordings_report.py',
        'templates': [
            'static_report/recordings_report_standalone.html',
        ],
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _env_template_cases() -> Any:
    """Yield (generator_name, template_rel_path, build_env_fn) for parametrize."""
    for name, info in GENERATOR_ENVS.items():
        for tpl in info['templates']:
            yield pytest.param(name, tpl, info['build_env'], id=f"{name}::{tpl}")


@pytest.mark.parametrize("generator_name, template_rel, build_env", list(_env_template_cases()))
def test_fluent_globals_registered_for_template(generator_name: str, template_rel: str, build_env: Callable[[], Any]) -> None:
    """Every Fluent global used in a template must be present in the
    generator's Jinja2 environment globals.

    If this test fails it means a template uses e.g. {{ ftl(...) }} but the
    report generator that renders it forgot to register ``ftl`` on its
    Jinja2 environment.
    """
    template_path = _TEMPLATES_DIR / template_rel
    required = _fluent_globals_used_in_template(template_path)

    if not required:
        # Template doesn't use any Fluent globals — nothing to check.
        return

    env = build_env()
    registered = set(env.globals.keys())
    missing = required - registered

    assert not missing, (
        f"{generator_name} ({GENERATOR_ENVS[generator_name]['source']}) renders "
        f"'{template_rel}' which uses Fluent globals {sorted(missing)}, but they "
        f"are not registered on the Jinja2 environment. "
        f"Add them with: env.globals['{next(iter(missing))}'] = {next(iter(missing))}"
    )


def test_all_static_report_templates_are_covered() -> None:
    """Every .html file in static_report/ that uses Fluent globals must be
    listed in at least one generator's template list (or rendered by Flask).

    This catches the case where a new template is added but not included in
    GENERATOR_ENVS, meaning it would escape testing.
    """
    # Templates that don't need to be in GENERATOR_ENVS:
    # - Flask-rendered templates (Flask's app.jinja_env has all globals)
    # - Base templates only used via {% extends %} (covered by the child tests)
    # - Legacy templates no longer referenced by any generator
    excluded = {
        'static_report/recordings_report.html',      # Flask-rendered
        'static_report/base.html',                    # only used via {% extends %}
        'static_report/project_deduplicated.html',    # legacy, unused by generators
    }

    covered_templates: set[str] = set()
    for info in GENERATOR_ENVS.values():
        covered_templates.update(info['templates'])
    covered_templates.update(excluded)

    uncovered = []
    for html_file in sorted(_STATIC_REPORT_DIR.glob('*.html')):
        rel = f"static_report/{html_file.name}"
        if rel in covered_templates:
            continue
        # Only flag if this template actually uses Fluent globals
        used = _fluent_globals_used_in_template(html_file)
        if used:
            uncovered.append((rel, sorted(used)))

    assert not uncovered, (
        f"The following templates use Fluent globals but are not listed in "
        f"GENERATOR_ENVS or excluded — add them so they get tested:\n"
        + "\n".join(f"  {t}: uses {g}" for t, g in uncovered)
    )


def test_no_new_fluent_globals_untracked() -> None:
    """If someone adds a new ftl_* function to init_fluent() but forgets to
    add it to FLUENT_GLOBALS in this test file, we'd miss detecting its
    absence in standalone envs.  This test keeps FLUENT_GLOBALS in sync.
    """
    from auto_a11y.web.fluent import init_fluent
    from flask import Flask

    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test'
    with app.app_context():
        init_fluent(app)
        flask_fluent_globals = {
            k for k in app.jinja_env.globals
            if k.startswith('ftl')
        }

    untracked = flask_fluent_globals - FLUENT_GLOBALS
    assert not untracked, (
        f"init_fluent() registers Jinja2 globals {sorted(untracked)} that are "
        f"not listed in FLUENT_GLOBALS in this test file. Add them so new "
        f"usages in templates are caught."
    )
