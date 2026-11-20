"""Tests for scripts/claude/r3_semantic.py.

passages() and the SQLite cache layer run offline with a fake scorer. Tests that
need onnxruntime, tokenizers and the downloaded model files are skipped when any of
them is missing (CI has no models and no network).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from scripts.claude import r3_semantic as rs

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# passages()
# ---------------------------------------------------------------------------


def _words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


def test_passages_empty_and_short():
    assert rs.passages("") == []
    assert rs.passages("   \n\t ") == []
    assert rs.passages("one  two\nthree") == ["one two three"]
    assert rs.passages(_words(200)) == [_words(200)]


def test_passages_windows_overlap_and_cover_every_word():
    text = _words(450)
    out = rs.passages(text, words=200, stride=100, max_passages=12)
    # Grid starts 0, 100, 200 and an end-aligned window at 250.
    assert [p.split()[0] for p in out] == ["w0", "w100", "w200", "w250"]
    assert all(len(p.split()) == 200 for p in out)
    covered = {w for p in out for w in p.split()}
    assert covered == set(text.split())


def test_passages_exact_grid_has_no_extra_window():
    out = rs.passages(_words(400), words=200, stride=100)
    assert [p.split()[0] for p in out] == ["w0", "w100", "w200"]
    assert out[-1].split()[-1] == "w399"


def test_passages_max_passages_head_and_spread():
    text = _words(5000)
    head = rs.passages(text, words=200, stride=100, max_passages=12)
    assert len(head) == 12
    assert [p.split()[0] for p in head] == [f"w{100 * i}" for i in range(12)]
    spread = rs.passages(text, words=200, stride=100, max_passages=12, spread=True)
    assert len(spread) == 12
    assert spread[0].split()[0] == "w0"
    assert spread[-1].split()[-1] == "w4999"
    firsts = [int(p.split()[0][1:]) for p in spread]
    assert firsts == sorted(firsts)


def test_passages_small_windows_and_bad_args():
    out = rs.passages("a b c d e", words=2, stride=2, max_passages=10)
    assert out == ["a b", "c d", "d e"]
    with pytest.raises(ValueError):
        rs.passages("a b", words=0)
    with pytest.raises(ValueError):
        rs.passages("a b", stride=0)
    with pytest.raises(ValueError):
        rs.passages("a b", max_passages=0)


# ---------------------------------------------------------------------------
# Cache layer with a fake scorer
# ---------------------------------------------------------------------------


def _fake_value(key: str, dim: int) -> np.ndarray:
    seed = int(key[:8], 16)
    return np.random.default_rng(seed).standard_normal(dim).astype(np.float32)


class FakeScorer:
    def __init__(self, keys: list[str], dim: int) -> None:
        self.keys = keys
        self.dim = dim
        self.calls: list[list[int]] = []

    def __call__(self, idx: list[int]) -> np.ndarray:
        self.calls.append(list(idx))
        return np.stack([_fake_value(self.keys[i], self.dim) for i in idx])

    @property
    def computed(self) -> int:
        return sum(len(c) for c in self.calls)


def test_cache_key_depends_on_every_field():
    base = ("cross", "minilm-l6", "rev1", 512, "q", "t")
    k = rs.cache_key(*base)
    assert len(k) == 40 and k == rs.cache_key(*base)
    for i, alt in enumerate(("embed", "minilm-l12", "rev2", 256, "q2", "t2")):
        changed = list(base)
        changed[i] = alt
        assert rs.cache_key(*changed) != k
    # Field boundaries are not ambiguous.
    assert rs.cache_key("cross", "m", "r", 1, "ab", "c") != rs.cache_key("cross", "m", "r", 1, "a", "bc")


def test_cached_compute_scalar_second_call_is_free(tmp_path):
    cache = rs.ScoreCache(tmp_path / "scores.sqlite")
    keys = [rs.cache_key("cross", "fake", "r", 512, "q", f"text {i}") for i in range(50)]
    s1 = FakeScorer(keys, 1)
    a = rs.cached_compute(keys, s1, 1, cache, chunk=16)
    assert a.shape == (50, 1) and a.dtype == np.float32
    assert s1.computed == 50
    assert max(len(c) for c in s1.calls) <= 16
    s2 = FakeScorer(keys, 1)
    b = rs.cached_compute(keys, s2, 1, cache)
    assert s2.computed == 0
    assert np.array_equal(a, b)
    assert cache.count() == 50
    cache.close()


def test_cached_compute_dedups_and_fills_only_misses(tmp_path):
    cache = rs.ScoreCache(tmp_path / "scores.sqlite")
    keys = [rs.cache_key("embed", "fake", "r", 512, "doc", t) for t in ("a", "b", "a", "c", "b")]
    s = FakeScorer(keys, 4)
    out = rs.cached_compute(keys, s, 4, cache)
    assert s.computed == 3  # a, b, c once each
    assert np.array_equal(out[0], out[2]) and np.array_equal(out[1], out[4])
    more = keys + [rs.cache_key("embed", "fake", "r", 512, "doc", "d")]
    s2 = FakeScorer(more, 4)
    out2 = rs.cached_compute(more, s2, 4, cache)
    assert s2.calls == [[5]]
    assert np.array_equal(out2[:5], out)
    for i, k in enumerate(more):
        assert np.allclose(out2[i], _fake_value(k, 4))
    cache.close()


def test_cached_compute_order_hint_and_no_cache():
    keys = [rs.cache_key("cross", "fake", "r", 512, "q", t) for t in ("long text here", "x", "mid t")]
    s = FakeScorer(keys, 1)
    out = rs.cached_compute(keys, s, 1, None, order_hint=[14, 1, 5])
    assert s.calls == [[1, 2, 0]]
    assert np.allclose(out[:, 0], [_fake_value(k, 1)[0] for k in keys])


def test_cached_compute_rejects_wrong_dim(tmp_path):
    cache = rs.ScoreCache(tmp_path / "scores.sqlite")
    keys = [rs.cache_key("embed", "fake", "r", 512, "doc", "a")]
    rs.cached_compute(keys, FakeScorer(keys, 4), 4, cache)
    with pytest.raises(ValueError):
        rs.cached_compute(keys, FakeScorer(keys, 3), 3, cache)
    cache.close()


def test_cache_persists_across_instances(tmp_path):
    path = tmp_path / "sub" / "scores.sqlite"
    c1 = rs.ScoreCache(path)
    c1.put_many({"k1": np.array([1.5], dtype=np.float32), "k2": np.arange(3, dtype=np.float32)})
    c1.put_many({"k1": np.array([9.0], dtype=np.float32)})  # first write wins
    c1.close()
    c2 = rs.ScoreCache(path)
    got = c2.get_many(["k1", "k2", "missing"])
    assert set(got) == {"k1", "k2"}
    assert got["k1"].tolist() == [1.5]
    assert got["k2"].tolist() == [0.0, 1.0, 2.0]
    assert c2.get_many([]) == {}
    c2.close()


def test_cache_many_keys_lookup_chunks(tmp_path):
    cache = rs.ScoreCache(tmp_path / "scores.sqlite")
    items = {f"key{i}": np.array([i], dtype=np.float32) for i in range(2500)}
    cache.put_many(items)
    got = cache.get_many(list(items))
    assert len(got) == 2500 and got["key2499"].tolist() == [2499.0]
    cache.close()


_WORKER = textwrap.dedent(
    """
    import sys
    import numpy as np
    from scripts.claude import r3_semantic as rs

    path, wid = sys.argv[1], int(sys.argv[2])
    cache = rs.ScoreCache(path)
    # Overlapping key ranges, small chunks, so writers interleave.
    keys = [rs.cache_key("cross", "fake", "r", 512, "q", f"t{i}") for i in range(wid * 150, wid * 150 + 600)]

    def compute(idx):
        return np.array([int(keys[i][:6], 16) % 1000 for i in idx], dtype=np.float32)

    for _ in range(3):
        out = rs.cached_compute(keys, compute, 1, cache, chunk=25)
        assert np.array_equal(out[:, 0], compute(list(range(len(keys))))), "wrong value"
    print("ok", wid)
    """
)


def test_cache_safe_with_concurrent_processes(tmp_path):
    path = tmp_path / "shared.sqlite"
    worker = tmp_path / "worker.py"
    worker.write_text(_WORKER)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")])
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(path), str(w)],
            cwd=str(REPO),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for w in range(4)
    ]
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        assert out.startswith("ok")
    cache = rs.ScoreCache(path)
    assert cache.count() == 3 * 150 + 600  # keys t0..t1049
    cache.close()


# ---------------------------------------------------------------------------
# Model files and manifest (offline)
# ---------------------------------------------------------------------------


def test_model_available_false_for_empty_dir(tmp_path):
    assert not rs.model_available("minilm-l6", base=tmp_path)
    with pytest.raises(FileNotFoundError):
        rs.load_model("minilm-l6", base=tmp_path)


def test_ensure_models_skips_network_when_files_present(tmp_path, monkeypatch):
    mdir = tmp_path / "models" / "minilm-l6"
    (mdir / "onnx").mkdir(parents=True)
    files = []
    for name, data in (("onnx/model.onnx", b"fake onnx"), ("tokenizer.json", b"{}")):
        (mdir / name).write_bytes(data)
        files.append({"name": name, "size": len(data), "sha256": rs.sha256_file(mdir / name)})
    meta = {"name": "minilm-l6", "repo_id": "r/x", "revision": "abc", "files": files}
    rs._atomic_write_json(mdir / rs.LOCAL_META, meta)

    def no_network(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(rs, "_download_model", no_network)
    manifest = tmp_path / "models.json"
    out = rs.ensure_models(["minilm-l6"], base=tmp_path / "models", manifest_path=manifest, verify=True)
    assert out["minilm-l6"]["revision"] == "abc"
    written = manifest.read_text()
    assert '"revision": "abc"' in written
    # Second call: same content, file left alone.
    mtime = manifest.stat().st_mtime_ns
    rs.ensure_models(["minilm-l6"], base=tmp_path / "models", manifest_path=manifest)
    assert manifest.stat().st_mtime_ns == mtime
    # A size change makes it refetch.
    (mdir / "tokenizer.json").write_bytes(b"{} ")
    with pytest.raises(AssertionError, match="network used"):
        rs.ensure_models(["minilm-l6"], base=tmp_path / "models", manifest_path=manifest)


def test_repo_manifest_pins_revisions_and_hashes():
    path = rs.MANIFEST_PATH
    if not path.exists():
        pytest.skip("models.json not written yet")
    import json

    models = json.loads(path.read_text())["models"]
    for name in rs.DEFAULT_MODELS:
        m = models[name]
        assert len(m["revision"]) == 40
        names = {f["name"] for f in m["files"]}
        assert set(rs.MODEL_SPECS[name].required_files) <= names
        assert all(len(f["sha256"]) == 64 for f in m["files"])


# ---------------------------------------------------------------------------
# Real models (skipped when not present)
# ---------------------------------------------------------------------------

_HAVE_RUNTIME = all(importlib.util.find_spec(m) is not None for m in ("onnxruntime", "tokenizers"))


def _need(*names: str) -> None:
    if not _HAVE_RUNTIME:
        pytest.skip("onnxruntime or tokenizers not installed")
    missing = [n for n in names if not rs.model_available(n)]
    if missing:
        pytest.skip(f"model files not downloaded: {missing}")


Q = "How many people live in Berlin?"
DOCS = [
    "Berlin had a population of 3,520,031 registered inhabitants in an area of 891.82 square kilometers.",
    "Berlin is well known for its museums.",
]


@pytest.mark.parametrize("model", ["minilm-l6", "minilm-l12"])
def test_cross_encoder_matches_model_card_and_triples(model, tmp_path):
    _need(model)
    cache = rs.ScoreCache(tmp_path / "c.sqlite")
    s = rs.cross_scores(Q, DOCS, model=model, cache=cache)
    assert s.dtype == np.float32 and s.shape == (2,)
    if model == "minilm-l6":  # values published on the model card
        assert np.allclose(s, [8.607138, -4.320078], atol=1e-3)
    assert s[0] > s[1]
    for q, rel, irr in rs.SANITY_TRIPLES:
        r = rs.cross_scores(q, [rel, irr], model=model, cache=cache)
        assert r[0] > r[1], q
    cache.close()


def test_cross_scores_order_cache_and_truncation(tmp_path):
    _need("minilm-l6")
    cache = rs.ScoreCache(tmp_path / "c.sqlite")
    texts = DOCS + ["short", "word " * 900, ""]
    a = rs.cross_scores(Q, texts, cache=cache)
    perm = [3, 0, 4, 2, 1]
    b = rs.cross_scores(Q, [texts[i] for i in perm], use_cache=False)
    assert np.allclose(b, a[perm], atol=1e-4)
    n_before = cache.count()
    c = rs.cross_scores(Q, texts, cache=cache)
    assert np.array_equal(a, c) and cache.count() == n_before
    # Different max_length is a different cache entry.
    rs.cross_scores(Q, texts, max_length=256, cache=cache)
    assert cache.count() == n_before + len(texts)
    assert rs.cross_scores(Q, [], cache=cache).shape == (0,)
    pairs = rs.cross_scores_pairs([(Q, DOCS[0]), ("museums in berlin", DOCS[1])], cache=cache)
    assert pairs.shape == (2,) and pairs[0] == a[0]
    with pytest.raises(ValueError):
        rs.cross_scores(Q, DOCS, model="bge-small")
    cache.close()


def test_embed_normalised_prefix_and_triples(tmp_path):
    _need("bge-small")
    cache = rs.ScoreCache(tmp_path / "c.sqlite")
    d = rs.embed(DOCS, cache=cache)
    assert d.shape == (2, 384) and d.dtype == np.float32
    assert np.allclose(np.linalg.norm(d, axis=1), 1.0, atol=1e-5)
    q = rs.embed([Q], is_query=True, cache=cache)[0]
    assert (d @ q)[0] > (d @ q)[1]
    # The query prefix changes the vector, and the cache keeps the roles apart.
    q_doc = rs.embed([Q], is_query=False, cache=cache)[0]
    assert not np.allclose(q, q_doc)
    assert np.array_equal(rs.embed([Q], is_query=True, cache=cache)[0], q)
    for qq, rel, irr in rs.SANITY_TRIPLES:
        qv = rs.embed([qq], is_query=True, cache=cache)[0]
        v = rs.embed([rel, irr], cache=cache)
        assert v[0] @ qv > v[1] @ qv, qq
    assert rs.embed([], cache=cache).shape == (0, 384)
    cache.close()
