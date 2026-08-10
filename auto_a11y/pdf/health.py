"""Pure-function health check for the PDF audit subsystem."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auto_a11y.pdf.audit.rasterize import renderer_available


@dataclass(frozen=True)
class RendererHealth:
    """Whether page rasterisation is available.

    Replaces the former Ghostscript probe. Rasterisation now runs in-process
    through PDFium, which ships inside its wheel, so this is really asking
    whether the environment was installed from requirements — it has no
    external binary to find.
    """

    available: bool
    engine: str


@dataclass(frozen=True)
class StorageHealth:
    dir: str
    writable: bool


@dataclass(frozen=True)
class PdfHealth:
    renderer: RendererHealth
    storage: StorageHealth

    @property
    def ok(self) -> bool:
        return self.renderer.available and self.storage.writable


def check_pdf_health(*, storage_dir: Path) -> PdfHealth:
    """Compute PDF subsystem health.

    Side effect: creates ``storage_dir`` if missing (and a probe file briefly).
    Returns a status snapshot suitable for ``/api/health/pdf``.
    """
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
        renderer=RendererHealth(available=renderer_available(), engine='pdfium'),
        storage=StorageHealth(dir=str(storage_dir), writable=storage_writable),
    )
