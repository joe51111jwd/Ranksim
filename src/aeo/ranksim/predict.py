"""Draft placement, persistence-anchored edits, and guidance.

Three ideas, kept separate so each can be tested and reported honestly:

1. A page that already ranks keeps its observed position as the anchor. An edit moves it
   by the change in a pairwise content score. Nothing is re-ranked from scratch.
2. A new page (a draft, or a page not in this search's results) is placed by a pairwise
   model: the PREDICTED position is the expected number of current pages that beat it.
   New pages start with a learned handicap (``entrant_logit``), because pages that newly
   enter a top 10 usually land below the pages already there.
3. Guidance compares the draft with the pages PREDICTED just above it, on the same
   features the model uses, and lists the few gaps most associated with ranking higher.
   It is guidance, not a guarantee.

Every number this module returns is PREDICTED. Backtests are labeled as backtests and
are never called accuracy.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

from aeo.textutil import salient_terms, tokenize_for_search

# -- features -------------------------------------------------------------------------------

CONTENT_FEATURES = (
    "title_match",
    "h1_match",
    "url_match",
    "heading_match",
    "body_match",
    "term_coverage",
    "words",
    "headings",
    "has_table",
    "has_faq",
    "schema",
    "links",
)
SITE_FEATURES = ("site_presence", "site_best")
ALL_FEATURES = CONTENT_FEATURES + SITE_FEATURES

# Plain names and how to describe a gap for each feature that a user can change.
FEATURE_TEXT = {
    "title_match": "search words in the title",
    "h1_match": "search words in the H1",
    "url_match": "search words in the URL",
    "heading_match": "search words in subheadings",
    "body_match": "search words in the body text",
    "term_coverage": "coverage of the terms the top pages share",
    "words": "word count",
    "headings": "number of subheadings",
    "has_table": "a comparison table",
    "has_faq": "an FAQ section",
    "schema": "structured data (schema.org)",
    "links": "number of links",
    "site_presence": "your site ranking for related searches",
    "site_best": "your site's best position on related searches",
}
ACTIONABLE = frozenset(CONTENT_FEATURES)

UNSURE = "UNSURE"
CLEARER = "CLEARER"
# A search's backtest must clear both of these before a call can be CLEARER.
CLEARER_MIN_PAIRWISE = 0.65
CLEARER_MIN_PAIRS = 30
# Within this many pairwise points of 0.5 the backtest is called near chance.
NEAR_CHANCE = 0.05

_SCHEMA = re.compile(r"application/ld\+json|itemtype\s*=", re.I)
_HEADING_TAG = re.compile(r"<h[2-6][\s>]", re.I)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def term_match(query: str, text: str) -> float:
    """Share of the search's salient words present in the text. 'dirt work' matches 'dirtwork'."""
    terms = salient_terms(query)
    if not terms:
        return 0.0
    tokens = set(tokenize_for_search(text or ""))
    compact = _compact(text)
    hits = 0
    for term in terms:
        if term in tokens or (len(term) >= 4 and term in compact):
            hits += 1
    phrase = _compact(query)
    bonus = 1.0 if phrase and len(phrase) >= 6 and phrase in compact else 0.0
    return min(1.0, hits / len(terms)) * 0.8 + 0.2 * bonus


def _url_text(url: str) -> str:
    return re.sub(r"[/\-_.:?=&]+", " ", re.sub(r"^https?://", "", url or ""))


def page_signals(query: str, doc, html: str | None, peers: Iterable = ()) -> dict[str, float]:
    """Interpretable content features for one page on one search. NaN means not observed."""
    from aeo.ranksim.features import topic_coverage

    peer_list = [peer for peer in peers if getattr(peer, "url", None) != doc.url]
    body = (doc.body or "")[:20000]
    heading_count = float(len(_HEADING_TAG.findall(html))) if html else float("nan")
    schema = (1.0 if _SCHEMA.search(html) else 0.0) if html else float("nan")
    return {
        "title_match": term_match(query, doc.title),
        "h1_match": term_match(query, doc.h1),
        "url_match": term_match(query, _url_text(doc.url)),
        "heading_match": term_match(query, doc.headings),
        "body_match": term_match(query, body),
        "term_coverage": topic_coverage(query, doc, peer_list) if peer_list else term_match(query, body),
        "words": math.log1p(max(0, int(doc.word_count or 0))),
        "headings": math.log1p(heading_count) if heading_count == heading_count else float("nan"),
        "has_table": 1.0 if doc.table_count else 0.0,
        "has_faq": 1.0 if doc.has_faq else 0.0,
        "schema": schema,
        "links": math.log1p(max(0, int(doc.link_count or 0))),
    }


def site_signals(domain: str, url_key: str, search: str, rows: Iterable[tuple[str, str, str, float]]) -> dict[str, float]:
    """How the page's site ranks on the other searches, leaving out this page and this search.

    ``rows`` are (search, domain, url_key, position). A draft has no history of its own, but the
    site it will be published on does. Leaving the page's own rows out keeps training honest:
    a training page is scored the way a brand new page on the same site would be.
    """
    positions = [
        float(position)
        for row_search, row_domain, row_key, position in rows
        if row_domain == domain and row_key != url_key and row_search != search
    ]
    if not positions:
        return {"site_presence": 0.0, "site_best": 0.0}
    return {"site_presence": math.log1p(len(positions)), "site_best": max(0.0, 11.0 - min(positions)) / 10.0}


# -- the pairwise model ---------------------------------------------------------------------


@dataclass
class PairwiseModel:
    """Logistic model of P(page a ranks above page b) from feature differences."""

    features: list[str]
    weights: list[float]
    means: list[float]
    scales: list[float]
    entrant_logit: float = 0.0
    l2: float = 1.0
    trained_on: str = ""
    pairs: int = 0
    notes: list[str] = field(default_factory=list)

    def vector(self, signals: dict[str, float]) -> np.ndarray:
        out = np.zeros(len(self.features))
        for index, name in enumerate(self.features):
            value = signals.get(name, float("nan"))
            if value is None or value != value:
                out[index] = 0.0  # missing is the mean: no push either way
            else:
                out[index] = (float(value) - self.means[index]) / self.scales[index]
        return out

    def score(self, signals: dict[str, float]) -> float:
        return float(np.dot(self.vector(signals), np.asarray(self.weights)))

    def p_above(self, a: dict[str, float], b: dict[str, float], *, a_is_new: bool = False) -> float:
        logit = self.score(a) - self.score(b) + (self.entrant_logit if a_is_new else 0.0)
        return _sigmoid(logit)

    def contributions(self, signals: dict[str, float]) -> dict[str, float]:
        vec = self.vector(signals)
        return {name: float(vec[index] * self.weights[index]) for index, name in enumerate(self.features)}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PairwiseModel":
        return cls(**data)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "PairwiseModel":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    z = math.exp(value)
    return z / (1.0 + z)


@dataclass
class Group:
    """One search: each page's signals and its observed position (1 is best)."""

    search: str
    signals: list[dict[str, float]]
    positions: list[float]
    urls: list[str] = field(default_factory=list)


def _pair_matrix(groups: list[Group], features: list[str], means: np.ndarray, scales: np.ndarray):
    rows = []
    for group in groups:
        mats = []
        for signals in group.signals:
            vec = np.array([signals.get(name, float("nan")) for name in features], dtype=float)
            vec = np.where(np.isnan(vec), means, vec)
            mats.append((vec - means) / scales)
        for i in range(len(group.signals)):
            for j in range(len(group.signals)):
                if group.positions[i] < group.positions[j]:
                    rows.append(mats[i] - mats[j])
    if not rows:
        return np.zeros((0, len(features)))
    return np.vstack(rows)


def fit_pairwise(groups: list[Group], features: Iterable[str], *, l2: float = 1.0, trained_on: str = "") -> PairwiseModel:
    """Fit a symmetric pairwise logistic model (no intercept) with an L2 penalty."""
    import warnings

    names = list(features)
    values = [
        [signals.get(name, float("nan")) for name in names] for group in groups for signals in group.signals
    ]
    matrix = np.array(values, dtype=float) if values else np.zeros((0, len(names)))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a feature never observed is all NaN
        means = np.nanmean(matrix, axis=0) if len(matrix) else np.zeros(len(names))
        scales = np.nanstd(matrix, axis=0) if len(matrix) else np.ones(len(names))
    means = np.where(np.isnan(means), 0.0, means)
    scales = np.where(np.isnan(scales) | (scales < 1e-9), 1.0, scales)
    diffs = _pair_matrix(groups, names, means, scales)
    weights = np.zeros(len(names))
    if len(diffs):
        # Each ordered pair appears as (above - below, 1). Symmetry gives (below - above, 0) for free:
        # the loss is sum log(1 + exp(-w.d)). Newton steps on that convex loss.
        for _ in range(50):
            margin = diffs @ weights
            prob = 1.0 / (1.0 + np.exp(-margin))
            grad = -diffs.T @ (1.0 - prob) + l2 * weights
            hess = (diffs * (prob * (1.0 - prob))[:, None]).T @ diffs + l2 * np.eye(len(names))
            step = np.linalg.solve(hess, grad)
            weights = weights - step
            if float(np.max(np.abs(step))) < 1e-7:
                break
    return PairwiseModel(
        features=names,
        weights=[float(value) for value in weights],
        means=[float(value) for value in means],
        scales=[float(value) for value in scales],
        l2=l2,
        trained_on=trained_on,
        pairs=int(len(diffs)),
    )


def fit_entrant_logit(model: PairwiseModel, pairs: list[tuple[dict, dict, bool]], offsets: list[float] | None = None) -> float:
    """Intercept for new pages: pairs are (entrant signals, returning signals, entrant ranked above).

    ``offsets`` lets the caller pass out-of-fold score differences so the intercept is not fit
    on in-sample model scores.
    """
    if not pairs:
        return 0.0
    if offsets is None:
        offsets = [model.score(a) - model.score(b) for a, b, _ in pairs]
    labels = np.array([1.0 if above else 0.0 for _, _, above in pairs])
    offs = np.array(offsets, dtype=float)
    bias = 0.0
    for _ in range(100):
        prob = 1.0 / (1.0 + np.exp(-(offs + bias)))
        grad = float(np.sum(prob - labels))
        hess = float(np.sum(prob * (1.0 - prob))) + 1e-9
        step = grad / hess
        bias -= step
        if abs(step) < 1e-9:
            break
    return float(bias)


# -- placement ------------------------------------------------------------------------------


def expected_position(model: PairwiseModel, page: dict[str, float], others: list[dict[str, float]], *, is_new: bool) -> float:
    """1 + expected number of the other pages that rank above this one. PREDICTED."""
    return 1.0 + sum(1.0 - model.p_above(page, other, a_is_new=is_new) for other in others)


def anchored_position(
    model: PairwiseModel,
    current_position: float,
    before: dict[str, float],
    after: dict[str, float],
    others: list[tuple[dict[str, float], float]],
    *,
    persistence_slope: float,
) -> dict:
    """A ranking page keeps its observed position. The edit moves it by the change in score.

    Each other page sits at its observed position. Persistence alone (no edit) puts the page
    exactly where it is today. The edit adds ``score(after) - score(before)`` to this page's
    log-odds against every other page, and the PREDICTED position moves by the change in the
    expected count of pages above it.
    """
    delta = model.score(after) - model.score(before)

    def expected(shift: float) -> float:
        total = 1.0
        for _signals, position in others:
            gap = persistence_slope * (position - current_position) + shift
            total += 1.0 - _sigmoid(gap)
        return total

    base = expected(0.0)
    moved = expected(delta)
    predicted = min(max(1.0, current_position + (moved - base)), max(float(len(others) + 1), float(current_position)))
    return {"now": float(current_position), "predicted": predicted, "score_change": delta}


# -- guidance -------------------------------------------------------------------------------


def guidance(
    model: PairwiseModel,
    draft: dict[str, float],
    above: list[dict[str, float]],
    *,
    limit: int = 4,
    stable_signs: dict[str, int] | None = None,
    raw_draft: dict | None = None,
    raw_above: list[dict] | None = None,
) -> list[dict]:
    """The few feature gaps to the pages just above that the model ties most to ranking higher.

    Only features a user can change, with a positive learned weight (and, when ``stable_signs``
    is given, the same sign in every training fold) are listed. Guidance, not a guarantee.
    """
    if not above:
        return []
    items = []
    for index, name in enumerate(model.features):
        if name not in ACTIONABLE:
            continue
        weight = model.weights[index]
        if weight <= 0:
            continue
        if stable_signs is not None and stable_signs.get(name, 0) != 1:
            continue
        mine = draft.get(name, float("nan"))
        values = [row.get(name, float("nan")) for row in above]
        values = [value for value in values if value == value]
        if mine != mine or not values:
            continue
        target = float(np.median(values))
        gap = (target - float(mine)) / model.scales[index]
        if gap <= 1e-9:
            continue
        items.append(
            {
                "feature": name,
                "label": FEATURE_TEXT.get(name, name),
                "gain": round(weight * gap, 4),
                "yours": _display(name, mine, raw_draft),
                "above": _display(name, target, None, raw_above),
                "text": _advice(name, mine, target, len(above), values, raw_draft, raw_above),
            }
        )
    items.sort(key=lambda item: -item["gain"])
    return items[:limit]


def _display(name: str, value: float, raw: dict | None, raw_list: list[dict] | None = None):
    if name == "words":
        if raw is not None and "word_count" in raw:
            return int(raw["word_count"])
        if raw_list:
            return int(np.median([row.get("word_count", 0) for row in raw_list]))
        return int(round(math.expm1(value)))
    if name == "headings":
        if raw is not None and "heading_count" in raw:
            return int(raw["heading_count"])
        if raw_list:
            return int(np.median([row.get("heading_count", 0) for row in raw_list]))
        return int(round(math.expm1(value)))
    if name == "links":
        if raw is not None and "link_count" in raw:
            return int(raw["link_count"])
        if raw_list:
            return int(np.median([row.get("link_count", 0) for row in raw_list]))
        return int(round(math.expm1(value)))
    if name in {"has_table", "has_faq", "schema"}:
        return "yes" if value >= 0.5 else "no"
    return round(float(value), 2)


def _advice(name: str, mine: float, target: float, count: int, values: list[float], raw: dict | None, raw_list: list[dict] | None) -> str:
    have = sum(1 for value in values if value >= 0.5)
    pages = f"{count} page{'s' if count != 1 else ''} above"
    if name == "title_match":
        return f"Put the search words in the title. The {pages} match more of them."
    if name == "h1_match":
        return f"Put the search words in the H1. The {pages} match more of them."
    if name == "url_match":
        return f"Use the search words in the URL. The {pages} match more of them."
    if name == "heading_match":
        return f"Use the search words in a subheading. The {pages} do more often."
    if name == "body_match":
        return f"Use every search word in the body text. The {pages} cover more of them."
    if name == "term_coverage":
        return f"Cover the terms the ranking pages share. The {pages} cover more of them."
    if name == "words":
        yours = _display("words", mine, raw)
        theirs = _display("words", target, None, raw_list)
        return f"Add depth. You have {yours} words. The {pages} have about {theirs}."
    if name == "headings":
        yours = _display("headings", mine, raw)
        theirs = _display("headings", target, None, raw_list)
        return f"Break the page into sections. You have {yours} subheadings. The {pages} have about {theirs}."
    if name == "has_table":
        return f"Add a table. {have} of the {pages} have one."
    if name == "has_faq":
        return f"Add an FAQ section. {have} of the {pages} have one."
    if name == "schema":
        return f"Add structured data (schema.org JSON-LD). {have} of the {pages} have it."
    if name == "links":
        yours = _display("links", mine, raw)
        theirs = _display("links", target, None, raw_list)
        return f"Link to more related pages. You have {yours} links. The {pages} have about {theirs}."
    return f"Close the gap on {FEATURE_TEXT.get(name, name)}."


# -- confidence -----------------------------------------------------------------------------


def confidence(backtest_pairwise: float | None, pairs: int | None) -> dict:
    """A per-search label from that search's backtest. The backtest is not accuracy."""
    if backtest_pairwise is None or not pairs:
        return {
            "label": UNSURE,
            "backtest_pairwise": None,
            "backtest_pairs": int(pairs or 0),
            "text": "No backtest for this search yet. Treat the PREDICTED position as a rough guide.",
        }
    near = abs(backtest_pairwise - 0.5) <= NEAR_CHANCE
    clearer = backtest_pairwise >= CLEARER_MIN_PAIRWISE and pairs >= CLEARER_MIN_PAIRS
    if clearer:
        text = f"Backtest on held-out searches ordered {backtest_pairwise:.0%} of {pairs} page pairs for this search. Still a prediction."
    elif near:
        text = f"Near chance: the backtest ordered {backtest_pairwise:.0%} of {pairs} page pairs for this search."
    else:
        text = f"The backtest ordered {backtest_pairwise:.0%} of {pairs} page pairs for this search. Not enough to be sure."
    return {
        "label": CLEARER if clearer else UNSURE,
        "backtest_pairwise": round(float(backtest_pairwise), 3),
        "backtest_pairs": int(pairs),
        "near_chance": near,
        "text": text,
    }


def raw_counts(doc, html: str | None) -> dict:
    return {
        "word_count": int(doc.word_count or 0),
        "heading_count": len(_HEADING_TAG.findall(html)) if html else 0,
        "link_count": int(doc.link_count or 0),
    }
