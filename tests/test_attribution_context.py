"""Attribution scores the old and new version of a page each against the same peers."""

from datetime import date

from aeo.ranksim.attribution import feature_moves
from aeo.ranksim.features import PageDoc

QUERY = "dirt work software"
TODAY = date(2026, 11, 10)


def _page(url, body, title="Dirt work software for crews"):
    return PageDoc(url=url, title=title, h1=title, body=body, word_count=len(body.split()))


PEERS = [_page(f"https://peer{index}.example/", f"generic construction tools list number {index} " * 40) for index in range(3)]
ORIGINAL = " ".join(f"crew{index} schedules dirt work jobs on site {index}" for index in range(20))  # within the 2,500 characters originality reads


def test_a_no_op_edit_moves_nothing():
    before = _page("https://ours.example/", ORIGINAL)
    after = _page("https://ours.example/", ORIGINAL)
    assert feature_moves(QUERY, "commercial", before, after, PEERS, today=TODAY) == ["No scored feature changed."]


def test_the_old_version_is_not_measured_against_its_own_copy():
    before = _page("https://ours.example/", ORIGINAL)
    after = _page("https://ours.example/", ORIGINAL + " plus a short new paragraph about grading crews and haul trucks")
    lines = feature_moves(QUERY, "commercial", before, after, PEERS, today=TODAY, limit=40)
    # Scored together, the before page shared nearly every trigram with its edited copy, so its
    # originality read about 0 and the edit looked like it created originality.
    assert not any(line.startswith("originality 0.0") for line in lines)
    originality = next((line for line in lines if line.startswith("originality")), None)
    assert originality is None or float(originality.split()[1]) > 0.9
