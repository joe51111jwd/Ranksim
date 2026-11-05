"""Leave-one-query-out fit plus a margin check for when to say UNSURE.

If large score gaps are not more accurate on the backtest, every check stays UNSURE.

A threshold picked on the backtest pairs and then scored on those same pairs is optimistic.
Before a threshold is used for CLEARER labels, ``validate_calibration`` re-checks it with
query clusters and their URLs held out and the threshold chosen on other folds only. That
is the closest backtest to scoring a page the model has never seen, such as a draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aeo.ranksim.features import MODEL_FEATURES
from aeo.ranksim.lambdamart import RankExample, _ndcg, _pairs, _top3_hit, fit_lambdamart, predict_scores
from aeo.ranksim.metrics import mean_metric, spearman


MIN_CONFIDENT_ACCURACY = 0.65
MIN_CONFIDENT_CALLS = 30


@dataclass
class PairMargin:
    gap: float
    correct: bool


@dataclass
class Calibration:
    threshold: float | None
    confident_pairwise: float | None
    confident_coverage: float | None
    note: str

    def label(self, gap: float) -> str:
        if self.threshold is None or gap < self.threshold:
            return "UNSURE"
        return "CLEARER"


@dataclass
class LooFit:
    models: dict = field(default_factory=dict)
    pairwise: float | None = None
    ndcg: float | None = None
    spearman: float | None = None
    top3: float | None = None
    n_queries: int = 0
    n_pairs: int = 0
    pairs: list[PairMargin] = field(default_factory=list)
    calibration: Calibration = field(
        default_factory=lambda: Calibration(None, None, None, "Not calibrated.")
    )


def loo_fit(
    examples: list[RankExample],
    feature_names: list[str] | tuple[str, ...] | None = None,
    *,
    keep_models: bool = False,
    validate: bool = True,
) -> LooFit:
    names = list(feature_names or MODEL_FEATURES)
    by_query: dict[str, list[RankExample]] = {}
    for example in examples:
        by_query.setdefault(example.query, []).append(example)
    usable = {query: rows for query, rows in by_query.items() if len(rows) >= 2}
    result = LooFit(n_queries=len(usable))
    if len(usable) < 2:
        result.calibration = Calibration(None, None, None, "Too few queries to calibrate confidence.")
        return result
    ndcgs: list[float | None] = []
    spearmans: list[float | None] = []
    hits: list[float] = []
    pair_correct = 0
    pair_total = 0
    for query, rows in usable.items():
        train = [row for name, group in usable.items() if name != query for row in group]
        if not train:
            continue
        model = fit_lambdamart(train, names)
        if keep_models:
            result.models[query] = model
        scores = predict_scores(model, rows, names)
        ordered_scores = [scores[id(row)] for row in rows]
        ndcgs.append(_ndcg(rows, ordered_scores))
        spearmans.append(spearman(ordered_scores, [row.position for row in rows]))
        hits.append(_top3_hit(rows, ordered_scores))
        correct, total = _pairs(rows, ordered_scores)
        pair_correct += correct
        pair_total += total
        result.pairs.extend(_margins(rows, ordered_scores))
    result.pairwise = (pair_correct / pair_total) if pair_total else None
    result.ndcg = mean_metric(ndcgs)
    result.spearman = mean_metric(spearmans)
    result.top3 = sum(hits) / len(hits) if hits else None
    result.n_pairs = pair_total
    in_sample = calibrate(result.pairs)
    if validate:
        rows = [row for group in usable.values() for row in group]
        result.calibration = validate_calibration(in_sample, rows, names)
    else:
        result.calibration = in_sample
    return result


def validate_calibration(in_sample: Calibration, examples: list[RankExample], feature_names: list[str]) -> Calibration:
    """Keep a threshold only if it holds with query clusters and their URLs held out."""
    if in_sample.threshold is None:
        return in_sample
    from aeo.ranksim.benchmark import lambdamart_scorer, query_clusters, run_protocol

    clusters = query_clusters(examples)
    if len(set(clusters.values())) < 2:
        return Calibration(None, None, None, "Too few query clusters to check the confidence threshold. Calls stay UNSURE.")
    result = run_protocol(
        examples,
        "cluster-url",
        clusters,
        {"lambdamart": lambdamart_scorer(feature_names)},
        bootstrap=0,
    )
    cross = result.confidence
    # Score the threshold that would actually be deployed on held-out predictions, not just
    # the per-fold thresholds the cross-fitted check picks.
    margins = [pair for row in result.scorers[0].queries for pair in row.margins]
    deployed = [pair for pair in margins if pair.gap >= in_sample.threshold]
    deployed_right = sum(1 for pair in deployed if pair.correct)
    deployed_acc = deployed_right / len(deployed) if deployed else 0.0
    deployed_cov = len(deployed) / len(margins) if margins else 0.0
    in_sample_text = (
        f"On the leave-one-query-out backtest a score gap of {in_sample.threshold:.2f} looked accurate "
        f"({in_sample.confident_pairwise:.3f} on {in_sample.confident_coverage:.0%} of pairs), but that "
        "threshold was chosen and scored on the same pairs."
    )
    held_text = (
        f"With query clusters and their URLs held out, that gap was right on {deployed_right} of {len(deployed)} "
        f"pairs; with each fold's threshold chosen on the other folds, {0 if cross is None else cross.correct} of "
        f"{0 if cross is None else cross.calls} confident calls were right."
    )
    passes = (
        len(deployed) >= MIN_CONFIDENT_CALLS
        and deployed_acc >= MIN_CONFIDENT_ACCURACY
        and cross is not None
        and cross.calls >= MIN_CONFIDENT_CALLS
        and (cross.accuracy or 0.0) >= MIN_CONFIDENT_ACCURACY
    )
    if not passes:
        return Calibration(
            None,
            None,
            None,
            (
                f"{in_sample_text} {held_text} Both must reach {MIN_CONFIDENT_ACCURACY:.2f} on at least "
                f"{MIN_CONFIDENT_CALLS} calls, so every call stays UNSURE."
            ),
        )
    return Calibration(
        in_sample.threshold,
        deployed_acc,
        deployed_cov,
        (
            f"Pairs with a score gap of at least {in_sample.threshold:.2f} are labeled CLEARER. {held_text} "
            f"(The gap was chosen on leave-one-query-out pairs from the same queries, so this is still "
            "somewhat optimistic.) Smaller gaps are UNSURE. Still PREDICTED, not a Google rank."
        ),
    )


def calibrate(pairs: list[PairMargin]) -> Calibration:
    if len(pairs) < 20:
        return Calibration(None, None, None, "Too few pairs to calibrate. The check stays UNSURE.")
    chosen: tuple[float, float, float] | None = None
    for threshold in (0.05, 0.1, 0.25, 0.5, 1.0, 2.0):
        subset = [pair for pair in pairs if pair.gap >= threshold]
        if len(subset) < 30:
            continue
        accuracy = sum(1 for pair in subset if pair.correct) / len(subset)
        coverage = len(subset) / len(pairs)
        if accuracy >= 0.65:
            chosen = (threshold, accuracy, coverage)
            break
    if chosen is None:
        return Calibration(
            None,
            None,
            None,
            "No score gap on this backtest lifts pairwise accuracy to 0.65 with at least 30 pairs. Calls stay UNSURE.",
        )
    threshold, accuracy, coverage = chosen
    return Calibration(
        threshold,
        accuracy,
        coverage,
        (
            f"Pairs with a score gap of at least {threshold:.2f} had pairwise accuracy {accuracy:.3f} "
            f"on {coverage:.0%} of backtest pairs. Smaller gaps are UNSURE."
        ),
    )


def _margins(rows: list[RankExample], scores: list[float]) -> list[PairMargin]:
    found: list[PairMargin] = []
    for left in range(len(rows)):
        for right in range(len(rows)):
            if rows[left].position >= rows[right].position:
                continue
            gap = abs(scores[left] - scores[right])
            found.append(PairMargin(gap=gap, correct=scores[left] > scores[right]))
    return found
