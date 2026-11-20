"""Target 2 counterfactual pairwise check. DIAGNOSTIC ONLY, not proof. Scores are PREDICTED.

Question: when a lower-ranked page L takes the higher-ranked page H's key on-page
signals, does the repo's frozen ranker (evals/preregistered/forward-pair-2026-11-16/
model-next.json, monotone LambdaMART, sha256 bc4de502...) score the edited page higher?

Queries: the 70 searches added on 2026-11-16 (rsdata group 'a70'), single top-10 pull
'smartcrew-q100:2026-11-16'. The frozen ranker was fit on transitions of the five
30-query snapshot files (Nov 6, 9, 11, 13, 16); this script re-checks that those files
hold no a70 query (it reads only their query column and sha256, never their rows as data).

Pages: the Nov 16 (ET) fetch of each URL from the sealed page index, parsed with the
ranker's own parser (pair_order.parse_html). Preference: timed http, timed browser,
date-only http, date-only browser; a page needs >= 50 extracted words.

Features: pair_order.build_transition with history = the o30 snapshot pulls of Nov 6,
9, 11, 13 (all before the outcome day; a70 queries have no own history, so every a70
URL is 'new' and the model leans on domain history, off-page and content features).
Off-page values come from the frozen offpage.tsv; domains absent from it get none.

Pairs: H and L both in the query's top 10, both with a usable page, H ranked better.
Swaps (L keeps its URL, page-type flags, dates and off-page values):
  head            L* = L with H's title, H1 and h1-h3 heading text
  head_body       head + H's main body text and word count
  head_body_serp  head_body + H's SERP title (what Google would show after the edit)
H* is the mirror (H with L's fields). Every swap rebuilds the whole query's candidate
set, because BM25F IDF, the shared-vocabulary feature and the within-query z-scores
depend on the other pages.

Reported, with query-bootstrap 95% CIs: share score(L*) > score(L), share score(H*) <
score(H), share of exact ties (swap did not move the score), share score(L*) > score(H),
and the plain pairwise accuracy score(H) > score(L).
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aeo.ranksim.pair_order import (  # noqa: E402
    Snap,
    SnapRow,
    _vector,
    build_transition,
    load_model,
    load_offpage_cache,
    parse_html,
)
from scripts.claude import metrics as M  # noqa: E402
from scripts.claude import rsdata  # noqa: E402
from scripts.claude.t2_common import OUT_DIR, fetch_table  # noqa: E402

FREEZE = ROOT / "evals/preregistered/forward-pair-2026-11-16"
MODEL_SHA = "bc4de5022a44c91bdb481c46f28c5374ee5477d1990d55931a21a4ce0e2a8ea7"
OUTCOME_DAY = "2026-11-16"
OUTCOME_SOURCE = "smartcrew-q100:2026-11-16"
HISTORY_DAYS = ("2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13")
TRAINING_SERPS = {
    "2026-11-06": ("/workspace/ranksim-live-baseline-2026-11-06/serp_serper_live.csv",
                   "c70ca9907a5243f092fe407d18ca002dc58a1bad301734fc56b6dad50e900a87"),
    "2026-11-09": ("/workspace/ranksim-live-snapshot-2026-11-09/serp_serper_live.csv",
                   "49a0c8d4b92bd1fd73a2f4fac507394200e724f6c76c611a9c14b9595b3607d0"),
    "2026-11-11": ("/workspace/ranksim-live-snapshot-2026-11-11/serp_serper_live.csv",
                   "e4c76d7336febb64e7e54d62f00f7b0a90adfe92f7110934e82d5bd9ca51767b"),
    "2026-11-13": ("/workspace/ranksim-live-snapshot-2026-11-13/serp_serper_live.csv",
                   "336211a3c78ceddd5e6ca43c6e764ce5a41d7e2d0b8db0ac15def5fc398fe306"),
    "2026-11-16": ("/workspace/ranksim-live-snapshot-2026-11-16/serp_serper_live.csv",
                   "65d0581df48d49dde936d6e01d8237abd521249e293de853a2bf32f6297a43c2"),
}
VARIANTS = ("head", "head_body", "head_body_serp")
MIN_WORDS = 50
TIE = 1e-12
HISTORY_BLOCK = {
    "has_history", "last_relevance", "url_mean_relevance", "appearances", "trend_relevance", "history_domain_gap",
    "domain_mean_relevance", "domain_query_relevance", "domain_seen", "domain_count_log", "replaced_domain_relevance",
    "url_transfer", "domain_transfer",
}
SERP_TITLE_BLOCK = {"serp_title_coverage", "serp_title_phrase"}
URL_BLOCK = {"url_overlap", "url_path_depth"}
OFFPAGE_BLOCK = {"tranco_listed", "tranco_popularity", "majestic_listed", "majestic_log", "cc_listed", "cc_pagerank", "cc_harmonic"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_training_queries(a70: set[str]) -> dict:
    """The five files the frozen ranker was fit on: hash check and query overlap with a70."""
    found = {}
    for day, (path, expected) in TRAINING_SERPS.items():
        actual = _sha256(Path(path))
        with open(path, newline="", encoding="utf-8-sig") as handle:
            queries = {" ".join((row.get("query") or "").split()) for row in csv.DictReader(handle)}
        found[day] = {"sha256_matches_log": actual == expected, "queries": len(queries), "a70_overlap": sorted(queries & a70)}
    return found


class FrozenScorer:
    """Same arithmetic as pair_order.BoostingModel.predict, with the booster parsed once."""

    def __init__(self, path: Path) -> None:
        import lightgbm as lgb

        if _sha256(path) != MODEL_SHA:
            raise SystemExit(f"{path} does not match the frozen sha256")
        self.model = load_model(path)
        self.booster = lgb.Booster(model_str=self.model.model_text)

    def __call__(self, rows: list) -> list[float]:
        matrix = np.asarray([_vector(row, self.model.features, self.model.mean, self.model.std) for row in rows], dtype=np.float64)
        return [float(value) for value in self.booster.predict(matrix)]

    def importance(self) -> dict[str, float]:
        gains = self.booster.feature_importance(importance_type="gain")
        # The booster stores Column_i; column i is model.features[i] (pair_order._vector order).
        return {name: float(value) for name, value in zip(self.model.features, gains, strict=True)}


def _snap(rows: pd.DataFrame, day: str) -> Snap:
    items = [
        SnapRow(query=row.query, url=row.url, position=float(row.position), title=row.title or "", url_key=row.key, site=row.site)
        for row in rows.itertuples(index=False)
    ]
    return Snap(day=day, rows=items)


def _pages(keys: set[str]) -> tuple[dict, dict]:
    fetches = fetch_table(rsdata.DESIGN_MAX_DATE)
    fetches = fetches[(fetches["day"] == OUTCOME_DAY) & fetches["key"].isin(keys)].copy()
    fetches["_rank"] = (fetches["date_only"].astype(int) * 2) + (fetches["fetch_method"] != "http").astype(int)
    fetches = fetches.sort_values(["key", "_rank", "fetched_at"], ascending=[True, True, False])
    pages: dict = {}
    source: dict = {}
    for key, group in fetches.groupby("key", sort=True):
        for row in group.itertuples(index=False):
            view = parse_html(row.url, rsdata.load_page_html(int(row.id)))
            if view.present and view.word_count >= MIN_WORDS:
                pages[key] = view
                source[key] = f"{row.fetch_method}{':date-only' if row.date_only else ''}"
                break
    return pages, source


def _swap(target, donor, variant: str):
    edited = replace(target, title=donor.title, h1=donor.h1, headings=donor.headings)
    if variant in ("head_body", "head_body_serp"):
        edited = replace(edited, body=donor.body, word_count=donor.word_count)
    return edited


def _scores(scorer, history, query_rows: pd.DataFrame, pages, offpage, serp_titles: dict | None = None) -> dict[str, float]:
    outcome = _snap(query_rows, OUTCOME_DAY)
    if serp_titles:
        outcome = Snap(day=OUTCOME_DAY, rows=[replace(row, title=serp_titles.get(row.url_key, row.title)) for row in outcome.rows])
    built = build_transition(history, outcome, pages, offpage)
    return dict(zip([row.url_key for row in built], scorer(built), strict=True))


def gain_summary(scorer: FrozenScorer) -> dict:
    gains = scorer.importance()
    total = sum(gains.values()) or 1.0
    blocks = {"history": HISTORY_BLOCK, "serp_title": SERP_TITLE_BLOCK, "url": URL_BLOCK, "offpage": OFFPAGE_BLOCK}
    share = {name: sum(gains.get(feature, 0.0) for feature in members) / total for name, members in blocks.items()}
    share["on_page_content"] = 1.0 - sum(share.values())
    top = sorted(((name, round(value / total, 4)) for name, value in gains.items()), key=lambda item: -item[1])[:12]
    return {"model_gain_share": share, "top_features_by_gain": top}


def gain_only() -> int:
    """Recompute only the gain-share fields of counterfactual.json from the frozen model."""
    path = OUT_DIR / "counterfactual.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary.update(gain_summary(FrozenScorer(FREEZE / "model-next.json")))
    path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=float) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("model_gain_share", "top_features_by_gain")}, indent=2))
    return 0


def main() -> int:
    if "--gain-only" in sys.argv:
        return gain_only()
    rows = rsdata.load_rows(rsdata.DESIGN_MAX_DATE)
    groups = rsdata.query_groups(rows)
    a70 = {query for query, label in groups.items() if label == "a70"}
    verification = verify_training_queries(a70)
    scorer = FrozenScorer(FREEZE / "model-next.json")
    offpage = load_offpage_cache(FREEZE / "offpage.tsv")
    top = rows[rows["kind"] == "top10"]
    history = [_snap(top[top["source"] == f"smartcrew-snapshot:{day}"], day) for day in HISTORY_DAYS]
    outcome_rows = top[(top["source"] == OUTCOME_SOURCE) & top["query"].isin(a70)]
    outcome_rows = outcome_rows.sort_values(["query", "position"]).drop_duplicates(["query", "key"], keep="first")
    pages, page_source = _pages(set(outcome_rows["key"]))
    offpage_sites = set(offpage.tranco) | set(offpage.majestic) | set(offpage.cc_pagerank)
    records = []
    base_spread = []
    for query, query_rows in outcome_rows.groupby("query", sort=True):
        base = _scores(scorer, history, query_rows, pages, offpage)
        values = list(base.values())
        if len(values) > 1:
            base_spread.append(float(np.std(values)))
        ranked = [row for row in query_rows.itertuples(index=False) if row.key in pages]
        serp_title = {row.key: row.title or "" for row in query_rows.itertuples(index=False)}
        for i, high in enumerate(ranked):
            for low in ranked[i + 1 :]:
                if high.position >= low.position:
                    continue
                record = {
                    "query": query, "high": high.key, "low": low.key, "pos_high": int(high.position), "pos_low": int(low.position),
                    "same_site": high.site == low.site, "score_high": base[high.key], "score_low": base[low.key],
                }
                for variant in VARIANTS:
                    l_pages = dict(pages)
                    l_pages[low.key] = _swap(pages[low.key], pages[high.key], variant)
                    h_pages = dict(pages)
                    h_pages[high.key] = _swap(pages[high.key], pages[low.key], variant)
                    l_titles = {low.key: serp_title[high.key]} if variant == "head_body_serp" else None
                    h_titles = {high.key: serp_title[low.key]} if variant == "head_body_serp" else None
                    record[f"{variant}_low_star"] = _scores(scorer, history, query_rows, l_pages, offpage, l_titles)[low.key]
                    record[f"{variant}_high_star"] = _scores(scorer, history, query_rows, h_pages, offpage, h_titles)[high.key]
                records.append(record)
        print(f"  {query}: {len(ranked)} pages, pairs so far {len(records)}", flush=True)
    pairs = pd.DataFrame.from_records(records)
    pairs["plain_correct"] = (pairs["score_high"] > pairs["score_low"] + TIE).astype(int)
    pairs["plain_tie"] = ((pairs["score_high"] - pairs["score_low"]).abs() <= TIE).astype(int)
    summary: dict = {
        "label": "PREDICTED, DIAGNOSTIC ONLY",
        "model_sha256": MODEL_SHA,
        "training_files": verification,
        "a70_queries": len(a70),
        "a70_queries_with_pairs": int(pairs["query"].nunique()),
        "outcome_rows": int(len(outcome_rows)),
        "rows_with_usable_page": int(outcome_rows["key"].isin(set(pages)).sum()),
        "page_sources": pd.Series(page_source).value_counts().to_dict(),
        "outcome_sites_with_offpage_values": f"{int(outcome_rows['site'].isin(offpage_sites).sum())}/{len(outcome_rows)}",
        "pairs": int(len(pairs)),
        "plain_pairwise": M.bootstrap_rate(pairs, "plain_correct"),
        "plain_ties": int(pairs["plain_tie"].sum()),
        "median_within_query_score_std": float(np.median(base_spread)) if base_spread else None,
        "median_abs_score_gap_high_low": float((pairs["score_high"] - pairs["score_low"]).abs().median()),
        "variants": {},
    }
    for variant in VARIANTS:
        low_delta = pairs[f"{variant}_low_star"] - pairs["score_low"]
        high_delta = pairs[f"{variant}_high_star"] - pairs["score_high"]
        frame = pairs.assign(
            l_up=(low_delta > TIE).astype(int),
            l_tie=(low_delta.abs() <= TIE).astype(int),
            h_down=(high_delta < -TIE).astype(int),
            h_tie=(high_delta.abs() <= TIE).astype(int),
            l_beats_h=(pairs[f"{variant}_low_star"] > pairs["score_high"] + TIE).astype(int),
        )
        summary["variants"][variant] = {
            "score_L_star_gt_L": M.bootstrap_rate(frame, "l_up"),
            "score_L_star_eq_L": M.bootstrap_rate(frame, "l_tie"),
            "score_H_star_lt_H": M.bootstrap_rate(frame, "h_down"),
            "score_H_star_eq_H": M.bootstrap_rate(frame, "h_tie"),
            "score_L_star_gt_H": M.bootstrap_rate(frame, "l_beats_h"),
            "median_abs_delta_L": float(low_delta.abs().median()),
            "mean_delta_L": float(low_delta.mean()),
            "mean_delta_H": float(high_delta.mean()),
        }
    summary.update(gain_summary(scorer))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pairs.insert(0, "label", "PREDICTED (diagnostic)")
    pairs.to_csv(OUT_DIR / "counterfactual_pairs.csv", index=False)
    (OUT_DIR / "counterfactual.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=float) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
