"""Freeze Nov 11 predictions before that outcome is opened.

Two windows are written here, and neither one reads a 2026-11-11 file.

- 2026-11-09 to 2026-11-11. LambdaMART is retrained on labels dated on or
  before 2026-11-09. Hyperparameters are the fit_lambdamart defaults.
  Persistence is the 2026-11-09 order. Candidates are the 2026-11-09 URLs.
- 2026-11-06 to 2026-11-11. The model, the page text, and the persistence
  order use only the 2026-11-06 snapshot.

The 2026-11-10 scrape is not a training row and not score-time text. Its
capture date is after the 2026-11-09 cutoff. Scoring the outcome is a
separate function. It does not refit.
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from aeo.ranksim.features import FEATURE_NAMES
from aeo.ranksim.forward_eval import (
    BLEND_CONTENT_WEIGHT,
    BLEND_PERSISTENCE_WEIGHT,
    ENTRANT_BASE_POSITION,
    ENTRANT_SCORE,
    HISTORY_CONTENT_SCALE,
    MISSING_CONTENT_SCORE,
    SCORERS,
    _assign_model_scores,
    _best_rows,
    _blend_inputs,
    _html_sha,
    _key,
    _read_html_manifest,
    _request_window,
    _sha256,
    agreement_on_overlap,
    blend_scores,
    export_ledger,
    history_aware_scores,
    persistence_scores,
    replay_ledger,
)
from aeo.ranksim.groundtruth import SerpLabel, load_serp_file
from aeo.ranksim.lambdamart import RankExample, _lookup_page, build_examples, fit_lambdamart, predict_scores, relevance
from aeo.ranksim.ledger import Call, RsPrediction, record_outcome, record_prediction, score_ledger, verify_ledger
from aeo.ranksim.metrics import mean_metric, ndcg_at_k, spearman
from aeo.ranksim.new_page import _bootstrap_delta
from aeo.ranksim.snapshot_eval import _corpus, _saved_html
from aeo.ranksim.snapshot_protocol import assert_no_rank_leak, pair_counts
from aeo.ranksim.store import open_session
from aeo.ranksim.unknown_content import score_scale, unknown_content_value

TARGET_DAY = "2026-11-11"
NOV6_DAY = "2026-11-06"
NOV9_DAY = "2026-11-09"
NOV6_PREDICTION_AT = datetime(2026, 11, 7, 4, 0, tzinfo=timezone.utc)
NOV9_PREDICTION_AT = datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)
NOV6_FEATURE_TODAY = date(2026, 11, 7)
NOV9_FEATURE_TODAY = date(2026, 11, 10)
WINDOW_NOV9 = "2026-11-09_to_2026-11-11"
WINDOW_NOV6 = "2026-11-06_to_2026-11-11"
ADOPTION_MARGIN = 0.05
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20261111
ELIGIBLE_SCORERS = ("lambdamart", "bm25", "blend")
PRIMARY_METRIC = (
    "All ordered pairs among URLs that are in the pre-registered candidate list "
    "and also in the 2026-11-11 top 10 for the same query. A score tie is not correct. "
    "The denominator is correct plus wrong plus ties."
)

# What fit_lambdamart hard-codes. The freeze calls that function and does not pass overrides.
EXPECTED_HYPERPARAMETERS = {
    "objective": "lambdarank",
    "learning_rate": 0.08,
    "num_leaves": 15,
    "min_data_in_leaf": 1,
    "num_boost_round": 40,
    "monotone_constraints_method": "advanced",
}


def _labels(mapping: dict) -> list[SerpLabel]:
    rows = []
    for query, urls in sorted(mapping.items()):
        for _url_key, (url, position) in sorted(urls.items(), key=lambda item: (item[1][1], item[1][0])):
            rows.append(SerpLabel(query=query, url=url, position=float(position), prior_position=None, source="serper_live"))
    return rows


def _assert_snapshot_day(snapshot: Path, day: str) -> dict:
    window = _request_window(snapshot / "serp_serper_live.csv")
    for value in (window.get("request_start_min"), window.get("request_end_max")):
        if not value or not str(value).startswith(day):
            raise RuntimeError(f"{snapshot} is not the {day} snapshot. Refusing to train on it.")
    return window


def _html_for(snapshot: Path) -> dict[str, str]:
    return _saved_html(list(_read_html_manifest(snapshot)), snapshot)


def _history_from(mapping: dict) -> list[tuple[str, str, float]]:
    rows = []
    for query, urls in mapping.items():
        for _url_key, (url, position) in urls.items():
            rows.append((query, url, float(position)))
    return rows


def _examples_for_day(labels: list[SerpLabel], corpus: dict, today: date, day: str) -> list[RankExample]:
    examples, _uncovered, withheld = build_examples(labels, corpus, today=today)
    if withheld:
        raise RuntimeError("A training prior copied the label position.")
    for example in examples:
        assert_no_rank_leak(example.features)
        example.query = f"{day}\t{example.query}"
    return examples


def _constraint_list(value):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().strip("[]")
        if not text:
            return []
        return [int(float(part)) for part in text.split(",")]
    return [int(part) for part in value]


def _coerce(value):
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return value
        if number.is_integer():
            return int(number)
        return number
    return value


def _model_record(model) -> dict:
    params = {key: _coerce(value) for key, value in dict(model.params).items()}
    record = {
        "objective": params.get("objective"),
        "learning_rate": params.get("learning_rate"),
        "num_leaves": params.get("num_leaves"),
        "min_data_in_leaf": params.get("min_data_in_leaf"),
        "num_boost_round": int(params.get("num_iterations") or 0),
        "fitted_trees": int(model.num_trees()),
        "monotone_constraints_method": params.get("monotone_constraints_method"),
        "feature_names": list(model.feature_name()),
        "monotone_constraints": _constraint_list(params.get("monotone_constraints")),
    }
    for name, expected in EXPECTED_HYPERPARAMETERS.items():
        if record.get(name) != expected:
            raise RuntimeError(f"LambdaMART {name} is {record.get(name)!r}, not the frozen {expected!r}.")
    if record["feature_names"] != list(FEATURE_NAMES):
        raise RuntimeError("LambdaMART features are not FEATURE_NAMES.")
    return record


def _fit(examples: list[RankExample]):
    groups = {row.query for row in examples}
    if len(groups) < 2:
        raise RuntimeError("LambdaMART needs at least two query groups.")
    model = fit_lambdamart(examples, list(FEATURE_NAMES))
    return model, _model_record(model)


def _training_scores(model, examples: list[RankExample]) -> list[float]:
    predicted = predict_scores(model, examples, list(FEATURE_NAMES))
    return [float(predicted[id(example)]) for example in examples]


def _score_query_rows(prepared: list[dict], model, today: date) -> None:
    from aeo.ranksim.features import raw_features

    pages = []
    owners = []
    for row in prepared:
        page = row.get("page")
        if page is None:
            continue
        pages.append(page)
        owners.append(row)
    if pages:
        raw = raw_features(prepared[0]["query"], "commercial", pages, today)
        for row, features in zip(owners, raw):
            assert_no_rank_leak(features)
            row["bm25_value"] = float(features.get("bm25f") or 0.0)
            row["features"] = features
    for row in prepared:
        if "bm25_value" not in row:
            row["bm25_value"] = None
            row["features"] = None
    _assign_model_scores(prepared, model)
    packed = _blend_inputs(prepared)
    for name, values in (
        ("persistence", persistence_scores(prepared)),
        ("blend", blend_scores(packed)),
        ("history_aware", history_aware_scores(packed)),
    ):
        for row, value in zip(prepared, values):
            row[name] = float(value)


def _public_row(row: dict) -> dict:
    scores = {name: float(row[name]) for name in SCORERS}
    return {
        "query": row["query"],
        "url": row["url"],
        "url_key": row["url_key"],
        "candidate_position": row["candidate_position"],
        "html": bool(row["html"]),
        "html_source": row["html_source"],
        "html_sha256": row["html_sha256"],
        "content_missing": int(row.get("content_missing") or 0),
        "scores": scores,
    }


def _assign_ranks(rows: list[dict]) -> None:
    by_query: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_query[row["query"]].append(row)
    for group in by_query.values():
        for name in SCORERS:
            order = sorted(group, key=lambda item: (-item["scores"][name], item["url"]))
            for place, item in enumerate(order, start=1):
                item.setdefault("ranks", {})[name] = place


def _html_index(html_by_url: dict[str, str]) -> dict[str, str]:
    indexed = {}
    for url, body in html_by_url.items():
        indexed[url] = body
        indexed[_key(url)] = body
    return indexed


def _window_rows(
    candidates: dict,
    corpus: dict,
    model,
    examples: list[RankExample],
    history: list[tuple[str, str, float]],
    today: date,
    html_by_url: dict[str, str],
    html_source_for,
) -> tuple[list[dict], dict]:
    mean, std = score_scale(_training_scores(model, examples))
    html_index = _html_index(html_by_url)
    prepared_all: list[dict] = []
    for query in sorted(candidates):
        prepared = []
        for url_key, (url, position) in sorted(candidates[query].items(), key=lambda item: (item[1][1], item[1][0])):
            page = _lookup_page(corpus, url)
            html_text = None if page is None else html_index.get(url) or html_index.get(url_key) or html_index.get(getattr(page, "url", ""))
            source = "missing" if page is None else html_source_for(url_key)
            prepared.append(
                {
                    "query": query,
                    "url": url,
                    "url_key": url_key,
                    "candidate_position": int(position) if float(position).is_integer() else float(position),
                    "baseline_position": float(position),
                    "entrant": False,
                    "html": page is not None,
                    "html_source": source,
                    "html_sha256": _html_sha(html_text),
                    "page": page,
                }
            )
        _score_query_rows(prepared, model, today)
        prepared_all.extend(prepared)
    unknown_content_value(prepared_all, history, signals=None, mean=mean, std=std)
    finite = [row["lambdamart"] for row in prepared_all if row.get("html")]
    if any(value <= MISSING_CONTENT_SCORE for value in finite):
        raise RuntimeError("A page with HTML received the missing-content floor.")
    missing = [row["lambdamart"] for row in prepared_all if not row.get("html")]
    if any(value <= MISSING_CONTENT_SCORE for value in missing):
        raise RuntimeError("A page with no HTML still has the shared missing-content floor.")
    public = [_public_row(row) for row in prepared_all]
    _assign_ranks(public)
    coverage = {
        "candidate_rows": len(public),
        "queries": len(candidates),
        "html_rows": sum(1 for row in public if row["html"]),
        "content_missing_rows": sum(1 for row in public if row["content_missing"]),
        "html_by_source": {
            source: sum(1 for row in public if row["html_source"] == source)
            for source in sorted({row["html_source"] for row in public})
        },
        "unknown_content_mean": mean,
        "unknown_content_std": std,
        "offpage_tables": "not loaded. Majestic, Tranco, Common Crawl, and Open PageRank add nothing.",
    }
    return public, coverage


def _slug(window: str, scorer: str) -> str:
    return f"forward-{TARGET_DAY}-{window}-{scorer}"


def _prediction_stamp(window: str) -> datetime:
    if window == WINDOW_NOV6:
        return NOV6_PREDICTION_AT
    if window == WINDOW_NOV9:
        return NOV9_PREDICTION_AT
    raise ValueError(window)


def freeze_predictions(nov6: Path, nov9: Path) -> dict:
    """Build both prediction windows. Does not take an outcome path."""
    nov6 = Path(nov6)
    nov9 = Path(nov9)
    nov6_window = _assert_snapshot_day(nov6, NOV6_DAY)
    nov9_window = _assert_snapshot_day(nov9, NOV9_DAY)
    nov6_labels = load_serp_file(nov6 / "serp_serper_live.csv")
    nov9_labels = load_serp_file(nov9 / "serp_serper_live.csv")
    nov6_map = _best_rows(nov6_labels)
    nov9_map = _best_rows(nov9_labels)
    nov6_html = _html_for(nov6)
    nov9_html = _html_for(nov9)
    nov6_corpus = _corpus(nov6_html)
    union_html = dict(nov6_html)
    union_html.update(nov9_html)
    nov9_corpus = _corpus(union_html)
    nov6_examples = _examples_for_day(_labels(nov6_map), nov6_corpus, NOV6_FEATURE_TODAY, NOV6_DAY)
    nov9_examples = _examples_for_day(_labels(nov9_map), nov9_corpus, NOV9_FEATURE_TODAY, NOV9_DAY)
    nov6_model, nov6_hyper = _fit(nov6_examples)
    nov9_model, nov9_hyper = _fit(nov6_examples + nov9_examples)
    shared = EXPECTED_HYPERPARAMETERS
    if {key: nov6_hyper[key] for key in shared} != {key: nov9_hyper[key] for key in shared}:
        raise RuntimeError("The two LambdaMART fits did not keep the same hyperparameters.")

    def source_nov6(_url_key: str) -> str:
        return NOV6_DAY

    def source_nov9(url_key: str) -> str:
        if any(_key(url) == url_key for url in nov9_html):
            return NOV9_DAY
        if any(_key(url) == url_key for url in nov6_html):
            return NOV6_DAY
        return "missing"

    nov6_rows, nov6_coverage = _window_rows(
        nov6_map,
        nov6_corpus,
        nov6_model,
        nov6_examples,
        _history_from(nov6_map),
        NOV6_FEATURE_TODAY,
        nov6_html,
        source_nov6,
    )
    nov9_rows, nov9_coverage = _window_rows(
        nov9_map,
        nov9_corpus,
        nov9_model,
        nov6_examples + nov9_examples,
        _history_from(nov6_map) + _history_from(nov9_map),
        NOV9_FEATURE_TODAY,
        union_html,
        source_nov9,
    )
    windows = {
        WINDOW_NOV9: _window_payload(
            baseline_day=NOV9_DAY,
            rows=nov9_rows,
            coverage=nov9_coverage,
            training={"2026-11-06": _training_note(nov6_examples), "2026-11-09": _training_note(nov9_examples)},
            content_rule=(
                "Score-time text is HTML saved on 2026-11-09, otherwise HTML saved on 2026-11-06. "
                "The link graph is the union of those pages, with 2026-11-09 HTML winning when both days saved the URL. "
                "The 2026-11-10 scrape is not used."
            ),
        ),
        WINDOW_NOV6: _window_payload(
            baseline_day=NOV6_DAY,
            rows=nov6_rows,
            coverage=nov6_coverage,
            training={"2026-11-06": _training_note(nov6_examples)},
            content_rule=(
                "Score-time text and the link graph are the 2026-11-06 HTML only. "
                "A URL with no 2026-11-06 HTML is unknown content even if a later snapshot saved it."
            ),
        ),
    }
    return {
        "label": "PREDICTED",
        "outcome_opened": False,
        "target_observed_on": TARGET_DAY,
        "primary_metric": PRIMARY_METRIC,
        "secondary_metrics": ["NDCG@10", "Spearman"],
        "promotion_bar": _promotion_bar_text(),
        "ranker_change": "none",
        "scrape_2026_11_10": "excluded from training and from score-time text",
        "hyperparameters": {key: nov9_hyper[key] for key in EXPECTED_HYPERPARAMETERS},
        "lambdamart_fitted_trees": {
            WINDOW_NOV6: nov6_hyper["fitted_trees"],
            WINDOW_NOV9: nov9_hyper["fitted_trees"],
        },
        "constants": {
            "entrant_score": ENTRANT_SCORE,
            "entrant_base_position": ENTRANT_BASE_POSITION,
            "history_content_scale": HISTORY_CONTENT_SCALE,
            "blend_persistence_weight": BLEND_PERSISTENCE_WEIGHT,
            "blend_content_weight": BLEND_CONTENT_WEIGHT,
            "adoption_margin": ADOPTION_MARGIN,
            "bootstrap_draws": BOOTSTRAP_DRAWS,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "missing_content_floor_not_used": MISSING_CONTENT_SCORE,
        },
        "inputs": {
            NOV6_DAY: {
                "serp_sha256": _sha256(nov6 / "serp_serper_live.csv"),
                "html_saved": len(nov6_html),
                "organic_rows": sum(len(urls) for urls in nov6_map.values()),
                "request_window": nov6_window,
            },
            NOV9_DAY: {
                "serp_sha256": _sha256(nov9 / "serp_serper_live.csv"),
                "html_saved": len(nov9_html),
                "organic_rows": sum(len(urls) for urls in nov9_map.values()),
                "request_window": nov9_window,
            },
        },
        "windows": windows,
    }


def _training_note(examples: list[RankExample]) -> dict:
    return {
        "examples": len(examples),
        "queries": len({example.query.split("\t", 1)[1] for example in examples}),
        "urls": sorted({example.url for example in examples}),
    }


def _window_payload(baseline_day: str, rows: list[dict], coverage: dict, training: dict, content_rule: str) -> dict:
    window = WINDOW_NOV9 if baseline_day == NOV9_DAY else WINDOW_NOV6
    return {
        "label": "PREDICTED",
        "baseline_observed_on": baseline_day,
        "target_observed_on": TARGET_DAY,
        "protocol_prediction_at": _prediction_stamp(window).isoformat(timespec="seconds"),
        "training_cutoff": baseline_day,
        "content_rule": content_rule,
        "training": {day: {key: value for key, value in note.items() if key != "urls"} | {"url_count": len(note["urls"])} for day, note in training.items()},
        "training_urls": {day: note["urls"] for day, note in training.items()},
        "coverage": {key: value for key, value in coverage.items() if key not in {"unknown_content_mean", "unknown_content_std"}},
        "unknown_content": {
            "mean": coverage["unknown_content_mean"],
            "std": coverage["unknown_content_std"],
            "rule": (
                "A page with no HTML gets the mean of this model's training scores, "
                "plus that standard deviation times the average within-query z-score of the off-page parts that exist. "
                "A missing part adds nothing. Domain prior uses positions on or before the training cutoff, "
                "from the other query families only."
            ),
        },
        "rows": rows,
    }


def _promotion_bar_text() -> dict:
    return {
        "comparator": "persistence on the same overlap pairs",
        "margin": ADOPTION_MARGIN,
        "bootstrap": {
            "draws": BOOTSTRAP_DRAWS,
            "seed": BOOTSTRAP_SEED,
            "unit": "query",
            "interval": "2.5 and 97.5 percentile of scorer minus persistence",
        },
        "eligible": list(ELIGIBLE_SCORERS),
        "not_eligible": ["history_aware", "persistence"],
        "rule": (
            "An eligible scorer clears the bar when its primary pairwise is at least persistence plus 0.05, "
            "the bootstrap interval of that gain has its lower end above zero, "
            "and, for lambdamart and blend, it is also at least bm25 on the same pairs. "
            "history_aware is reported and is not eligible. "
            "Clearing the bar does not change the ranker in this run."
        ),
        "applied_in_this_run": False,
    }


def render_preregistration(payload: dict) -> str:
    nov9 = payload["windows"][WINDOW_NOV9]
    nov6 = payload["windows"][WINDOW_NOV6]
    hyper = payload["hyperparameters"]
    lines = [
        "# Pre-registration: 2026-11-11 forward test",
        "",
        "Label: PREDICTED. The 2026-11-11 outcome file was not opened when these rows were written. This note names the metric and the bars before that file is scored.",
        "",
        "## Primary metric",
        "",
        payload["primary_metric"],
        "",
        "Spearman and NDCG@10 on those same URLs are secondary. They are not promotion gates.",
        "",
        "A URL in the 2026-11-11 top 10 that is not in the candidate list is unpredicted. It is not given a score. Pairs that include it are not in the primary denominator. The report states that coverage.",
        "",
        "The surviving-URL subset is the URLs that are in both the candidate list and the 2026-11-11 top 10. For this protocol that subset is the primary pair set.",
        "",
        "The churn baseline is the agreement of the candidate-day order with the 2026-11-11 order on that overlap. It is not a model. A figure supplied before this file was scored will be reproduced or corrected. It is not an input and it is not a target.",
        "",
        "## Promotion bar",
        "",
        payload["promotion_bar"]["rule"],
        "",
        f"Margin {ADOPTION_MARGIN:g}. Bootstrap {BOOTSTRAP_DRAWS} query resamples, seed {BOOTSTRAP_SEED}. `ranker_change` stays `none` in this run even if a scorer clears the bar.",
        "",
        "## Windows",
        "",
        f"### {WINDOW_NOV9}",
        "",
        f"Protocol time {nov9['protocol_prediction_at']}. Candidates: every 2026-11-09 result URL ({nov9['coverage']['candidate_rows']} rows, {nov9['coverage']['queries']} queries). Persistence is the negative of the 2026-11-09 position. LambdaMART is retrained on 2026-11-06 and 2026-11-09 labels. Each day is its own ranking group. Page text for a training row is HTML captured on or before that row's day. {nov9['training']['2026-11-06']['examples']} training rows from 2026-11-06 and {nov9['training']['2026-11-09']['examples']} from 2026-11-09.",
        "",
        nov9["content_rule"],
        "",
        f"### {WINDOW_NOV6}",
        "",
        f"Protocol time {nov6['protocol_prediction_at']}. This is the two-step test, frozen as of 2026-11-06. Candidates: every 2026-11-06 result URL ({nov6['coverage']['candidate_rows']} rows, {nov6['coverage']['queries']} queries). Persistence is the negative of the 2026-11-06 position. LambdaMART is fit only on the {nov6['training']['2026-11-06']['examples']} HTML rows from that day. A 2026-11-09 position is not a label, a feature, or a threshold.",
        "",
        nov6["content_rule"],
        "",
        "## Scorers",
        "",
        "The five scorers are lambdamart, bm25, persistence, blend, and history_aware. Blend weights are "
        f"{BLEND_PERSISTENCE_WEIGHT:g} and {BLEND_CONTENT_WEIGHT:g}. History-aware scale is {HISTORY_CONTENT_SCALE:g}. "
        f"An entrant base position of {ENTRANT_BASE_POSITION:g} and an entrant score of {ENTRANT_SCORE:g} stay in the code. "
        "Every candidate in these files already has a position on the baseline day, so those entrant constants do not move a row inside the candidate list. "
        "A page that first appears on 2026-11-11 is unpredicted rather than scored as an entrant.",
        "",
        "A page with HTML keeps its LambdaMART and BM25 scores. A page with no HTML is unknown content: the training-score mean, shifted by off-page evidence. It is not the shared floor. "
        "Blend and history-aware still treat missing HTML as no content push. They do not use the unknown-content score. That is the rule already on the branch.",
        "",
        "Off-page tables were not loaded. Domain prior and URL overlap are the off-page parts that exist. A missing part adds nothing.",
        "",
        "## Hyperparameters",
        "",
        f"objective {hyper['objective']}, learning_rate {hyper['learning_rate']}, num_leaves {hyper['num_leaves']}, "
        f"min_data_in_leaf {hyper['min_data_in_leaf']}, num_boost_round {hyper['num_boost_round']} requested, "
        f"monotone_constraints_method {hyper['monotone_constraints_method']}. "
        "Features are FEATURE_NAMES. Monotone constraints are the product set. fit_lambdamart is called with no overrides. The thin-page gate inside predict_scores stays on. "
        f"The 2026-11-06 fit kept {payload['lambdamart_fitted_trees'][WINDOW_NOV6]} trees and the 2026-11-09 fit kept {payload['lambdamart_fitted_trees'][WINDOW_NOV9]} trees. "
        "A smaller count means LightGBM stopped when no further split had positive gain. The requested round count was not changed.",
        "",
        "Feature dates are fixed: 2026-11-07 for 2026-11-06 rows, and 2026-11-10 for 2026-11-09 rows and for the 2026-11-09 score time.",
        "",
        "## Ledger",
        "",
        "Prediction rows are PREDICTED and UNSURE. They are appended at the protocol time. The 2026-11-09 ledger file is not edited. "
        "After the outcome is opened, a REAL outcome is appended only for scoring. A call resolves only when both URLs are in the 2026-11-11 top 10. An exit is unresolved, not a win.",
        "",
        "## Not done in this run",
        "",
        "The ranker is not changed from these numbers. The 2026-11-10 scrape stays out. A later snapshot can pre-register score-time text captured on or before the prediction clock. That is a hypothesis, not a change made here.",
        "",
    ]
    return "\n".join(lines)


def write_prediction_ledger(session, payload: dict) -> dict:
    """Append PREDICTED rows only. No outcome rows."""
    summary = {}
    for window, block in payload["windows"].items():
        by_query: dict[str, list[dict]] = defaultdict(list)
        for row in block["rows"]:
            by_query[row["query"]].append(row)
        stamp = _prediction_stamp(window)
        for scorer in SCORERS:
            slug = _slug(window, scorer)
            for query in sorted(by_query):
                rows = by_query[query]
                candidates = [
                    {
                        "url": item["url"],
                        "html_sha256": item["html_sha256"],
                        "labeled_position": int(item["candidate_position"]),
                    }
                    for item in sorted(rows, key=lambda item: item["url"])
                ]
                for subject in sorted(rows, key=lambda item: item["url"]):
                    calls = []
                    for other in rows:
                        if other["url"] == subject["url"]:
                            continue
                        gap = float(subject["scores"][scorer] - other["scores"][scorer])
                        if gap <= 0:
                            continue
                        calls.append(
                            Call(
                                competitor_url=other["url"],
                                predicted="above",
                                score_gap=gap,
                                confidence="UNSURE",
                                note="PREDICTED. Not a Google rank.",
                            )
                        )
                    record_prediction(
                        session,
                        kind="forward_order",
                        query=query,
                        subject_url=subject["url"],
                        subject_html=None,
                        predicted_rank=int(subject["ranks"][scorer]),
                        predicted_of=len(rows),
                        candidates=candidates,
                        calls=calls,
                        confidence="UNSURE",
                        calibration_note=(
                            "PREDICTED and UNSURE. Frozen before the 2026-11-11 snapshot was opened. "
                            "That order was not used to train, to pick features, or to set this label."
                        ),
                        provenance={
                            "dataset": "serper_live",
                            "label": "PREDICTED",
                            "scorer": scorer,
                            "window": window,
                            "baseline_observed_on": block["baseline_observed_on"],
                            "target_observed_on": TARGET_DAY,
                            "training_cutoff": block["training_cutoff"],
                            "timing": "protocol freeze before the 2026-11-11 observation",
                            "created_at_meaning": "Protocol time, not the wall clock of a later scoring run.",
                            "uncertainty": "UNSURE",
                            "html_source": subject["html_source"],
                            "content_missing": subject["content_missing"],
                        },
                        created_at=stamp,
                        project_slug=slug,
                    )
            scored = score_ledger(session, project_slug=slug)
            summary[slug] = {
                "predictions": scored.predictions,
                "calls": scored.calls,
                "resolved": scored.resolved,
                "correct": scored.correct,
                "no_outcome_yet": scored.no_outcome_yet,
                "unresolved": scored.unresolved,
            }
            if scored.resolved or scored.correct:
                raise RuntimeError("A prediction resolved before the outcome was opened.")
    ok, note = verify_ledger(session)
    if not ok:
        raise RuntimeError(note)
    summary["chain"] = note
    return summary


def _outcome_map(path: Path) -> dict:
    return _best_rows(load_serp_file(path))


def _per_query(rows: list[dict], outcome: dict, scorer: str) -> dict[str, tuple[int, int, int]]:
    found = {}
    for query, group in _by_query(rows).items():
        later = outcome.get(query, {})
        shared = [row for row in group if row["url_key"] in later]
        if len(shared) < 2:
            continue
        positions = [float(later[row["url_key"]][1]) for row in shared]
        scores = [float(row["scores"][scorer]) for row in shared]
        counts = pair_counts(positions, scores)
        if sum(counts) == 0:
            continue
        found[query] = counts
    return found


def _by_query(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["query"]].append(row)
    return grouped


def _rate_block(per_query: dict[str, tuple[int, int, int]]) -> dict:
    correct = sum(item[0] for item in per_query.values())
    wrong = sum(item[1] for item in per_query.values())
    ties = sum(item[2] for item in per_query.values())
    total = correct + wrong + ties
    return {
        "queries": len(per_query),
        "correct": correct,
        "wrong": wrong,
        "ties": ties,
        "pairs": total,
        "pairwise": None if total == 0 else round(correct / total, 6),
    }


def _list_metrics(rows: list[dict], outcome: dict, scorer: str) -> dict:
    ndcgs = []
    rhos = []
    for query, group in _by_query(rows).items():
        later = outcome.get(query, {})
        shared = [row for row in group if row["url_key"] in later]
        if len(shared) < 2:
            continue
        order = sorted(range(len(shared)), key=lambda index: (-shared[index]["scores"][scorer], shared[index]["url"]))
        gains = {str(index): relevance(later[shared[index]["url_key"]][1]) for index in range(len(shared))}
        ndcgs.append(ndcg_at_k([str(index) for index in order], gains, k=10))
        rhos.append(spearman([row["scores"][scorer] for row in shared], [float(later[row["url_key"]][1]) for row in shared]))
    ndcg = mean_metric(ndcgs)
    rho = mean_metric(rhos)
    return {
        "ndcg_at_10": None if ndcg is None else round(ndcg, 6),
        "ndcg_queries": sum(1 for value in ndcgs if value is not None),
        "spearman": None if rho is None else round(rho, 6),
        "spearman_queries": sum(1 for value in rhos if value is not None),
    }


def _coverage(rows: list[dict], outcome: dict) -> dict:
    candidate_rows = len(rows)
    outcome_rows = sum(len(urls) for urls in outcome.values())
    predicted_keys = {(row["query"], row["url_key"]) for row in rows}
    unpredicted_rows = 0
    for query, urls in outcome.items():
        for url_key in urls:
            if (query, url_key) not in predicted_keys:
                unpredicted_rows += 1
    overlap_rows = sum(1 for row in rows if row["url_key"] in outcome.get(row["query"], {}))
    scored_pairs = 0
    outcome_pairs = 0
    for query, urls in outcome.items():
        positions = [float(item[1]) for item in urls.values()]
        outcome_pairs += pair_counts(positions, [-position for position in positions])[0]
        # pair_counts with scores = -position counts every differing pair as correct, so the
        # returned correct count is the number of ordered pairs. Wrong and ties are zero.
        shared_positions = []
        for row in rows:
            if row["query"] != query or row["url_key"] not in urls:
                continue
            shared_positions.append(float(urls[row["url_key"]][1]))
        scored_pairs += pair_counts(shared_positions, [-position for position in shared_positions])[0]
    return {
        "candidate_rows": candidate_rows,
        "candidate_rows_in_outcome": overlap_rows,
        "outcome_rows": outcome_rows,
        "unpredicted_outcome_rows": unpredicted_rows,
        "exits": candidate_rows - overlap_rows,
        "outcome_pairs": outcome_pairs,
        "scored_pairs": scored_pairs,
        "unpredicted_outcome_pairs": outcome_pairs - scored_pairs,
        "pair_coverage": None if outcome_pairs == 0 else round(scored_pairs / outcome_pairs, 6),
        "row_coverage": None if outcome_rows == 0 else round((outcome_rows - unpredicted_rows) / outcome_rows, 6),
    }


def _churn(rows: list[dict], outcome: dict) -> dict:
    baseline = [(row["query"], row["url"], float(row["candidate_position"])) for row in rows]
    forward = []
    for query, urls in outcome.items():
        for url, position in ((item[0], item[1]) for item in urls.values()):
            forward.append((query, url, float(position)))
    report = agreement_on_overlap(baseline, forward)
    report["note"] = "Agreement of the candidate-day order with the outcome on URLs present both days. Not a model."
    return report


def _decision(per_query: dict[str, dict[str, tuple[int, int, int]]]) -> dict:
    details = {}
    any_clear = False
    for offset, name in enumerate(SCORERS):
        block = _rate_block(per_query[name])
        base = _rate_block(per_query["persistence"])
        bm25 = _rate_block(per_query["bm25"])
        point = None if block["pairs"] == 0 else block["correct"] / block["pairs"]
        base_point = None if base["pairs"] == 0 else base["correct"] / base["pairs"]
        bm25_point = None if bm25["pairs"] == 0 else bm25["correct"] / bm25["pairs"]
        interval = _bootstrap_delta(per_query[name], per_query["persistence"], BOOTSTRAP_DRAWS, random.Random(BOOTSTRAP_SEED + offset))
        eligible = name in ELIGIBLE_SCORERS
        reasons = []
        clears = False
        if not eligible:
            reasons.append(f"{name} is not eligible.")
        elif point is None or base_point is None:
            reasons.append(f"{name} has no overlap pairs.")
        else:
            if point + 1e-12 < base_point + ADOPTION_MARGIN:
                reasons.append(f"{name} {point:.6f} does not clear persistence {base_point:.6f} plus {ADOPTION_MARGIN:.2f}.")
            if interval[0] is None or interval[0] <= 0:
                reasons.append(f"{name} gain interval is {interval}.")
            if name != "bm25" and (bm25_point is None or point + 1e-12 < bm25_point):
                reasons.append(f"{name} is below bm25.")
            clears = not reasons
        if clears:
            any_clear = True
            reasons.append("Clears the pre-registered bar. The ranker is still not changed in this run.")
        details[name] = {
            "eligible": eligible,
            "clears_bar": clears,
            "applied": False,
            "pairwise": block["pairwise"],
            "persistence": base["pairwise"],
            "bm25": bm25["pairwise"],
            "delta_vs_persistence_ci": [None if value is None else round(value, 6) for value in interval],
            "reasons": reasons,
        }
    return {"ranker_change": "none", "any_scorer_clears_bar": any_clear, "scorers": details}


def score_predictions(payload: dict, outcome_serp: Path) -> dict:
    """Score a frozen prediction file. Does not fit a model and does not rewrite the file."""
    if payload.get("label") != "PREDICTED":
        raise RuntimeError("Prediction file is not labeled PREDICTED.")
    if payload.get("outcome_opened") is not False:
        raise RuntimeError("Prediction file does not say the outcome was still closed.")
    outcome_serp = Path(outcome_serp)
    window_check = _assert_snapshot_day(outcome_serp.parent, TARGET_DAY)
    outcome = _outcome_map(outcome_serp)
    windows = {}
    for name, block in payload["windows"].items():
        rows = block["rows"]
        per_query = {scorer: _per_query(rows, outcome, scorer) for scorer in SCORERS}
        scorers = {}
        for scorer in SCORERS:
            scorers[scorer] = {
                "label": "PREDICTED",
                "pairwise": _rate_block(per_query[scorer]),
                **_list_metrics(rows, outcome, scorer),
            }
        coverage = _coverage(rows, outcome)
        windows[name] = {
            "label": "PREDICTED",
            "baseline_observed_on": block["baseline_observed_on"],
            "target_observed_on": TARGET_DAY,
            "protocol_prediction_at": block["protocol_prediction_at"],
            "primary_metric": PRIMARY_METRIC,
            "surviving_url_subset": {
                "meaning": "URLs in the candidate list and in the outcome top 10. This is the primary pair set.",
                "rows": coverage["candidate_rows_in_outcome"],
                "pairs": coverage["scored_pairs"],
            },
            "coverage": coverage,
            "churn_baseline": _churn(rows, outcome),
            "scorers": scorers,
            "promotion": _decision(per_query),
            "ranker_change": "none",
        }
    unique_urls = {_key(url) for urls in outcome.values() for url, _position in ((item[0], item[1]) for item in urls.values())}
    return {
        "label": "PREDICTED",
        "outcome_observed_on": TARGET_DAY,
        "outcome_serp_sha256": _sha256(outcome_serp),
        "outcome_window": window_check,
        "outcome_rows": sum(len(urls) for urls in outcome.values()),
        "outcome_unique_urls": len(unique_urls),
        "primary_metric": PRIMARY_METRIC,
        "ranker_change": "none",
        "windows": windows,
    }


def append_outcomes(session, payload: dict, outcome_serp: Path, uploaded_at: str) -> dict:
    """Append REAL outcome rows. Prediction rows already in the session stay as they are."""
    outcome_serp = Path(outcome_serp)
    _assert_snapshot_day(outcome_serp.parent, TARGET_DAY)
    mapping = _outcome_map(outcome_serp)
    digest = _sha256(outcome_serp)
    before = [row.row_hash for row in session.query(RsPrediction).order_by(RsPrediction.id)]
    for window, block in payload["windows"].items():
        queries = sorted({row["query"] for row in block["rows"]})
        for scorer in SCORERS:
            slug = _slug(window, scorer)
            for query in queries:
                urls = mapping.get(query, {})
                record_outcome(
                    session,
                    query=query,
                    observed_on=TARGET_DAY,
                    provider="serper",
                    positions={url: int(position) for url, position in ((item[0], item[1]) for item in urls.values())},
                    depth=None,
                    notes=(
                        "Serper organic results, gl=us, hl=en, num=10, observed on 2026-11-11. "
                        "REAL outcome. Not a model score. A call resolves only when both URLs are in this list."
                    ),
                    project_slug=slug,
                    source_file=outcome_serp.name,
                    source_sha256=digest,
                    uploaded_at=uploaded_at,
                )
    after_rows = session.query(RsPrediction).order_by(RsPrediction.id).all()
    after = [row.row_hash for row in after_rows]
    if after != before:
        raise RuntimeError("Appending outcomes changed a prediction hash.")
    ok, note = verify_ledger(session)
    if not ok:
        raise RuntimeError(note)
    summary = {"chain": note}
    for window in payload["windows"]:
        for scorer in SCORERS:
            slug = _slug(window, scorer)
            scored = score_ledger(session, project_slug=slug)
            summary[slug] = {
                "predictions": scored.predictions,
                "calls": scored.calls,
                "resolved": scored.resolved,
                "correct": scored.correct,
                "unresolved": scored.unresolved,
                "no_outcome_yet": scored.no_outcome_yet,
                "accuracy": None if scored.accuracy is None else round(scored.accuracy, 6),
            }
    return summary


def _ledger_session():
    """A private database. open_session caches engines, so sqlite:// would be shared."""
    folder = tempfile.mkdtemp(prefix="ranksim-ledger-")
    return open_session(f"sqlite:///{folder}/ledger.db")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_freeze_dir(nov6: Path, nov9: Path, out_dir: Path) -> dict:
    payload = freeze_predictions(nov6, nov9)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "predictions.json", payload)
    (out_dir / "preregistration.md").write_text(render_preregistration(payload), encoding="utf-8")
    session = _ledger_session()
    try:
        ledger_summary = write_prediction_ledger(session, payload)
        exported = export_ledger(session)
        exported["scores"] = ledger_summary
        _write_json(out_dir / "ledger.json", exported)
    finally:
        session.close()
    manifest = {
        "label": "PREDICTED",
        "outcome_opened": False,
        "purpose": "Frozen predictions for 2026-11-11. The outcome snapshot is not in this directory.",
        "predictions": "predictions.json",
        "preregistration": "preregistration.md",
        "ledger": "ledger.json",
        "inputs": payload["inputs"],
        "html_in_git": False,
    }
    _write_json(out_dir / "manifest.json", manifest)
    return payload


def write_score_dir(predictions_path: Path, outcome_snapshot: Path, ledger_path: Path, out_path: Path) -> dict:
    payload = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
    report = score_predictions(payload, Path(outcome_snapshot) / "serp_serper_live.csv")
    exported = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    prediction_hashes = [row["row_hash"] for row in exported["predictions"]]
    session = _ledger_session()
    try:
        replay_ledger(session, exported)
        uploaded = datetime.now(timezone.utc).isoformat(timespec="seconds")
        summary = append_outcomes(session, payload, Path(outcome_snapshot) / "serp_serper_live.csv", uploaded)
        updated = export_ledger(session)
        if [row["row_hash"] for row in updated["predictions"]] != prediction_hashes:
            raise RuntimeError("Ledger export changed a prediction row.")
        updated["scores"] = summary
        _write_json(Path(ledger_path), updated)
    finally:
        session.close()
    report["ledger"] = summary
    report["ledger_json"] = str(ledger_path)
    _write_json(Path(out_path), report)
    return report


def main_freeze(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze 2026-11-11 predictions. Does not read that snapshot.")
    parser.add_argument("--nov6", required=True)
    parser.add_argument("--nov9", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    payload = write_freeze_dir(Path(args.nov6), Path(args.nov9), Path(args.out_dir))
    print(json.dumps({name: block["coverage"] for name, block in payload["windows"].items()}, indent=2))
    return 0


def main_score(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a frozen 2026-11-11 prediction file. Does not refit.")
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--outcome", required=True, help="2026-11-11 snapshot directory.")
    parser.add_argument("--ledger-json", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = write_score_dir(Path(args.predictions), Path(args.outcome), Path(args.ledger_json), Path(args.out))
    printable = {}
    for name, block in report["windows"].items():
        printable[name] = {
            scorer: {
                "pairwise": block["scorers"][scorer]["pairwise"]["pairwise"],
                "correct": block["scorers"][scorer]["pairwise"]["correct"],
                "pairs": block["scorers"][scorer]["pairwise"]["pairs"],
                "ndcg_at_10": block["scorers"][scorer]["ndcg_at_10"],
                "spearman": block["scorers"][scorer]["spearman"],
            }
            for scorer in SCORERS
        }
        printable[name]["churn"] = block["churn_baseline"]["pairwise"]
        printable[name]["survived_rows"] = block["coverage"]["candidate_rows_in_outcome"]
        printable[name]["unpredicted_rows"] = block["coverage"]["unpredicted_outcome_rows"]
    print(json.dumps(printable, indent=2))
    return 0
