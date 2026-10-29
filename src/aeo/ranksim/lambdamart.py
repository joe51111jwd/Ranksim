"""LambdaMART reranker. Metrics are computed only on labeled rows you supply."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from aeo.crawl.urls import normalize_url
from aeo.ranksim.features import FEATURE_NAMES, MONOTONE_UP, PageDoc, raw_features
from aeo.ranksim.groundtruth import SerpLabel, navboost_leaks
from aeo.ranksim.metrics import mean_metric, ndcg_at_k, spearman


@dataclass
class RankExample:
    query: str
    url: str
    position: float
    features: dict[str, float]
    intent: str = "commercial"


@dataclass
class QueryScore:
    query: str
    n_urls: int
    pairwise_accuracy: float | None
    ndcg_at_10: float | None
    spearman: float | None
    top3_hit: float | None
    smartcrew_real_position: float | None
    smartcrew_predicted_rank: int | None
    smartcrew_url: str | None


@dataclass
class AccuracyReport:
    ndcg_at_10: float | None
    spearman: float | None
    top3_hit_rate: float | None
    pairwise_accuracy: float | None
    n_queries: int
    n_pairs: int
    n_labeled_urls: int
    uncovered_urls: int
    navboost_withheld: bool
    cross_validated: bool
    real_google: bool
    note: str
    fold_notes: list[str] = field(default_factory=list)
    query_scores: list[QueryScore] = field(default_factory=list)


def _lookup_page(pages: dict[str, PageDoc], url: str) -> PageDoc | None:
    if url in pages:
        return pages[url]
    try:
        key = normalize_url(url)
    except ValueError:
        return None
    return pages.get(key)


def relevance(position: float) -> float:
    if position <= 0:
        return 0.0
    if position >= 11:
        return 0.0
    return float(11 - position)


def navboost_value(position: float | None) -> float:
    if position is None or position <= 0:
        return 0.0
    return 1.0 / math.log2(position + 1.0)


def build_examples(
    labels: list[SerpLabel],
    pages: dict[str, PageDoc],
    intents: dict[str, str] | None = None,
    *,
    today: date | None = None,
) -> tuple[list[RankExample], int, bool]:
    """Join labels to pages we have. URLs we never crawled are counted, not invented."""
    today = today or date.today()
    intents = intents or {}
    withhold = navboost_leaks(labels)
    grouped: dict[str, list[tuple[SerpLabel, PageDoc]]] = {}
    uncovered = 0
    for label in labels:
        page = _lookup_page(pages, label.url)
        if page is None:
            uncovered += 1
            continue
        grouped.setdefault(label.query, []).append((label, page))
    examples: list[RankExample] = []
    for query, pairs in grouped.items():
        docs = []
        for label, page in pairs:
            prior = 0.0 if withhold else navboost_value(label.prior_position)
            docs.append(
                PageDoc(
                    url=page.url,
                    title=page.title,
                    h1=page.h1,
                    headings=page.headings,
                    body=page.body,
                    word_count=page.word_count,
                    table_count=page.table_count,
                    has_faq=page.has_faq,
                    link_count=page.link_count,
                    fetch_ms=page.fetch_ms,
                    updated=page.updated,
                    authority=page.authority,
                    anchors=page.anchors,
                    pagerank=page.pagerank,
                    domain_rank=page.domain_rank,
                    homepage_authority=page.homepage_authority,
                    ad_hits=page.ad_hits,
                    navboost_prior=prior,
                    is_ours=page.is_ours,
                )
            )
        raw = raw_features(query, intents.get(query, "commercial"), docs, today)
        for (label, _page), features in zip(pairs, raw):
            examples.append(
                RankExample(
                    query=query,
                    url=label.url,
                    position=label.position,
                    features=features,
                    intent=intents.get(query, "commercial"),
                )
            )
    return examples, uncovered, withhold


def cross_validate(examples: list[RankExample], *, real_google: bool, folds_mode: str = "auto") -> AccuracyReport:
    by_query: dict[str, list[RankExample]] = {}
    for example in examples:
        by_query.setdefault(example.query, []).append(example)
    usable = {query: rows for query, rows in by_query.items() if len(rows) >= 2}
    empty = AccuracyReport(
        ndcg_at_10=None,
        spearman=None,
        top3_hit_rate=None,
        pairwise_accuracy=None,
        n_queries=len(usable),
        n_pairs=0,
        n_labeled_urls=len(examples),
        uncovered_urls=0,
        navboost_withheld=False,
        cross_validated=False,
        real_google=real_google,
        note=_need_more(len(usable)),
    )
    if len(usable) < 2:
        return empty

    queries = sorted(usable)
    if folds_mode == "loo":
        folds = [{query} for query in queries]
        protocol = "Leave-one-query-out"
    else:
        fold_count = len(queries) if len(queries) <= 8 else 5
        folds = _folds(queries, fold_count)
        protocol = "Query holdout"
    ndcgs: list[float | None] = []
    spearmans: list[float | None] = []
    hits: list[float] = []
    pair_correct = 0
    pair_total = 0
    notes: list[str] = []
    query_scores: list[QueryScore] = []
    for test_queries in folds:
        train_rows = [row for query, rows in usable.items() if query not in test_queries for row in rows]
        test_rows = [row for query in test_queries for row in usable[query]]
        if len({row.query for row in train_rows}) < 1 or not test_rows:
            continue
        model = fit_lambdamart(train_rows)
        scores = predict_scores(model, test_rows)
        for query in test_queries:
            group = [row for row in test_rows if row.query == query]
            group_scores = [scores[id(row)] for row in group]
            ndcg = _ndcg(group, group_scores)
            rho = spearman(group_scores, [row.position for row in group])
            hit = _top3_hit(group, group_scores)
            correct, total = _pairs(group, group_scores)
            ndcgs.append(ndcg)
            spearmans.append(rho)
            hits.append(hit)
            pair_correct += correct
            pair_total += total
            query_scores.append(_query_score(group, group_scores, ndcg, rho, hit, correct, total))
    if pair_total == 0:
        empty.note = "Cross-validation produced no labeled pairs."
        return empty
    return AccuracyReport(
        ndcg_at_10=mean_metric(ndcgs),
        spearman=mean_metric(spearmans),
        top3_hit_rate=sum(hits) / len(hits) if hits else None,
        pairwise_accuracy=pair_correct / pair_total,
        n_queries=len(usable),
        n_pairs=pair_total,
        n_labeled_urls=len(examples),
        uncovered_urls=0,
        navboost_withheld=False,
        cross_validated=True,
        real_google=real_google,
        note=(
            f"{protocol} predicted agreement with the labeled file, not a live Google measurement."
        ),
        fold_notes=notes,
        query_scores=query_scores,
    )


def fit_lambdamart(examples: list[RankExample]):
    import lightgbm as lgb

    ordered = sorted(examples, key=lambda row: row.query)
    features = [[row.features.get(name, 0.0) for name in FEATURE_NAMES] for row in ordered]
    labels = [int(max(0, min(10, round(relevance(row.position))))) for row in ordered]
    group: list[int] = []
    current = None
    size = 0
    for row in ordered:
        if row.query != current:
            if size:
                group.append(size)
            current = row.query
            size = 1
        else:
            size += 1
    if size:
        group.append(size)
    import numpy as np

    dataset = lgb.Dataset(
        np.asarray(features, dtype=float),
        label=np.asarray(labels, dtype=float),
        group=group,
        feature_name=list(FEATURE_NAMES),
    )
    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "num_leaves": 15,
        "learning_rate": 0.08,
        "min_data_in_leaf": 1,
        "verbose": -1,
        "num_threads": 1,
        "monotone_constraints": [1 if name in MONOTONE_UP else 0 for name in FEATURE_NAMES],
        "monotone_constraints_method": "advanced",
    }
    return lgb.train(params, dataset, num_boost_round=40)


def predict_scores(model, examples: list[RankExample]) -> dict[int, float]:
    if not examples:
        return {}
    import numpy as np

    matrix = np.asarray(
        [[_feature_value(row.features, name) for name in FEATURE_NAMES] for row in examples],
        dtype=float,
    )
    values = model.predict(matrix)
    scores = {id(row): float(score) for row, score in zip(examples, values)}
    return gate_thin_pages(examples, scores)


def gate_thin_pages(examples: list[RankExample], scores: dict[int, float]) -> dict[int, float]:
    """Pages under 150 words rank behind every page that has a real body."""
    grouped: dict[str, list[RankExample]] = {}
    for example in examples:
        grouped.setdefault(example.query, []).append(example)
    adjusted = dict(scores)
    for group in grouped.values():
        thin = [row for row in group if _is_thin(row)]
        solid = [row for row in group if not _is_thin(row)]
        if not thin or not solid:
            continue
        floor = min(adjusted[id(row)] for row in solid)
        ordered = sorted(thin, key=lambda row: (-adjusted[id(row)], row.url))
        for offset, row in enumerate(ordered, start=1):
            adjusted[id(row)] = floor - offset
    return adjusted


def _is_thin(example: RankExample) -> bool:
    return float(example.features.get("thin_content", 0.0) or 0.0) >= 1.0


def _feature_value(features: dict[str, float], name: str) -> float:
    value = features.get(name, 0.0)
    if value is None:
        return float("nan")
    return float(value)


def save_model(model, path: str) -> None:
    model.save_model(path)


def load_model(path: str):
    import lightgbm as lgb

    return lgb.Booster(model_file=path)


def _ndcg(group: list[RankExample], scores: list[float]) -> float | None:
    order = sorted(range(len(group)), key=lambda index: (-scores[index], index))
    gains = {str(index): relevance(group[index].position) for index in range(len(group))}
    return ndcg_at_k([str(index) for index in order], gains, k=10)


def _top3_hit(group: list[RankExample], scores: list[float]) -> float:
    gold = min(range(len(group)), key=lambda index: (group[index].position, index))
    order = sorted(range(len(group)), key=lambda index: (-scores[index], index))
    return 1.0 if gold in order[:3] else 0.0


def _pairs(group: list[RankExample], scores: list[float]) -> tuple[int, int]:
    correct = 0
    total = 0
    for left in range(len(group)):
        for right in range(len(group)):
            if group[left].position >= group[right].position:
                continue
            total += 1
            if scores[left] > scores[right]:
                correct += 1
    return correct, total


def _query_score(group, scores, ndcg, rho, hit, correct, total) -> QueryScore:
    from aeo.crawl.urls import domain_of

    order = sorted(range(len(group)), key=lambda index: (-scores[index], index))
    rank = {index: place for place, index in enumerate(order, start=1)}
    ours = [index for index, row in enumerate(group) if _is_smartcrew(domain_of(row.url))]
    if not ours:
        real = None
        predicted = None
        url = None
    else:
        best = min(ours, key=lambda index: (group[index].position, index))
        real = group[best].position
        predicted = rank[best]
        url = group[best].url
    return QueryScore(
        query=group[0].query,
        n_urls=len(group),
        pairwise_accuracy=(correct / total) if total else None,
        ndcg_at_10=ndcg,
        spearman=rho,
        top3_hit=hit,
        smartcrew_real_position=real,
        smartcrew_predicted_rank=predicted,
        smartcrew_url=url,
    )


def _is_smartcrew(host: str) -> bool:
    return host == "smartcrewhq.com" or host.endswith(".smartcrewhq.com")


def _folds(queries: list[str], fold_count: int) -> list[set[str]]:
    folds: list[set[str]] = [set() for _ in range(fold_count)]
    for index, query in enumerate(queries):
        folds[index % fold_count].add(query)
    return [fold for fold in folds if fold]


def _need_more(n_queries: int) -> str:
    return (
        f"Accuracy was not computed ({n_queries} usable queries). "
        "Need at least 2 queries, each with at least 2 result URLs we have pages for. "
        "Best input is a real Google top 10 for many of the 150 keywords: query, url, position. "
        "A Search Console export with Query, Page, and Position also works. "
        "prior_position must be an older observation, not a copy of position."
    )
