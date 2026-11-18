"""Position metrics for a known top 10. Every number is PREDICTED except the stability counts.

A predicted position is a rank in the candidate set, 1 best. A range is at most
three positions wide. Offsets and confidence thresholds are chosen on earlier
windows and then frozen. Outcome positions are labels.
"""

from __future__ import annotations

import random
from collections import defaultdict

from aeo.ranksim.pair_order import Row, bootstrap_ci

MAX_WIDTH = 3
RANGE_TARGET = 0.95
RANGE_REFERENCE = 0.90
POINT_METHODS = ("model_rank", "persistence_last", "pin_previous_1", "pin_previous_top3")


def last_position(row: Row) -> int | None:
    if not row.features.get("has_history"):
        return None
    return int(round(11.0 - float(row.features.get("last_relevance", 0.0))))


def ranks_from_scores(rows: list[Row], scores: list[float]) -> list[int]:
    """1 is the highest score. Ties break by URL so every row gets a position."""
    if len(rows) != len(scores):
        raise ValueError("scores and rows differ in length")
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[row.query].append(index)
    predicted = [0] * len(rows)
    for indexes in grouped.values():
        ordered = sorted(indexes, key=lambda index: (-float(scores[index]), rows[index].url))
        for rank, index in enumerate(ordered, start=1):
            predicted[index] = rank
    return predicted


def persistence_points(rows: list[Row]) -> list[int]:
    """Last position for a survivor. A new URL is predicted at 10."""
    return [10 if last_position(row) is None else int(last_position(row)) for row in rows]


def _assign_slots(rows: list[Row], scores: list[float], pinned: dict[int, int]) -> list[int]:
    """Place pinned row indexes at the given positions. Fill the other slots by score."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[row.query].append(index)
    predicted = [0] * len(rows)
    for indexes in grouped.values():
        used: dict[int, int] = {}
        n = len(indexes)
        claimed = []
        free = []
        for index in indexes:
            slot = pinned.get(index)
            if slot is None:
                free.append(index)
                continue
            slot = min(max(1, slot), n)
            while slot in used and slot < n:
                slot += 1
            if slot in used:
                free.append(index)
                continue
            used[slot] = index
            claimed.append(index)
        open_slots = [slot for slot in range(1, n + 1) if slot not in used]
        ordered = sorted(free, key=lambda index: (-float(scores[index]), rows[index].url))
        for slot, index in zip(open_slots, ordered, strict=True):
            predicted[index] = slot
        for slot, index in used.items():
            predicted[index] = slot
    return predicted


def pin_previous(rows: list[Row], scores: list[float], *, top: int) -> list[int]:
    """Keep yesterday's order for positions 1..top when those URLs are still present."""
    pinned = {}
    for index, row in enumerate(rows):
        previous = last_position(row)
        if previous is not None and previous <= top:
            pinned[index] = previous
    return _assign_slots(rows, scores, pinned)


def predict_positions(method: str, rows: list[Row], scores: list[float]) -> list[int]:
    if method == "model_rank":
        return ranks_from_scores(rows, scores)
    if method == "persistence_last":
        return persistence_points(rows)
    if method == "pin_previous_1":
        return pin_previous(rows, scores, top=1)
    if method == "pin_previous_top3":
        return pin_previous(rows, scores, top=3)
    raise ValueError(method)


def clip_range(pred: int, low_offset: int, high_offset: int) -> tuple[int, int, int]:
    """Inclusive range around a point. Width is at most 3, inside 1..10."""
    if high_offset < low_offset:
        raise ValueError("range offsets are reversed")
    if high_offset - low_offset > MAX_WIDTH - 1:
        raise ValueError("range is wider than 3 positions")
    low = max(1, min(10, int(pred) + low_offset))
    high = max(1, min(10, int(pred) + high_offset))
    if low > high:
        low, high = high, low
    # Clipping at the edge can only shrink the range.
    if high - low > MAX_WIDTH - 1:
        high = low + MAX_WIDTH - 1
    return low, high, high - low + 1


def _offset_grid() -> list[tuple[int, int]]:
    found = []
    for low in range(-(MAX_WIDTH - 1), MAX_WIDTH):
        for high in range(low, low + MAX_WIDTH):
            if high - low <= MAX_WIDTH - 1:
                found.append((low, high))
    return found


def _coverage_of(pairs: list[tuple[int, int]], low_offset: int, high_offset: int) -> tuple[float, float]:
    if not pairs:
        return 0.0, 0.0
    covered = 0
    width = 0
    for pred, real in pairs:
        low, high, span = clip_range(pred, low_offset, high_offset)
        width += span
        if low <= real <= high:
            covered += 1
    return covered / len(pairs), width / len(pairs)


def choose_offsets(pairs: list[tuple[int, int]], *, target: float = RANGE_TARGET) -> dict:
    """Smallest range, at most 3 wide, that covers the target share of calibration rows.

    If none reach the target, keep the highest coverage, then the narrower range.
    """
    best = None
    for low, high in _offset_grid():
        coverage, width = _coverage_of(pairs, low, high)
        reaches = coverage + 1e-12 >= target
        key = (1 if reaches else 0, -width if reaches else coverage, -width, -(abs(low) + abs(high)))
        if best is None or key > best[0]:
            best = (key, {"low_offset": low, "high_offset": high, "calibration_coverage": coverage, "calibration_mean_width": width})
    assert best is not None
    return best[1]


def _rule_at(pairs: list[tuple[int, int, str]], target: float) -> dict:
    """One global offset, or separate survivor and new offsets, chosen on calibration rows only."""
    global_pairs = [(pred, real) for pred, real, _bucket in pairs]
    global_rule = choose_offsets(global_pairs, target=target)
    by_bucket = {}
    for bucket in ("surviving", "new"):
        subset = [(pred, real) for pred, real, name in pairs if name == bucket]
        by_bucket[bucket] = choose_offsets(subset, target=target) if subset else dict(global_rule)
    split_covered = 0
    split_width = 0
    for pred, real, bucket in pairs:
        rule = by_bucket[bucket]
        low, high, span = clip_range(pred, rule["low_offset"], rule["high_offset"])
        split_width += span
        if low <= real <= high:
            split_covered += 1
    n = len(pairs) or 1
    split_coverage = split_covered / n
    split_mean = split_width / n
    use_split = split_coverage > global_rule["calibration_coverage"] + 1e-12 or (
        abs(split_coverage - global_rule["calibration_coverage"]) <= 1e-12 and split_mean < global_rule["calibration_mean_width"] - 1e-12
    )
    return {
        "by_bucket": use_split,
        "global": global_rule,
        "surviving": by_bucket["surviving"],
        "new": by_bucket["new"],
        "target": target,
        "calibration_coverage": split_coverage if use_split else global_rule["calibration_coverage"],
        "calibration_mean_width": split_mean if use_split else global_rule["calibration_mean_width"],
    }


def choose_range_rule(pairs: list[tuple[int, int, str]]) -> dict:
    """The applied range targets 95% coverage. The 90% range is stored for reference."""
    primary = _rule_at(pairs, RANGE_TARGET)
    primary["reference_90"] = _rule_at(pairs, RANGE_REFERENCE)
    return primary


def apply_ranges(preds: list[int], buckets: list[str], rule: dict) -> list[tuple[int, int, int]]:
    ranges = []
    for pred, bucket in zip(preds, buckets, strict=True):
        chosen = rule[bucket] if rule.get("by_bucket") else rule["global"]
        ranges.append(clip_range(pred, int(chosen["low_offset"]), int(chosen["high_offset"])))
    return ranges


def _per_query(rows: list[Row], values: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sum (numerator, denominator) inside each query. Order is sorted by query name."""
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row, value in zip(rows, values, strict=True):
        grouped[row.query].append(value)
    return [
        (sum(item[0] for item in grouped[query]), sum(item[1] for item in grouped[query]))
        for query in sorted(grouped)
    ]


def position_record(rows: list[Row], preds: list[int], ranges: list[tuple[int, int, int]]) -> dict:
    """Exact hit, within 1, MAE, and range coverage. Every row is in the denominator."""
    if not (len(rows) == len(preds) == len(ranges)):
        raise ValueError("position rows, predictions, and ranges differ in length")
    exact = []
    within = []
    errors = []
    covered = []
    widths = []
    for row, pred, (low, high, span) in zip(rows, preds, ranges, strict=True):
        if span > MAX_WIDTH or high < low:
            raise ValueError("a reported range is wider than 3 positions")
        real = int(round(row.position))
        hit = 1.0 if pred == real else 0.0
        near = 1.0 if abs(pred - real) <= 1 else 0.0
        exact.append((hit, 1.0))
        within.append((near, 1.0))
        errors.append((abs(pred - real), 1.0))
        covered.append((1.0 if low <= real <= high else 0.0, 1.0))
        widths.append((float(span), 1.0))
    def rate(pairs: list[tuple[float, float]]) -> float | None:
        total = sum(item[1] for item in pairs)
        if total == 0:
            return None
        return sum(item[0] for item in pairs) / total

    blocks = {}
    for name, mask in (
        ("all", lambda row: True),
        ("surviving", lambda row: row.bucket == "surviving"),
        ("new", lambda row: row.bucket == "new"),
    ):
        chosen = [index for index, row in enumerate(rows) if mask(row)]
        subset_rows = [rows[index] for index in chosen]
        blocks[name] = {
            "rows": len(chosen),
            "exact": rate([exact[index] for index in chosen]),
            "within_1": rate([within[index] for index in chosen]),
            "mae": rate([errors[index] for index in chosen]),
            "range_coverage": rate([covered[index] for index in chosen]),
            "mean_width": rate([widths[index] for index in chosen]),
            "exact_by_query": _per_query(subset_rows, [exact[index] for index in chosen]),
            "within_by_query": _per_query(subset_rows, [within[index] for index in chosen]),
            "mae_by_query": _per_query(subset_rows, [errors[index] for index in chosen]),
            "coverage_by_query": _per_query(subset_rows, [covered[index] for index in chosen]),
            "width_by_query": _per_query(subset_rows, [widths[index] for index in chosen]),
        }
    return blocks


def bootstrap_mean(query_sums: list[tuple[float, float]], *, draws: int = 1000, seed: int = 20261116) -> tuple[float | None, float | None]:
    return bootstrap_ci([(numerator, denominator) for numerator, denominator in query_sums], draws=draws, seed=seed)


def top_margins(rows: list[Row], scores: list[float], preds: list[int]) -> list[dict]:
    """One record per query: the predicted #1, its score gap over #2, and whether it is really #1."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[row.query].append(index)
    records = []
    for query in sorted(grouped):
        indexes = grouped[query]
        if len(indexes) < 2:
            continue
        top = min(indexes, key=lambda index: (preds[index], rows[index].url))
        second = min((index for index in indexes if index != top), key=lambda index: (preds[index], rows[index].url))
        records.append(
            {
                "query": query,
                "margin": float(scores[top]) - float(scores[second]),
                "correct": 1.0 if int(round(rows[top].position)) == 1 and preds[top] == 1 else 0.0,
                "called_rank": preds[top],
            }
        )
    return records


def choose_thresholds(records: list[dict], targets: tuple[float, ...] = (0.80, 0.90)) -> list[dict]:
    """Smallest score gap whose calibration precision meets each target, preferring more calls.

    A target the calibration set never reaches keeps the gap with the best precision,
    then the most calls.
    """
    if not records:
        return [{"target": target, "threshold": 0.0, "calibration_precision": None, "calibration_coverage": 0.0} for target in targets]
    margins = sorted({round(float(record["margin"]), 6) for record in records})
    # A call is margin >= threshold. Evaluate just below each observed gap so the gap itself qualifies.
    candidates = [min(margins) - 1.0] + margins
    chosen = []
    for target in targets:
        best = None
        for threshold in candidates:
            called = [record for record in records if record["margin"] + 1e-12 >= threshold]
            if not called:
                continue
            precision = sum(record["correct"] for record in called) / len(called)
            coverage = len(called) / len(records)
            reaches = precision + 1e-12 >= target
            key = (1 if reaches else 0, precision, coverage)
            if best is None or key > best[0]:
                best = (
                    key,
                    {
                        "target": target,
                        "threshold": threshold,
                        "calibration_precision": precision,
                        "calibration_coverage": coverage,
                        "calibration_calls": len(called),
                        "calibration_queries": len(records),
                    },
                )
        assert best is not None
        chosen.append(best[1])
    return chosen


def confident_top(records: list[dict], threshold: float) -> dict:
    called = [record for record in records if record["margin"] + 1e-12 >= threshold]
    queries = len(records)
    correct = sum(record["correct"] for record in called)
    by_query_precision = []
    by_query_coverage = []
    called_ids = {record["query"] for record in called}
    correct_ids = {record["query"] for record in called if record["correct"]}
    for record in records:
        made = 1.0 if record["query"] in called_ids else 0.0
        hit = 1.0 if record["query"] in correct_ids else 0.0
        by_query_coverage.append((made, 1.0))
        by_query_precision.append((hit, made))
    return {
        "queries": queries,
        "calls": len(called),
        "correct": int(correct),
        "precision": None if not called else correct / len(called),
        "coverage": None if not queries else len(called) / queries,
        "precision_by_query": by_query_precision,
        "coverage_by_query": by_query_coverage,
    }


def persistence_top_calls(day_rows: list[tuple[str, str, float]], next_rows: list[tuple[str, str, float]]) -> list[dict]:
    """Yesterday's #1 is the call on every query. It is correct only when that URL is still #1."""
    previous = {}
    for query, url, position in day_rows:
        if int(position) == 1:
            previous[query] = url
    today = {}
    for query, url, position in next_rows:
        today.setdefault(query, {})[url] = int(position)
    records = []
    for query, url in sorted(previous.items()):
        positions = today.get(query)
        if not positions or url not in positions:
            records.append({"query": query, "margin": 0.0, "correct": 0.0, "present": 0.0})
            continue
        records.append({"query": query, "margin": 1.0, "correct": 1.0 if positions[url] == 1 else 0.0, "present": 1.0})
    return records


def stability_table(snaps: list) -> list[dict]:
    """How often the same URL holds a top rank on the next snapshot. This describes REAL lists."""
    ordered = sorted(snaps, key=lambda snap: snap.day)
    rows = []
    for left, right in zip(ordered, ordered[1:]):
        left_at = _rank_map(left)
        right_at = _rank_map(right)
        right_top3 = {query: {url for url, pos in ranks.items() if pos <= 3} for query, ranks in _url_pos(right).items()}
        queries = sorted(set(left_at) & set(right_at))
        same = {1: 0, 2: 0, 3: 0}
        top3_kept = 0
        top3_slots = 0
        for query in queries:
            for rank in (1, 2, 3):
                left_url = left_at[query].get(rank)
                right_url = right_at[query].get(rank)
                if left_url and left_url == right_url:
                    same[rank] += 1
            for url, pos in _url_pos(left).get(query, {}).items():
                if pos > 3:
                    continue
                top3_slots += 1
                if url in right_top3.get(query, ()):
                    top3_kept += 1
        n = len(queries)
        rows.append(
            {
                "from": left.day,
                "to": right.day,
                "queries": n,
                "same_1": same[1],
                "same_2": same[2],
                "same_3": same[3],
                "top3_kept": top3_kept,
                "top3_slots": top3_slots,
                "rate_1": None if n == 0 else same[1] / n,
                "rate_2": None if n == 0 else same[2] / n,
                "rate_3": None if n == 0 else same[3] / n,
                "top3_rate": None if top3_slots == 0 else top3_kept / top3_slots,
            }
        )
    return rows


def _url_pos(snap) -> dict[str, dict[str, int]]:
    found: dict[str, dict[str, int]] = defaultdict(dict)
    for row in snap.rows:
        current = found[row.query].get(row.url_key)
        position = int(round(row.position))
        if current is None or position < current:
            found[row.query][row.url_key] = position
    return found


def _rank_map(snap) -> dict[str, dict[int, str]]:
    ranks: dict[str, dict[int, str]] = defaultdict(dict)
    for query, positions in _url_pos(snap).items():
        for url, position in positions.items():
            if position not in ranks[query]:
                ranks[query][position] = url
    return ranks


def early_exact(rows: list[Row], preds: list[int]) -> float:
    if not rows:
        return 0.0
    hit = sum(1 for row, pred in zip(rows, preds, strict=True) if int(round(row.position)) == pred)
    return hit / len(rows)


def select_point_method(bundles: list[tuple[list[Row], list[float]]]) -> str:
    """Highest exact-position accuracy on the early bundles. Simpler methods win a tie."""
    best_name = POINT_METHODS[0]
    best_key = None
    for name in POINT_METHODS:
        hits = 0
        total = 0
        near = 0
        error = 0.0
        for rows, scores in bundles:
            preds = predict_positions(name, rows, scores)
            for row, pred in zip(rows, preds, strict=True):
                real = int(round(row.position))
                total += 1
                hits += int(pred == real)
                near += int(abs(pred - real) <= 1)
                error += abs(pred - real)
        key = (hits / total if total else 0.0, near / total if total else 0.0, -(error / total if total else 0.0), -POINT_METHODS.index(name))
        if best_key is None or key > best_key:
            best_key = key
            best_name = name
    return best_name


def bootstrap_rate(pairs: list[tuple[float, float]], *, skip_empty: bool = False, draws: int = 1000, seed: int = 20261116) -> tuple[float | None, float | None]:
    """Query bootstrap of a ratio. Empty denominators can be skipped, as with precision when no call was made."""
    if not pairs:
        return None, None
    rng = random.Random(seed)
    stats: list[float] = []
    size = len(pairs)
    for _ in range(draws):
        numerator = denominator = 0.0
        for _draw in range(size):
            got, total = pairs[rng.randrange(size)]
            numerator += got
            denominator += total
        if denominator == 0:
            if skip_empty:
                continue
            stats.append(0.0)
        else:
            stats.append(numerator / denominator)
    if not stats:
        return None, None
    stats.sort()
    return stats[int((len(stats) - 1) * 0.025)], stats[int((len(stats) - 1) * 0.975)]


def html_split(rows: list[Row], preds: list[int]) -> dict:
    """Exact-position rate with HTML and without it. A diagnostic, not a selection score."""
    groups = {"present": [], "missing": []}
    for row, pred in zip(rows, preds, strict=True):
        key = "present" if row.features.get("content_present") else "missing"
        groups[key].append(1.0 if int(round(row.position)) == pred else 0.0)
    out = {}
    for key, values in groups.items():
        out[key] = {"rows": len(values), "exact": None if not values else sum(values) / len(values)}
    return out
