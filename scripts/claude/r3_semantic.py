"""Round 3: cached, CPU-only query-to-text relevance scores from free pretrained models.

Features for the history-free content model (GOAL.md, Round 3). Inference runs on
onnxruntime with word pieces from ``tokenizers``. No torch, no GPU, no API key.

Models (short name, Hub repo, what it returns):
  minilm-l6   cross-encoder/ms-marco-MiniLM-L-6-v2   raw logit per (query, text) pair
  minilm-l12  cross-encoder/ms-marco-MiniLM-L-12-v2  same, 12 layers (slower)
  bge-small   BAAI/bge-small-en-v1.5                 384-dim embedding, CLS pooling, L2 norm;
                                                     queries get the bge instruction prefix

Model files live outside git in /workspace/ranksim-data/models/<name>/ (override with
R3_MODELS_DIR). evals/claude/r3/models.json in the repo pins each model's repo id,
revision (commit hash), file names, sizes and sha256, so a run can be reproduced.
When that file already pins a model, ensure_models() downloads exactly that revision
and fails if any sha256 differs.

Scores are cached in SQLite (WAL, busy timeout) at
/workspace/ranksim-data/cache/r3_semantic/scores.sqlite (override with
R3_SEMANTIC_CACHE_DIR). Key: sha1 of kind, model name, revision, max_length, query
(or the embedding role) and text. Several processes may share the cache.

Threads: each process uses all cores (os.cpu_count()) for onnxruntime. When running
several scoring processes at once, set R3_ORT_THREADS to cores / processes.

Commands:
  python scripts/claude/r3_semantic.py download   fetch models (idempotent, resumable)
  python scripts/claude/r3_semantic.py verify     recheck sha256 of local files
  python scripts/claude/r3_semantic.py bench      throughput and sanity triples
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "evals" / "claude" / "r3" / "models.json"
DEFAULT_MODELS_DIR = Path("/workspace/ranksim-data/models")
DEFAULT_CACHE_DIR = Path("/workspace/ranksim-data/cache/r3_semantic")
LOCAL_META = "ranksim_meta.json"

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@dataclass(frozen=True)
class ModelSpec:
    name: str
    kind: str  # "cross" or "embed"
    repo_id: str
    mirror: str | None = None  # fallback repo with the same file layout
    onnx_file: str = "onnx/model.onnx"
    files: tuple[str, ...] = ("tokenizer.json", "config.json", "tokenizer_config.json")
    optional_files: tuple[str, ...] = ()
    pooling: str | None = None
    query_prefix: str = ""
    max_length: int = 512

    @property
    def required_files(self) -> tuple[str, ...]:
        return (self.onnx_file,) + self.files


MODEL_SPECS: dict[str, ModelSpec] = {
    "minilm-l6": ModelSpec(
        name="minilm-l6",
        kind="cross",
        repo_id="cross-encoder/ms-marco-MiniLM-L-6-v2",
        mirror="Xenova/ms-marco-MiniLM-L-6-v2",
    ),
    "minilm-l12": ModelSpec(
        name="minilm-l12",
        kind="cross",
        repo_id="cross-encoder/ms-marco-MiniLM-L-12-v2",
        mirror="Xenova/ms-marco-MiniLM-L-12-v2",
    ),
    "bge-small": ModelSpec(
        name="bge-small",
        kind="embed",
        repo_id="BAAI/bge-small-en-v1.5",
        mirror="Xenova/bge-small-en-v1.5",
        optional_files=("1_Pooling/config.json",),
        pooling="cls",
        query_prefix=BGE_QUERY_PREFIX,
    ),
}
DEFAULT_MODELS = ("minilm-l6", "minilm-l12", "bge-small")
CROSS_MODELS = tuple(n for n, s in MODEL_SPECS.items() if s.kind == "cross")

# Batching: sort by token length, then cut batches so that batch_size * padded_length
# stays under this many tokens (measured on this box: larger budgets do not help).
MAX_BATCH_TOKENS = 8192
MAX_BATCH_SIZE = 64
# Misses are computed and written to the cache in chunks of this many items, so a
# crash loses at most one chunk of work.
COMPUTE_CHUNK = 1024


def models_dir() -> Path:
    return Path(os.environ.get("R3_MODELS_DIR", str(DEFAULT_MODELS_DIR)))


def cache_dir() -> Path:
    return Path(os.environ.get("R3_SEMANTIC_CACHE_DIR", str(DEFAULT_CACHE_DIR)))


# ---------------------------------------------------------------------------
# Passages
# ---------------------------------------------------------------------------


def passages(
    text: str,
    words: int = 200,
    stride: int = 100,
    max_passages: int = 12,
    *,
    spread: bool = False,
) -> list[str]:
    """Overlapping word windows over a page body.

    Whitespace is collapsed. Every window has exactly ``words`` words: windows start
    at 0, stride, 2*stride, ... and, when the grid does not reach the end, one more
    window is end-aligned (starts at n - words), so every word is in at least one
    window when stride <= words. Text with at most ``words`` words gives one passage;
    empty text gives []. When there are more than ``max_passages`` windows, the first
    ``max_passages`` are kept (the head of the page), or with spread=True
    ``max_passages`` windows evenly spaced from first to last.
    """
    if words <= 0 or stride <= 0 or max_passages <= 0:
        raise ValueError("words, stride and max_passages must be positive")
    toks = text.split()
    n = len(toks)
    if n == 0:
        return []
    if n <= words:
        return [" ".join(toks)]
    starts = list(range(0, n - words + 1, stride))
    if starts[-1] + words < n:
        starts.append(n - words)
    if len(starts) > max_passages:
        if spread:
            idx = np.linspace(0, len(starts) - 1, max_passages).round().astype(int)
            starts = [starts[i] for i in sorted(set(idx.tolist()))]
        else:
            starts = starts[:max_passages]
    return [" ".join(toks[s : s + words]) for s in starts]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def cache_key(kind: str, model: str, revision: str, max_length: int, query: str, text: str) -> str:
    """sha1 over the fields joined by the ASCII unit separator."""
    raw = "\x1f".join((kind, model, revision, str(int(max_length)), query, text))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class ScoreCache:
    """Key to float32 vector store in SQLite, safe for several processes at once.

    WAL journal, 60 s busy timeout, INSERT OR IGNORE inside BEGIN IMMEDIATE, retries
    on 'database is locked'. One connection per process (reopened after fork).
    """

    _LOOKUP_CHUNK = 900

    def __init__(self, path: str | Path, timeout_s: float = 60.0) -> None:
        self.path = Path(path)
        self.timeout_s = timeout_s
        self._conn: sqlite3.Connection | None = None
        self._pid: int | None = None
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None and self._pid == os.getpid():
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path), timeout=self.timeout_s, isolation_level=None, check_same_thread=False
        )
        conn.execute(f"PRAGMA busy_timeout={int(self.timeout_s * 1000)}")
        self._retry(lambda: conn.execute("PRAGMA journal_mode=WAL"))
        conn.execute("PRAGMA synchronous=NORMAL")
        self._retry(
            lambda: conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value BLOB NOT NULL) WITHOUT ROWID"
            )
        )
        self._conn, self._pid = conn, os.getpid()
        return conn

    def _retry(self, fn: Callable[[], object], tries: int = 8) -> object:
        delay = 0.05
        for attempt in range(tries):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if ("locked" not in msg and "busy" not in msg) or attempt == tries - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 2.0)
        raise AssertionError("unreachable")

    def get_many(self, keys: Sequence[str]) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        if not keys:
            return out
        uniq = list(dict.fromkeys(keys))
        with self._lock:
            conn = self._connect()
            for i in range(0, len(uniq), self._LOOKUP_CHUNK):
                part = uniq[i : i + self._LOOKUP_CHUNK]
                marks = ",".join("?" * len(part))
                rows = self._retry(
                    lambda: conn.execute(f"SELECT key, value FROM kv WHERE key IN ({marks})", part).fetchall()
                )
                for k, v in rows:  # type: ignore[union-attr]
                    out[k] = np.frombuffer(v, dtype="<f4").copy()
        return out

    def put_many(self, items: dict[str, np.ndarray]) -> None:
        if not items:
            return
        rows = [(k, np.asarray(v, dtype="<f4").reshape(-1).tobytes()) for k, v in items.items()]
        with self._lock:
            conn = self._connect()

            def write() -> None:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.executemany("INSERT OR IGNORE INTO kv(key, value) VALUES (?, ?)", rows)
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise

            self._retry(write)

    def count(self) -> int:
        with self._lock:
            conn = self._connect()
            return int(conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and self._pid == os.getpid():
                self._conn.close()
            self._conn, self._pid = None, None


_CACHES: dict[str, ScoreCache] = {}


def get_cache(directory: str | Path | None = None) -> ScoreCache:
    d = Path(directory) if directory is not None else cache_dir()
    path = str(d / "scores.sqlite")
    if path not in _CACHES:
        _CACHES[path] = ScoreCache(path)
    return _CACHES[path]


def cached_compute(
    keys: Sequence[str],
    compute: Callable[[list[int]], np.ndarray],
    dim: int,
    cache: ScoreCache | None,
    *,
    order_hint: Sequence[int] | None = None,
    chunk: int = COMPUTE_CHUNK,
) -> np.ndarray:
    """Rows for ``keys`` (shape (len(keys), dim), float32), computing only misses.

    ``compute(indices)`` gets indices into ``keys`` (one per distinct missing key, at
    most ``chunk`` per call) and returns an array of shape (len(indices), dim) or
    (len(indices),) when dim == 1. Each chunk is written to the cache before the next
    one is computed. ``order_hint`` (for example input lengths) groups similar items
    into the same chunk.
    """
    n = len(keys)
    out = np.empty((n, dim), dtype=np.float32)
    found = cache.get_many(keys) if cache is not None else {}
    first: dict[str, int] = {}
    for i, k in enumerate(keys):
        first.setdefault(k, i)
    missing = [i for k, i in first.items() if k not in found]
    if order_hint is not None:
        missing.sort(key=lambda i: order_hint[i])
    for c in range(0, len(missing), chunk):
        idx = missing[c : c + chunk]
        vals = np.asarray(compute(idx), dtype=np.float32).reshape(len(idx), dim)
        for j, i in enumerate(idx):
            found[keys[i]] = vals[j]
        if cache is not None:
            cache.put_many({keys[i]: vals[j] for j, i in enumerate(idx)})
    for i, k in enumerate(keys):
        v = found[k]
        if v.size != dim:
            raise ValueError(f"cached value for key {k} has size {v.size}, expected {dim}")
        out[i] = v
    return out


# ---------------------------------------------------------------------------
# Model files
# ---------------------------------------------------------------------------


def sha256_file(path: str | Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _atomic_write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_local_meta(name: str, base: Path | None = None) -> dict:
    return _read_json((base or models_dir()) / name / LOCAL_META)


def _files_present(mdir: Path, meta: dict, verify: bool) -> bool:
    files = meta.get("files") or []
    if not files:
        return False
    for f in files:
        p = mdir / f["name"]
        if not p.is_file() or p.stat().st_size != f["size"]:
            return False
        if verify and sha256_file(p) != f["sha256"]:
            return False
    return True


def model_available(name: str, base: Path | None = None) -> bool:
    """True when the model's files are on disk (no network, no hashing)."""
    mdir = (base or models_dir()) / name
    meta = read_local_meta(name, base)
    return bool(meta) and _files_present(mdir, meta, verify=False)


def _hub_call(fn: Callable[[], object], what: str, tries: int = 6) -> object:
    """Retry on HTTP 429 and 5xx with backoff (Retry-After honoured when given)."""
    from huggingface_hub.errors import HfHubHTTPError

    delay = 15.0
    for attempt in range(tries):
        try:
            return fn()
        except HfHubHTTPError as exc:
            resp = getattr(exc, "response", None)
            status = getattr(resp, "status_code", None)
            if status not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
            wait = delay
            try:
                wait = float(resp.headers.get("Retry-After", wait))  # type: ignore[union-attr]
            except (TypeError, ValueError, AttributeError):
                pass
            wait = min(max(wait, 1.0), 300.0)
            print(f"[r3_semantic] {what}: HTTP {status}, retry in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            delay = min(delay * 2, 300.0)
    raise AssertionError("unreachable")


def _download_model(spec: ModelSpec, mdir: Path, pin: dict | None) -> dict:
    from huggingface_hub import get_hf_file_metadata, hf_hub_download, hf_hub_url
    from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError

    if pin:
        candidates = [(pin["repo_id"], pin["revision"])]
    else:
        candidates = [(spec.repo_id, None)] + ([(spec.mirror, None)] if spec.mirror else [])
    last_err: Exception | None = None
    for repo_id, revision in candidates:
        try:
            if revision is None:
                meta = _hub_call(
                    lambda: get_hf_file_metadata(hf_hub_url(repo_id, spec.onnx_file), token=False),
                    f"{repo_id} resolve",
                )
                revision = meta.commit_hash  # type: ignore[attr-defined]
            if not revision:
                raise RuntimeError(f"could not resolve a commit hash for {repo_id}")
            names: list[str] = []
            pinned_names = {f["name"] for f in pin["files"]} if pin else None
            for fn in spec.required_files + spec.optional_files:
                if pinned_names is not None and fn not in pinned_names:
                    continue
                try:
                    _hub_call(
                        lambda fn=fn: hf_hub_download(
                            repo_id, fn, revision=revision, local_dir=mdir, token=False
                        ),
                        f"{repo_id}/{fn}",
                    )
                    names.append(fn)
                except EntryNotFoundError:
                    if fn in spec.optional_files:
                        continue
                    raise
        except (EntryNotFoundError, RepositoryNotFoundError) as exc:
            if pin:
                raise
            last_err = exc
            continue
        files = []
        for fn in names:
            p = mdir / fn
            files.append({"name": fn, "size": p.stat().st_size, "sha256": sha256_file(p)})
        if pin:
            want = {f["name"]: f["sha256"] for f in pin["files"]}
            got = {f["name"]: f["sha256"] for f in files}
            if want != got:
                bad = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
                raise RuntimeError(f"{spec.name}: sha256 differs from models.json for {bad}")
        meta_out = {
            "name": spec.name,
            "kind": spec.kind,
            "repo_id": repo_id,
            "revision": revision,
            "onnx_file": spec.onnx_file,
            "pooling": spec.pooling,
            "query_prefix": spec.query_prefix,
            "max_length": spec.max_length,
            "files": files,
        }
        _atomic_write_json(mdir / LOCAL_META, meta_out)
        return meta_out
    raise RuntimeError(f"no usable repo for {spec.name}: {last_err}")


def ensure_models(
    names: Iterable[str] | None = None,
    *,
    base: Path | None = None,
    manifest_path: Path | None = MANIFEST_PATH,
    verify: bool = False,
) -> dict[str, dict]:
    """Download the ONNX weights and tokenizer files once into <base>/<name>/.

    Idempotent: a model whose files are present (sizes match its local
    ranksim_meta.json, and sha256 too when verify=True) and whose revision matches
    models.json is not touched and no network call is made. Interrupted downloads
    resume (huggingface_hub keeps .incomplete files under <name>/.cache). When
    models.json pins a model, that exact revision is fetched and every sha256 must
    match. Writes models.json (only if its content changes). Returns name -> meta.
    """
    base = base or models_dir()
    wanted = list(names) if names is not None else list(DEFAULT_MODELS)
    manifest = _read_json(manifest_path) if manifest_path else {}
    pinned: dict = manifest.get("models", {}) if manifest else {}
    out: dict[str, dict] = {}
    for name in wanted:
        if name not in MODEL_SPECS:
            raise KeyError(f"unknown model {name!r}; known: {sorted(MODEL_SPECS)}")
        spec = MODEL_SPECS[name]
        mdir = base / name
        meta = read_local_meta(name, base)
        pin = pinned.get(name)
        ok = bool(meta) and _files_present(mdir, meta, verify=verify)
        if ok and pin and (meta.get("revision") != pin.get("revision") or meta.get("repo_id") != pin.get("repo_id")):
            ok = False
        if not ok:
            mdir.mkdir(parents=True, exist_ok=True)
            meta = _download_model(spec, mdir, pin)
        out[name] = meta
    if manifest_path:
        new_models = dict(pinned)
        new_models.update(out)
        new_manifest = {
            "about": (
                "Pinned free pretrained models for scripts/claude/r3_semantic.py. Files are "
                "stored outside git under /workspace/ranksim-data/models/<name>/. "
                "ensure_models() refetches exactly these revisions and checks every sha256."
            ),
            "models": new_models,
        }
        if new_manifest != manifest:
            _atomic_write_json(manifest_path, new_manifest)
    return out


def verify_models(names: Iterable[str] | None = None, base: Path | None = None) -> dict[str, bool]:
    """Recompute sha256 of every local file against models.json (no network)."""
    base = base or models_dir()
    pinned = _read_json(MANIFEST_PATH).get("models", {})
    result: dict[str, bool] = {}
    for name in names or DEFAULT_MODELS:
        pin = pinned.get(name)
        meta = read_local_meta(name, base)
        result[name] = bool(pin) and bool(meta) and meta.get("revision") == pin.get("revision") and _files_present(
            base / name, pin, verify=True
        )
    return result


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def ort_threads() -> int:
    env = os.environ.get("R3_ORT_THREADS")
    if env:
        return max(1, int(env))
    return max(1, os.cpu_count() or 1)


@dataclass
class _Runner:
    name: str
    spec: ModelSpec
    revision: str
    session: object
    tokenizer: object
    input_names: tuple[str, ...]
    output_name: str
    dim: int
    lock: threading.Lock = field(default_factory=threading.Lock)

    def encode(self, items: Sequence[str] | Sequence[tuple[str, str]], max_length: int) -> list:
        """tokenizers Encodings (ids, type_ids), truncated to max_length (longest_first for pairs)."""
        tok = self.tokenizer
        with self.lock:
            tok.enable_truncation(max_length=int(max_length), strategy="longest_first")  # type: ignore[attr-defined]
            enc = tok.encode_batch(list(items))  # type: ignore[attr-defined]
        return enc

    def run(self, encodings: list, max_batch_tokens: int = MAX_BATCH_TOKENS) -> np.ndarray:
        """Model output per encoding, in input order (sorted by length internally)."""
        n = len(encodings)
        if n == 0:
            return np.empty((0,), dtype=np.float32)
        lengths = np.array([len(e.ids) for e in encodings])
        order = np.argsort(lengths, kind="stable")
        results: list[np.ndarray | None] = [None] * n
        i = 0
        while i < n:
            # Grow the batch while padded size stays under the token budget.
            j = i + 1
            while j < n and j - i < MAX_BATCH_SIZE and (j - i + 1) * lengths[order[j]] <= max_batch_tokens:
                j += 1
            idx = order[i:j]
            width = int(lengths[idx].max())
            ids = np.zeros((len(idx), width), dtype=np.int64)
            mask = np.zeros((len(idx), width), dtype=np.int64)
            types = np.zeros((len(idx), width), dtype=np.int64)
            for r, k in enumerate(idx):
                e = encodings[k]
                L = len(e.ids)
                ids[r, :L] = e.ids
                mask[r, :L] = 1
                types[r, :L] = e.type_ids
            feeds = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
            feeds = {k: v for k, v in feeds.items() if k in self.input_names}
            out = self.session.run([self.output_name], feeds)[0]  # type: ignore[attr-defined]
            if self.spec.kind == "cross":
                vals = np.asarray(out, dtype=np.float32).reshape(len(idx), -1)[:, 0]
            else:
                arr = np.asarray(out, dtype=np.float32)
                vals = arr[:, 0, :] if arr.ndim == 3 else arr  # CLS token
                norms = np.linalg.norm(vals, axis=1, keepdims=True)
                vals = vals / np.maximum(norms, 1e-12)
            for r, k in enumerate(idx):
                results[k] = vals[r]
            i = j
        return np.stack(results).astype(np.float32)  # type: ignore[arg-type]


_RUNNERS: dict[tuple[str, str], _Runner] = {}
_RUNNERS_LOCK = threading.Lock()


def load_model(name: str, base: Path | None = None) -> _Runner:
    """ONNX session and tokenizer for a downloaded model (cached per process)."""
    base = base or models_dir()
    key = (str(base), name)
    with _RUNNERS_LOCK:
        if key in _RUNNERS:
            return _RUNNERS[key]
        if name not in MODEL_SPECS:
            raise KeyError(f"unknown model {name!r}; known: {sorted(MODEL_SPECS)}")
        spec = MODEL_SPECS[name]
        mdir = base / name
        meta = read_local_meta(name, base)
        if not meta or not _files_present(mdir, meta, verify=False):
            raise FileNotFoundError(
                f"model {name} is not downloaded under {mdir}; run "
                "`python scripts/claude/r3_semantic.py download`"
            )
        import onnxruntime as ort
        from tokenizers import Tokenizer

        so = ort.SessionOptions()
        so.intra_op_num_threads = ort_threads()
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(str(mdir / meta["onnx_file"]), so, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(str(mdir / "tokenizer.json"))
        tok.no_padding()
        inputs = tuple(i.name for i in sess.get_inputs())
        outputs = [o.name for o in sess.get_outputs()]
        if spec.kind == "cross":
            out_name = "logits" if "logits" in outputs else outputs[0]
            dim = 1
        else:
            out_name = "last_hidden_state" if "last_hidden_state" in outputs else outputs[0]
            last = [o for o in sess.get_outputs() if o.name == out_name][0].shape[-1]
            dim = last if isinstance(last, int) else int(_read_json(mdir / "config.json")["hidden_size"])
        runner = _Runner(name, spec, meta["revision"], sess, tok, inputs, out_name, dim)
        _RUNNERS[key] = runner
        return runner


def _model_info(name: str, base: Path | None = None) -> tuple[str, int]:
    """(revision, output dim) from the local files, without loading the ONNX session."""
    base = base or models_dir()
    meta = read_local_meta(name, base)
    if not meta.get("revision"):
        raise FileNotFoundError(
            f"model {name} is not downloaded under {base / name}; run "
            "`python scripts/claude/r3_semantic.py download`"
        )
    if MODEL_SPECS[name].kind == "cross":
        return meta["revision"], 1
    return meta["revision"], int(_read_json(base / name / "config.json")["hidden_size"])


def cross_scores_pairs(
    pairs: Sequence[tuple[str, str]],
    model: str = "minilm-l6",
    max_length: int = 512,
    *,
    use_cache: bool = True,
    cache: ScoreCache | None = None,
) -> np.ndarray:
    """Raw cross-encoder logit per (query, text) pair, float32, input order.

    Pairs are tokenized as [CLS] query [SEP] text [SEP] and truncated to max_length
    word pieces (longest_first, so a short query is kept whole and the text is cut).
    """
    if MODEL_SPECS.get(model) is None or MODEL_SPECS[model].kind != "cross":
        raise ValueError(f"{model!r} is not a cross-encoder; use one of {CROSS_MODELS}")
    if not pairs:
        return np.empty((0,), dtype=np.float32)
    revision, _ = _model_info(model)
    c = (cache or get_cache()) if use_cache else None
    keys = [cache_key("cross", model, revision, max_length, q, t) for q, t in pairs]

    def compute(idx: list[int]) -> np.ndarray:
        runner = load_model(model)  # loaded only when something is not cached
        enc = runner.encode([pairs[i] for i in idx], max_length)
        return runner.run(enc)

    hint = [len(q) + len(t) for q, t in pairs]
    return cached_compute(keys, compute, 1, c, order_hint=hint)[:, 0]


def cross_scores(
    query: str,
    texts: list[str],
    model: str = "minilm-l6",
    max_length: int = 512,
    *,
    use_cache: bool = True,
    cache: ScoreCache | None = None,
) -> np.ndarray:
    """Raw cross-encoder logit for each text against one query (float32, shape (n,))."""
    return cross_scores_pairs([(query, t) for t in texts], model, max_length, use_cache=use_cache, cache=cache)


def embed(
    texts: list[str],
    is_query: bool = False,
    *,
    model: str = "bge-small",
    max_length: int = 512,
    use_cache: bool = True,
    cache: ScoreCache | None = None,
) -> np.ndarray:
    """L2-normalised float32 embeddings, shape (n, dim). Queries get the bge prefix."""
    spec = MODEL_SPECS.get(model)
    if spec is None or spec.kind != "embed":
        raise ValueError(f"{model!r} is not an embedder")
    revision, dim = _model_info(model)
    if not texts:
        return np.empty((0, dim), dtype=np.float32)
    c = (cache or get_cache()) if use_cache else None
    role = "query" if is_query else "doc"
    prefix = spec.query_prefix if is_query else ""
    keys = [cache_key("embed", model, revision, max_length, role, t) for t in texts]

    def compute(idx: list[int]) -> np.ndarray:
        runner = load_model(model)
        enc = runner.encode([prefix + texts[i] for i in idx], max_length)
        return runner.run(enc)

    return cached_compute(keys, compute, dim, c, order_hint=[len(t) for t in texts])


def sigmoid(x: np.ndarray) -> np.ndarray:
    """Map cross-encoder logits to (0, 1) if a bounded feature is wanted."""
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=np.float64)))


# ---------------------------------------------------------------------------
# Bench
# ---------------------------------------------------------------------------

SANITY_TRIPLES: list[tuple[str, str, str]] = [
    (
        "free alternative to raken",
        "Looking for a free alternative to Raken? SmartCrew has a free plan for small "
        "construction crews with daily reports, time cards and crew scheduling, while Raken "
        "starts at a paid per user price.",
        "Our company was founded in 2009 in Denver. We believe in hard work, honest "
        "communication and giving back to the local community through volunteer days.",
    ),
    (
        "how to reset a forgotten gmail password",
        "To reset a forgotten Gmail password, open the Google account recovery page, enter "
        "your email address, choose Forgot password, and verify with the code sent to your "
        "recovery phone or email. Then pick a new password.",
        "Gmail lets you organise messages with labels and filters. You can colour code labels, "
        "nest them, and create filters that archive newsletters automatically.",
    ),
    (
        "best crm for small business",
        "The best CRM for a small business is one that is cheap, easy to set up and tracks "
        "leads, deals and follow ups. We compared HubSpot, Pipedrive and Zoho CRM on price, "
        "pipeline features and ease of use for teams under 20 people.",
        "Payroll for small businesses must account for federal and state withholding, "
        "unemployment insurance and quarterly filings. Missing a deadline can bring penalties.",
    ),
    (
        "symptoms of vitamin d deficiency",
        "Common symptoms of vitamin D deficiency include fatigue, bone pain, muscle weakness, "
        "aches and low mood. Severe deficiency can cause rickets in children and osteomalacia "
        "in adults.",
        "Vitamin C is found in citrus fruits, strawberries, peppers and broccoli. It helps the "
        "body absorb iron and supports the immune system.",
    ),
    (
        "construction crew scheduling software",
        "Crew scheduling software for construction lets foremen assign workers and equipment "
        "to jobs on a drag and drop calendar, send shift notices to phones, and see who is "
        "on which job site each day.",
        "Our recipe for sourdough bread uses a starter fed twice a day, a long cold "
        "fermentation overnight and a very hot Dutch oven for a crisp crust.",
    ),
    (
        "python read csv file",
        "To read a CSV file in Python, use the csv module: open the file with "
        "open(path, newline=''), pass it to csv.reader, and loop over the rows. With pandas, "
        "pd.read_csv(path) returns a DataFrame in one line.",
        "Python was created by Guido van Rossum and first released in 1991. The language is "
        "named after the Monty Python comedy group, not the snake.",
    ),
    (
        "busybusy pricing per user",
        "busybusy pricing: the Pro plan costs about 10 dollars per user per month for GPS time "
        "tracking, with an Enterprise plan priced on request. There is also a free tier with "
        "basic time cards.",
        "Heavy civil contractors move large amounts of earth, so equipment maintenance and "
        "fuel logs are a major part of their operating cost.",
    ),
    (
        "what is the capital of australia",
        "Canberra is the capital city of Australia. It was chosen as a compromise between "
        "Sydney and Melbourne and became the seat of government in 1927.",
        "Australia is home to unique wildlife such as kangaroos, koalas and platypuses, and "
        "the Great Barrier Reef is the largest coral reef system in the world.",
    ),
]


def _bench_passages(n: int, words: int = 200, seed: int = 0) -> list[str]:
    """~200-word passages of plain English assembled from the triple texts."""
    rng = np.random.default_rng(seed)
    sentences: list[str] = []
    for _, rel, irr in SANITY_TRIPLES:
        for t in (rel, irr):
            sentences.extend(s.strip() + "." for s in t.split(".") if s.strip())
    out = []
    for _ in range(n):
        ws: list[str] = []
        while len(ws) < words:
            ws.extend(sentences[int(rng.integers(len(sentences)))].split())
        out.append(" ".join(ws[:words]))
    return out


def _rate(fn: Callable[[], object], n: int, repeats: int) -> tuple[float, float]:
    """(best, median) items per second over ``repeats`` timed runs."""
    rates = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        rates.append(n / (time.perf_counter() - t0))
    return max(rates), float(np.median(rates))


def bench(
    n_pairs: int = 256, n_texts: int = 256, models: Sequence[str] | None = None, repeats: int = 3
) -> dict:
    import tempfile

    import onnxruntime as ort

    names = list(models or DEFAULT_MODELS)
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = float("nan")
    print(
        f"onnxruntime {ort.__version__}, threads {ort_threads()}, cpu_count {os.cpu_count()}, "
        f"load avg (1 min) {load:.1f}; no cache; best of {repeats} runs (median in brackets)"
    )
    texts = _bench_passages(max(n_pairs, n_texts))
    query = "construction crew scheduling software with free plan"
    report: dict = {"throughput": {}, "sanity": {}}
    for name in names:
        runner = load_model(name)
        spec = MODEL_SPECS[name]
        if spec.kind == "cross":
            for ml in (256, 512):
                enc = runner.encode([(query, t) for t in texts[:n_pairs]], ml)
                mean_len = float(np.mean([len(e.ids) for e in enc]))
                cross_scores(query, texts[:16], model=name, max_length=ml, use_cache=False)  # warm-up
                best, med = _rate(
                    lambda: cross_scores(query, texts[:n_pairs], model=name, max_length=ml, use_cache=False),
                    n_pairs,
                    repeats,
                )
                report["throughput"][f"{name}@{ml}"] = {"best": best, "median": med}
                print(
                    f"{name:11s} max_length {ml}: {best:7.1f} pairs/s ({med:6.1f})  "
                    f"{n_pairs} pairs, mean {mean_len:.0f} word pieces after truncation"
                )
        else:
            enc = runner.encode(texts[:n_texts], spec.max_length)
            mean_len = float(np.mean([len(e.ids) for e in enc]))
            embed(texts[:16], use_cache=False)
            best, med = _rate(lambda: embed(texts[:n_texts], use_cache=False), n_texts, repeats)
            report["throughput"][f"{name}@{spec.max_length}"] = {"best": best, "median": med}
            print(
                f"{name:11s} max_length {spec.max_length}: {best:7.1f} texts/s ({med:6.1f})  "
                f"{n_texts} texts, mean {mean_len:.0f} word pieces"
            )

    cross_names = [n for n in names if MODEL_SPECS[n].kind == "cross"]
    if cross_names:
        name = cross_names[0]
        with tempfile.TemporaryDirectory() as tmp:
            c = ScoreCache(Path(tmp) / "scores.sqlite")
            cold = cross_scores(query, texts[:n_pairs], model=name, cache=c)
            best, _ = _rate(lambda: cross_scores(query, texts[:n_pairs], model=name, cache=c), n_pairs, repeats)
            warm = cross_scores(query, texts[:n_pairs], model=name, cache=c)
            c.close()
        same = bool(np.array_equal(cold, warm))
        report["throughput"]["cache_hit"] = best
        print(f"cache hit   {name} @512: {best:9.0f} pairs/s (temp SQLite cache, identical scores: {same})")

    print("\nSanity triples (relevant must score above irrelevant):")
    all_ok = True
    for name in names:
        spec = MODEL_SPECS[name]
        ok_n = 0
        rows = []
        for q, rel, irr in SANITY_TRIPLES:
            if spec.kind == "cross":
                s = cross_scores(q, [rel, irr], model=name, use_cache=False)
            else:
                qv = embed([q], is_query=True, use_cache=False)[0]
                dv = embed([rel, irr], use_cache=False)
                s = dv @ qv
            ok = bool(s[0] > s[1])
            ok_n += ok
            rows.append((q, float(s[0]), float(s[1]), ok))
        unit = "logit" if spec.kind == "cross" else "cosine"
        print(f"\n  {name} ({unit}): {ok_n}/{len(SANITY_TRIPLES)} correct")
        for q, a, b, ok in rows:
            print(f"    {'ok  ' if ok else 'FAIL'} rel {a:8.3f}  irr {b:8.3f}  | {q}")
        report["sanity"][name] = {"correct": ok_n, "rows": rows}
        all_ok &= ok_n == len(SANITY_TRIPLES)
    print(f"\nSanity: {'PASS' if all_ok else 'FAIL'}")
    report["sanity_pass"] = all_ok
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="fetch models into the models dir (idempotent)")
    d.add_argument("--models", nargs="*", default=None)
    d.add_argument("--verify", action="store_true", help="also recheck sha256 of present files")
    sub.add_parser("verify", help="recompute sha256 of local files against models.json")
    b = sub.add_parser("bench", help="throughput and sanity triples")
    b.add_argument("--models", nargs="*", default=None)
    b.add_argument("--pairs", type=int, default=256)
    b.add_argument("--texts", type=int, default=256)
    b.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args(argv)
    if args.cmd == "download":
        metas = ensure_models(args.models, verify=args.verify)
        for name, m in metas.items():
            total = sum(f["size"] for f in m["files"])
            print(f"{name}: {m['repo_id']}@{m['revision']} ({len(m['files'])} files, {total / 1e6:.1f} MB)")
        return 0
    if args.cmd == "verify":
        res = verify_models()
        for name, ok in res.items():
            print(f"{name}: {'ok' if ok else 'MISMATCH or missing'}")
        return 0 if all(res.values()) else 1
    if args.cmd == "bench":
        missing = [n for n in (args.models or DEFAULT_MODELS) if not model_available(n)]
        if missing:
            ensure_models(missing)
        rep = bench(args.pairs, args.texts, args.models, args.repeats)
        return 0 if rep["sanity_pass"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
