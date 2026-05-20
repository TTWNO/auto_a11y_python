"""Minimal type stubs for the pyannote.audio symbols we use.

Only ``Model`` (and its ``from_pretrained`` classmethod) is declared.
Upstream pyannote.audio ships ``py.typed`` but several method signatures
include untyped ``map_location`` / ``**kwargs`` parameters that propagate
``Unknown`` types into call sites under pyright strict mode. This stub
overrides only ``Model.from_pretrained`` with a fully-typed signature
covering the kwargs auto_a11y actually passes.
"""
import io
from pathlib import Path


class Model:
    @classmethod
    def from_pretrained(
        cls,
        checkpoint: Path | str | io.BytesIO,
        map_location: object | None = None,
        strict: bool = True,
        subfolder: str | None = None,
        revision: str | None = None,
        token: str | bool | None = None,
        cache_dir: Path | str | None = None,
    ) -> Model | None: ...
