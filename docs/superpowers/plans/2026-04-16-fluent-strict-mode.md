# Fluent Strict Mode Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make missing Fluent translations raise `MissingTranslationError` (HTTP 500) in Flask debug mode, so CI's existing endpoint health check catches them. Production behavior is preserved byte-for-byte.

**Architecture:** Add a module-level `_strict_mode` flag in `auto_a11y/web/fluent.py`, set from `app.debug` in `init_fluent`. `ftl()` is the chokepoint that every wrapper funnels through — in strict mode it verifies the message resolves in every supported locale (`en`, `fr`) and raises on any miss or format error. `run.py` gets a `--no-reloader` flag so CI can run `DEBUG=True` without Werkzeug forking a reloader child. CI's `.env` generator flips `DEBUG=True` globally, and the backgrounded server uses the new flag.

**Tech Stack:** Python 3.11+, Flask, `fluent-compiler`, pytest, GitHub Actions.

**Spec:** [2026-04-16-fluent-strict-mode-design.md](../specs/2026-04-16-fluent-strict-mode-design.md)

---

## File Inventory

### Files to Modify

| File | Lines (approx.) | What Changes |
|------|-----------------|--------------|
| `auto_a11y/web/fluent.py` | 298-316 | `_resolve()` returns `(value, errors)` tuple instead of `value \| None` |
| `auto_a11y/web/fluent.py` | new, after line 34 | Add `_strict_mode` module flag + `_is_strict()` helper |
| `auto_a11y/web/fluent.py` | new, near top | Add `MissingTranslationError(KeyError)` class |
| `auto_a11y/web/fluent.py` | 196-221 | `init_fluent()` sets `_strict_mode = app.debug` |
| `auto_a11y/web/fluent.py` | 41-64 | `ftl()` gains strict-mode raise paths (missing locales, format errors) |
| `auto_a11y/web/fluent.py` | 169-189 | `ftl_translate_issue()` gains strict raise on unmapped text |
| `run.py` | 135-157 + 232-237 | Add `--no-reloader` CLI flag |
| `.github/workflows/ci.yml` | 62, 148 | Set `DEBUG=True` in generated `.env`; pass `--no-reloader` to `run.py &` |
| `tests/test_fluent.py` | append | New test classes for strict mode |

### No Code Changes Required

`ftl_attr`, `ftl_wcag`, `lazy_ftl`, `ftl_enum`, `ftl_issue` all call `ftl()` internally, so they inherit strictness automatically. We add *tests* for their strict behavior (Task 7) but no implementation changes.

---

## Prerequisites

- Git branch: whatever branch current work is on (do not rewrite history per CLAUDE.md).
- Virtualenv active: `source .venv/bin/activate`
- Dependencies installed: `python -m pip install -r requirements.txt`
- Quick baseline check:

```bash
python -m pytest tests/test_fluent.py -v
```

All existing tests must pass before you start. If they don't, stop and investigate.

---

## Task 1: Refactor `_resolve()` to return `(value, errors)` tuple

**Rationale:** Today `_resolve` calls `bundle.format()`, which returns a `(value, errors)` tuple, but it throws away `errors` via a `logger.warning` and returns only the value. For strict mode to raise on format errors, `ftl()` needs access to the errors list. Refactoring `_resolve` to pass the tuple up is cleaner than duplicating the `bundle.format()` call in `ftl()`.

**Files:**
- Modify: `auto_a11y/web/fluent.py:298-316` (`_resolve`)
- Modify: `auto_a11y/web/fluent.py:41-64` (`ftl` — consume new tuple)
- Test: `tests/test_fluent.py` (no new test needed — behavior is unchanged, existing tests will catch regressions)

- [ ] **Step 1.1: Run the existing test suite as a baseline**

```bash
python -m pytest tests/test_fluent.py -v
```

Expected: all tests pass. If any fail, stop and fix before proceeding.

- [ ] **Step 1.2: Change `_resolve()` signature**

Replace the entire `_resolve()` function body (`fluent.py:298-316`) with:

```python
def _resolve(locale: str, message_id: str, args: dict) -> tuple[str, list] | None:
    """Try to format *message_id* in the given locale's bundle.

    Returns ``(value, errors)`` tuple where ``errors`` is the Fluent
    error list (empty list if none). Returns ``None`` if the message
    is not found at all in this bundle.

    Note: this function no longer logs format errors — callers decide
    whether to log or raise based on strict mode.
    """
    bundle = _bundles.get(locale)
    if bundle is None:
        return None
    try:
        value, errors = bundle.format(message_id, args or None)
        return (value, errors or [])
    except (KeyError, Exception):
        return None
```

Key changes from the old version:
- Return type is `tuple[str, list] | None` (not `str | None`).
- Dropped the `logger.warning("Fluent errors for ...")` line — callers now own that decision.
- Dropped the `has_message` vestige block (was dead code — comment said "pass").

- [ ] **Step 1.3: Update `ftl()` to unpack the tuple**

Replace `fluent.py:41-64` (`ftl()` function body, keeping the signature and docstring) with:

```python
def ftl(message_id: str, **kwargs) -> Markup | str:
    """Resolve a Fluent message for the current locale.

    Falls back: current locale -> English -> message ID as plain string.

    Returns ``Markup(escape(value))`` on success so that French apostrophes
    are safely escaped for HTML/JS contexts.  On complete miss the raw
    message ID string is returned (not Markup) so callers can distinguish
    missing translations.
    """
    locale = _get_current_locale()
    result = _resolve(locale, message_id, kwargs)
    if result is not None:
        value, errors = result
        if errors:
            logger.warning("Fluent errors for '%s' [%s]: %s", message_id, locale, errors)
        return Markup(escape(value))

    # Fallback to English
    if locale != _DEFAULT_LOCALE:
        result = _resolve(_DEFAULT_LOCALE, message_id, kwargs)
        if result is not None:
            value, errors = result
            if errors:
                logger.warning("Fluent errors for '%s' [%s]: %s", message_id, _DEFAULT_LOCALE, errors)
            return Markup(escape(value))

    # Complete miss — return the message ID as a plain string
    logger.warning("Missing Fluent message: %s", message_id)
    return message_id
```

This is a behavior-preserving refactor: it still logs format errors, still falls back to English, still returns the raw ID on complete miss. The only difference is the `logger.warning` for format errors moves from `_resolve` to `ftl`. Strict-mode logic will be added in later tasks.

- [ ] **Step 1.4: Run tests to confirm no regression**

```bash
python -m pytest tests/test_fluent.py -v
```

Expected: all tests pass (same count as baseline in 1.1).

- [ ] **Step 1.5: Commit**

```bash
git add auto_a11y/web/fluent.py
git commit -m "refactor: _resolve returns (value, errors) tuple

Prep for strict-mode error handling: move format-error logging up
to ftl() so the strict path can raise on it instead."
```

---

## Task 2: Add `MissingTranslationError` exception class

**Files:**
- Modify: `auto_a11y/web/fluent.py` (add class near top)
- Test: `tests/test_fluent.py` (append a trivial test class)

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_fluent.py`:

```python
# ---------------------------------------------------------------------------
# Tests: MissingTranslationError
# ---------------------------------------------------------------------------

class TestMissingTranslationError:

    def test_import(self):
        """MissingTranslationError is importable from auto_a11y.web.fluent."""
        from auto_a11y.web.fluent import MissingTranslationError
        assert MissingTranslationError is not None

    def test_subclasses_keyerror(self):
        """MissingTranslationError subclasses KeyError so existing
        except-KeyError blocks keep working."""
        from auto_a11y.web.fluent import MissingTranslationError
        assert issubclass(MissingTranslationError, KeyError)

    def test_can_raise_with_message(self):
        from auto_a11y.web.fluent import MissingTranslationError
        with pytest.raises(MissingTranslationError, match="test msg"):
            raise MissingTranslationError("test msg")
```

- [ ] **Step 2.2: Run the test to verify it fails**

```bash
python -m pytest tests/test_fluent.py::TestMissingTranslationError -v
```

Expected: FAIL with `ImportError: cannot import name 'MissingTranslationError'`.

- [ ] **Step 2.3: Add the exception class**

Add to `auto_a11y/web/fluent.py` just after the module imports and before the `_locale_override` ContextVar (around line 22, after `logger = logging.getLogger(__name__)`):

```python
class MissingTranslationError(KeyError):
    """Raised in debug (strict) mode when a Fluent message is missing
    from one or more supported locales, or when format-time errors occur.

    Subclasses ``KeyError`` so existing ``except KeyError`` blocks in
    production code paths continue to work unchanged in non-strict mode.
    """
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_fluent.py::TestMissingTranslationError -v
```

Expected: all 3 tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add auto_a11y/web/fluent.py tests/test_fluent.py
git commit -m "feat(fluent): add MissingTranslationError exception class

Used by forthcoming strict-mode checks. Subclasses KeyError for
backward compatibility with production code paths."
```

---

## Task 3: Add `_strict_mode` flag, `_is_strict()` helper, and wire into `init_fluent()`

**Files:**
- Modify: `auto_a11y/web/fluent.py` (add flag + helper near other module state; update `init_fluent`)
- Test: `tests/test_fluent.py` (append test class)

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_fluent.py`:

```python
# ---------------------------------------------------------------------------
# Tests: strict-mode flag
# ---------------------------------------------------------------------------

class TestStrictModeFlag:

    def test_is_strict_defaults_false(self, monkeypatch):
        """_is_strict() returns False when _strict_mode is False (default)."""
        import auto_a11y.web.fluent as fluent_mod
        monkeypatch.setattr(fluent_mod, "_strict_mode", False)
        assert fluent_mod._is_strict() is False

    def test_is_strict_reflects_flag(self, monkeypatch):
        """_is_strict() returns True when _strict_mode is True."""
        import auto_a11y.web.fluent as fluent_mod
        monkeypatch.setattr(fluent_mod, "_strict_mode", True)
        assert fluent_mod._is_strict() is True

    def test_init_fluent_sets_flag_from_app_debug(self, tmp_path):
        """init_fluent(app) sets _strict_mode from app.debug."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import init_fluent

        # Minimal translations dir so init_fluent is happy
        (tmp_path / "en").mkdir()
        (tmp_path / "en" / "m.ftl").write_text("hello = Hi\n", encoding="utf-8")
        (tmp_path / "fr").mkdir()
        (tmp_path / "fr" / "m.ftl").write_text("hello = Salut\n", encoding="utf-8")

        # Reset before each scenario
        fluent_mod._strict_mode = False

        # app.debug=True -> strict
        app = Flask(__name__)
        app.debug = True
        # Monkey-patch translations dir location
        original_dirname = os.path.dirname
        monkey_dir = str(tmp_path.parent)

        # init_fluent uses os.path.dirname(__file__) + '/translations'.
        # We avoid that by directly calling _load_bundles then setting flag:
        fluent_mod._load_bundles(str(tmp_path))
        # Now simulate what init_fluent does re: the flag:
        init_fluent(app)
        # init_fluent will also re-call _load_bundles with the real path;
        # that's fine — we only care about the flag.
        assert fluent_mod._strict_mode is True

        # app.debug=False -> non-strict
        app2 = Flask(__name__)
        app2.debug = False
        init_fluent(app2)
        assert fluent_mod._strict_mode is False
```

- [ ] **Step 3.2: Run the test to verify it fails**

```bash
python -m pytest tests/test_fluent.py::TestStrictModeFlag -v
```

Expected: FAIL — `_strict_mode` and `_is_strict` don't exist yet.

- [ ] **Step 3.3: Add the flag, helper, and init wiring**

In `auto_a11y/web/fluent.py`, after the `_bundles: dict = {}` line (around line 30), add:

```python
# Module-level strict-mode flag. Set by init_fluent() from app.debug.
# When True, ftl() raises MissingTranslationError on any miss or format
# error in any supported locale, instead of logging + falling back.
_strict_mode: bool = False


def _is_strict() -> bool:
    """Return True if strict translation checking is enabled.

    Tests can monkey-patch this function (or the _strict_mode module
    variable) to control strict behavior per-test.
    """
    return _strict_mode
```

In `init_fluent(app)` (around line 196), add the flag assignment at the very top of the function body, before `translations_dir = ...`:

```python
def init_fluent(app):
    """Initialize Fluent integration on *app*.
    ... (existing docstring) ...
    """
    global _strict_mode
    _strict_mode = bool(app.debug)

    translations_dir = os.path.join(os.path.dirname(__file__), 'translations')
    # ... rest of function unchanged ...
```

Also update the existing `logger.info("Fluent initialized ...")` line at the bottom of `init_fluent` to mention strictness:

```python
logger.info(
    "Fluent initialized — locales loaded: %s (strict_mode=%s)",
    ', '.join(sorted(_bundles.keys())) or '(none)',
    _strict_mode,
)
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_fluent.py::TestStrictModeFlag -v
```

Expected: all 3 tests PASS.

- [ ] **Step 3.5: Run the full `test_fluent.py` to confirm no regressions**

```bash
python -m pytest tests/test_fluent.py -v
```

Expected: all tests pass.

- [ ] **Step 3.6: Commit**

```bash
git add auto_a11y/web/fluent.py tests/test_fluent.py
git commit -m "feat(fluent): add _strict_mode flag set from app.debug

Module-level flag controlled by init_fluent(app); _is_strict() helper
provides a monkey-patchable accessor for tests."
```

---

## Task 4: Make `ftl()` raise on missing locales in strict mode

**Files:**
- Modify: `auto_a11y/web/fluent.py:41-64` (`ftl()` body)
- Test: `tests/test_fluent.py` (append test class)

- [ ] **Step 4.1: Write the failing test**

Append to `tests/test_fluent.py`:

```python
# ---------------------------------------------------------------------------
# Tests: strict mode — missing-locale raises
# ---------------------------------------------------------------------------

class TestStrictModeMissingLocales:

    def test_missing_in_fr_raises(self, fluent_app, monkeypatch):
        """When FR is missing a message ID, strict mode raises even if EN has it."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError, match="only-in-english"):
                ftl("only-in-english")

    def test_missing_in_both_raises(self, fluent_app, monkeypatch):
        """Missing in both locales raises."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError, match="does-not-exist"):
                ftl("does-not-exist")

    def test_error_message_names_missing_locales(self, fluent_app, monkeypatch):
        """The error message lists which locales are missing the ID."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError) as exc_info:
                ftl("only-in-english")
            # Only FR is missing this one
            assert "fr" in str(exc_info.value)
            assert "only-in-english" in str(exc_info.value)

    def test_non_strict_unchanged(self, fluent_app, monkeypatch):
        """Non-strict mode (default): same inputs still log + fall back."""
        import auto_a11y.web.fluent as fluent_mod

        monkeypatch.setattr(fluent_mod, "_strict_mode", False)

        with fluent_app.test_request_context():
            session["language"] = "fr"
            # only-in-english: FR missing, EN has it -> falls back to EN
            result = ftl("only-in-english")
            assert str(result) == "English only"

            # does-not-exist: both missing -> returns raw ID
            result2 = ftl("does-not-exist")
            assert result2 == "does-not-exist"

    def test_present_in_both_does_not_raise(self, fluent_app, monkeypatch):
        """When the message exists in both locales, strict mode is silent."""
        import auto_a11y.web.fluent as fluent_mod
        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "fr"
            # Should not raise
            assert str(ftl("hello")) == "Bonjour"
```

- [ ] **Step 4.2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_fluent.py::TestStrictModeMissingLocales -v
```

Expected: FAIL — the raising logic isn't implemented yet. `test_present_in_both_does_not_raise` and `test_non_strict_unchanged` may already pass.

- [ ] **Step 4.3: Implement strict missing-locale check in `ftl()`**

Replace the current `ftl()` body with the strict-aware version:

```python
def ftl(message_id: str, **kwargs) -> Markup | str:
    """Resolve a Fluent message for the current locale.

    Falls back: current locale -> English -> message ID as plain string.

    Returns ``Markup(escape(value))`` on success so that French apostrophes
    are safely escaped for HTML/JS contexts.  On complete miss the raw
    message ID string is returned (not Markup) so callers can distinguish
    missing translations.

    In strict mode (Flask debug), raises ``MissingTranslationError`` if
    the message ID is missing from any supported locale.
    """
    # Strict-mode full-coverage check
    if _is_strict():
        missing = [
            loc for loc in _SUPPORTED_LOCALES
            if _resolve(loc, message_id, kwargs) is None
        ]
        if missing:
            raise MissingTranslationError(
                f"Fluent message {message_id!r} missing from locale(s): "
                f"{', '.join(missing)}\n"
                f"  Strict mode is active (Flask debug). Add this message ID to:\n"
                + "\n".join(
                    f"    auto_a11y/web/translations/{loc}/*.ftl"
                    for loc in missing
                )
            )

    # Non-strict path (also the post-strict-check path)
    locale = _get_current_locale()
    result = _resolve(locale, message_id, kwargs)
    if result is not None:
        value, errors = result
        if errors:
            logger.warning("Fluent errors for '%s' [%s]: %s", message_id, locale, errors)
        return Markup(escape(value))

    # Fallback to English
    if locale != _DEFAULT_LOCALE:
        result = _resolve(_DEFAULT_LOCALE, message_id, kwargs)
        if result is not None:
            value, errors = result
            if errors:
                logger.warning("Fluent errors for '%s' [%s]: %s", message_id, _DEFAULT_LOCALE, errors)
            return Markup(escape(value))

    # Complete miss — return the message ID as a plain string
    logger.warning("Missing Fluent message: %s", message_id)
    return message_id
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_fluent.py::TestStrictModeMissingLocales -v
```

Expected: all 5 tests PASS.

- [ ] **Step 4.5: Run the full `test_fluent.py` to confirm no regressions**

```bash
python -m pytest tests/test_fluent.py -v
```

Expected: all tests pass. The existing `TestFallback.test_missing_returns_message_id` and `TestFallback.test_fr_falls_back_to_en` should still pass because they run in non-strict mode (the `fluent_app` fixture does not flip `_strict_mode`).

- [ ] **Step 4.6: Commit**

```bash
git add auto_a11y/web/fluent.py tests/test_fluent.py
git commit -m "feat(fluent): ftl() raises on missing locales in strict mode

In strict mode (Flask debug), every ftl() call verifies the message ID
resolves in every supported locale. Any miss raises MissingTranslationError,
which Flask's debug handler converts to a 500 with a traceback."
```

---

## Task 5: Make `ftl()` raise on format errors in strict mode

**Files:**
- Modify: `auto_a11y/web/fluent.py` (`ftl()` — add format-error raise)
- Test: `tests/test_fluent.py` (append test class)

- [ ] **Step 5.1: Write the failing test**

First, we need an FTL message that will produce format errors when called with wrong kwargs. The existing `EN_FTL` fixture has `greeting = Hello, { $name }!` which needs `$name`. Calling `ftl("greeting")` (no kwargs) produces a Fluent `ReferenceError`. Append to `tests/test_fluent.py`:

```python
# ---------------------------------------------------------------------------
# Tests: strict mode — format errors raise
# ---------------------------------------------------------------------------

class TestStrictModeFormatErrors:

    def test_missing_variable_raises_in_strict(self, fluent_app, monkeypatch):
        """In strict mode, missing a required variable raises."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            # 'greeting' requires { $name }; passing no kwargs -> format error
            with pytest.raises(MissingTranslationError, match="formatting errors"):
                ftl("greeting")

    def test_missing_variable_non_strict_logs_warning(self, fluent_app, monkeypatch, caplog):
        """Non-strict mode still logs a warning on format errors (unchanged behavior)."""
        import auto_a11y.web.fluent as fluent_mod
        import logging

        monkeypatch.setattr(fluent_mod, "_strict_mode", False)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with caplog.at_level(logging.WARNING, logger="auto_a11y.web.fluent"):
                # Should NOT raise, just log + return whatever fluent gives us
                result = ftl("greeting")
            # A warning was logged mentioning format errors
            assert any("Fluent errors" in rec.message for rec in caplog.records)

    def test_correct_kwargs_does_not_raise(self, fluent_app, monkeypatch):
        """Correct kwargs produce no format errors, strict mode silent."""
        import auto_a11y.web.fluent as fluent_mod
        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            assert str(ftl("greeting", name="World")) == "Hello, World!"
```

- [ ] **Step 5.2: Run the test to verify it fails**

```bash
python -m pytest tests/test_fluent.py::TestStrictModeFormatErrors -v
```

Expected: `test_missing_variable_raises_in_strict` FAILS (raise not implemented). The other two may already pass.

- [ ] **Step 5.3: Add format-error raise to the strict-mode check in `ftl()`**

In `ftl()`, extend the strict-mode block (the `if _is_strict():` section added in Task 4) to also check for format errors. Replace the strict-mode block with:

```python
    # Strict-mode full-coverage check
    if _is_strict():
        missing = []
        format_errors = []  # list of (locale, errors)
        for loc in _SUPPORTED_LOCALES:
            res = _resolve(loc, message_id, kwargs)
            if res is None:
                missing.append(loc)
            else:
                _, errs = res
                if errs:
                    format_errors.append((loc, errs))

        if missing:
            raise MissingTranslationError(
                f"Fluent message {message_id!r} missing from locale(s): "
                f"{', '.join(missing)}\n"
                f"  Strict mode is active (Flask debug). Add this message ID to:\n"
                + "\n".join(
                    f"    auto_a11y/web/translations/{loc}/*.ftl"
                    for loc in missing
                )
            )

        if format_errors:
            loc, errs = format_errors[0]  # first one is enough for diagnosis
            raise MissingTranslationError(
                f"Fluent message {message_id!r} in locale {loc!r} has "
                f"formatting errors: {errs}\n"
                f"  Called with kwargs: {kwargs}\n"
                f"  Strict mode is active (Flask debug)."
            )
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_fluent.py::TestStrictModeFormatErrors -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5.5: Run the full `test_fluent.py` to confirm no regressions**

```bash
python -m pytest tests/test_fluent.py -v
```

Expected: all tests pass.

- [ ] **Step 5.6: Commit**

```bash
git add auto_a11y/web/fluent.py tests/test_fluent.py
git commit -m "feat(fluent): strict mode raises on Fluent format errors

Catches bugs where a template calls ftl() with missing or misspelled
kwargs. In non-strict mode, format errors still just log a warning."
```

---

## Task 6: Make `ftl_translate_issue()` raise on unmapped text in strict mode

**Files:**
- Modify: `auto_a11y/web/fluent.py:169-189` (`ftl_translate_issue`)
- Test: `tests/test_fluent.py` (append)

**Background:** `ftl_translate_issue(text)` looks `text` up in `inline_issue_ids.json` to find the FTL message ID. If `text` isn't in the map, today it silently returns the original English. In strict mode we want this to raise too, since the point is "100% FR translated" — anything user-visible that isn't translatable is a bug.

- [ ] **Step 6.1: Write the failing test**

Append to `tests/test_fluent.py`:

```python
# ---------------------------------------------------------------------------
# Tests: strict mode — ftl_translate_issue
# ---------------------------------------------------------------------------

class TestStrictModeTranslateIssue:

    def test_unmapped_text_raises_in_strict(self, fluent_app, monkeypatch):
        """In strict mode, inline text not in the JSON map raises."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError, ftl_translate_issue

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)
        # Force the JSON map to an empty dict so "any text" is unmapped
        monkeypatch.setattr(fluent_mod, "_inline_issue_ids", {})

        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError, match="inline_issue_ids.json"):
                ftl_translate_issue("Some untracked inline text")

    def test_unmapped_text_non_strict_returns_original(self, fluent_app, monkeypatch):
        """Non-strict: unmapped text silently falls back (unchanged behavior)."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import ftl_translate_issue

        monkeypatch.setattr(fluent_mod, "_strict_mode", False)
        monkeypatch.setattr(fluent_mod, "_inline_issue_ids", {})

        with fluent_app.test_request_context():
            session["language"] = "en"
            assert ftl_translate_issue("Some untracked inline text") == (
                "Some untracked inline text"
            )

    def test_empty_text_does_not_raise(self, fluent_app, monkeypatch):
        """Empty/None text returns empty string without raising, even in strict."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import ftl_translate_issue

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        assert ftl_translate_issue("") == ""
        assert ftl_translate_issue(None) == ""
```

- [ ] **Step 6.2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_fluent.py::TestStrictModeTranslateIssue -v
```

Expected: `test_unmapped_text_raises_in_strict` FAILS. Others may already pass.

- [ ] **Step 6.3: Add the strict raise in `ftl_translate_issue`**

Replace `ftl_translate_issue` (`fluent.py:169-189`) with:

```python
def ftl_translate_issue(text: str) -> str:
    """Translate an inline issue description string via Fluent.

    Looks up the English text in the inline issue ID map, resolves the
    corresponding FTL message, and returns the translated string.
    Falls back to the original text if no mapping exists.

    This replaces the old ``translate_issue`` Jinja2 filter.

    In strict mode, an unmapped English string raises
    ``MissingTranslationError`` so CI catches untranslated issue
    descriptions.
    """
    if not text:
        return text or ''
    id_map = _load_inline_issue_ids()
    ftl_id = id_map.get(text)
    if ftl_id is None:
        if _is_strict():
            raise MissingTranslationError(
                f"Inline issue text has no entry in inline_issue_ids.json:\n"
                f"  {text!r}\n"
                f"  Strict mode is active. Add this English string to:\n"
                f"    auto_a11y/web/translations/inline_issue_ids.json"
            )
        # No mapping — return original text
        return text
    result = ftl(ftl_id)
    # If ftl() returned the message ID (miss), fall back to original text.
    # In strict mode this branch is unreachable because ftl() raises first.
    if isinstance(result, str) and result == ftl_id:
        return text
    return str(result)
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
python -m pytest tests/test_fluent.py::TestStrictModeTranslateIssue -v
```

Expected: all 3 tests PASS.

- [ ] **Step 6.5: Run the full `test_fluent.py` to confirm no regressions**

```bash
python -m pytest tests/test_fluent.py -v
```

Expected: all tests pass.

- [ ] **Step 6.6: Commit**

```bash
git add auto_a11y/web/fluent.py tests/test_fluent.py
git commit -m "feat(fluent): ftl_translate_issue raises on unmapped text in strict mode

The JSON map lookup's None-fallback is bypassed in strict mode so
untracked inline issue strings surface as 500s in CI."
```

---

## Task 7: Add tests verifying inherited strictness for wrapper functions

**Files:**
- Test only: `tests/test_fluent.py` (append)

**Rationale:** `ftl_attr`, `ftl_wcag`, `ftl_enum`, `ftl_issue`, and `lazy_ftl` all funnel through `ftl()`, so they inherit strictness automatically. We add tests to lock that behavior in place so a future refactor can't break it.

- [ ] **Step 7.1: Write the tests**

Append to `tests/test_fluent.py`:

```python
# ---------------------------------------------------------------------------
# Tests: strict mode inherited by wrappers
# ---------------------------------------------------------------------------

class TestStrictModeWrappers:

    def test_ftl_attr_inherits_strict(self, fluent_app, monkeypatch):
        """ftl_attr raises in strict mode when the composite id.attr is missing."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError, ftl_attr

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            # search-input.does-not-exist: attribute missing from both locales
            with pytest.raises(MissingTranslationError):
                ftl_attr("search-input", "does-not-exist")

    def test_lazy_ftl_inherits_strict(self, fluent_app, monkeypatch):
        """lazy_ftl raises at stringification time in strict mode."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError, lazy_ftl

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        lazy = lazy_ftl("does-not-exist")
        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError):
                str(lazy)

    def test_ftl_enum_inherits_strict(self, fluent_app, monkeypatch):
        """ftl_enum raises in strict mode when the enum-* id is missing."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError, ftl_enum

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            # 'enum-bogus-value' is not in the test bundles
            with pytest.raises(MissingTranslationError):
                ftl_enum("bogus_value")

    def test_ftl_enum_non_strict_still_title_cases(self, fluent_app, monkeypatch):
        """Non-strict mode: ftl_enum's title-case fallback still works."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import ftl_enum

        monkeypatch.setattr(fluent_mod, "_strict_mode", False)

        with fluent_app.test_request_context():
            session["language"] = "en"
            # Falls back to title-cased 'Bogus Value'
            assert ftl_enum("bogus_value") == "Bogus Value"

    def test_ftl_wcag_inherits_strict(self, fluent_app, monkeypatch):
        """ftl_wcag raises in strict mode when the wcag-* id is missing."""
        import auto_a11y.web.fluent as fluent_mod
        from auto_a11y.web.fluent import MissingTranslationError, ftl_wcag

        monkeypatch.setattr(fluent_mod, "_strict_mode", True)

        with fluent_app.test_request_context():
            session["language"] = "en"
            with pytest.raises(MissingTranslationError):
                ftl_wcag("Some Criterion Not In Bundles")
```

- [ ] **Step 7.2: Run the tests to verify they pass**

```bash
python -m pytest tests/test_fluent.py::TestStrictModeWrappers -v
```

Expected: all 5 tests PASS (no implementation changes needed — they test inherited behavior from Tasks 4-6).

- [ ] **Step 7.3: Run the full `test_fluent.py`**

```bash
python -m pytest tests/test_fluent.py -v
```

Expected: all tests pass.

- [ ] **Step 7.4: Commit**

```bash
git add tests/test_fluent.py
git commit -m "test(fluent): lock in strict-mode inheritance for wrapper functions

ftl_attr, lazy_ftl, ftl_enum, ftl_wcag all funnel through ftl() and
inherit strictness automatically. These tests prevent regressions if
anyone refactors the wrapper chain."
```

---

## Task 8: Add `--no-reloader` CLI flag to `run.py`

**Rationale:** CI will run the server with `DEBUG=True` in the environment. `run.py:236` currently reads `use_reloader=config.DEBUG`, which spawns Werkzeug's reloader child process. In a backgrounded `python run.py &` invocation, `kill $APP_PID` only kills the watcher parent and orphans the worker. `--no-reloader` lets CI opt out of the reloader while keeping `DEBUG=True` for strict mode.

**Files:**
- Modify: `run.py:135-157` (argparse) and `run.py:232-237` (`app.run`)

No automated test is practical here (it's a CLI flag on a process entry point). We verify manually after implementation.

- [ ] **Step 8.1: Add the argparse flag**

In `run.py` around line 142 (among the other `parser.add_argument` calls), add:

```python
    parser.add_argument('--no-reloader', dest='no_reloader', action='store_true',
                        help='Disable Werkzeug auto-reloader even when debug is on (useful for CI)')
```

Place it immediately after the `--debug` argument for readability.

- [ ] **Step 8.2: Use the flag in the `app.run()` call**

Change `run.py:236` from:

```python
            use_reloader=config.DEBUG
```

to:

```python
            use_reloader=config.DEBUG and not args.no_reloader
```

- [ ] **Step 8.3: Verify `--help` shows the flag**

```bash
python run.py --help
```

Expected: output includes a line describing `--no-reloader`.

- [ ] **Step 8.4: Verify `--no-reloader` does not affect non-debug runs**

(Just a syntax / import check — we don't actually start MongoDB here.)

```bash
python -c "import run; p = run.argparse.ArgumentParser(); print('OK')"
```

Expected: prints `OK` with no error.

- [ ] **Step 8.5: (Optional manual check) Verify backgrounded debug run has no reloader child**

Only do this if you have a running MongoDB. Skip otherwise — CI will exercise it.

```bash
DEBUG=True python run.py --no-reloader &
sleep 3
ps -ef | grep 'python run.py' | grep -v grep
kill %1
```

Expected: exactly one `python run.py` process (no parent/child pair). If you see two, the reloader is still active and the flag isn't wired correctly.

- [ ] **Step 8.6: Commit**

```bash
git add run.py
git commit -m "feat(run): add --no-reloader flag for debug-mode CI runs

Lets CI run the app with DEBUG=True (for strict Fluent checking)
without Werkzeug forking an auto-reloader child process that would
escape the 'kill \$APP_PID' cleanup."
```

---

## Task 9: Update `ci.yml` — flip DEBUG=True and add --no-reloader

**Files:**
- Modify: `.github/workflows/ci.yml:62` (DEBUG line in generated `.env`)
- Modify: `.github/workflows/ci.yml:148` (server startup line)

- [ ] **Step 9.1: Flip `DEBUG=False` → `DEBUG=True` in the `.env` generator**

In `.github/workflows/ci.yml`, find the `Set up environment variables` step (around line 56-87). Change line 62 from:

```
        DEBUG=False
```

to:

```
        DEBUG=True
```

- [ ] **Step 9.2: Add `--no-reloader` to the backgrounded server startup**

Find line 148 in `.github/workflows/ci.yml` (in the `Test Flask app and endpoints` step). Change:

```yaml
        python run.py &
```

to:

```yaml
        python run.py --no-reloader &
```

- [ ] **Step 9.3: Validate YAML syntax**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: no output (parse success). If you see a `yaml.YAMLError`, re-check indentation — YAML here-docs are whitespace-sensitive.

- [ ] **Step 9.4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run with DEBUG=True and --no-reloader

DEBUG=True enables Fluent strict mode across every CI step — any
missing translation reached by pytest, check scripts, or the endpoint
health check now fails CI. --no-reloader keeps the backgrounded server
from spawning a reloader child that escapes process cleanup."
```

---

## Task 10: Smoke test the full pipeline locally

**Rationale:** Confirm the new behavior works end-to-end before pushing. This surfaces any interaction we missed and gives us a preview of which existing templates/code have missing translations (i.e., the "expected CI fallout").

**Files:** none modified.

- [ ] **Step 10.1: Run the full pytest suite**

```bash
python -m pytest tests/test_fluent.py -v
```

Expected: all tests pass (baseline + all new tests from Tasks 2-7, ~20+ tests).

- [ ] **Step 10.2: Run the translation validator**

```bash
python tests/validate_translations.py
```

Expected: exits 0. This is the *file-level* validator — unchanged by this work and should still pass.

- [ ] **Step 10.3: (If you have MongoDB running) start the app in strict mode and browse a page**

```bash
DEBUG=True python run.py --no-reloader
```

In another terminal, or browser, hit `http://127.0.0.1:5001/`. If any page you visit shows a 500 with `MissingTranslationError`, that is **real fallout** — log the page, stop the server with Ctrl+C, and move on to Step 10.4. Do not fix the missing translations as part of this plan — per the spec, that cleanup is tracked separately.

- [ ] **Step 10.4: Document any discovered missing-translation issues**

Create (or append to) a scratch note at the repo root recording any 500s you hit in Step 10.3:

```
echo "# Fluent strict-mode fallout (2026-04-16)" > /tmp/fluent-fallout.md
echo "Run through the pages that failed and record:" >> /tmp/fluent-fallout.md
echo "- URL" >> /tmp/fluent-fallout.md
echo "- Missing message ID from the traceback" >> /tmp/fluent-fallout.md
echo "- Which locale is missing it" >> /tmp/fluent-fallout.md
```

Share this note with the project owner (or attach it to the CI-fallout follow-up ticket) so the cleanup can be planned separately.

- [ ] **Step 10.5: Push the branch**

```bash
git push origin $(git branch --show-current)
```

Expected: CI runs. If CI fails with `MissingTranslationError` 500s, that is the **expected fallout** from the spec's "Expected CI fallout" section — do not revert. Triage the failures as real issues to fix separately.

If CI fails for any other reason (pytest regression, syntax error, YAML parse), investigate and fix with a new commit on this branch.

- [ ] **Step 10.6: (Only if CI blocks critical work and cleanup is not feasible right now) Roll back**

Per the spec, a single revert of `DEBUG=True` in `ci.yml` disables strict mode CI-wide without touching `fluent.py`:

```bash
# Only run this if you need to roll back
# git revert <commit-of-task-9>
```

Prefer fixing the underlying missing translations over rolling back. Rollback is the emergency exit.

---

## Done Criteria

- [ ] All tasks above checked off.
- [ ] `python -m pytest tests/test_fluent.py -v` passes locally.
- [ ] `python tests/validate_translations.py` still passes (file-level validator unaffected).
- [ ] CI run on the pushed branch either passes (if there are no missing translations in the codebase today) or fails with clear `MissingTranslationError` messages in the endpoint-check step (expected fallout — a separate cleanup effort).

## Out of Scope for This Plan

Per the spec, **do not** fix any missing translations surfaced by the new strict check as part of this plan. That cleanup is tracked separately. This plan only delivers the infrastructure (strict mode + CI wiring).

## References

- **Spec:** [`docs/superpowers/specs/2026-04-16-fluent-strict-mode-design.md`](../specs/2026-04-16-fluent-strict-mode-design.md)
- **Existing Fluent module:** `auto_a11y/web/fluent.py`
- **Existing test suite:** `tests/test_fluent.py`
- **File-level translation validator (unchanged):** `tests/validate_translations.py`
- **Endpoint health checker (unchanged):** `tests/check_endpoints.py`
- **CI config:** `.github/workflows/ci.yml`
- **Skills:** @superpowers:subagent-driven-development (preferred), @superpowers:executing-plans (fallback), @superpowers:test-driven-development (per-task discipline), @superpowers:verification-before-completion (Task 10)
