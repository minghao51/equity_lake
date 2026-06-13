"""Sample uniqueness weights (de Prado, Ch. 4) for meta-label training.

When multiple candidate trades overlap in time, their labels carry
redundant information. The Concurrent Sample Uniqueness algorithm assigns
each sample a weight inversely proportional to how many other active
trades overlap with its evaluation window.

Reference:
    López de Prado, M. (2018). *Advances in Financial Machine Learning*.
    Chapter 4, "Sample Uniqueness".
"""

from __future__ import annotations

import numpy as np

from equity_lake.core.polars_utils import FrameLike, ensure_polars


def compute_concurrency_matrix(
    start_indices: np.ndarray,
    end_indices: np.ndarray,
) -> np.ndarray:
    """Build the symmetric concurrency matrix c_t1,t2.

    ``c[i, j]`` = 1 if sample ``i`` and sample ``j`` overlap (share at
    least one row index), else 0. The diagonal is 1 by definition.
    """
    n = len(start_indices)
    concurrency = np.eye(n, dtype=np.float64)
    for i in range(n):
        active = (start_indices <= end_indices[i]) & (end_indices >= start_indices[i])
        concurrency[i, active] = 1.0
    return concurrency


def compute_sample_uniqueness(
    start_indices: np.ndarray,
    end_indices: np.ndarray,
) -> np.ndarray:
    """Return average uniqueness for each sample.

    For sample ``i`` with evaluation window ``[t_i0, t_i1]``, uniqueness
    is the sum over the window of ``1 / c_t,t'`` where ``c`` is the
    number of samples active at time ``t``. We approximate this using
    the integral over the window divided by the number of overlapping
    samples per timestamp.

    For a tractable approximation we use the mean inverse concurrency:
    ``u_i = (1 / T_i) * sum_t (1 / c_t)`` for t in ``[t_i0, t_i1]``.
    """
    n = len(start_indices)
    if n == 0:
        return np.zeros(0, dtype=np.float64)

    n_steps = int(end_indices.max()) + 1 if n > 0 else 0
    if n_steps <= 0:
        return np.ones(n, dtype=np.float64)

    counts: np.ndarray = np.zeros(n_steps, dtype=np.float64)
    for s, e in zip(start_indices, end_indices, strict=False):
        if s < 0 or e < 0:
            continue
        counts[s : e + 1] += 1.0

    counts = np.maximum(counts, 1.0)

    uniqueness = np.ones(n, dtype=np.float64)
    for i in range(n):
        s, e = int(start_indices[i]), int(end_indices[i])
        if s < 0 or e < 0:
            uniqueness[i] = 0.0
            continue
        window = counts[s : e + 1]
        uniqueness[i] = float((1.0 / window).mean())
    return uniqueness


def compute_uniqueness_weights(labels_df: FrameLike) -> np.ndarray:
    """Compute sample weights from a labeled barrier DataFrame.

    Expects ``barrier_start_idx`` and ``barrier_end_idx`` columns (added
    by ``apply_triple_barrier_labels``). Returns a numpy array of weights
    aligned with the input row order. Missing/invalid windows yield 0.
    """
    frame = ensure_polars(labels_df)
    if frame.is_empty():
        return np.zeros(0, dtype=np.float64)
    if "barrier_start_idx" not in frame.columns or "barrier_end_idx" not in frame.columns:
        return np.ones(frame.height, dtype=np.float64)
    starts = frame["barrier_start_idx"].to_numpy().astype(np.int64)
    ends = frame["barrier_end_idx"].to_numpy().astype(np.int64)
    uniqueness = compute_sample_uniqueness(starts, ends)
    return uniqueness
