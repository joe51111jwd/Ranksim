"""The cluster bootstrap draws in chunks and still gives the published intervals bit for bit.

The reference below is a frozen copy of the one-block implementation that made the
published intervals. Inputs are random numbers made here. No network.
"""

from __future__ import annotations

import numpy as np
import pytest

from aeo.ranksim.forecast import stats
from aeo.ranksim.forecast.stats import (
    BOOTSTRAP_CHUNK_DRAWS,
    _cluster_sums,
    cluster_bootstrap_diff,
    cluster_bootstrap_ratio,
)


def frozen_ratio(numer, denom, clusters, *, draws=2000, seed=20261122):
    """The implementation before chunking, copied as it was. Do not edit."""
    tops, bottoms = _cluster_sums(numer, denom, clusters)
    count = len(tops)
    if count < 2 or bottoms.sum() <= 0:
        return None, None
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, count, size=(draws, count))
    top = tops[picks].sum(axis=1)
    bottom = bottoms[picks].sum(axis=1)
    keep = bottom > 0
    if not keep.any():
        return None, None
    rates = top[keep] / bottom[keep]
    low, high = np.quantile(rates, [0.025, 0.975])
    return float(low), float(high)


def frozen_diff(first, second, clusters, *, draws=2000, seed=20261122):
    if not first:
        return None, None, None
    gaps = [float(a) - float(b) for a, b in zip(first, second)]
    point = sum(gaps) / len(gaps)
    low, high = frozen_ratio(gaps, [1.0] * len(gaps), clusters, draws=draws, seed=seed)
    return point, low, high


def random_input(rng: np.random.Generator, rows: int, clusters: int, sparse_denom: bool = False):
    numer = rng.integers(0, 2, size=rows).astype(float).tolist()
    denom = (rng.random(rows) < 0.2).astype(float).tolist() if sparse_denom else [1.0] * rows
    keys = [f"site{int(k)}.example" for k in rng.integers(0, clusters, size=rows)]
    return numer, denom, keys


@pytest.mark.parametrize("rows,clusters,sparse", [(40, 7, False), (500, 120, True), (3, 2, False), (900, 900, False)])
def test_chunked_ratio_is_bit_identical_to_the_frozen_one_block_draws(rows, clusters, sparse):
    rng = np.random.default_rng(rows * 31 + clusters)
    for trial in range(3):
        numer, denom, keys = random_input(rng, rows, clusters, sparse)
        for seed in (20261122, trial):
            want = frozen_ratio(numer, denom, keys, seed=seed)
            assert cluster_bootstrap_ratio(numer, denom, keys, seed=seed) == want
            assert cluster_bootstrap_ratio(numer, denom, keys, seed=seed, chunk_draws=7) == want
            assert cluster_bootstrap_ratio(numer, denom, keys, seed=seed, chunk_draws=1999) == want


def test_chunked_ratio_matches_with_many_clusters_and_odd_draw_counts():
    rng = np.random.default_rng(5)
    numer, denom, keys = random_input(rng, 12000, 9000)
    want = frozen_ratio(numer, denom, keys, draws=333)
    assert cluster_bootstrap_ratio(numer, denom, keys, draws=333) == want
    assert cluster_bootstrap_ratio(numer, denom, keys, draws=333, chunk_draws=50) == want


def test_chunked_diff_is_bit_identical_to_the_frozen_one():
    rng = np.random.default_rng(11)
    for _ in range(3):
        first = rng.integers(0, 2, size=300).astype(float).tolist()
        second = rng.integers(0, 2, size=300).astype(float).tolist()
        keys = [f"s{int(k)}" for k in rng.integers(0, 60, size=300)]
        assert cluster_bootstrap_diff(first, second, keys) == frozen_diff(first, second, keys)
        assert cluster_bootstrap_diff(first, second, keys, chunk_draws=13) == frozen_diff(first, second, keys)


def test_bootstrap_never_builds_more_than_one_chunk_of_picks(monkeypatch):
    sizes: list[tuple[int, int]] = []
    real = np.random.default_rng

    class Watch:
        def __init__(self, seed):
            self._rng = real(seed)

        def integers(self, low, high, size):
            sizes.append(tuple(size))
            return self._rng.integers(low, high, size=size)

    monkeypatch.setattr(stats.np.random, "default_rng", Watch)
    numer = [1.0, 0.0] * 500
    keys = [f"s{i}" for i in range(1000)]
    cluster_bootstrap_ratio(numer, [1.0] * 1000, keys)
    assert sizes and all(rows <= BOOTSTRAP_CHUNK_DRAWS for rows, _ in sizes)
    assert sum(rows for rows, _ in sizes) == 2000


def test_empty_and_single_cluster_inputs_still_give_no_interval():
    assert cluster_bootstrap_ratio([1.0], [1.0], ["a"]) == (None, None)
    assert cluster_bootstrap_ratio([1.0, 0.0], [0.0, 0.0], ["a", "b"]) == (None, None)
    assert cluster_bootstrap_ratio([1.0, 0.0], [1.0, 1.0], ["a", "b"], draws=0) == (None, None)
    with pytest.raises(ValueError):
        cluster_bootstrap_ratio([1.0, 0.0], [1.0, 1.0], ["a", "b"], chunk_draws=0)
