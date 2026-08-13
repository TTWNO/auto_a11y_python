"""Every check belongs to an area, and the map cannot silently rot.

The executive summary groups findings by the part of the document they
concern, because "six problems with your tables" is a task and "six
problems" is not.

pdfMax keeps a hand-written name-to-area dict. A third of its checks are
missing from it and fall into an "Other" bucket, which is routinely the
largest bar on its own chart — the screenshot that prompted this work
shows Other at 9, ahead of every real area. This map follows the engine's
module structure instead, and this test is what keeps it complete: add a
check without giving it an area and the suite says so.
"""
from __future__ import annotations

import pathlib

import pytest

from auto_a11y.pdf.audit.check_areas import AREA_LABELS, CHECK_AREAS, area_for
from auto_a11y.pdf.audit.pipeline import run_audit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CORPUS = REPO_ROOT / "docs" / "Auto_A11y_API_Guide.pdf"


def test_every_area_key_has_a_label() -> None:
    assert set(CHECK_AREAS.values()) <= set(AREA_LABELS)


def test_an_unknown_check_has_no_area() -> None:
    """``None``, not a catch-all — see the module docstring."""
    assert area_for("Not a real check") is None


def test_area_labels_resolve_in_both_languages() -> None:
    from flask import Flask

    from auto_a11y.web.fluent import force_locale, ftl, init_fluent

    app = Flask(__name__)
    init_fluent(app)
    missing: list[str] = []
    with app.test_request_context("/"):
        for locale in ("en", "fr"):
            with force_locale(locale):
                for message_id in AREA_LABELS.values():
                    if str(ftl(message_id)) == message_id:
                        missing.append(f"{locale}:{message_id}")

    assert not missing, f"unresolved area labels: {missing}"


@pytest.mark.skipif(not _CORPUS.is_file(), reason="corpus PDF absent")
def test_every_check_the_engine_emits_has_an_area() -> None:
    """The completeness guard. A new check with no area would otherwise
    vanish from the chart, silently."""
    audit = run_audit(_CORPUS)

    homeless = sorted({
        check.name for check in audit.check_results
        if area_for(check.name) is None
    })

    assert not homeless, f"checks with no area: {homeless}"
