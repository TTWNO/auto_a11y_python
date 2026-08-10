"""Tests for word-frequency language detection.

The detector exists to decide whether it is safe to write ``/Lang`` into a
document. Its useful property is therefore not accuracy on clear prose but
restraint on everything else: a URL, an address block, or a fifty-fifty
sample must come back as something other than a language, because callers
turn a language verdict into a claim inside the file.
"""
from __future__ import annotations

from auto_a11y.pdf.language import detect_language_from_text


def test_detects_english_prose() -> None:
    guess, confidence = detect_language_from_text(
        "The report is available on the website and it can be read by"
        + " anyone who would like to see what we have said about this."
    )

    assert guess == "en"
    assert confidence > 0.1


def test_detects_french_prose() -> None:
    guess, confidence = detect_language_from_text(
        "Le rapport est disponible sur le site et il peut être lu par"
        + " tous ceux qui veulent savoir ce que nous avons dit à ce sujet."
    )

    assert guess == "fr"
    assert confidence > 0.1


def test_short_url_is_not_reported_as_a_language() -> None:
    guess, _ = detect_language_from_text("https://cnib.ca/reports")

    assert guess == "url"


def test_short_email_is_not_reported_as_a_language() -> None:
    guess, _ = detect_language_from_text("contact us: info@cnib.ca")

    assert guess == "email"


def test_a_url_inside_real_prose_does_not_win() -> None:
    # Above the short-text threshold the surrounding prose should decide,
    # otherwise any document with a footer link would be unclassifiable.
    guess, _ = detect_language_from_text(
        "The full report and all of the supporting data that we have"
        + " collected for it can be found at https://cnib.ca/reports today."
    )

    assert guess == "en"


def test_empty_and_trivial_input_is_unknown() -> None:
    assert detect_language_from_text("")[0] == "unknown"
    assert detect_language_from_text("   ")[0] == "unknown"
    assert detect_language_from_text("ab")[0] == "unknown"


def test_text_with_no_recognisable_function_words_is_unknown() -> None:
    guess, confidence = detect_language_from_text(
        "zzz qqq xxx vvv www kkk jjj hhh ggg fff ddd sss aaa ppp ooo"
    )

    assert guess == "unknown"
    assert confidence == 0.0
