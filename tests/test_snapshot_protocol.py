"""The published snapshot split stays frozen, and rank fields stay out of features."""

from aeo.ranksim.snapshot_protocol import (
    ADOPTION_MARGIN,
    adoption_decision,
    assert_no_rank_leak,
    assign_families,
    frozen_split,
    pair_counts,
    selective_pairs,
    summarize_queries,
)

# Manifest order of the 2026-11-06 thirty queries. No URLs and no page text.
MANIFEST = [
    "hcss alternative",
    "best all-in-one dirtworks software",
    "best heavy civil construction software",
    "hcss heavyjob alternative",
    "dirt work contractor software",
    "best heavy construction software",
    "b2w alternative",
    "excavation contractor software",
    "heavy civil field management software",
    "hcss heavybid alternative",
    "all in one heavy civil software",
    "all in one dirt work software",
    "all-in-one heavy civil construction software",
    "all in one dirtwork software",
    "all in one excavation software",
    "best all in one heavy civil software",
    "best all in one dirt work software",
    "all in 1 dirt work software",
    "best all in 1 dirt work software",
    "all in one dirt work management software",
    "best all in one dirt work management software",
    "best all-in-one dirt work management software",
    "dirt work management software",
    "best all-in-one dirt work and excavation software",
    "best all in one excavation software",
    "dirt work software with crm",
    "best excavation software",
    "excavation software",
    "excavation company software",
    "best dirt work software",
]

PUBLISHED_HOLDOUT = [
    "all in 1 dirt work software",
    "all in one dirt work software",
    "best all in 1 dirt work software",
    "best all in one dirt work software",
    "best all in one heavy civil software",
    "best all-in-one dirt work management software",
    "best heavy construction software",
    "dirt work management software",
    "dirt work software with crm",
    "excavation company software",
]


def test_frozen_split_matches_the_published_holdout():
    train, holdout = frozen_split(MANIFEST)
    assert holdout == PUBLISHED_HOLDOUT
    assert len(train) == 20
    assert set(train).isdisjoint(holdout)
    assert frozen_split(MANIFEST) == (train, holdout)


def test_near_duplicate_queries_stay_in_one_family():
    train, _holdout = frozen_split(MANIFEST)
    families = assign_families(train)
    dirt = families["best dirt work software"]
    assert families["all in one dirtwork software"] == dirt
    assert families["best all-in-one dirtworks software"] == dirt
    assert families["hcss alternative"] == families["hcss heavybid alternative"]
    assert families["hcss alternative"] != dirt
    assert families["excavation software"] != dirt
    assert len(set(families.values())) == 4


def test_pairwise_keeps_every_ordered_pair_and_does_not_count_a_tie():
    correct, wrong, ties = pair_counts([1, 2, 3], [3.0, 2.0, 2.0])
    assert (correct, wrong, ties) == (2, 0, 1)
    report = summarize_queries([([1, 2, 3], [3.0, 2.0, 2.0])])
    assert report["pairs"] == 3
    assert report["pairwise"] == round(2 / 3, 6)


def test_selective_accuracy_reports_coverage():
    groups = [([1, 2, 3, 4], [4.0, 0.1, 3.0, 0.0])]
    selected = selective_pairs(groups, keep_fraction=0.5)
    assert selected["pairs"] == 6
    assert selected["kept"] == 3
    assert selected["coverage"] == 0.5
    assert selected["pairwise"] is not None


def test_a_small_family_out_gain_is_not_adopted():
    rejected = adoption_decision(0.54, 0.52, 0.49)
    assert rejected["adopt"] is False
    assert ADOPTION_MARGIN == 0.05
    accepted = adoption_decision(0.60, 0.52, 0.49)
    assert accepted["adopt"] is True


def test_captured_position_is_rejected_as_a_feature():
    assert_no_rank_leak({"bm25f": 1.0, "navboost_prior": 0.0})
    try:
        assert_no_rank_leak({"bm25f": 1.0, "position": 1.0})
    except ValueError as exc:
        assert "position" in str(exc)
    else:
        raise AssertionError("position was accepted")
    try:
        assert_no_rank_leak({"navboost_prior": 0.4})
    except ValueError as exc:
        assert "navboost_prior" in str(exc)
    else:
        raise AssertionError("a previous rank was accepted")
