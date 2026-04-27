"""Typed narrowing wrappers around ``pikepdf.Object``.

pikepdf's Python API exposes a single ``pikepdf.Object`` class for every
PDF object kind. Strict-mode type-checking therefore can't tell whether
``pdf.Root["/StructTreeRoot"]`` is a ``Name``, a ``Dictionary``, or
something else. Caller code that wants concrete types ends up needing
``isinstance`` checks scattered everywhere — exactly the place where the
zero-escape-hatch policy bites hardest.

This module collects those narrowings into a handful of helpers used by
the data-collector modules in Phase 3. Each accessor takes a (possibly
indirect) Dictionary-shaped object plus a key, looks the key up, type
checks the result against the expected concrete type, and either returns
the narrowed value or ``None``. No casts, no ignores.
"""
from __future__ import annotations

import pikepdf


def get_name(obj: pikepdf.Object, key: str) -> pikepdf.Name | None:
    """Return ``obj[key]`` if it exists and is a ``Name``, else ``None``.

    Looks up the key (e.g. ``"/StructTreeRoot"``) on a Dictionary-shaped
    Object. Returns ``None`` if the key is absent or if the value is some
    other PDF type.
    """
    try:
        val = obj[key]
    except KeyError:
        return None
    if isinstance(val, pikepdf.Name):
        return val
    return None


def get_string(obj: pikepdf.Object, key: str) -> str | None:
    """Return ``obj[key]`` coerced to ``str`` if it is a ``String``, else ``None``.

    The PDF ``String`` type covers both literal and hex strings; pikepdf
    decodes them transparently, so ``str(value)`` yields the unicode
    payload.
    """
    try:
        val = obj[key]
    except KeyError:
        return None
    if isinstance(val, pikepdf.String):
        return str(val)
    return None


def get_array(obj: pikepdf.Object, key: str) -> pikepdf.Array | None:
    """Return ``obj[key]`` if it exists and is an ``Array``, else ``None``."""
    try:
        val = obj[key]
    except KeyError:
        return None
    if isinstance(val, pikepdf.Array):
        return val
    return None


def get_dict(obj: pikepdf.Object, key: str) -> pikepdf.Dictionary | None:
    """Return ``obj[key]`` if it exists and is a ``Dictionary``, else ``None``."""
    try:
        val = obj[key]
    except KeyError:
        return None
    if isinstance(val, pikepdf.Dictionary):
        return val
    return None


def get_int(obj: pikepdf.Object, key: str) -> int | None:
    """Return ``obj[key]`` coerced to ``int`` if it is numeric, else ``None``.

    PDF integer values that ride along on a Dictionary come back as plain
    Python ``int`` from pikepdf; PDF reals come back as ``Decimal``-like
    objects (and indirect numerics arrive as ``pikepdf.Object``). Rather
    than enumerate every numeric variant, the helper attempts ``int(val)``
    and treats failure as "not numeric, return None". ``str``/``bytes``
    are explicitly rejected up-front because ``int()`` on a numeric string
    would coerce to a Python int and conceal a type mismatch.

    Coercion semantics: float/Decimal values truncate toward zero (i.e.
    standard ``int()`` behaviour). Callers that need the unrounded value
    should read it themselves.
    """
    try:
        val = obj[key]
    except KeyError:
        return None
    if isinstance(val, (str, bytes, pikepdf.String, pikepdf.Name)):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def resolve_object(obj: pikepdf.Object) -> pikepdf.Object:
    """If ``obj`` is an indirect reference, return the resolved object; else ``obj``.

    pikepdf 10.x resolves indirect references transparently — every read
    through ``__getitem__`` / ``__getattr__`` already follows the indirect
    link and yields the target object. There is therefore no separate
    "force resolve" call to make. This helper is a no-op pass-through that
    exists for explicitness at structure-tree boundaries (where callers
    want to make the resolution step visible) and for forward-compatibility
    if a future pikepdf release changes that posture.
    """
    return obj


def as_string(obj: pikepdf.Object) -> str:
    """Coerce a PDF object to a Python ``str`` for diagnostic rendering.

    * ``pikepdf.String`` returns its decoded unicode value.
    * ``pikepdf.Name`` returns the name without the leading ``/``.
    * Any other object falls back to ``str(obj)``.

    Never raises — used to render arbitrary, possibly-malformed objects
    into log lines and error messages.
    """
    if isinstance(obj, pikepdf.String):
        return str(obj)
    if isinstance(obj, pikepdf.Name):
        # pikepdf renders Names as "/Foo"; strip the leading slash so
        # callers get the bare name.
        rendered = str(obj)
        return rendered[1:] if rendered.startswith("/") else rendered
    return str(obj)
