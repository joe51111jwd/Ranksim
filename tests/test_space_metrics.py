"""Space metrics: top k only when more than k pages were scored, REAL top 10 means position 1 to
10, head-to-head records say whose they are, and competitors keep their kind. Synthetic data."""

from datetime import date

from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.space import build_space, read_competitor_kinds, render_space_text, write_competitors

OURS = "smartcrewhq.com"


class Model:
    def feature_name(self):
        return ["bm25f"]

    def predict(self, matrix):
        return [float(len(matrix) - index) for index in range(len(matrix))]


def _labels_and_pages(rows):
    labels, pages = [], {}
    for query, url, position in rows:
        labels.append(SerpLabel(query, url, position, None, "manual_csv"))
        pages[url] = PageDoc(url=url, title=query, h1=query, body=(query + " ") * 60, word_count=180)
    return labels, pages


def test_two_page_sets_are_not_top_three_and_position_35_is_not_top_ten():
    labels, pages = _labels_and_pages([
        ("small", f"https://www.{OURS}/a", 1), ("small", "https://rival.example/a", 35),
    ])
    report = build_space(labels, pages, {"small": Model()}, ["rival.example"], our_domain=OURS, today=date(2026, 11, 10))
    ours = next(row for row in report.shares if row.domain == OURS)
    rival = next(row for row in report.shares if row.domain == "rival.example")
    assert ours.predicted_top3_queries == 0 and ours.predicted_top3_qualified == 0  # 1st of 2 is not "top 3"
    assert ours.real_top10 == 1 and rival.real_top10 == 0  # position 35 is not top 10
    text = render_space_text(report)
    assert "PREDICTED n/a" in text


def test_head_to_head_says_whose_record_it_is_and_competitors_keep_their_kind(tmp_path):
    labels, pages = _labels_and_pages([
        ("q", f"https://www.{OURS}/a", 1), ("q", "https://rival.example/a", 35),
        ("q", "https://partner.example/a", 2), ("q", "https://other.example/a", 3), ("q", "https://more.example/a", 4),
    ])
    competitors_file = tmp_path / "competitors.csv"
    write_competitors(competitors_file, ["rival.example", "partner.example"], "file", {"rival.example": "business", "partner.example": "serp"})
    kinds = read_competitor_kinds(competitors_file)
    assert kinds == {"rival.example": "business", "partner.example": "serp"}
    report = build_space(labels, pages, {"q": Model()}, ["rival.example", "partner.example"], our_domain=OURS,
                         today=date(2026, 11, 10), kinds=kinds)
    rival = next(row for row in report.headtohead if row.domain == "rival.example")
    assert (rival.real_wins, rival.real_losses, rival.kind) == (1, 0, "business")  # our #1 against their #35
    text = render_space_text(report)
    assert f"REAL {OURS} won 1, lost 0, tied 0 of 1 queries" in text
    assert "rival.example\tbusiness" in text and "partner.example\tserp" in text
    ours = next(row for row in report.shares if row.domain == OURS)
    assert ours.predicted_top3_queries == 1  # five pages scored, so top 3 means something here
