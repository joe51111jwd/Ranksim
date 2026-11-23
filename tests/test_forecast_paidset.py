"""The paid cohort path (paidset.py and cli_paid.py): upload cells, training on the train split, the cohort backtest.

Offline. No network, no money, no key. The REAL-format tables are tiny SYNTHETIC EDITS written in the exact layout
the paid pull writes (a saved DataForSEO Labs historical_serps response, parsed by sources/panel.build_panel, the
parser pull_past_result_pages calls) and the layout cohort build writes. The end-to-end run uses the SYNTHETIC tables
of trainingset.synthetic_training_set; nothing scored there is accuracy.
"""

from __future__ import annotations

import io
import json
import math
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from aeo.ranksim.forecast import cli, features, forecasting, paidset, pipeline
from aeo.ranksim.forecast import trainingset as ts
from aeo.ranksim.forecast.asof import AsOfGuard, Dated, LeakError
from aeo.ranksim.forecast.config import NT50, forecast_paths
from aeo.ranksim.forecast.content import ContentScorer
from aeo.ranksim.forecast.sources import dfs_samples as dfs
from aeo.ranksim.forecast.sources import newsites as ns
from aeo.ranksim.forecast.sources import panel as panel_mod
from aeo.ranksim.forecast.sources.universe import SearchRow
from aeo.ranksim.forecast.types import REAL, SYNTHETIC

LEXICAL = ContentScorer("lexical")
RULE = paidset.DEFAULT_JOIN_RULE


def nan_strength(site_dates: pd.DataFrame) -> pd.DataFrame:
    """No strength list holds these sites: every strength value is missing (NaN), never zero."""
    out = site_dates[["site", "as_of"]].copy()
    for col in (*features.RAW_STRENGTH_KEYS, *features.MEASURES):
        out[col] = math.nan
    return out


# ---------------------------------------------------------------------------------------------
# The frozen choice of the input page and the outcome page


def page(day: date, *, depth: int = 50, poison: bool = False, ranks: dict[str, int] | None = None) -> paidset.Page:
    return paidset.Page("k", pd.Timestamp(day.isoformat(), tz="UTC"), day, depth, poison, "dfs_labs_historical",
                        dict(ranks or {}), None, None)


def test_the_input_page_is_the_latest_deep_clean_page_on_or_before_the_earliest_launch_date():
    as_of = date(2025, 3, 1)
    pages = [page(date(2025, 1, 20)), page(date(2025, 2, 10), depth=30), page(date(2025, 2, 20), poison=True),
             page(date(2025, 3, 2))]
    chosen, reason = paidset._choose_input(pages, as_of, RULE)
    assert reason is None and chosen.day == date(2025, 1, 20)  # the shallow, the poisoned and the later page are passed over


def test_an_input_page_older_than_the_frozen_limit_or_only_after_launch_is_never_used():
    as_of = date(2025, 6, 1)
    assert paidset._choose_input([page(date(2025, 6, 2))], as_of, RULE) == (None, "no_page_on_or_before_launch")
    old = as_of - timedelta(days=RULE.input_max_age_days + 1)
    assert paidset._choose_input([page(old)], as_of, RULE) == (None, "input_page_too_old")
    assert paidset._choose_input([page(as_of, poison=True)], as_of, RULE) == (None, "input_pages_poison_risk")
    assert paidset._choose_input([page(as_of, depth=49)], as_of, RULE) == (None, "input_pages_shallow")


def test_the_outcome_is_the_page_closest_to_day_90_inside_the_frozen_window():
    launch = date(2025, 1, 1)
    pages = [page(launch + timedelta(days=d)) for d in (59, 70, 95, 121)]
    chosen, reason = paidset._choose_outcome(pages, launch, RULE)
    assert reason is None and (chosen.day - launch).days == 95
    tie = [page(launch + timedelta(days=85)), page(launch + timedelta(days=95))]
    assert (paidset._choose_outcome(tie, launch, RULE)[0].day - launch).days == 85  # a tie goes to the earlier page
    assert paidset._choose_outcome([page(launch + timedelta(days=130))], launch, RULE) == (None, "no_outcome_page_in_window")


def _cell(**extra: Any) -> dict[str, Any]:
    return {"site": "new.com", "launch_date": date(2025, 3, 10), "launch_uncertainty_days": 5, **extra}


def test_a_site_absent_from_a_shallow_outcome_page_is_unknown_and_dropped_never_not_in_top_50():
    launch = date(2025, 3, 10)
    pages = [page(date(2025, 3, 1)), page(launch + timedelta(days=90), depth=30)]
    assert paidset._join_one(_cell(), pages, RULE, want_reading=False) == (None, "outcome_unknown_shallow_page")
    pages = [page(date(2025, 3, 1)), page(launch + timedelta(days=90), depth=30, ranks={"new.com": 12})]
    row, reason = paidset._join_one(_cell(), pages, RULE, want_reading=False)
    assert reason is None and row["position_out"] == 12  # present on a short page: its position is known


def test_a_site_absent_from_a_deep_outcome_page_is_a_real_not_in_top_50_and_the_cell_is_kept():
    launch = date(2025, 3, 10)
    pages = [page(date(2025, 3, 1)), page(launch + timedelta(days=88), depth=100, ranks={"new.com": 64})]
    row, reason = paidset._join_one(_cell(), pages, RULE, want_reading=False)
    assert reason is None and row["position_out"] == NT50 and row["never_ranked_to_outcome"]
    assert row["position_in"] == NT50 and row["as_of"] == launch - timedelta(days=5)
    assert row["lead_days"] == (launch + timedelta(days=88) - row["as_of"]).days


def test_an_unknown_or_too_large_launch_uncertainty_drops_the_cell_with_its_reason():
    pages = [page(date(2025, 3, 1))]
    assert paidset._join_one(_cell(launch_uncertainty_days=None), pages, RULE, want_reading=False)[1] == "launch_uncertainty_unknown"
    big = RULE.max_launch_uncertainty_days + 1
    assert paidset._join_one(_cell(launch_uncertainty_days=big), pages, RULE, want_reading=False)[1] == "launch_uncertainty_too_large"


def test_the_input_page_must_be_on_or_before_the_earliest_plausible_launch_date():
    launch = date(2025, 3, 10)
    pages = [page(date(2025, 3, 8), ranks={"new.com": 3}), page(launch + timedelta(days=90))]
    # The page of 8 March is after 10 March minus 5 days, so it could hold the site's own early pages.
    assert paidset._join_one(_cell(), pages, RULE, want_reading=False)[1] == "no_page_on_or_before_launch"


def test_the_time_cutoff_is_the_earliest_test_forecast_date_from_launch_dates_only():
    cells = pd.DataFrame([
        {"split": "test", "launch_date": date(2025, 9, 10), "launch_uncertainty_days": 10},
        {"split": "test", "launch_date": date(2025, 8, 1), "launch_uncertainty_days": 400},  # dropped by the join anyway
        {"split": "train", "launch_date": date(2025, 1, 1), "launch_uncertainty_days": 0},
    ])
    assert paidset.time_cutoff(cells) == date(2025, 8, 31)


def test_the_join_rule_hash_covers_its_values_and_its_code():
    assert paidset.JoinRule().rule_hash() == RULE.rule_hash()
    assert paidset.JoinRule(input_max_age_days=90).rule_hash() != RULE.rule_hash()


# ---------------------------------------------------------------------------------------------
# REAL-format tables: a saved Labs historical_serps response and the cohort build layout

TOPICS = ("plumber", "dentist", "roofer", "baker", "florist")
SUFFIXES = ("near me", "prices", "reviews", "services", "emergency", "open now late", "cheap deals today", "best rated local")
LAUNCHES = {"plumberco.com": date(2025, 1, 10), "dentistco.com": date(2025, 2, 10), "rooferco.com": date(2025, 3, 10),
            "bakerco.com": date(2025, 4, 10), "floristco.com": date(2025, 12, 10)}
UNCERTAINTY = {"plumberco.com": 10, "dentistco.com": 90, "rooferco.com": 10, "bakerco.com": 10, "floristco.com": 10}
LOCALE = "US_en"


def _keywords() -> list[str]:
    return [f"{t} {s}" for t in TOPICS for s in SUFFIXES]


def _labs_response(path: Path, *, shallow: set[tuple[str, str]], placed: dict[tuple[str, str], tuple[str, int]]) -> Path:
    """A historical_serps response in DataForSEO's exact layout: one task per search, one dated page per month."""
    months = [date(2024 + (10 + i) // 12, (10 + i) % 12 + 1, 1) for i in range(20)]  # 1 Nov 2024 to 1 Jun 2026
    tasks = []
    for keyword in _keywords():
        pages = []
        for day in months:
            depth = 30 if (keyword, day.isoformat()) in shallow else 50
            sites = [f"comp{i}.com" for i in range(1, depth + 1)]
            if (keyword, day.isoformat()) in placed:
                site, rank = placed[(keyword, day.isoformat())]
                sites[rank - 1] = site
            items = [{"type": "organic", "rank_group": r, "rank_absolute": r, "domain": s, "main_domain": s,
                      "url": f"https://{s}/", "title": f"{keyword} at {s}", "etv": 1.0} for r, s in enumerate(sites, start=1)]
            pages.append({"se_type": "google", "keyword": keyword, "type": "organic", "location_code": 2840,
                          "language_code": "en", "datetime": f"{day.isoformat()} 08:00:00 +00:00", "items_count": depth,
                          "se_results_count": 1000, "items": items})
        tasks.append({"id": keyword, "status_code": 20000, "status_message": "Ok.", "cost": 0.0005, "result_count": 1,
                      "result": [{"se_type": "google", "keyword": keyword, "location_code": 2840, "language_code": "en",
                                  "total_count": len(pages), "items_count": len(pages), "items": pages}]})
    path.write_text(json.dumps({"status_code": 20000, "tasks": tasks}), encoding="utf-8")
    return path


def _cohort_frame() -> pd.DataFrame:
    rows = []
    for site, launch in sorted(LAUNCHES.items()):
        rows.append(ns.CohortRow(
            site=site, registered_on=launch - timedelta(days=20), registered_on_source="given", listed_on=launch - timedelta(days=20),
            source="test", tld="com", rdap_status=None, rdap_registered_on=None, nameservers=(), ns_parking=None, dns_resolves=None,
            current_state_checked_at=None, cc_crawls_checked=(), cc_pre_registration_captures=None, cc_first_seen=launch,
            cc_first_content=launch, cc_pages_first_content_crawl=5, home_url=f"https://{site}/", home_title=None,
            launch_date=launch, launch_date_source="cc_first_content_capture:TEST", launch_uncertainty_days=UNCERTAINTY[site],
            parked=False, parked_now=False, possibly_re_registered=False, possible_rebrand=False, no_content_evidence=False,
            redirects_offsite=False, re_registered_after_listing=False, registration_older_than_list=False,
            not_registered_now=False, resolvable_or_crawled=True, content_evidence=True, kept=True))
    return ns.rows_to_frame(rows)


def _launch_pages() -> dict[str, ns.PageText]:
    out = {}
    for site, launch in LAUNCHES.items():
        topic = site.replace("co.com", "")
        out[site] = ns.PageText(site=site, crawl="CC-TEST", url=f"https://{site}/", captured_at=f"{launch.isoformat()}T10:00:00Z",
                                http_status=200, title=f"{topic.title()} services and prices", meta_description=f"Your {topic}",
                                h1=(topic,), h2=(), h3=(), text=f"We are a {topic}. Call us for {topic} services.",
                                visible_chars=60, parked_text=False, rebrand_text=False, sha256="0" * 64)
    return out


def _universe() -> list[SearchRow]:
    out = []
    for i, keyword in enumerate(_keywords()):
        out.append(SearchRow(keyword=keyword, keyword_id=dfs.keyword_id(LOCALE, keyword), locale=LOCALE, country="US",
                             niche="local", seed_term="", search_volume=100, competition=None, cpc=None, kd=None, intent=None,
                             sources="test", difficulty_proxy=float(i), difficulty_proxy_source="test",
                             stratum=("easy", "medium", "hard")[i % 3]))
    return out


@pytest.fixture()
def real_set(tmp_path: Path) -> dict[str, Path]:
    """A REAL-labelled training set in the exact layouts the pulls and cohort build write. Tiny SYNTHETIC EDITS.

    The site plumberco.com is placed at rank 7 on the outcome page (1 April 2025, day 81) of one of its train searches.
    The outcome page (1 June 2025, day 83) of one rooferco.com train search stops at rank 30 without the site.
    """
    folder = tmp_path / "data" / "cache" / "trainingset" / "cells"
    ts.write_cells_set(folder, _cohort_frame(), _launch_pages(), _universe())
    cells = pd.read_parquet(folder / ts.CELLS_FILE)
    train = cells[cells["split"] == "train"].sort_values("keyword")
    placed_kw = str(train[train["site"] == "plumberco.com"]["keyword"].iloc[0])
    shallow_kw = str(train[train["site"] == "rooferco.com"]["keyword"].iloc[0])
    placed = {(placed_kw, "2025-04-01"): ("plumberco.com", 7)}
    shallow = {(shallow_kw, "2025-06-01")}
    raw = _labs_response(tmp_path / "historical_serps.json", shallow=shallow, placed=placed)
    labs = tmp_path / "data" / "cache" / "panel_labs"
    panel_mod.build_panel([raw], out_dir=labs, labs_locale=LOCALE)
    return {"folder": folder, "labs": labs, "data": tmp_path / "data", "placed": placed_kw, "shallow": shallow_kw}


def test_real_format_pages_and_cells_join_into_real_upload_cells_with_never_ranked_searches(real_set):
    paths = forecast_paths(real_set["data"])
    join = paidset.join_upload_cells(real_set["folder"], splits=("train", "test"), paths=paths, strength=nan_strength,
                                     content_scorer=LEXICAL)
    cells = join.cells
    assert join.label == REAL and set(cells["label"]) == {REAL} and len(cells)
    assert set(cells["split"]) <= {"train", "test"}
    hit = cells[(cells["site"] == "plumberco.com") & (cells["keyword"] == real_set["placed"])]
    assert len(hit) == 1 and int(hit["position_out"].iloc[0]) == 7 and not bool(hit["never_ranked_to_outcome"].iloc[0])
    assert int(hit["outcome_days_since_launch"].iloc[0]) == 81 and int(hit["position_in"].iloc[0]) == NT50
    assert (cells["position_out"] == NT50).sum() > 0 and cells["never_ranked_to_outcome"].sum() > 0  # never-ranked cells are kept
    dropped = join.dropped
    assert dropped.get("launch_uncertainty_too_large", 0) > 0  # dentistco.com: 90 days of doubt
    assert "dentistco.com" not in set(cells["site"])
    assert cells["content_match"].notna().all() and set(cells["content_match_backend"]) == {"lexical"}
    assert (cells["input_date"] <= cells["as_of"]).all() and (cells["outcome_date"] > cells["as_of"]).all()
    feats = [f for f in forecasting.PLACEMENT_FEATURES if f in cells.columns]
    assert feats and all(cells[f].isna().all() for f in feats if f.startswith("st_"))  # no list value: NaN, never 0
    assert cells["pg_sites_top10"].notna().all()


def test_a_shallow_outcome_page_in_real_format_is_counted_and_dropped(real_set):
    paths = forecast_paths(real_set["data"])
    join = paidset.join_upload_cells(real_set["folder"], splits=("train", "test"), paths=paths, build_features=False)
    assert join.dropped_by_split["train"].get("outcome_unknown_shallow_page") == 1
    assert not ((join.cells["site"] == "rooferco.com") & (join.cells["keyword"] == real_set["shallow"])).any()
    assert join.dropped_by_split["train"]["split_excluded"] > 0  # search groups of the test site leave train cells out


def test_the_real_cell_set_is_label_blind_split_and_labelled_real(real_set):
    cells = pd.read_parquet(real_set["folder"] / ts.CELLS_FILE)
    meta = json.loads((real_set["folder"] / ts.REAL_META_FILE).read_text())
    assert meta["label"] == REAL and set(cells["label"]) == {REAL}
    assert meta["split"] == {"sites_on_both_sides": 0, "groups_on_both_sides": 0}
    assert set(cells[cells["split"] == "test"]["site"]) == {"floristco.com"}  # the latest launch month is the test side
    texts = ts.load_page_texts(real_set["folder"] / ts.PAGE_TEXT_FILE)
    assert texts["plumberco.com"][0]["captured_on"] == LAUNCHES["plumberco.com"]


def test_mixed_labels_are_refused(real_set, tmp_path):
    paths = forecast_paths(real_set["data"])
    (real_set["folder"] / ts.SYNTHETIC_META_FILE).write_text("{}")
    with pytest.raises(paidset.PaidSetRefusal, match="never mixed"):
        paidset.open_training_set(real_set["folder"], paths=paths)
    (real_set["folder"] / ts.SYNTHETIC_META_FILE).unlink()
    cells = pd.read_parquet(real_set["folder"] / ts.CELLS_FILE)
    cells.loc[0, "label"] = SYNTHETIC
    cells.to_parquet(real_set["folder"] / ts.CELLS_FILE, index=False)
    with pytest.raises(paidset.PaidSetRefusal, match="never mixed"):
        paidset.open_training_set(real_set["folder"], paths=paths)


def test_real_cells_are_never_joined_with_synthetic_pages(real_set, tmp_path):
    fake = tmp_path / "fake_panel" / f"dataset={ts.SYNTHETIC_DATASET}" / "locale=US_en"
    fake.mkdir(parents=True)
    frame = panel_mod.load_panel(panel_dir=real_set["labs"]).head(5).assign(dataset=ts.SYNTHETIC_DATASET)
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.Table.from_pandas(frame, schema=panel_mod.PANEL_SCHEMA, preserve_index=False), fake / "part-x.parquet")
    with pytest.raises(paidset.PaidSetRefusal, match="SYNTHETIC pages"):
        paidset.join_upload_cells(real_set["folder"], splits=("train",), panel_dir=tmp_path / "fake_panel",
                                  paths=forecast_paths(real_set["data"]), build_features=False)


def test_a_cell_of_an_excluded_site_is_refused(real_set):
    cells = pd.read_parquet(real_set["folder"] / ts.CELLS_FILE)
    cells.loc[0, "site"] = "www.SmartCrewHQ.com"
    cells.to_parquet(real_set["folder"] / ts.CELLS_FILE, index=False)
    with pytest.raises(paidset.PaidSetRefusal, match="excluded site"):
        paidset.open_training_set(real_set["folder"], paths=forecast_paths(real_set["data"]))


def test_launch_page_text_dated_after_the_launch_date_raises_a_leak(real_set):
    path = real_set["folder"] / ts.PAGE_TEXT_FILE
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        row["captured_on"] = (date.fromisoformat(row["captured_on"]) + timedelta(days=40)).isoformat()
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(LeakError):
        paidset.join_upload_cells(real_set["folder"], splits=("train",), paths=forecast_paths(real_set["data"]),
                                  strength=nan_strength, content_scorer=LEXICAL)


def test_a_strength_value_published_after_the_forecast_date_raises_a_leak(real_set):
    def late_strength(site_dates: pd.DataFrame) -> pd.DataFrame:
        for day in site_dates["as_of"]:
            AsOfGuard(day).take(Dated(1.0, "a list published later", day + timedelta(days=1)))
        return nan_strength(site_dates)

    with pytest.raises(LeakError):
        paidset.join_upload_cells(real_set["folder"], splits=("train",), paths=forecast_paths(real_set["data"]),
                                  strength=late_strength, content_scorer=LEXICAL)


def test_strength_lists_that_cannot_be_read_leave_strength_missing_with_a_note_never_zero(real_set):
    calls: list[int] = []

    def broken(site_dates: pd.DataFrame) -> pd.DataFrame:
        calls.append(len(site_dates))
        raise OSError("no strength lists here")

    join = paidset.join_upload_cells(real_set["folder"], splits=("train",), paths=forecast_paths(real_set["data"]),
                                     strength=broken, content_scorer=LEXICAL)
    assert len(calls) == 1  # looked up once, never once per cell
    assert len(join.cells) and join.cells["st_s_cc_hc"].isna().all() and join.cells["cl_s_cc_hc_q_top10"].isna().all()
    assert any("could not be read" in note for note in join.notes)


def test_brand_searches_are_flagged_and_never_handed_to_training():
    rows = pd.DataFrame([{"split": "train", "is_brand": True, "site": "a.com"}, {"split": "train", "is_brand": False, "site": "b.com"}])
    join = paidset.JoinResult(REAL, rows, {}, {}, RULE, None, None, "f", "p", "c", "x", None)
    assert list(paidset.training_rows(join)["site"]) == ["b.com"]
    join.cells = pd.concat([rows, pd.DataFrame([{"split": "test", "is_brand": False, "site": "c.com"}])], ignore_index=True)
    with pytest.raises(paidset.PaidSetRefusal, match="train split only"):
        paidset.training_rows(join)


def test_updater_rows_take_their_features_from_the_month_1_page_on_its_own_day():
    rows = pd.DataFrame([
        {"site": "a.com", "keyword_id": "k1", "split": "train", "pg_sites_top10": 9.0, "reading_date": date(2025, 2, 10),
         "reading_rank": 8.0, "reading_depth": 50, "outcome_date": date(2025, 4, 10), "as_of": date(2025, 1, 5)},
        {"site": "b.com", "keyword_id": "k2", "split": "train", "pg_sites_top10": 9.0, "reading_date": date(2025, 2, 10),
         "reading_rank": math.nan, "reading_depth": 30, "outcome_date": date(2025, 4, 10), "as_of": date(2025, 1, 5)},
        {"site": "c.com", "keyword_id": "k3", "split": "train", "pg_sites_top10": 9.0, "reading_date": None,
         "reading_rank": math.nan, "reading_depth": math.nan, "outcome_date": date(2025, 4, 10), "as_of": date(2025, 1, 5)},
    ])
    later = pd.DataFrame([{"site": "a.com", "keyword_id": "k1", "pg_sites_top10": 4.0},
                          {"site": "b.com", "keyword_id": "k2", "pg_sites_top10": 5.0}])
    out = paidset.updater_rows(rows, REAL, later)
    assert list(out["site"]) == ["a.com", "b.com"]  # no month 1 page: no updater row
    assert list(out["pg_sites_top10"]) == [4.0, 5.0] and set(out["as_of"]) == {date(2025, 2, 10)}
    assert out["reading_position"].iloc[0] == 8.0
    assert math.isnan(out["reading_position"].iloc[1])  # absent from a page 30 deep: unknown, never NOT IN TOP 50
    assert list(out["lead_days"]) == [59.0, 59.0]
    assert paidset.updater_rows(rows, REAL, None) is None


def test_train_data_paid_names_the_pull_commands_and_the_cell_command_when_inputs_are_missing(tmp_path):
    result = forecasting.train(forecast_paths(tmp_path), data="paid", name="paid-missing")
    assert result.bundle is None
    for words in ("pull past-pages", "pull traffic", "pull positions", "paid cells"):
        assert words in result.refusal
    assert not (tmp_path / "models" / "paid-missing").exists()


def test_a_real_scored_run_never_skips_the_git_checks(real_set):
    with pytest.raises(paidset.PaidSetRefusal, match="always checks git"):
        paidset.score_design(forecast_paths(real_set["data"]), folder=real_set["folder"], bundle_name="none",
                             root=real_set["data"] / "designs", check_git=False)


# ---------------------------------------------------------------------------------------------
# The whole path on SYNTHETIC tables, from the command line in a temporary folder


def run_cli(argv: list[str], data: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv + ["--dir", str(data)], clock=pipeline.SystemClock(), stdout=out, stderr=err,
                    settings_factory=lambda: None)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(scope="module")
def synthetic_run(tmp_path_factory) -> dict[str, Any]:
    """paid synthetic, paid join, paid train from the command line. SYNTHETIC."""
    data = tmp_path_factory.mktemp("paid") / "data"
    folder = data / "cache" / "trainingset" / "synthetic"
    code, out, err = run_cli(["paid", "synthetic", "--seed", "5", "--niches", "3", "--searches-per-niche", "60",
                              "--new-sites", "45"], data)
    assert code == 0, err
    assert all("SYNTHETIC" in line for line in out.splitlines() if line.strip())
    calls: list[str] = []
    original = paidset._join_one

    def spy(cell, pages, rule, *, want_reading):
        calls.append(str(cell["split"]))
        return original(cell, pages, rule, want_reading=want_reading)

    paidset._join_one = spy
    try:
        code, train_out, err = run_cli(["paid", "train", "--set", str(folder), "--name", "syn-paid", "--content-backend",
                                        "lexical"], data)
    finally:
        paidset._join_one = original
    assert code == 0, err
    return {"data": data, "folder": folder, "train_out": train_out, "train_splits": calls}


def test_training_never_reads_a_test_row(synthetic_run):
    assert synthetic_run["train_splits"] and set(synthetic_run["train_splits"]) == {"train"}
    bundle = forecasting.load_bundle(forecast_paths(synthetic_run["data"]), "syn-paid")
    assert bundle.manifest["rows_read_by_split"]["test"] == 0
    cutoff = date.fromisoformat(bundle.manifest["time_cutoff"])
    assert bundle.training_cutoff < cutoff  # no training outcome is dated on or after the earliest test forecast


def test_the_paid_bundle_is_synthetic_includes_never_ranked_cells_and_names_its_content_backend(synthetic_run):
    bundle = forecasting.load_bundle(forecast_paths(synthetic_run["data"]), "syn-paid")
    manifest = bundle.manifest
    assert bundle.training_label == SYNTHETIC and manifest["includes_never_ranked"] is True and not bundle.conditional
    assert manifest["training_cells"] == forecasting.CELLS_UPLOAD_WITH_NEVER_RANKED
    assert manifest["content_match_backend"] == "lexical"
    assert manifest["content_match_is_model_input"] is ("content_match" in manifest["features"])
    assert manifest["never_ranked"]["never_ranked"] > 0
    assert manifest["join_rule_hash"] == RULE.rule_hash()
    assert set(manifest["features"]) <= set(forecasting.PLACEMENT_FEATURES)
    assert all("SYNTHETIC" in line for line in synthetic_run["train_out"].splitlines() if line.strip())


def test_the_synthetic_cohort_backtest_freezes_and_scores_from_the_command_line(synthetic_run):
    data, folder = synthetic_run["data"], synthetic_run["folder"]
    root = data / "designs"
    code, out, err = run_cli(["cohort-backtest", "freeze", "--set", str(folder), "--bundle", "syn-paid"], data)
    assert code == 2 and "explicit root" in err  # never beside the REAL designs
    code, out, err = run_cli(["cohort-backtest", "freeze", "--set", str(folder), "--bundle", "syn-paid", "--root", str(root)], data)
    assert code == 0, err
    assert "Nothing was scored" in out and "(SYNTHETIC)" in out
    code, out, err = run_cli(["cohort-backtest", "score", "--set", str(folder), "--bundle", "syn-paid", "--root", str(root),
                              "--skip-git-check", "--content-backend", "lexical"], data)
    assert code == 1, err  # a SYNTHETIC run never passes a marker
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines and all("SYNTHETIC" in line for line in lines)
    assert "NOT A MARKER RESULT (SYNTHETIC)" in out
    report = json.loads((root / paidset.DESIGN_NAME / "report.json").read_text())
    assert report["label"] == SYNTHETIC and report["source"] == "synthetic" and report["marker_3"]["passed"] is None
    assert set(report["headline"]["baselines"]) == {"always_nt50", "persistence", "band_prior", "site_strength"}
    assert report["never_ranked"]["cells"] > 0 and "range_coverage" in report and "dropped_cells" in report
    assert report["every_graded_cell_held_out"] is True and report["held_out"]["not_test"] == 0
    code, out, err = run_cli(["cohort-backtest", "score", "--set", str(folder), "--bundle", "syn-paid", "--root", str(root),
                              "--skip-git-check", "--content-backend", "lexical"], data)
    assert code == 2 and "scored once" in err  # a design version is scored once


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
                   cwd=repo, check=True, capture_output=True)


def test_the_scored_run_is_refused_until_the_design_is_committed_and_pushed(synthetic_run, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "start")
    monkeypatch.setattr(features, "repo_root", lambda: repo)
    paths = forecast_paths(synthetic_run["data"])
    root = repo / "evals"
    kwargs = dict(folder=synthetic_run["folder"], bundle_name="syn-paid", root=root, name="cohort-git")
    paidset.freeze_design(paths, **kwargs)
    with pytest.raises(features.ProvenanceRefusal, match="not committed"):
        paidset.score_design(paths, content_scorer=LEXICAL, **kwargs)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "freeze")
    with pytest.raises(features.ProvenanceRefusal, match="no remote"):
        paidset.score_design(paths, content_scorer=LEXICAL, **kwargs)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    result = paidset.score_design(paths, content_scorer=LEXICAL, **kwargs)
    assert result["label"] == SYNTHETIC and result["manifest"]["git_checked"] is True
    assert result["manifest"]["bundle_sha256"] == forecasting.load_bundle(paths, "syn-paid").sha256
    with pytest.raises(features.AlreadyScored):
        paidset.score_design(paths, content_scorer=LEXICAL, **kwargs)


def test_test_cells_are_scored_only_with_the_content_backend_the_bundle_learned(synthetic_run):
    bundle = forecasting.load_bundle(forecast_paths(synthetic_run["data"]), "syn-paid")
    assert paidset.scorer_for_bundle(bundle, None).mode == "lexical"  # the default is the bundle's own backend

    class OtherScale:
        mode = "cross_encoder"

    with pytest.raises(paidset.PaidSetRefusal, match="different scales|--content-backend lexical"):
        paidset.scorer_for_bundle(bundle, OtherScale())


def test_the_design_refuses_a_changed_join_rule_or_a_bundle_from_another_cell_table(synthetic_run, tmp_path):
    paths = forecast_paths(synthetic_run["data"])
    root = tmp_path / "designs"
    kwargs = dict(folder=synthetic_run["folder"], bundle_name="syn-paid", root=root, name="cohort-rule")
    paidset.freeze_design(paths, **kwargs)
    with pytest.raises(ValueError, match="does not match"):
        paidset.score_design(paths, check_git=False, rule=paidset.JoinRule(input_max_age_days=90), **kwargs)
    copy = tmp_path / "copy"
    import shutil

    shutil.copytree(synthetic_run["folder"], copy)
    cells = pd.read_parquet(copy / ts.CELLS_FILE)
    cells.drop(index=cells.index[-1]).to_parquet(copy / ts.CELLS_FILE, index=False)
    with pytest.raises(paidset.PaidSetRefusal, match="another cell table"):
        paidset.freeze_design(paths, folder=copy, bundle_name="syn-paid", root=root, name="cohort-other")
