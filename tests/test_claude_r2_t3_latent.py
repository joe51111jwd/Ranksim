"""Round 2 Target 3 instruments: the latent-order scorer and the permutation noise check.

Offline, synthetic pulls only. No database, no network.
"""

from __future__ import annotations

import json
import math

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")
pytest.importorskip("scipy")  # r2_t3_noise (imported by the noise check) needs it

from scripts.claude import r2_t3_latent as lt  # noqa: E402
from scripts.claude import r2_t3_noise_check as nc  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude.freeze import ROOT, sha256_file  # noqa: E402

DAY_A = "2026-11-16"
DAY_B = "2026-11-17"


def _key(name: str) -> str:
    return f"https://{name}/"


def _rows(pulls):
    """pulls: (day, hour, minute, [site names in rank order]). One source per pull."""
    records = []
    for number, (day, hour, minute, order) in enumerate(pulls):
        for index, name in enumerate(order):
            records.append(("q", day, f"{day}T{hour:02d}:{minute:02d}:00.000Z", index + 1, _key(name), f"src{number}"))
    frame = pd.DataFrame(records, columns=["query", "day", "fetched_at", "position", "url", "source"])
    frame["kind"] = "top10"
    frame["title"] = ""
    frame["key"] = frame["url"]
    frame["site"] = frame["url"].str.replace("https://", "").str.split("/").str[0]
    return frame


# Hand-checked case used by several tests.
#   Nov 14 (age 2, weight 0.5)       b a e f      (c and d both absent)
#   Nov 15 (age 1, weight 2 ** -0.5) b a c        (d absent)
#   Nov 16 = day A, one session      a b c d / b a d c / a b c d  -> consensus a 1, b 2, c 3, d 4
#   Nov 17 = day B                   b a c d / b a c d / a b d c  -> consensus b 1, a 2, c 3, d 4
HISTORY = [
    ("2026-11-14", 14, 0, ["b", "a", "e", "f"]),
    ("2026-11-15", 14, 0, ["b", "a", "c"]),
    (DAY_A, 14, 0, ["a", "b", "c", "d"]),
    (DAY_A, 14, 30, ["b", "a", "d", "c"]),
    (DAY_A, 15, 0, ["a", "b", "c", "d"]),
]
DAY_B_PULLS = [
    (DAY_B, 9, 0, ["b", "a", "c", "d"]),
    (DAY_B, 9, 30, ["b", "a", "c", "d"]),
    (DAY_B, 10, 0, ["a", "b", "d", "c"]),
]
W1 = 2**-0.5


def _b3(p: float) -> float:
    return p**3 + 3 * p**2 * (1 - p)


def _pair(frame, x, y):
    row = frame[(frame["key_x"] == _key(x)) & (frame["key_y"] == _key(y))]
    assert len(row) == 1
    return row.iloc[0]


# --------------------------------------------------------------------------- latent-order scorer


def test_half_life_weights_are_fixed_and_refuse_a_later_pull():
    assert lt.HALF_LIFE_DAYS == 2.0 and lt.PRIOR == 0.5
    assert lt.vote_weight(DAY_A, DAY_A) == 1.0
    assert lt.vote_weight("2026-11-15", DAY_A) == pytest.approx(W1)
    assert lt.vote_weight("2026-11-14", DAY_A) == pytest.approx(0.5)
    assert lt.vote_weight("2026-11-12", DAY_A) == pytest.approx(0.25)
    assert lt.vote_weight("2026-11-06", DAY_A) == pytest.approx(0.5**5)
    with pytest.raises(AssertionError):
        lt.vote_weight(DAY_B, DAY_A)


def test_pull_vote_counts_missing_as_11_and_skips_both_missing():
    ranks = {"x": 3, "y": 5}
    assert lt.pull_vote(ranks, "x", "y") == 1
    assert lt.pull_vote(ranks, "y", "x") == -1
    assert lt.pull_vote({"x": 10}, "x", "y") == 1
    assert lt.pull_vote({"y": 10}, "x", "y") == -1
    assert lt.pull_vote({"z": 1}, "x", "y") == 0


def test_p_flip_is_the_majority_of_three_with_a_jeffreys_share():
    assert lt.smoothed_share(0.0, 0.0) == 0.5
    assert lt.smoothed_share(1.0, 0.0) == pytest.approx(0.75)
    assert lt.smoothed_share(0.0, 3.0) == pytest.approx(0.125)
    assert lt.p_flip_from_share(0.0) == 0.0
    assert lt.p_flip_from_share(1.0) == 1.0
    assert lt.p_flip_from_share(0.5) == 0.5
    for p in np.linspace(0.01, 0.99, 25):
        majority = sum(math.comb(3, k) * p**k * (1 - p) ** (3 - k) for k in (2, 3))
        assert lt.p_flip_from_share(p) == pytest.approx(majority)
        assert lt.p_flip_from_share(1 - p) == pytest.approx(1 - lt.p_flip_from_share(p))


def test_vote_pooling_on_the_hand_checked_case():
    rows = _rows(HISTORY)
    frame = lt.latent_pairs(rows, DAY_A)
    assert len(frame) == 6  # four URLs in day A's top 10, all strictly ordered
    assert set(frame["n_pulls"]) == {5}

    ab = _pair(frame, "a", "b")  # keep: two day A pulls. reverse: one day A pull, Nov 15, Nov 14
    assert (ab["n_votes"], ab["n_rev"]) == (5, 3)
    assert ab["w_keep"] == pytest.approx(2.0)
    assert ab["w_rev"] == pytest.approx(1.0 + W1 + 0.5)
    assert ab["share_rev"] == pytest.approx((1.5 + W1 + 0.5) / (3.5 + W1 + 1))
    assert ab["p_flip"] == pytest.approx(_b3((2.0 + W1) / (4.5 + W1)))

    cd = _pair(frame, "c", "d")  # Nov 14 shows neither: no vote. Nov 15 shows c only: keep.
    assert (cd["n_votes"], cd["n_rev"]) == (4, 1)
    assert cd["w_keep"] == pytest.approx(2.0 + W1)
    assert cd["w_rev"] == pytest.approx(1.0)
    assert cd["p_flip"] == pytest.approx(_b3(1.5 / (4.0 + W1)))

    ac = _pair(frame, "a", "c")  # c missing on Nov 14 counts as 11: a above c
    assert (ac["n_votes"], ac["n_rev"]) == (5, 0)
    assert ac["w_keep"] == pytest.approx(3.0 + W1 + 0.5)
    assert ac["p_flip"] == pytest.approx(_b3(0.5 / (4.5 + W1)))


def test_rows_after_day_a_are_refused():
    rows = _rows(HISTORY + DAY_B_PULLS)
    with pytest.raises(AssertionError):
        lt.latent_pairs(rows, DAY_A)
    with pytest.raises(AssertionError):
        lt.assert_history(rows, DAY_A)
    lt.assert_history(_rows(HISTORY), DAY_A)


def test_r1_and_r2_on_hand_checked_values():
    p = np.array([0.9, 0.6, 0.3, 0.2, 0.1])
    assert lt.r1_calls(p).tolist() == [True, True, False, False, False]
    # running means 0.9, 0.75, 0.6, 0.5, 0.42: the first four
    assert lt.r2_calls(p).tolist() == [True, True, True, True, False]
    # order of the input does not matter
    shuffled = np.array([0.2, 0.9, 0.1, 0.3, 0.6])
    assert lt.r2_calls(shuffled).tolist() == [True, True, False, True, True]
    # a tie group is taken or left whole: 0.8 alone is 0.8, with both 0.3s it is 0.467
    assert lt.r2_calls(np.array([0.3, 0.8, 0.3])).tolist() == [False, True, False]
    # exactly one half is called by R2 (at least one half) and not by R1 (above one half)
    assert lt.r1_calls(np.array([0.5])).tolist() == [False]
    assert lt.r2_calls(np.array([0.5, 0.5])).tolist() == [True, True]
    # nothing reaches one half: no calls
    assert lt.r2_calls(np.array([0.4, 0.1])).tolist() == [False, False]
    assert lt.r2_calls(np.array([])).tolist() == []
    # R2 always holds R1
    rng = np.random.default_rng(1)
    for _ in range(50):
        values = rng.beta(0.5, 2.0, size=30)
        assert not (lt.r1_calls(values) & ~lt.r2_calls(values)).any()
        called = values[lt.r2_calls(values)]
        assert called.size == 0 or called.mean() >= 0.5


def test_score_transition_and_summary_on_the_hand_checked_case():
    rows = _rows(HISTORY + DAY_B_PULLS)
    cons = rs.consensus_table(rows)
    frame = lt.score_transition(rows, cons, DAY_A, DAY_B)
    assert len(frame) == 6
    assert set(frame.columns) >= {"query", "key_x", "key_y", "p_flip", "call_r1", "call_r2", "flipped"}
    assert int(frame["flipped"].sum()) == 1 and bool(_pair(frame, "a", "b")["flipped"])
    # (a, b): P(flip) about 0.530, the only pair above one half; (c, d) 0.240; the rest 0.026
    assert _pair(frame, "a", "b")["p_flip"] > 0.5
    assert frame["call_r1"].tolist() == frame["call_r2"].tolist()
    assert int(frame["call_r1"].sum()) == 1 and bool(_pair(frame, "a", "b")["call_r1"])
    summary = lt.summarise(frame)
    assert summary["pairs"] == 6 and summary["flips"] == 1
    for rule in ("r1", "r2"):
        block = summary[rule]
        assert (block["calls"], block["right"]) == (1, 1)
        assert (block["flip_recall"]["hits"], block["flip_recall"]["n"]) == (1, 1)
        assert (block["precision"]["hits"], block["precision"]["n"]) == (1, 1)
        assert block["all_pairs_minus_persistence"]["diff"] == pytest.approx(1 / 6)
    assert summary["expected_flips_model"] == pytest.approx(frame["p_flip"].sum())


def test_day_b_pulls_never_change_a_probability_or_a_call():
    one = _rows(HISTORY + DAY_B_PULLS)
    other_b = [(DAY_B, 9, 0, ["d", "c", "b", "a"]), (DAY_B, 9, 30, ["d", "c", "b", "a"]), (DAY_B, 10, 0, ["c", "d", "a", "b"])]
    two = _rows(HISTORY + other_b)
    first = lt.score_transition(one, rs.consensus_table(one), DAY_A, DAY_B)
    second = lt.score_transition(two, rs.consensus_table(two), DAY_A, DAY_B)
    merged = first.merge(second, on=["query", "key_x", "key_y"], suffixes=("_1", "_2"))
    assert len(merged) == 6
    assert (merged["p_flip_1"] == merged["p_flip_2"]).all()
    assert (merged["call_r1_1"] == merged["call_r1_2"]).all()
    assert (merged["call_r2_1"] == merged["call_r2_2"]).all()
    assert merged["flipped_2"].all()  # every pair reversed in the second outcome


def test_r2_budget_runs_over_day_a_universe_not_the_scored_pairs():
    # e drops out of day B's top 10, so pairs with e are not scored, but they still count in R2's budget.
    history = [
        (DAY_A, 14, 0, ["a", "b", "e"]),
        (DAY_A, 14, 30, ["a", "e", "b"]),
        (DAY_A, 15, 0, ["e", "a", "b"]),
    ]
    day_b = [(DAY_B, 9, 0, ["a", "b", "x"]), (DAY_B, 9, 30, ["a", "b", "x"]), (DAY_B, 10, 0, ["a", "b", "x"])]
    rows = _rows(history + day_b)
    universe = lt.transition_calls(rows, DAY_A, ["q"])
    assert len(universe) == 3
    frame = lt.score_transition(rows, rs.consensus_table(rows), DAY_A, DAY_B)
    assert len(frame) == 1  # only (a, b) survives into day B
    called = universe.set_index(["key_x", "key_y"])["call_r2"]
    assert frame["call_r2"].tolist() == [bool(called[(_key("a"), _key("b"))])]


def _manifest(directory, files: dict[str, str]):
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({"sha256": files}), encoding="utf-8")
    return directory


@pytest.mark.parametrize("module", [lt, nc])
def test_score_refuses_without_a_verified_prereg(module, tmp_path):
    base = ["score", "--day-a", "2026-11-18", "--day-b", "2026-11-19", "--max-date", "2026-11-19", "--out", str(tmp_path / "out")]
    with pytest.raises(SystemExit, match="prereg"):
        module.main(base)
    with pytest.raises(SystemExit, match="No manifest"):
        module.main(base + ["--prereg-dir", str(tmp_path)])
    bad = _manifest(tmp_path / "bad", {"scripts/claude/rsdata.py": "0" * 64})
    with pytest.raises(SystemExit, match="do not match"):
        module.main(base + ["--prereg-dir", str(bad)])
    other = _manifest(tmp_path / "other", {"scripts/claude/rsdata.py": sha256_file(ROOT / "scripts/claude/rsdata.py")})
    with pytest.raises(SystemExit, match="does not freeze"):
        module.main(base + ["--prereg-dir", str(other)])
    assert not (tmp_path / "out").exists()


def test_score_arguments_keep_the_seal():
    lt.check_score_args("2026-11-18", "2026-11-19", "2026-11-19")
    with pytest.raises(SystemExit):
        lt.check_score_args("2026-11-18", "2026-11-19", "2026-11-20")  # nothing after day B
    with pytest.raises(SystemExit):
        lt.check_score_args("2026-11-19", "2026-11-18", "2026-11-18")
    with pytest.raises(SystemExit):
        lt.check_score_args(DAY_A, DAY_B, DAY_B)  # design days go through `design`


# --------------------------------------------------------------------------- permutation noise check

# Two URLs, two pulls a day. Day A: a b, a b. Day B: b a, b a. The real split flips the pair.
# The six ways to choose pseudo day A: {0,1} and {2,3} flip (1 pair, 1 flip); the four mixed
# splits give a and b the same median (positions 1 and 2 round up to 2) on both pseudo days,
# so the pair is tied and not scored. Expected under shuffling: 2/6 flips on 2/6 pairs.
TWO_BY_TWO = ([{"a": 1, "b": 2}, {"a": 1, "b": 2}], [{"b": 1, "a": 2}, {"b": 1, "a": 2}])


def test_split_count_and_exact_null_on_the_tiny_case():
    day_a_votes, day_b_votes = TWO_BY_TWO
    pool = day_a_votes + day_b_votes
    assert nc.split_count(pool, (0, 1)) == (1, 1)
    assert nc.split_count(pool, (2, 3)) == (1, 1)
    for subset in ((0, 2), (0, 3), (1, 2), (1, 3)):
        assert nc.split_count(pool, subset) == (0, 0)
    flips, pairs = nc.exact_null(day_a_votes, day_b_votes)
    assert flips == pytest.approx(1 / 3) and pairs == pytest.approx(1 / 3)


def test_unequal_pull_counts_are_kept_per_day():
    # One pull on day A (a b), two on day B (b a, b a). Pseudo day A is always one pull:
    # only the real split scores the pair, so the exact expectation is 1/3 flips on 1/3 pairs.
    day_a_votes, day_b_votes = [{"a": 1, "b": 2}], [{"b": 1, "a": 2}, {"b": 1, "a": 2}]
    assert nc.exact_null(day_a_votes, day_b_votes) == (pytest.approx(1 / 3), pytest.approx(1 / 3))
    flips, pairs = nc.permutation_null({"q": (day_a_votes, day_b_votes)}, permutations=600)
    assert set(np.unique(flips)) <= {0.0, 1.0} and (flips == pairs).all()
    assert flips.mean() == pytest.approx(1 / 3, abs=0.06)


def test_permutation_null_is_seeded_and_the_noise_share_is_hand_checked():
    searches = {"q": TWO_BY_TWO}
    flips, pairs = nc.permutation_null(searches)
    again, _ = nc.permutation_null(searches)
    assert flips.shape == (nc.PERMUTATIONS, 1) and (flips == again).all()
    assert flips.mean() == pytest.approx(1 / 3, abs=0.04)
    table = nc.search_table(searches, flips, pairs)
    assert (int(table.loc["q", "obs_f"]), int(table.loc["q", "obs_p"])) == (1, 1)
    summary = nc.summarise_noise(table, flips, pairs)
    assert summary["observed"] == {"flips": 1, "pairs": 1, "rate": 1.0}
    assert summary["noise_share_of_flip_count"]["value"] == pytest.approx(flips.mean())
    assert summary["exact_check"]["expected_flips"] == pytest.approx(1 / 3)
    assert summary["exact_check"]["noise_share_of_flip_count"]["value"] == pytest.approx(1 / 3)
    assert summary["point_below_falsifier_0.7"]


def test_noise_check_reads_voting_pulls_and_matches_flip_pairs():
    pulls = [
        (DAY_A, 8, 0, ["c", "b", "a"]),  # a morning pull outside the voting session: not shuffled
        (DAY_A, 14, 0, ["a", "b", "c"]),
        (DAY_A, 14, 30, ["a", "b", "c"]),
        (DAY_B, 9, 0, ["b", "a", "c"]),
        (DAY_B, 9, 30, ["b", "a", "c"]),
    ]
    rows = _rows(pulls)
    cons = rs.consensus_table(rows)
    searches = nc.voting_pulls(rows, DAY_A, DAY_B, ["q"])
    assert [len(day) for day in searches["q"]] == [2, 2]
    flips, pairs = nc.permutation_null(searches, permutations=50)
    table = nc.search_table(searches, flips, pairs)
    nc.check_observed(table, cons, DAY_A, DAY_B)
    assert (int(table.loc["q", "obs_f"]), int(table.loc["q", "obs_p"])) == (1, 3)
