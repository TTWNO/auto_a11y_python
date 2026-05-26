"""Minimal type stubs for the pyannote.audio symbols we use.

``Model`` (and its ``from_pretrained`` classmethod) plus ``Inference``
(and its ``crop`` method) are declared. Upstream pyannote.audio ships
``py.typed`` but several method signatures include untyped
``map_location`` / ``**kwargs`` parameters that propagate ``Unknown``
types into call sites under pyright strict mode. This stub overrides
only the members auto_a11y actually uses with fully-typed signatures.

``Inference.crop`` is declared returning ``NDArray`` because auto_a11y
always constructs it with ``window="whole"`` — the only mode in which
the real method returns a plain numpy array (the "sliding" mode returns
a ``SlidingWindowFeature``, which we never request).
"""
import io
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pyannote.core import Segment


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


class Inference:
    def __init__(
        self,
        model: Model,
        window: str = "sliding",
        duration: float | None = None,
        step: float | None = None,
        skip_aggregation: bool = False,
        skip_conversion: bool = False,
        batch_size: int = 32,
    ) -> None: ...
    def crop(
        self,
        file: str | Path,
        chunk: Segment,
    ) -> NDArray[np.floating[Any]]: ...
