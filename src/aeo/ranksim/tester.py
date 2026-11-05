"""Sandbox tester: score a page version in seconds and keep a version history.

Every rank here is PREDICTED by the leave-one-query-out LambdaMART models on
the stored competitor pages. None of it is a live Google rank. A query that is
not in the labeled Google file is skipped and gets no rank.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from aeo.crawl.urls import normalize_url
from aeo.ranksim.check import assemble_query_docs, check_draft
from aeo.ranksim.confidence import LooFit, loo_fit
from aeo.ranksim.features import THIN_WORD_GATE, url_text
from aeo.ranksim.groundtruth import SerpLabel, load_serp_file
from aeo.ranksim.loop import page_from_html
from aeo.ranksim.score import HAND_WEIGHTS, score_pages
from aeo.ranksim.whatif import worse_than_live
from aeo.textutil import tokenize, tokenize_for_search

HEADER = "Sandbox index. Ranks are PREDICTED on the stored competitor set. They are not live Google ranks."
SKIP_NOTE = "not in the labeled Google file, no rank invented"
SKIP_NOTE_UNSTORED = "in the labeled Google file, but fewer than two of its pages are stored, no rank invented"
LIVE_LABEL = "live (stored crawl)"
SEEDED_LABEL = "stored copy (seeded from a committed file, not a crawl)"
NO_LIVE_NOTE = "No stored crawl of this URL in the rank database, so the live column is blank. No rank was invented."
# A query is THIN when fewer than this share of its labeled Google results are
# in the scored set. A PREDICTED rank on a thin set says little.
THIN_SHARE = 0.5
UNSTORED_HINT = "Run python -m aeo.ranksim index-serp to store more of the labeled pages."
# Gaps a page edit cannot close, or that a rewriter must not chase.
NOT_PAGE_EDITS = frozenset(
    {
        "word count", "thin content", "freshness", "link count", "URL match", "URL term match", "anchor text",
        "crawl PageRank", "domain PageRank", "homepage authority", "authority", "fetch speed", "site focus",
        "site radius", "Navboost prior", "orphan page", "link depth", "in-crawl inlinks", "ad density",
    }
)
SITE_SIGNALS = frozenset(
    {
        "crawl PageRank", "domain PageRank", "homepage authority", "authority", "fetch speed", "site focus",
        "site radius", "Navboost prior", "ad density",
    }
)
CONCRETE_FIXES = {
    "readability": "Pages PREDICTED ahead read more easily. Use shorter sentences and plainer words.",
    "content effort": (
        "Pages PREDICTED ahead repeat fewer words and sit nearer the length the model prefers. "
        "Cut repetition, not useful content."
    ),
    "link count": "Pages PREDICTED ahead have more links. Add a link only where it helps a reader.",
    "embedding similarity": "Pages PREDICTED ahead stay closer to the query topic. Keep the opening about the query.",
    "originality": "Pages PREDICTED ahead share less text with other pages. Say what is specific to this product.",
    "intent match": "Pages PREDICTED ahead match the kind of page the query wants, for example a comparison or a list.",
    "FAQ": "Add a short FAQ that answers real questions about the query.",
}
DEFAULT_HISTORY_DIR = Path("data/tester")
GENERIC_FIX = "Close this gap on the page."
# A query is picked by overlap only when terms worth this share of its weight
# appear in the title, H1, or URL. Rare terms weigh more than common ones.
MIN_OVERLAP = 0.6


@dataclass
class SimContext:
    """Everything needed to score a draft in memory: labels, stored pages, fitted models."""

    labels: list[SerpLabel]
    pages: dict
    fit: LooFit
    intents: dict[str, str]
    queries: list[str]
    today: date
    uncovered: int = 0
    database: str | None = None
    notes: list[str] = field(default_factory=list)
    serp_file: str = ""

    def accuracy_line(self) -> str:
        return accuracy_line(self.fit, self.uncovered)


def accuracy_line(fit: LooFit, uncovered: int) -> str:
    """The backtest computed for this run. It says how much to trust a PREDICTED rank."""
    if fit.pairwise is None:
        return (
            "No backtest was computed for this run. Too few labeled queries have stored pages. "
            "Treat every PREDICTED rank as a guess."
        )
    line = (
        f"Backtest on the pages stored now, leave-one-query-out, PREDICTED: pairwise {_num(fit.pairwise)}, "
        f"NDCG at 10 {_num(fit.ndcg)}, Spearman {_num(fit.spearman)}, top-3 hit rate {_num(fit.top3)}. "
        f"{fit.n_queries} queries, {fit.n_pairs} pairs, {uncovered} labeled rows had no stored HTML. "
        "Pairwise 0.500 is a coin flip."
    )
    if fit.pairwise <= 0.55:
        line += (
            " This backtest is no better than a coin flip, so a PREDICTED move here is a smoke test, "
            "not evidence of a Google move. Store more labeled pages to make it useful."
        )
    return line


def load_sim_context(
    database: str,
    serp_file: str,
    today: date | None = None,
    graph_path: str | None = "smartcrew/linkgraph.json",
) -> SimContext:
    """Load labels, stored pages, the link graph, and the leave-one-query-out fit.

    Mirrors the demo server's loading. Each model in fit.models was trained
    without the query it scores.
    """
    from aeo.ranksim.bootstrap import seed_corpus_if_empty
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.keywords import build_keywords
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.linkgraph import attach_graph, load_graph
    from aeo.ranksim.store import open_session

    today = today or date.today()
    labels = load_serp_file(serp_file)
    queries = list(dict.fromkeys(row.query for row in labels))
    intents = {row.query: row.intent for row in build_keywords()}
    notes = [seed_corpus_if_empty(database)]
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
    finally:
        session.close()
    graph_file = Path(graph_path) if graph_path else None
    if graph_file is not None and graph_file.is_file():
        attach_graph(pages, load_graph(str(graph_file)))
    else:
        notes.append("No link graph file was found. Link features stay missing.")
    examples, uncovered, _withheld = build_examples(labels, pages, intents, today=today)
    fit = loo_fit(examples, keep_models=True)
    return SimContext(
        labels=labels,
        pages=pages,
        fit=fit,
        intents=intents,
        queries=queries,
        today=today,
        uncovered=uncovered,
        database=database,
        notes=notes,
        serp_file=str(serp_file),
    )


@dataclass
class QueryScore:
    query: str
    predicted_rank: int | None
    predicted_of: int
    confidence: str
    linear_rank: int | None
    top_gap: str = ""
    ahead: list[str] = field(default_factory=list)
    error: str = ""
    gaps: list[str] = field(default_factory=list)
    added: bool = False
    labeled: int = 0
    in_set: int = 0
    missing: int = 0
    labeled_position: float | None = None
    gap_details: list[dict] = field(default_factory=list)
    on_topic: bool | None = None  # the project tester's PREDICTED on-topic check; None when not checked

    @property
    def thin(self) -> bool:
        """Fewer than half of the labeled Google results are in the scored set."""
        return self.labeled > 0 and self.in_set < self.labeled * THIN_SHARE

    def coverage_text(self) -> str:
        if not self.labeled:
            return "not in the labeled Google file"
        text = f"{self.in_set} of {self.labeled} labeled Google results are stored and scored"
        if self.missing:
            text += f", {self.missing} have no stored HTML and were left out"
        return text + "."


@dataclass
class PageScore:
    url: str
    label: str
    source: str
    content_hash: str
    word_count: int
    scores: list[QueryScore]
    created: str = ""
    version: str = ""

    def by_query(self) -> dict[str, QueryScore]:
        return {row.query: row for row in self.scores}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PageScore":
        rows = []
        for item in data.get("scores") or []:
            rows.append(
                QueryScore(
                    query=str(item.get("query") or ""),
                    predicted_rank=_int_or_none(item.get("predicted_rank")),
                    predicted_of=int(item.get("predicted_of") or 0),
                    confidence=str(item.get("confidence") or ""),
                    linear_rank=_int_or_none(item.get("linear_rank")),
                    top_gap=str(item.get("top_gap") or ""),
                    ahead=[str(url) for url in item.get("ahead") or []],
                    error=str(item.get("error") or ""),
                    gaps=[str(gap) for gap in item.get("gaps") or []],
                    added=bool(item.get("added")),
                    labeled=int(item.get("labeled") or 0),
                    in_set=int(item.get("in_set") or 0),
                    missing=int(item.get("missing") or 0),
                    labeled_position=_float_or_none(item.get("labeled_position")),
                    gap_details=[dict(gap) for gap in item.get("gap_details") or [] if isinstance(gap, dict)],
                    on_topic=item.get("on_topic") if isinstance(item.get("on_topic"), bool) else None,
                )
            )
        return cls(
            url=str(data.get("url") or ""),
            label=str(data.get("label") or ""),
            source=str(data.get("source") or ""),
            content_hash=str(data.get("content_hash") or ""),
            word_count=int(data.get("word_count") or 0),
            scores=rows,
            created=str(data.get("created") or ""),
            version=str(data.get("version") or ""),
        )


def content_hash(html: str) -> str:
    return hashlib.sha1((html or "").encode("utf-8", "replace")).hexdigest()


def pick_targets(
    ctx: SimContext,
    url: str,
    html: str,
    queries: list[str] | None = None,
    top: int = 5,
) -> tuple[list[str], list[str]]:
    """Choose the labeled queries to score. Returns (targets, skipped).

    Given queries are kept only when a model exists for them; the rest are
    skipped with no rank. Otherwise labeled queries are ranked: this URL is a
    labeled row first, then keywords.csv targets this URL, then term overlap
    with the title, H1, and URL, weighted so rare terms count more. Overlap
    below MIN_OVERLAP is not picked. Skipped then lists keywords.csv queries that
    target this URL but are not in the labeled file.
    """
    if queries:
        spelling = {_fold(query): query for query in [*ctx.queries, *ctx.fit.models]}
        wanted = list(dict.fromkeys(spelling.get(_fold(query), " ".join(query.split())) for query in queries if query and query.strip()))
        targets = [query for query in wanted if query in ctx.fit.models]
        skipped = [query for query in wanted if query not in ctx.fit.models]
        return targets, skipped
    key = _key(url)
    labeled_here: dict[str, float] = {}
    for label in ctx.labels:
        if _key(label.url) == key:
            labeled_here[label.query] = min(label.position, labeled_here.get(label.query, label.position))
    suggested = _suggested_queries(key)
    doc = page_from_html(url, html or "")
    haystack = set(tokenize(f"{doc.title} {doc.h1} {url_text(url)}"))
    weights = _term_weights(ctx.queries)
    ranked: list[tuple[tuple, str]] = []
    for order, query in enumerate(ctx.queries):
        if query not in ctx.fit.models:
            continue
        terms = query_terms(query)
        matched = [term for term in terms if has_term(term, haystack)]
        total = sum(weights.get(term, 1.0) for term in terms)
        share = sum(weights.get(term, 1.0) for term in matched) / total if total else 0.0
        if query in labeled_here:
            tier = 0
        elif query in suggested:
            tier = 1
        elif share >= MIN_OVERLAP:
            tier = 2
        else:
            continue
        ranked.append(((tier, -round(share, 6), -len(matched), labeled_here.get(query, 99.0), order), query))
    ranked.sort()
    targets = [query for _sort, query in ranked[: max(0, top)]]
    skipped = [query for query in suggested if query not in ctx.fit.models]
    return targets, skipped


def stable_targets(
    ctx: SimContext,
    url: str,
    html: str,
    *,
    queries: list[str] | None = None,
    top: int = 5,
    pinned: list[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Targets that stay fixed across versions. Returns (targets, skipped, pinned_kept).

    Given queries win. Otherwise the queries scored for v1 or the stored copy
    are kept first, so an edit that drops words from the title cannot drop a
    query from the regression check. New picks for this HTML come after them.
    """
    if queries:
        targets, skipped = pick_targets(ctx, url, html, queries, top=top)
        return targets, skipped, []
    picked, skipped = pick_targets(ctx, url, html, top=top)
    kept = [query for query in dict.fromkeys(pinned or []) if query in ctx.fit.models]
    gone = [query for query in dict.fromkeys(pinned or []) if query not in ctx.fit.models]
    targets = kept + [query for query in picked if query not in kept]
    skipped = list(dict.fromkeys([*skipped, *gone]))
    return targets, skipped, kept


def skip_notes(ctx: SimContext, skipped: list[str]) -> dict[str, str]:
    """Why each skipped query has no rank. Nothing is scored for them."""
    labeled = set(ctx.queries)
    return {query: SKIP_NOTE_UNSTORED if query in labeled else SKIP_NOTE for query in skipped}


def _term_weights(queries: list[str]) -> dict[str, float]:
    """Rare query terms count more than terms most labeled queries share, like "software"."""
    counts: dict[str, int] = {}
    for query in queries:
        for term in set(query_terms(query)):
            counts[term] = counts.get(term, 0) + 1
    size = max(1, len(queries))
    return {term: math.log(1.0 + size / count) for term, count in counts.items()}


def query_terms(query: str) -> list[str]:
    return list(dict.fromkeys(tokenize_for_search(query)))


def has_term(term: str, tokens: set[str]) -> bool:
    if term in tokens or f"{term}s" in tokens:
        return True
    return term.endswith("s") and term[:-1] in tokens


def score_page(
    ctx: SimContext,
    url: str,
    html: str,
    targets: list[str],
    label: str = "",
    source: str = "",
    created: str = "",
) -> PageScore:
    """PREDICTED rank of this HTML at this URL on every target query, in memory."""
    rows: list[QueryScore] = []
    words = None
    for query in targets:
        row, query_words = _score_query(ctx, url, html, query)
        rows.append(row)
        if words is None and query_words is not None:
            words = query_words
    if words is None:
        words = page_from_html(url, html or "", is_ours=True).word_count
    return PageScore(
        url=url,
        label=label,
        source=source,
        content_hash=content_hash(html),
        word_count=int(words),
        scores=rows,
        created=created,
    )


def _score_query(ctx: SimContext, url: str, html: str, query: str) -> tuple[QueryScore, int | None]:
    intent = ctx.intents.get(query, "commercial")
    checked = check_draft(
        query=query,
        html=html,
        url=url,
        labels=ctx.labels,
        pages=ctx.pages,
        model=ctx.fit.models.get(query),
        calibration=ctx.fit.calibration,
        today=ctx.today,
        intent=intent,
        run_ai=False,
    )
    row = QueryScore(
        query=query,
        predicted_rank=checked.predicted_rank,
        predicted_of=checked.predicted_of,
        confidence=checked.confidence if checked.predicted_rank is not None else "n/a",
        linear_rank=None,
        error=checked.error,
        added=checked.added_draft,
    )
    _set_coverage(row, ctx.labels, url, checked.missing_urls)
    if checked.predicted_rank is None:
        return row, None
    assembled = assemble_query_docs(query, html, url, ctx.labels, ctx.pages)
    ours = assembled.docs[assembled.ours_index]
    ahead_all = [comp.url for comp in checked.competitors if comp.predicted_rank < checked.predicted_rank]
    row.ahead = ahead_all[-3:]
    words_by_url = {doc.url: doc.word_count for doc in assembled.docs}
    ahead_words = [words_by_url.get(item, 0) for item in ahead_all]
    details = [_gap_detail(gap, ours.word_count, ahead_words) for gap in checked.gaps]
    details.sort(key=lambda item: not item["actionable"])
    row.gap_details = details
    row.gaps = list(dict.fromkeys(item["fix"] for item in details))
    row.top_gap = row.gaps[0] if row.gaps else ""
    scored = score_pages(query, intent, assembled.docs, HAND_WEIGHTS, ctx.today)
    row.linear_rank = next((item.predicted_position for item in scored if item.url == ours.url), None)
    return row, ours.word_count


def _set_coverage(row: QueryScore, labels: list[SerpLabel], url: str, missing_urls: list[str]) -> None:
    """How much of the labeled Google set for this query is stored. Nothing is invented."""
    ours = _key(url)
    labeled = {_key(label.url) for label in labels if label.query == row.query}
    if not labeled:
        return
    missing = {_key(item) for item in missing_urls} & labeled
    missing.discard(ours)  # this draft stands in for its own labeled row
    row.labeled = len(labeled)
    row.missing = len(missing)
    row.in_set = row.labeled - row.missing
    positions = [label.position for label in labels if label.query == row.query and _key(label.url) == ours]
    row.labeled_position = min(positions) if positions else None


def _gap_detail(gap, our_words: int, ahead_words: list[int]) -> dict:
    """A gap as fix text, the model detail, and whether a page edit can act on it."""
    feature = gap.feature
    fix = gap.fix
    actionable = feature not in NOT_PAGE_EDITS
    if feature in {"word count", "thin content"} and our_words >= THIN_WORD_GATE:
        longest = max(ahead_words, default=0)
        if longest > our_words:
            fix = f"Write a fuller page. The longest page PREDICTED ahead has {longest} words. This one has {our_words}."
        else:
            fix = (
                f"Pages PREDICTED ahead are shorter than this one, which has {our_words} words. "
                "Length is not the gap. Do not pad or cut the page for it."
            )
    elif feature in SITE_SIGNALS:
        fix = f"This is a site signal ({feature}). A page edit does not change it."
    elif fix == GENERIC_FIX:
        fix = CONCRETE_FIXES.get(feature, f"Close the gap on {feature}.")
    return {"feature": feature, "fix": fix, "detail": gap.detail, "actionable": actionable}


def stored_html(database: str | None, url: str) -> tuple[str, str, str] | None:
    """(raw_html, stored_url, fetched_on) for this URL from the rank database, or None."""
    if not database:
        return None
    from aeo.ranksim.store import RsPage, open_session

    candidates = {url}
    try:
        normal = normalize_url(url)
        candidates.update({normal, normalize_url(url, keep_trailing_slash=True), normal.rstrip("/") + "/"})
    except ValueError:
        pass
    session = open_session(database)
    try:
        rows = (
            session.query(RsPage)
            .filter(RsPage.url.in_(sorted(candidates)), RsPage.status == "fetched")
            .all()
        )
    finally:
        session.close()
    rows.sort(key=lambda row: row.url != url)
    for row in rows:
        if (row.raw_html or "").strip():
            fetched = row.fetched_at.date().isoformat() if row.fetched_at else "unknown date"
            return row.raw_html, row.url, fetched
    return None


def seed_origin(url: str, html: str) -> str:
    """The committed file this stored HTML was seeded from, or "" when it matches none.

    A seeded row is a copy of a file in the repo, not a crawl of the live site.
    """
    from aeo.ranksim.bootstrap import PR8_V3
    from aeo.ranksim.saved_html import saved_html_dir

    key = _key(url)
    for folder in (PR8_V3, saved_html_dir()):
        manifest = Path(folder) / "manifest.csv"
        if not manifest.is_file():
            continue
        try:
            with manifest.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            continue
        for item in rows:
            row_url = (item.get("url") or "").strip()
            name = (item.get("file") or "").strip()
            if not row_url or not name or _key(row_url) != key:
                continue
            path = Path(folder) / name
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if text == html:
                return path.as_posix()
    return ""


def live_score(ctx: SimContext, url: str, targets: list[str], created: str = "") -> PageScore | None:
    """Score the HTML the rank database holds for this URL. None when it holds none.

    When that HTML is a committed file the database was seeded from, it is
    labeled a stored copy, not live.
    """
    found = stored_html(ctx.database, url)
    if found is None:
        return None
    html, stored_url, fetched = found
    origin = seed_origin(stored_url, html)
    if origin:
        label = SEEDED_LABEL
        source = f"rank database row {stored_url}, seeded from {origin}, not crawled from the live site"
    else:
        label = LIVE_LABEL
        source = f"rank database row {stored_url}, stored {fetched}"
    return score_page(ctx, url, html, targets, label=label, source=source, created=created)


class TesterHistory:
    """JSONL of PageScore dicts. A new line only when the HTML changed."""

    __test__ = False  # not a pytest test class, despite the name

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @staticmethod
    def default_path(url: str, root: str | Path = DEFAULT_HISTORY_DIR) -> Path:
        digest = hashlib.sha1(_key(url).encode("utf-8")).hexdigest()[:12]
        return Path(root) / f"{digest}.jsonl"

    def entries(self) -> list[dict]:
        if not self.path.is_file():
            return []
        found = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                found.append(item)
        return found

    def versions(self) -> list[PageScore]:
        return [PageScore.from_dict(item) for item in self.entries()]

    def last(self) -> PageScore | None:
        items = self.entries()
        return PageScore.from_dict(items[-1]) if items else None

    def first(self) -> PageScore | None:
        items = self.entries()
        return PageScore.from_dict(items[0]) if items else None

    def previous(self) -> PageScore | None:
        """The version before the newest one."""
        items = self.entries()
        return PageScore.from_dict(items[-2]) if len(items) >= 2 else None

    def append(self, score: PageScore) -> bool:
        """Record score as a new version when its HTML hash differs from the last one.

        Sets score.version either way. Returns True when a line was written.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _history_lock(self.path):
            items = self.entries()
            if items and items[-1].get("content_hash") == score.content_hash:
                score.version = str(items[-1].get("version") or f"v{len(items)}")
                return False
            score.version = f"v{len(items) + 1}"
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(score.to_dict()) + "\n")
            return True


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


@contextmanager
def _history_lock(path: Path):
    """Read, number and append one version as one step, across threads and processes (fcntl where available)."""
    key = str(Path(path).resolve())
    with _THREAD_LOCKS_GUARD:
        local = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with local:
        lock_path = Path(path).with_suffix(Path(path).suffix + ".lock")
        try:
            import fcntl
        except ImportError:  # pragma: no cover - not POSIX
            yield
            return
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def baseline_for(current: PageScore, live: PageScore | None, first: PageScore | None) -> tuple[PageScore | None, str]:
    """Live (or the stored copy) when stored, else v1 when this is a later version, else no baseline."""
    if live is not None:
        return live, _baseline_name(live)
    if first is None:
        return None, ""
    if current.version and first.version:
        later = current.version != first.version
    else:
        later = first.content_hash != current.content_hash
    return (first, first.version or "v1") if later else (None, "")


def regressions(current: PageScore, baseline: PageScore | None, baseline_name: str = "") -> list[str]:
    """One line per target query PREDICTED worse than the baseline."""
    if baseline is None:
        return []
    name = baseline_name or _baseline_name(baseline)
    before = baseline.by_query()
    found = []
    for row in current.scores:
        old = before.get(row.query)
        if old is None or old.predicted_rank is None:
            continue
        if worse_than_live(old.predicted_rank, row.predicted_rank):
            now = f"PREDICTED {row.predicted_rank} of {row.predicted_of}" if row.predicted_rank is not None else "no PREDICTED rank"
            found.append(
                f"{row.query}: {now}, worse than {name} at PREDICTED {old.predicted_rank} of {old.predicted_of}."
            )
    return found


def board_dict(
    current: PageScore,
    previous: PageScore | None = None,
    live: PageScore | None = None,
    accuracy_line: str = "",
    *,
    skipped: list[str] | dict[str, str] | None = None,
    first: PageScore | None = None,
    serp_file: str = "",
    targets_note: str = "",
) -> dict:
    """The board as JSON-serializable data. Same content as render_board."""
    baseline, baseline_name = baseline_for(current, live, first)
    live_by = live.by_query() if live else {}
    prev_by = previous.by_query() if previous else {}
    base_by = baseline.by_query() if baseline else {}
    rows = []
    better = worse = same = no_base = 0
    for row in current.scores:
        base = base_by.get(row.query)
        base_rank = base.predicted_rank if base else None
        if base is None or base_rank is None:
            change, flag = "n/a", ""
            no_base += 1
        elif row.predicted_rank is None:
            change, flag = "n/a", "WORSE"
            worse += 1
        else:
            delta = base_rank - row.predicted_rank
            change = "same" if delta == 0 else (f"up {delta}" if delta > 0 else f"down {-delta}")
            if worse_than_live(base_rank, row.predicted_rank):
                flag = "WORSE"
                worse += 1
            elif delta > 0:
                flag = "better"
                better += 1
            else:
                flag = "same"
                same += 1
        rows.append(
            {
                "query": row.query,
                "live": _rank_dict(live_by.get(row.query)),
                "previous": _rank_dict(prev_by.get(row.query)),
                "now": _rank_dict(row),
                "change": change,
                "flag": flag,
                "confidence": row.confidence,
                "linear_rank": row.linear_rank,
                "linear_of": row.predicted_of if row.linear_rank is not None else None,
                "top_gap": row.top_gap,
                "gaps": list(row.gaps),
                "ahead": list(row.ahead),
                "error": row.error,
                "draft_added": row.added,
                "labeled": row.labeled,
                "stored_of_labeled": row.in_set,
                "missing": row.missing,
                "thin": row.thin,
                "coverage": row.coverage_text(),
                "labeled_file_position": row.labeled_position,
                "gap_details": [dict(item) for item in row.gap_details],
            }
        )
    total = len(current.scores)
    if baseline is None:
        summary = "No baseline yet. Later versions are compared with this one." if total else "No target queries were scored."
    else:
        summary = f"Better on {better}, worse on {worse}, same on {same}, of {count_of(total, 'target query', 'target queries')}."
        if no_base:
            summary += f" {no_base} had no {baseline_name} rank to compare."
    if live is not None and live.label == SEEDED_LABEL:
        live_note = (
            f"The stored copy of this URL is {live.source}. It is not the live site. "
            "The stored column and the change are measured against that copy."
        )
    elif live is not None:
        live_note = f"Live is the HTML stored for this URL: {live.source}. It may be older than the live site."
    else:
        live_note = NO_LIVE_NOTE
    return {
        "header": HEADER,
        "predicted": True,
        "live_google": False,
        "url": current.url,
        "version": current.version,
        "label": current.label,
        "source": current.source,
        "content_hash": current.content_hash,
        "word_count": current.word_count,
        "created": current.created,
        "previous_version": previous.version if previous else None,
        "baseline": baseline_name or None,
        "live_kind": None if live is None else ("stored copy" if live.label == SEEDED_LABEL else "live"),
        "live_note": live_note,
        "targets_note": targets_note,
        "serp_file": serp_file,
        "accuracy_line": accuracy_line,
        "rows": rows,
        "summary": {"better": better, "worse": worse, "same": same, "total": total, "text": summary},
        "regressions": regressions(current, baseline, baseline_name),
        "skipped": _skip_items(skipped),
    }


def render_board(
    current: PageScore,
    previous: PageScore | None = None,
    live: PageScore | None = None,
    accuracy_line: str = "",
    *,
    skipped: list[str] | dict[str, str] | None = None,
    first: PageScore | None = None,
    serp_file: str = "",
    targets_note: str = "",
) -> str:
    """Plain-text board. Every rank is PREDICTED on the stored competitor set."""
    data = board_dict(
        current, previous, live, accuracy_line, skipped=skipped, first=first, serp_file=serp_file, targets_note=targets_note
    )
    lines = [HEADER, f"Page: {current.url}"]
    version = current.version or "unsaved"
    lines.append(
        f"This version: {version}, {current.label or 'draft'} from {current.source or 'memory'}, "
        f"{current.word_count} words, sha1 {current.content_hash[:12]}"
        + (f", scored {current.created}." if current.created else ".")
    )
    if previous is not None:
        lines.append(f"Previous version: {previous.version or 'unsaved'}, scored {previous.created or 'unknown time'}.")
    lines.append(data["live_note"])
    if data["baseline"]:
        lines.append(f"Change is measured against {data['baseline']}.")
    if targets_note:
        lines.append(targets_note)
    if accuracy_line:
        lines.append(accuracy_line)
    lines.append("")
    skip_items = data["skipped"]
    if not current.scores:
        if skip_items and all(item["note"] == SKIP_NOTE_UNSTORED for item in skip_items):
            lines.append(f"No target queries were scored. The queries asked for have too few stored pages. {UNSTORED_HINT}")
        else:
            lines.append("No target queries were scored. Pass --queries with queries from the labeled Google file.")
    else:
        lines.append("PREDICTED ranks, written rank/of. 3/9 means PREDICTED 3 of the 9 pages scored.")
        lines.append("Linear check is an independent hand-weighted scorer on the same pages. It is also PREDICTED.")
        lines.append("Stored set: labeled Google results for the query that have stored HTML. THIN means fewer than half.")
        live_head = "stored" if data["live_kind"] == "stored copy" else "live"
        table = [["query", live_head, "prev", "now", "change", "flag", "confidence", "linear", "stored set", "biggest gap"]]
        for row in data["rows"]:
            table.append(
                [
                    row["query"],
                    _rank_cell(row["live"]),
                    _rank_cell(row["previous"]),
                    _rank_cell(row["now"]),
                    row["change"],
                    row["flag"] or "n/a",
                    row["confidence"],
                    f"{row['linear_rank']}/{row['linear_of']}" if row["linear_rank"] is not None else "n/a",
                    _set_cell(row),
                    row["top_gap"] or _no_gap_text(row),
                ]
            )
        lines.extend(_table(table))
        lines.append("")
        lines.append(data["summary"]["text"])
        for line in data["regressions"]:
            lines.append(f"WORSE: {line}")
        errors = [row for row in data["rows"] if row["error"]]
        for row in errors:
            lines.append(f"No rank for {row['query']}: {row['error']}")
        ahead = [row for row in data["rows"] if row["ahead"]]
        if ahead:
            lines.append("Next pages PREDICTED ahead of this one:")
            for row in ahead:
                lines.append(f"  {row['query']}: {', '.join(row['ahead'])}")
        thin = [row for row in data["rows"] if row["thin"]]
        for row in thin:
            lines.append(f"THIN: {row['query']}: {row['coverage']} A PREDICTED rank on this set says little.")
        if thin:
            lines.append(UNSTORED_HINT)
        placed = [row for row in data["rows"] if row["labeled_file_position"] is not None]
        if placed:
            where = f"in {serp_file}" if serp_file else "in the labeled Google file"
            cells = ", ".join(f"{row['query']} {_position(row['labeled_file_position'])}" for row in placed)
            lines.append(f"Labeled Google file position of this URL {where}, a past snapshot, not a live rank: {cells}.")
        added = [row["query"] for row in data["rows"] if row["draft_added"] and not row["error"]]
        if added:
            lines.append(
                "This URL is not a labeled row for "
                + ", ".join(added)
                + ". The page was added to the stored set for that query, so the set has one more page than its stored labeled rows."
            )
    if skip_items:
        lines.append("Skipped queries:")
        for item in skip_items:
            lines.append(f"  {item['query']}: {item['note']}.")
    return plain_dashes("\n".join(lines) + "\n")


def count_of(number: int, one: str, many: str) -> str:
    return f"{number} {one if number == 1 else many}"


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def plain_dashes(text: str) -> str:
    """House style: no em or en dashes in output, even when page text has them.

    Also drops control characters (escape sequences in a crawled title could
    otherwise clear the terminal or rewrite its title). Newlines and tabs stay.
    """
    return _CONTROL.sub("", text.replace("\u2014", "-").replace("\u2013", "-"))


def watch(
    load_source_fn: Callable[[], str],
    score_fn: Callable[[str], object],
    interval_s: float,
    max_checks: int,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_change: Callable[[object], None] = print,
) -> int:
    """Poll the source. When its HTML hash changes, rescore and emit the result.

    max_checks 0 or less means poll until interrupted. Returns the number of rescores.
    """
    last_hash: str | None = None
    checks = 0
    rescored = 0
    while max_checks <= 0 or checks < max_checks:
        if checks:
            sleep_fn(interval_s)
        checks += 1
        try:
            html = load_source_fn()
        except Exception as exc:  # a failed fetch should not end the watch
            on_change(f"Could not load the source on check {checks}: {exc}. Nothing was scored.")
            continue
        digest = content_hash(html)
        if digest == last_hash:
            continue
        last_hash = digest
        on_change(score_fn(html))
        rescored += 1
    return rescored


def _skip_items(skipped: list[str] | dict[str, str] | None) -> list[dict[str, str]]:
    if not skipped:
        return []
    if isinstance(skipped, dict):
        return [{"query": query, "note": note} for query, note in skipped.items()]
    return [{"query": query, "note": SKIP_NOTE} for query in skipped]


def _baseline_name(baseline: PageScore) -> str:
    if baseline.label == LIVE_LABEL:
        return "live"
    if baseline.label == SEEDED_LABEL:
        return "the stored copy"
    return baseline.version or baseline.label or "the baseline"


def _rank_dict(row: QueryScore | None) -> dict | None:
    if row is None:
        return None
    return {"rank": row.predicted_rank, "of": row.predicted_of}


def _no_gap_text(row: dict) -> str:
    if row["error"]:
        return "n/a"
    now = row["now"] or {}
    if now.get("rank") == 1:
        return "none, nothing is PREDICTED ahead"
    return "none listed"


def _set_cell(row: dict) -> str:
    if not row["labeled"]:
        return "n/a"
    return f"{row['stored_of_labeled']} of {row['labeled']}" + (" THIN" if row["thin"] else "")


def _position(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _rank_cell(value: dict | None) -> str:
    if not value or value.get("rank") is None:
        return "n/a"
    return f"{value['rank']}/{value['of']}"


def _table(rows: list[list[str]]) -> list[str]:
    widths = [max(len(str(row[index])) for row in rows) for index in range(len(rows[0]) - 1)]
    out = []
    for row in rows:
        cells = [str(cell).ljust(widths[index]) for index, cell in enumerate(row[:-1])]
        out.append("  ".join(cells + [str(row[-1])]).rstrip())
    return out


def _suggested_queries(key: str) -> list[str]:
    try:
        from aeo.ranksim.keywords import build_keywords

        rows = build_keywords()
    except (OSError, ValueError):
        return []
    return [row.query for row in rows if row.suggested_target_page == key]


def _key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url


def _fold(query: str) -> str:
    return " ".join(str(query).casefold().split())


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"
