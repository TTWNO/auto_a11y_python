"""Minimal type stubs for the sklearn.cluster symbols we use.

Only ``AgglomerativeClustering`` is declared — it's the single clustering
class invoked by ``auto_a11y.audio.speaker_identification``.
"""
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray


class AgglomerativeClustering:
    def __init__(
        self,
        n_clusters: int | None = 2,
        *,
        metric: Literal["euclidean", "l1", "l2", "manhattan", "cosine", "precomputed"] = "euclidean",
        memory: object | None = None,
        connectivity: object | None = None,
        compute_full_tree: bool | Literal["auto"] = "auto",
        linkage: Literal["ward", "complete", "average", "single"] = "ward",
        distance_threshold: float | None = None,
        compute_distances: bool = False,
    ) -> None: ...
    def fit_predict(
        self,
        X: NDArray[np.floating[Any]],
        y: object | None = None,
    ) -> NDArray[np.integer[Any]]: ...
