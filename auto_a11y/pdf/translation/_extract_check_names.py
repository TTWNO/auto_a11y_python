"""One-shot enumeration tool: list every ``(name, result, standard)`` triple
emitted by the audit-engine check functions.

Run this script directly to dump the catalogue scaffolding for review:

.. code-block:: bash

    python -m auto_a11y.pdf.translation._extract_check_names

The script walks every function listed in
:data:`auto_a11y.pdf.audit.checks.ALL_CHECKS`, reads each one's source via
:func:`inspect.getsource`, parses it with :mod:`ast`, and collects every
literal ``CheckResult(...)`` constructor call. Both keyword and positional
forms are supported.

Used at development time (not at runtime) when:

* a new check is added — re-run to copy the new ``(name, result)`` pairs
  into :data:`auto_a11y.pdf.translation.check_mapper.CHECK_CATALOGUE`;
* the completeness regression test in
  ``tests/pdf/test_check_mapper.py`` flags a missing entry — re-run to
  see what's missing.
"""
from __future__ import annotations

import ast
import inspect
from collections.abc import Callable, Iterable

from auto_a11y.pdf.audit.checks import ALL_CHECKS
from auto_a11y.pdf.models import AuditContext, CheckResult


CheckFunction = Callable[[AuditContext], list[CheckResult]]


def _extract_str(
    node: ast.expr, locals_map: dict[str, str] | None = None
) -> str | None:
    """Return the string value of an AST node, or ``None``.

    Handles:
    * :class:`ast.Constant` with ``str`` value (the canonical case);
    * :class:`ast.JoinedStr` (an f-string) where every part is a constant
      string — falls back to the concatenation of the constant parts and
      treats variable interpolations as ``""`` so the resulting prefix
      still matches the literal name pdfMax emits;
    * :class:`ast.Name` referring to a local string assignment captured
      in *locals_map* (e.g. ``name = "Text contrast (WCAG AA)"`` followed
      by ``CheckResult(name=name, ...)``).

    Returns ``None`` for anything else; the caller drops those triples.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("")
        return "".join(parts)
    if isinstance(node, ast.Name) and locals_map is not None:
        return locals_map.get(node.id)
    return None


def _collect_local_strings(fn_node: ast.AST) -> dict[str, str]:
    """Map every ``X = "literal"`` assignment in *fn_node* to its value.

    This lets us resolve ``name = "Text contrast (WCAG AA)"`` followed
    by ``CheckResult(name=name, ...)`` in
    :mod:`auto_a11y.pdf.audit.checks.color_contrast`. Only string
    constants and joined-string-of-constants are captured; anything more
    dynamic stays unresolved and the caller drops those triples.
    """
    out: dict[str, str] = {}
    for sub in ast.walk(fn_node):
        if not isinstance(sub, ast.Assign):
            continue
        # ``a = b = "x"`` would have multiple targets; only single-name
        # targets matter here.
        for target in sub.targets:
            if not isinstance(target, ast.Name):
                continue
            literal = _extract_str(sub.value, locals_map=None)
            if literal is None:
                continue
            out[target.id] = literal
    return out


def _iter_check_result_calls(tree: ast.AST) -> Iterable[ast.Call]:
    """Yield every ``CheckResult(...)`` Call node in *tree*."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "CheckResult":
            yield node


def _extract_triple(
    call: ast.Call, locals_map: dict[str, str]
) -> tuple[str, str, str] | None:
    """Return ``(name, standard, result)`` from a ``CheckResult(...)`` call.

    The audit-engine checks call ``CheckResult`` either:

    * positionally — ``CheckResult(name, standard, result, details)`` —
      with four arguments in that order;
    * by keyword — ``CheckResult(name=..., standard=..., result=...,
      details=...)``.

    *locals_map* is the result of :func:`_collect_local_strings` for the
    surrounding function; it lets the resolver follow ``name=name``
    references back to a literal.

    Returns ``None`` when any of the three target fields can't be
    resolved to a string literal.
    """
    name: str | None = None
    standard: str | None = None
    result: str | None = None

    # Positional form
    if len(call.args) >= 3:
        name = _extract_str(call.args[0], locals_map)
        standard = _extract_str(call.args[1], locals_map)
        result = _extract_str(call.args[2], locals_map)

    # Keyword form (overrides positional if both somehow present)
    for kw in call.keywords:
        if kw.arg == "name":
            name = _extract_str(kw.value, locals_map)
        elif kw.arg == "standard":
            standard = _extract_str(kw.value, locals_map)
        elif kw.arg == "result":
            result = _extract_str(kw.value, locals_map)

    if name is None or standard is None or result is None:
        return None
    return name, standard, result


def collect_triples(
    funcs: list[CheckFunction] | None = None,
) -> list[tuple[str, str, str]]:
    """Scan *funcs* (defaults to :data:`ALL_CHECKS`) and return every
    distinct ``(name, standard, result)`` triple emitted as a literal.

    The returned list preserves first-seen order — useful when emitting
    catalogue rows so they appear in roughly the order the audit pipeline
    runs.

    Local string variables are followed (one hop) so checks that bind
    ``name`` and ``standard`` to a local at the top of the function
    still surface correctly. See :mod:`color_contrast` for an example.

    To handle module-level helper functions (e.g. ``_no_data_result``
    in ``color_contrast``) that themselves construct ``CheckResult``
    instances, the entire source module of each registered check is
    also scanned — once per module, deduplicated by file.
    """
    if funcs is None:
        funcs = ALL_CHECKS

    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str]] = []

    # Scan each check function's source first so the catalogue order
    # matches the registry order. Then also scan the rest of each
    # module to pick up helper-function CheckResult constructions
    # (e.g. ``_no_data_result``).
    seen_modules: set[str] = set()
    for fn in funcs:
        source = inspect.getsource(fn)
        tree = ast.parse(source)
        locals_map = _collect_local_strings(tree)
        for call in _iter_check_result_calls(tree):
            triple = _extract_triple(call, locals_map)
            if triple is None:
                continue
            if triple in seen:
                continue
            seen.add(triple)
            out.append(triple)

        module_path = inspect.getsourcefile(fn)
        if module_path is None or module_path in seen_modules:
            continue
        seen_modules.add(module_path)
        with open(module_path, encoding="utf-8") as handle:
            module_src = handle.read()
        module_tree = ast.parse(module_src)
        for node in ast.walk(module_tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            helper_locals = _collect_local_strings(node)
            for call in _iter_check_result_calls(node):
                triple = _extract_triple(call, helper_locals)
                if triple is None:
                    continue
                if triple in seen:
                    continue
                seen.add(triple)
                out.append(triple)
    return out


def collect_pairs(
    funcs: list[CheckFunction] | None = None,
) -> list[tuple[str, str]]:
    """Like :func:`collect_triples`, but drop the ``standard`` column.

    Returns ``(name, result)`` pairs; only those with ``result`` in
    ``{"FAIL", "WARN", "INFO"}`` are returned (PASS entries don't make
    it to :data:`CHECK_CATALOGUE`).
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, _standard, result in collect_triples(funcs):
        if result == "PASS":
            continue
        key = (name, result)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def main() -> None:
    """Print every (name, result) triple to stdout for review."""
    triples = collect_triples()
    print(f"# {len(triples)} distinct (name, standard, result) triples:")
    for name, standard, result in triples:
        print(f"  ({name!r}, {standard!r}, {result!r})")


if __name__ == "__main__":
    main()
