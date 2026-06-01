"""Regression tests guarding against stored XSS in the project HTML report.

Project and website names/descriptions/urls are user-entered free text. They
are interpolated into the generated HTML report and MUST be HTML-escaped so an
injected ``<script>`` cannot execute or corrupt the report.
"""
from __future__ import annotations

from typing import cast

from auto_a11y.core.database import Database
from auto_a11y.models import Project, ProjectStatus, Website
from auto_a11y.reporting.project_report import ProjectReport


def _build_report() -> ProjectReport:
    """Construct a ProjectReport with malicious user-supplied fields.

    ``to_html`` only consumes ``report_data`` (built from the project/websites),
    never ``self.database``, so a ``None`` database is sufficient for this test.
    """
    project = Project(
        name='<script>alert("project")</script>',
        description='<script>alert("desc")</script>',
        status=ProjectStatus.ACTIVE,
        config={'wcag_level': 'AA'},
    )

    website = Website(
        project_id='proj-1',
        url='"><img src=x onerror=alert("url")>',
        name='"><img src=x onerror=alert("wsname")>',
    )

    return ProjectReport(
        database=cast(Database, None),
        project=project,
        websites=[website],
        pages_by_website={},
    )


def _build_report_with_wcag_level(wcag_level: str) -> ProjectReport:
    """Construct a ProjectReport with a malicious ``wcag_level`` config value."""
    project = Project(
        name='Safe Project',
        description='Safe description',
        status=ProjectStatus.ACTIVE,
        config={'wcag_level': wcag_level},
    )
    return ProjectReport(
        database=cast(Database, None),
        project=project,
        websites=[],
        pages_by_website={},
    )


def test_wcag_level_is_escaped() -> None:
    html = _build_report_with_wcag_level('<script>alert("wcag")</script>').to_html()

    # Raw script payload from the wcag_level config must not survive.
    assert '<script>alert("wcag")</script>' not in html

    # The escaped form must be present instead.
    assert '&lt;script&gt;alert(&quot;wcag&quot;)&lt;/script&gt;' in html


def test_project_name_and_description_are_escaped() -> None:
    html = _build_report().to_html()

    # Raw script payloads must not survive into the output.
    assert '<script>alert("project")</script>' not in html
    assert '<script>alert("desc")</script>' not in html

    # The escaped form must be present instead.
    assert '&lt;script&gt;alert(&quot;project&quot;)&lt;/script&gt;' in html
    assert '&lt;script&gt;alert(&quot;desc&quot;)&lt;/script&gt;' in html


def test_website_name_and_url_are_escaped() -> None:
    html = _build_report().to_html()

    # Attribute-breakout payload must be neutralised.
    assert '"><img src=x onerror=alert("url")>' not in html
    assert '"><img src=x onerror=alert("wsname")>' not in html

    # Escaped forms present (quote=True escapes the leading double-quote too).
    assert '&lt;img src=x onerror=alert(&quot;url&quot;)&gt;' in html
    assert '&lt;img src=x onerror=alert(&quot;wsname&quot;)&gt;' in html

    # No unescaped <img> injected anywhere.
    assert '<img src=x' not in html
