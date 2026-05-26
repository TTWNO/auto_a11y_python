"""Minimal type stubs for the pyannote.core symbols we use.

Only ``Segment`` is declared — it's constructed in
``auto_a11y.audio.speaker_identification`` to delimit the audio span fed
to ``Inference.crop``. Upstream pyannote ships ``py.typed`` but the
``stubs/pyannote`` package shadows the whole namespace, so every
submodule we import needs a local declaration here.
"""


class Segment:
    start: float
    end: float
    def __init__(self, start: float = ..., end: float = ...) -> None: ...
