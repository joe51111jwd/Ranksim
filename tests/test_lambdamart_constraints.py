"""The monotone_up argument is applied. Omitting it still uses the product constraint set."""

from aeo.ranksim.features import MONOTONE_UP
from aeo.ranksim.lambdamart import RankExample, fit_lambdamart, predict_scores

NAMES = ["bm25f", "word_count"]


def _examples() -> list[RankExample]:
    rows = []
    for query in ("q1", "q2"):
        for position in (1, 2, 3):
            rows.append(
                RankExample(
                    query,
                    f"https://ex.example/{query}/{position}",
                    float(position),
                    {"bm25f": 1.0 / position, "word_count": 400 - position},
                )
            )
    return rows


def _constraints(model) -> list[int]:
    raw = model.params.get("monotone_constraints")
    if isinstance(raw, (list, tuple)):
        return [int(value) for value in raw]
    text = model.model_to_string()
    for line in text.splitlines():
        if line.startswith("monotone_constraints="):
            return [int(part) for part in line.split("=", 1)[1].split(",") if part != ""]
    raise AssertionError("monotone_constraints missing from the booster")


def test_omitted_monotone_matches_the_product_set_and_a_passed_set_is_used():
    rows = _examples()
    product = fit_lambdamart(rows, NAMES)
    explicit = fit_lambdamart(rows, NAMES, MONOTONE_UP)
    empty = fit_lambdamart(rows, NAMES, frozenset())
    expected = [1 if name in MONOTONE_UP else 0 for name in NAMES]
    assert _constraints(product) == expected
    assert _constraints(explicit) == expected
    assert _constraints(empty) == [0, 0]
    left = predict_scores(product, rows, NAMES)
    right = predict_scores(explicit, rows, NAMES)
    assert [left[id(row)] for row in rows] == [right[id(row)] for row in rows]
