"""Leakage-aware benchmark: the model next to simple baselines, on identical folds, with uncertainty.

Why this exists. The labeled file has few queries, many near-duplicate queries (singular and
plural, "hcss alternative" and "hcss heavyjob alternative"), and the same URL in several
queries. Leave-one-query-out then lets a model score a page it already saw ranked for a
near-identical query. That says little about an unpublished draft the model has never seen.

Protocols (all hold out whole queries; none train on a held-out label):
- ``loqo``: leave one query out. Comparable with ``evaluate``.
- ``cluster``: leave one query cluster out. Queries whose labeled URL sets overlap
  (Jaccard >= threshold) are one cluster, so near-duplicates are held out together.
- ``cluster-url``: ``cluster`` plus every training row whose URL appears in the held-out
  cluster is dropped. The model never sees a held-out page with any label. This is the
  closest protocol to "score a page Google has not ranked before".

Scorers on identical folds and pairs: the LambdaMART reranker, BM25F alone, the hand
weighted linear prior, a domain prior learned from training labels only, and random.

Metric definitions (per held-out query, then pooled):
- Pairwise accuracy: over URL pairs with different labeled positions, the share where the
  better position has the strictly higher score. Score ties count as wrong (as in
  ``evaluate``); the tie count is reported. Pooled = correct pairs / all pairs ("micro").
  Macro = mean of per-query pairwise accuracy.
- NDCG@10 with gain ``2^(11 - position) - 1`` over the labeled candidates only, so short
  lists inflate it. Spearman over predicted and labeled order. Both are means over queries.
- Uncertainty: percentile bootstrap that resamples query clusters (not queries, because
  queries inside a cluster are correlated). Differences between scorers use paired
  resamples of the same clusters.

These are predicted-agreement numbers on labeled Google results among the pages Google
already returned. They are not live Google ranks and not a claim about a new page.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Callable

from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim.confidence import PairMargin, calibrate
from aeo.ranksim.features import FEATURE_NAMES, normalize_rows
from aeo.ranksim.lambdamart import RankExample, _ndcg, fit_lambdamart, predict_scores, relevance
from aeo.ranksim.metrics import spearman

PROTOCOLS = ("loqo", "cluster", "cluster-url")
GRAPH_FEATURES = frozenset({"orphan", "link_depth", "crawl_inlinks"})
BENCHMARK_VERSION = "1"

Scorer = Callable[[list[RankExample], list[RankExample]], list[float]]


@dataclass
class Fold:
    test_queries: frozenset[str]
    dropped_train_rows: int = 0


@dataclass
class QueryResult:
    query: str
    cluster: int
    n_urls: int
    correct: int
    ties: int
    total: int
    ndcg: float | None
    spearman: float | None
    margins: list[PairMargin] = field(default_factory=list)

    @property
    def pairwise(self) -> float | None:
        return self.correct / self.total if self.total else None


@dataclass
class Interval:
    point: float | None
    low: float | None
    high: float | None


@dataclass
class ScorerResult:
    name: str
    pairwise: Interval
    pairwise_macro: float | None
    ndcg: float | None
    spearman: float | None
    n_pairs: int
    n_ties: int
    n_queries: int
    diff_vs_bm25f: Interval | None = None
    diff_vs_model: Interval | None = None
    queries: list[QueryResult] = field(default_factory=list)


@dataclass
class ConfidenceCheck:
    """Cross-fitted score-gap threshold: chosen without the fold it is applied to."""

    calls: int
    correct: int
    total_pairs: int
    folds_with_threshold: int
    folds: int
    note: str

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.calls if self.calls else None

    @property
    def coverage(self) -> float | None:
        return self.calls / self.total_pairs if self.total_pairs else None


@dataclass
class NoiseBand:
    """Pooled pairwise accuracy of random scores on exactly these pairs, over many seeds."""

    seeds: int
    mean: float | None
    low: float | None
    high: float | None
    ndcg_mean: float | None = None


@dataclass
class ProtocolResult:
    protocol: str
    n_folds: int
    n_clusters: int
    dropped_train_rows: int
    scorers: list[ScorerResult]
    confidence: ConfidenceCheck | None
    random_band: NoiseBand | None = None


@dataclass
class BenchmarkReport:
    benchmark_version: str
    created_at: str
    as_of: str
    git_commit: str | None
    lightgbm_version: str | None
    feature_names_sha: str
    labels_sha: str | None
    corpus_sha: str | None
    corpus_pages: int | None
    n_labels: int
    n_examples: int
    uncovered: int
    n_queries: int
    clusters: dict[str, int]
    cluster_threshold: float
    bootstrap: int
    seed: int
    coverage: dict[str, int]
    protocols: list[ProtocolResult]
    notes: list[str]

    def to_json(self) -> str:
        return json.dumps(_drop_keys(asdict(self), {"margins"}), indent=2, sort_keys=True, default=_json_default)


def query_clusters(examples, threshold: float = 0.3) -> dict[str, int]:
    """Group queries whose labeled URL sets overlap (Jaccard >= threshold), transitively.

    ``examples`` is anything with ``.query`` and ``.url`` (RankExample or SerpLabel). Pass the
    full label file so clusters do not change when crawl coverage changes. Uses only which
    URLs were labeled for each query, never positions, so it is safe for fold assignment.
    It makes held-out sets larger and the estimate more conservative.
    """
    urls: dict[str, set[str]] = defaultdict(set)
    for row in examples:
        urls[row.query].add(_url_key(row.url))
    queries = sorted(urls)
    parent = {query: query for query in queries}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for index, left in enumerate(queries):
        for right in queries[index + 1 :]:
            union = urls[left] | urls[right]
            if union and len(urls[left] & urls[right]) / len(union) >= threshold:
                parent[find(left)] = find(right)
    roots = sorted({find(query) for query in queries}, key=lambda root: min(q for q in queries if find(q) == root))
    ids = {root: index for index, root in enumerate(roots)}
    return {query: ids[find(query)] for query in queries}


def make_folds(queries: list[str], clusters: dict[str, int], protocol: str) -> list[Fold]:
    if protocol == "loqo":
        return [Fold(frozenset({query})) for query in sorted(queries)]
    if protocol in {"cluster", "cluster-url"}:
        grouped: dict[int, set[str]] = defaultdict(set)
        for query in queries:
            grouped[clusters[query]].add(query)
        return [Fold(frozenset(grouped[key])) for key in sorted(grouped)]
    raise ValueError(f"Unknown protocol {protocol!r}. Use one of {', '.join(PROTOCOLS)}.")


def split_fold(usable: dict[str, list[RankExample]], fold: Fold, protocol: str) -> tuple[list[RankExample], list[RankExample], int]:
    test = [row for query in sorted(fold.test_queries) for row in usable[query]]
    train = [row for query, rows in sorted(usable.items()) if query not in fold.test_queries for row in rows]
    dropped = 0
    if protocol == "cluster-url":
        held = {_url_key(row.url) for row in test}
        kept = [row for row in train if _url_key(row.url) not in held]
        dropped = len(train) - len(kept)
        train = kept
    return train, test, dropped


# Scorers -----------------------------------------------------------------------------


def lambdamart_scorer(feature_names: list[str] | None = None) -> Scorer:
    names = list(feature_names or FEATURE_NAMES)

    def score(train: list[RankExample], test: list[RankExample]) -> list[float]:
        trainable = _queries_with_pairs(train)
        if len(trainable) < 1:
            return [0.0 for _ in test]
        model = fit_lambdamart(trainable, names)
        scores = predict_scores(model, test, names)
        return [scores[id(row)] for row in test]

    return score


def feature_scorer(name: str) -> Scorer:
    def score(_train: list[RankExample], test: list[RankExample]) -> list[float]:
        values = []
        for row in test:
            value = row.features.get(name)
            values.append(0.0 if value is None or value != value else float(value))
        return values

    return score


def linear_prior_scorer() -> Scorer:
    from aeo.ranksim.score import HAND_WEIGHTS, dot

    def score(_train: list[RankExample], test: list[RankExample]) -> list[float]:
        out = [0.0] * len(test)
        for indexes in _by_query_index(test).values():
            rows = [{name: test[i].features.get(name, 0.0) for name in FEATURE_NAMES} for i in indexes]
            for i, scaled in zip(indexes, normalize_rows(rows)):
                out[i] = dot(HAND_WEIGHTS, scaled)
        return out

    return score


def domain_prior_scorer() -> Scorer:
    """Mean labeled relevance of each domain in the training queries only."""

    def score(train: list[RankExample], test: list[RankExample]) -> list[float]:
        totals: dict[str, list[float]] = defaultdict(list)
        for row in train:
            totals[domain_of(row.url)].append(relevance(row.position))
        overall = [value for values in totals.values() for value in values]
        default = sum(overall) / len(overall) if overall else 0.0
        means = {domain: sum(values) / len(values) for domain, values in totals.items()}
        return [means.get(domain_of(row.url), default) for row in test]

    return score


def random_scorer(seed: int) -> Scorer:
    def score(_train: list[RankExample], test: list[RankExample]) -> list[float]:
        rng = random.Random(f"{seed}:" + "|".join(sorted(row.query for row in test)))
        return [rng.random() for _ in test]

    return score


def default_scorers(feature_names: list[str], seed: int) -> dict[str, Scorer]:
    return {
        "lambdamart": lambdamart_scorer(feature_names),
        "bm25f_only": feature_scorer("bm25f"),
        "linear_prior": linear_prior_scorer(),
        "domain_prior": domain_prior_scorer(),
    }


def random_band(examples: list[RankExample], *, seeds: int = 1000, seed: int = 0) -> NoiseBand:
    """What luck alone scores on these exact pairs. Random scores need no training, so the
    band is the same for every protocol."""
    usable = _queries_with_pairs_map(examples)
    if not usable or seeds <= 0:
        return NoiseBand(seeds, None, None, None)
    rng = random.Random(seed)
    values = []
    ndcgs = []
    for _ in range(seeds):
        correct = total = 0
        run_ndcg = []
        for rows in usable.values():
            scores = [rng.random() for _ in rows]
            result = _query_result(rows, scores, 0)
            correct += result.correct
            total += result.total
            if result.ndcg is not None:
                run_ndcg.append(result.ndcg)
        values.append(correct / total)
        if run_ndcg:
            ndcgs.append(sum(run_ndcg) / len(run_ndcg))
    low, high = _percentiles(values)
    ndcg_mean = sum(ndcgs) / len(ndcgs) if ndcgs else None
    return NoiseBand(seeds, sum(values) / len(values), low, high, ndcg_mean)


# Running ---------------------------------------------------------------------------


def run_protocol(
    examples: list[RankExample],
    protocol: str,
    clusters: dict[str, int],
    scorers: dict[str, Scorer],
    *,
    bootstrap: int = 2000,
    seed: int = 0,
) -> ProtocolResult:
    usable = _queries_with_pairs_map(examples)
    folds = make_folds(sorted(usable), clusters, protocol)
    per_scorer: dict[str, list[QueryResult]] = {name: [] for name in scorers}
    fold_of: dict[str, int] = {}
    dropped_total = 0
    for fold_index, fold in enumerate(folds):
        train, test, dropped = split_fold(usable, fold, protocol)
        fold.dropped_train_rows = dropped
        dropped_total += dropped
        for query in fold.test_queries:
            fold_of[query] = fold_index
        for name, scorer in scorers.items():
            scores = scorer(train, test)
            for indexes in _by_query_index(test).values():
                group = [test[i] for i in indexes]
                group_scores = [scores[i] for i in indexes]
                per_scorer[name].append(_query_result(group, group_scores, clusters[group[0].query]))
    cluster_ids = sorted({clusters[query] for query in usable})
    results: list[ScorerResult] = []
    for name, queries in per_scorer.items():
        results.append(_summarize(name, queries, cluster_ids, bootstrap, seed))
    by_name = {row.name: row for row in results}
    for row in results:
        if "bm25f_only" in per_scorer and row.name != "bm25f_only":
            row.diff_vs_bm25f = _paired_diff(per_scorer[row.name], per_scorer["bm25f_only"], cluster_ids, bootstrap, seed)
        if "lambdamart" in per_scorer and row.name != "lambdamart":
            row.diff_vs_model = _paired_diff(per_scorer[row.name], per_scorer["lambdamart"], cluster_ids, bootstrap, seed)
    confidence = None
    if "lambdamart" in by_name:
        confidence = cross_fitted_confidence(per_scorer["lambdamart"], fold_of, len(folds))
    return ProtocolResult(
        protocol=protocol,
        n_folds=len(folds),
        n_clusters=len(cluster_ids),
        dropped_train_rows=dropped_total,
        scorers=results,
        confidence=confidence,
    )


def cross_fitted_confidence(queries: list[QueryResult], fold_of: dict[str, int], n_folds: int) -> ConfidenceCheck:
    """Choose the score-gap threshold on the other folds' pairs, apply it to this fold's.

    ``confidence.calibrate`` picks a threshold on every backtest pair and then reports
    accuracy on those same pairs, which is optimistic. Here the reported accuracy comes
    only from pairs that did not choose their own threshold.
    """
    by_fold: dict[int, list[PairMargin]] = defaultdict(list)
    for row in queries:
        by_fold[fold_of[row.query]].extend(row.margins)
    total = sum(len(values) for values in by_fold.values())
    calls = 0
    correct = 0
    with_threshold = 0
    for fold, margins in by_fold.items():
        others = [pair for other, values in by_fold.items() if other != fold for pair in values]
        chosen = calibrate(others)
        if chosen.threshold is None:
            continue
        with_threshold += 1
        confident = [pair for pair in margins if pair.gap >= chosen.threshold]
        calls += len(confident)
        correct += sum(1 for pair in confident if pair.correct)
    if not calls:
        note = "No cross-fitted threshold produced a confident call. Every pair stays UNSURE."
    else:
        note = (
            f"Cross-fitted: {calls} of {total} pairs got a confident call; "
            f"{correct} were right ({correct / calls:.3f}). Thresholds came from other folds."
        )
    return ConfidenceCheck(calls, correct, total, with_threshold, n_folds, note)


def run_benchmark(
    examples: list[RankExample],
    *,
    protocols: tuple[str, ...] = PROTOCOLS,
    cluster_threshold: float = 0.3,
    bootstrap: int = 2000,
    seed: int = 0,
    feature_names: list[str] | None = None,
    scorers: dict[str, Scorer] | None = None,
    cluster_source=None,
) -> tuple[dict[str, int], list[ProtocolResult]]:
    usable = _queries_with_pairs(examples)
    all_clusters = query_clusters(cluster_source if cluster_source is not None else usable, cluster_threshold)
    used = {row.query for row in usable}
    missing = used - set(all_clusters)
    if missing:
        raise ValueError(f"cluster_source has no labels for {sorted(missing)[:3]}")
    remap: dict[int, int] = {}
    for query in sorted(used):
        remap.setdefault(all_clusters[query], len(remap))
    clusters = {query: remap[all_clusters[query]] for query in sorted(used)}
    names = list(feature_names or FEATURE_NAMES)
    chosen = scorers or default_scorers(names, seed)
    band = random_band(usable, seed=seed)
    results = []
    for protocol in protocols:
        result = run_protocol(usable, protocol, clusters, chosen, bootstrap=bootstrap, seed=seed)
        result.random_band = band
        results.append(result)
    return clusters, results


# Provenance --------------------------------------------------------------------------


def file_sha256(path) -> str | None:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def corpus_fingerprint(rows: list[tuple[str, str]]) -> str:
    """sha256 over sorted (url, sha256(html)) of every stored page.

    In-corpus PageRank and anchor text depend on every stored page, not only the labeled
    ones, so two runs on different corpora are not comparable even with the same labels.
    """
    digest = hashlib.sha256()
    for url, html in sorted(rows):
        digest.update(url.encode("utf-8", "replace"))
        digest.update(b"\0")
        digest.update(hashlib.sha256((html or "").encode("utf-8", "replace")).digest())
    return digest.hexdigest()


def feature_names_sha(names: list[str]) -> str:
    return hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    if out.returncode != 0 or not sha:
        return None
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5, check=False)
    return sha + ("-dirty" if dirty.stdout.strip() else "")


def lightgbm_version() -> str | None:
    try:
        import lightgbm

        return str(lightgbm.__version__)
    except Exception:  # pragma: no cover - lightgbm is a declared dependency
        return None


def build_report(
    *,
    examples: list[RankExample],
    n_labels: int,
    uncovered: int,
    coverage: dict[str, int],
    clusters: dict[str, int],
    protocols: list[ProtocolResult],
    as_of: date,
    labels_sha: str | None,
    corpus_sha: str | None,
    corpus_pages: int | None,
    cluster_threshold: float,
    bootstrap: int,
    seed: int,
    feature_names: list[str],
    notes: list[str] | None = None,
) -> BenchmarkReport:
    return BenchmarkReport(
        benchmark_version=BENCHMARK_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        as_of=as_of.isoformat(),
        git_commit=git_commit(),
        lightgbm_version=lightgbm_version(),
        feature_names_sha=feature_names_sha(feature_names),
        labels_sha=labels_sha,
        corpus_sha=corpus_sha,
        corpus_pages=corpus_pages,
        n_labels=n_labels,
        n_examples=len(examples),
        uncovered=uncovered,
        n_queries=len({row.query for row in _queries_with_pairs(examples)}),
        clusters=clusters,
        cluster_threshold=cluster_threshold,
        bootstrap=bootstrap,
        seed=seed,
        coverage=coverage,
        protocols=protocols,
        notes=list(notes or []),
    )


def labeled_coverage(labels, session) -> dict[str, int]:
    """Why each labeled row does or does not have usable stored HTML.

    covered: stored HTML. blocked: robots or URL safety refused it. error: fetch failed
    (HTTP 403/404, timeout). empty: fetched but no HTML. not_attempted: never stored.
    """
    from aeo.ranksim.store import RsPage

    rows = session.query(RsPage.url, RsPage.status, RsPage.raw_html).all()
    by_key: dict[str, tuple[str, bool]] = {}
    for url, status, html in rows:
        has_html = bool((html or "").strip())
        by_key[url] = (status, has_html)
        by_key.setdefault(_url_key(url), (status, has_html))
    counts: dict[str, int] = defaultdict(int)
    for label in labels:
        found = by_key.get(label.url) or by_key.get(_url_key(label.url))
        if found is None:
            counts["not_attempted"] += 1
            continue
        status, has_html = found
        if status == "fetched" and has_html:
            counts["covered"] += 1
        elif status == "fetched":
            counts["empty"] += 1
        elif status == "blocked":
            counts["blocked"] += 1
        else:
            counts["error"] += 1
    return dict(counts)


# Rendering ---------------------------------------------------------------------------


def render_report(report: BenchmarkReport) -> str:
    lines = [
        "Benchmark: predicted agreement with labeled Google results, among the pages Google returned.",
        "Not live Google ranks. Not a claim about a new or unpublished page.",
        (
            f"Labels {report.n_labels}, usable rows {report.n_examples}, uncovered {report.uncovered}, "
            f"queries {report.n_queries}, query clusters {len(set(report.clusters.values()))} "
            f"(URL-overlap Jaccard >= {report.cluster_threshold})."
        ),
        "Coverage of labeled rows: " + ", ".join(f"{key} {value}" for key, value in sorted(report.coverage.items())) + ".",
        (
            f"as_of {report.as_of}. labels sha256 {_short(report.labels_sha)}. corpus sha256 {_short(report.corpus_sha)} "
            f"over {report.corpus_pages if report.corpus_pages is not None else 'n/a'} stored pages. "
            f"git {report.git_commit or 'n/a'}. lightgbm {report.lightgbm_version or 'n/a'}. "
            f"features {report.feature_names_sha}."
        ),
        (
            f"Pairwise: score ties count as wrong. 95% intervals: percentile bootstrap over query clusters, "
            f"{report.bootstrap} resamples, seed {report.seed}."
        ),
    ]
    for protocol in report.protocols:
        lines.append("")
        lines.append(
            f"[{protocol.protocol}] folds {protocol.n_folds}, clusters {protocol.n_clusters}, "
            f"training rows dropped for URL overlap {protocol.dropped_train_rows}"
        )
        lines.append(f"  {'scorer':<14}{'pairwise (95% CI)':<26}{'macro':>7}{'ndcg10':>8}{'spearman':>9}{'pairs':>7}{'ties':>6}  vs bm25f_only (95% CI)")
        for row in protocol.scorers:
            diff = "" if row.diff_vs_bm25f is None else _interval(row.diff_vs_bm25f, signed=True)
            lines.append(
                f"  {row.name:<14}{_interval(row.pairwise):<26}{_num(row.pairwise_macro):>7}{_num(row.ndcg):>8}"
                f"{_num(row.spearman):>9}{row.n_pairs:>7}{row.n_ties:>6}  {diff}"
            )
        band = protocol.random_band
        if band is not None and band.mean is not None:
            lines.append(
                f"  {'random':<14}{band.mean:.3f} mean over {band.seeds} seeds; 95% of seeds between "
                f"{band.low:.3f} and {band.high:.3f} on these pairs (luck alone); "
                f"random NDCG10 {_num(band.ndcg_mean)} for this query mix"
            )
        model = next((row for row in protocol.scorers if row.name == "lambdamart"), None)
        if model and model.diff_vs_bm25f is not None:
            lines.append("  " + _verdict("lambdamart", "bm25f_only", model.diff_vs_bm25f))
        if protocol.confidence is not None:
            lines.append("  Confidence: " + protocol.confidence.note)
    lines.append("")
    lines.extend(report.notes)
    return "\n".join(lines) + "\n"


def _verdict(left: str, right: str, diff: Interval) -> str:
    if diff.point is None or diff.low is None or diff.high is None:
        return f"{left} vs {right}: not measurable."
    if diff.low > 0:
        return f"{left} beats {right} on this sample: {diff.point:+.3f} (95% CI {diff.low:+.3f} to {diff.high:+.3f}). Small sample; one comparison of several."
    if diff.high < 0:
        return f"{left} is worse than {right} on this sample: {diff.point:+.3f} (95% CI {diff.low:+.3f} to {diff.high:+.3f})."
    return f"{left} vs {right}: {diff.point:+.3f}, 95% CI {diff.low:+.3f} to {diff.high:+.3f} includes 0. Not distinguishable on this sample."


# Internals ---------------------------------------------------------------------------


def _query_result(group: list[RankExample], scores: list[float], cluster: int) -> QueryResult:
    correct = ties = total = 0
    margins: list[PairMargin] = []
    for left in range(len(group)):
        for right in range(len(group)):
            if group[left].position >= group[right].position:
                continue
            total += 1
            if scores[left] > scores[right]:
                correct += 1
            elif scores[left] == scores[right]:
                ties += 1
            margins.append(PairMargin(gap=abs(scores[left] - scores[right]), correct=scores[left] > scores[right]))
    return QueryResult(
        query=group[0].query,
        cluster=cluster,
        n_urls=len(group),
        correct=correct,
        ties=ties,
        total=total,
        ndcg=_ndcg(group, scores),
        spearman=spearman(scores, [row.position for row in group]),
        margins=margins,
    )


def _summarize(name: str, queries: list[QueryResult], cluster_ids: list[int], bootstrap: int, seed: int) -> ScorerResult:
    correct = sum(row.correct for row in queries)
    total = sum(row.total for row in queries)
    point = correct / total if total else None
    macro_values = [row.pairwise for row in queries if row.pairwise is not None]
    ndcgs = [row.ndcg for row in queries if row.ndcg is not None]
    rhos = [row.spearman for row in queries if row.spearman is not None]
    low, high = _bootstrap_ratio(queries, cluster_ids, bootstrap, seed)
    return ScorerResult(
        name=name,
        pairwise=Interval(point, low, high),
        pairwise_macro=sum(macro_values) / len(macro_values) if macro_values else None,
        ndcg=sum(ndcgs) / len(ndcgs) if ndcgs else None,
        spearman=sum(rhos) / len(rhos) if rhos else None,
        n_pairs=total,
        n_ties=sum(row.ties for row in queries),
        n_queries=len(queries),
        queries=queries,
    )


def _cluster_totals(queries: list[QueryResult]) -> dict[int, tuple[int, int]]:
    totals: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for row in queries:
        totals[row.cluster][0] += row.correct
        totals[row.cluster][1] += row.total
    return {key: (value[0], value[1]) for key, value in totals.items()}


def _resamples(cluster_ids: list[int], bootstrap: int, seed: int) -> list[list[int]]:
    rng = random.Random(seed)
    return [[rng.choice(cluster_ids) for _ in cluster_ids] for _ in range(bootstrap)]


def _bootstrap_ratio(queries: list[QueryResult], cluster_ids: list[int], bootstrap: int, seed: int) -> tuple[float | None, float | None]:
    if bootstrap <= 0 or not cluster_ids:
        return None, None
    totals = _cluster_totals(queries)
    values = []
    for sample in _resamples(cluster_ids, bootstrap, seed):
        correct = sum(totals.get(key, (0, 0))[0] for key in sample)
        total = sum(totals.get(key, (0, 0))[1] for key in sample)
        if total:
            values.append(correct / total)
    return _percentiles(values)


def _paired_diff(left: list[QueryResult], right: list[QueryResult], cluster_ids: list[int], bootstrap: int, seed: int) -> Interval:
    a = _cluster_totals(left)
    b = _cluster_totals(right)
    point_a = sum(v[0] for v in a.values()) / max(1, sum(v[1] for v in a.values()))
    point_b = sum(v[0] for v in b.values()) / max(1, sum(v[1] for v in b.values()))
    if bootstrap <= 0 or not cluster_ids:
        return Interval(point_a - point_b, None, None)
    values = []
    for sample in _resamples(cluster_ids, bootstrap, seed):
        total_a = sum(a.get(key, (0, 0))[1] for key in sample)
        total_b = sum(b.get(key, (0, 0))[1] for key in sample)
        if not total_a or not total_b:
            continue
        values.append(
            sum(a.get(key, (0, 0))[0] for key in sample) / total_a
            - sum(b.get(key, (0, 0))[0] for key in sample) / total_b
        )
    low, high = _percentiles(values)
    return Interval(point_a - point_b, low, high)


def _percentiles(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    ordered = sorted(values)
    low = ordered[max(0, int(math.floor(0.025 * (len(ordered) - 1))))]
    high = ordered[min(len(ordered) - 1, int(math.ceil(0.975 * (len(ordered) - 1))))]
    return low, high


def _queries_with_pairs(examples: list[RankExample]) -> list[RankExample]:
    usable = _queries_with_pairs_map(examples)
    return [row for rows in usable.values() for row in rows]


def _queries_with_pairs_map(examples: list[RankExample]) -> dict[str, list[RankExample]]:
    grouped: dict[str, list[RankExample]] = defaultdict(list)
    for row in examples:
        grouped[row.query].append(row)
    return {
        query: rows
        for query, rows in sorted(grouped.items())
        if len(rows) >= 2 and len({row.position for row in rows}) >= 2
    }


def _by_query_index(rows: list[RankExample]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[row.query].append(index)
    return grouped


def _url_key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url.strip()


def _interval(value: Interval, *, signed: bool = False) -> str:
    if value.point is None:
        return "n/a"
    fmt = "{:+.3f}" if signed else "{:.3f}"
    if value.low is None or value.high is None:
        return fmt.format(value.point)
    return f"{fmt.format(value.point)} ({fmt.format(value.low)} to {fmt.format(value.high)})"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _short(value: str | None) -> str:
    return "n/a" if not value else value[:12]


def _drop_keys(value, keys: set[str]):
    if isinstance(value, dict):
        return {key: _drop_keys(item, keys) for key, item in value.items() if key not in keys}
    if isinstance(value, list):
        return [_drop_keys(item, keys) for item in value]
    return value


def _json_default(value):
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, float) and value != value:
        return None
    raise TypeError(f"Not serializable: {type(value)!r}")
