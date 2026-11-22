"""Part 4: the content match scorer. How well a page fits a search.

What is REAL here: the page text (title, H1, meta description, body) is copied
from the page's own HTML by the repo's extractor (`aeo.crawl.extract`). The model
files are the pinned public files named in `evals/claude/r3/models.json`, checked
by sha256 before use. What is PREDICTED: every score. A `ContentScore` is model
output. It is not a Google position and it is never reported as one.

Two backends, and every output says which one ran (`ContentScore.mode`):

- `cross_encoder`: the pretrained cross-encoder `minilm-l6`
  (cross-encoder/ms-marco-MiniLM-L-6-v2 at the pinned revision), run with
  onnxruntime on CPU. It is used untrained, as the build plan says. Nothing is
  fitted here, and the fitted page-blend artefacts under `evals/claude/r3/model`
  are never loaded. The default "head" input is exactly the one behind the 0.694
  same-site figure: the search against `title . h1 . meta description`, cut to
  600 characters (`scripts/claude/r3_features.page_text_for_title`). Pairs are
  tokenized as [CLS] search [SEP] text [SEP], truncated longest first to 512 word
  pieces. The raw logit is the score.
- `lexical`: a BM25F score over title, H1, meta description and body passages.
  It needs no model files. Field weights follow the repo's design study
  (`scripts/claude/r2_samesite_pairs.BM25F_FIELDS`). Average field lengths are
  fixed constants and term weights come from an optional frozen document
  frequency table, so a score never depends on which other pages were scored in
  the same batch.

`ContentScore.input` is `page` when the page itself was read, and `snippet` when
only a result-page title and description were scored. Snippets are written by
Google for the search, so a snippet score is not a page content score.

Downloads happen only when the model files are missing and downloads are
allowed. Set `RANKSIM_MODEL_DOWNLOAD=0` (or `HF_HUB_OFFLINE=1`) to forbid them.
The model is fetched into `data/forecast/models/<name>/` (or
`$RANKSIM_FORECAST_DIR/models`). `evals/claude/r3/models.json` is read, never written.

Commands:
  python -m aeo.ranksim.forecast.content download   fetch and verify the pinned model
  python -m aeo.ranksim.forecast.content verify     recheck sha256 of the local files
  python -m aeo.ranksim.forecast.content score --search "..." --url URL --html-file page.html
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

import numpy as np

from aeo.crawl.extract import extract_html
from aeo.textutil import STOPWORDS, tokenize

from .config import forecast_paths
from .types import PREDICTED

REPO_ROOT = Path(__file__).resolve().parents[4]
MODELS_MANIFEST = REPO_ROOT / "evals" / "claude" / "r3" / "models.json"
DEFAULT_MODEL = "minilm-l6"
CROSS_ENCODER_MODELS = ("minilm-l6", "minilm-l12")

MODE_CROSS = "cross_encoder"
MODE_LEXICAL = "lexical"
MODES = (MODE_CROSS, MODE_LEXICAL)
INPUT_PAGE = "page"
INPUT_SNIPPET = "snippet"

# Input construction (the exact r3 setup behind the 0.694 figure).
HEAD_MAX_CHARS = 600
HEAD_JOINER = " . "
MAX_LENGTH = 512
PASSAGE_WORDS = 200
PASSAGE_STRIDE = 100
MAX_PASSAGES = 12

# Batching: sort by token length, then cut batches so that batch size times padded
# length stays under this many tokens (the r3 numbers).
MAX_BATCH_TOKENS = 8192
MAX_BATCH_SIZE = 64

# Which part becomes ContentScore.value.
VALUE_HEAD = "head"  # the default: the validated input
VALUE_PASSAGE = "passage"  # the best body passage
VALUE_PAGE = "page"  # cross-encoder: max(head, best passage); lexical: BM25F over all fields
VALUE_FROM = (VALUE_HEAD, VALUE_PASSAGE, VALUE_PAGE)

# Lexical BM25F. Field (weight, b) as in the r2/r3 design study, meta in place of headings.
LEXICAL_VERSION = "bm25f-v1"
BM25F_FIELDS: dict[str, tuple[float, float]] = {
    "title": (3.0, 0.5),
    "h1": (3.0, 0.5),
    "meta": (2.0, 0.5),
    "body": (1.0, 0.75),
}
BM25_K1 = 1.2
# Fixed average field lengths in tokens. A constant, so a score does not move with the batch.
BM25F_AVG_LEN: dict[str, float] = {"title": 9.0, "h1": 7.0, "meta": 25.0, "body": float(PASSAGE_WORDS)}
MIN_TERM_CHARS = 3

ENV_DOWNLOAD = "RANKSIM_MODEL_DOWNLOAD"
ENV_THREADS = "RANKSIM_ORT_THREADS"
ENV_MODELS_DIR = "RANKSIM_MODELS_DIR"
DEFAULT_THREADS = 4
HASH_CHUNK = 1 << 20


class ModelMissingError(FileNotFoundError):
    """The pinned model files are not on disk and downloads are off."""


class ChecksumError(RuntimeError):
    """A model file on disk does not match the sha256 pinned in models.json."""


# ---------------------------------------------------------------------------
# Page text


@dataclass(frozen=True)
class PageText:
    """The text of one page that the scorer reads. REAL: copied from the page."""

    title: str = ""
    h1: str = ""
    meta_description: str = ""
    body_passages: tuple[str, ...] = ()
    url: str = ""

    def head_text(self) -> str:
        """`title . h1 . meta description`, cut to 600 characters (the r3 head input)."""
        return head_text(self.title, self.h1, self.meta_description)


def head_text(title: str | None, h1: str | None, meta: str | None) -> str:
    """Join the non-empty parts with " . " and cut to HEAD_MAX_CHARS, exactly as r3 did."""
    parts = [title or "", h1 or "", meta or ""]
    return HEAD_JOINER.join(part.strip() for part in parts if part and part.strip())[:HEAD_MAX_CHARS]


def passages(
    text: str,
    words: int = PASSAGE_WORDS,
    stride: int = PASSAGE_STRIDE,
    max_passages: int = MAX_PASSAGES,
) -> list[str]:
    """Overlapping word windows over a body (the r3 rule, head of the page kept).

    Windows start at 0, stride, 2*stride, ... plus one end-aligned window when the
    grid does not reach the end. A body of at most `words` words is one passage.
    Empty text gives []. Only the first `max_passages` windows are kept.
    """
    if words <= 0 or stride <= 0 or max_passages <= 0:
        raise ValueError("words, stride and max_passages must be positive")
    toks = (text or "").split()
    n = len(toks)
    if n == 0:
        return []
    if n <= words:
        return [" ".join(toks)]
    starts = list(range(0, n - words + 1, stride))
    if starts[-1] + words < n:
        starts.append(n - words)
    starts = starts[:max_passages]
    return [" ".join(toks[s : s + words]) for s in starts]


def page_text_from_fields(
    *,
    title: str | None,
    h1: str | None,
    meta_description: str | None,
    body: str | None,
    url: str = "",
) -> PageText:
    """A PageText from already extracted fields. Missing fields become empty strings."""

    def clean(value: str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, float) and math.isnan(value):
            return ""
        return " ".join(str(value).split())

    return PageText(
        title=clean(title),
        h1=clean(h1),
        meta_description=clean(meta_description),
        body_passages=tuple(passages(clean(body))),
        url=url or "",
    )


def page_text_from_html(html: str, url: str) -> PageText:
    """Extract a page with the repo's extractor (`aeo.crawl.extract.extract_html`).

    Title is the <title>, H1 the first non-empty <h1>, meta the description (or
    og:description), body the main text with navigation, footer, forms and
    scripts removed. The HTML is parsed as data, never executed.
    """
    page = extract_html(html or "", url)
    return page_text_from_fields(
        title=page.title,
        h1=page.h1,
        meta_description=page.meta_description,
        body=page.main_text,
        url=url,
    )


# ---------------------------------------------------------------------------
# Scores


@dataclass(frozen=True)
class ContentScore:
    """One PREDICTED content match score.

    `value` is the part named by `value_from` (None when it could not be
    computed). `parts` holds `head` (search versus title, H1, meta, or versus the
    snippet) and `best_passage` (the best body passage, None for snippets or
    pages without a body). Scores of different modes are on different scales and
    must never be compared with each other.
    """

    value: float | None
    mode: str
    input: str
    parts: dict[str, float | None]
    model: str
    value_from: str
    label: str = PREDICTED
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "mode": self.mode,
            "input": self.input,
            "parts": dict(self.parts),
            "model": self.model,
            "value_from": self.value_from,
            "label": self.label,
            "fallback_reason": self.fallback_reason,
        }


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------------------
# Lexical backend


def _stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token


def lexical_tokens(text: str) -> list[str]:
    """Lower-case word tokens with a light plural stem (the r2/r3 rule)."""
    return [_stem(token) for token in tokenize(text or "")]


def query_terms(search: str) -> list[str]:
    """Distinct search terms: stop words and terms under 3 characters dropped."""
    return list(dict.fromkeys(_stem(t) for t in tokenize(search or "") if t not in STOPWORDS and len(t) >= MIN_TERM_CHARS))


@dataclass(frozen=True)
class DfTable:
    """Frozen document frequencies for BM25 term weights. Built once, never per batch."""

    n_docs: int
    df: Mapping[str, int]

    def idf(self, term: str) -> float:
        d = int(self.df.get(term, 0))
        return math.log(1.0 + (self.n_docs - d + 0.5) / (d + 0.5))

    @classmethod
    def from_texts(cls, texts: Iterable[str]) -> "DfTable":
        """Document frequencies over a corpus of texts (one text per document). Uses no labels."""
        counts: Counter[str] = Counter()
        n = 0
        for text in texts:
            n += 1
            counts.update(set(lexical_tokens(text)))
        return cls(n_docs=n, df=dict(counts))

    def to_dict(self) -> dict[str, Any]:
        return {"n_docs": self.n_docs, "df": dict(sorted(self.df.items()))}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DfTable":
        return cls(n_docs=int(data["n_docs"]), df={str(k): int(v) for k, v in dict(data["df"]).items()})


class LexicalBackend:
    """BM25F over title, H1, meta and one body field. No model files, no network."""

    name = LEXICAL_VERSION

    def __init__(self, df_table: DfTable | None = None) -> None:
        self.df_table = df_table

    def _idf(self, term: str) -> float:
        return self.df_table.idf(term) if self.df_table is not None else 1.0

    def bm25f(self, terms: Sequence[str], fields: Mapping[str, str]) -> float | None:
        """Normalised BM25F in [0, 1): sum of idf * pseudo / (K1 + pseudo) over terms, divided by sum of idf.

        None when the search has no usable terms or every field is empty.
        """
        if not terms:
            return None
        toks = {name: lexical_tokens(fields.get(name) or "") for name in BM25F_FIELDS}
        if not any(toks.values()):
            return None
        counts = {name: Counter(tokens) for name, tokens in toks.items()}
        total_idf = 0.0
        score = 0.0
        for term in terms:
            idf = self._idf(term)
            total_idf += idf
            pseudo = 0.0
            for name, (weight, b) in BM25F_FIELDS.items():
                tf = counts[name].get(term, 0)
                if tf:
                    norm = 1.0 - b + b * len(toks[name]) / BM25F_AVG_LEN[name]
                    pseudo += weight * tf / norm
            if pseudo:
                score += idf * pseudo / (BM25_K1 + pseudo)
        if total_idf <= 0:
            return None
        return score / total_idf

    def score_page(self, search: str, page: PageText) -> dict[str, float | None]:
        terms = query_terms(search)
        head_fields = {"title": page.title, "h1": page.h1, "meta": page.meta_description}
        head = self.bm25f(terms, head_fields)
        best_passage: float | None = None
        best_text = ""
        for text in page.body_passages:
            value = self.bm25f(terms, {"body": text})
            if value is not None and (best_passage is None or value > best_passage):
                best_passage, best_text = value, text
        whole = self.bm25f(terms, {**head_fields, "body": best_text}) if (head is not None or best_passage is not None) else None
        return {"head": head, "best_passage": best_passage, "page": whole}

    def score_snippet(self, search: str, title: str, description: str) -> float | None:
        return self.bm25f(query_terms(search), {"title": title or "", "meta": description or ""})


# ---------------------------------------------------------------------------
# Model files (pinned, verified)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(HASH_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def load_pin(name: str = DEFAULT_MODEL, manifest_path: str | Path = MODELS_MANIFEST) -> dict[str, Any]:
    """The pinned entry of one model in models.json (read only)."""
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    models = data.get("models") or {}
    if name not in models:
        raise KeyError(f"model {name!r} is not pinned in {manifest_path}")
    pin = models[name]
    for key in ("repo_id", "revision", "files", "onnx_file", "kind"):
        if key not in pin:
            raise ValueError(f"pin for {name} in {manifest_path} has no {key!r}")
    return pin


def default_models_dir() -> Path:
    env = os.environ.get(ENV_MODELS_DIR)
    return Path(env) if env else forecast_paths().models


def downloads_allowed() -> bool:
    """False when RANKSIM_MODEL_DOWNLOAD is 0/false/no or HF_HUB_OFFLINE is set to a true value."""
    flag = os.environ.get(ENV_DOWNLOAD, "1").strip().lower()
    offline = os.environ.get("HF_HUB_OFFLINE", "").strip().lower()
    return flag not in {"0", "false", "no", "off"} and offline not in {"1", "true", "yes", "on"}


Fetcher = Callable[[str, str, str, Path], Path]


def hub_fetch(repo_id: str, filename: str, revision: str, local_dir: Path) -> Path:
    """Download one file of one pinned revision from Hugging Face, anonymously."""
    from huggingface_hub import hf_hub_download

    return Path(hf_hub_download(repo_id, filename, revision=revision, local_dir=str(local_dir), token=False))


def _file_ok(path: Path, spec: Mapping[str, Any]) -> bool:
    return path.is_file() and path.stat().st_size == int(spec["size"]) and sha256_file(path) == spec["sha256"]


def download_command(name: str = DEFAULT_MODEL) -> str:
    return f"python -m aeo.ranksim.forecast.content download --model {name}"


def ensure_model(
    name: str = DEFAULT_MODEL,
    *,
    models_dir: str | Path | None = None,
    allow_download: bool | None = None,
    manifest_path: str | Path = MODELS_MANIFEST,
    fetch: Fetcher | None = None,
) -> Path:
    """The verified local folder of a pinned model, downloading it first when allowed.

    Every file's size and sha256 must match models.json. A file that does not
    match is deleted and the call raises ChecksumError. With downloads off and a
    file missing or wrong, it raises ModelMissingError naming the command to run.
    """
    pin = load_pin(name, manifest_path)
    mdir = Path(models_dir) if models_dir is not None else default_models_dir()
    mdir = mdir / name
    allow = downloads_allowed() if allow_download is None else allow_download
    fetcher = fetch or hub_fetch
    for spec in pin["files"]:
        path = mdir / spec["name"]
        if _file_ok(path, spec):
            continue
        if not allow:
            state = "does not match its pinned sha256" if path.exists() else "is missing"
            raise ModelMissingError(
                f"model file {path} {state} and downloads are off. Run `{download_command(name)}` "
                f"with network access, or unset {ENV_DOWNLOAD}=0 / HF_HUB_OFFLINE. "
                "Use mode 'lexical' to score without model files."
            )
        mdir.mkdir(parents=True, exist_ok=True)
        got = fetcher(pin["repo_id"], spec["name"], pin["revision"], mdir)
        got = Path(got)
        if got.resolve() != path.resolve():
            raise ChecksumError(f"downloaded {spec['name']} landed at {got}, expected {path}")
        if not _file_ok(path, spec):
            actual = sha256_file(path) if path.is_file() else "nothing"
            path.unlink(missing_ok=True)
            raise ChecksumError(
                f"{name}/{spec['name']} from {pin['repo_id']}@{pin['revision']} has sha256 {actual}, "
                f"models.json pins {spec['sha256']}. The file was deleted. Nothing was scored."
            )
    return mdir


def verify_model(name: str = DEFAULT_MODEL, *, models_dir: str | Path | None = None, manifest_path: str | Path = MODELS_MANIFEST) -> dict[str, bool]:
    """sha256 check of every pinned file of one model on disk. No network."""
    pin = load_pin(name, manifest_path)
    mdir = (Path(models_dir) if models_dir is not None else default_models_dir()) / name
    return {spec["name"]: _file_ok(mdir / spec["name"], spec) for spec in pin["files"]}


# ---------------------------------------------------------------------------
# Cross-encoder backend


class OrtSession(Protocol):
    """The part of onnxruntime.InferenceSession the scorer uses (a seam for tests)."""

    def get_inputs(self) -> Sequence[Any]: ...

    def get_outputs(self) -> Sequence[Any]: ...

    def run(self, output_names: Sequence[str], feeds: Mapping[str, np.ndarray]) -> Sequence[Any]: ...


class PairTokenizer(Protocol):
    """The part of tokenizers.Tokenizer the scorer uses (a seam for tests)."""

    def enable_truncation(self, max_length: int, **kwargs: Any) -> Any: ...

    def encode_batch(self, inputs: list[Any]) -> list[Any]: ...


def ort_threads() -> int:
    env = os.environ.get(ENV_THREADS)
    if env:
        return max(1, int(env))
    return max(1, min(DEFAULT_THREADS, os.cpu_count() or 1))


class CrossEncoderBackend:
    """A cross-encoder run on CPU. Output: one raw logit per (search, text) pair.

    Build it with `CrossEncoderBackend.load()` for the pinned model, or pass any
    session and tokenizer with the same interface (tests inject fakes here).
    """

    def __init__(self, session: OrtSession, tokenizer: PairTokenizer, *, model_name: str, revision: str, max_length: int = MAX_LENGTH) -> None:
        self.session = session
        self.tokenizer = tokenizer
        self.model_name = model_name
        self.revision = revision
        self.max_length = int(max_length)
        self.input_names = tuple(i.name for i in session.get_inputs())
        outputs = [o.name for o in session.get_outputs()]
        if not outputs:
            raise ValueError("the ONNX session has no outputs")
        self.output_name = "logits" if "logits" in outputs else outputs[0]
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"{self.model_name}@{self.revision[:12]}"

    @classmethod
    def load(
        cls,
        name: str = DEFAULT_MODEL,
        *,
        models_dir: str | Path | None = None,
        allow_download: bool | None = None,
        manifest_path: str | Path = MODELS_MANIFEST,
        fetch: Fetcher | None = None,
        threads: int | None = None,
    ) -> "CrossEncoderBackend":
        """Verify (and if allowed download) the pinned model, then open it with onnxruntime on CPU."""
        if name not in CROSS_ENCODER_MODELS:
            raise ValueError(f"{name!r} is not a pinned cross-encoder; use one of {CROSS_ENCODER_MODELS}")
        pin = load_pin(name, manifest_path)
        mdir = ensure_model(name, models_dir=models_dir, allow_download=allow_download, manifest_path=manifest_path, fetch=fetch)
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads or ort_threads()
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(str(mdir / pin["onnx_file"]), options, providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_file(str(mdir / "tokenizer.json"))
        tokenizer.no_padding()
        return cls(session, tokenizer, model_name=name, revision=pin["revision"], max_length=int(pin.get("max_length") or MAX_LENGTH))

    def _encode(self, pairs: Sequence[tuple[str, str]]) -> list[Any]:
        with self._lock:
            self.tokenizer.enable_truncation(max_length=self.max_length, strategy="longest_first")
            return list(self.tokenizer.encode_batch([(q, t) for q, t in pairs]))

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> np.ndarray:
        """Raw logits in input order (float32). Identical pairs are scored once."""
        if not pairs:
            return np.empty((0,), dtype=np.float32)
        unique = list(dict.fromkeys(pairs))
        encodings = self._encode(unique)
        lengths = np.array([len(e.ids) for e in encodings])
        # Stable sort by length, then by the pair text, so the batch layout is a pure function of the input set.
        order = sorted(range(len(unique)), key=lambda k: (int(lengths[k]), unique[k]))
        values = np.empty(len(unique), dtype=np.float32)
        i = 0
        n = len(order)
        while i < n:
            j = i + 1
            while j < n and j - i < MAX_BATCH_SIZE and (j - i + 1) * int(lengths[order[j]]) <= MAX_BATCH_TOKENS:
                j += 1
            idx = order[i:j]
            width = int(max(lengths[k] for k in idx))
            ids = np.zeros((len(idx), width), dtype=np.int64)
            mask = np.zeros((len(idx), width), dtype=np.int64)
            types = np.zeros((len(idx), width), dtype=np.int64)
            for r, k in enumerate(idx):
                e = encodings[k]
                size = len(e.ids)
                ids[r, :size] = e.ids
                mask[r, :size] = 1
                types[r, :size] = e.type_ids
            feeds = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
            feeds = {k: v for k, v in feeds.items() if k in self.input_names}
            out = self.session.run([self.output_name], feeds)[0]
            logits = np.asarray(out, dtype=np.float32).reshape(len(idx), -1)[:, 0]
            for r, k in enumerate(idx):
                values[k] = logits[r]
            i = j
        position = {pair: k for k, pair in enumerate(unique)}
        return np.array([values[position[p]] for p in pairs], dtype=np.float32)


# ---------------------------------------------------------------------------
# The scorer


class ContentScorer:
    """Scores how well a page fits a search. Untrained. Every output names its mode.

    backend: "cross_encoder" (raises ModelMissingError when the model is missing
    and downloads are off), "lexical", or "auto" (cross-encoder when it can be
    loaded, else lexical with `fallback_reason` set on every output).
    value_from: which part becomes `ContentScore.value` (default "head").
    """

    def __init__(
        self,
        backend: str = "auto",
        *,
        cross_encoder: CrossEncoderBackend | None = None,
        model: str = DEFAULT_MODEL,
        models_dir: str | Path | None = None,
        allow_download: bool | None = None,
        df_table: DfTable | None = None,
        value_from: str = VALUE_HEAD,
    ) -> None:
        if backend not in ("auto", MODE_CROSS, MODE_LEXICAL):
            raise ValueError(f"backend must be auto, {MODE_CROSS} or {MODE_LEXICAL}, not {backend!r}")
        if value_from not in VALUE_FROM:
            raise ValueError(f"value_from must be one of {VALUE_FROM}, not {value_from!r}")
        self.value_from = value_from
        self.lexical = LexicalBackend(df_table)
        self.cross: CrossEncoderBackend | None = None
        self.fallback_reason: str | None = None
        if backend == MODE_LEXICAL:
            self.mode = MODE_LEXICAL
            return
        if cross_encoder is not None:
            self.cross = cross_encoder
        else:
            try:
                self.cross = CrossEncoderBackend.load(model, models_dir=models_dir, allow_download=allow_download)
            except (ModelMissingError, ImportError) as exc:
                if backend == MODE_CROSS:
                    raise
                self.fallback_reason = f"cross-encoder unavailable, lexical used: {exc}"
        self.mode = MODE_CROSS if self.cross is not None else MODE_LEXICAL

    @property
    def model_name(self) -> str:
        return self.cross.name if (self.mode == MODE_CROSS and self.cross is not None) else self.lexical.name

    def _pick(self, parts: Mapping[str, float | None]) -> float | None:
        if self.value_from == VALUE_HEAD:
            return parts.get("head")
        if self.value_from == VALUE_PASSAGE:
            return parts.get("best_passage")
        if self.mode == MODE_LEXICAL:
            return parts.get("page")
        known = [v for v in (parts.get("head"), parts.get("best_passage")) if v is not None]
        return max(known) if known else None

    def _make(self, parts: dict[str, float | None], input_kind: str) -> ContentScore:
        value = self._pick(parts) if input_kind == INPUT_PAGE else parts.get("head")
        return ContentScore(
            value=_finite(value),
            mode=self.mode,
            input=input_kind,
            parts={"head": _finite(parts.get("head")), "best_passage": _finite(parts.get("best_passage"))},
            model=self.model_name,
            value_from=self.value_from if input_kind == INPUT_PAGE else VALUE_HEAD,
            fallback_reason=self.fallback_reason,
        )

    # pages

    def score(self, search: str, page: PageText) -> ContentScore:
        return self.score_many([(search, page)])[0]

    def score_many(self, items: Sequence[tuple[str, PageText]]) -> list[ContentScore]:
        """Batch API. The result for each item equals scoring it alone (up to float rounding of the model)."""
        if self.mode == MODE_LEXICAL:
            return [self._make(dict(self.lexical.score_page(search, page)), INPUT_PAGE) for search, page in items]
        assert self.cross is not None
        pairs: list[tuple[str, str]] = []
        for search, page in items:
            head = page.head_text()
            if head:
                pairs.append((search, head))
            pairs.extend((search, text) for text in page.body_passages if text)
        logits = self.cross.score_pairs(pairs)
        table = {pair: float(v) for pair, v in zip(pairs, logits)}
        out: list[ContentScore] = []
        for search, page in items:
            head = page.head_text()
            head_score = table[(search, head)] if head else None
            body = [table[(search, t)] for t in page.body_passages if t]
            out.append(self._make({"head": head_score, "best_passage": max(body) if body else None}, INPUT_PAGE))
        return out

    # snippets

    def score_snippet(self, search: str, title: str | None, description: str | None) -> ContentScore:
        return self.score_snippets([(search, title, description)])[0]

    def score_snippets(self, items: Sequence[tuple[str, str | None, str | None]]) -> list[ContentScore]:
        """Result-page snippets when the page is not fetched. Marked input="snippet".

        The cross-encoder reads `title . description` built like the head input.
        Snippets are written by Google for this search, so these scores flatter
        the scorer and are not page content scores.
        """
        if self.mode == MODE_LEXICAL:
            return [self._make({"head": self.lexical.score_snippet(s, t or "", d or ""), "best_passage": None}, INPUT_SNIPPET) for s, t, d in items]
        assert self.cross is not None
        texts = [head_text(t, None, d) for _, t, d in items]
        pairs = [(s, text) for (s, _, _), text in zip(items, texts) if text]
        logits = self.cross.score_pairs(pairs)
        table = {pair: float(v) for pair, v in zip(pairs, logits)}
        return [
            self._make({"head": table[(s, text)] if text else None, "best_passage": None}, INPUT_SNIPPET)
            for (s, _, _), text in zip(items, texts)
        ]


# ---------------------------------------------------------------------------
# CLI


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.forecast.content", description="Part 4 content match scorer (PREDICTED scores).")
    sub = parser.add_subparsers(dest="command", required=True)
    dl = sub.add_parser("download", help="fetch the pinned model and verify every sha256")
    dl.add_argument("--model", default=DEFAULT_MODEL, choices=CROSS_ENCODER_MODELS)
    dl.add_argument("--models-dir", default=None)
    ver = sub.add_parser("verify", help="recheck sha256 of the local model files (no network)")
    ver.add_argument("--model", default=DEFAULT_MODEL, choices=CROSS_ENCODER_MODELS)
    ver.add_argument("--models-dir", default=None)
    sc = sub.add_parser("score", help="score one saved HTML page against one search")
    sc.add_argument("--search", required=True)
    sc.add_argument("--url", required=True)
    sc.add_argument("--html-file", required=True)
    sc.add_argument("--backend", default="auto", choices=("auto", MODE_CROSS, MODE_LEXICAL))
    sc.add_argument("--value-from", default=VALUE_HEAD, choices=VALUE_FROM)
    args = parser.parse_args(argv)
    if args.command == "download":
        mdir = ensure_model(args.model, models_dir=args.models_dir, allow_download=True)
        print(f"Model {args.model} is in {mdir}. Every file matches its pinned sha256.")
        return 0
    if args.command == "verify":
        result = verify_model(args.model, models_dir=args.models_dir)
        for name, ok in result.items():
            print(f"{name}: {'ok' if ok else 'MISSING OR WRONG'}")
        return 0 if all(result.values()) else 1
    html = Path(args.html_file).read_text(encoding="utf-8", errors="replace")
    page = page_text_from_html(html, args.url)
    scorer = ContentScorer(args.backend, value_from=args.value_from)
    print(json.dumps(scorer.score(args.search, page).to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
