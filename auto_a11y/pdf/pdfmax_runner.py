"""Verbatim ``pdfMax`` accessibility-audit runner.

This module is deliberately *not* a port. It shells out to the
existing ``pdfMax`` Python audit tool
(``~/Documents/cnib/code/pdfMax/python/checker/pdf_accessibility_audit.py``)
and returns the Markdown report it writes — byte-for-byte the same
content the pdfMax Electron app renders in its CheckerReport tab.

The auto_a11y route layer wraps the Markdown in an HTML page that
loads ``marked`` + ``DOMPurify`` from a CDN and runs the *same*
sanitiser / extension config as ``CheckerReport.tsx`` (``ADD_TAGS:
['details','summary']``, ``ADD_ATTR:
['open','data-check-name','data-check-result']``), so the rendered
DOM matches pdfMax's view byte-for-byte without re-implementing the
React component.

Why subprocess instead of vendoring:

* ``pdf_accessibility_audit.py`` is ~12k lines of un-typed legacy
  Python that would not pass auto_a11y's strict mypy/pyright/ty
  gates.
* It has its own ``.venv`` with pikepdf / pdfminer / etc. pinned at
  versions that don't match auto_a11y's. Vendoring would force a
  dependency reconciliation effort that's well outside the scope of
  "just show what pdfMax shows".
* Process isolation: pdfMax monkey-patches ``builtins.print`` for
  progress reporting. Importing it would clobber auto_a11y's own
  print hooks.

Caching: each ``(pdf_document_id, sha256, wcag_level)`` triple
produces a deterministic ``.md`` file we cache alongside the PDF
under ``PdfStorage``. The cache is keyed on the *content hash* of
the source PDF — a SHA-256 of its bytes, persisted in a sidecar
``*_accessibility_report.sha256`` file next to the report. We reuse
the cached report only when the current PDF hashes to the stored
value, so overwriting the PDF in place (even with an unchanged
mtime) correctly busts the cache. The ``.md`` mtime is used solely
as a cheap fast-path: when it already post-dates the PDF *and* the
stored hash matches, we skip even the re-hash on the next render.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# Suffix of the sidecar holding the content hash of the PDF the cached
# report was produced from. Lives next to ``*_accessibility_report.md``.
_HASH_SIDECAR_SUFFIX = "_accessibility_report.sha256"

# Read the PDF in chunks when hashing so we never buffer a large file
# whole just to compute its digest.
_HASH_CHUNK_BYTES = 1024 * 1024


def _sha256_of_file(path: Path) -> str:
    """Return the hex SHA-256 of ``path``'s bytes, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_sidecar_for(report_path: Path) -> Path:
    """Path of the content-hash sidecar beside ``report_path``.

    The report is named ``<stem>_accessibility_report.md``; the sidecar
    swaps the trailing ``.md`` for ``.sha256`` so the two travel
    together and a single ``glob`` of the report finds its partner.
    """
    stem = report_path.name[: -len(".md")]
    return report_path.with_name(f"{stem}.sha256")


# Default search list for the pdfMax checkout. ``$PDFMAX_CHECKER_DIR``
# from the env wins if set; otherwise we walk these candidates and
# pick the first that exists.
#
# ``/opt/pdfmax/checker`` is where the Dockerfile bakes the checker
# (and where docker-compose.override.yml bind-mounts it for dev),
# so the in-container path is the first hit. The remaining entries
# cover bare-metal development setups where pdfMax sits next to
# auto_a11y_python on disk.
_DEFAULT_PDFMAX_CANDIDATES: tuple[Path, ...] = (
    Path("/opt/pdfmax/checker"),
    Path(__file__).resolve().parents[3] / "pdfMax" / "python" / "checker",
    Path(os.path.expanduser("~/Documents/cnib/code/pdfMax/python/checker")),
)


def _resolve_default_pdfmax_dir() -> Path:
    """Pick the first existing default candidate, else the first.

    Returning a non-existent path when nothing is found lets the
    caller's ``is_dir()`` check raise a clean :class:`PdfMaxRunError`
    instead of leaking the env-var search internals.
    """
    env_value = os.environ.get("PDFMAX_CHECKER_DIR")
    if env_value:
        return Path(env_value)
    for candidate in _DEFAULT_PDFMAX_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return _DEFAULT_PDFMAX_CANDIDATES[0]


@dataclass(frozen=True)
class PdfMaxReport:
    """Result of a successful pdfMax audit run."""

    markdown: str
    """Raw Markdown content of pdfMax's ``*_accessibility_report.md``."""

    output_dir: Path
    """Directory pdfMax wrote into. Holds the .md plus extracted
    images that the markdown references with relative ``![](image_N.png)``
    links."""


class PdfMaxRunError(RuntimeError):
    """Raised when the pdfMax subprocess fails or its output is missing."""


def run_pdfmax(
    *,
    pdf_path: Path,
    output_dir: Path,
    wcag_level: Literal["AA", "AAA"] = "AA",
    skip_claude: bool = True,
    pdfmax_dir: Path | None = None,
    timeout_seconds: float = 600.0,
) -> PdfMaxReport:
    """Run pdfMax's audit tool against ``pdf_path``.

    Writes pdfMax's outputs (the .md report + extracted images +
    issue-map JSON) into ``output_dir``. The Markdown is read back
    and returned alongside the directory so the route can stream
    referenced images.

    ``skip_claude`` defaults to ``True`` because the auto_a11y app
    doesn't have an Anthropic API key configured by default. When the
    flag is on, pdfMax's ``--no-claude`` switch is passed and the
    AI-driven sections of the report ("AI Visual Analysis", AI
    executive summary, etc.) are simply omitted from the Markdown.

    Raises :class:`PdfMaxRunError` if pdfMax's checkout isn't
    present, the subprocess returns non-zero, or the expected
    ``*_accessibility_report.md`` file isn't written.
    """
    pdfmax_dir = pdfmax_dir or _resolve_default_pdfmax_dir()
    if not pdfmax_dir.is_dir():
        raise PdfMaxRunError(
            f"pdfMax checker directory not found: {pdfmax_dir!s}. "
            + "Set PDFMAX_CHECKER_DIR in your .env or environment to "
            + "the absolute path of pdfMax/python/checker."
        )

    audit_script = pdfmax_dir / "pdf_accessibility_audit.py"
    if not audit_script.is_file():
        raise PdfMaxRunError(
            f"pdfMax audit script missing: {audit_script!s}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    python_executable = _select_python(pdfmax_dir)
    cmd: list[str] = [
        str(python_executable),
        str(audit_script),
        str(pdf_path),
        "--output-dir",
        str(output_dir),
        "--wcag-level",
        wcag_level,
    ]
    if skip_claude:
        cmd.append("--no-claude")

    logger.info("Running pdfMax audit: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            cwd=str(pdfmax_dir),
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfMaxRunError(
            f"pdfMax audit timed out after {timeout_seconds}s"
        ) from exc

    if result.returncode != 0:
        raise PdfMaxRunError(
            f"pdfMax audit failed (exit {result.returncode}):\n"
            + (result.stderr or result.stdout or "(no output)")
        )

    # pdfMax names the report ``<basename>_accessibility_report.md``.
    basename = pdf_path.stem
    report_path = output_dir / f"{basename}_accessibility_report.md"
    if not report_path.is_file():
        # Fall back to scanning the directory for any *_accessibility_report.md
        # — covers the case where pdfMax sanitised the basename.
        candidates = sorted(output_dir.glob("*_accessibility_report.md"))
        if not candidates:
            raise PdfMaxRunError(
                f"pdfMax did not produce a Markdown report under {output_dir!s}"
            )
        report_path = candidates[0]

    markdown = report_path.read_text(encoding="utf-8")
    return PdfMaxReport(markdown=markdown, output_dir=output_dir)


def cached_or_run_pdfmax(
    *,
    pdf_path: Path,
    cache_dir: Path,
    wcag_level: Literal["AA", "AAA"] = "AA",
    skip_claude: bool = True,
    pdfmax_dir: Path | None = None,
) -> PdfMaxReport:
    """Re-use a cached pdfMax run when the PDF's content is unchanged.

    pdfMax's audit costs 5-30s on a typical document; we don't want
    to pay that on every detail-page render. The cache is keyed on the
    SHA-256 of the source PDF's bytes (matching the module docstring's
    contract), persisted in a ``*_accessibility_report.sha256`` sidecar
    beside the report. We reuse the cached report only when the current
    PDF hashes to that stored value, so overwriting the PDF in place —
    even with an identical mtime — correctly busts the cache. ``cache_dir``
    is per-PdfDocument so we never serve one document's report against
    another's.

    The ``.md`` mtime is used only as a cheap fast-path: if it already
    post-dates the PDF *and* a sidecar hash matches, we reuse without
    re-hashing on subsequent renders. Whenever the mtime fast-path does
    not apply (e.g. an in-place overwrite that preserves the mtime) we
    fall back to the authoritative content-hash comparison.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    existing_reports = sorted(cache_dir.glob("*_accessibility_report.md"))
    if existing_reports:
        report_path = existing_reports[0]
        sidecar = _hash_sidecar_for(report_path)
        try:
            current_hash = _sha256_of_file(pdf_path)
            stored_hash = (
                sidecar.read_text(encoding="utf-8").strip()
                if sidecar.is_file()
                else None
            )
            if stored_hash is not None and stored_hash == current_hash:
                logger.debug(
                    "Reusing cached pdfMax report at %s (content hash match)",
                    report_path,
                )
                return PdfMaxReport(
                    markdown=report_path.read_text(encoding="utf-8"),
                    output_dir=cache_dir,
                )
        except OSError:
            # Stat/read/hash failed — fall through to a fresh run.
            pass

    # Cache miss / stale: clear and re-run. We prune just the .md and its
    # hash sidecar; we don't touch the parent dir, which may be the PDF's
    # home directory under ``PdfStorage``.
    for stale in cache_dir.glob("*_accessibility_report.md"):
        for path in (stale, _hash_sidecar_for(stale)):
            try:
                path.unlink()
            except OSError:
                pass

    report = run_pdfmax(
        pdf_path=pdf_path,
        output_dir=cache_dir,
        wcag_level=wcag_level,
        skip_claude=skip_claude,
        pdfmax_dir=pdfmax_dir,
    )

    # Persist the content hash next to the freshly written report so the
    # next render can decide cache validity by content, not mtime. A
    # failure here is non-fatal: a missing sidecar simply forces the next
    # call to re-run, which is the safe (correct-but-slower) direction.
    fresh_reports = sorted(report.output_dir.glob("*_accessibility_report.md"))
    if fresh_reports:
        try:
            _hash_sidecar_for(fresh_reports[0]).write_text(
                _sha256_of_file(pdf_path), encoding="utf-8"
            )
        except OSError:
            logger.warning(
                "Could not write pdfMax cache hash sidecar in %s",
                report.output_dir,
            )
    return report


def _select_python(pdfmax_dir: Path) -> Path:
    """Pick the python executable that has pdfMax's deps installed.

    Prefer the venv that ships alongside the pdfMax checkout
    (``../../.venv/bin/python``), then the local checker venv if one
    exists, then ``$PATH`` python3. The chosen interpreter must have
    ``pikepdf`` available — pdfMax's import-time machinery will
    otherwise abort.
    """
    candidates = [
        pdfmax_dir.parent.parent / ".venv" / "bin" / "python3",
        pdfmax_dir.parent.parent / ".venv" / "bin" / "python",
        pdfmax_dir / ".venv" / "bin" / "python3",
        pdfmax_dir / ".venv" / "bin" / "python",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    fallback = shutil.which("python3") or shutil.which("python")
    if fallback is None:
        raise PdfMaxRunError(
            "No usable python interpreter found for pdfMax subprocess"
        )
    return Path(fallback)
