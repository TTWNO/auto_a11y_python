# Fluent Strict Mode — Design

**Status:** Draft
**Author:** Claude + @tait
**Date:** 2026-04-16

## Problem

Today, when a template or Python caller invokes `ftl('some-id')` with a
message ID that is missing from the Fluent bundles, the system:

1. Logs a `WARNING` ("Missing Fluent message: ...")
2. Returns the raw message ID as the rendered string

The failure is silent from the user's perspective — the page renders, the
literal message ID appears as visible text, and no CI signal is produced.
This means missing translations (especially missing French translations)
routinely reach production undetected.

We already have `tests/validate_translations.py` which checks *file-level*
coverage between `en/` and `fr/` `.ftl` files. But it cannot catch
`ftl('id-that-never-existed')` — a reference from code/templates to an ID
that isn't in any bundle.

## Goal

Make the application fail loudly (HTTP 500) whenever a Fluent translation
is missing, **in debug mode only**. In production, preserve exactly the
current behavior (log + graceful fallback). Ensure the CI "endpoint
health check" runs the server in debug mode so these failures block
merges.

## Non-goals

- Changing production behavior in any way.
- Replacing or extending `validate_translations.py` (which continues to
  enforce file-level EN↔FR coverage).
- Adding a separate `FLUENT_STRICT` env var. Strict mode is tied to Flask
  debug mode per user direction.

## Design

### Overview

Every translation call funnels through `ftl()` in
`auto_a11y/web/fluent.py`. In debug mode, `ftl()` verifies that the
requested message ID resolves successfully in **every** supported locale
(`en` and `fr`). If any locale is missing the ID — or if formatting the
message produces errors — `ftl()` raises `MissingTranslationError`. When
raised inside a Jinja2 template, Flask's debug handler produces a 500
with a traceback pointing at the offending template line.

`check_endpoints.py` already exists and already detects HTTP 500 and
traceback markers; no changes to its detection logic are required. CI
is amended to run the Flask app with `DEBUG=True` (globally) plus a new
`--no-reloader` flag to prevent Werkzeug's auto-reloader from forking a
child process that escapes the `kill $APP_PID` cleanup.

### Components

#### 1. `auto_a11y/web/fluent.py` — strict-mode flag

A new module-level variable:

```python
_strict_mode: bool = False
```

`init_fluent(app)` sets it from `app.debug`:

```python
def init_fluent(app):
    global _strict_mode
    _strict_mode = app.debug
    ...
```

A small helper `_is_strict()` reads the flag so tests can monkey-patch a
single function. `init_fluent` runs exactly once per app (in
`create_app`), before any request, so the flag is stable for the life of
the process.

This satisfies:
- `lazy_ftl` calls made before a request context exists still know
  whether they are in strict mode.
- `python run.py --debug` → `config.DEBUG=True` → `app.debug=True` →
  strict on.
- `python run.py` plain → strict off, current behavior preserved
  byte-for-byte.

#### 2. `auto_a11y/web/fluent.py` — new exception class

```python
class MissingTranslationError(KeyError):
    """Raised in debug mode when a Fluent message is missing from one or
    more supported locales, or when format-time errors occur.

    Subclasses KeyError so existing ``except KeyError`` blocks in
    production code paths continue to work unchanged in non-debug mode.
    """
```

#### 3. `auto_a11y/web/fluent.py` — `ftl()` chokepoint

`ftl(message_id, **kwargs)` keeps its current non-strict path unchanged.
In strict mode, before returning a value:

1. Call `_resolve(locale, message_id, kwargs)` for every locale in
   `_SUPPORTED_LOCALES` (currently `('en', 'fr')`).
2. Collect the list of locales for which `_resolve` returned `None`.
3. If the list is non-empty, raise `MissingTranslationError` with a
   message naming the missing locales.
4. Additionally, when `bundle.format()` returned a non-empty `errors`
   list for *any* locale, raise `MissingTranslationError` citing the
   format errors and the `kwargs` passed.

Only after these checks pass does `ftl()` run its normal fallback chain
and return the string.

`_resolve()` stays exactly as it is today — the "try one locale"
primitive. Keeping it None-returning preserves the existing fallback
chain in production. The strictness policy lives in exactly one place.

#### 4. Wrapper behavior

| Wrapper | Non-strict (unchanged) | Strict (debug only) |
|---|---|---|
| `ftl(id)` | log + return raw ID on miss | raises if `id` missing in any locale, or format errors |
| `ftl_attr(id, attr)` | delegates to `ftl` | strictness inherited via `ftl` |
| `ftl_wcag(name)` | delegates to `ftl` | strictness inherited via `ftl` |
| `lazy_ftl(id)` | deferred `ftl()` call at `__str__` time | strictness inherited at stringification |
| `ftl_enum(value)` | title-cases unknown values | the title-case fallback is skipped; underlying `ftl()` raises |
| `ftl_issue(code, field)` | delegates to `ftl_attr` | strictness inherited |
| `ftl_translate_issue(text)` | returns original English if text not in `inline_issue_ids.json` **OR** if mapped FTL ID missing | **two independent raise paths:** (a) text unmapped in JSON → raise; (b) underlying `ftl()` miss → raise |

`ftl_translate_issue` requires distinct handling because it has two
independent fallback points that represent different classes of bug:

1. **Unmapped English text** — the caller is passing an inline issue
   string that was never cataloged. Fix: add it to
   `auto_a11y/web/translations/inline_issue_ids.json` with a new FTL ID.
2. **Mapped ID missing from bundles** — the English string is cataloged
   but one or more locales' `.ftl` files are missing the message. Fix:
   add the message to the appropriate `.ftl` files.

#### 5. `run.py` — `--no-reloader` flag

Line 236 currently reads:

```python
use_reloader=config.DEBUG
```

With `DEBUG=True` globally in CI, this would spawn Werkzeug's auto-reloader
child process for any backgrounded `python run.py &` invocation. The CI
cleanup (`kill $APP_PID`) only kills the watcher parent, orphaning the
worker.

Add a `--no-reloader` CLI flag and change line 236 to:

```python
use_reloader=config.DEBUG and not args.no_reloader
```

Local behavior (`python run.py --debug`) is unchanged — reloader still
enabled. CI uses `python run.py --no-reloader` to get debug mode without
the fork.

#### 6. `.github/workflows/ci.yml` — flip DEBUG and disable reloader

Two edits:

1. In the `Set up environment variables` step, change the generated
   `.env` from `DEBUG=False` to `DEBUG=True`. This enables strict mode
   for **every** CI step — pytest, check scripts, endpoint health check.

2. In the `Test Flask app and endpoints` step, change
   `python run.py &` to `python run.py --no-reloader &`.

No other CI edits are required. `check_endpoints.py` already catches
500s and traceback markers.

### Error messages

Three user-facing error messages, each with distinct actionable guidance:

**Missing from one or more locales:**

```
MissingTranslationError: Fluent message 'pages-new-thing' missing from locale(s): fr
  Strict mode is active (Flask debug). Add this message ID to:
    auto_a11y/web/translations/fr/*.ftl
```

When missing from both locales, both are listed and the message suggests
starting with `en/`.

**Format-time errors:**

```
MissingTranslationError: Fluent message 'greeting' in locale 'en' has formatting errors: [ReferenceError("Unknown variable: $name")]
  Called with kwargs: {}
  Strict mode is active (Flask debug).
```

Surfaces the Fluent error list and what was passed. Catches the "template
forgot to pass a variable" class.

**Unmapped inline issue text:**

```
MissingTranslationError: Inline issue text has no entry in inline_issue_ids.json:
  "Image has no alt attribute"
  Strict mode is active. Add this English string to:
    auto_a11y/web/translations/inline_issue_ids.json
```

### Logging

In strict mode we skip the existing `logger.warning("Missing Fluent
message: %s", message_id)` call — raising is already loud enough and
duplicate noise in CI logs is unhelpful. In non-strict mode the warning
stays exactly as today.

## Testing

- **Unit tests in `tests/test_fluent.py`:** add tests that
  monkey-patch `_strict_mode = True` and verify each raise path:
  missing in FR, missing in both, format errors, unmapped issue text,
  unknown enum.
- **Unit tests verifying non-strict is unchanged:** the same cases under
  `_strict_mode = False` continue to log warnings and return fallbacks.
- **CI end-to-end:** no dedicated new test. The existing
  `check_endpoints.py` invocation against the DEBUG=True server
  constitutes the integration test — if any page template references a
  missing message ID, CI will fail with a clear 500.

## Expected CI fallout

First CI run under strict mode is likely to surface real, previously
invisible gaps. Most probable classes:

1. French translations missing for recently-added English strings.
2. Inline issue descriptions passed to `ftl_translate_issue` that were
   never added to `inline_issue_ids.json`.
3. Enum values rendered via `ftl_enum` that don't have an `enum-*`
   message in either locale (these have been silently title-casing).
4. Template calls with `kwargs` mismatched against the Fluent message
   signature (format errors).

This is the intended outcome — the point of strict mode is to make these
visible. The implementation plan should assume a follow-up cleanup pass
to fix whatever strict mode surfaces, tracked separately from this
infrastructure change.

## Out of scope (explicit)

- Per-request opt-out of strict mode.
- Making any CI step other than the endpoint health check debug-gated
  (the user chose to flip `DEBUG=True` globally in CI).
- Changing the `_SUPPORTED_LOCALES` list or adding additional locales.
- Refactoring `ftl_translate_issue` or the `inline_issue_ids.json`
  pipeline.
- Touching `validate_translations.py` — it continues to enforce
  file-level EN↔FR coverage as a complementary check.

## Rollback

If strict mode surfaces too much at once and blocks all CI, a single
revert of the two `ci.yml` edits (DEBUG flip + `--no-reloader` flag)
disables strict mode everywhere without touching `fluent.py`. The
`--no-reloader` flag on `run.py` is harmless when left in place.
