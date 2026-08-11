"""Completeness tests for the check → fix map.

This table is what wires the report to the fixer, and every way it can be
wrong is silent: a fix nothing offers is dead code, a check naming a
fix that does not exist raises only when someone clicks the button, and a
label with no French half shows English in a French UI. All three are
checked here rather than discovered in use.
"""
from __future__ import annotations

import pytest
from flask import Flask

from auto_a11y.pdf.fix import FIX_REGISTRY
from auto_a11y.pdf.fix.catalogue import (
    FIXABLE_CHECKS,
    PENDING_CHECKS,
    UNOFFERED_FIXES,
    fix_for_check,
)
from auto_a11y.pdf.translation.check_mapper import CHECK_CATALOGUE
from auto_a11y.web.fluent import force_locale, ftl, init_fluent


def _engine_check_names() -> set[str]:
    return {row["pdfmax_check_name"] for row in CHECK_CATALOGUE}


def _offered_fix_ids() -> set[str]:
    offered = {entry.fix_id for entry in FIXABLE_CHECKS.values()}
    offered |= {
        entry.warn_fix_id
        for entry in FIXABLE_CHECKS.values()
        if entry.warn_fix_id is not None
    }
    return offered


def test_every_offered_fix_exists() -> None:
    """A check offering a fix the registry lacks fails only on click."""
    missing = sorted(_offered_fix_ids() - set(FIX_REGISTRY))

    assert not missing, f"checks offer fixes that do not exist: {missing}"


def test_every_fix_is_offered_by_some_check() -> None:
    """A fix nothing offers cannot be reached from a report.

    UNOFFERED_FIXES names the deliberate exceptions, so adding a fix
    without wiring it up fails here rather than going unnoticed.
    """
    unreachable = sorted(set(FIX_REGISTRY) - _offered_fix_ids() - UNOFFERED_FIXES)

    assert not unreachable, f"fixes no check offers: {unreachable}"


def test_every_mapped_check_exists_in_the_engine() -> None:
    """A misspelled check name silently offers its fix to nobody.

    PENDING_CHECKS names the checks whose fix is ported but whose check
    has not landed yet, so this distinguishes "not built" from "typo".
    """
    absent = sorted(set(FIXABLE_CHECKS) - _engine_check_names() - PENDING_CHECKS)

    assert not absent, f"mapped checks the engine never emits: {absent}"


def test_pending_checks_are_genuinely_absent() -> None:
    """Stops the pending list outliving the gap it describes."""
    landed = sorted(PENDING_CHECKS & _engine_check_names())

    assert not landed, (
        f"these checks now exist and should leave PENDING_CHECKS: {landed}"
    )


def test_unoffered_fixes_are_genuinely_unoffered() -> None:
    stale = sorted(UNOFFERED_FIXES & _offered_fix_ids())

    assert not stale, (
        f"these fixes are offered and should leave UNOFFERED_FIXES: {stale}"
    )


# ---------------------------------------------------------------------------
# Bilingual labels
# ---------------------------------------------------------------------------

def _label_ids() -> set[str]:
    ids: set[str] = set()
    for entry in FIXABLE_CHECKS.values():
        for field in entry.fields:
            ids.add(field.label_id)
            if field.placeholder_id:
                ids.add(field.placeholder_id)
            if field.help_id:
                ids.add(field.help_id)
            for option in field.options:
                ids.add(option.label_id)
    return ids


@pytest.mark.parametrize("locale", ["en", "fr"])
def test_every_input_label_resolves_in_both_languages(locale: str) -> None:
    """An unresolved id renders as the id itself, in both languages."""
    app = Flask(__name__)
    init_fluent(app)

    unresolved: list[str] = []
    with app.test_request_context("/"), force_locale(locale):
        for message_id in sorted(_label_ids()):
            if str(ftl(message_id)) == message_id:
                unresolved.append(message_id)

    assert not unresolved, f"missing {locale} strings: {unresolved}"


# ---------------------------------------------------------------------------
# fix_for_check
# ---------------------------------------------------------------------------

def test_a_failing_check_offers_its_fix() -> None:
    entry = fix_for_check("PDF is tagged", "FAIL")

    assert entry is not None
    assert entry.fix_id == "fix_mark_info"


def test_a_warning_prefers_the_lesser_remedy() -> None:
    """A title that exists but is not displayed needs only the preference.

    Applying fix_title there would rewrite a title the author had already
    set.
    """
    entry = fix_for_check("Document title set", "WARN")

    assert entry is not None
    assert entry.fix_id == "fix_display_doc_title"


def test_a_warning_without_a_lesser_remedy_offers_the_main_one() -> None:
    entry = fix_for_check("PDF is tagged", "WARN")

    assert entry is not None
    assert entry.fix_id == "fix_mark_info"


@pytest.mark.parametrize("verdict", ["PASS", "NA", "INFO"])
def test_nothing_is_offered_for_a_non_fault(verdict: str) -> None:
    assert fix_for_check("PDF is tagged", verdict) is None


def test_an_unknown_check_offers_nothing() -> None:
    assert fix_for_check("Not a real check", "FAIL") is None
