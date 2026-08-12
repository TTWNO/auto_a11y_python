"""Guards for the PDF.js Uint8Array hex/base64 polyfill.

PDF.js 5.4 calls ``Uint8Array#toHex`` (document fingerprints, worker-side),
``#toBase64`` (embedded font data URLs) and ``Uint8Array.fromBase64``
(signature decompression). Those methods only reached Chrome 140 / Safari
18.4, so on anything older every document load fails with
``UnknownErrorException: a.toHex is not a function`` — which is what the
bundled desktop app hit, its Electron 33 carrying Chromium ~130.

These are static guards. They cannot execute JavaScript, so they assert the
wiring that is easy to break silently on a PDF.js upgrade: that the polyfill
exists, that it is loaded ahead of PDF.js in both the page and the worker,
and that the vendored PDF.js files were left untouched. The behavioural
proof — output compared against Chromium's native implementation across 258
cases, and a real document loading in a worker with the methods deleted —
was run in a browser; see the commit that introduced this file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR = REPO_ROOT / "auto_a11y" / "web" / "static" / "vendor" / "pdfjs"
POLYFILL = VENDOR / "uint8array-polyfill.mjs"
WORKER_WRAPPER = VENDOR / "pdf.worker.polyfilled.mjs"
VIEWER = REPO_ROOT / "auto_a11y" / "web" / "static" / "js" / "pdf_viewer_app.js"


def test_polyfill_defines_every_method_pdfjs_calls() -> None:
    source = POLYFILL.read_text(encoding="utf-8")
    for name in ("toHex", "toBase64", "fromBase64", "fromHex"):
        assert f'"{name}"' in source, f"polyfill does not define {name}"


def test_polyfill_defers_to_a_native_implementation() -> None:
    """A current engine must keep its own implementation."""
    source = POLYFILL.read_text(encoding="utf-8")
    assert 'typeof target[name] === "function"' in source, (
        "polyfill must not overwrite a native implementation"
    )


def test_worker_wrapper_applies_the_polyfill_before_pdfjs() -> None:
    """A worker has its own global scope.

    Polyfilling only the page leaves toHex — which runs worker-side —
    still missing, so the document fails to load exactly as before.
    """
    # Compare import statements, not raw substrings — both filenames are
    # also named in the file's explanatory comment.
    imports = [
        line.strip()
        for line in WORKER_WRAPPER.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("import ")
    ]
    polyfill_at = next(
        (i for i, line in enumerate(imports) if "uint8array-polyfill.mjs" in line),
        None,
    )
    worker_at = next(
        (i for i, line in enumerate(imports) if "pdf.worker.min.mjs" in line),
        None,
    )
    assert polyfill_at is not None, "worker wrapper does not import the polyfill"
    assert worker_at is not None, "worker wrapper does not import the PDF.js worker"
    assert polyfill_at < worker_at, (
        "the polyfill must be imported before the worker body runs"
    )


def test_viewer_loads_the_polyfill_before_pdfjs() -> None:
    source = VIEWER.read_text(encoding="utf-8")
    polyfill_at = source.find("uint8array-polyfill.mjs")
    pdfjs_at = source.find("pdfjs/pdf.min.mjs")
    assert polyfill_at != -1, "viewer does not load the polyfill"
    assert polyfill_at < pdfjs_at, (
        "the polyfill must load before PDF.js evaluates"
    )


def test_viewer_points_the_worker_at_the_wrapper() -> None:
    source = VIEWER.read_text(encoding="utf-8")
    assert "pdf.worker.polyfilled.mjs" in source, (
        "workerSrc must point at the polyfilled wrapper, not the raw worker"
    )


@pytest.mark.parametrize("name", ["pdf.min.mjs", "pdf.worker.min.mjs"])
def test_vendored_pdfjs_is_unpatched(name: str) -> None:
    """The fix must stay outside the vendored files.

    Keeping them pristine is what makes a PDF.js upgrade a straight file
    swap rather than a re-patching exercise.
    """
    source = (VENDOR / name).read_text(encoding="utf-8", errors="replace")
    assert "uint8array-polyfill" not in source, (
        f"{name} has been patched; the polyfill belongs in the wrapper"
    )
