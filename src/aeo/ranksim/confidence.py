"""Leave-one-query-out fit plus a margin check for when to say UNSURE.

If large score gaps are not more accurate on the backtest, every check stays UNSURE.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aeo.ranksim.features import FEATURE_NAMES
from aeo.ranksim.lambdamart import RankExample, _ndcg, _pairs, _top3_hit, fit_lambdamart, predict_scores
from aeo.ranksim.metrics import mean_metric, spearman


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
) -> LooFit:
    names = list(feature_names or FEATURE_NAMES)
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
    result.calibration = calibrate(result.pairs)
    return result


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
