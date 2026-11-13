"""Page type, popularity, and link-rank joins stay free of captured position."""

import math

from aeo.ranksim.snapshot_protocol import paired_query_delta, signal_decision
from aeo.ranksim.snapshot_signals import cc_features, load_cc_domain_ranks, load_tranco, page_type_features, tranco_features


def test_page_type_uses_the_url_and_schema_not_a_rank():
    assert page_type_features("https://www.youtube.com/watch?v=1")["pt_video"] == 1.0
    assert page_type_features("https://www.reddit.com/r/estimators/comments/x")["pt_forum"] == 1.0
    assert page_type_features("https://learn.g2.com/hcss")["pt_review"] == 1.0
    assert page_type_features("https://help.hcss.com/s/article/Overview")["pt_docs"] == 1.0
    assert page_type_features("https://earthworkssoftware.com/")["pt_home"] == 1.0
    assert page_type_features("https://example.com/blog/hcss-alternative")["pt_article"] == 1.0
    html = """<html><script type="application/ld+json">{"@type":"SoftwareApplication","name":"Crew"}</script></html>"""
    assert page_type_features("https://example.com/pricing", html)["pt_schema_product"] == 1.0
    assert "position" not in page_type_features("https://example.com/pricing", html)


def test_tranco_popularity_is_higher_for_a_better_rank_and_zero_when_unlisted(tmp_path):
    path = tmp_path / "tranco.csv"
    path.write_text("1,google.com\n10,hcss.com\n", encoding="utf-8")
    ranks = load_tranco(path)
    listed = tranco_features("https://www.hcss.com/products", ranks)
    missing = tranco_features("https://dirtworksoftware.com/", ranks)
    assert listed["tranco_listed"] == 1.0
    assert listed["tranco_popularity"] == 1.0 / math.log2(11.0)
    assert missing == {"tranco_listed": 0.0, "tranco_popularity": 0.0}
    assert tranco_features("https://www.google.com/", ranks)["tranco_popularity"] > listed["tranco_popularity"]


def test_common_crawl_rank_stays_missing_when_the_domain_was_not_published(tmp_path):
    path = tmp_path / "cc.tsv"
    path.write_text(
        "domain\tharmonicc_pos\tharmonicc_val\tpr_pos\tpr_val\tn_hosts\n"
        "youtube.com\t5\t100\t5\t0.003\t20\n",
        encoding="utf-8",
    )
    ranks = load_cc_domain_ranks(path)
    found = cc_features("https://www.youtube.com/watch?v=1", ranks)
    assert found["cc_pagerank"] == 0.003
    missing = cc_features("https://exayard.com/", ranks)
    assert missing["cc_pagerank"] != missing["cc_pagerank"]
    assert missing["cc_harmonic"] != missing["cc_harmonic"]


def test_a_noisy_query_level_gain_is_not_adopted():
    baseline = {f"q{i}": 0.50 for i in range(20)}
    noisy = dict(baseline)
    noisy["q0"] = 0.90
    delta = paired_query_delta(baseline, noisy)
    decision = signal_decision(delta)
    assert decision["adopt"] is False
    stable = {f"q{i}": 0.60 for i in range(20)}
    adopted = signal_decision(paired_query_delta(baseline, stable))
    assert adopted["adopt"] is True
