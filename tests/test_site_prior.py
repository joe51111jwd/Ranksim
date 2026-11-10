"""The content-blind site prior and the persistence baseline run on the same folds as the model.
Synthetic labels."""

from aeo.ranksim.benchmark import default_scorers, has_prior_positions, run_benchmark, site_key, site_prior_scorer
from aeo.ranksim.lambdamart import RankExample


def _row(query, url, position, prior=0.0):
    return RankExample(query, url, float(position), {"bm25f": 0.0, "navboost_prior": prior})


def test_site_key_folds_www_and_subdomains():
    assert site_key("https://www.hcss.com/x") == "hcss.com"
    assert site_key("https://learn.g2.com/a") == "g2.com"
    assert site_key("https://shop.example.co.uk/") == "example.co.uk"


def test_the_site_prior_counts_training_queries_only():
    train = [_row("a", "https://www.big.example/1", 1), _row("a", "https://small.example/1", 2),
             _row("b", "https://blog.big.example/2", 1), _row("b", "https://other.example/", 2)]
    test = [_row("held", "https://big.example/new", 1), _row("held", "https://small.example/new", 2),
            _row("held", "https://unseen.example/", 3)]
    assert site_prior_scorer()(train, test) == [2.0, 1.0, 0.0]
    # The held-out query's own rows never count toward its sites.
    assert site_prior_scorer()([], test) == [0.0, 0.0, 0.0]


def test_persistence_is_offered_only_when_labels_carry_prior_positions():
    plain = [_row("q", f"https://p{index}.example/", index) for index in range(1, 4)]
    assert not has_prior_positions(plain) and "persistence" not in default_scorers(["bm25f"], 0, plain)
    with_prior = [_row("q", f"https://p{index}.example/", index, prior=1.0 / index) for index in range(1, 4)]
    assert "persistence" in default_scorers(["bm25f"], 0, with_prior)


def test_new_baselines_score_the_same_pairs_as_the_model():
    examples = []
    for q in range(6):
        for position in range(1, 5):
            examples.append(_row(f"query {q}", f"https://site{position}.example/q{q}", position, prior=1.0 / position))
    _clusters, results = run_benchmark(examples, protocols=("cluster",), bootstrap=0)
    by_name = {row.name: row for row in results[0].scorers}
    assert {"site_prior", "persistence"} <= set(by_name)
    assert by_name["site_prior"].n_pairs == by_name["lambdamart"].n_pairs == 6 * 6
    assert by_name["persistence"].pairwise.point == 1.0  # prior order equals the label order here
