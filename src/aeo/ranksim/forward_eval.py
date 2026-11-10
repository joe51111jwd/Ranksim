"""Forward test: freeze on the 2026-11-06 snapshot, then score the 2026-11-09 order.

The rules below are the protocol. They are not revised after the 2026-11-09
labels are scored. Those labels are the outcome. They are not training rows,
not a feature search, and not a threshold.

This is one interval of about 2.5 days on 30 queries. It is not a general
forecast of Google.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from aeo.crawl.urls import normalize_url
from aeo.ranksim.features import FEATURE_NAMES
from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.lambdamart import RankExample, _lookup_page, build_examples, fit_lambdamart, predict_scores, relevance
from aeo.ranksim.ledger import Call, record_outcome, record_prediction, score_ledger, verify_ledger
from aeo.ranksim.loop import page_from_html
from aeo.ranksim.metrics import mean_metric, ndcg_at_k, spearman
from aeo.ranksim.snapshot_eval import TODAY as FEATURE_TODAY
from aeo.ranksim.snapshot_eval import _corpus, _saved_html
from aeo.ranksim.snapshot_protocol import assert_no_rank_leak, pair_counts
from aeo.ranksim.store import open_session
from aeo.ranksim.unknown_content import score_scale, unknown_content_value

BASELINE_DAY = "2026-11-06"
FORWARD_DAY = "2026-11-09"
# Nov 6 HTML fetch ended 22:16 EST, which is 03:16 UTC on Nov 7. 04:00 UTC is
# after that close. Adding the ledger's 14 local-hour buffer still lands on
# 2026-11-07, so an outcome dated 2026-11-09 is a later observation.
PREDICTION_CREATED_AT = datetime(2026, 11, 7, 4, 0, tzinfo=timezone.utc)
# Persistence: a URL with no position on this query on Nov 6 ties with every
# other such URL, strictly below every URL that was in that query's top 10.
# -1000 is outside the collected depth of 10. It is not a fitted cutoff.
ENTRANT_SCORE = -1000.0
# History-aware and the blend place an entrant just outside the collected top
# 10 (position 11) before content is allowed to move it. Fixed, not fit.
ENTRANT_BASE_POSITION = 11.0
# One within-query BM25 standard deviation moves the history score by one
# position. Fixed before the Nov 9 labels were scored. Not adopted: there is
# no split that can test a prior position without those labels.
HISTORY_CONTENT_SCALE = 1.0
BLEND_PERSISTENCE_WEIGHT = 1.0
BLEND_CONTENT_WEIGHT = 1.0
# No stored HTML. Tied with every other missing page and below any real
# BM25 or LambdaMART score this model produces. Not chosen to lift accuracy.
MISSING_CONTENT_SCORE = -1.0e9
SCORERS = ("lambdamart", "bm25", "persistence", "blend", "history_aware")
HISTORY_ADOPTION_REASON = (
    "No fair split exists that scores a prior position without using the 2026-11-09 labels. "
    "Family-out on 2026-11-06 would train on the same positions it would call history. "
    "The rule was pre-registered and is scored once. It is not adopted."
)


@dataclass
class ForwardEval:
    training_examples: list[RankExample]
    scored_rows: list[dict]
    report: dict
    forward_serp_sha256: str
    forward_serp_name: str = "serp_serper_live.csv"


def persistence_scores(rows: list[dict]) -> list[float]:
    """Last observed position. Unseen URLs on this query share ENTRANT_SCORE."""
    scores = []
    for row in rows:
        position = row.get("baseline_position")
        if position is None:
            scores.append(ENTRANT_SCORE)
        else:
            scores.append(-float(position))
    return scores


def _content_values(rows: list[dict]) -> list[float | None]:
    values: list[float | None] = []
    for row in rows:
        if not row.get("html"):
            values.append(None)
        else:
            value = row.get("bm25")
            values.append(None if value is None else float(value))
    return values


def _population_z(values: list[float | None]) -> list[float]:
    """Z-score of the present numbers. A missing number adds no push (z = 0)."""
    present = [value for value in values if value is not None]
    if len(present) < 2:
        return [0.0 for _ in values]
    mean = sum(present) / len(present)
    variance = sum((value - mean) ** 2 for value in present) / len(present)
    std = math.sqrt(variance)
    if std == 0.0:
        return [0.0 for _ in values]
    return [0.0 if value is None else (value - mean) / std for value in values]


def _history_base(row: dict) -> float:
    position = row.get("baseline_position")
    if position is None:
        return -float(ENTRANT_BASE_POSITION)
    return -float(position)


def history_aware_scores(rows: list[dict]) -> list[float]:
    """Prior position, plus content only as a fixed-size move. Entrants start at position 11."""
    content_z = _population_z(_content_values(rows))
    return [_history_base(row) + HISTORY_CONTENT_SCALE * zed for row, zed in zip(rows, content_z)]


def blend_scores(rows: list[dict]) -> list[float]:
    """Equal weight on the z-scored position base and the z-scored BM25. Weights are not fit."""
    base_z = _population_z([_history_base(row) for row in rows])
    content_z = _population_z(_content_values(rows))
    return [
        BLEND_PERSISTENCE_WEIGHT * left + BLEND_CONTENT_WEIGHT * right
        for left, right in zip(base_z, content_z)
    ]


def _key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url.strip()


def _best_rows(labels) -> dict[str, dict[str, tuple[str, float]]]:
    """query -> normalized url -> (original url, best position)."""
    grouped: dict[str, dict[str, tuple[str, float]]] = defaultdict(dict)
    for label in labels:
        query = " ".join(label.query.split())
        url = label.url.strip()
        key = _key(url)
        current = grouped[query].get(key)
        if current is None or label.position < current[1]:
            grouped[query][key] = (url, float(label.position))
    return grouped


def agreement_on_overlap(
    baseline: list[tuple[str, str, float]],
    forward: list[tuple[str, str, float]],
) -> dict:
    """Order agreement on URLs present for the same query on both days.

    Entrants and exits are not in the pairs. This describes the two snapshots.
    It is not a model score.
    """
    left = _best_rows_from_tuples(baseline)
    right = _best_rows_from_tuples(forward)
    baseline_rows = sum(len(urls) for urls in left.values())
    reappeared = 0
    correct = wrong = ties = 0
    rhos: list[float | None] = []
    usable = 0
    for query, urls in left.items():
        other = right.get(query, {})
        shared = sorted(set(urls) & set(other))
        reappeared += len(shared)
        if len(shared) < 2:
            continue
        positions = [urls[key][1] for key in shared]
        later = [other[key][1] for key in shared]
        scores = [-position for position in positions]
        got, bad, tied = pair_counts(later, scores)
        # pair_counts treats the first list as the label positions and the
        # second as scores (higher is better). Passing later positions and
        # negative baseline positions asks whether baseline order matches later order.
        correct += got
        wrong += bad
        ties += tied
        usable += 1
        rhos.append(spearman(scores, later))
    total = correct + wrong + ties
    defined = [value for value in rhos if value is not None]
    return {
        "baseline_rows": baseline_rows,
        "rows_reappeared": reappeared,
        "queries_with_two_shared_urls": usable,
        "pairs": total,
        "agree": correct,
        "wrong": wrong,
        "ties": ties,
        "pairwise": None if total == 0 else round(correct / total, 6),
        "mean_spearman": None if not defined else round(sum(defined) / len(defined), 6),
        "spearman_queries": len(defined),
        "meaning": (
            "Agreement of Nov 6 order with Nov 9 order on URLs that stayed in the same query. "
            "Entrants and exits are ignored. Not a model."
        ),
    }


def _best_rows_from_tuples(rows: list[tuple[str, str, float]]) -> dict[str, dict[str, tuple[str, float]]]:
    grouped: dict[str, dict[str, tuple[str, float]]] = defaultdict(dict)
    for query, url, position in rows:
        key = _key(url)
        name = " ".join(query.split())
        current = grouped[name].get(key)
        if current is None or position < current[1]:
            grouped[name][key] = (url.strip(), float(position))
    return grouped


def _request_window(path: Path) -> dict:
    import csv

    starts: list[str] = []
    ends: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("request_start"):
                starts.append(row["request_start"])
            if row.get("request_end"):
                ends.append(row["request_end"])
    return {
        "request_start_min": min(starts) if starts else None,
        "request_end_max": max(ends) if ends else None,
        "params": {"gl": "us", "hl": "en", "num": 10},
        "provider": "serper",
    }


def persistence_from_positions(
    baseline: list[tuple[str, str, float]],
    forward: list[tuple[str, str, float]],
) -> dict:
    """Persistence metrics from position lists only. No page text and no Nov 9 training."""
    left = _best_rows_from_tuples(baseline)
    right = _best_rows_from_tuples(forward)
    queries: dict[str, list[dict]] = {}
    for query, urls in right.items():
        rows = []
        for key, (url, position) in urls.items():
            prior = left.get(query, {}).get(key)
            rows.append(
                {
                    "url": url,
                    "position": position,
                    "baseline_position": None if prior is None else prior[1],
                    "entrant": prior is None,
                    "html_source": "baseline",
                }
            )
        scores = persistence_scores(rows)
        for row, score in zip(rows, scores):
            row["persistence"] = score
        queries[query] = rows
    return _rank_metrics(queries, "persistence")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _html_sha(text: str | None) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _slice_pairs(queries: dict[str, list[dict]], score_key: str, mode: str) -> dict:
    correct = wrong = ties = 0
    used = 0
    per_query: list[float] = []
    for rows in queries.values():
        got = bad = tied = 0
        for left in range(len(rows)):
            for right in range(len(rows)):
                if rows[left]["position"] >= rows[right]["position"]:
                    continue
                if not _pair_in_slice(rows[left], rows[right], mode):
                    continue
                left_score = rows[left][score_key]
                right_score = rows[right][score_key]
                if left_score > right_score:
                    got += 1
                elif left_score < right_score:
                    bad += 1
                else:
                    tied += 1
        total = got + bad + tied
        if total == 0:
            continue
        used += 1
        correct += got
        wrong += bad
        ties += tied
        per_query.append(got / total)
    total = correct + wrong + ties
    return {
        "queries": used,
        "pairs": total,
        "correct": correct,
        "wrong": wrong,
        "ties": ties,
        "pairwise": None if total == 0 else round(correct / total, 6),
    }


def _pair_in_slice(left: dict, right: dict, mode: str) -> bool:
    if mode == "all":
        return True
    left_new = bool(left["entrant"])
    right_new = bool(right["entrant"])
    if mode == "returning_only":
        return not left_new and not right_new
    if mode == "entrant_vs_entrant":
        return left_new and right_new
    if mode == "entrant_vs_returning":
        return left_new != right_new
    if mode == "draft_vs_returning":
        return _is_draft(left) != _is_draft(right) and (not left_new or not right_new)
    if mode == "draft_vs_draft":
        return _is_draft(left) and _is_draft(right)
    raise ValueError(mode)


def _is_draft(row: dict) -> bool:
    """No position on this query on Nov 6, and the Nov 6 crawl did not store the page.

    That is the situation of a user's draft: persistence has no history, and the
    content model has only the text the user supplies (or nothing, if the fetch failed).
    """
    return bool(row["entrant"]) and row.get("html_source") != "baseline"


def _rank_metrics(queries: dict[str, list[dict]], score_key: str) -> dict:
    pairwise = _slice_pairs(queries, score_key, "all")
    ndcgs: list[float | None] = []
    rhos: list[float | None] = []
    for rows in queries.values():
        if len(rows) < 2:
            continue
        order = sorted(range(len(rows)), key=lambda index: (-rows[index][score_key], rows[index]["url"]))
        gains = {str(index): relevance(rows[index]["position"]) for index in range(len(rows))}
        ndcgs.append(ndcg_at_k([str(index) for index in order], gains, k=10))
        rhos.append(spearman([row[score_key] for row in rows], [row["position"] for row in rows]))
    ndcg = mean_metric(ndcgs)
    rho = mean_metric(rhos)
    return {
        "pairwise": pairwise,
        "returning_only": _slice_pairs(queries, score_key, "returning_only"),
        "entrant_vs_returning": _slice_pairs(queries, score_key, "entrant_vs_returning"),
        "entrant_vs_entrant": _slice_pairs(queries, score_key, "entrant_vs_entrant"),
        "draft_vs_returning": _slice_pairs(queries, score_key, "draft_vs_returning"),
        "draft_vs_draft": _slice_pairs(queries, score_key, "draft_vs_draft"),
        "ndcg_at_10": None if ndcg is None else round(ndcg, 6),
        "spearman": None if rho is None else round(rho, 6),
        "spearman_queries": sum(1 for value in rhos if value is not None),
        "ndcg_queries": sum(1 for value in ndcgs if value is not None),
    }


def _rules() -> dict:
    return {
        "training": (
            "LambdaMART is fit on every 2026-11-06 query with stored HTML, using FEATURE_NAMES only. "
            "The link graph is the 2026-11-06 saved pages. 2026-11-09 positions are not labels, features, or thresholds."
        ),
        "candidate_set": (
            "The candidate set is the 2026-11-09 organic list for that query. "
            "This scores order among pages that were on the later top list. It does not score whether a page enters the list."
        ),
        "html": (
            "A URL stored on 2026-11-06 uses that HTML and that link graph. "
            "A URL with no 2026-11-06 HTML uses the 2026-11-09 fetch as the page text a user would paste, and pagerank 0. "
            "A URL with neither uses recovered HTML when a later polite fetch saved it. "
            "Recovered HTML is not added to the Nov 6 training rows. "
            "A URL that still has no HTML is unknown content, not a shared low score."
        ),
        "persistence": (
            f"Score is the negative of the 2026-11-06 position on the same query. "
            f"A URL absent from that query scores {ENTRANT_SCORE:g}, tied with every other absent URL and below every URL that was present."
        ),
        "bm25": (
            "Score is bm25f on the HTML rule above. No HTML is unknown content: "
            "the score is the Nov 6 LambdaMART training-score mean, shifted by the "
            "average within-query z-score of the off-page parts that are present. "
            f"It is not the shared floor {MISSING_CONTENT_SCORE:g}."
        ),
        "lambdamart": (
            "The frozen Nov 6 model scores pages that have HTML. "
            "No HTML uses the same unknown-content score as BM25. "
            "The domain prior is fit on the other query families' Nov 6 rows only. "
            "Recovered HTML is score-time text only and is not a training row."
        ),
        "blend": (
            f"Within a query, z-score the position base (observed position, or {ENTRANT_BASE_POSITION:g} for an entrant) "
            f"and z-score bm25f (missing HTML adds 0). "
            f"Score is {BLEND_PERSISTENCE_WEIGHT:g} * position z + {BLEND_CONTENT_WEIGHT:g} * bm25 z. Weights are not fit."
        ),
        "history_aware": (
            f"Score is -(prior position) + {HISTORY_CONTENT_SCALE:g} * bm25 z. "
            f"An entrant uses prior position {ENTRANT_BASE_POSITION:g}. Missing HTML adds 0. "
            "The scale is pre-registered. It is not adopted."
        ),
        "uncertainty": "Every ledger call is PREDICTED and UNSURE. The same-snapshot content order was near a coin flip, and this interval is the first forward look.",
        "limit": "One interval, 2026-11-06 to 2026-11-09, about 2.5 days, the fixed 30-query list. Not a forecast beyond that.",
    }


def evaluate_snapshot_pair(
    baseline: Path,
    forward: Path,
    recovered: dict[str, tuple[str, str]] | None = None,
    offpage_signals: dict[str, dict[str, float | None]] | None = None,
) -> ForwardEval:
    baseline = Path(baseline)
    forward = Path(forward)
    baseline_labels = load_serp_file(baseline / "serp_serper_live.csv")
    forward_labels = load_serp_file(forward / "serp_serper_live.csv")
    baseline_map = _best_rows(baseline_labels)
    forward_map = _best_rows(forward_labels)
    baseline_html = _saved_html(list(_read_html_manifest(baseline)), baseline)
    forward_html = _saved_html(list(_read_html_manifest(forward)), forward)
    corpus = _corpus(baseline_html)
    train_labels = [label for label in baseline_labels if _lookup_page(corpus, label.url) is not None]
    training, _missing, _withheld = build_examples(train_labels, corpus, today=FEATURE_TODAY)
    for example in training:
        assert_no_rank_leak(example.features)
    model = fit_lambdamart(training, list(FEATURE_NAMES)) if len({row.query for row in training}) >= 2 else None
    train_scores: list[float] = []
    if model is not None and training:
        predicted_train = predict_scores(model, training, list(FEATURE_NAMES))
        train_scores = [float(predicted_train[id(example)]) for example in training]
    score_mean, score_std = score_scale(train_scores)
    history = [(" ".join(label.query.split()), label.url, float(label.position)) for label in baseline_labels]
    recovered_index: dict[str, tuple[str, str]] = {}
    for raw_url, payload in (recovered or {}).items():
        body, method = payload
        label = str(method or "").strip() or "http"
        if not label.startswith("recovered_"):
            label = f"recovered_{label}"
        recovered_index[_key(raw_url)] = (body, label)

    scored: list[dict] = []
    by_query: dict[str, list[dict]] = defaultdict(list)
    for query in sorted(forward_map):
        prepared = []
        pages = []
        owners = []
        for key, (url, position) in sorted(forward_map[query].items(), key=lambda item: (item[1][1], item[1][0])):
            prior = baseline_map.get(query, {}).get(key)
            page = _lookup_page(corpus, url)
            if page is not None:
                html_source = "baseline"
                html_text = baseline_html.get(page.url) or baseline_html.get(url)
            elif url in forward_html or key in {_key(item) for item in forward_html}:
                html_text = forward_html.get(url)
                if html_text is None:
                    html_text = next(body for raw, body in forward_html.items() if _key(raw) == key)
                page = page_from_html(url, html_text)
                html_source = "forward_fetch"
            elif key in recovered_index:
                html_text, html_source = recovered_index[key]
                page = page_from_html(url, html_text)
            else:
                page = None
                html_text = None
                html_source = "missing"
            row = {
                "query": query,
                "url": url,
                "url_key": key,
                "position": position,
                "baseline_position": None if prior is None else prior[1],
                "entrant": prior is None,
                "html": page is not None,
                "html_source": html_source,
                "html_sha256": _html_sha(html_text),
            }
            prepared.append(row)
            if page is not None:
                pages.append(page)
                owners.append(row)
        if pages:
            raw = _features_for(query, pages)
            for row, features in zip(owners, raw):
                assert_no_rank_leak(features)
                row["bm25_value"] = float(features.get("bm25f") or 0.0)
                row["features"] = features
        for row in prepared:
            if "bm25_value" not in row:
                row["bm25_value"] = None
                row["features"] = None
        _assign_model_scores(prepared, model)
        for row in prepared:
            by_query[query].append(row)
            scored.append(row)
        for key_name, values in (
            ("persistence", persistence_scores(prepared)),
            ("blend", blend_scores(_blend_inputs(prepared))),
            ("history_aware", history_aware_scores(_blend_inputs(prepared))),
        ):
            for row, value in zip(prepared, values):
                row[key_name] = value

    unknown_content_value(scored, history, signals=offpage_signals, mean=score_mean, std=score_std)
    finite_model = [row["lambdamart"] for row in scored if row.get("html")]
    if any(value <= MISSING_CONTENT_SCORE for value in finite_model):
        raise RuntimeError("A content score collided with the missing-HTML sentinel. The sentinel was not moved.")
    missing_model = [row["lambdamart"] for row in scored if not row.get("html")]
    if any(not math.isfinite(value) or value <= MISSING_CONTENT_SCORE for value in missing_model):
        raise RuntimeError("A page with no HTML still received the shared missing-content floor.")

    queries = by_query
    scorers = {}
    for name in SCORERS:
        block = _rank_metrics(queries, name)
        if name == "history_aware":
            block["adopted"] = False
            block["adoption_reason"] = HISTORY_ADOPTION_REASON
        scorers[name] = block

    baseline_tuples = [(query, url, position) for query, urls in baseline_map.items() for url, position in ((item[0], item[1]) for item in urls.values())]
    forward_tuples = [(query, url, position) for query, urls in forward_map.items() for url, position in ((item[0], item[1]) for item in urls.values())]
    exits = 0
    for query, urls in baseline_map.items():
        later = forward_map.get(query, {})
        exits += sum(1 for key in urls if key not in later)
    coverage = {
        "baseline_rows": sum(len(urls) for urls in baseline_map.values()),
        "forward_rows": len(scored),
        "returning_rows": sum(1 for row in scored if not row["entrant"]),
        "entrant_rows": sum(1 for row in scored if row["entrant"]),
        "exits": exits,
        "html_baseline": sum(1 for row in scored if row["html_source"] == "baseline"),
        "html_forward_fetch_only": sum(1 for row in scored if row["html_source"] == "forward_fetch"),
        "html_missing": sum(1 for row in scored if row["html_source"] == "missing"),
        "html_recovered": sum(1 for row in scored if str(row["html_source"]).startswith("recovered_")),
        "content_missing": sum(1 for row in scored if row.get("content_missing")),
        "queries": len(forward_map),
        "training_examples": len(training),
        "training_queries": len({row.query for row in training}),
    }
    report = {
        "label": "PREDICTED",
        "generated_at_meaning": "The scored file may be written later. Prediction rows use the protocol time, not this clock.",
        "protocol_prediction_at": PREDICTION_CREATED_AT.isoformat(timespec="seconds"),
        "baseline_observed_on": BASELINE_DAY,
        "forward_observed_on": FORWARD_DAY,
        "feature_today": FEATURE_TODAY.isoformat(),
        "feature_names": list(FEATURE_NAMES),
        "rules": _rules(),
        "coverage": coverage,
        "snapshot_agreement_overlap_only": agreement_on_overlap(baseline_tuples, forward_tuples),
        "scorers": scorers,
        "entrant_note": (
            "A page with no 2026-11-06 position on that query has no persistence signal. "
            "Persistence ranks every previously seen URL above every unseen URL and does not order the unseen URLs. "
            "entrant_vs_returning is the pairs between those two groups. entrant_vs_entrant is the pairs among unseen URLs. "
            "A user's draft is an unseen URL that this crawl did not already store. "
            "draft_vs_returning and draft_vs_draft count those pages. "
            "Persistence does not tell one draft from another."
        ),
        "ranker_change": "none",
        "not_a_general_forecast": True,
        "files": {
            "baseline_serp_sha256": _sha256(baseline / "serp_serper_live.csv"),
            "forward_serp_sha256": _sha256(forward / "serp_serper_live.csv"),
            "baseline_html_saved": len(baseline_html),
            "forward_html_saved": len(forward_html),
            "html_in_git": False,
            "baseline_window": _request_window(baseline / "serp_serper_live.csv"),
            "forward_window": _request_window(forward / "serp_serper_live.csv"),
        },
    }
    public_rows = []
    for row in scored:
        public_rows.append({key: value for key, value in row.items() if key != "features"})
    return ForwardEval(
        training_examples=training,
        scored_rows=public_rows,
        report=report,
        forward_serp_sha256=_sha256(forward / "serp_serper_live.csv"),
    )


def _blend_inputs(rows: list[dict]) -> list[dict]:
    packed = []
    for row in rows:
        packed.append(
            {
                "baseline_position": row["baseline_position"],
                "html": row["html"],
                "bm25": row["bm25_value"],
            }
        )
    return packed


def _assign_model_scores(rows: list[dict], model) -> None:
    usable = [row for row in rows if row.get("features") is not None]
    by_id: dict[int, float] = {}
    if usable and model is None:
        raise RuntimeError("LambdaMART was not fit. The baseline needs at least two queries with stored HTML.")
    if usable:
        examples = [RankExample(row["query"], row["url"], 0.0, row["features"]) for row in usable]
        predicted = predict_scores(model, examples, list(FEATURE_NAMES))
        for example, row in zip(examples, usable):
            by_id[id(row)] = predicted[id(example)]
    for row in rows:
        if row.get("features") is None:
            row["bm25"] = MISSING_CONTENT_SCORE
            row["lambdamart"] = MISSING_CONTENT_SCORE
        else:
            row["bm25"] = float(row["bm25_value"])
            row["lambdamart"] = float(by_id[id(row)])


def _features_for(query: str, pages) -> list[dict]:
    from aeo.ranksim.features import raw_features

    rows = raw_features(query, "commercial", pages, FEATURE_TODAY if isinstance(FEATURE_TODAY, date) else date(2026, 11, 7))
    return rows


def _read_html_manifest(snapshot: Path):
    import csv

    path = snapshot / "html_manifest.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def write_forward_ledger(session, result: ForwardEval) -> dict:
    """Append PREDICTED rows at the protocol time, then the 2026-11-09 outcomes."""
    by_query: dict[str, list[dict]] = defaultdict(list)
    for row in result.scored_rows:
        by_query[row["query"]].append(row)
    stamp = PREDICTION_CREATED_AT
    for scorer in SCORERS:
        slug = _slug(scorer)
        for query in sorted(by_query):
            rows = by_query[query]
            order = sorted(rows, key=lambda item: (-item[scorer], item["url"]))
            rank = {item["url"]: place for place, item in enumerate(order, start=1)}
            candidates = [
                {
                    "url": item["url"],
                    "html_sha256": item["html_sha256"],
                    "labeled_position": None if item["baseline_position"] is None else int(item["baseline_position"]),
                }
                for item in sorted(rows, key=lambda item: item["url"])
            ]
            for subject in rows:
                calls = []
                for other in rows:
                    if other["url"] == subject["url"]:
                        continue
                    if subject[scorer] <= other[scorer]:
                        continue
                    calls.append(
                        Call(
                            competitor_url=other["url"],
                            predicted="above",
                            score_gap=float(subject[scorer] - other[scorer]),
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
                    predicted_rank=rank[subject["url"]],
                    predicted_of=len(rows),
                    candidates=candidates,
                    calls=calls,
                    confidence="UNSURE",
                    calibration_note=(
                        "PREDICTED and UNSURE. The call was frozen from the 2026-11-06 snapshot. "
                        "The 2026-11-09 order was not used to train, to pick features, or to set this label."
                    ),
                    provenance={
                        "dataset": "serper_live",
                        "label": "PREDICTED",
                        "scorer": scorer,
                        "baseline_observed_on": BASELINE_DAY,
                        "target_observed_on": FORWARD_DAY,
                        "timing": "protocol freeze after the 2026-11-06 snapshot closed and before the 2026-11-09 observation",
                        "created_at_meaning": "Protocol time, not the wall clock of a later scoring run.",
                        "uncertainty": "UNSURE",
                        "html_source": subject["html_source"],
                        "entrant": subject["entrant"],
                    },
                    created_at=stamp,
                    project_slug=slug,
                )
    uploaded = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for scorer in SCORERS:
        slug = _slug(scorer)
        for query in sorted(by_query):
            rows = by_query[query]
            record_outcome(
                session,
                query=query,
                observed_on=FORWARD_DAY,
                provider="serper",
                positions={row["url"]: int(row["position"]) for row in rows},
                depth=10,
                notes=(
                    "Serper organic results, gl=us, hl=en, num=10, observed on 2026-11-09. "
                    "This is the later snapshot. It is not a model score."
                ),
                project_slug=slug,
                source_file=result.forward_serp_name,
                source_sha256=result.forward_serp_sha256,
                uploaded_at=uploaded,
            )
    summary = {}
    for scorer in SCORERS:
        scored = score_ledger(session, project_slug=_slug(scorer))
        summary[scorer] = {
            "predictions": scored.predictions,
            "calls": scored.calls,
            "resolved": scored.resolved,
            "correct": scored.correct,
            "unresolved": scored.unresolved,
            "no_outcome_yet": scored.no_outcome_yet,
            "accuracy": None if scored.accuracy is None else round(scored.accuracy, 6),
        }
    ok, note = verify_ledger(session)
    if not ok:
        raise RuntimeError(note)
    summary["chain"] = note
    return summary


def _slug(scorer: str) -> str:
    return f"forward-{FORWARD_DAY}-{scorer}"


def export_ledger(session) -> dict:
    from aeo.ranksim.ledger import RsOutcome, RsPrediction

    predictions = []
    for row in session.query(RsPrediction).order_by(RsPrediction.id):
        predictions.append(
            {
                "created_at": row.created_at,
                "kind": row.kind,
                "query": row.query,
                "subject_url": row.subject_url,
                "project_slug": row.project_slug,
                "payload": row.payload,
                "prev_hash": row.prev_hash,
                "row_hash": row.row_hash,
            }
        )
    outcomes = []
    for row in session.query(RsOutcome).order_by(RsOutcome.id):
        outcomes.append(
            {
                "observed_on": row.observed_on,
                "query": row.query,
                "provider": row.provider,
                "depth": row.depth,
                "positions": row.positions,
                "notes": row.notes,
                "project_slug": row.project_slug,
                "source_file": row.source_file,
                "source_sha256": row.source_sha256,
            }
        )
    return {"predictions": predictions, "outcomes": outcomes}


def replay_ledger(session, exported: dict) -> None:
    """Reload exported rows and recompute the hash chain. Raises if a row changed."""
    from aeo.ranksim.ledger import RsOutcome, RsPrediction, _row_hash

    previous = "0" * 64
    for item in exported["predictions"]:
        payload = item["payload"]
        version = int(payload.get("ledger_hash_version") or 1)
        if version >= 2:
            expected = _row_hash(
                item["prev_hash"],
                item["created_at"],
                item["kind"],
                item["query"],
                item["subject_url"],
                payload,
                item["project_slug"],
            )
        else:
            expected = _row_hash(
                item["prev_hash"],
                item["created_at"],
                item["kind"],
                item["query"],
                item["subject_url"],
                payload,
            )
        if expected != item["row_hash"] or item["prev_hash"] != previous:
            raise RuntimeError("Exported prediction does not match its hash.")
        session.add(
            RsPrediction(
                created_at=item["created_at"],
                kind=item["kind"],
                query=item["query"],
                subject_url=item["subject_url"],
                project_slug=item["project_slug"],
                payload=payload,
                prev_hash=item["prev_hash"],
                row_hash=item["row_hash"],
            )
        )
        previous = item["row_hash"]
    for item in exported["outcomes"]:
        session.add(
            RsOutcome(
                observed_on=item["observed_on"],
                query=item["query"],
                provider=item["provider"],
                depth=item["depth"],
                positions=item["positions"],
                notes=item.get("notes") or "",
                project_slug=item.get("project_slug") or "",
                source_file=item.get("source_file") or "",
                source_sha256=item.get("source_sha256") or "",
                uploaded_at=item.get("uploaded_at") or "",
            )
        )
    session.commit()


def _finish(result: ForwardEval, out: Path, ledger_json: Path | None, database: str) -> dict:
    session = open_session(database)
    try:
        summary = write_forward_ledger(session, result)
        result.report["ledger"] = summary
        if ledger_json is not None:
            exported = export_ledger(session)
            exported["scores"] = summary
            _write_json(Path(ledger_json), exported)
            result.report["ledger_json"] = str(ledger_json)
        _write_json(Path(out), result.report)
    finally:
        session.close()
    return result.report


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze on the Nov 6 snapshot and score the Nov 9 order once.")
    parser.add_argument("--baseline", required=True, help="2026-11-06 snapshot directory (serp CSV and HTML). HTML is not written to git.")
    parser.add_argument("--forward", required=True, help="2026-11-09 snapshot directory.")
    parser.add_argument("--out", required=True, help="JSON report path.")
    parser.add_argument("--ledger-json", default=None, help="Export the prediction chain and the later outcomes.")
    parser.add_argument("--ledger-db", default=None, help="SQLite file for the ledger. Default is an in-memory database.")
    args = parser.parse_args(argv)
    database = f"sqlite:///{args.ledger_db}" if args.ledger_db else "sqlite://"
    if args.ledger_db:
        Path(args.ledger_db).parent.mkdir(parents=True, exist_ok=True)
    result = evaluate_snapshot_pair(Path(args.baseline), Path(args.forward))
    report = _finish(result, Path(args.out), Path(args.ledger_json) if args.ledger_json else None, database)
    printable = {
        name: {
            "pairwise": report["scorers"][name]["pairwise"]["pairwise"],
            "correct": report["scorers"][name]["pairwise"]["correct"],
            "pairs": report["scorers"][name]["pairwise"]["pairs"],
            "ndcg_at_10": report["scorers"][name]["ndcg_at_10"],
            "spearman": report["scorers"][name]["spearman"],
            "entrant_vs_returning": report["scorers"][name]["entrant_vs_returning"]["pairwise"],
            "entrant_vs_entrant": report["scorers"][name]["entrant_vs_entrant"]["pairwise"],
        }
        for name in SCORERS
    }
    printable["overlap_pairwise"] = report["snapshot_agreement_overlap_only"]["pairwise"]
    printable["overlap_spearman"] = report["snapshot_agreement_overlap_only"]["mean_spearman"]
    printable["history_adopted"] = False
    print(json.dumps(printable, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
