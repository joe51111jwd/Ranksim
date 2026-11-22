"""Offline tests of the part 4 content match scorer and its evaluation helpers.

No network, no model files, no downloaded data. The cross-encoder path runs on
a fake ONNX session and a fake tokenizer injected through the backend seam.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aeo.ranksim.forecast import content as C
from aeo.ranksim.forecast import content_eval as E

SEARCH = "cheap flights to london"

PAGE_HTML = """<!doctype html><html><head>
<title>Cheap Flights to London | Fly4Less</title>
<meta name="description" content="Compare cheap flights to London from 300 airlines.">
<script>var junk = "flights flights flights";</script>
</head><body>
<nav>Home Hotels Cars</nav>
<main><h1>Cheap flights to London</h1>
<p>Find cheap flights to London Heathrow and Gatwick. Prices start at 99 dollars.</p>
<p>Book with free cancellation on most fares.</p></main>
<footer>Contact us</footer>
</body></html>"""


def london_page() -> C.PageText:
    return C.page_text_from_fields(
        title="Cheap Flights to London",
        h1="London flights",
        meta_description="Compare cheap flights to London Heathrow.",
        body="Cheap flights to London from New York. " * 60,
        url="https://a.example/london",
    )


def paris_page() -> C.PageText:
    return C.page_text_from_fields(
        title="Paris hotel reviews",
        h1="Hotels in Paris",
        meta_description="Read reviews of hotels in Paris.",
        body="The best hotels in Paris for families. " * 10,
        url="https://b.example/paris",
    )


# ---------------------------------------------------------------------------
# fakes for the cross-encoder seam


@dataclass
class _Encoding:
    ids: list[int]
    type_ids: list[int]


@dataclass
class _Named:
    name: str


class FakeTokenizer:
    """Word-level ids: [CLS]=1, words hashed into 2..999, [SEP]=2. Truncates the text side."""

    def __init__(self) -> None:
        self.max_length = 512

    def enable_truncation(self, max_length: int, **kwargs: object) -> None:
        self.max_length = int(max_length)

    @staticmethod
    def _wid(word: str) -> int:
        return 3 + int(hashlib.sha1(word.encode()).hexdigest(), 16) % 997

    def encode_batch(self, inputs: list[tuple[str, str]]) -> list[_Encoding]:
        out = []
        for query, text in inputs:
            q = [self._wid(w) for w in query.lower().split()]
            t = [self._wid(w) for w in text.lower().split()]
            room = self.max_length - len(q) - 3
            t = t[: max(0, room)]
            ids = [1, *q, 2, *t, 2]
            types = [0] * (len(q) + 2) + [1] * (len(t) + 1)
            out.append(_Encoding(ids, types))
        return out


class FakeSession:
    """Logit = number of text tokens that also occur in the query, minus 0.01 per text token.

    Padding is masked out, so the value of a row does not depend on the batch.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def get_inputs(self) -> list[_Named]:
        return [_Named("input_ids"), _Named("attention_mask"), _Named("token_type_ids")]

    def get_outputs(self) -> list[_Named]:
        return [_Named("logits")]

    def run(self, names: list[str], feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        ids, mask, types = feeds["input_ids"], feeds["attention_mask"], feeds["token_type_ids"]
        self.calls.append(ids.shape)
        out = np.zeros((ids.shape[0], 1), dtype=np.float32)
        for r in range(ids.shape[0]):
            live = mask[r] == 1
            q = set(ids[r][live & (types[r] == 0)].tolist()) - {1, 2}
            t = [i for i in ids[r][live & (types[r] == 1)].tolist() if i != 2]
            out[r, 0] = sum(1 for i in t if i in q) - 0.01 * len(t)
        return [out]


def fake_cross_scorer(value_from: str = C.VALUE_HEAD) -> C.ContentScorer:
    backend = C.CrossEncoderBackend(FakeSession(), FakeTokenizer(), model_name="fake", revision="0" * 40)
    return C.ContentScorer(C.MODE_CROSS, cross_encoder=backend, value_from=value_from)


# ---------------------------------------------------------------------------
# extraction


def test_html_extraction_uses_repo_extractor_and_drops_junk():
    page = C.page_text_from_html(PAGE_HTML, "https://fly4less.example/london")
    assert page.title == "Cheap Flights to London | Fly4Less"
    assert page.h1 == "Cheap flights to London"
    assert page.meta_description == "Compare cheap flights to London from 300 airlines."
    body = " ".join(page.body_passages)
    assert "Heathrow" in body and "junk" not in body and "Contact us" not in body and "Hotels Cars" not in body
    assert page.head_text() == "Cheap Flights to London | Fly4Less . Cheap flights to London . Compare cheap flights to London from 300 airlines."
    assert page.url == "https://fly4less.example/london"


def test_head_text_matches_r3_rule_and_cuts_at_600():
    assert C.head_text("T", "", "M") == "T . M"
    assert C.head_text(None, None, None) == ""
    assert len(C.head_text("x" * 700, "h", "m")) == C.HEAD_MAX_CHARS


def test_passages_windows_like_r3():
    words = " ".join(f"w{i}" for i in range(450))
    out = C.passages(words)
    assert [p.split()[0] for p in out] == ["w0", "w100", "w200", "w250"]
    assert all(len(p.split()) == 200 for p in out)
    assert C.passages("") == []
    assert len(C.passages(" ".join(["a"] * 5000))) == C.MAX_PASSAGES


# ---------------------------------------------------------------------------
# lexical backend


def test_lexical_end_to_end_orders_relevant_page_first():
    scorer = C.ContentScorer(C.MODE_LEXICAL)
    good, bad = scorer.score_many([(SEARCH, london_page()), (SEARCH, paris_page())])
    for result in (good, bad):
        assert result.mode == C.MODE_LEXICAL and result.input == C.INPUT_PAGE
        assert result.label == "PREDICTED" and result.model == C.LEXICAL_VERSION
        assert set(result.parts) == {"head", "best_passage"}
        assert result.value == result.parts["head"]
    assert good.value > bad.value
    assert good.parts["best_passage"] > bad.parts["best_passage"]
    assert 0.0 <= bad.value < good.value < 1.0


def test_lexical_missing_stays_none_not_zero():
    scorer = C.ContentScorer(C.MODE_LEXICAL)
    empty = scorer.score(SEARCH, C.PageText(url="https://x.example/"))
    assert empty.value is None and empty.parts == {"head": None, "best_passage": None}
    no_terms = scorer.score("the of and", london_page())
    assert no_terms.value is None
    head_only = scorer.score(SEARCH, C.page_text_from_fields(title="London flights", h1=None, meta_description=None, body=None))
    assert head_only.parts["best_passage"] is None and head_only.value is not None


def test_lexical_df_table_is_frozen_and_round_trips():
    table = C.DfTable.from_texts(["cheap flights", "london hotels", "london flights"])
    assert table.n_docs == 3 and table.df["london"] == 2 and table.df["flight"] == 2
    again = C.DfTable.from_dict(json.loads(json.dumps(table.to_dict())))
    assert again == table
    scorer = C.ContentScorer(C.MODE_LEXICAL, df_table=table)
    one = scorer.score(SEARCH, london_page()).value
    batch = scorer.score_many([(SEARCH, paris_page()), (SEARCH, london_page())])[1].value
    assert one == batch


def test_value_from_options():
    page = london_page()
    head = C.ContentScorer(C.MODE_LEXICAL).score(SEARCH, page)
    passage = C.ContentScorer(C.MODE_LEXICAL, value_from=C.VALUE_PASSAGE).score(SEARCH, page)
    whole = C.ContentScorer(C.MODE_LEXICAL, value_from=C.VALUE_PAGE).score(SEARCH, page)
    assert passage.value == passage.parts["best_passage"] and passage.value_from == C.VALUE_PASSAGE
    assert head.parts == passage.parts == whole.parts
    assert whole.value is not None
    ce = fake_cross_scorer(C.VALUE_PAGE).score(SEARCH, page)
    assert ce.value == max(ce.parts["head"], ce.parts["best_passage"])
    with pytest.raises(ValueError):
        C.ContentScorer(C.MODE_LEXICAL, value_from="title")


# ---------------------------------------------------------------------------
# cross-encoder path through the seam


def test_cross_encoder_path_with_fake_session():
    scorer = fake_cross_scorer()
    good, bad = scorer.score_many([(SEARCH, london_page()), (SEARCH, paris_page())])
    assert good.mode == bad.mode == C.MODE_CROSS
    assert good.model == "fake@000000000000"
    assert good.fallback_reason is None
    assert good.value == good.parts["head"] and good.value > bad.value
    assert good.parts["best_passage"] > bad.parts["best_passage"]


def test_cross_encoder_uses_exact_head_input():
    seen: list[tuple[str, str]] = []

    class Spy(C.CrossEncoderBackend):
        def score_pairs(self, pairs):
            seen.extend(pairs)
            return super().score_pairs(pairs)

    backend = Spy(FakeSession(), FakeTokenizer(), model_name="fake", revision="1" * 40)
    scorer = C.ContentScorer(C.MODE_CROSS, cross_encoder=backend)
    page = london_page()
    scorer.score(SEARCH, page)
    assert seen[0] == (SEARCH, "Cheap Flights to London . London flights . Compare cheap flights to London Heathrow.")
    assert seen[1:] == [(SEARCH, p) for p in page.body_passages]


def test_batching_equals_one_by_one_and_is_deterministic():
    scorer = fake_cross_scorer()
    items = [(SEARCH, london_page()), ("paris hotels", paris_page()), (SEARCH, paris_page()), ("london", london_page())]
    batch = [r.to_dict() for r in scorer.score_many(items)]
    single = [scorer.score(s, p).to_dict() for s, p in items]
    assert batch == single
    assert [r.to_dict() for r in scorer.score_many(items)] == batch
    assert [r.to_dict() for r in fake_cross_scorer().score_many(list(reversed(items)))] == list(reversed(batch))
    lex = C.ContentScorer(C.MODE_LEXICAL)
    assert [r.to_dict() for r in lex.score_many(items)] == [lex.score(s, p).to_dict() for s, p in items]


def test_backend_batches_by_token_budget_and_keeps_order():
    session = FakeSession()
    backend = C.CrossEncoderBackend(session, FakeTokenizer(), model_name="fake", revision="2" * 40)
    pairs = [(SEARCH, "london " * n) for n in range(1, 200)]
    values = backend.score_pairs(pairs)
    assert len(session.calls) > 1
    assert all(rows * width <= C.MAX_BATCH_TOKENS or rows == 1 for rows, width in session.calls)
    alone = np.array([backend.score_pairs([p])[0] for p in pairs[:20]])
    assert np.array_equal(values[:20], alone)


def test_snippet_mode_is_marked():
    for scorer in (fake_cross_scorer(C.VALUE_PAGE), C.ContentScorer(C.MODE_LEXICAL, value_from=C.VALUE_PASSAGE)):
        res = scorer.score_snippet(SEARCH, "Cheap Flights to London", "Compare fares.")
        assert res.input == C.INPUT_SNIPPET and res.value_from == C.VALUE_HEAD
        assert res.parts["best_passage"] is None and res.value == res.parts["head"]
        empty = scorer.score_snippet(SEARCH, None, None)
        assert scorer.score_snippet(SEARCH, float("nan"), "Compare fares.").value is not None
        assert empty.value is None and empty.input == C.INPUT_SNIPPET
        batch = scorer.score_snippets([(SEARCH, "London flights", "x"), (SEARCH, None, None)])
        assert [b.to_dict() for b in batch] == [scorer.score_snippet(SEARCH, "London flights", "x").to_dict(), empty.to_dict()]


# ---------------------------------------------------------------------------
# model files: checksum, missing model, fallback


def _manifest(tmp_path: Path, payload: bytes) -> Path:
    pin = {
        "models": {
            "minilm-l6": {
                "repo_id": "example/model",
                "revision": "a" * 40,
                "kind": "cross",
                "onnx_file": "onnx/model.onnx",
                "max_length": 512,
                "files": [{"name": "onnx/model.onnx", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}],
            }
        }
    }
    path = tmp_path / "models.json"
    path.write_text(json.dumps(pin), encoding="utf-8")
    return path


def _writer(content: bytes, calls: list[tuple[str, str, str]]):
    def fetch(repo_id: str, filename: str, revision: str, local_dir: Path) -> Path:
        calls.append((repo_id, filename, revision))
        target = Path(local_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    return fetch


def test_checksum_verification_refuses_a_wrong_file(tmp_path: Path):
    manifest = _manifest(tmp_path, b"the right bytes")
    calls: list[tuple[str, str, str]] = []
    with pytest.raises(C.ChecksumError, match="was deleted"):
        C.ensure_model(models_dir=tmp_path / "m", allow_download=True, manifest_path=manifest, fetch=_writer(b"wrong bytes!!!!", calls))
    assert calls == [("example/model", "onnx/model.onnx", "a" * 40)]
    assert not (tmp_path / "m" / "minilm-l6" / "onnx" / "model.onnx").exists()


def test_checksum_ok_downloads_once_then_no_fetch(tmp_path: Path):
    manifest = _manifest(tmp_path, b"the right bytes")
    calls: list[tuple[str, str, str]] = []
    folder = C.ensure_model(models_dir=tmp_path / "m", allow_download=True, manifest_path=manifest, fetch=_writer(b"the right bytes", calls))
    assert folder == tmp_path / "m" / "minilm-l6" and len(calls) == 1
    C.ensure_model(models_dir=tmp_path / "m", allow_download=False, manifest_path=manifest, fetch=_writer(b"x", calls))
    assert len(calls) == 1
    assert C.verify_model(models_dir=tmp_path / "m", manifest_path=manifest) == {"onnx/model.onnx": True}
    (folder / "onnx" / "model.onnx").write_bytes(b"tampered bytes!")
    assert C.verify_model(models_dir=tmp_path / "m", manifest_path=manifest) == {"onnx/model.onnx": False}
    with pytest.raises(C.ModelMissingError, match="does not match"):
        C.ensure_model(models_dir=tmp_path / "m", allow_download=False, manifest_path=manifest)


def test_missing_model_with_downloads_off_says_what_to_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(C.ENV_DOWNLOAD, "0")
    monkeypatch.setenv(C.ENV_MODELS_DIR, str(tmp_path / "none"))
    assert C.downloads_allowed() is False
    with pytest.raises(C.ModelMissingError, match="python -m aeo.ranksim.forecast.content download"):
        C.ContentScorer(C.MODE_CROSS)
    auto = C.ContentScorer("auto")
    res = auto.score(SEARCH, london_page())
    assert res.mode == C.MODE_LEXICAL and res.fallback_reason and "lexical used" in res.fallback_reason


def test_real_pin_is_read_only_and_matches_r3(tmp_path: Path):
    before = C.MODELS_MANIFEST.read_bytes()
    pin = C.load_pin("minilm-l6")
    assert pin["repo_id"] == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert pin["revision"] == "233902d25c440f23af6f7d6e94d2946bac0bee0a"
    onnx = next(f for f in pin["files"] if f["name"] == "onnx/model.onnx")
    assert onnx["sha256"] == "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a"
    assert C.MODELS_MANIFEST.read_bytes() == before


def test_module_never_loads_fitted_blend_artefacts():
    for path in (Path(C.__file__), Path(E.__file__)):
        text = path.read_text(encoding="utf-8")
        for marker in ("FrozenScorer", "r3_model", "gbm.txt", "logit.json", "df_table.json"):
            assert marker not in text


# ---------------------------------------------------------------------------
# evaluation helpers


def test_pair_hits_and_accuracy():
    hits = E.pair_hits([3.0, 1.0, 2.0, np.nan], [1.0, 3.0, 2.0, 1.0])
    assert hits[:3].tolist() == [1.0, 0.0, 0.5] and np.isnan(hits[3])
    frame = pd.DataFrame(
        {
            "sid": ["a"] * 3 + ["b"] * 3,
            "rank": [1, 2, 3, 1, 2, 3],
            "site": ["x", "y", "x", "p", "q", "r"],
            "s": [3.0, 2.0, 1.0, 1.0, 2.0, np.nan],
        }
    )
    pairs = E.build_pairs(frame, group_col="sid", rank_col="rank", keep=["site", "s"])
    assert len(pairs) == 6 and sorted(pairs["gap"].tolist()) == [1, 1, 1, 1, 2, 2]
    acc = E.accuracy(pairs, "s", cluster_col="sid")
    assert acc.pairs == 4 and acc.unscored_pairs == 2 and acc.searches == 2
    assert acc.accuracy == pytest.approx(3 / 4)
    assert acc.ci_low is not None and acc.ci_low <= acc.accuracy <= acc.ci_high
    pairs["same"] = pairs["site_hi"] == pairs["site_lo"]
    table = E.accuracy_table(pairs, ["s"], cluster_col="sid", same_site_col="same")
    subsets = [a.subset for a in table]
    assert subsets == ["all pairs", "cross-site pairs", "same-site pairs", "cross-site, adjacent", "cross-site, 2 to 4 apart", "cross-site, 5 or more apart"]
    same = table[2]
    assert same.pairs == 1 and same.accuracy == 1.0 and same.ci_low is None


def _flights_csv(tmp_path: Path) -> Path:
    rows = []
    for gl in ("us", "uk"):
        for rank, (url, title, body) in enumerate(
            [
                ("https://www.fly.example/london", "Cheap flights to London", "Cheap flights to London. " * 50),
                ("https://hotels.example/paris", "Paris hotels", "Hotels in Paris. " * 50),
                ("https://smartcrewhq.com/x", "Crew app", "Construction crew software. " * 50),
                ("https://fly.example/london-deals", "London flight deals", ""),
            ],
            start=1,
        ):
            rows.append(
                {
                    "serp_searchTerms": SEARCH,
                    "serp_gl": gl,
                    "serp_rank": rank,
                    "serp_link": url,
                    "serp_title": title,
                    "serp_snippet": "snippet " + title,
                    "serp_queryTime": "2020-03-01 11:26:23+00:00",
                    "scrape_title": title,
                    "scrape_h1": title + "@@second h1",
                    "scrape_meta_desc": None,
                    "scrape_body_text": body,
                    "scrape_status": 200.0 if rank != 4 or gl == "us" else 404.0,
                    "scrape_time": "2020-03-11 17:56:16",
                }
            )
    path = tmp_path / "flights.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_load_flights_and_eval_offline(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = _flights_csv(tmp_path)
    frame = E.load_flights(path)
    assert "smartcrewhq.com" not in set(frame["site"])
    assert len(frame) == 6 and frame["h1"].iloc[0] == "Cheap flights to London"
    assert frame["usable"].sum() == 5
    out = E.eval_flights(path, cross=fake_cross_scorer(), out_dir=tmp_path / "out", echo=True)
    printed = capsys.readouterr().out
    assert "Not a marker number" in printed and "2020" in printed and "One niche" in printed
    names = {(r["scorer"], r["subset"]) for r in out["results"]}
    assert ("ce_head", "same-site pairs") in names and ("lex_snippet", "cross-site, adjacent") in names
    assert Path(out["written_to"]).is_file()


def _panel(tmp_path: Path) -> Path:
    base = tmp_path / "panel" / "dataset=dfs_serp"
    rng = np.random.default_rng(0)
    for locale in ("US_en", "GB_en", "FR_fr"):
        rows = []
        for k in range(6):
            kw = f"london flights {k}"
            for rank in range(1, 23):
                rows.append(
                    {
                        "locale": locale,
                        "keyword": kw,
                        "keyword_id": f"{locale}{k:02d}",
                        "snapshot_date": pd.Timestamp("2026-01-05").date(),
                        "rank": rank,
                        "site": f"s{rank % 15}.example",
                        "url": f"https://s{rank % 15}.example/{k}/{rank}",
                        "title": "London flights" if rank < 5 else f"Page {rank}",
                        "description": "Cheap flights" if rank < 8 else "Other text",
                        "main_domain_rank": int(rng.integers(100, 140)) if rank != 3 else None,
                        "poison_window": False,
                    }
                )
        folder = base / f"locale={locale}"
        folder.mkdir(parents=True)
        frame = pd.DataFrame(rows)
        frame["main_domain_rank"] = frame["main_domain_rank"].astype("Int32")
        frame.to_parquet(folder / f"part-{locale}.parquet", index=False)
    return base


def test_eval_snippets_offline_is_seeded(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    base = _panel(tmp_path)
    assert E.english_locales(base) == ["GB_en", "US_en"]
    first = E.sample_dfs_searches(base, ["US_en"], 4, seed=7)
    again = E.sample_dfs_searches(base, ["US_en"], 4, seed=7)
    assert first.equals(again) and first["search_id"].nunique() == 4
    out = E.eval_snippets(panel_dir=base, searches=3, cross=fake_cross_scorer(), out_dir=tmp_path / "out")
    printed = capsys.readouterr().out
    assert "flatters the scorer" in printed and "Not a marker number" in printed
    keys = {(r["locales"], r["top_k"], r["scorer"], r["subset"]) for r in out["results"]}
    assert ("US English", 10, "ce_snippet", "cross-site, domain rank diff < 25") in keys
    assert ("other English (1 locales)", 20, "domain_rank", "cross-site, domain rank known") in keys
    assert all(r["top_k"] in (10, 20, 50) for r in out["results"])
