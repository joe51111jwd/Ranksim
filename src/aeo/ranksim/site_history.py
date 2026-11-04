"""Site history: the PREDICTED slot that did best in the accuracy lab.

The accuracy lab (leave-one-search-out on the 16 SmartCrew searches) found one
signal that predicts where a page lands among a search's REAL Google results:
how many of the project's OTHER searches list the page's site in their REAL top
10. A pairwise logistic model on that single number put a hidden page closer to
its REAL slot than the content model, beyond noise in every setting the lab
tried (new URL: 1.83 vs 2.57 places off on average; brand new site: 1.43 vs
2.39). The content model could not be told apart from a random guess.

So this slot is content blind: editing the page text does not move it. The
board says so next to it, and prints this predictor's own backtest, measured
on the project's REAL results with site_history_backtest(), not hard coded.

Everything here works on REAL result lists only (search, position, url). No
stored HTML is needed, so results we could not crawl (HTTP 403, robots) still
count as sites that appear, and still get placed.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from aeo.crawl.urls import domain_of, normalize_url

SITE_HISTORY_DEPTH = 10
SITE_HISTORY_L2 = 1.0
DEFAULT_BAND = 3
MIN_BACKTEST_RESULTS = 3
METHOD_LABEL = "site history"
CONTENT_BLIND_NOTE = (
    "This slot comes from where your site and these sites appear across your searches. "
    "Editing the page text does not change it."
)
_SLD2 = {"co", "com", "org", "net", "gov", "ac", "edu"}


def site_key(url_or_host: str) -> str:
    """The registrable site used for counting: www and subdomains folded.

    https://www.hcss.com/x -> hcss.com, learn.g2.com -> g2.com, shop.example.co.uk -> example.co.uk.
    """
    text = str(url_or_host or "").strip()
    host = domain_of(text) if ("/" in text or ":" in text) else text
    labels = [part for part in (host or "").lower().strip(".").split(".") if part]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SLD2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def url_key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return str(url or "")


@dataclass(frozen=True)
class RealList:
    """The REAL results of one search: (REAL position, url) pairs."""

    search: str
    results: tuple[tuple[int, str], ...]


def top_results(entry: RealList, depth: int = SITE_HISTORY_DEPTH) -> list[tuple[int, str]]:
    return sorted(((int(pos), url) for pos, url in entry.results if 1 <= int(pos) <= depth), key=lambda item: item[0])


class SiteIndex:
    """Which sites each search lists in its REAL top `depth`, and through which URLs.

    count() answers "in how many searches does this site appear", leaving out
    some searches and one URL, in constant time per call.
    """

    def __init__(self, lists: Iterable[RealList], depth: int = SITE_HISTORY_DEPTH) -> None:
        self.depth = depth
        self.lists: list[RealList] = [entry for entry in lists]
        self.by_search: dict[str, dict[str, set[str]]] = {}
        self.base: Counter[str] = Counter()
        self.only_via: dict[str, list[str]] = defaultdict(list)
        for entry in self.lists:
            sites: dict[str, set[str]] = defaultdict(set)
            for _pos, url in top_results(entry, depth):
                sites[site_key(url)].add(url_key(url))
            self.by_search[entry.search] = dict(sites)
            for site, urls in sites.items():
                self.base[site] += 1
                if len(urls) == 1:
                    self.only_via[next(iter(urls))].append(entry.search)

    def count(self, site: str, exclude_searches: frozenset[str] | set[str] = frozenset(), exclude_url: str | None = None) -> int:
        """Searches whose REAL top results list `site`, not counting exclude_searches,
        and not counting a search where the site appears only through exclude_url."""
        total = self.base.get(site, 0)
        for search in exclude_searches:
            if site in self.by_search.get(search, {}):
                total -= 1
        if exclude_url is not None:
            key = url_key(exclude_url)
            if site_key(exclude_url) == site:
                for search in self.only_via.get(key, ()):
                    if search not in exclude_searches:
                        total -= 1
        return max(0, total)

    def searches_with_results(self) -> int:
        return sum(1 for entry in self.lists if top_results(entry, self.depth))


@dataclass
class SiteHistoryModel:
    intercept: float
    weight: float
    n_searches: int
    n_pairs: int

    def p_above(self, your_count: int, their_count: int) -> float:
        """PREDICTED probability that you rank above an incumbent, from site presence only."""
        z = self.intercept + self.weight * (math.log1p(your_count) - math.log1p(their_count))
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def fit_site_history(
    index: SiteIndex,
    *,
    skip_search: str | None = None,
    l2: float = SITE_HISTORY_L2,
) -> SiteHistoryModel | None:
    """Simulated insertions on the REAL results.

    In every search g other than skip_search, hide each REAL result h in turn and
    compare it with each other result j of g: x = log1p(count(site h)) -
    log1p(count(site j)), counting other searches only (never g or skip_search)
    and not counting h's own URL. y = 1 when h ranks above j. A logistic fit with
    an unpenalized intercept and L2 on the weight. Identical (x, y) pairs are
    pooled, so this is fast for hundreds of searches.
    """
    pooled: Counter[tuple[float, float]] = Counter()
    used = 0
    for entry in index.lists:
        if entry.search == skip_search:
            continue
        top = top_results(entry, index.depth)
        if len(top) < 2:
            continue
        used += 1
        excluded = frozenset({entry.search} | ({skip_search} if skip_search else set()))
        for h_pos, h_url in top:
            ch = index.count(site_key(h_url), excluded, h_url)
            for j_pos, j_url in top:
                if j_url == h_url and j_pos == h_pos:
                    continue
                cj = index.count(site_key(j_url), excluded, h_url)
                x = round(math.log1p(ch) - math.log1p(cj), 9)
                pooled[(x, 1.0 if h_pos < j_pos else 0.0)] += 1
    n_pairs = sum(pooled.values())
    if used < 2 or not n_pairs:
        return None
    b = w = 0.0
    for _ in range(50):
        gb = gw = hbb = hbw = hww = 0.0
        for (x, y), weight in pooled.items():
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, b + w * x))))
            r, s = (p - y) * weight, p * (1.0 - p) * weight
            gb += r
            gw += r * x
            hbb += s
            hbw += s * x
            hww += s * x * x
        gw += l2 * w
        hww += l2
        det = hbb * hww - hbw * hbw
        if det <= 1e-12:
            break
        db = (hww * gb - hbw * gw) / det
        dw = (hbb * gw - hbw * gb) / det
        b -= db
        w -= dw
        if max(abs(db), abs(dw)) < 1e-9:
            break
    return SiteHistoryModel(intercept=b, weight=w, n_searches=used, n_pairs=n_pairs)


@dataclass
class SiteSlot:
    position: int  # PREDICTED slot, 1 = above every REAL incumbent
    of: int  # len(incumbents) + 1
    low: int
    high: int  # position -/+ band, clamped to [1, of]
    your_site_searches: int
    expected_above: float
    p_above: list[float] = field(default_factory=list)  # per incumbent, in REAL order


def site_history_position(
    model: SiteHistoryModel | None,
    index: SiteIndex,
    your_url: str,
    search: str,
    incumbents: Sequence[str],
    *,
    band: int = DEFAULT_BAND,
) -> SiteSlot | None:
    """PREDICTED slot of your_url among `incumbents` (URLs in REAL order). None when there is nothing to place."""
    if model is None or not incumbents:
        return None
    excluded = frozenset({search})
    yours = index.count(site_key(your_url), excluded, your_url)
    probabilities = [model.p_above(yours, index.count(site_key(url), excluded, your_url)) for url in incumbents]
    expected_above = sum(1.0 - p for p in probabilities)
    size = len(incumbents)
    position = max(1, min(size + 1, int(math.floor(1.0 + expected_above + 0.5))))
    return SiteSlot(
        position=position,
        of=size + 1,
        low=max(1, position - band),
        high=min(size + 1, position + band),
        your_site_searches=yours,
        expected_above=round(expected_above, 3),
        p_above=[round(p, 4) for p in probabilities],
    )


@dataclass
class SlotBacktest:
    """The lab's insertion protocol, run on this project's REAL results. Every number is measured here.

    method names the predictor ("site history" or "content model"). n is the
    number of hidden pages, middle_* is always guessing the middle slot.
    """

    method: str = METHOD_LABEL
    mae: float | None = None
    exact: float | None = None
    within_1: float | None = None
    within_2: float | None = None
    top3: float | None = None
    n: int = 0
    n_searches: int = 0
    middle_mae: float | None = None
    middle_within_2: float | None = None
    new_site_mae: float | None = None  # sites in no other search through any URL
    new_site_middle_mae: float | None = None
    new_site_n: int = 0
    known_site_mae: float | None = None  # a page whose site is in other searches through other URLs
    known_site_middle_mae: float | None = None
    known_site_n: int = 0
    p80: int | None = None
    band: int = DEFAULT_BAND
    population: str = "REAL results"
    text: str = ""


SiteHistoryBacktest = SlotBacktest
NOT_RUN_BACKTEST = "not run: needs REAL results for at least 2 searches"


@dataclass
class SlotErrors:
    """Raw per-page errors, collected by a backtest and turned into a SlotBacktest."""

    errors: list[int] = field(default_factory=list)
    middles: list[int] = field(default_factory=list)
    top3_right: list[bool] = field(default_factory=list)
    new_errors: list[int] = field(default_factory=list)
    new_middles: list[int] = field(default_factory=list)
    known_errors: list[int] = field(default_factory=list)
    known_middles: list[int] = field(default_factory=list)
    searches: int = 0

    def add(self, predicted: int, true_slot: int, n_others: int, new_site: bool = False, known_site: bool = False) -> None:
        error = abs(int(predicted) - int(true_slot))
        middle = abs((n_others + 2) // 2 - int(true_slot))
        self.errors.append(error)
        self.middles.append(middle)
        self.top3_right.append((true_slot <= 3) == (predicted <= 3))
        if new_site:
            self.new_errors.append(error)
            self.new_middles.append(middle)
        if known_site:
            self.known_errors.append(error)
            self.known_middles.append(middle)

    def summary(self, method: str, population: str = "REAL results") -> SlotBacktest:
        if not self.errors:
            return SlotBacktest(method=method, population=population, text=NOT_RUN_BACKTEST)
        n = len(self.errors)
        ordered = sorted(self.errors)
        p80 = ordered[min(n - 1, max(0, math.ceil(0.8 * n) - 1))]
        result = SlotBacktest(
            method=method,
            mae=round(sum(self.errors) / n, 3),
            exact=round(sum(1 for e in self.errors if e == 0) / n, 3),
            within_1=round(sum(1 for e in self.errors if e <= 1) / n, 3),
            within_2=round(sum(1 for e in self.errors if e <= 2) / n, 3),
            top3=round(sum(self.top3_right) / n, 3),
            n=n,
            n_searches=self.searches,
            middle_mae=round(sum(self.middles) / n, 3),
            middle_within_2=round(sum(1 for e in self.middles if e <= 2) / n, 3),
            new_site_mae=round(sum(self.new_errors) / len(self.new_errors), 3) if self.new_errors else None,
            new_site_middle_mae=round(sum(self.new_middles) / len(self.new_middles), 3) if self.new_middles else None,
            new_site_n=len(self.new_errors),
            known_site_mae=round(sum(self.known_errors) / len(self.known_errors), 3) if self.known_errors else None,
            known_site_middle_mae=round(sum(self.known_middles) / len(self.known_middles), 3) if self.known_middles else None,
            known_site_n=len(self.known_errors),
            p80=p80,
            band=max(1, p80),
            population=population,
        )
        result.text = backtest_sentence(result)
        return result


def site_history_backtest(index: SiteIndex) -> SlotBacktest:
    """Hold out each search, hide each of its REAL results in turn, and predict its slot.

    For each search s with at least 3 REAL results: the model is fit without s.
    For each result h of s, the incumbents are the other results of s in REAL
    order, the true slot is 1 + incumbents above h, and the prediction is
    site_history_position(...). Reported: mean places off, exact, within 1 and 2
    places, top 3 called right, the same for always guessing the middle, and
    the subset of sites seen in no other search.
    """
    usable = [entry for entry in index.lists if len(top_results(entry, index.depth)) >= 2]
    if len(usable) < 2:
        return SlotBacktest(text=NOT_RUN_BACKTEST)
    found = SlotErrors()
    for entry in usable:
        top = top_results(entry, index.depth)
        if len(top) < MIN_BACKTEST_RESULTS:
            continue
        model = fit_site_history(index, skip_search=entry.search)
        if model is None:
            continue
        found.searches += 1
        for h_pos, h_url in top:
            others = [(pos, url) for pos, url in top if not (pos == h_pos and url == h_url)]
            true_slot = 1 + sum(1 for pos, _url in others if pos < h_pos)
            slot = site_history_position(model, index, h_url, entry.search, [url for _pos, url in others], band=0)
            if slot is None:
                continue
            site = site_key(h_url)
            new_site = index.count(site, frozenset({entry.search})) == 0
            known_site = index.count(site, frozenset({entry.search}), h_url) > 0
            found.add(slot.position, true_slot, len(others), new_site, known_site)
    return found.summary(METHOD_LABEL)


def backtest_sentence(result: SlotBacktest) -> str:
    if result.mae is None or result.within_2 is None:
        return result.text or NOT_RUN_BACKTEST
    who = "this predictor" if result.method == METHOD_LABEL else f"the {result.method}"
    text = (
        f"In backtests {who} put a hidden page within 2 places of its REAL Google position "
        f"{_pct(result.within_2)} of the time ({result.n} {result.population} over {result.n_searches} searches, "
        f"each search held out). It was off by {result.mae:.1f} places on average and exactly right "
        f"{_pct(result.exact)} of the time. Always guessing the middle is off by {result.middle_mae:.1f}"
        f" and within 2 places {_pct(result.middle_within_2)} of the time."
    )
    if result.new_site_n:
        text += (
            f" For sites that appear in no other search (through any page) it was off by {result.new_site_mae:.1f} "
            f"(the middle guess {result.new_site_middle_mae:.1f}, {result.new_site_n} pages)."
        )
    if result.known_site_n:
        text += (
            f" For a page whose site already ranks in other searches through other pages it was off by {result.known_site_mae:.1f} "
            f"(the middle guess {result.known_site_middle_mae:.1f}, {result.known_site_n} pages)."
        )
    return text


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{round(100 * value)}%"


def lists_from_pairs(rows: Iterable[tuple[str, Iterable[tuple[int, str]]]]) -> list[RealList]:
    return [RealList(search, tuple((int(pos), str(url)) for pos, url in results)) for search, results in rows]
