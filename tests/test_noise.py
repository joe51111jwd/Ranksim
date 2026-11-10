from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.noise import noise_ceiling


def _day(rows):
    return [SerpLabel("q", f"https://{name}.example/", position, None, "manual_csv") for name, position in rows]


def test_churn_is_visible():
    day1 = _day([("a", 1), ("b", 2), ("c", 3), ("d", 4), ("e", 5)])
    day2 = _day([("f", 1), ("a", 2), ("b", 3), ("c", 4), ("d", 5)])
    report = noise_ceiling(day1, day2)
    assert report.pairwise_agreement == 1.0  # shared URLs kept their order
    assert report.entered == 1 and report.dropped == 1
    assert report.mean_overlap == 4 / 5
    assert report.pairwise_with_churn < 1.0
    assert "not a day-to-day" not in report.note


def test_last_ranked_url_move_counts_and_is_not_identical():
    base = _day([("a", 1), ("b", 2), ("c", 3), ("d", 4)])
    moved = _day([("a", 1), ("b", 2), ("c", 3), ("d", 9)])
    report = noise_ceiling(base, moved)
    assert report.mean_abs_position_change == 5 / 4
    assert "not a day-to-day" not in report.note


def test_each_url_change_is_counted_once():
    base = _day([("a", 1), ("b", 2), ("c", 3), ("d", 4)])
    moved = _day([("b", 1), ("c", 2), ("d", 3), ("a", 6)])
    report = noise_ceiling(base, moved)
    assert report.mean_abs_position_change == (5 + 1 + 1 + 1) / 4
