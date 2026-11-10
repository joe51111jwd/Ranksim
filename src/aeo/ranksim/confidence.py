"""Leave-one-query-out fit plus a margin check for when to say UNSURE.

If large score gaps are not more accurate on the backtest, every check stays UNSURE.

Two statistics, each calibrated on its own pairs, because each answers a different question:
- ``pairs``: the score gap between two pages. Labels a head-to-head row (draft vs one competitor).
- ``adjacent``: a page's smallest score gap to its neighbours in the predicted order, and whether
  both neighbour orders are right. Labels the predicted rank, which the check reads the same way.
Both use the model's own scores. Pages placed by the 150-word rule are left out of both: their
order comes from the rule and their gaps are synthetic.

User-facing labels go through ``product_label`` (or ``rank_confidence`` / ``pair_confidence``,
which compute the gap correctly): CLEARER needs a threshold that passed the held-out check
(``held_out``), and synthetic data is always UNSURE. The signature matches the integration branch
(PR #5), where every caller passes a predicted-rank gap, so it uses the predicted-rank threshold.

A threshold picked on the backtest pairs and then scored on those same pairs is optimistic.
Before a threshold is used for CLEARER labels, ``validate_calibration`` re-checks it with
query clusters and their URLs held out and the threshold chosen on other folds only. That
is the closest backtest to scoring a page the model has never seen, such as a draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aeo.ranksim.features import MODEL_FEATURES
from aeo.ranksim.lambdamart import RankExample, _ndcg, _pairs, _top3_hit, fit_lambdamart, gated_rows, predict_scores
from aeo.ranksim.metrics import mean_metric, spearman


MIN_CONFIDENT_ACCURACY = 0.65
MIN_CONFIDENT_CALLS = 30


@dataclass
class PairMargin:
    gap: float
    correct: bool


@dataclass
class Calibration:
    """``threshold`` labels head-to-head gaps; ``adjacent_threshold`` labels a predicted rank."""

    threshold: float | None
    confident_pairwise: float | None
    confident_coverage: float | None
    note: str
    held_out: bool = False  # the head-to-head threshold passed the held-out check
    adjacent_threshold: float | None = None
    adjacent_note: str = ""
    adjacent_held_out: bool = False  # the predicted-rank threshold passed the held-out check

    def label(self, gap: float | None) -> str:
        if self.threshold is None or gap is None or gap < self.threshold:
            return "UNSURE"
        return "CLEARER"

    def label_adjacent(self, gap: float | None) -> str:
        if self.adjacent_threshold is None or gap is None or gap < self.adjacent_threshold:
            return "UNSURE"
        return "CLEARER"

    def product_label(self, gap: float | None, *, synthetic: bool = False, kind: str = "adjacent") -> tuple[str, str]:
        """(label, note) for users. CLEARER only from a held-out threshold and never on synthetic data.

        ``kind="adjacent"`` (default) labels a predicted rank from its gap to the nearest page;
        ``kind="pairs"`` labels one head-to-head comparison.
        """
        if kind == "pairs":
            threshold, held, note, label = self.threshold, self.held_out, self.note, self.label
        elif kind == "adjacent":
            threshold, held, note, label = self.adjacent_threshold, self.adjacent_held_out, self.adjacent_note or self.note, self.label_adjacent
        else:
            raise ValueError(f"Unknown kind {kind!r}")
        if synthetic:
            return "UNSURE", (note + " Synthetic example data cannot support a confident call.").strip()
        if not held or threshold is None:
            return "UNSURE", note
        return label(gap), note


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
    adjacent: list[PairMargin] = field(default_factory=list)
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
        gated = gated_rows(rows)
        ndcgs.append(_ndcg(rows, ordered_scores))
        spearmans.append(spearman(ordered_scores, [row.position for row in rows]))
        hits.append(_top3_hit(rows, ordered_scores))
        correct, total = _pairs(rows, ordered_scores)
        pair_correct += correct
        pair_total += total
        result.pairs.extend(pair_margins(rows, ordered_scores, gated))
        result.adjacent.extend(adjacent_margins(rows, ordered_scores, gated))
    result.pairwise = (pair_correct / pair_total) if pair_total else None
    result.ndcg = mean_metric(ndcgs)
    result.spearman = mean_metric(spearmans)
    result.top3 = sum(hits) / len(hits) if hits else None
    result.n_pairs = pair_total
    in_pairs = calibrate(result.pairs, kind="pairs")
    in_adjacent = calibrate(result.adjacent, kind="adjacent")
    if validate and (in_pairs.threshold is not None or in_adjacent.threshold is not None):
        rows = [row for group in usable.values() for row in group]
        held = held_out_queries(rows, names)
        in_pairs = validate_calibration(in_pairs, rows, names, kind="pairs", queries=held)
        in_adjacent = validate_calibration(in_adjacent, rows, names, kind="adjacent", queries=held)
    result.calibration = combine(in_pairs, in_adjacent)
    return result


def combine(pairs: Calibration, adjacent: Calibration) -> Calibration:
    """One object for the check: head-to-head threshold plus the predicted-rank threshold."""
    return Calibration(
        pairs.threshold,
        pairs.confident_pairwise,
        pairs.confident_coverage,
        pairs.note,
        held_out=pairs.held_out,
        adjacent_threshold=adjacent.threshold,
        adjacent_note=adjacent.note,
        adjacent_held_out=adjacent.held_out,
    )


def held_out_queries(examples: list[RankExample], feature_names: list[str]):
    """Per-query results (with margins) with query clusters and their URLs held out, or None."""
    from aeo.ranksim.benchmark import lambdamart_scorer, query_clusters, run_protocol

    clusters = query_clusters(examples)
    if len(set(clusters.values())) < 2:
        return None
    result = run_protocol(
        examples, "cluster-url", clusters, {"lambdamart": lambdamart_scorer(feature_names)}, bootstrap=0
    )
    return result.scorers[0].queries


KIND_TEXT = {
    "pairs": "a score gap between two pages",
    "adjacent": "a score gap to the nearest page in the predicted order",
}


def validate_calibration(
    in_sample: Calibration,
    examples: list[RankExample],
    feature_names: list[str],
    *,
    kind: str = "pairs",
    queries=None,
) -> Calibration:
    """Keep a threshold only if it holds with query clusters and their URLs held out.

    ``queries`` reuses held-out results from ``held_out_queries`` so both statistics are checked
    on one run.
    """
    if in_sample.threshold is None:
        return in_sample
    if queries is None:
        queries = held_out_queries(examples, feature_names)
    if queries is None:
        return Calibration(
            None,
            None,
            None,
            "Too few query clusters to check the confidence threshold. Calls stay UNSURE. " + _in_sample_text(in_sample, kind),
        )
    return check_threshold(in_sample, queries, kind)


def _in_sample_text(in_sample: Calibration, kind: str) -> str:
    if in_sample.threshold is None or in_sample.confident_pairwise is None:
        return in_sample.note
    return (
        f"On the leave-one-query-out backtest, {KIND_TEXT.get(kind, 'a score gap')} of at least {in_sample.threshold:.2f} "
        f"looked accurate ({in_sample.confident_pairwise:.3f} on {in_sample.confident_coverage:.0%} of cases), but that "
        "threshold was chosen and scored on the same pairs."
    )


def check_threshold(in_sample: Calibration, queries, kind: str) -> Calibration:
    """Two out-of-sample checks of a threshold on held-out per-query results:
    the deployed threshold itself, and thresholds chosen on other folds only (cross-fitted)."""
    from aeo.ranksim.benchmark import cross_fitted_confidence

    margins = [pair for row in queries for pair in _kind_margins(row, kind)]
    folds = sorted({row.cluster for row in queries})
    cross = cross_fitted_confidence(queries, {row.query: row.cluster for row in queries}, len(folds), kind=kind)
    deployed = [pair for pair in margins if pair.gap >= in_sample.threshold]
    deployed_right = sum(1 for pair in deployed if pair.correct)
    deployed_acc = deployed_right / len(deployed) if deployed else 0.0
    deployed_cov = len(deployed) / len(margins) if margins else 0.0
    what = KIND_TEXT.get(kind, "a score gap")
    in_sample_text = _in_sample_text(in_sample, kind)
    held_text = (
        f"With query clusters and their URLs held out, that gap was right on {deployed_right} of {len(deployed)} "
        f"cases; with each fold's threshold chosen on the other folds, {cross.correct} of "
        f"{cross.calls} confident calls were right."
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
            f"With {what} of at least {in_sample.threshold:.2f}, the call is labeled CLEARER. {held_text} "
            f"(The gap was chosen on leave-one-query-out pairs from the same queries, so this is still "
            "somewhat optimistic.) Smaller gaps are UNSURE. Still PREDICTED, not a Google rank."
        ),
        held_out=True,
    )


def _kind_margins(row, kind: str) -> list[PairMargin]:
    if kind == "pairs":
        return row.margins
    if kind == "adjacent":
        return row.adjacent
    raise ValueError(f"Unknown margin kind {kind!r}")


def calibrate(pairs: list[PairMargin], kind: str = "pairs") -> Calibration:
    """Smallest gap at which held-in cases are right at least 65% of the time, on at least 30 cases."""
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
            f"No threshold on {KIND_TEXT.get(kind, 'a score gap')} makes calls right at least 65% of the time on at least 30 cases "
            "on this backtest. Calls stay UNSURE.",
        )
    threshold, accuracy, coverage = chosen
    return Calibration(
        threshold,
        accuracy,
        coverage,
        (
            f"With {KIND_TEXT.get(kind, 'a score gap')} of at least {threshold:.2f}, calls were right {accuracy:.3f} of the time "
            f"on {coverage:.0%} of backtest cases. Smaller gaps are UNSURE."
        ),
    )


def pair_margins(rows: list[RankExample], scores: list[float], gated: set[int] | frozenset = frozenset()) -> list[PairMargin]:
    """Every labeled pair with different positions, except pairs with a page the 150-word rule placed."""
    found: list[PairMargin] = []
    for left in range(len(rows)):
        if id(rows[left]) in gated:
            continue
        for right in range(len(rows)):
            if id(rows[right]) in gated or rows[left].position >= rows[right].position:
                continue
            gap = abs(scores[left] - scores[right])
            found.append(PairMargin(gap=gap, correct=scores[left] > scores[right]))
    return found


def predicted_order(scores: list[float]) -> list[int]:
    """Indexes by score, highest first; equal scores keep input order (as the check does)."""
    return sorted(range(len(scores)), key=lambda index: (-scores[index], index))


def neighbor_gap(scores: list[float], order: list[int], index: int, gated: set[int] | frozenset = frozenset(), ids=None) -> tuple[float | None, list[int]]:
    """(smallest gap to a predicted neighbour, the neighbours used). Neighbours the 150-word rule
    placed are skipped; a page it placed itself has no gap (None). ``ids`` maps index -> id for
    the gated lookup (defaults to the index itself)."""
    key = ids or (lambda value: value)
    if key(index) in gated:
        return None, []
    place = order.index(index)
    used = [order[spot] for spot in (place - 1, place + 1) if 0 <= spot < len(order) and key(order[spot]) not in gated]
    if not used:
        return None, []
    return min(abs(scores[index] - scores[other]) for other in used), used


def adjacent_margins(rows: list[RankExample], scores: list[float], gated: set[int] | frozenset = frozenset()) -> list[PairMargin]:
    """One case per page: its smallest gap to a predicted neighbour, and whether the page is
    ordered right against every such neighbour with a different labeled position."""
    order = predicted_order(scores)
    found: list[PairMargin] = []
    for index in range(len(rows)):
        gap, used = neighbor_gap(scores, order, index, gated, ids=lambda value: id(rows[value]))
        used = [other for other in used if rows[other].position != rows[index].position]
        if gap is None or not used:
            continue
        gap = min(abs(scores[index] - scores[other]) for other in used)
        correct = all(
            (scores[index] > scores[other]) == (rows[index].position < rows[other].position)
            and scores[index] != scores[other]
            for other in used
        )
        found.append(PairMargin(gap=gap, correct=correct))
    return found


THIN_RULE_NOTE = (
    "This page is under 150 words, so a fixed rule placed it behind every fuller page. "
    "That placement is a rule, not a calibrated model call."
)


def rank_confidence(
    calibration: Calibration,
    examples: list[RankExample],
    raw_scores: dict[int, float],
    index: int,
    *,
    synthetic: bool = False,
) -> tuple[str, str]:
    """(label, note) for the predicted rank of ``examples[index]`` among ``examples`` (one query).

    ``raw_scores`` are the model's own scores (``predict_scores(..., gate=False)``). The order
    applies the 150-word rule; the gap is to the nearest predicted neighbour the rule did not
    place. A page the rule placed is always UNSURE. Use this instead of computing a gap by hand.
    """
    from aeo.ranksim.lambdamart import gate_thin_pages

    gated = gated_rows(examples)
    if id(examples[index]) in gated:
        return "UNSURE", THIN_RULE_NOTE
    ordered = gate_thin_pages(examples, raw_scores)
    order = sorted(range(len(examples)), key=lambda spot: (-ordered[id(examples[spot])], spot))
    raw = [raw_scores[id(example)] for example in examples]
    gap, _used = neighbor_gap(raw, order, index, gated, ids=lambda spot: id(examples[spot]))
    return calibration.product_label(gap, synthetic=synthetic, kind="adjacent")


def pair_confidence(
    calibration: Calibration,
    examples: list[RankExample],
    raw_scores: dict[int, float],
    left: int,
    right: int,
    *,
    synthetic: bool = False,
) -> str:
    """Head-to-head label for two pages of one query, from the model's own scores. A pair the
    150-word rule decided is UNSURE."""
    gated = gated_rows(examples)
    if id(examples[left]) in gated or id(examples[right]) in gated:
        return "UNSURE"
    gap = abs(raw_scores[id(examples[left])] - raw_scores[id(examples[right])])
    return calibration.product_label(gap, synthetic=synthetic, kind="pairs")[0]

