# Local type stubs

`.pyi` stubs for third-party libraries that do not ship type information
and for which no `types-*` package exists on PyPI. Every stub covers only
the symbols we actually import — no `Any` in signatures.

Policy: see `docs/superpowers/specs/2026-04-16-strict-type-checking-design.md`.

Before adding a new stub here, **first** check:
1. Does a newer version of the library ship `py.typed`?
2. Is there a `types-<package>` or `<package>-stubs` on PyPI?
3. Is the package in `typeshed`?

If any of the above, use it instead of writing a local stub.
