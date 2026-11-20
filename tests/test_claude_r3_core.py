"""Offline tests for the round 3 site-held-fixed tooling (no network, no database, no models)."""

from __future__ import annotations

import gzip
import json

import numpy as np
import pandas as pd
import pytest

from scripts.claude import r3_data, r3_features as F, r3_model as R, r3_pull


def _chunk(tmp_path, label, items):
    folder = tmp_path / label
    folder.mkdir(parents=True)
    payload = {"label": label, "date": "2026-11-19", "source": f"claude-r3:{label}", "chunk": 1, "items": items}
    (folder / "chunk-001.json.gz").write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))


def _item(q, links):
    organic = [{"position": i + 1, "link": link, "title": f"t{i}", "snippet": ""} for i, link in enumerate(links)]
    return {"q": q, "page": 1, "start": "2026-11-19T19:00:00-05:00", "end": "2026-11-19T19:00:01-05:00", "status": 200,
            "raw": json.dumps({"searchParameters": {"q": q}, "organic": organic, "credits": 1})}


def test_split_site_query():
    assert r3_data.split_site_query("site:Example.com crew scheduling software") == ("example.com", "crew scheduling software")
    assert r3_data.split_site_query("plain search") == (None, "plain search")


def test_load_label_and_pairs(tmp_path, monkeypatch):
    monkeypatch.setattr(r3_data, "SERP_DIR", tmp_path)
    _chunk(tmp_path, "t", [
        _item("site:example.com alpha beta", ["https://www.example.com/a", "https://example.com/b/", "https://other.org/x", "https://example.com/c"]),
        _item("site:example.com nothing", []),
    ])
    frame = r3_data.load_label("t")
    assert len(frame) == 4 and set(frame["site"]) == {"example.com"}
    requests = r3_data.load_requests("t")
    assert list(requests["n_results"]) == [4, 0]
    pairs = r3_data.list_pairs(frame)
    # The off-site result is dropped: 3 own pages give 3 pairs, higher page first.
    assert len(pairs) == 3
    assert (pairs["pos_hi"] < pairs["pos_lo"]).all()
    assert not pairs["url_lo"].str.contains("other.org").any()


def test_plan_loading_and_body(tmp_path):
    plan = tmp_path / "plan.jsonl"
    plan.write_text('{"q": "site:a.com x", "page": 1}\n{"q": "site:a.com x"}\n{"q": "site:a.com x", "page": 2}\n# note\n\n', encoding="utf-8")
    requests = r3_pull.load_plan(plan)
    assert requests == [{"q": "site:a.com x", "page": 1}, {"q": "site:a.com x", "page": 2}]
    assert "page" not in r3_pull.request_body(requests[0])
    assert r3_pull.request_body(requests[1])["page"] == 2
    assert r3_pull.request_body(requests[0])["num"] == 10


def test_min_window_and_phrase_count():
    tokens = "a b crew x scheduling y z crew scheduling".split()
    assert F._min_window(tokens, ["crew", "scheduling"]) == pytest.approx(1.0)  # adjacent at the end: window 2 over 2 terms
    assert F._min_window(tokens, ["crew", "absent"]) == 0.0
    assert F._phrase_count(tokens, ["crew", "scheduling"]) == 1
    assert F._phrase_count(tokens, []) == 0


def test_lexical_features_prefer_the_matching_page():
    from scripts.claude.r2_samesite_pairs import Corpus, page_fields

    match = {"title": "Crew Scheduling Software for Contractors", "h1": "Crew scheduling software", "headings": ["Scheduling"],
             "main_text": "crew scheduling software " * 30, "meta": "Crew scheduling software.", "word_count": 90}
    other = {"title": "About our company", "h1": "About", "headings": ["Team"], "main_text": "we build things for people " * 30,
             "meta": "About us.", "word_count": 150}
    corpus = Corpus({"a": page_fields(match), "b": page_fields(other)})
    fa = F.lexical_features("crew scheduling software", F.static_page(match, "https://x.com/crew-scheduling-software/"), corpus)
    fb = F.lexical_features("crew scheduling software", F.static_page(other, "https://x.com/about/"), corpus)
    for name in ("bm25f", "title_cov", "h1_cov", "slug_cov", "title_phrase", "title_focus", "body_density", "all_terms_title"):
        assert fa[name] > fb[name], name
    assert F.static_page(other, "https://x.com/blog/post-1/")["pt_blog"] == 1.0
    assert F.query_flags("raken alternative pricing")["qi_compare"] == 1.0


def _toy_table(n_sites=12, per_list=6, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_sites):
        for l in range(2):
            good = rng.normal(size=per_list)
            noise = rng.normal(size=per_list)
            order = np.argsort(-(good + 0.3 * rng.normal(size=per_list)))
            rank = np.empty(per_list, dtype=int)
            rank[order] = np.arange(1, per_list + 1)
            for i in range(per_list):
                rows.append({"list_id": f"s{s}|q{l}", "site": f"s{s}.com", "query": f"q{l} {s % 3}", "key": f"s{s}/p{l}{i}",
                             "rank": int(rank[i]), "usable": True, "good": good[i], "noise": noise[i]})
    return pd.DataFrame(rows)


def test_pair_accuracy_and_ties():
    table = _toy_table()
    pairs = R.list_pairs_index(table)
    assert len(pairs) == 24 * 15
    perfect = R.pair_hits(pairs, -table["rank"].astype(float))
    assert perfect.mean() == 1.0
    flat = R.pair_hits(pairs, pd.Series(0.0, index=table.index))
    assert flat.mean() == 0.5
    entry = R.accuracy(pairs, perfect)
    assert entry["rate"] == 1.0 and entry["sites"] == 12 and entry["lists"] == 24


def test_logit_learns_direction_and_cv_holds_out_sites():
    table = _toy_table()
    model = R.LogitModel(["good", "noise"]).fit(table)
    assert model.coef[0] > 0 and abs(model.coef[0]) > 5 * abs(model.coef[1])
    restored = R.LogitModel.from_dict(json.loads(json.dumps(model.to_dict())))
    assert np.allclose(restored.score(table), model.score(table))
    folds = R.site_folds(table, 4)
    assert set(folds.values()) == {0, 1, 2, 3}
    assert R.site_folds(table, 4) == folds  # deterministic
    scores = R.cross_validate(table, "logit", ["good", "noise"], n_splits=4)
    pairs = R.list_pairs_index(table)
    assert R.pair_hits(pairs, scores).mean() > 0.8


def test_missing_values_do_not_decide_a_pair():
    table = _toy_table(n_sites=2)
    table.loc[table.index[0], "good"] = np.nan
    filled = R._filled(table, ["good"])
    first_list = table["list_id"] == table["list_id"].iloc[0]
    assert filled.loc[table.index[0], "good"] == pytest.approx(table.loc[first_list, "good"].mean())
