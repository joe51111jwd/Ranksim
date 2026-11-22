"""Offline tests for the shared feature frame (forecast/features.py).

Tiny hand-made frames and the synthetic world only. No network, no downloaded data.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import pytest

from aeo.ranksim.forecast import features as F
from aeo.ranksim.forecast.asof import Dated, LeakError
from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.sources.strength_lists import Snapshot
from aeo.ranksim.forecast.types import SYNTHETIC


# ---------------------------------------------------------------------------
# fakes


@dataclass
class FakeSource:
    """A dated strength source with fixed snapshots. `values[snapshot_id][site] = dict`."""

    name: str
    snaps: list[Snapshot]
    values: dict[str, dict[str, dict]]
    lie_about_date: date | None = None  # when set, lookup stamps every value with this (later) date

    def snapshots(self) -> list[Snapshot]:
        return list(self.snaps)

    def lookup(self, sites: Iterable[str], as_of: date) -> dict[str, Dated]:
        from aeo.ranksim.forecast.sources.strength_lists import pick_snapshot

        snap = pick_snapshot(self.snaps, as_of)
        if snap is None:
            return {}
        out = {}
        for site in sites:
            if site in self.values.get(snap.snapshot_id, {}):
                stamp = self.lie_about_date or snap.published
                out[site] = Dated(self.values[snap.snapshot_id][site], self.name, stamp)
        return out


def cc_source(lie: date | None = None) -> FakeSource:
    snaps = [Snapshot("r1", date(2025, 1, 1), date(2025, 3, 1), date(2025, 4, 1)),
             Snapshot("r2", date(2025, 2, 1), date(2025, 4, 1), date(2025, 5, 5))]
    values = {
        "r1": {"a.com": {"present": 1.0, "hc_pos": 1000.0, "pr_pos": 2000.0, "n_hosts": 9.0},
               "b.com": {"present": 0.0}},
        "r2": {"a.com": {"present": 1.0, "hc_pos": 100.0, "pr_pos": 200.0, "n_hosts": 99.0},
               "b.com": {"present": 1.0, "hc_pos": 10_000.0, "pr_pos": 20_000.0, "n_hosts": 0.0}},
    }
    return FakeSource("commoncrawl_domain_ranks", snaps, values, lie)


# ---------------------------------------------------------------------------
# strength values and the guard


def test_strength_values_floor_missing_and_opr_rule():
    raw = {"cc_present": 1.0, "cc_hc_pos": 1000.0, "cc_pr_pos": 100.0, "cc_n_hosts": 9.0,
           "crux_present": 0.0, "crux_bucket": None, "tranco_present": None, "tranco_rank": None, "opr_opr": None}
    out = F.strength_values(raw)
    assert out["s_cc_hc"] == pytest.approx(-3.0)
    assert out["s_cc_pr"] == pytest.approx(-2.0)
    assert out["s_cc_hosts"] == pytest.approx(1.0)
    assert out["s_crux"] == -F.TOPLIST_ABSENT_LOG10  # searched, absent: weaker than every listed site
    assert math.isnan(out["s_tranco"])  # not searched: unknown
    assert math.isnan(out["s_opr"])  # Open PageRank missing is never a floor (survivorship)
    absent = F.strength_values({"cc_present": 0.0})
    assert absent["s_cc_hc"] == -F.CC_ABSENT_POS_LOG10 and absent["s_cc_hosts"] == 0.0


def test_vectorised_strength_values_match_row_rule():
    rows = [
        {"cc_present": 1.0, "cc_hc_pos": 50.0, "cc_pr_pos": 70.0, "cc_n_hosts": 3.0, "crux_present": 1.0, "crux_bucket": 1000.0,
         "tranco_present": 1.0, "tranco_rank": 12.0, "opr_opr": 5.5},
        {"cc_present": 0.0, "crux_present": 0.0, "tranco_present": 0.0},
        {},
    ]
    frame = F.add_strength_values(pd.DataFrame(rows))
    for i, raw in enumerate(rows):
        expected = F.strength_values(raw)
        for name in F.MEASURES:
            got = frame.loc[i, name]
            assert (math.isnan(got) and math.isnan(expected[name])) or got == pytest.approx(expected[name])


def test_strength_for_dates_groups_by_snapshot_and_moves_ages():
    frame = pd.DataFrame({"site": ["a.com", "a.com", "b.com", "a.com"],
                          "as_of": [date(2025, 4, 10), date(2025, 4, 20), date(2025, 4, 20), date(2025, 6, 1)]})
    out = F.strength_for_dates(frame, sources=[cc_source()])
    out = out.sort_values(["site", "as_of"]).reset_index(drop=True)
    a_april = out[(out["site"] == "a.com") & (out["as_of"] == date(2025, 4, 20))].iloc[0]
    assert a_april["cc_hc_pos"] == 1000.0  # r1, the newest published by 20 April
    assert a_april["cc_snapshot_age_days"] == 19.0
    a_june = out[(out["site"] == "a.com") & (out["as_of"] == date(2025, 6, 1))].iloc[0]
    assert a_june["cc_hc_pos"] == 100.0  # r2 was published 5 May
    b = out[out["site"] == "b.com"].iloc[0]
    assert b["s_cc_hc"] == -F.CC_ABSENT_POS_LOG10


def test_planted_future_strength_value_is_refused():
    frame = pd.DataFrame({"site": ["a.com"], "as_of": [date(2025, 4, 10)]})
    with pytest.raises(LeakError):
        F.strength_for_dates(frame, sources=[cc_source(lie=date(2025, 12, 1))])


def test_cached_strength_reuses_rows(tmp_path: Path):
    frame = pd.DataFrame({"site": ["a.com"], "as_of": [date(2025, 6, 1)]})
    path = tmp_path / "cache.parquet"
    first = F.cached_strength(frame, cache_path=path, sources=[cc_source()])
    # a source that would fail proves the second call reads the cache only
    second = F.cached_strength(frame, cache_path=path, sources=[cc_source(lie=date(2030, 1, 1))])
    assert first["cc_hc_pos"].iloc[0] == second["cc_hc_pos"].iloc[0] == 100.0


def test_model_feature_check_refuses_opr_and_outcomes():
    assert F.check_model_features(["st_s_cc_hc", "sd_age_days"]) == ["st_s_cc_hc", "sd_age_days"]
    for bad in (["st_s_opr"], ["st_opr_opr"], ["position_out"], ["st_cc_snapshot_age_days"]):
        with pytest.raises(ValueError):
            F.check_model_features(bad)


# ---------------------------------------------------------------------------
# start dates


def _cc(first_release_end: date, published: date, absent_by: date | None) -> Dated:
    return Dated({"date": first_release_end.isoformat(), "kind": "first_seen", "release": "r", "absent_in": "x" if absent_by else None,
                  "absent_by": absent_by.isoformat() if absent_by else None, "left_censored": absent_by is None},
                 "commoncrawl_first_release", published)


def test_left_censored_start_is_never_an_age():
    ev = [_cc(date(2018, 1, 24), date(2018, 2, 1), None)]
    out = F.start_features("old.com", date(2025, 6, 1), ev)
    assert math.isnan(out["sd_age_days"])
    assert out["sd_left_censored"] == 1.0
    assert out["sd_age_lower_bound_days"] == float((date(2025, 6, 1) - date(2018, 1, 24)).days)
    assert out["sd_young"] == 0.0  # censored long ago: known not young


def test_start_evidence_after_as_of_is_ignored():
    ev = [_cc(date(2025, 9, 30), date(2025, 10, 25), date(2025, 8, 31))]
    before = F.start_features("new.com", date(2025, 6, 1), ev)
    assert before["sd_no_evidence"] == 1.0 and math.isnan(before["sd_age_days"])
    after = F.start_features("new.com", date(2025, 11, 1), ev)
    assert after["sd_no_evidence"] == 0.0 and after["sd_age_days"] > 0 and after["sd_young"] == 1.0
    frame = F.start_features_frame(pd.DataFrame({"site": ["new.com", "new.com"], "as_of": [date(2025, 6, 1), date(2025, 11, 1)]}),
                                   F.StartEvidence({"new.com": ev}))
    assert frame["sd_no_evidence"].tolist() == [1.0, 0.0]
    assert frame["sd_age_days"].iloc[1] == pytest.approx(after["sd_age_days"])


def test_open_pagerank_start_evidence_is_refused():
    opr = Dated({"date": "2025-01-31", "kind": "first_seen", "left_censored": False}, "openpagerank_first_month", date(2025, 2, 1))
    with pytest.raises(ValueError):
        F.start_features("x.com", date(2025, 6, 1), [opr])


# ---------------------------------------------------------------------------
# page, history and cell features


def _page(keyword_id: str, when: datetime, sites: list[str], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"keyword_id": keyword_id, "snapshot_time": pd.Timestamp(when), "site": sites,
                         "rank100": np.arange(1, len(sites) + 1), **{m: values for m in F.MODEL_MEASURES}})


def test_page_features_quantiles_churn_and_entrants():
    t1 = datetime(2025, 5, 1, tzinfo=timezone.utc)
    t2 = datetime(2025, 7, 1, tzinfo=timezone.utc)
    sites1 = [f"s{i}.com" for i in range(1, 61)]
    sites2 = ["new1.com", "new2.com"] + sites1[2:58] + ["s1.com", "s2.com"]
    p1 = _page("k", t1, sites1, list(np.linspace(0, -5, 60)))
    p2 = _page("k", t2, sites2, list(np.linspace(0, -5, 60)))
    frame = F.page_feature_frame(pd.concat([p1, p2]))
    first = frame.loc[f"k|{pd.Timestamp(t1)}"]
    second = frame.loc[f"k|{pd.Timestamp(t2)}"]
    assert math.isnan(first["pg_churn_top10"])  # a first page has no churn, not 0
    assert second["pg_churn_top10"] == pytest.approx(0.2)  # s1 and s2 left the top 10
    assert second["pg_new_entrants_top50"] == 2.0
    assert second["pg_gap_from_previous_days"] == pytest.approx(61.0)
    vals = np.linspace(0, -5, 60)[:10]
    assert first["pg_s_cc_hc_t10_q50"] == pytest.approx(float(np.quantile(vals, 0.5)))
    assert first["pg_sites_top10"] == 10.0
    single = F.page_features(p2, earlier_pages=p1)
    assert single["pg_churn_top10"] == pytest.approx(0.2)
    with pytest.raises(ValueError):
        F.page_features(p1, earlier_pages=p2)  # a later page must never shape a page's features


def test_history_uses_only_earlier_pages_and_other_searches():
    positions = pd.DataFrame({
        "locale": "US_en",
        "keyword_id": ["k1", "k2", "k3", "k4"],
        "site": "a.com",
        "snapshot_date": [date(2025, 5, 1), date(2025, 6, 1), date(2025, 6, 1), date(2025, 12, 1)],
        "rank100": [3, 40, 80, 1],
    })
    cells = pd.DataFrame({"site": ["a.com"], "locale": ["US_en"], "keyword_id": ["k2"], "as_of": [date(2025, 7, 1)]})
    hist = F.history_features(cells, positions).iloc[0]
    assert hist["hist_searches_locale"] == 2.0  # k1 and k3; k2 is the cell's own search; k4 is in the future
    assert hist["hist_top10_locale"] == 1.0
    assert hist["hist_top50_locale"] == 1.0
    assert hist["hist_pages_this_search"] == 1.0
    assert hist["hist_last_rank_this_search"] == 40.0


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_leave_one_out_quantile_matches_brute_force(seed: int):
    rng = np.random.default_rng(seed)
    arr = np.sort(rng.integers(0, 5, size=12).astype(float))
    for idx in range(len(arr)):
        v = arr[idx]
        others = np.delete(arr, idx)
        expected = (np.sum(others < v) + 0.5 * np.sum(others == v)) / len(others)
        q, gap = F._loo_quantile_and_gap(np.array([v]), arr, np.array([True]))
        assert q[0] == pytest.approx(expected)
        assert gap[0] == pytest.approx(v - np.median(others))
    q, gap = F._loo_quantile_and_gap(np.array([2.5]), arr, np.array([False]))
    assert q[0] == pytest.approx(np.mean(arr < 2.5))
    assert gap[0] == pytest.approx(2.5 - np.median(arr))


def test_cell_features_missing_title_is_missing_not_zero():
    from aeo.ranksim.forecast.content import ContentScorer

    t = datetime(2025, 5, 1, tzinfo=timezone.utc)
    page = _page("k", t, ["a.com", "b.com", "c.com"], [0.0, -1.0, -2.0])
    page["title"] = ["Best plumbers in town", "Plumber reviews", None]
    scorer = ContentScorer("lexical")
    present = F.cell_features("a.com", "best plumbers", date(2025, 5, 2), page, site_values={m: 1.0 for m in F.MODEL_MEASURES},
                              scorer=scorer)
    assert present["cell_rank_in"] == 1.0
    assert present["content_backend"] == "lexical" and present["cl_title_content"] > 0
    assert present["cl_s_cc_hc_q_top10"] == 1.0  # beats both other sites, itself left out
    absent = F.cell_features("z.com", "best plumbers", date(2025, 5, 2), page, site_values={m: -1.5 for m in F.MODEL_MEASURES},
                             scorer=scorer)
    assert math.isnan(absent["cl_title_content"]) and math.isnan(absent["cell_rank_in"])
    no_title = F.cell_features("c.com", "best plumbers", date(2025, 5, 2), page, site_values={m: -2.0 for m in F.MODEL_MEASURES},
                               scorer=scorer)
    assert math.isnan(no_title["cl_title_content"])
    with pytest.raises(ValueError):
        F.cell_features("a.com", "best plumbers", date(2025, 4, 1), page, site_values={}, scorer=scorer)


# ---------------------------------------------------------------------------
# the frame builders


def test_build_cells_positions_and_depth():
    meta = pd.DataFrame({"keyword_id": ["k", "k"], "keyword": ["x", "x"], "locale": ["US_en"] * 2,
                         "snapshot_time": [pd.Timestamp("2025-05-01", tz="UTC"), pd.Timestamp("2025-07-01", tz="UTC")],
                         "depth": [60, 40]})
    site_rows = pd.DataFrame({"keyword_id": "k", "snapshot_time": [meta["snapshot_time"][0]] * 2 + [meta["snapshot_time"][1]],
                              "site": ["a.com", "b.com", "a.com"], "rank100": [5, 70, 12]})
    cells = F.build_cells(site_rows, meta, (28.0, 120.0)).set_index("site")
    assert cells.loc["a.com", "position_in"] == 5 and cells.loc["a.com", "position_out"] == 12
    assert cells.loc["b.com", "position_in"] == NT50 and cells.loc["b.com", "position_out"] == NT50
    assert not cells["depth_ok"].any()  # the outcome page stops at 40: absence is not NT50
    assert (cells["as_of"] == date(2025, 5, 1)).all()


def test_synthetic_frame_has_upload_cells_with_known_truth():
    from aeo.ranksim.forecast.synth import World

    world = World(5, niches=2, searches_per_niche=4, established_sites=40, new_sites=6, months=12)
    frame = F.build_synthetic_frame(world)
    assert (frame["label"] == SYNTHETIC).all()
    upload = frame[frame["upload_cell"]]
    assert len(upload) > 0
    for row in upload.head(10).itertuples():
        assert row.position_out == world.true_position(row.site, row.keyword, row.outcome_date)
        assert row.as_of <= row.outcome_date - timedelta(days=28)
    # every row's inputs come from a page dated on or before as_of
    assert (pd.to_datetime(frame["input_time"]).dt.date == frame["as_of"]).all()
    assert (frame["outcome_date"] > frame["as_of"]).all()
    for col in ("pg_s_cc_hc_t10_q50", "cl_s_cc_hc_q_top10", "hist_searches_locale", "fp_referring_domains", "st_s_cc_hc"):
        assert col in frame.columns
    assert frame.loc[~frame["site"].isin(world.new_sites), "fp_referring_domains"].isna().all()


def test_freeze_prereg_is_write_once(tmp_path: Path):
    path = tmp_path / "prereg.json"
    digest = F.freeze_prereg(path, {"a": 1, "when": date(2026, 1, 1)})
    assert json.loads(path.read_text())["sha256"] == digest
    assert F.freeze_prereg(path, {"a": 1, "when": date(2026, 1, 1)}) == digest
    assert F.require_prereg(path, {"a": 1, "when": date(2026, 1, 1)}) == digest
    with pytest.raises(ValueError):
        F.freeze_prereg(path, {"a": 2})
    with pytest.raises(ValueError):
        F.require_prereg(path, {"a": 2})
    with pytest.raises(FileNotFoundError):
        F.require_prereg(tmp_path / "missing.json", {"a": 1})


# ---------------------------------------------------------------------------
# start evidence without a dated history (RDAP) never enters a backtest or training frame


def _rdap(registered: date, site: str = "x.com") -> Dated:
    """What RdapLookup.registration_dates builds from a record fetched on 2026-11-22 (dated by the registration)."""
    return Dated({"date": registered.isoformat(), "kind": "registration", "fetched_at": "2026-11-22T16:56:16Z",
                  "url": f"https://rdap.example/domain/{site}"}, "rdap_registration", registered)


def test_start_features_do_not_use_a_registration_record_fetched_after_as_of():
    row = F.start_features("x.com", date(2024, 1, 1), [_rdap(date(2019, 5, 3))])
    assert row["sd_registration_known"] == 0.0
    assert math.isnan(row["sd_age_days"])
    live = F.start_features("x.com", date(2024, 1, 1), [_rdap(date(2019, 5, 3))], live=True)  # live forecasts keep it
    assert live["sd_registration_known"] == 1.0


def test_two_sites_with_the_same_dated_evidence_get_the_same_start_features():
    first_seen = _cc(date(2019, 8, 31), date(2019, 9, 15), date(2019, 5, 31))
    evidence = F.StartEvidence({"still-registered.com": [first_seen, _rdap(date(2019, 6, 1), "still-registered.com")],
                                "dropped-in-2024.com": [first_seen]})
    as_of = date(2021, 3, 1)
    frame = F.start_features_frame(pd.DataFrame({"site": ["still-registered.com", "dropped-in-2024.com"],
                                                 "as_of": [as_of, as_of]}), evidence)
    a, b = frame.iloc[0], frame.iloc[1]
    for col in F.START_COLUMNS:
        assert (pd.isna(a[col]) and pd.isna(b[col])) or a[col] == b[col], col


def test_start_evidence_loader_leaves_rdap_out_unless_live(tmp_path: Path, monkeypatch):
    from aeo.ranksim.forecast.sources import domain_age

    monkeypatch.setattr(domain_age, "cc_first_release", lambda sites, base: {})
    monkeypatch.setattr(domain_age.RdapLookup, "registration_dates",
                        lambda self, sites: {s: _rdap(date(2019, 6, 1), s) for s in sites})
    assert F.StartEvidence.load(["a.com"], tmp_path).by_site == {"a.com": []}
    assert [d.source for d in F.StartEvidence.load(["a.com"], tmp_path, live=True).by_site["a.com"]] == ["rdap_registration"]


# ---------------------------------------------------------------------------
# the shared strength cache and the excluded sites


def test_cached_strength_keeps_rows_another_process_wrote_meanwhile(tmp_path: Path, monkeypatch):
    path = tmp_path / "cache.parquet"
    F.cached_strength(pd.DataFrame({"site": ["a.com"], "as_of": [date(2025, 6, 1)]}), cache_path=path, sources=[cc_source()])
    real_lookup = F.strength_for_dates

    def lookup_while_another_process_writes(missing, **kwargs):
        # another process adds b.com to the shared file while this one is looking c.com up
        monkeypatch.setattr(F, "strength_for_dates", real_lookup)
        F.cached_strength(pd.DataFrame({"site": ["b.com"], "as_of": [date(2025, 6, 1)]}), cache_path=path,
                          sources=[cc_source()])
        return real_lookup(missing, **kwargs)

    monkeypatch.setattr(F, "strength_for_dates", lookup_while_another_process_writes)
    F.cached_strength(pd.DataFrame({"site": ["c.com"], "as_of": [date(2025, 6, 1)]}), cache_path=path, sources=[cc_source()])
    on_disk = pd.read_parquet(path)
    assert sorted(on_disk["site"]) == ["a.com", "b.com", "c.com"]
    assert not [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]  # the temporary file was moved, not left


def test_merge_into_parquet_never_changes_a_cached_value(tmp_path: Path):
    path = tmp_path / "x.parquet"
    F.merge_into_parquet(path, pd.DataFrame({"site": ["a.com"], "as_of": [date(2025, 1, 1)], "v": [1.0]}),
                         keys=["site", "as_of"], columns=["site", "as_of", "v"])
    merged = F.merge_into_parquet(path, pd.DataFrame({"site": ["a.com"], "as_of": [date(2025, 1, 1)], "v": [9.0]}),
                                  keys=["site", "as_of"], columns=["site", "as_of", "v"])
    assert merged["v"].tolist() == [1.0] and pd.read_parquet(path)["v"].tolist() == [1.0]


def test_prepare_page_rows_drops_an_excluded_site_however_it_is_spelled():
    rows = pd.DataFrame({"keyword_id": ["k"] * 3, "keyword": ["q"] * 3, "snapshot_time": ["2025-01-01T00:00:00Z"] * 3,
                         "rank": [1, 2, 3], "site": ["WWW.SmartCrewHQ.com ", "blog.smartcrewhq.com", "plumbco.com"]})
    site_rows, _ = F.prepare_page_rows(rows)
    assert site_rows["site"].tolist() == ["plumbco.com"]


# ---------------------------------------------------------------------------
# design versions and the provenance of a scored run


def test_version_1_payload_is_unchanged_and_later_versions_record_their_reason():
    base = {"name": "x", "version": "x-v1", "a": 1}
    assert F.versioned_payload(base, name="x", version=1, reason=None) == base
    with pytest.raises(ValueError):
        F.versioned_payload(base, name="x", version=2, reason=" ")
    v2 = F.versioned_payload(base, name="x", version=2, reason="panel rebuilt with the data-layer fixes")
    assert v2["version"] == "x-v2" and v2["reason_for_new_version"] == "panel rebuilt with the data-layer fixes"
    assert F.prereg_file("f", 1).name == "prereg.json" and F.prereg_file("f", 2).name == "prereg-v2.json"
    assert F.report_file("f", 1).name == "report.json" and F.report_file("f", 3).name == "report-v3.json"


def test_a_prereg_frozen_with_its_code_refuses_scoring_after_the_code_changed(tmp_path: Path, monkeypatch):
    path = tmp_path / "prereg-v2.json"
    digest = F.freeze_prereg(path, {"a": 1}, code=True)
    stored = json.loads(path.read_text())
    assert stored["code"]["source_tree_sha256"] == F.source_tree_sha256() and "source_sha256" in stored["code"]
    assert F.require_prereg(path, {"a": 1}) == digest
    monkeypatch.setattr(F, "source_tree_sha256", lambda base=None: "0" * 64)
    with pytest.raises(ValueError, match="changed after"):
        F.require_prereg(path, {"a": 1})


def test_code_provenance_records_the_source_hash_of_every_forecast_module():
    prov = F.code_provenance()
    assert {"code_revision", "source_dirty", "dirty_files", "source_tree_sha256", "source_sha256"} <= set(prov)
    assert "backtest.py" in prov["source_sha256"] and "sources/panel.py" in prov["source_sha256"]
    with pytest.raises(F.ProvenanceRefusal):
        F.require_clean_source({**prov, "source_dirty": True, "dirty_files": ["src/aeo/ranksim/forecast/placement.py"]})
    with pytest.raises(F.ProvenanceRefusal):
        F.require_clean_source({**prov, "code_revision": None})


def _git_repo(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return repo


def test_a_prereg_must_be_committed_unchanged_and_pushed_before_scoring(tmp_path: Path):
    import subprocess

    repo = _git_repo(tmp_path)
    prereg = repo / "evals" / "prereg.json"
    prereg.parent.mkdir(parents=True)
    prereg.write_text("{}")
    with pytest.raises(F.ProvenanceRefusal, match="not committed"):
        F.require_prereg_committed(prereg, root=repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "freeze"], cwd=repo, check=True, capture_output=True)
    with pytest.raises(F.ProvenanceRefusal, match="no remote"):
        F.require_prereg_committed(prereg, root=repo)
    prereg.write_text('{"edited": true}')
    with pytest.raises(F.ProvenanceRefusal, match="differs from HEAD"):
        F.require_prereg_committed(prereg, root=repo)
