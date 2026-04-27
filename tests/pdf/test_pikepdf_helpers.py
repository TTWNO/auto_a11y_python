"""Tests for the typed pikepdf narrowing helpers."""
from __future__ import annotations

import pikepdf

from auto_a11y.pdf.audit.pikepdf_helpers import (
    as_string,
    get_array,
    get_dict,
    get_int,
    get_name,
    get_string,
    resolve_object,
)


def _make_pdf() -> pikepdf.Pdf:
    """Return a single-page Pdf whose Root is populated by callers."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    return pdf


# ─── get_name ────────────────────────────────────────────────────────────────


def test_get_name_returns_name_when_present() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestName"] = pikepdf.Name("/Foo")
    result = get_name(pdf.Root, "/TestName")
    assert result is not None
    assert isinstance(result, pikepdf.Name)
    assert str(result) == "/Foo"


def test_get_name_returns_none_when_value_is_wrong_type() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestName"] = pikepdf.String("not-a-name")
    assert get_name(pdf.Root, "/TestName") is None


def test_get_name_returns_none_when_key_absent() -> None:
    pdf = _make_pdf()
    assert get_name(pdf.Root, "/Missing") is None


# ─── get_string ──────────────────────────────────────────────────────────────


def test_get_string_returns_str_when_present() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestStr"] = pikepdf.String("hello world")
    result = get_string(pdf.Root, "/TestStr")
    assert result == "hello world"


def test_get_string_returns_none_when_value_is_wrong_type() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestStr"] = pikepdf.Name("/NotAString")
    assert get_string(pdf.Root, "/TestStr") is None


def test_get_string_returns_none_when_key_absent() -> None:
    pdf = _make_pdf()
    assert get_string(pdf.Root, "/Missing") is None


# ─── get_array ───────────────────────────────────────────────────────────────


def test_get_array_returns_array_when_present() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestArr"] = pikepdf.Array([1, 2, 3])
    result = get_array(pdf.Root, "/TestArr")
    assert result is not None
    assert isinstance(result, pikepdf.Array)
    assert len(result) == 3


def test_get_array_returns_none_when_value_is_wrong_type() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestArr"] = pikepdf.String("not an array")
    assert get_array(pdf.Root, "/TestArr") is None


def test_get_array_returns_none_when_key_absent() -> None:
    pdf = _make_pdf()
    assert get_array(pdf.Root, "/Missing") is None


# ─── get_dict ────────────────────────────────────────────────────────────────


def test_get_dict_returns_dict_when_present() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestDict"] = pikepdf.Dictionary({"/Inner": pikepdf.Name("/Bar")})
    result = get_dict(pdf.Root, "/TestDict")
    assert result is not None
    assert isinstance(result, pikepdf.Dictionary)


def test_get_dict_returns_none_when_value_is_wrong_type() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestDict"] = pikepdf.Array([1, 2])
    assert get_dict(pdf.Root, "/TestDict") is None


def test_get_dict_returns_none_when_key_absent() -> None:
    pdf = _make_pdf()
    assert get_dict(pdf.Root, "/Missing") is None


# ─── get_int ─────────────────────────────────────────────────────────────────


def test_get_int_returns_int_when_present() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestInt"] = 42
    assert get_int(pdf.Root, "/TestInt") == 42


def test_get_int_truncates_float_value() -> None:
    """Floats stored as PDF numbers coerce to int via int()."""
    pdf = _make_pdf()
    pdf.Root["/TestFloat"] = 3.7
    assert get_int(pdf.Root, "/TestFloat") == 3


def test_get_int_returns_none_when_value_is_wrong_type() -> None:
    pdf = _make_pdf()
    pdf.Root["/TestInt"] = pikepdf.String("not a number")
    assert get_int(pdf.Root, "/TestInt") is None


def test_get_int_returns_none_when_key_absent() -> None:
    pdf = _make_pdf()
    assert get_int(pdf.Root, "/Missing") is None


# ─── resolve_object ──────────────────────────────────────────────────────────


def test_resolve_object_returns_same_object() -> None:
    """pikepdf 10.x resolves indirect refs transparently; helper is a pass-through."""
    pdf = _make_pdf()
    pdf.Root["/TestName"] = pikepdf.Name("/Foo")
    val = pdf.Root["/TestName"]
    resolved = resolve_object(val)
    assert resolved == val
    assert isinstance(resolved, pikepdf.Name)


def test_resolve_object_handles_indirect_dictionary() -> None:
    """An indirect Dictionary (e.g. Root) round-trips unchanged."""
    pdf = _make_pdf()
    root = pdf.Root
    assert root.is_indirect
    resolved = resolve_object(root)
    # Same object semantics: keys() should match.
    assert set(resolved.keys()) == set(root.keys())


# ─── as_string ───────────────────────────────────────────────────────────────


def test_as_string_for_pikepdf_string() -> None:
    pdf = _make_pdf()
    pdf.Root["/Test"] = pikepdf.String("greetings")
    val = pdf.Root["/Test"]
    assert as_string(val) == "greetings"


def test_as_string_for_pikepdf_name_strips_slash() -> None:
    pdf = _make_pdf()
    pdf.Root["/Test"] = pikepdf.Name("/Foo")
    val = pdf.Root["/Test"]
    assert as_string(val) == "Foo"


def test_as_string_for_integer_returns_decimal_repr() -> None:
    pdf = _make_pdf()
    pdf.Root["/Test"] = pikepdf.Array([42])
    val = pdf.Root["/Test"][0]
    assert as_string(val) == "42"


def test_as_string_for_dictionary_does_not_raise() -> None:
    """Diagnostic fallback: arbitrary objects render via str() without crashing."""
    pdf = _make_pdf()
    pdf.Root["/Test"] = pikepdf.Dictionary({"/Inner": pikepdf.Name("/Bar")})
    val = pdf.Root["/Test"]
    rendered = as_string(val)
    assert isinstance(rendered, str)
    assert len(rendered) > 0
