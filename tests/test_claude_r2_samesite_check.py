"""Offline tests for scripts/claude/r2_samesite_check.py. Synthetic data only, except one test that
reads design-day SERP rows (2026-11-17 or earlier) and is skipped when the raw database is absent."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

pd = pytest.importorskip("pandas")

from scripts.claude import r2_samesite_check as ck  # noqa: E402
from scripts.claude import r2_samesite_pairs as ss  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402

FILLER = " ".join(f"filler{i}" for i in range(70))


def _html(title: str, h1: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body><h1>{h1}</h1><h2>More</h2><p>{body} {FILLER}</p></body></html>"


# ------------------------------------------------------------------ fixed definitions


def test_vote_uses_the_design_text_group_without_serp_title():
    assert ck.VOTE_FEATURES == ["title_cov", "h1_cov", "slug_cov", "title_phrase", "bm25_body", "bm25f", "first100_cov"]
    assert ck.VOTE_FEATURES == [f for f in ss.GROUPS["text"] if f != "serp_title_all"]
    for banned in ("serp_title_all", "inlinks", "url_depth", "is_home"):
        assert banned not in ck.VOTE_FEATURES
        assert banned in ck.EXCLUDED_FEATURES
    assert all(ss.DIRECTION[f] == 1 for f in ck.VOTE_FEATURES)
    assert ck.GROUPS_USED == ("o30", "a70")


def test_parse_days_and_mode_guards():
    assert ck.parse_days("2026-11-19, 2026-11-18,2026-11-18") == ["2026-11-18", "2026-11-19"]
    for bad in ("", "2026-11-1", "2026-13-01", "nov 18"):
        with pytest.raises(SystemExit):
            ck.parse_days(bad)
    ck.check_days(["2026-11-16", "2026-11-17"], design=True)
    ck.check_days(["2026-11-18", "2026-11-19"], design=False)
    with pytest.raises(SystemExit):
        ck.check_days(["2026-11-17", "2026-11-18"], design=True)
    with pytest.raises(SystemExit):
        ck.check_days(["2026-11-17", "2026-11-18"], design=False)


def test_seen_cutoff_never_passes_the_design_cutoff():
    assert ck.seen_cutoff(["2026-11-18", "2026-11-19"]) == "2026-11-17"
    assert ck.seen_cutoff(["2026-11-20"]) == "2026-11-17"
    assert ck.seen_cutoff(["2026-11-16", "2026-11-17"]) == "2026-11-15"


# ------------------------------------------------------------------ held-out guards (nothing loads)


def _forbid_loading(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("data loaded before the freeze check")

    for name in ("load_rows", "load_page_index", "load_page_html", "consensus_table"):
        monkeypatch.setattr(ck.rs, name, boom)
    monkeypatch.setattr(ck.tc, "fetch_table", boom)


def test_heldout_checks_the_freeze_before_loading(monkeypatch):
    _forbid_loading(monkeypatch)
    seen = []

    def frozen(directory):
        seen.append(directory)
        raise SystemExit("not frozen")

    monkeypatch.setattr(ck.r2_score, "require_frozen_dir", frozen)
    with pytest.raises(SystemExit, match="not frozen"):
        ck.main(["--prereg", "evals/preregistered/claude-r2-samesite", "--days", "2026-11-18,2026-11-19"])
    assert seen and seen[0] == (ck.ROOT / "evals/preregistered/claude-r2-samesite").resolve()


def test_heldout_needs_prereg_and_refuses_design_days(monkeypatch):
    _forbid_loading(monkeypatch)

    def never(_directory):
        raise AssertionError("freeze check reached")

    monkeypatch.setattr(ck.r2_score, "require_frozen_dir", never)
    with pytest.raises(SystemExit, match="prereg"):
        ck.main(["--days", "2026-11-18"])
    with pytest.raises(SystemExit, match="Design days"):
        ck.main(["--prereg", "x", "--days", "2026-11-17,2026-11-18"])
    with pytest.raises(SystemExit, match="Sealed days"):
        ck.main(["--design", "--days", "2026-11-17,2026-11-18"])


def test_heldout_refuses_overwrite_unless_force(monkeypatch, tmp_path):
    _forbid_loading(monkeypatch)
    monkeypatch.setattr(ck.r2_score, "require_frozen_dir", lambda d: {"frozen_at_utc": "t"})
    monkeypatch.setattr(ck, "HELDOUT_OUT", tmp_path)
    (tmp_path / "samesite-2026-11-18_2026-11-19.json").write_text("{}", encoding="utf-8")
    args = ["--prereg", "evals/preregistered/claude-r2-samesite", "--days", "2026-11-18,2026-11-19"]
    with pytest.raises(SystemExit, match="already exists"):
        ck.main(args)
    with pytest.raises(AssertionError, match="data loaded"):  # --force goes on to load
        ck.main(args + ["--force"])


# ------------------------------------------------------------------ pairs and history


def _cons(entries):
    """entries: (query, day, url, cons). Site is the host."""
    out = []
    for query, day, url, pos in entries:
        out.append({"query": query, "day": day, "key": url, "url": url, "site": urlsplit(url).hostname.removeprefix("www."),
                    "cons": pos, "n_pulls": 3})
    return pd.DataFrame(out)


def test_pair_tables_majority_label_split_ref_day_and_groups():
    a, b, c, d = "https://s.com/a", "https://s.com/b", "https://o.com/c", "https://s.com/d"
    cons = _cons([
        ("q", "2026-11-01", a, 1), ("q", "2026-11-01", b, 2), ("q", "2026-11-01", c, 3), ("q", "2026-11-01", d, 4),
        ("q", "2026-11-02", b, 1), ("q", "2026-11-02", a, 2), ("q", "2026-11-02", c, 3), ("q", "2026-11-02", d, 5),
        ("q", "2026-11-03", a, 1), ("q", "2026-11-03", c, 2), ("q", "2026-11-03", b, 3), ("q", "2026-11-03", d, 11),
        ("z", "2026-11-01", a, 1), ("z", "2026-11-01", b, 2),
    ])
    groups = {"q": "o30", "z": "p35"}
    days = ["2026-11-01", "2026-11-02", "2026-11-03"]
    pooled, per_day, info = ck.pair_tables(cons, days, groups, ck.GROUPS_USED)
    assert set(pooled["query"]) == {"q"}  # p35 search left out
    row = pooled[(pooled["hi"] == "s.com/a") & (pooled["lo"] == "s.com/b")].iloc[0]
    assert row["days"] == 3 and row["agree_days"] == 2 and row["same_site"] and row["ref_day"] == "2026-11-03"
    row = pooled[(pooled["hi"] == "s.com/a") & (pooled["lo"] == "s.com/d")].iloc[0]
    assert row["days"] == 2 and row["ref_day"] == "2026-11-02"  # d is outside the top 10 on Nov 3
    assert not pooled[(pooled["hi"] == "o.com/c") | (pooled["lo"] == "o.com/c")]["same_site"].any()
    assert list(per_day) == days and set(per_day["2026-11-02"]["ref_day"]) == {"2026-11-02"}
    assert per_day["2026-11-02"].query("hi == 's.com/b' and lo == 's.com/a'").shape[0] == 1
    # two days that disagree: the pair is an exact split and is dropped
    pooled2, _p, info2 = ck.pair_tables(cons, days[:2], groups, ck.GROUPS_USED)
    assert pooled2.query("(hi == 's.com/a' and lo == 's.com/b') or (hi == 's.com/b' and lo == 's.com/a')").empty
    assert info2["pooled"]["split_pairs_dropped"] == 1
    empty, per_empty, _i = ck.pair_tables(cons, ["2026-11-09"], groups, ck.GROUPS_USED)
    assert empty.empty and per_empty["2026-11-09"].empty


def test_seen_sets_use_single_pulls_and_whole_deep_lists_up_to_the_cutoff():
    rows = pd.DataFrame([
        {"query": "q1", "day": "2026-11-10", "source": "s", "fetched_at": "t1", "url": "https://www.x.com/a/?utm_source=1", "kind": "top10"},
        {"query": "q1", "day": "2026-11-10", "source": "s", "fetched_at": "t1", "url": "https://x.com/b", "kind": "top10"},
        {"query": "q1", "day": "2026-11-10", "source": "s", "fetched_at": "t2", "url": "https://x.com/e", "kind": "top10"},
        {"query": "q1", "day": "2026-11-11", "source": "deep:abspos", "fetched_at": "p1", "url": "https://x.com/c", "kind": "deep"},
        {"query": "q1", "day": "2026-11-11", "source": "deep:abspos", "fetched_at": "p2", "url": "https://x.com/d", "kind": "deep"},
        {"query": "q2", "day": "2026-11-10", "source": "s", "fetched_at": "t1", "url": "https://x.com/e", "kind": "top10"},
        {"query": "q2", "day": "2026-11-10", "source": "s", "fetched_at": "t1", "url": "https://x.com/f", "kind": "top10"},
        {"query": "q1", "day": "2026-11-12", "source": "s", "fetched_at": "t9", "url": "https://x.com/e", "kind": "top10"},
        {"query": "q1", "day": "2026-11-12", "source": "s", "fetched_at": "t9", "url": "https://x.com/f", "kind": "top10"},
    ])
    by_search, any_search = ck.seen_sets(rows, "2026-11-11")
    assert ("q1", "x.com/a", "x.com/b") in by_search
    assert ("q1", "x.com/c", "x.com/d") in by_search  # one deep list across its pages
    assert ("q1", "x.com/a", "x.com/e") not in by_search  # different pulls
    assert ("q1", "x.com/e", "x.com/f") not in by_search  # only after the cutoff, or on another search
    assert ("x.com/e", "x.com/f") in any_search
    frame = pd.DataFrame({"query": ["q1", "q1", "q1"], "hi": ["x.com/b", "x.com/f", "x.com/z"], "lo": ["x.com/a", "x.com/e", "x.com/a"]})
    marked = ck.mark_history(frame, by_search, any_search)
    assert marked["seen_this_search"].tolist() == [True, False, False]
    assert marked["seen_any_search"].tolist() == [True, True, False]


# ------------------------------------------------------------------ pages


def test_pick_page_nearest_usable_then_design_tie_breaks():
    html = {1: "<html><body><p>too short</p></body></html>", 2: _html("Two", "Two", "two"), 3: _html("Three", "Three", "three"),
            4: _html("Four", "Four", "four"), 5: _html("Five", "Five", "five"), 6: _html("Six", "Six", "six")}
    fetches = pd.DataFrame([
        {"id": 1, "url": "https://s.com/p", "fetched_at": "2026-11-16T15:00:00Z", "fetch_method": "http", "day": "2026-11-16", "date_only": False},
        {"id": 2, "url": "https://s.com/p", "fetched_at": "2026-11-15T15:00:00Z", "fetch_method": "browser", "day": "2026-11-15", "date_only": False},
        {"id": 3, "url": "https://s.com/p", "fetched_at": "2026-11-17T00:00:00.000Z", "fetch_method": "http", "day": "2026-11-17", "date_only": True},
        {"id": 4, "url": "https://s.com/p", "fetched_at": "2026-11-17T14:00:00Z", "fetch_method": "browser", "day": "2026-11-17", "date_only": False},
        {"id": 5, "url": "https://s.com/p", "fetched_at": "2026-11-17T13:00:00Z", "fetch_method": "http", "day": "2026-11-17", "date_only": False},
        {"id": 6, "url": "https://s.com/p", "fetched_at": "2026-11-17T16:00:00Z", "fetch_method": "http", "day": "2026-11-17", "date_only": False},
    ])
    store = ck.PageStore(load_html=lambda pid: html[pid])
    cands = ck.page_candidates(fetches, {"s.com/p"})["s.com/p"]
    # Nov 16: the same-day fetch is too short, so one day away; Nov 15 and Nov 17 tie on distance and
    # the timed http fetches come first, the latest of them (id 6) wins.
    assert ck.pick_page(cands, "2026-11-16", store)["page_id"] == 6
    assert ck.pick_page(cands, "2026-11-17", store)["page_id"] == 6
    assert ck.pick_page(cands, "2026-11-14", store)["page_id"] == 2
    assert ck.pick_page(cands[cands["id"].isin([1, 3, 4])], "2026-11-17", store)["page_id"] == 4  # timed browser before date-only http
    assert ck.pick_page(cands[cands["id"] == 1], "2026-11-16", store) is None
    assert ck.pick_page(None, "2026-11-16", store) is None
    assert ck.pick_page(cands, "2026-11-16", store)["fetch_day"] == "2026-11-17"


# ------------------------------------------------------------------ end to end on a synthetic world

DAY = "2026-11-18"
T, O, A, C, R, N = ("https://s.com/time-tracking", "https://o.com/blog", "https://s.com/about", "https://s.com/copy",
                    "https://s.com/rental-software", "https://n.com/x")
PAGES = {
    T: _html("Crew time tracking app", "Crew time tracking", "crew time tracking for every crew with time tracking"),
    O: _html("Time tracking tips", "Tips", "time tracking ideas"),
    A: _html("About us", "About", "our company story"),
    C: _html("About us", "About", "our company story"),
    R: _html("Equipment rental software", "Equipment rental software", "equipment rental software for rental yards"),
}


def _world():
    cons = _cons([
        ("crew time tracking", DAY, T, 1), ("crew time tracking", DAY, O, 2), ("crew time tracking", DAY, A, 3), ("crew time tracking", DAY, C, 4),
        ("equipment rental software", DAY, A, 1), ("equipment rental software", DAY, R, 2), ("equipment rental software", DAY, N, 3),
        ("other search", DAY, A, 1), ("other search", DAY, R, 2),
    ])
    groups = {"crew time tracking": "o30", "equipment rental software": "a70", "other search": "p35"}
    rows = pd.DataFrame([
        {"query": "crew time tracking", "day": "2026-11-17", "source": "s", "fetched_at": "t1", "url": T, "kind": "top10", "position": 1, "title": "Crew time tracking"},
        {"query": "crew time tracking", "day": "2026-11-17", "source": "s", "fetched_at": "t1", "url": A, "kind": "top10", "position": 2, "title": "About"},
        {"query": "equipment rental software", "day": "2026-11-17", "source": "s", "fetched_at": "t1", "url": T, "kind": "top10", "position": 1, "title": "x"},
        {"query": "equipment rental software", "day": "2026-11-17", "source": "s", "fetched_at": "t1", "url": C, "kind": "top10", "position": 2, "title": "y"},
    ])
    ids = {url: i for i, url in enumerate(PAGES, 1)}
    fetches = pd.DataFrame([{"id": ids[url], "url": url, "fetched_at": f"{DAY}T15:00:00Z", "fetch_method": "http", "day": DAY, "date_only": False}
                            for url in PAGES])
    html = {ids[url]: text for url, text in PAGES.items()}
    return rows, cons, groups, fetches, html


def test_run_check_end_to_end_counts_scores_and_history():
    rows, cons, groups, fetches, html = _world()
    report, pooled, per_day = ck.run_check(rows, cons, fetches, ck.PageStore(load_html=html.__getitem__), [DAY], groups)
    counts = report["pooled"]["counts"]
    assert counts["same_site_pairs"] == 4 and counts["cross_site_pairs"] == 5
    assert counts["same_site_duplicate_content_dropped"] == 1  # about vs copy
    assert counts["cross_site_without_usable_html"] == 2 and counts["same_site_without_usable_html"] == 0
    assert counts["same_site_usable_pairs"] == 3 and counts["same_site_searches"] == 2 and counts["same_site_sites"] == 1
    assert counts["same_site_top5_sites"] == [["s.com", 3]] and counts["same_site_top5_share"] == 1.0
    assert report["corpus"]["documents"] == 5 and report["pages"]["urls_in_pairs"] == 6
    assert report["seen_cutoff"] == "2026-11-17" and report["search_groups"] == ["o30", "a70"]

    same = pooled[pooled["same_site"]].set_index(["hi", "lo"])
    assert same.loc[("s.com/time-tracking", "s.com/about"), "bm25f_credit"] == 1.0
    assert same.loc[("s.com/time-tracking", "s.com/copy"), "bm25f_credit"] == 1.0
    assert same.loc[("s.com/about", "s.com/rental-software"), "bm25f_credit"] == 0.0
    assert same.loc[("s.com/time-tracking", "s.com/about"), "vote_sum"] == 7
    assert same.loc[("s.com/about", "s.com/rental-software"), "vote_sum"] == -7
    # The BM25F difference is the design study's Corpus.bm25f on the run's corpus, unchanged.
    store = ck.PageStore(load_html=html.__getitem__)
    fields = {ss.ident(url): ss.page_fields(store.get(i, url)) for i, url in enumerate(PAGES, 1)}
    corpus = ss.Corpus(fields)
    terms = ss.query_terms("crew time tracking")
    expected = corpus.bm25f(fields["s.com/time-tracking"], terms) - corpus.bm25f(fields["s.com/about"], terms)
    assert same.loc[("s.com/time-tracking", "s.com/about"), "d_bm25f"] == pytest.approx(expected)

    bm = report["pooled"]["scorers"]["bm25f"]
    assert bm["same_site"]["hits"] == 2.0 and bm["same_site"]["n"] == 3
    assert bm["same_minus_cross"]["diff"] == pytest.approx(2 / 3 - bm["cross_site"]["rate"])
    assert "text_vote_8_with_serp_title" not in report["pooled"]["scorers"]  # no SERP title without --design
    hist = report["pooled"]["by_history"]
    assert hist["seen_pair_this_search"]["same_site_pairs"] == 1  # time-tracking and about shared a pull
    assert hist["new_pair_this_search"]["same_site_pairs"] == 2
    assert hist["new_pair_every_search"]["same_site_pairs"] == 1  # time-tracking and copy shared a pull on another search
    assert list(report["per_day"]) == [DAY] and report["per_day"][DAY]["counts"]["same_site_usable_pairs"] == 3
    assert set(per_day["day"]) == {DAY}

    check = ck.prediction_check(report["pooled"], "heldout")
    assert check["prediction"] == ck.PREDICTION["text"] and check["mode"] == "heldout"
    assert check["parts"]["bm25f_between_0.55_and_0.65"]["held"] is False  # 0.667
    assert check["held"] is False

    with_titles, _p, _d = ck.run_check(rows, cons, fetches, ck.PageStore(load_html=html.__getitem__), [DAY], groups, serp_title_day=DAY)
    assert "text_vote_8_with_serp_title" in with_titles["pooled"]["scorers"]


def test_prediction_check_rules():
    def summary(bm, bm_cross, vote, vote_cross, lo=0.01):
        def block(same, cross):
            return {"same_site": {"rate": same}, "cross_site": {"rate": cross}, "same_minus_cross": {"diff": same - cross, "lo": lo, "hi": 0.2}}
        return {"scorers": {"bm25f": block(bm, bm_cross), "text_vote": block(vote, vote_cross)}}

    ok = ck.prediction_check(summary(0.60, 0.52, 0.55, 0.50), "heldout")
    assert ok["held"] is True and all(p["held"] for p in ok["parts"].values())
    assert ok["parts"]["bm25f_above_cross_site"]["difference_interval_above_zero"] is True
    assert ck.prediction_check(summary(0.66, 0.52, 0.60, 0.50), "heldout")["parts"]["bm25f_between_0.55_and_0.65"]["held"] is False
    below = ck.prediction_check(summary(0.60, 0.61, 0.60, 0.50, lo=-0.1), "design")
    assert below["parts"]["bm25f_above_cross_site"]["held"] is False and below["held"] is False
    assert below["parts"]["bm25f_above_cross_site"]["difference_interval_above_zero"] is False
    assert below["mode"] == "design dry run, not a test"


def test_main_writes_heldout_outputs_once(monkeypatch, tmp_path):
    rows, cons, groups, fetches, html = _world()
    monkeypatch.setattr(ck.r2_score, "require_frozen_dir", lambda d: {"frozen_at_utc": "2026-11-19T00:00:00Z"})
    monkeypatch.setattr(ck, "HELDOUT_OUT", tmp_path)
    monkeypatch.setattr(ck, "load", lambda last_day: (rows, cons, groups, fetches))
    monkeypatch.setattr(ck.rs, "load_page_html", html.__getitem__)
    args = ["--prereg", "evals/preregistered/claude-r2-samesite", "--days", DAY]
    assert ck.main(args) == 0
    report = json.loads((tmp_path / f"samesite-{DAY}.json").read_text(encoding="utf-8"))
    assert report["mode"] == "heldout" and report["prereg"] == "claude-r2-samesite"
    assert report["manifest_frozen_at"] == "2026-11-19T00:00:00Z"
    assert "PREDICTED" in report["label"] and "not proof" in report["label"]
    assert report["prediction_check"]["mode"] == "heldout" and "design_comparison" not in report
    assert (tmp_path / f"samesite-{DAY}-pairs.csv").exists() and (tmp_path / f"samesite-{DAY}.md").exists()
    header = (tmp_path / f"samesite-{DAY}-pairs.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "seen_this_search" in header and "d_serp_title_all" not in header
    with pytest.raises(SystemExit, match="already exists"):
        ck.main(args)


# ------------------------------------------------------------------ real design rows (skipped without the database)


@pytest.mark.skipif(not rs.DB.exists(), reason="raw SERP database not present")
def test_real_design_pairs_match_the_design_study():
    rows = rs.load_rows(rs.DESIGN_MAX_DATE)
    assert str(rows["day"].max()) <= rs.DESIGN_MAX_DATE
    cons = rs.consensus_table(rows)
    groups = rs.query_groups(rows)
    design = json.loads((ck.ROOT / "evals/claude/r2/samesite/results.json").read_text(encoding="utf-8"))["q1_u2"]
    every_day = sorted(cons["day"].unique())
    pooled, _per, info = ck.pair_tables(cons, every_day, groups, None)
    assert len(pooled) == design["pairs_total"] and int(pooled["same_site"].sum()) == design["same_site_pairs"]
    assert info["pooled"]["split_pairs_dropped"] == design["split_pairs_dropped"]
    pooled, per_day, info = ck.pair_tables(cons, ["2026-11-16", "2026-11-17"], groups, ck.GROUPS_USED)
    assert set(pooled["query"].map(groups)) <= {"o30", "a70"}
    assert info["searches_with_lists"] == {"2026-11-16": 100, "2026-11-17": 30}
    assert len(pooled) == 4472 and int(pooled["same_site"].sum()) == 137
