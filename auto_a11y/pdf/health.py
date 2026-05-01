"""Pure-function health check for the PDF audit subsystem."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auto_a11y.pdf.audit.ghostscript import detect_ghostscript


@dataclass(frozen=True)
class GhostscriptHealth:
    found: bool
    path: str | None


@dataclass(frozen=True)
class StorageHealth:
    dir: str
    writable: bool


@dataclass(frozen=True)
class PdfHealth:
    ghostscript: GhostscriptHealth
    storage: StorageHealth

    @property
    def ok(self) -> bool:
        return self.ghostscript.found and self.storage.writable


def check_pdf_health(*, gs_override: str | None, storage_dir: Path) -> PdfHealth:
    """Compute PDF subsystem health.

    Side effect: creates ``storage_dir`` if missing (and a probe file briefly).
    Returns a status snapshot suitable for ``/api/health/pdf``.
    """
    gs_path = detect_ghostscript(override=gs_override)

    storage_writable = False
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        probe = storage_dir / '.probe'
        probe.touch()
        probe.unlink()
        storage_writable = True
    except OSError:
        pass

    return PdfHealth(
        ghostscript=GhostscriptHealth(found=gs_path is not None, path=gs_path),
        storage=StorageHealth(dir=str(storage_dir), writable=storage_writable),
    )
