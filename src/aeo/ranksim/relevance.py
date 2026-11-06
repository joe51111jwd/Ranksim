"""On-topic check: does a page cover a search the way the pages Google ranks for it do?

Google's top 10 is already filtered for relevance, so within it the accuracy lab
found no on-page signal that orders pages. What the REAL top 10 pages do share is
that they are about the search. This check compares a page with the stored REAL
top 10 pages of each search on three plain things:

1. Search words: how many of the search's words the page uses (title, headings
   and text). The page needs at least as many as the readable REAL top 10 page
   that uses the fewest, and never more than half of them.
2. Topic match: how close its vocabulary is to the REAL top 10 pages (a TF-IDF
   cosine to their average). The floor is half the lowest match any readable
   REAL top 10 page has with the others.
3. Repetition: the share of its 3-word sequences that are distinct. Keyword
   stuffed pages repeat themselves; the floor sits far below the most
   repetitive REAL top 10 page.

A page that fails is PREDICTED not to make the top 10 for that search. The
check is backtested on the REAL pages themselves (each one checked against the
others), so the board can say how many pages Google did rank would pass it. It
has never been tested on pages that failed to rank, because the data holds
none; the board says that too.

Page text is data. Nothing here follows instructions written inside a page.
"""

from __future__ import annotations

import math
from collections import Counter
from functools import lru_cache
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from aeo.ranksim.features import PageDoc
from aeo.textutil import STOPWORDS, salient_terms, tokenize

READABLE_WORDS = 30
REPETITION_FLOOR = 0.2
TOPIC_MARGIN = 0.5
TOPIC_FLOOR_DEFAULT = 0.02
# Under the clone-similarity floor, a page is uncertain when it still matches this
# search more than every other stored search. With only one stored search there is
# no comparison, so a low cosine is unvalidated, not an off-topic decision.
# A near-zero score stays off topic when another search exists and this page is
# not closer to the query. The word-count floor and TOPIC_MARGIN are unchanged.
TOPIC_SEPARATION = 0.02
MIN_READABLE = 3
TITLE_NORM_SHARE = 0.6
# Modifiers a to-do item should not ask a page to "cover" on their own: 'all in one' or 'best' are phrasing, not topics.
GENERIC_WORDS = frozenset(
    {"all", "one", "best", "top", "free", "new", "good", "great", "cheap", "online", "near", "list", "review", "reviews", "versus"}
)
_SUFFIXES = ("ings", "ing", "ions", "ion", "es", "s")
CHECK_LABEL = "on-topic check"
UNTESTED_NOTE = (
    "This check has only been tested on pages Google did rank; the data holds no pages that failed to rank, "
    "so how often it is right about an off-topic page is not measured."
)


@lru_cache(maxsize=200_000)
def stem(token: str) -> str:
    """A light stem so 'alternatives' matches 'alternative' and 'excavating' matches 'excavat'."""
    for suffix in _SUFFIXES:
        if len(token) - len(suffix) >= 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def search_words(search: str) -> list[str]:
    """The words of a search that a page should use: no stopwords, no 1 or 2 letter words."""
    return salient_terms(search)


def _page_text(doc: PageDoc) -> str:
    return f"{doc.title} {doc.h1} {doc.headings} {doc.body}"


def _vocab(text: str) -> tuple[set[str], set[str]]:
    tokens = tokenize(text)
    stems = {stem(token) for token in set(tokens)}
    joined = {tokens[index] + tokens[index + 1] for index in range(len(tokens) - 1)}
    return stems, joined


def words_found(words: Sequence[str], text: str, vocab: tuple[set[str], set[str]] | None = None) -> tuple[list[str], list[str]]:
    """Which search words appear in text. 'heavyjob' also matches 'heavy job', 'dirtwork' matches 'dirt work'."""
    stems, joined = vocab if vocab is not None else _vocab(text)
    found = [word for word in words if stem(word) in stems or word in joined]
    missing = [word for word in words if word not in found]
    return found, missing


def repetition(doc: PageDoc) -> float | None:
    """Distinct 3-word sequences over all 3-word sequences in the first 5000 words. None when too short to tell."""
    tokens = tokenize(doc.body or "")[:5000]
    if len(tokens) < READABLE_WORDS:
        return None
    grams = [tuple(tokens[index : index + 3]) for index in range(len(tokens) - 2)]
    return len(set(grams)) / len(grams)


def _repetition_floor(values: Sequence[float]) -> float:
    """Half the repetition score of the most repetitive REAL page, and never above REPETITION_FLOOR."""
    return min(REPETITION_FLOOR, TOPIC_MARGIN * min(values)) if values else REPETITION_FLOOR


def readable(doc: PageDoc | None) -> bool:
    return doc is not None and int(doc.word_count or 0) >= READABLE_WORDS


def _counts(doc: PageDoc) -> Counter:
    raw = Counter(token for token in tokenize(_page_text(doc)) if len(token) > 2 and token not in STOPWORDS)
    counts: Counter = Counter()
    for token, n in raw.items():
        counts[stem(token)] += n
    return counts


@dataclass
class TopicCheck:
    """The PREDICTED on-topic check for one page on one search."""

    search: str
    on_topic: bool | None  # None: not checked (no readable text, or nothing stored to compare with)
    status: str  # 'on topic' | 'off topic' | 'repetitive' | 'not checked' | 'uncertain'
    words: list[str] = field(default_factory=list)
    found: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    need: int = 0
    topic_score: float | None = None
    topic_floor: float | None = None
    repetition: float | None = None
    repetition_floor: float = REPETITION_FLOOR
    title_found: list[str] = field(default_factory=list)
    title_missing: list[str] = field(default_factory=list)
    real_title_share: float | None = None  # REAL: share of the REAL top 10 titles that hold every search word
    real_titles: int = 0
    compared_with: int = 0
    text: str = ""


@dataclass
class TopicBacktest:
    """Each readable REAL top 10 page checked against the other pages of its search."""

    pages: int = 0
    passed: int = 0
    failed_words: int = 0
    failed_topic: int = 0
    failed_repetition: int = 0
    lowest_repetition: float | None = None
    topic_floor: float = TOPIC_FLOOR_DEFAULT
    text: str = ""


class TopicModel:
    """Built once per project from the stored REAL top 10 pages of each search."""

    def __init__(self, pages_by_search: dict[str, list[PageDoc]], titles_by_search: dict[str, list[str]] | None = None) -> None:
        self.titles_by_search = {search: list(titles) for search, titles in (titles_by_search or {}).items()}
        self.readable: dict[str, list[PageDoc]] = {}
        unique: dict[str, PageDoc] = {}
        for search, docs in pages_by_search.items():
            seen: set[str] = set()
            kept = []
            for doc in docs:
                if not readable(doc) or doc.url in seen:
                    continue
                seen.add(doc.url)
                kept.append(doc)
                unique.setdefault(doc.url, doc)
            self.readable[search] = kept
        self.n_docs = len(unique)
        self.df: Counter = Counter()
        counts = {url: _counts(doc) for url, doc in unique.items()}
        for value in counts.values():
            self.df.update(set(value))
        self.vectors = {url: self._weigh(value) for url, value in counts.items()}
        vocabs = {url: _vocab(_page_text(doc)) for url, doc in unique.items()}
        self.found_counts: dict[str, dict[str, int]] = {
            search: {doc.url: len(words_found(search_words(search), "", vocabs[doc.url])[0]) for doc in docs}
            for search, docs in self.readable.items()
        }
        self.sums: dict[str, Counter] = {}
        for search, docs in self.readable.items():
            total: Counter = Counter()
            for doc in docs:
                total.update(self.vectors[doc.url])
            self.sums[search] = total
        self._page_cache: dict[tuple, tuple] = {}
        self.repetitions = {url: repetition(doc) for url, doc in unique.items()}
        known = [value for value in self.repetitions.values() if value is not None]
        self.lowest_repetition = min(known) if known else None
        self.repetition_floor = _repetition_floor(known)
        self.backtest = self._backtest()

    def _page(self, doc: PageDoc) -> tuple:
        """Vocabulary, title vocabulary, TF-IDF vector and repetition of a page, computed once per page."""
        key = (id(doc), doc.url, len(doc.body or ""), doc.title, doc.h1, len(doc.headings or ""))
        found = self._page_cache.get(key)
        if found is None:
            if len(self._page_cache) > 16:
                self._page_cache.clear()
            found = (
                _vocab(_page_text(doc)),
                _vocab(f"{doc.title} {doc.h1}"),
                self._weigh(_counts(doc)),
                repetition(doc),
            )
            self._page_cache[key] = found
        return found

    # vectors

    def _weigh(self, counts: Counter) -> dict[str, float]:
        weights = {
            term: (1.0 + math.log(n)) * math.log((self.n_docs + 1) / (self.df.get(term, 0) + 1) + 1.0)
            for term, n in counts.items()
        }
        norm = math.sqrt(sum(value * value for value in weights.values())) or 1.0
        return {term: value / norm for term, value in weights.items()}

    def _best_other_search(self, vector: dict[str, float], search: str) -> tuple[str | None, float | None]:
        best_name = None
        best_score = None
        for name in self.readable:
            if name == search:
                continue
            value = self._match(vector, name)
            if value is None:
                continue
            if best_score is None or value > best_score:
                best_name = name
                best_score = value
        return best_name, best_score

    def _match(self, vector: dict[str, float], search: str, leave_out: str | None = None) -> float | None:
        docs = self.readable.get(search) or []
        total = self.sums.get(search) or Counter()
        size = len(docs)
        own = self.vectors.get(leave_out) if leave_out else None
        if own is not None:
            size -= 1
        if size <= 0:
            return None
        score = 0.0
        for term, value in vector.items():
            weight = total.get(term, 0.0) - (own.get(term, 0.0) if own else 0.0)
            score += value * weight
        return score / size

    # thresholds

    def need(self, search: str, words: Sequence[str], leave_out: str | None = None) -> int:
        """Search words a page needs: the fewest any readable REAL page here uses, capped at half the words."""
        half = max(1, len(words) // 2) if words else 0
        counts = self.found_counts.get(search) or {}
        others = [count for url, count in counts.items() if url != leave_out]
        if len(others) < 2 or list(words) != search_words(search):
            return min(1, len(words))
        return min(min(others), half)

    def _backtest(self) -> TopicBacktest:
        matches: list[tuple[str, str, float]] = []
        for search, docs in self.readable.items():
            if len(docs) < MIN_READABLE:
                continue
            for doc in docs:
                score = self._match(self.vectors[doc.url], search, leave_out=doc.url)
                if score is not None:
                    matches.append((search, doc.url, score))
        lowest = min((score for _s, _u, score in matches), default=None)
        floor = TOPIC_MARGIN * lowest if lowest is not None and lowest > 0 else TOPIC_FLOOR_DEFAULT
        self.topic_floor = floor
        result = TopicBacktest(topic_floor=round(floor, 4))
        by_url = {doc.url: doc for docs in self.readable.values() for doc in docs}
        for search, url, score in matches:
            doc = by_url[url]
            words = search_words(search)
            found = self.found_counts[search][url]
            other_scores = [value for _other, other_url, value in matches if other_url != url]
            own_floor = TOPIC_MARGIN * min(other_scores) if other_scores else floor
            rep = self.repetitions.get(url)
            rep_floor = _repetition_floor([value for other, value in self.repetitions.items() if other != url and value is not None])
            result.pages += 1
            ok = True
            if found < self.need(search, words, leave_out=url):
                result.failed_words += 1
                ok = False
            if score < own_floor:
                result.failed_topic += 1
                ok = False
            if rep is not None and rep < rep_floor:
                result.failed_repetition += 1
                ok = False
            if ok:
                result.passed += 1
        result.lowest_repetition = round(self.lowest_repetition, 3) if self.lowest_repetition is not None else None
        if result.pages:
            result.text = (
                f"Backtest: {result.passed} of {result.pages} readable REAL top 10 pages pass this check when each is "
                f"checked against the other pages of its search. {UNTESTED_NOTE}"
            )
        else:
            result.text = f"not run: needs at least {MIN_READABLE} readable stored REAL results on a search"
        return result

    # the check

    def check(self, search: str, doc: PageDoc, real_titles: Iterable[str] | None = None) -> TopicCheck:
        words = search_words(search)
        titles = list(real_titles if real_titles is not None else self.titles_by_search.get(search, []))
        titles = [title for title in titles if (title or "").strip()]
        page_vocab, title_vocab, vector, repeats = self._page(doc)
        title_found, title_missing = words_found(words, "", title_vocab)
        share = None
        if titles and words:
            share = sum(1 for title in titles if not words_found(words, title)[1]) / len(titles)
        found, missing = words_found(words, "", page_vocab)
        compared = self.readable.get(search) or []
        result = TopicCheck(
            search=search,
            on_topic=None,
            status="not checked",
            words=words,
            found=found,
            missing=missing,
            need=self.need(search, words),
            topic_floor=round(self.topic_floor, 4),
            title_found=title_found,
            title_missing=title_missing,
            real_title_share=None if share is None else round(share, 3),
            real_titles=len(titles),
            compared_with=len(compared),
            repetition_floor=round(self.repetition_floor, 4),
        )
        if not readable(doc):
            result.text = (
                f"Not checked: the page has {int(doc.word_count or 0)} words of readable text (under {READABLE_WORDS}), "
                "so there is nothing to compare. No PREDICTED slot."
            )
            return result
        result.repetition = repeats
        score = self._match(vector, search)
        result.topic_score = None if score is None else round(score, 4)
        listed = ", ".join(words)
        if result.repetition is not None and result.repetition < self.repetition_floor:
            result.on_topic = False
            result.status = "repetitive"
            lowest = self.backtest.lowest_repetition
            result.text = (
                f"Off topic (PREDICTED): the page repeats itself. Only {round(100 * result.repetition)}% of its 3-word "
                "sequences are distinct"
                + (f"; the most repetitive readable page Google ranks in this project has {round(100 * lowest)}%." if lowest is not None else ".")
                + " Pages like this read as keyword stuffing, which Google's spam policies demote. PREDICTED: not in the top 10."
            )
            return result
        if len(found) < result.need:
            result.on_topic = False
            result.status = "off topic"
            result.text = (
                f"Off topic (PREDICTED): your page uses {len(found)} of the {len(words)} search words ({listed}); "
                f"missing: {', '.join(missing)}. Every readable page Google ranks here uses at least {result.need}. "
                "PREDICTED: not in the top 10."
            )
            return result
        if score is not None and score < self.topic_floor:
            other_name, other_score = self._best_other_search(vector, search)
            if other_score is None:
                result.on_topic = None
                result.status = "uncertain"
                result.text = (
                    f"Low cosine to stored pages, unvalidated: topic match {score:.3f} is under the floor "
                    f"{self.topic_floor:.3f} (half the lowest match among stored pages). "
                    "This project has no other stored search to compare with, so this is not an off-topic decision. "
                    "A PREDICTED slot is still shown. Confidence stays UNSURE."
                )
                return result
            closer = (
                score >= other_score + TOPIC_SEPARATION
                and score >= TOPIC_FLOOR_DEFAULT
            )
            if closer:
                # Stored pages can match each other near 0.8 because they share a template,
                # so half of that is a high floor. Ordinary wording that still prefers this
                # search is not a validated off-topic result.
                result.on_topic = None
                result.status = "uncertain"
                result.text = (
                    f"Low cosine to stored pages, unvalidated: topic match {score:.3f} is under the floor "
                    f"{self.topic_floor:.3f} (half the lowest match among stored pages) and higher than "
                    f"{other_name} ({other_score:.3f}). This is not an off-topic decision. "
                    "A PREDICTED slot is still shown. Confidence stays UNSURE."
                )
                return result
            result.on_topic = False
            result.status = "off topic"
            result.text = (
                f"Off topic (PREDICTED): your page's vocabulary barely overlaps the pages Google ranks here "
                f"(topic match {score:.3f}; the floor is {self.topic_floor:.3f}, half the lowest match of any REAL top 10 page). "
                "PREDICTED: not in the top 10."
            )
            return result
        result.on_topic = True
        result.status = "on topic"
        match = f" Topic match {score:.3f} (floor {self.topic_floor:.3f})." if score is not None else ""
        if result.need:
            norm = f"pages Google ranks here use at least {result.need}."
        else:
            norm = "some page Google ranks here uses none of them, so the topic match decides."
        result.text = f"On topic (PREDICTED): your page uses {len(found)} of the {len(words)} search words ({listed}); {norm}{match}"
        return result


def build_topic_model(pages_by_search: dict[str, list[PageDoc]], titles_by_search: dict[str, list[str]] | None = None) -> TopicModel:
    return TopicModel(pages_by_search, titles_by_search)


@dataclass
class TodoItem:
    """One thing to change on the page, with the searches it affects. Plain text, labeled."""

    kind: str  # 'off_topic_words' | 'off_topic_vocab' | 'repetitive' | 'no_text' | 'title_words'
    text: str
    searches: list[str] = field(default_factory=list)
    label: str = "PREDICTED"


def todo_list(checks: Sequence[TopicCheck]) -> list[TodoItem]:
    """A ranked to-do list from the on-topic checks: the change that affects the most searches first."""
    items: list[TodoItem] = []
    usable = [check for check in checks if check is not None]
    no_text = [check.search for check in usable if check.status == "not checked" and check.text.startswith("Not checked: the page has")]
    if no_text:
        items.append(
            TodoItem(
                "no_text",
                f"Give the page readable text: we found under {READABLE_WORDS} words, so it could not be checked on {len(no_text)} searches.",
                no_text,
            )
        )
    repetitive = [check.search for check in usable if check.status == "repetitive"]
    if repetitive:
        items.append(
            TodoItem(
                "repetitive",
                f"Stop repeating the same phrases: the page reads as keyword stuffing, so it is PREDICTED off topic on {len(repetitive)} searches.",
                repetitive,
            )
        )
    off = [check for check in usable if check.status == "off topic"]
    # The vocabulary itself is foreign on most off-topic searches: the page is about something else. Say that first.
    foreign = [check.search for check in off if check.topic_score is not None and check.topic_floor is not None and check.topic_score < check.topic_floor]
    if off and len(foreign) * 2 >= len(off):
        items.append(
            TodoItem(
                "off_topic_vocab",
                f"This page reads as being about something else: its wording barely overlaps the pages Google ranks for {len(foreign)} "
                f"of your searches. Write about what those searches are about before tuning words.",
                foreign,
            )
        )
        foreign_set = set(foreign)
    else:
        foreign_set = set()
    missing: dict[str, list[str]] = {}
    phrasing: list[str] = []
    for check in off:
        if len(check.found) >= check.need or check.search in foreign_set:
            continue
        topical = [word for word in check.missing if word not in GENERIC_WORDS]
        if not topical:
            phrasing.append(check.search)
        for word in topical:
            missing.setdefault(word, []).append(check.search)
    if phrasing:
        items.append(
            TodoItem(
                "off_topic_words",
                "Use the searches' own wording (for example 'all in one' or 'best'): without it the page is PREDICTED off topic on "
                f"{len(phrasing)} {'search' if len(phrasing) == 1 else 'searches'}.",
                phrasing,
            )
        )
    for word, searches in sorted(missing.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        items.append(
            TodoItem(
                "off_topic_words",
                f"Cover '{word}' on the page (say what you offer for it, do not just repeat the word): without it the page is "
                f"PREDICTED off topic on {len(searches)} {'search' if len(searches) == 1 else 'searches'}.",
                searches,
            )
        )
    vocab = [check.search for check in off if len(check.found) >= check.need and check.search not in foreign_set]
    if vocab:
        items.append(
            TodoItem(
                "off_topic_vocab",
                f"Write about what these searches are about: the page's wording barely overlaps the pages Google ranks for {len(vocab)} searches.",
                vocab,
            )
        )
    title: dict[str, list[str]] = {}
    for check in usable:
        if check.on_topic is not True or not check.title_missing:
            continue
        if check.real_title_share is None or check.real_title_share < TITLE_NORM_SHARE:
            continue
        for word in check.title_missing:
            if word in GENERIC_WORDS:
                continue
            title.setdefault(word, []).append(check.search)
    for word, searches in sorted(title.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        items.append(
            TodoItem(
                "title_words",
                f"Put '{word}' in the title or main heading: most REAL top 10 titles have every search word on "
                f"{len(searches)} {'search' if len(searches) == 1 else 'searches'} where yours lacks it. "
                "This is what the ranked pages have in common, not a proven ranking lever.",
                searches,
                label="REAL",
            )
        )
    order = {"no_text": 0, "repetitive": 1, "off_topic_vocab": 2, "off_topic_words": 3, "title_words": 4}
    first = {"no_text", "repetitive"} | ({"off_topic_vocab"} if foreign_set else set())
    items.sort(key=lambda item: (0 if item.kind in first else 1, -len(item.searches), order.get(item.kind, 9)))
    return items
