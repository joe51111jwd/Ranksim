"""Projects: your key searches, the REAL Google top 10 for each, and a mini search engine.

A project is a site plus the searches that matter to it. A pull records the
REAL Google results for every search (from a SERP API, or carried over from a
Google position file you imported), crawls every result page into the shared
rank database, and trains the mini search engine on that set. Testing a page
then answers one question per search: the incumbents' REAL order is known, so
where does this page slot in? That slot is PREDICTED.

House rules kept here:
- Every model output is PREDICTED. Google data is REAL and carries its source
  and the time it was pulled, or "pull date not recorded".
- Nothing is invented. A search with no REAL results says "not run: needs
  <ENV VAR>". A result page with no stored HTML is listed, never scored.
- Page text and crawled text are data. Nothing in this module sends them to an
  LLM or follows directions written inside them.

Layout on disk (data/projects/<slug>/):
  project.json                     the project
  pulls/<UTC timestamp>/google.json REAL results per search
  pulls/<UTC timestamp>/pull.json   per URL crawl status, step timings, backtest
  pulls/<UTC timestamp>/engine/     the trained mini engine (pages, models, fit)
  tests/<url hash>.jsonl            tested versions (TesterHistory)
  tests/<url hash>/<sha1>.html      the HTML of each tested version
Pages stay in the shared rank database (RsPage) so projects reuse them.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from aeo.config import Settings
from aeo.crawl.urls import domain_of, normalize_url
from aeo.errors import AeoError, ProviderConfigError, UnsafeURL
from aeo.ranksim.attribution import LABELS
from aeo.ranksim.check import FIXES
from aeo.ranksim.confidence import Calibration, LooFit, loo_fit
from aeo.ranksim.features import FEATURE_NAMES, THIN_WORD_GATE, PageDoc, normalize_rows, raw_features
from aeo.ranksim.groundtruth import SerpLabel, load_serp_file
from aeo.ranksim.lambdamart import RankExample, _ndcg, _pairs, _top3_hit, build_examples, fit_lambdamart, predict_scores
from aeo.ranksim.loop import FetchResult, page_from_html, store_fetched_page
from aeo.ranksim.score import HAND_WEIGHTS, dot
from aeo.ranksim.site_history import (
    CONTENT_BLIND_NOTE,
    METHOD_LABEL as SITE_METHOD,
    SiteHistoryModel,
    SiteIndex,
    SlotBacktest,
    SlotErrors,
    fit_site_history,
    lists_from_pairs,
    site_history_backtest,
    site_history_position,
    site_key,
)
from aeo.ranksim.relevance import UNTESTED_NOTE as TOPIC_UNTESTED_NOTE
from aeo.ranksim.relevance import TodoItem, TopicBacktest, TopicCheck, TopicModel, todo_list
from aeo.ranksim.tester import CONCRETE_FIXES, PageScore, QueryScore, TesterHistory, content_hash, count_of, plain_dashes

DEFAULT_ROOT = Path("data/projects")
ROOT_ENV = "RANKSIM_PROJECTS_DIR"
GOOGLE_KEYS = "SERPAPI_KEY, SERPER_API_KEY, or DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD"
NOT_RUN_GOOGLE = f"not run: needs {GOOGLE_KEYS}"
PULL_DATE_NOT_RECORDED = "pull date not recorded"
SMARTCREW_SERP_SOURCE = "smartcrew/serp.csv (committed 2026-10-29, pull date not recorded)"
FRESH_DAYS = 7
THIN_SHARE = 0.5
DEFAULT_MAX_SITE_PAGES = 60
PREDICTED_NOTE = "PREDICTED means the mini search engine's guess. It is not a Google rank."
CONTENT_METHOD = "content model"
CONTENT_MODEL_NOTE = (
    "The content model reads the page text. In backtests it did no better than always guessing the middle, "
    "so read its slot and every change between versions as a smoke test, not a forecast."
)
REAL_NOTE = "REAL means Google's own results, with their source and the time they were pulled."
CRAWL_NOTE = "Stored means we hold a copy of the page HTML. Pages we could not fetch are listed, never invented."
# Features a page edit can move. Everything else is a site or link signal.
PAGE_FEATURES = (
    "bm25f", "bm25_title", "bm25_h1", "bm25_headings", "bm25_body", "has_headings", "has_body", "embedding",
    "intent_match", "freshness", "topic_coverage", "has_table", "has_faq", "word_count", "content_effort",
    "originality", "thin_content", "readability", "links",
)
SITE_FEATURES = (
    "domain_rank", "homepage_authority", "pagerank", "authority", "crawl_inlinks", "link_depth", "orphan",
    "bm25_anchor", "bm25_url", "url_exact", "site_focus", "site_radius", "speed", "ad_density", "navboost_prior",
)
SITE_FIXES = {
    "domain_rank": "Links between sites raise this. Editing this page does not.",
    "homepage_authority": "Links to the homepage raise this. Editing this page does not.",
    "pagerank": "Links to this page from other pages raise this. Editing this page does not.",
    "authority": "Links to this page from other pages raise this. Editing this page does not.",
    "crawl_inlinks": "Add links to this page from your other pages.",
    "link_depth": "Link to this page from pages closer to your homepage.",
    "orphan": "Link to this page from a page that already has inlinks.",
    "bm25_anchor": "Point links at this page with the search words as the link text.",
    "bm25_url": "Their URL holds the search words. Changing yours means moving the page with a redirect.",
    "url_exact": "Their URL holds the search words. Changing yours means moving the page with a redirect.",
    "site_focus": "Their site is more focused on this topic. One page edit does not change that.",
    "site_radius": "Their site is more focused on this topic. One page edit does not change that.",
    "speed": "Their server answered faster when we crawled. That is hosting, not page text.",
    "ad_density": "Site template signal.",
    "navboost_prior": "Past click signal. Not used for a new page.",
}
MIN_GAP = 0.02
TOP_DEPTH = 10  # only REAL positions 1 to 10 are incumbents; deeper rows are listed but never used to place a page
BOARD_SCHEMA = 2  # 2: headline counts come from the on-topic check plus site history, not the content model
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_ga", "ref_src")
TOP10_NOTE = (
    "The tester places a page among pages that already made Google's top 10, so it cannot say whether a page makes page one at all. "
    "An off-topic page is PREDICTED not to."
)

ProgressFn = Callable[["ProgressEvent"], None]
InsertionFn = Callable[[float, Sequence[float | None]], int]
FetchBatch = Callable[[list[str]], list[FetchResult]]


class ProjectError(AeoError):
    """A project is missing, malformed, or has nothing to work on yet."""


# Data model


@dataclass
class Project:
    name: str
    slug: str
    site: str
    searches: list[str]
    created: str
    intents: dict[str, str] = field(default_factory=dict)
    site_pages: list[str] = field(default_factory=list)
    sitemap_file: str | None = None
    link_graph: str | None = None
    discover_sitemap: bool = True
    max_site_pages: int = DEFAULT_MAX_SITE_PAGES
    root: str = ""

    @property
    def dir(self) -> Path:
        return Path(self.root or projects_root()) / self.slug

    @property
    def domain(self) -> str:
        return domain_of(f"https://{self.site}/")

    def is_our_url(self, url: str) -> bool:
        host = domain_of(url)
        return bool(host) and (host == self.domain or host.endswith("." + self.domain))

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("root", None)
        return data

    @classmethod
    def from_dict(cls, data: dict, root: str | Path = "") -> "Project":
        known = {item.name for item in fields(cls)}
        values = {key: value for key, value in data.items() if key in known and key != "root"}
        project = cls(**values)
        project.root = str(root)
        project.searches = [str(item) for item in project.searches]
        return project


@dataclass
class ProjectSummary:
    name: str
    slug: str
    site: str
    searches: int
    created: str
    latest_pull: str | None
    latest_google: str | None
    has_engine: bool


@dataclass
class SerpResult:
    position: int
    url: str
    domain: str
    title: str = ""
    snippet: str = ""


@dataclass
class SearchResults:
    """REAL Google results for one search, with where and when they came from."""

    search: str
    results: list[SerpResult]
    source: str
    provider: str
    pulled_at: str | None
    location: str | None = None
    language: str | None = None
    error: str = ""

    @property
    def pulled_label(self) -> str:
        return pulled_label(self.pulled_at)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["pulled_label"] = self.pulled_label
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SearchResults":
        return cls(
            search=str(data.get("search") or ""),
            results=[
                SerpResult(
                    position=int(item.get("position") or 0),
                    url=str(item.get("url") or ""),
                    domain=str(item.get("domain") or domain_of(str(item.get("url") or ""))),
                    title=str(item.get("title") or ""),
                    snippet=str(item.get("snippet") or ""),
                )
                for item in data.get("results") or []
                if item.get("url")
            ],
            source=str(data.get("source") or ""),
            provider=str(data.get("provider") or ""),
            pulled_at=data.get("pulled_at"),
            location=data.get("location"),
            language=data.get("language"),
            error=str(data.get("error") or ""),
        )


@dataclass
class GoogleSnapshot:
    """The REAL results of one pull, one entry per search."""

    pull_id: str
    written_at: str
    kind: str  # "import", "api", or "carried"
    searches: list[SearchResults]
    carried_from: str | None = None
    note: str = ""

    def by_search(self) -> dict[str, SearchResults]:
        return {item.search: item for item in self.searches}

    def to_dict(self) -> dict:
        return {
            "pull_id": self.pull_id,
            "written_at": self.written_at,
            "kind": self.kind,
            "carried_from": self.carried_from,
            "note": self.note,
            "real": True,
            "searches": [item.to_dict() for item in self.searches],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GoogleSnapshot":
        return cls(
            pull_id=str(data.get("pull_id") or ""),
            written_at=str(data.get("written_at") or ""),
            kind=str(data.get("kind") or ""),
            searches=[SearchResults.from_dict(item) for item in data.get("searches") or []],
            carried_from=data.get("carried_from"),
            note=str(data.get("note") or ""),
        )


@dataclass
class ImportReport:
    project: str
    pull_id: str
    source: str
    rows: int
    searches_imported: list[str]
    searches_missing: list[str]
    searches_ignored: list[str]
    searches_added: list[str]


@dataclass
class ProgressEvent:
    step: str  # google, crawl, train, save, done
    message: str
    done: int = 0
    total: int = 0
    seconds: float = 0.0


@dataclass
class UrlStatus:
    url: str
    code: str  # stored, fetched_now, blocked_robots, http_error, error, unsafe, kept_stored
    status: str  # plain words: "stored", "fetched now", "blocked by robots", "HTTP 403", "error"
    detail: str = ""
    stored_at: str | None = None
    ours: bool = False
    searches: list[str] = field(default_factory=list)
    seeded_from: str = ""


@dataclass
class StepTiming:
    step: str
    seconds: float
    detail: str = ""


@dataclass
class SearchBacktest:
    """How well the held-out model ordered this search's REAL results."""

    search: str
    stored: int
    pairs: int
    pairwise: float | None
    ndcg: float | None
    top3_hit: float | None


@dataclass
class EngineMetrics:
    """Backtest of the mini engine: PREDICTED order vs REAL order, each search held out."""

    pairwise: float | None
    ndcg: float | None
    spearman: float | None
    top3: float | None
    n_searches: int
    n_pairs: int
    uncovered: int
    calibration_threshold: float | None
    calibration_pairwise: float | None
    calibration_coverage: float | None
    calibration_note: str
    per_search: list[SearchBacktest] = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)
    trained_at: str = ""
    text: str = ""

    def for_search(self, search: str) -> SearchBacktest | None:
        return next((row for row in self.per_search if row.search == search), None)

    @classmethod
    def from_dict(cls, data: dict) -> "EngineMetrics":
        rows = [SearchBacktest(**item) for item in data.get("per_search") or []]
        known = {item.name for item in fields(cls)}
        values = {key: value for key, value in data.items() if key in known and key != "per_search"}
        return cls(per_search=rows, **values)


@dataclass
class PullReport:
    project: str
    pull_id: str
    kind: str
    started_at: str
    finished_at: str
    google_kind: str
    google_note: str
    google_source: str
    searches_total: int
    searches_with_results: int
    urls: list[UrlStatus] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    metrics: EngineMetrics | None = None
    steps: list[StepTiming] = field(default_factory=list)
    total_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    site_urls: list[str] = field(default_factory=list)
    engines_note: str = ""
    engines_status: dict[str, str] = field(default_factory=dict)
    engines_step: StepTiming | None = None  # ran in the background, alongside crawl and train

    def to_dict(self) -> dict:
        return clean(asdict(self))

    def all_steps(self) -> list[StepTiming]:
        """Every timed step in run order: google, engines, crawl, train, save."""
        found = list(self.steps)
        if self.engines_step is not None:
            found.insert(1 if found else 0, self.engines_step)
        return found

    @classmethod
    def from_dict(cls, data: dict) -> "PullReport":
        metrics = EngineMetrics.from_dict(data["metrics"]) if data.get("metrics") else None
        known = {item.name for item in fields(cls)}
        skip = {"urls", "steps", "metrics", "engines_step"}
        values = {key: value for key, value in data.items() if key in known and key not in skip}
        engines_step = StepTiming(**data["engines_step"]) if data.get("engines_step") else None
        return cls(
            urls=[UrlStatus(**item) for item in data.get("urls") or []],
            steps=[StepTiming(**item) for item in data.get("steps") or []],
            metrics=metrics,
            engines_step=engines_step,
            **values,
        )


@dataclass
class PullInfo:
    pull_id: str
    kind: str
    written_at: str
    searches_with_results: int
    has_engine: bool
    google_note: str
    pairwise: float | None
    total_seconds: float | None
    google_kind: str = ""  # 'api' (Google asked now), 'carried' (REAL results reused), 'import'
    carried_from: str | None = None
    finished: bool = True  # google.json written; a pull still being written is not 'the last pull'

    @property
    def what(self) -> str:
        """Plain words for what this pull did."""
        if self.google_kind == "api":
            return "Google asked now"
        if self.google_kind == "carried":
            return f"recrawl only: Google not asked, REAL results reused from pull {self.carried_from}" if self.carried_from else "recrawl only: Google not asked"
        if self.google_kind == "import":
            return "REAL results imported from a file"
        return self.kind or "unknown"


@dataclass
class ProjectStatus:
    project: dict
    pulls: list[PullInfo]
    google: GoogleSnapshot | None
    report: PullReport | None
    coverage: dict[str, dict]
    site_backtest: SlotBacktest | None = None

    def to_dict(self) -> dict:
        return clean(
            {
                "project": self.project,
                "pulls": [asdict(item) for item in self.pulls],
                "google": self.google.to_dict() if self.google else None,
                "report": asdict(self.report) if self.report else None,
                "coverage": self.coverage,
                "site_backtest": asdict(self.site_backtest) if self.site_backtest else None,
            }
        )


@dataclass
class ProjectContext:
    """The loaded mini engine for one project. Like tester.SimContext."""

    project: Project
    pull_id: str
    google: GoogleSnapshot
    labels: list[SerpLabel]
    pages: dict
    fit: LooFit
    fallback_model: object | None
    metrics: EngineMetrics
    stored_at: dict[str, str]
    seeded: dict[str, str]
    today: date
    loaded_from: str
    load_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    database: str | None = None
    site_index: SiteIndex | None = field(default=None, repr=False)
    site_model: SiteHistoryModel | None = None
    site_backtest: SlotBacktest | None = None
    content_backtest: SlotBacktest | None = None
    topic: TopicModel | None = field(default=None, repr=False)
    _gains: dict = field(default_factory=dict, repr=False)

    @property
    def slot_method(self) -> str:
        """Which predictor gives the headline PREDICTED slot."""
        return SITE_METHOD if self.site_model is not None else CONTENT_METHOD

    def model_for(self, search: str) -> tuple[object | None, str]:
        if search in self.fit.models:
            return self.fit.models[search], "held-out model: trained on the other searches, never saw this one"
        if self.fallback_model is not None:
            return self.fallback_model, "model trained on the other searches (this one had too few stored pages to train on)"
        return None, ""

    def gains(self, model) -> dict[str, float]:
        key = id(model)
        if key not in self._gains:
            try:
                names = list(model.feature_name())
                values = model.feature_importance(importance_type="gain")
                self._gains[key] = {name: float(value) for name, value in zip(names, values)}
            except Exception:  # noqa: BLE001 - a stand-in model may not report gains
                self._gains[key] = {}
        return self._gains[key]


# Board (the result of testing a page)


@dataclass
class RealResult:
    position: int
    url: str
    domain: str
    title: str
    stored: bool
    stored_at: str | None = None
    tested_url: bool = False
    our_site: bool = False
    model_scores_above_you: bool | None = None  # PREDICTED, None when not stored
    beyond_top10: bool = False  # REAL position deeper than 10: listed, never used to place a page


@dataclass
class Neighbor:
    real_position: int
    url: str
    domain: str
    title: str
    stored: bool


@dataclass
class FeatureGap:
    feature: str
    label: str
    yours: float | None
    theirs: float | None
    yours_text: str
    theirs_text: str
    fix: str


@dataclass
class Comparison:
    """One page above your slot, compared with yours. Model features, so PREDICTED."""

    real_position: int
    url: str
    domain: str
    title: str
    stored: bool
    model_scores_above_you: bool | None
    score_gap: float | None = None  # their model score minus yours, PREDICTED; smaller is easier to overtake
    page_gaps: list[FeatureGap] = field(default_factory=list)
    site_signals: list[FeatureGap] = field(default_factory=list)
    note: str = ""
    # Site history (what actually sets the headline slot): REAL counts and the PREDICTED odds.
    their_site_searches: int | None = None  # other searches whose REAL top 10 lists their site
    your_site_searches: int | None = None
    p_above_you: float | None = None  # PREDICTED chance they rank above you, from site history only
    own_site: bool = False  # this REAL result is a page of your own site
    facts: list[str] = field(default_factory=list)  # REAL facts about their page next to yours


@dataclass
class EngineRow:
    """One AI answer engine on one search. REAL when status is ok, else 'not run: needs <ENV VAR>' or an error."""

    engine: str
    name: str
    status: str
    label: str = ""
    pull_id: str = ""
    pulled_at: str = ""
    model: str = ""
    runs: int = 0
    ok_runs: int = 0
    our_cited_runs: int = 0
    our_share: float | None = None
    our_mean_position: float | None = None
    brand_mentioned_runs: int = 0
    top_domains: list[str] = field(default_factory=list)
    our_urls: list[str] = field(default_factory=list)
    cited: list[dict] = field(default_factory=list)  # position, url, domain, title from the latest answer
    note: str = ""


@dataclass
class SimSummary:
    """PREDICTED: our own LLM answering from the stored Google top 10 plus your page. Not the real engine."""

    status: str
    label: str = ""
    provider: str = ""
    model: str = ""
    runs: int = 0
    ok_runs: int = 0
    your_cited_runs: int = 0
    your_share: float | None = None
    your_mean_position: float | None = None
    domain_share_without: float | None = None
    domain_share_with: float | None = None
    domain_delta: float | None = None
    sources: list[dict] = field(default_factory=list)  # domain, url, google_position, share_with, share_without, is_yours
    agreement: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cost_usd: float | None = None


@dataclass
class SearchBoard:
    search: str
    intent: str
    real_source: str
    real_pulled: str
    real_results: list[RealResult] = field(default_factory=list)
    real_position: int | None = None
    real_site_position: int | None = None
    real_site_url: str | None = None
    predicted_position: int | None = None
    predicted_of: int = 0
    predicted_best: int | None = None
    predicted_worst: int | None = None
    above: Neighbor | None = None
    below: Neighbor | None = None
    beaten_by: list[Comparison] = field(default_factory=list)
    confidence: str = "n/a"
    confidence_note: str = ""
    linear_position: int | None = None
    coverage_stored: int = 0
    coverage_total: int = 0
    thin: bool = False
    coverage_text: str = ""
    backtest: SearchBacktest | None = None
    model: str = ""
    previous_position: int | None = None
    stored_copy_position: int | None = None
    change_vs_previous: str = "n/a"
    change_vs_stored: str = "n/a"
    flag: str = ""
    error: str = ""
    model_check: str = ""  # when this URL already ranks: the stored copy's PREDICTED slot vs its REAL position
    # Headline slot (site history, the predictor that did best in backtests). Content blind.
    site_position: int | None = None
    site_position_of: int = 0
    site_position_low: int | None = None
    site_position_high: int | None = None
    your_site_searches: int | None = None
    site_note: str = ""
    site_above: Neighbor | None = None
    site_below: Neighbor | None = None
    above_slot: list[Comparison] = field(default_factory=list)  # REAL results above the headline slot, compared
    site_check: str = ""  # when this URL already ranks: the site history slot vs its REAL position
    # AI answer engines: REAL citations per engine, and the PREDICTED simulation.
    engines: list[EngineRow] = field(default_factory=list)
    simulation: SimSummary | None = None
    # The on-topic check (PREDICTED). An off-topic page gets no slot at all.
    topic: TopicCheck | None = None
    topic_regression: str = ""
    # Google positions implied by the slot's REAL neighbors. Equal to the slot when the REAL list has no gaps;
    # high is None when the REAL list stops before the slot and deeper positions are unknown.
    site_google_low: int | None = None
    site_google_high: int | None = None
    google_low: int | None = None
    google_high: int | None = None

    def headline(self) -> int | None:
        """The slot to lead with: site history when it ran, else the content model."""
        return self.site_position if self.site_position is not None else self.predicted_position

    def headline_google(self) -> tuple[int | None, int | None]:
        """(low, high) Google position of the headline slot. high None means 'or lower'."""
        if self.site_position is not None:
            return self.site_google_low, self.site_google_high
        if self.predicted_position is not None:
            return self.google_low, self.google_high
        return None, None

    def headline_label(self) -> str:
        low, high = self.headline_google()
        return google_label(low, high)


@dataclass
class BoardSummary:
    text: str
    searches: int
    scored: int
    predicted_top3: int
    predicted_top10: int
    real_top10: int
    real_site_top10: int
    real_pulled: str
    better: int = 0
    worse: int = 0
    same: int = 0
    baseline: str = ""
    method: str = CONTENT_METHOD
    content_top3: int = 0
    content_top10: int = 0
    ai_text: str = ""
    on_topic: int = 0
    off_topic: int = 0
    not_checked: int = 0


@dataclass
class ProjectBoard:
    project: str
    project_name: str
    site: str
    url: str
    version: str
    content_hash: str
    word_count: int
    source: str
    tested_at: str
    pull_id: str
    google_source: str
    google_pulled: str
    summary: BoardSummary
    searches: list[SearchBoard]
    backtest: EngineMetrics
    stored_copy: str | None = None
    previous_version: str | None = None
    baseline: str = ""
    regressions: list[str] = field(default_factory=list)
    history_path: str = ""
    new_version_saved: bool = False
    notes: list[str] = field(default_factory=list)
    timing: dict[str, float] = field(default_factory=dict)
    predicted_note: str = PREDICTED_NOTE
    real_note: str = REAL_NOTE
    slot_method: str = CONTENT_METHOD
    slot_backtest: SlotBacktest | None = None
    content_backtest: SlotBacktest | None = None
    content_note: str = CONTENT_MODEL_NOTE
    ai_real_note: str = ""
    ai_sim_label: str = ""
    engines_pulls: list[str] = field(default_factory=list)
    schema: int = BOARD_SCHEMA
    todo: list[TodoItem] = field(default_factory=list)
    topic_backtest: TopicBacktest | None = None
    topic_regressions: list[str] = field(default_factory=list)
    slot_group_text: str = ""  # the backtest subgroup that matches this page (new site, or new page on a known site)
    top10_note: str = TOP10_NOTE

    def to_dict(self) -> dict:
        return clean(asdict(self))


# Projects on disk


def projects_root(root: str | Path | None = None) -> Path:
    if root:
        return Path(root)
    override = os.environ.get(ROOT_ENV)
    return Path(override) if override else DEFAULT_ROOT


def slugify(name: str) -> str:
    """ASCII slug. Accents fold ('Excavación' -> 'excavacion'); a name with no Latin letters gets a short hash,
    so two Japanese or Greek project names never share one slug."""
    import unicodedata

    text = str(name or "")
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:60].strip("-")
    if slug:
        return slug
    stripped = " ".join(text.split())
    if not stripped:
        return "project"
    return "project-" + hashlib.sha1(stripped.casefold().encode("utf-8")).hexdigest()[:8]


def clean_site(site: str) -> str:
    """'https://www.example.com/x' and 'www.example.com' both become 'www.example.com'."""
    text = str(site or "").strip()
    if not text:
        raise ProjectError("A project needs a site, for example www.example.com.")
    if "://" not in text:
        text = "https://" + text
    try:
        normal = normalize_url(text)
    except ValueError as exc:
        raise ProjectError(f"Not a usable site: {site}. {exc}") from exc
    host = normal.split("://", 1)[1].split("/", 1)[0]
    return host


def clean_searches(searches: Iterable[str] | str) -> list[str]:
    if isinstance(searches, str):
        # One string is a list of searches separated by ';' or new lines, never a list of letters.
        searches = re.split(r"[;\n]", searches)
    seen: dict[str, str] = {}
    for item in searches:
        text = " ".join(str(item or "").split())
        if text and _fold(text) not in seen:
            seen[_fold(text)] = text
    return list(seen.values())


def split_searches(text: str | None) -> list[str]:
    """'a; b' or one search per line. Commas stay inside a search."""
    if not text:
        return []
    return clean_searches(re.split(r"[;\n]", text))


def guess_intent(search: str) -> str:
    """The intent label the features use. keywords.csv wins when it lists the search."""
    known = _known_intents()
    if _fold(search) in known:
        return known[_fold(search)]
    text = f" {search.lower()} "
    if any(word in text for word in (" alternative", " alternatives", " vs ", " versus ", " compare", " competitor")):
        return "comparison"
    if any(word in text for word in (" pricing", " price", " cost ", " demo", " free trial", " buy ")):
        return "transactional"
    if text.strip().startswith(("how ", "what ", "why ", "when ", "can ", "is ", "guide")):
        return "informational"
    return "commercial"


@lru_cache(maxsize=1)
def _known_intents() -> dict[str, str]:
    try:
        from aeo.ranksim.keywords import build_keywords

        return {_fold(row.query): row.intent for row in build_keywords()}
    except (OSError, ValueError):
        return {}


def create_project(
    name: str,
    site: str,
    searches: Iterable[str],
    *,
    root: str | Path | None = None,
    slug: str | None = None,
    site_pages: Iterable[str] | None = None,
    sitemap_file: str | None = None,
    link_graph: str | None = None,
    discover_sitemap: bool = True,
    max_site_pages: int = DEFAULT_MAX_SITE_PAGES,
    created: str | None = None,
) -> Project:
    name = " ".join(str(name or "").split())
    if not name:
        raise ProjectError("A project needs a name.")
    cleaned = clean_searches(searches)
    if not cleaned:
        raise ProjectError("A project needs at least one key search.")
    base = projects_root(root)
    project = Project(
        name=name,
        slug=slugify(slug or name),
        site=clean_site(site),
        searches=cleaned,
        created=created or now_iso(),
        intents={search: guess_intent(search) for search in cleaned},
        site_pages=[url for url in (site_pages or []) if url],
        sitemap_file=sitemap_file,
        link_graph=link_graph,
        discover_sitemap=discover_sitemap,
        max_site_pages=max_site_pages,
        root=str(base),
    )
    if (project.dir / "project.json").exists():
        raise ProjectError(f"Project {project.slug} already exists in {base}.")
    save_project(project)
    return project


def save_project(project: Project) -> None:
    _write_json(project.dir / "project.json", project.to_dict())


def load_project(name_or_slug: str, *, root: str | Path | None = None) -> Project:
    base = projects_root(root)
    path = base / slugify(name_or_slug) / "project.json"
    if not path.is_file():
        for summary in _project_files(base):
            data = _read_json(summary)
            if data and _fold(str(data.get("name") or "")) == _fold(name_or_slug):
                path = summary
                break
        else:
            raise ProjectError(f"No project named {name_or_slug} in {base}. Create one with: project create NAME --site DOMAIN.")
    data = _read_json(path)
    if not data:
        raise ProjectError(f"{path} could not be read.")
    return Project.from_dict(data, base)


def list_projects(*, root: str | Path | None = None) -> list[ProjectSummary]:
    base = projects_root(root)
    found: list[ProjectSummary] = []
    for path in _project_files(base):
        data = _read_json(path)
        if not data:
            continue
        project = Project.from_dict(data, base)
        pulls = list_pulls(project)
        latest = pulls[-1] if pulls else None
        google = latest_google(project)
        found.append(
            ProjectSummary(
                name=project.name,
                slug=project.slug,
                site=project.site,
                searches=len(project.searches),
                created=project.created,
                latest_pull=latest.pull_id if latest else None,
                latest_google=_snapshot_label(google) if google else None,
                has_engine=any(item.has_engine for item in pulls),
            )
        )
    return found


def update_searches(
    project: Project | str,
    *,
    add: Iterable[str] | None = None,
    remove: Iterable[str] | None = None,
    replace: Iterable[str] | None = None,
    root: str | Path | None = None,
) -> Project:
    """Add, remove, or replace key searches. A new search has no REAL results until the next pull."""
    if isinstance(project, str):
        project = load_project(project, root=root)
    if replace is not None:
        searches = clean_searches(replace)
    else:
        drop = {_fold(item) for item in (remove or [])}
        searches = clean_searches([*[item for item in project.searches if _fold(item) not in drop], *(add or [])])
    if not searches:
        raise ProjectError("A project needs at least one key search.")
    project.searches = searches
    project.intents = {search: project.intents.get(search) or guess_intent(search) for search in searches}
    save_project(project)
    return project


# Pulls on disk


def list_pulls(project: Project) -> list[PullInfo]:
    found: list[PullInfo] = []
    pulls_dir = project.dir / "pulls"
    if not pulls_dir.is_dir():
        return found
    for folder in sorted((item for item in pulls_dir.iterdir() if item.is_dir()), key=_pull_order):
        google = _read_json(folder / "google.json")
        report = _read_json(folder / "pull.json") or {}
        metrics = report.get("metrics") or {}
        found.append(
            PullInfo(
                pull_id=folder.name,
                kind=str(report.get("kind") or (google or {}).get("kind") or ""),
                written_at=str(report.get("finished_at") or (google or {}).get("written_at") or ""),
                searches_with_results=sum(1 for item in (google or {}).get("searches") or [] if item.get("results")),
                has_engine=(folder / "engine" / "fit.json").is_file(),
                google_note=str(report.get("google_note") or (google or {}).get("note") or ""),
                pairwise=metrics.get("pairwise"),
                total_seconds=report.get("total_seconds"),
                google_kind=str((google or {}).get("kind") or report.get("google_kind") or ""),
                carried_from=(google or {}).get("carried_from"),
                finished=google is not None,
            )
        )
    return found


def _pull_order(folder: Path) -> tuple[str, int]:
    """20261104T120000Z-10 sorts after 20261104T120000Z-9 (text order would put it before -2)."""
    base, _sep, suffix = folder.name.partition("-")
    return base, int(suffix) if suffix.isdigit() else (1 if not suffix else 0)


def latest_google(project: Project) -> GoogleSnapshot | None:
    for info in reversed(list_pulls(project)):
        data = _read_json(project.dir / "pulls" / info.pull_id / "google.json")
        if data:
            return GoogleSnapshot.from_dict(data)
    return None


def load_pull_report(project: Project, pull_id: str | None = None) -> PullReport | None:
    pulls = list_pulls(project)
    if pull_id is None:
        pull_id = pulls[-1].pull_id if pulls else None
    if pull_id is None:
        return None
    data = _read_json(project.dir / "pulls" / pull_id / "pull.json")
    return PullReport.from_dict(data) if data else None


def project_status(project: Project) -> ProjectStatus:
    """Everything `project show` prints, for a UI. Reads files only."""
    pulls = list_pulls(project)
    google = latest_google(project)
    report = None
    for info in reversed(pulls):
        report = load_pull_report(project, info.pull_id)
        if report is not None and report.urls:
            break
    coverage: dict[str, dict] = {}
    stored_keys = {_key(item.url) for item in (report.urls if report else []) if item.code in {"stored", "fetched_now", "kept_stored"}}
    if google is not None:
        by_search = google.by_search()
        for search in project.searches:
            entry = by_search.get(search)
            results = entry.results if entry else []
            coverage[search] = {
                "results": len(results),
                "stored": sum(1 for item in results if _key(item.url) in stored_keys) if report else None,
                "pulled": entry.pulled_label if entry and results else None,
                "source": entry.source if entry else None,
                "error": (entry.error if entry else NOT_RUN_GOOGLE) or ("" if results else NOT_RUN_GOOGLE),
            }
    site_backtest = site_history_backtest(site_index(google, project.searches)) if google is not None else None
    return ProjectStatus(project=project.to_dict(), pulls=pulls, google=google, report=report, coverage=coverage, site_backtest=site_backtest)


def site_index(google: GoogleSnapshot, searches: Iterable[str] | None = None) -> SiteIndex:
    """Which sites each search lists in its REAL top 10. Only the project's current searches count."""
    keep = set(searches) if searches is not None else None
    return SiteIndex(
        lists_from_pairs(
            (entry.search, [(item.position, item.url) for item in entry.results])
            for entry in google.searches
            if entry.results and (keep is None or entry.search in keep)
        )
    )


def import_serp_csv(
    project: Project,
    path: str | Path,
    snapshot_date: str | None = None,
    source_label: str | None = None,
    *,
    add_searches: bool = False,
    now: datetime | None = None,
) -> ImportReport:
    """Seed a REAL Google pull from a query,url,position file. Nothing is fetched or invented.

    Only rows for the project's searches are kept. With add_searches, searches in
    the file that the project does not have yet are added first. snapshot_date
    (YYYY-MM-DD) is the day the positions were seen; without it the pull date is
    "not recorded".
    """
    labels = load_serp_file(path)
    in_file = clean_searches(label.query for label in labels)
    added: list[str] = []
    if add_searches:
        known = {_fold(item) for item in project.searches}
        added = [search for search in in_file if _fold(search) not in known]
        if added:
            update_searches(project, add=added)
    spelling = {_fold(search): search for search in project.searches}
    grouped: dict[str, list[SerpLabel]] = defaultdict(list)
    for label in labels:
        search = spelling.get(_fold(label.query))
        if search is not None:
            grouped[search].append(label)
    if snapshot_date:
        try:
            seen_on = date.fromisoformat(snapshot_date.strip())
        except ValueError as exc:
            raise ProjectError(f"--snapshot-date must be YYYY-MM-DD, not {snapshot_date}.") from exc
        today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
        if seen_on > today:
            raise ProjectError(f"--snapshot-date {seen_on.isoformat()} is in the future. Give the day the positions were seen.")
        pulled_at: str | None = seen_on.isoformat()
    else:
        pulled_at = None
    source = source_label or _default_source(path, pulled_at)
    searches: list[SearchResults] = []
    for search in project.searches:
        rows = sorted(grouped.get(search, []), key=lambda item: item.position)
        if not rows:
            continue
        results: list[SerpResult] = []
        seen: set[str] = set()
        for row in rows:
            if loose_key(row.url) in seen:
                continue  # http/https, www, slash or tracking-tag twins of one page: keep the best position
            seen.add(loose_key(row.url))
            results.append(SerpResult(position=int(round(row.position)), url=row.url, domain=domain_of(row.url)))
        searches.append(
            SearchResults(
                search=search,
                results=results,
                source=source,
                provider="csv_import",
                pulled_at=pulled_at,
                location=None,
                language=None,
            )
        )
    if not searches:
        raise ProjectError(
            f"{path} has no rows for this project's searches. Searches in the file: {', '.join(in_file[:10])}. "
            "Pass --add-searches to add them."
        )
    pull_id = _new_pull_id(project, now)
    written = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    snapshot = GoogleSnapshot(
        pull_id=pull_id,
        written_at=written,
        kind="import",
        searches=searches,
        note=f"Imported from {path}. {source}.",
    )
    folder = project.dir / "pulls" / pull_id
    _write_json(folder / "google.json", clean(snapshot.to_dict()))
    imported = [item.search for item in searches]
    missing = [search for search in project.searches if search not in imported]
    report = PullReport(
        project=project.slug,
        pull_id=pull_id,
        kind="import",
        started_at=written,
        finished_at=written,
        google_kind="import",
        google_note=f"REAL results imported from {source}.",
        google_source=source,
        searches_total=len(project.searches),
        searches_with_results=len(searches),
        notes=[f"{search}: not in {path}. {NOT_RUN_GOOGLE}." for search in missing],
    )
    _write_json(folder / "pull.json", report.to_dict())
    return ImportReport(
        project=project.slug,
        pull_id=pull_id,
        source=source,
        rows=sum(len(item.results) for item in searches),
        searches_imported=imported,
        searches_missing=missing,
        searches_ignored=[search for search in in_file if _fold(search) not in spelling],
        searches_added=added,
    )


def _default_source(path: str | Path, pulled_at: str | None) -> str:
    try:
        relative = Path(path).resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        relative = Path(path).as_posix()
    if relative == "smartcrew/serp.csv" and pulled_at is None:
        return SMARTCREW_SERP_SOURCE
    if pulled_at:
        return f"{relative} (Google positions seen {pulled_at})"
    return f"{relative} ({PULL_DATE_NOT_RECORDED})"


# Pull: REAL Google results, crawl, train


def pull(
    project: Project,
    settings: Settings,
    *,
    depth: int = 10,
    progress: ProgressFn | None = None,
    skip_google: bool = False,
    database: str | None = None,
    serp_provider=None,
    fetcher: FetchBatch | None = None,
    fresh_days: float = FRESH_DAYS,
    workers: int = 8,
    delay_seconds: float = 0.4,
    today: date | None = None,
    now: datetime | None = None,
    engines: Sequence[str] | None = None,
    engine_runs: int = 1,
    brand: str | None = None,
    engine_client=None,
    engine_cache_dir: str | Path | None = None,
    engine_use_cache: bool = True,
    engine_models: dict[str, str] | None = None,
) -> PullReport:
    """Pull REAL Google results, crawl what is missing or stale, ask the AI engines, and train the mini engine.

    Steps, each timed: google, engines, crawl, train, save. The engines step
    (REAL AI answer citations, aeo.ranksim.engines) runs in a background thread
    while the crawl and the training run, so it adds little wall time. engines
    picks which ones (default all four; an empty list skips the step); an engine
    without its key is recorded as 'not run: needs <ENV VAR>' and nothing else.
    progress gets a ProgressEvent per step message, per search, and per crawled
    URL (done and total counts for a progress bar). Calls are serialized by a
    lock; engine events come from the engines thread.

    With no Google key and no earlier results, raises ProviderConfigError naming
    the env vars. Nothing is invented. serp_provider, fetcher and engine_client
    are for tests and for callers that bring their own; fetcher takes a list of
    URLs on one host and returns one FetchResult per URL, in order.
    """
    database = database or settings.ranksim_database_url
    started = time.perf_counter()
    started_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    emit = _Emitter(progress, started)
    steps: list[StepTiming] = []
    notes: list[str] = []
    previous = latest_google(project)
    chosen_engines = _chosen_engines(engines)
    if chosen_engines:
        from aeo.ranksim.engines import MAX_RUNS

        if not 1 <= int(engine_runs) <= MAX_RUNS:
            raise ValueError(f"AI engine runs must be between 1 and {MAX_RUNS}, not {engine_runs}.")

    # 1. REAL Google results
    step_start = time.perf_counter()
    emit("google", f"Getting the REAL Google top {depth} for {len(project.searches)} searches.", 0, len(project.searches))
    pull_id = _new_pull_id(project, now)
    try:
        snapshot, google_note = _google_step(project, settings, previous, pull_id, depth, skip_google, serp_provider, emit, now)
    except BaseException:
        _drop_empty(project.dir / "pulls" / pull_id)
        raise
    with_results = sum(1 for item in snapshot.searches if item.results)
    steps.append(StepTiming("google", _round(time.perf_counter() - step_start), google_note))
    if snapshot.kind == "api":
        emit("google", google_note, len(project.searches), len(project.searches))
    else:
        emit("google", google_note, 0, 0)  # Google was not asked: no full progress bar for work that did not happen

    # 2. REAL AI engine citations, in the background while we crawl and train
    engines_box: dict = {}
    engines_thread: threading.Thread | None = None
    if chosen_engines:
        from aeo.ranksim.project_engines import run_engines_step

        def _engines() -> None:
            began = time.perf_counter()
            try:
                engines_box["snapshot"] = run_engines_step(
                    project,
                    settings,
                    pull_id=pull_id,
                    engines=chosen_engines,
                    n_runs=engine_runs,
                    brand=brand,
                    cache_dir=engine_cache_dir,
                    client=engine_client,
                    use_cache=engine_use_cache,
                    models=engine_models,
                    emit=emit,
                    now=now,
                )
            except Exception as exc:  # noqa: BLE001 - the engines must not sink the Google pull
                engines_box["error"] = _safe_error(exc)
            engines_box["seconds"] = time.perf_counter() - began

        engines_thread = threading.Thread(target=_engines, name=f"engines-{project.slug}", daemon=True)
        engines_thread.start()

    # 3. Crawl result pages and our own pages
    step_start = time.perf_counter()
    site_urls, site_notes = _site_urls(project, snapshot, fetcher, settings, delay_seconds, database)
    notes.extend(site_notes)
    statuses = _crawl_step(
        project, snapshot, site_urls, database, settings, fetcher, fresh_days, workers, delay_seconds, emit, now
    )
    counts = _count_codes(statuses)
    crawl_detail = (
        f"{len(statuses)} URLs: {counts.get('stored', 0)} already stored, {counts.get('fetched_now', 0)} fetched now, "
        f"{counts.get('kept_stored', 0)} kept an older stored copy after a failed refresh, "
        f"{counts.get('blocked_robots', 0)} blocked by robots, {counts.get('http_error', 0)} HTTP errors, "
        f"{counts.get('error', 0) + counts.get('unsafe', 0)} other errors."
    )
    steps.append(StepTiming("crawl", _round(time.perf_counter() - step_start), crawl_detail))
    emit("crawl", crawl_detail, len(statuses), len(statuses))

    # 4. Train the mini engine (or reuse the last one when nothing it learns from changed)
    step_start = time.perf_counter()
    engine = None
    train_detail = ""
    reuse = _reusable_engine(project, previous, snapshot, counts, site_urls)
    if reuse is not None:
        engine, reused_from = reuse
        train_detail = (
            f"Reused the engine of pull {reused_from}: no new Google results and no newly fetched pages, "
            f"so training again would give the same model. {engine.metrics.text}"
        )
    else:
        emit("train", "Training the mini search engine, one held-out model per search.", 0, len(project.searches))
        engine = _train_engine(project, snapshot, database, site_urls, today or _today(), statuses)
        train_detail = engine.metrics.text
    metrics = engine.metrics
    steps.append(StepTiming("train", _round(time.perf_counter() - step_start), train_detail))
    emit("train", train_detail, len(project.searches), len(project.searches))

    engines_note = "AI engines step skipped (no engines chosen)."
    engines_status: dict[str, str] = {}
    engines_snapshot = None
    engines_step: StepTiming | None = None
    if engines_thread is not None:
        engines_thread.join()
        engines_snapshot = engines_box.get("snapshot")
        if engines_snapshot is not None:
            engines_note = engines_snapshot.note
            engines_status = dict(engines_snapshot.status)
        else:
            engines_note = f"AI engines step failed ({engines_box.get('error', 'unknown error')}). No engine answers were invented."
        # Timed on its own: it ran alongside crawl and train, so it is not part of their sum.
        engines_step = StepTiming("engines", _round(engines_box.get("seconds", 0.0)), engines_note)

    # 5. Save. google.json goes last: a pull without it is still being written and is never 'the latest'.
    step_start = time.perf_counter()
    folder = project.dir / "pulls" / pull_id
    if engines_snapshot is not None:
        from aeo.ranksim.project_engines import save_engines

        save_engines(folder, engines_snapshot)
    _save_engine_atomic(folder / "engine", engine)
    _write_json(folder / "google.json", clean(snapshot.to_dict()))
    steps.append(StepTiming("save", _round(time.perf_counter() - step_start), f"Saved to {folder}."))
    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sources = sorted({item.source for item in snapshot.searches if item.results})
    report = PullReport(
        project=project.slug,
        pull_id=pull_id,
        kind="pull",
        started_at=started_at,
        finished_at=finished_at,
        google_kind=snapshot.kind,
        google_note=google_note,
        google_source="; ".join(sources),
        searches_total=len(project.searches),
        searches_with_results=with_results,
        urls=statuses,
        counts=counts,
        metrics=metrics,
        steps=steps,
        total_seconds=_round(time.perf_counter() - started),
        notes=notes,
        site_urls=site_urls,
        engines_note=engines_note,
        engines_status=engines_status,
        engines_step=engines_step,
    )
    _write_json(folder / "pull.json", report.to_dict())
    emit("done", f"Pull {pull_id} finished in {report.total_seconds:.1f} s.", 1, 1)
    return report


def _chosen_engines(engines: Sequence[str] | str | None) -> list[str]:
    """None means all four AI engines. 'none' or an empty list skips the step."""
    from aeo.ranksim.engines import ENGINE_KEYS, engine_key

    if engines is None:
        return list(ENGINE_KEYS)
    if isinstance(engines, str):
        engines = [part for part in re.split(r"[,\s]+", engines) if part]
    names = [str(item).strip() for item in engines if str(item).strip()]
    if not names or [item.lower() for item in names] == ["none"]:
        return []
    return list(dict.fromkeys(engine_key(item) for item in names))


class _Emitter:
    def __init__(self, progress: ProgressFn | None, started: float) -> None:
        self.progress = progress
        self.started = started
        self.lock = threading.Lock()

    def __call__(self, step: str, message: str, done: int = 0, total: int = 0) -> None:
        if self.progress is None:
            return
        event = ProgressEvent(step, plain_dashes(message), done, total, _round(time.perf_counter() - self.started))
        with self.lock:
            self.progress(event)


def _google_step(project, settings, previous, pull_id, depth, skip_google, serp_provider, emit, now):
    by_previous = previous.by_search() if previous else {}
    written = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    provider = None
    reason = ""
    if not skip_google:
        provider, reason = _google_provider(settings, serp_provider)
    if provider is None:
        carried = [by_previous[search] for search in project.searches if search in by_previous and by_previous[search].results]
        if not carried:
            borrowed = borrow_real_results(project)
            if borrowed:
                searches = []
                for search in project.searches:
                    entry = borrowed.get(search)
                    searches.append(entry if entry is not None else SearchResults(search=search, results=[], source="", provider="", pulled_at=None, error=NOT_RUN_GOOGLE))
                donors = sorted({entry.source.rsplit("copied from project ", 1)[-1].rstrip(")") for entry in borrowed.values()})
                why = "skipped (--skip-google)" if skip_google else (reason or NOT_RUN_GOOGLE)
                note = (
                    f"Google step {why}. This project had no REAL results yet, so the REAL results other projects in this app hold "
                    f"for the same searches were copied, with their own source and pull date: {len(borrowed)} of {len(project.searches)} "
                    f"searches, from project {', '.join(donors)}."
                )
                missing = len(project.searches) - len(borrowed)
                if missing:
                    note += f" {missing} searches have no REAL results: {NOT_RUN_GOOGLE}."
                snapshot = GoogleSnapshot(pull_id=pull_id, written_at=written, kind="carried", searches=searches, carried_from=None, note=note)
                return snapshot, note
            raise ProviderConfigError(
                f"No REAL Google results for {project.name}. Google step {NOT_RUN_GOOGLE}. "
                "No results were imported either, and no other project here has REAL results for these searches. Set one of those env vars, import a CSV of Google positions (query,url,position) on the project page, or run: "
                f"python -m aeo.ranksim project import-serp {project.slug} FILE.csv (query,url,position). "
                "Nothing was invented."
            )
        searches = []
        for search in project.searches:
            entry = by_previous.get(search)
            if entry is not None and entry.results:
                searches.append(copy.deepcopy(entry))
            else:
                searches.append(SearchResults(search=search, results=[], source="", provider="", pulled_at=None, error=NOT_RUN_GOOGLE))
        why = "skipped (--skip-google)" if skip_google else (reason or NOT_RUN_GOOGLE)
        note = (
            f"Google step {why}. Using the REAL results saved in pull {previous.pull_id}: "
            f"{len(carried)} of {len(project.searches)} searches, from {_sources_text(carried)}."
        )
        missing = len(project.searches) - len(carried)
        if missing:
            note += f" {missing} searches have no REAL results: {NOT_RUN_GOOGLE}."
        snapshot = GoogleSnapshot(pull_id=pull_id, written_at=written, kind="carried", searches=searches, carried_from=previous.pull_id, note=note)
        return snapshot, note
    try:
        searches = _pull_google(project, provider, by_previous, depth, emit)
    finally:
        closer = getattr(provider, "close", None)
        if callable(closer) and serp_provider is None:
            closer()
    failed = [item for item in searches if item.error and not item.results]
    note = f"REAL Google results pulled now from {provider.name} for {len(searches) - len(failed)} of {len(searches)} searches."
    kept = [item for item in searches if item.error and item.results]
    if kept:
        note += f" {len(kept)} searches failed now and kept their earlier REAL results, with the earlier pull date."
    if failed:
        note += f" {len(failed)} searches have no REAL results: " + "; ".join(f"{item.search} ({item.error})" for item in failed) + "."
    snapshot = GoogleSnapshot(pull_id=pull_id, written_at=written, kind="api", searches=searches, note=note)
    return snapshot, note


def borrow_real_results(project: Project) -> dict[str, SearchResults]:
    """REAL results other projects in the same folder hold for this project's searches (same words, any case).

    Each keeps its own source and pull date, and says which project it was copied from. Nothing is invented:
    a search no other project has stays without results.
    """
    wanted = {_fold(search): search for search in project.searches}
    found: dict[str, SearchResults] = {}
    for summary in list_projects(root=project.root or None):
        if summary.slug == project.slug:
            continue
        try:
            other = load_project(summary.slug, root=project.root or None)
        except ProjectError:
            continue
        if _is_synthetic(other):
            continue  # invented demo positions are never copied into another project as REAL results
        snapshot = latest_google(other)
        if snapshot is None:
            continue
        for entry in snapshot.searches:
            search = wanted.get(_fold(entry.search))
            if search is None or search in found or not entry.results:
                continue
            copied = copy.deepcopy(entry)
            copied.search = search
            copied.source = f"{entry.source} (copied from project {other.slug})"
            copied.error = ""
            found[search] = copied
    return found


def _is_synthetic(project: Project) -> bool:
    """True when the web workspace marked this project as synthetic demo data (workspace.json)."""
    data = _read_json(project.dir / "workspace.json") or {}
    return str(data.get("dataset") or "") == "synthetic"


def _google_provider(settings: Settings, serp_provider):
    if serp_provider is not None:
        if getattr(serp_provider, "synthetic", False):
            raise ProviderConfigError("That SERP provider is synthetic. It is never stored as a Google pull.")
        if not getattr(serp_provider, "is_google", False):
            return None, f"not run: {getattr(serp_provider, 'name', 'that provider')} is not Google"
        return serp_provider, ""
    from aeo.ranksim.serp import resolve_serp

    try:
        provider = resolve_serp(settings, "auto")
    except ProviderConfigError:
        return None, NOT_RUN_GOOGLE
    if provider.synthetic or not provider.is_google:
        closer = getattr(provider, "close", None)
        if callable(closer):
            closer()
        return None, f"{NOT_RUN_GOOGLE} (BRAVE_API_KEY is set, but Brave is not Google, so it was not used)"
    return provider, ""


def _pull_google(project, provider, by_previous, depth, emit) -> list[SearchResults]:
    location, language = _provider_locale(provider.name)
    done = 0
    lock = threading.Lock()
    found: dict[str, SearchResults] = {}

    def one(search: str) -> SearchResults:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            hits = provider.search(search, depth)
        except Exception as exc:  # noqa: BLE001 - one failed search must not end the pull
            error = _safe_error(exc)
            earlier = by_previous.get(search)
            if earlier is not None and earlier.results:
                kept = copy.deepcopy(earlier)
                kept.error = f"fresh pull failed ({error}); kept the earlier REAL results pulled {earlier.pulled_label}"
                return kept
            return SearchResults(search=search, results=[], source=provider.name, provider=provider.name, pulled_at=None, error=f"pull failed: {error}")
        results = []
        seen: set[str] = set()
        for hit in sorted(hits, key=lambda item: int(item.position)):
            key = loose_key(hit.url)
            if key in seen:
                continue  # the same page twice (http and https, tracking tags): keep its best position
            seen.add(key)
            results.append(
                SerpResult(position=int(hit.position), url=hit.url, domain=domain_of(hit.url), title=plain_dashes(hit.title), snippet=plain_dashes(hit.snippet))
            )
        return SearchResults(
            search=search,
            results=results,
            source=f"{provider.name} Google organic results",
            provider=provider.name,
            pulled_at=stamp,
            location=location,
            language=language,
            error="" if results else "the provider returned no organic results",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(one, search): search for search in project.searches}
        for future in as_completed(futures):
            item = future.result()
            with lock:
                found[item.search] = item
                done += 1
                emit("google", f"{item.search}: {len(item.results)} REAL results", done, len(project.searches))
    return [found[search] for search in project.searches]


def _provider_locale(name: str) -> tuple[str | None, str | None]:
    if name == "dataforseo":
        return "United States (location code 2840)", "en"
    if name == "serper":
        return "us", "en"
    return None, None


def _safe_error(exc: Exception) -> str:
    """Never echo a request URL: SerpAPI puts the key in the query string."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code:
        return f"HTTP {code}"
    return type(exc).__name__


def _site_urls(project: Project, snapshot: GoogleSnapshot, fetcher, settings, delay_seconds, database: str) -> tuple[list[str], list[str]]:
    """Our own pages: our URLs in the results, pages already stored for our domain, listed pages, the sitemap."""
    notes: list[str] = []
    urls: list[str] = list(project.site_pages)
    if project.sitemap_file:
        from aeo.ranksim.keywords import load_sitemap_urls

        try:
            urls.extend(sorted(load_sitemap_urls(Path(project.sitemap_file))))
        except OSError as exc:
            notes.append(f"Sitemap file {project.sitemap_file} could not be read: {exc}.")
    elif project.discover_sitemap:
        found, note = _discover_sitemap(project, fetcher, settings, delay_seconds)
        urls.extend(found)
        if note:
            notes.append(note)
    ours_in_results = [item.url for entry in snapshot.searches for item in entry.results if project.is_our_url(item.url)]
    ours_stored = sorted(row["url"] for row in _stored_index(database).values() if row["has_html"] and project.is_our_url(row["url"]))
    capped = _unique([url for url in urls if project.is_our_url(url)])[: max(0, project.max_site_pages)]
    return _unique([*ours_in_results, *ours_stored, *capped]), notes


def _discover_sitemap(project: Project, fetcher, settings, delay_seconds) -> tuple[list[str], str]:
    from aeo.crawl.sitemap import parse_sitemap

    fetch = fetcher or _crawler_batch(settings, delay_seconds)
    start = f"https://{project.site}/sitemap.xml"
    try:
        first = fetch([start])[0]
        if first.status != "fetched" or not first.html:
            return [], f"Sitemap {start} was not read ({first.error or first.status}). Our pages in the results are still crawled."
        kind, locs = parse_sitemap(first.html)
        if kind == "urlset":
            return locs[: project.max_site_pages], ""
        pages: list[str] = []
        for child in locs[:3]:
            item = fetch([child])[0]
            if item.status != "fetched" or not item.html:
                continue
            child_kind, child_locs = parse_sitemap(item.html)
            if child_kind == "urlset":
                pages.extend(child_locs)
            if len(pages) >= project.max_site_pages:
                break
        return pages[: project.max_site_pages], ""
    except (ValueError, UnsafeURL, OSError) as exc:
        return [], f"Sitemap {start} was not read ({exc}). Our pages in the results are still crawled."


def _crawl_step(project, snapshot, site_urls, database, settings, fetcher, fresh_days, workers, delay_seconds, emit, now) -> list[UrlStatus]:
    from aeo.ranksim.store import open_session

    searches_by_url: dict[str, list[str]] = defaultdict(list)
    first_form: dict[str, str] = {}
    for entry in snapshot.searches:
        for item in entry.results:
            key = _key(item.url)
            first_form.setdefault(key, item.url)
            if entry.search not in searches_by_url[key]:
                searches_by_url[key].append(entry.search)
    for url in site_urls:
        first_form.setdefault(_key(url), url)
    stored = _stored_index(database)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=fresh_days)
    statuses: dict[str, UrlStatus] = {}
    to_crawl: list[str] = []
    for key, url in first_form.items():
        row = stored.get(key)
        ours = project.is_our_url(url)
        if row is not None and row["has_html"] and row["fetched_at"] is not None and row["fetched_at"] >= cutoff:
            statuses[key] = UrlStatus(
                url=url,
                code="stored",
                status="stored",
                detail=f"stored copy from {_stamp(row['fetched_at'])}, within the {fresh_days:g} day freshness window",
                stored_at=_iso(row["fetched_at"]),
                ours=ours,
                searches=searches_by_url.get(key, []),
            )
        else:
            to_crawl.append(url)
    total = len(first_form)
    emit("crawl", f"{len(statuses)} of {total} pages are already stored and fresh. Fetching {len(to_crawl)}.", len(statuses), total)
    by_host: dict[str, list[str]] = defaultdict(list)
    for url in to_crawl:
        by_host[domain_of(url) or url].append(url)
    fetch = fetcher or _crawler_batch(settings, delay_seconds)
    done = len(statuses)
    session = open_session(database)
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_fetch_safely, fetch, urls): urls for urls in by_host.values()}
            for future in as_completed(futures):
                urls = futures[future]
                for url, result in zip(urls, future.result()):
                    key = _key(url)
                    row = stored.get(key)
                    target = row["url"] if row is not None else url  # refresh the existing row, not a twin
                    status = _record_fetch(session, target, result, row, project.is_our_url(url), searches_by_url.get(key, []))
                    status.url = url
                    statuses[key] = status
                    done += 1
                    emit("crawl", f"{status.status}: {url}", done, total)
    finally:
        session.close()
    _mark_seeded(database, statuses)
    ordered = [statuses[key] for key in first_form if key in statuses]
    return ordered


def _fetch_safely(fetch: FetchBatch, urls: list[str]) -> list[FetchResult]:
    try:
        results = list(fetch(urls))
    except Exception as exc:  # noqa: BLE001 - one host failing must not end the pull
        results = [FetchResult(url=url, html="", status="error", error=f"crawl failed: {type(exc).__name__}") for url in urls]
    while len(results) < len(urls):
        results.append(FetchResult(url=urls[len(results)], html="", status="error", error="no result returned"))
    return results[: len(urls)]


def _record_fetch(session, url: str, result: FetchResult, row: dict | None, ours: bool, searches: list[str]) -> UrlStatus:
    code, label = classify_fetch(result)
    if code == "fetched_now":
        store_fetched_page(session, FetchResult(url=url, html=result.html, status="fetched", fetch_ms=result.fetch_ms))
        stamp = datetime.now(timezone.utc)
        return UrlStatus(url=url, code=code, status=label, detail="fetched and stored now", stored_at=_iso(stamp), ours=ours, searches=searches)
    if row is not None and row["has_html"]:
        # A failed refresh never erases a stored copy.
        return UrlStatus(
            url=url,
            code="kept_stored",
            status=f"{label}, kept stored copy",
            detail=f"refresh failed ({result.error or label}); kept the stored copy from {_stamp(row['fetched_at'])}",
            stored_at=_iso(row["fetched_at"]),
            ours=ours,
            searches=searches,
        )
    store_fetched_page(session, FetchResult(url=url, html="", status="blocked" if code in {"blocked_robots", "unsafe"} else "error", error=result.error, fetch_ms=result.fetch_ms))
    return UrlStatus(url=url, code=code, status=label, detail=result.error or label, stored_at=None, ours=ours, searches=searches)


def classify_fetch(result: FetchResult) -> tuple[str, str]:
    """(code, plain words) for one fetch: fetched now, blocked by robots, HTTP 403, error."""
    if result.status == "fetched" and (result.html or "").strip():
        return "fetched_now", "fetched now"
    error = result.error or ""
    if result.status == "blocked":
        if "robots" in error.lower():
            return "blocked_robots", "blocked by robots"
        return "unsafe", "blocked (unsafe URL)"
    match = re.match(r"HTTP (\d{3})", error)
    if match:
        return "http_error", f"HTTP {match.group(1)}"
    return "error", "error"


def _crawler_batch(settings: Settings, delay_seconds: float) -> FetchBatch:
    """Polite, robots-respecting, SSRF-safe fetch of URLs on one host."""
    from aeo.crawl.fetch import Crawler

    def fetch(urls: list[str]) -> list[FetchResult]:
        crawler = Crawler(
            user_agent=settings.crawl_user_agent,
            delay_seconds=delay_seconds,
            allow_private=settings.crawl_allow_private,
            timeout=min(settings.request_timeout_seconds, 20.0),
        )
        out: list[FetchResult] = []
        try:
            for url in urls:
                began = time.perf_counter()
                try:
                    got = crawler.fetch(url)
                except UnsafeURL as exc:
                    out.append(FetchResult(url=url, html="", status="blocked", error=str(exc)))
                    continue
                elapsed = (time.perf_counter() - began) * 1000
                if got.blocked:
                    out.append(FetchResult(url=url, html="", status="blocked", error=got.error or "Blocked by robots.txt", fetch_ms=elapsed))
                elif got.error or not got.html:
                    out.append(FetchResult(url=url, html="", status="error", error=got.error or "empty response", fetch_ms=elapsed))
                else:
                    out.append(FetchResult(url=url, html=got.html, status="fetched", fetch_ms=elapsed))
        finally:
            crawler.close()
        return out

    return fetch


def _stored_index(database: str) -> dict[str, dict]:
    """Normalized URL to {url, status, fetched_at, has_html}. Reads light columns only."""
    from sqlalchemy import func

    from aeo.ranksim.store import RsPage, open_session

    session = open_session(database)
    try:
        rows = session.query(RsPage.url, RsPage.status, RsPage.fetched_at, func.length(RsPage.raw_html)).all()
    finally:
        session.close()
    found: dict[str, dict] = {}
    for url, status, fetched_at, size in rows:
        item = {"url": url, "status": status, "fetched_at": _aware(fetched_at), "has_html": status == "fetched" and bool(size)}
        key = _key(url)
        current = found.get(key)
        if current is None or (item["has_html"] and not current["has_html"]):
            found[key] = item
    return found


def _mark_seeded(database: str, statuses: dict[str, UrlStatus]) -> None:
    """Our stored pages that are copies of committed files, not crawls, say so."""
    from aeo.ranksim.store import RsPage, open_session
    from aeo.ranksim.tester import seed_origin

    ours = [status for status in statuses.values() if status.ours and status.code in {"stored", "kept_stored"}]
    if not ours:
        return
    session = open_session(database)
    try:
        for status in ours:
            for candidate in _variants(status.url):
                row = session.get(RsPage, candidate)
                if row is not None and (row.raw_html or "").strip():
                    origin = seed_origin(row.url, row.raw_html)
                    if origin:
                        status.seeded_from = origin
                        status.detail += f"; seeded from {origin}, not a crawl of the live site"
                    break
    finally:
        session.close()


# The mini engine


@dataclass
class _Engine:
    pages: dict
    fit: LooFit
    fallback: object | None
    metrics: EngineMetrics
    stored_at: dict[str, str]
    seeded: dict[str, str]


def _labels(snapshot: GoogleSnapshot) -> list[SerpLabel]:
    labels: list[SerpLabel] = []
    for entry in snapshot.searches:
        for item in entry.results:
            labels.append(SerpLabel(entry.search, item.url, float(item.position), None, entry.provider or "google"))
    return labels


def _train_engine(project, snapshot, database, site_urls, today, statuses=None) -> _Engine:
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.store import open_session

    labels = _labels(snapshot)
    urls = _unique([*(label.url for label in labels), *site_urls])
    session = open_session(database)
    try:
        pages = load_corpus_docs(session, urls=urls)
    finally:
        session.close()
    notes = _attach_link_graph(project, pages)
    stored_at: dict[str, str] = {}
    seeded: dict[str, str] = {}
    for status in statuses or []:
        if status.stored_at:
            stored_at[_key(status.url)] = status.stored_at
        if status.seeded_from:
            seeded[_key(status.url)] = status.seeded_from
    if not stored_at:
        for key, row in _stored_index(database).items():
            if row["has_html"] and row["fetched_at"] is not None:
                stored_at[key] = _iso(row["fetched_at"])
    fit, fallback, metrics = _fit_engine(project, labels, pages, today)
    if notes:
        metrics.text += " " + notes
    return _Engine(pages=pages, fit=fit, fallback=fallback, metrics=metrics, stored_at=stored_at, seeded=seeded)


def _attach_link_graph(project: Project, pages: dict) -> str:
    if not project.link_graph:
        return ""
    path = Path(project.link_graph)
    if not path.is_file():
        return f"Link graph file {project.link_graph} was not found, so link-graph features stay missing."
    from aeo.ranksim.linkgraph import attach_graph, load_graph

    attach_graph(pages, load_graph(str(path)))
    return ""


def _fit_engine(project: Project, labels: list[SerpLabel], pages: dict, today: date) -> tuple[LooFit, object | None, EngineMetrics]:
    intents = {search: project.intents.get(search) or guess_intent(search) for search in project.searches}
    examples, uncovered, _withheld = build_examples(labels, pages, intents, today=today)
    fit = loo_fit(examples, keep_models=True)
    by_search: dict[str, list[RankExample]] = defaultdict(list)
    for example in examples:
        by_search[example.query].append(example)
    usable = [row for rows in by_search.values() if len(rows) >= 2 for row in rows]
    fallback = None
    if len({row.query for row in usable}) >= 1 and any(search not in fit.models for search in project.searches):
        try:
            fallback = fit_lambdamart(usable)
        except Exception:  # noqa: BLE001 - LightGBM refuses some tiny sets; the search then gets no position
            fallback = None
    per_search: list[SearchBacktest] = []
    for search in project.searches:
        rows = by_search.get(search, [])
        model = fit.models.get(search)
        if model is None or len(rows) < 2:
            per_search.append(SearchBacktest(search, len(rows), 0, None, None, None))
            continue
        scores = predict_scores(model, rows)
        ordered = [scores[id(row)] for row in rows]
        correct, total = _pairs(rows, ordered)
        per_search.append(
            SearchBacktest(
                search=search,
                stored=len(rows),
                pairs=total,
                pairwise=_round(correct / total, 3) if total else None,
                ndcg=_round(_ndcg(rows, ordered), 3),
                top3_hit=_top3_hit(rows, ordered),
            )
        )
    metrics = EngineMetrics(
        pairwise=fit.pairwise,
        ndcg=fit.ndcg,
        spearman=fit.spearman,
        top3=fit.top3,
        n_searches=fit.n_queries,
        n_pairs=fit.n_pairs,
        uncovered=uncovered,
        calibration_threshold=fit.calibration.threshold,
        calibration_pairwise=fit.calibration.confident_pairwise,
        calibration_coverage=fit.calibration.confident_coverage,
        calibration_note=fit.calibration.note,
        per_search=per_search,
        feature_names=list(FEATURE_NAMES),
        trained_at=now_iso(),
    )
    metrics.text = backtest_text(metrics)
    return fit, fallback, metrics


def backtest_text(metrics: EngineMetrics) -> str:
    """How much to trust a PREDICTED slot, in plain words."""
    if metrics.pairwise is None:
        return (
            "No backtest: fewer than two searches have two or more stored REAL results. "
            "Treat every PREDICTED position as a guess."
        )
    line = (
        f"Backtest (PREDICTED order vs REAL Google order, each search held out): pairwise {_num(metrics.pairwise)} "
        f"on {metrics.n_pairs} pairs (0.500 is a coin flip), NDCG at 10 {_num(metrics.ndcg)}, "
        f"Spearman {_num(metrics.spearman)}, top-3 hit {_num(metrics.top3)}. {metrics.n_searches} searches. "
        f"{metrics.uncovered} REAL results had no stored HTML and were left out."
    )
    if metrics.pairwise <= 0.55:
        line += (
            " That is barely better than a coin flip, so read a PREDICTED slot as a smoke test, "
            "not as where Google will put the page."
        )
    return line


def _save_engine(folder: Path, engine: _Engine) -> None:
    unique: dict[int, PageDoc] = {}
    for doc in engine.pages.values():
        unique.setdefault(id(doc), doc)
    _write_json(
        folder / "pages.json",
        {"pages": [asdict(doc) for doc in unique.values()], "stored_at": engine.stored_at, "seeded": engine.seeded},
        indent=None,
    )
    models = {search: model.model_to_string() for search, model in engine.fit.models.items()}
    fallback = engine.fallback.model_to_string() if engine.fallback is not None else None
    _write_json(folder / "models.json", {"models": models, "fallback": fallback}, indent=None)
    _write_json(folder / "fit.json", asdict(engine.metrics))


def _save_engine_atomic(folder: Path, engine: _Engine) -> None:
    """Write the engine into a temp folder, then rename it into place, so a reader never sees half an engine."""
    import shutil

    temp = folder.with_name(folder.name + ".tmp")
    if temp.exists():
        shutil.rmtree(temp, ignore_errors=True)
    _save_engine(temp, engine)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    temp.replace(folder)


def _result_lists(snapshot: GoogleSnapshot, searches: Iterable[str]) -> dict[str, list[tuple[int, str]]]:
    by_search = snapshot.by_search()
    return {search: [(item.position, item.url) for item in (by_search[search].results if search in by_search else [])] for search in searches}


def _reusable_engine(
    project: Project, previous: GoogleSnapshot | None, snapshot: GoogleSnapshot, counts: dict[str, int], site_urls: Sequence[str] = ()
) -> tuple[_Engine, str] | None:
    """The previous pull's engine, when this pull added nothing it learns from: no new Google data, nothing fetched,
    and the same pages of your own site (another project may have stored more of them in the shared database)."""
    if previous is None or snapshot.kind != "carried" or counts.get("fetched_now", 0) or counts.get("kept_stored", 0):
        return None
    if _result_lists(previous, project.searches) != _result_lists(snapshot, project.searches):
        return None
    report = load_pull_report(project, previous.pull_id)
    if report is None or sorted(_unique(report.site_urls)) != sorted(_unique(site_urls)):
        return None
    folder = project.dir / "pulls" / previous.pull_id / "engine"
    if not (folder / "fit.json").is_file():
        return None
    engine = _load_engine(folder)
    if engine is None or set(engine.fit.models) - set(project.searches):
        return None
    return engine, previous.pull_id


def _engine_files_complete(folder: Path) -> bool:
    return all((folder / name).is_file() for name in ("fit.json", "pages.json", "models.json"))


def _load_engine(folder: Path) -> _Engine | None:
    import lightgbm as lgb

    fit_data = _read_json(folder / "fit.json")
    pages_data = _read_json(folder / "pages.json")
    models_data = _read_json(folder / "models.json")
    if not fit_data or not pages_data or models_data is None:
        return None
    metrics = EngineMetrics.from_dict(fit_data)
    if list(metrics.feature_names) != list(FEATURE_NAMES):
        return None
    pages: dict[str, PageDoc] = {}
    known = {item.name for item in fields(PageDoc)}
    for item in pages_data.get("pages") or []:
        doc = PageDoc(**{key: value for key, value in item.items() if key in known})
        pages[doc.url] = doc
        try:
            pages.setdefault(normalize_url(doc.url), doc)
        except ValueError:
            pass
    models = {search: lgb.Booster(model_str=text) for search, text in (models_data.get("models") or {}).items()}
    fallback_text = models_data.get("fallback")
    fallback = lgb.Booster(model_str=fallback_text) if fallback_text else None
    fit = LooFit(
        models=models,
        pairwise=metrics.pairwise,
        ndcg=metrics.ndcg,
        spearman=metrics.spearman,
        top3=metrics.top3,
        n_queries=metrics.n_searches,
        n_pairs=metrics.n_pairs,
        calibration=Calibration(metrics.calibration_threshold, metrics.calibration_pairwise, metrics.calibration_coverage, metrics.calibration_note),
    )
    return _Engine(pages=pages, fit=fit, fallback=fallback, metrics=metrics, stored_at=dict(pages_data.get("stored_at") or {}), seeded=dict(pages_data.get("seeded") or {}))


def load_project_context(
    slug: str | Project,
    *,
    root: str | Path | None = None,
    database: str | None = None,
    today: date | None = None,
    pull_id: str | None = None,
) -> ProjectContext:
    """The mini engine of the newest pull that has one. Reads saved files, so it takes about a second.

    When the newest REAL results have no saved engine (for example just after an
    import), the engine is trained now from the rank database and the context
    says so. That takes longer; run `project pull` to save one.
    """
    started = time.perf_counter()
    project = slug if isinstance(slug, Project) else load_project(slug, root=root)
    pulls = list_pulls(project)
    if pull_id is not None:
        pulls = [item for item in pulls if item.pull_id == pull_id]
        if not pulls:
            raise ProjectError(f"Pull {pull_id} not found for {project.slug}.")
    google = latest_google(project) if pull_id is None else _snapshot(project, pull_id)
    if google is None:
        raise ProjectError(
            f"{project.name} has no REAL Google results yet. Run project import-serp {project.slug} FILE.csv, "
            f"or project pull {project.slug} with {GOOGLE_KEYS} set."
        )
    notes: list[str] = []
    engine = None
    loaded_from = ""
    chosen = google.pull_id
    engine_dir = project.dir / "pulls" / google.pull_id / "engine"
    if engine_dir.is_dir():
        engine = _load_engine(engine_dir)
        if engine is not None:
            loaded_from = f"saved engine from pull {google.pull_id}"
        elif _engine_files_complete(engine_dir):
            notes.append("The saved engine was built with a different feature list, so it was retrained now.")
        else:
            notes.append(f"The saved engine of pull {google.pull_id} is incomplete, so it was retrained now.")
    if engine is None:
        from aeo.ranksim.store import RsPage, open_session

        database = database or Settings().ranksim_database_url
        session = open_session(database)
        try:
            ours = [row.url for row in session.query(RsPage.url).all() if project.is_our_url(row.url)]
        finally:
            session.close()
        report = load_pull_report(project, google.pull_id)
        site_urls = (report.site_urls if report and report.site_urls else ours)
        engine = _train_engine(project, google, database, site_urls, today or _today(), report.urls if report else None)
        loaded_from = f"trained now from the rank database on the REAL results of pull {google.pull_id}"
        notes.append(f"No saved engine for pull {google.pull_id}. Run project pull {project.slug} to save one; loading then takes about a second.")
    current = set(project.searches)
    stale = [item.search for item in google.searches if item.search not in current]
    if stale:
        notes.append(f"Searches dropped from the project since this pull are ignored: {', '.join(stale)}.")
    index = site_index(google, project.searches)
    ctx = ProjectContext(
        project=project,
        pull_id=chosen,
        google=google,
        labels=_labels(google),
        pages=engine.pages,
        fit=engine.fit,
        fallback_model=engine.fallback,
        metrics=engine.metrics,
        stored_at=engine.stored_at,
        seeded=engine.seeded,
        today=today or _today(),
        loaded_from=loaded_from,
        notes=notes,
        database=database,
        site_index=index,
        site_model=fit_site_history(index),
        site_backtest=site_history_backtest(index),
    )
    ctx.content_backtest = content_insertion_backtest(ctx)
    ctx.topic = topic_model(google, engine.pages, project.searches)
    if ctx.site_model is None:
        notes.append(
            "The site history slot needs REAL results for at least 2 searches, so the headline slot is the content model's."
        )
    ctx.load_seconds = _round(time.perf_counter() - started)
    return ctx


def content_insertion_backtest(ctx: ProjectContext) -> SlotBacktest:
    """The lab's insertion protocol for the content model, scored the way the board scores a page.

    For each search, the held-out model (it never saw that search) scores the
    stored REAL results. Each one is hidden in turn and slotted at 1 + the other
    stored results it scores above it. Measured against the REAL order of the
    stored results, next to always guessing the middle.
    """
    found = SlotErrors()
    by_search = ctx.google.by_search()
    for search in ctx.project.searches:
        entry = by_search.get(search)
        model = ctx.fit.models.get(search)
        if entry is None or model is None:
            continue
        stored: list[tuple[SerpResult, PageDoc]] = []
        seen: set[str] = set()
        for item in sorted(entry.results, key=lambda result: result.position):
            doc = _lookup(ctx.pages, item.url)
            if doc is None or not (doc.title or doc.body or "").strip() or _key(item.url) in seen:
                continue
            seen.add(_key(item.url))
            stored.append((item, doc))
        if len(stored) < 3:
            continue
        intent = ctx.project.intents.get(search) or guess_intent(search)
        docs = [doc for _item, doc in stored]
        raw = raw_features(search, intent, docs, ctx.today)
        examples = [RankExample(search, doc.url, 0.0, row, intent) for doc, row in zip(docs, raw)]
        scores = predict_scores(model, examples)
        values = [scores[id(example)] for example in examples]
        found.searches += 1
        for index, (item, _doc) in enumerate(stored):
            others = [other for other in range(len(stored)) if other != index]
            predicted = 1 + sum(1 for other in others if values[other] > values[index])
            true_slot = 1 + sum(1 for other in others if stored[other][0].position < item.position)
            new_site = known_site = False
            if ctx.site_index is not None:
                new_site = ctx.site_index.count(site_key(item.url), frozenset({search})) == 0
                known_site = ctx.site_index.count(site_key(item.url), frozenset({search}), item.url) > 0
            found.add(predicted, true_slot, len(others), new_site, known_site)
    return found.summary(CONTENT_METHOD, population="stored REAL results")


def topic_model(google: GoogleSnapshot, pages: dict, searches: Iterable[str]) -> TopicModel:
    """The on-topic check, calibrated on the stored REAL top 10 pages of each search."""
    wanted = set(searches)
    by_search: dict[str, list[PageDoc]] = {}
    titles: dict[str, list[str]] = {}
    for entry in google.searches:
        if entry.search not in wanted:
            continue
        docs = []
        names = []
        for item in entry.results:
            if not 1 <= item.position <= TOP_DEPTH:
                continue
            doc = _lookup(pages, item.url)
            if doc is not None:
                docs.append(doc)
            names.append(item.title or (doc.title if doc is not None else ""))
        by_search[entry.search] = docs
        titles[entry.search] = names
    return TopicModel(by_search, titles)


def _snapshot(project: Project, pull_id: str) -> GoogleSnapshot | None:
    data = _read_json(project.dir / "pulls" / pull_id / "google.json")
    return GoogleSnapshot.from_dict(data) if data else None


# Testing a page


def insertion_position(your_score: float, incumbent_scores: Sequence[float | None]) -> int:
    """Default slot: 1 + the number of incumbents the model scores above you.

    incumbent_scores are in REAL Google order. None is an incumbent with no stored
    HTML: it was not scored and is not counted, so the board also reports the
    range from counting none of them above you to counting all of them.
    A tie does not put an incumbent above you.
    """
    return 1 + sum(1 for score in incumbent_scores if score is not None and score > your_score)


def test_page(
    ctx: ProjectContext,
    html: str,
    url: str,
    *,
    source: str = "",
    insertion: InsertionFn = insertion_position,
    save: bool = True,
    now: datetime | None = None,
    llm=None,
    sim_runs: int = 3,
    settings: Settings | None = None,
    ai: bool = True,
    progress: Callable[[str, int, int], None] | None = None,
    sim_off: str = "",
) -> ProjectBoard:
    """Score a page on ALL of the project's searches. Every slot is PREDICTED.

    The REAL top 10 of each search stays in its REAL order; the model only says
    where this page slots in. The headline slot comes from site history (the
    predictor that did best in backtests; content blind); the content model's
    slot is kept as predicted_position. When the URL already ranks, this HTML
    stands in for the stored copy. Each version is saved under the project
    (TesterHistory) and compared with the previous version and with the stored
    copy of the URL, all rescored now on the same content model.

    With ai=True each search also gets the latest REAL AI engine citations from
    the project's pulls (or 'not run: needs <ENV VAR>'), and, when llm is a
    configured LLM, the PREDICTED citation simulation (sim_runs runs per search).
    sim_off is the status to show when llm is None on purpose (for example
    'not run: switched off for this test'). progress(message, done, total)
    reports the simulation.
    """
    began = time.perf_counter()
    project = ctx.project
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    typed = url
    url, url_note = resolve_test_url(ctx, url)
    base = page_from_html(url, html or "", is_ours=True)
    rows = [_score_search(ctx, search, base, url, insertion) for search in project.searches]
    score_s = time.perf_counter() - began
    digest = content_hash(html)

    stored_doc, stored_label = _stored_copy(ctx, url)
    stored_rows: list[SearchBoard] | None = None
    if stored_doc is not None:
        stored_rows = [_score_search(ctx, search, stored_doc, url, insertion, compare=False) for search in project.searches]

    history = TesterHistory(TesterHistory.default_path(url, project.dir / "tests"))
    current = PageScore(
        url=url,
        label="draft",
        source=source or "memory",
        content_hash=digest,
        word_count=int(base.word_count),
        scores=[_query_score(row) for row in rows],
        created=stamp,
    )
    saved = False
    if save:
        saved = history.append(current)
        _save_version_html(history, digest, html)
    entries = history.entries()
    if entries and entries[-1].get("content_hash") == digest:
        current.version = current.version or str(entries[-1].get("version") or "")
        entries = entries[:-1]
    previous = PageScore.from_dict(entries[-1]) if entries else None
    previous_rows = None
    previous_topic: dict[str, bool | None] = {}
    if previous is not None:
        previous_html = _version_html(history, previous.content_hash)
        if previous_html is not None:
            previous_base = page_from_html(url, previous_html, is_ours=True)
            rescored = [_score_search(ctx, search, previous_base, url, insertion, compare=False) for search in project.searches]
            previous_rows = {row.search: row.predicted_position for row in rescored}
            previous_topic = {row.search: (row.topic.on_topic if row.topic is not None else None) for row in rescored}
        else:
            previous_rows = {row.query: row.predicted_rank for row in previous.scores}
    stored_positions = {row.search: row.predicted_position for row in stored_rows} if stored_rows else {}
    stored_topic = {row.search: (row.topic.on_topic if row.topic is not None else None) for row in stored_rows} if stored_rows else {}

    baseline = "the stored copy" if stored_rows else (previous.version if previous else "")
    better = worse = same = 0
    regressions: list[str] = []
    for row in rows:
        if previous_rows is not None:
            row.previous_position = previous_rows.get(row.search)
            row.change_vs_previous = change_text(row.previous_position, row.predicted_position)
        if stored_rows:
            row.stored_copy_position = stored_positions.get(row.search)
            row.change_vs_stored = change_text(row.stored_copy_position, row.predicted_position)
            row.model_check = model_check_text(row.stored_copy_position, row.real_position, row.real_pulled)
        base_position = row.stored_copy_position if stored_rows else row.previous_position
        if not baseline or base_position is None:
            continue
        if row.predicted_position is None or row.predicted_position > base_position:
            row.flag = "WORSE"
            worse += 1
            now_text = f"PREDICTED #{row.predicted_position}" if row.predicted_position is not None else "no PREDICTED position"
            regressions.append(f"{row.search}: {now_text}, worse than {baseline} at PREDICTED #{base_position}.")
        elif row.predicted_position < base_position:
            row.flag = "better"
            better += 1
        else:
            row.flag = "same"
            same += 1

    # On-topic regressions: the baseline was on topic for a search and this version is not. This, not the
    # unvalidated content model, is what --fail-on-regression checks.
    topic_baseline = stored_topic if stored_rows else previous_topic
    topic_regressions: list[str] = []
    if baseline and topic_baseline:
        for row in rows:
            if topic_baseline.get(row.search) is True and row.topic is not None and row.topic.on_topic is not True:
                row.topic_regression = f"{row.search}: on topic in {baseline}, not now. {row.topic.text}"
                topic_regressions.append(row.topic_regression)

    ai_real_note = ai_sim_label = ""
    engines_pulls: list[str] = []
    ai_seconds = 0.0
    if ai:
        from aeo.ranksim.project_engines import attach_ai

        ai_began = time.perf_counter()
        ai_real_note, ai_sim_label, engines_pulls = attach_ai(
            ctx, rows, html, url, llm=llm, sim_runs=sim_runs, settings=settings, progress=progress, sim_off=sim_off
        )
        ai_seconds = time.perf_counter() - ai_began

    summary = _summary(ctx, rows, url)
    summary.better, summary.worse, summary.same, summary.baseline = better, worse, same, baseline
    if ai:
        from aeo.ranksim.project_engines import ai_summary_text

        summary.ai_text = ai_summary_text(rows)
    notes = list(ctx.notes)
    if url_note:
        notes.append(url_note)
    if not project.is_our_url(url):
        notes.append(
            f"This URL is not on your project site ({project.site}). 'Your site' numbers on this board are about {project.domain}; "
            f"the PREDICTED slot uses the site of the URL you gave ({domain_of(url) or typed})."
        )
    if base.word_count < THIN_WORD_GATE:
        notes.append(f"This page has {base.word_count} words. The engine ranks pages under {THIN_WORD_GATE} words behind every fuller page.")
    if _lookup(ctx.pages, url) is None and project.is_our_url(url):
        notes.append(
            "This URL is not in the engine's link graph, so it is scored as a new page: it gets your site's "
            "domain-level link signals but no links of its own yet."
        )
    board = ProjectBoard(
        project=project.slug,
        project_name=project.name,
        site=project.site,
        url=url,
        version=current.version or "unsaved",
        content_hash=digest,
        word_count=int(base.word_count),
        source=source or "memory",
        tested_at=stamp,
        pull_id=ctx.pull_id,
        google_source=_sources_text([item for item in ctx.google.searches if item.results]),
        google_pulled=summary.real_pulled,
        summary=summary,
        searches=rows,
        backtest=ctx.metrics,
        stored_copy=stored_label,
        previous_version=previous.version if previous else None,
        baseline=baseline,
        regressions=regressions,
        history_path=str(history.path),
        new_version_saved=saved,
        notes=notes,
        timing={
            "load_seconds": ctx.load_seconds,
            "score_seconds": _round(score_s),
            "ai_seconds": _round(ai_seconds),
            "total_seconds": _round(time.perf_counter() - began),
        },
        slot_method=ctx.slot_method,
        slot_backtest=ctx.site_backtest if ctx.site_model is not None else ctx.content_backtest,
        content_backtest=ctx.content_backtest,
        ai_real_note=ai_real_note,
        ai_sim_label=ai_sim_label,
        engines_pulls=engines_pulls,
        todo=todo_list([row.topic for row in rows if row.topic is not None]),
        topic_backtest=ctx.topic.backtest if ctx.topic is not None else None,
        topic_regressions=topic_regressions,
        slot_group_text=slot_group_text(ctx, rows),
    )
    return board


def slot_group_text(ctx: ProjectContext, rows: list[SearchBoard]) -> str:
    """The backtest number for pages like this one: a new page on a site that already ranks, or a brand new site."""
    backtest = ctx.site_backtest
    if ctx.site_model is None or backtest is None or backtest.mae is None:
        return ""
    counts = [row.your_site_searches for row in rows if row.your_site_searches is not None]
    if not counts:
        return ""
    if max(counts) > 0:
        if not backtest.known_site_n:
            return ""
        verdict = "no better than" if backtest.known_site_mae >= backtest.known_site_middle_mae - 0.1 else "better than"
        return (
            f"Pages like yours (a new page on a site that already ranks for other searches): in backtests the slot was off by "
            f"{backtest.known_site_mae:.1f} places on average, against {backtest.known_site_middle_mae:.1f} for always guessing the middle "
            f"of the list ({backtest.known_site_n} pages), so it is {verdict} the middle guess."
        )
    if not backtest.new_site_n:
        return ""
    verdict = "no better than" if backtest.new_site_mae >= backtest.new_site_middle_mae - 0.1 else "better than"
    return (
        f"Pages like yours (a site that is in none of your other searches): in backtests the slot was off by "
        f"{backtest.new_site_mae:.1f} places on average, against {backtest.new_site_middle_mae:.1f} for always guessing the middle "
        f"({backtest.new_site_n} pages), so it is {verdict} the middle guess."
    )


def loose_key(url: str) -> str:
    """The same page whatever the spelling: no scheme, no www, no trailing slash, no tracking tags, no fragment."""
    from urllib.parse import urlsplit

    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return text.lower()
    if not host:
        return text.lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host + (f":{port}" if port and port not in (80, 443) else "")
    path = (parts.path or "/").rstrip("/") or "/"
    query = "&".join(sorted(pair for pair in parts.query.split("&") if pair and not _tracking(pair.split("=", 1)[0])))
    return netloc + path + (f"?{query}" if query else "")


def _tracking(name: str) -> bool:
    name = name.lower()
    return any(name.startswith(prefix) if prefix.endswith("_") else name == prefix for prefix in TRACKING_PARAMS)


def strip_tracking(url: str) -> str:
    """url without tracking tags (utm_*, gclid ...) and without a #fragment."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return str(url or "").strip()
    query = "&".join(pair for pair in parts.query.split("&") if pair and not _tracking(pair.split("=", 1)[0]))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def resolve_test_url(ctx: ProjectContext, url: str) -> tuple[str, str]:
    """The URL to test under, and a note when it differs from what was typed.

    http vs https, www vs no www, a trailing slash and tracking tags all name the same page. When a REAL result,
    a stored page or an earlier tested version is that page, its spelling is used, so the stored copy is replaced
    (not counted as a competitor) and versions stay in one history.
    """
    key = loose_key(url)
    candidates: list[str] = []
    for entry in ctx.google.searches:
        candidates.extend(item.url for item in entry.results)
    candidates.extend(doc.url for doc in ctx.pages.values())
    candidates.extend(_tested_urls(ctx.project))
    for candidate in candidates:
        if loose_key(candidate) == key:
            if candidate == url:
                return url, ""
            if _key(candidate) == _key(url):
                return candidate, ""
            return candidate, f"You gave {url}; it is tested as {candidate}, the same page (scheme, www, trailing slash and tracking tags ignored)."
    cleaned = strip_tracking(url)
    if cleaned != str(url).strip():
        return cleaned, f"You gave {url}; it is tested as {cleaned} (tracking tags and #fragment removed)."
    return url, ""


def _tested_urls(project: Project) -> list[str]:
    found: list[str] = []
    folder = project.dir / "tests"
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.jsonl")):
        try:
            with path.open(encoding="utf-8") as handle:
                first = handle.readline()
            value = json.loads(first).get("url") if first.strip() else None
        except (OSError, ValueError, AttributeError):
            value = None
        if isinstance(value, str) and value:
            found.append(value)
    return found


test_page.__test__ = False  # a product function, not a pytest test


def _score_search(ctx: ProjectContext, search: str, base: PageDoc, url: str, insertion: InsertionFn, compare: bool = True) -> SearchBoard:
    project = ctx.project
    intent = project.intents.get(search) or guess_intent(search)
    entry = ctx.google.by_search().get(search)
    board = SearchBoard(search=search, intent=intent, real_source=entry.source if entry else "", real_pulled=entry.pulled_label if entry else "")
    board.backtest = ctx.metrics.for_search(search)
    if entry is None or not entry.results:
        board.error = (entry.error if entry and entry.error else "") or NOT_RUN_GOOGLE
        return board
    our_key = loose_key(url)
    incumbents: list[tuple[SerpResult, PageDoc | None]] = []
    seen: set[str] = set()
    for item in sorted(entry.results, key=lambda result: result.position):
        key = loose_key(item.url)
        if key in seen:
            continue  # the same page listed twice (http and https, tracking tags): keep its best REAL position
        seen.add(key)
        doc = _lookup(ctx.pages, item.url)
        stored = doc is not None and bool((doc.title or doc.body or "").strip())
        is_ours = key == our_key
        deep = not 1 <= item.position <= TOP_DEPTH
        board.real_results.append(
            RealResult(
                position=item.position,
                url=item.url,
                domain=item.domain or domain_of(item.url),
                title=plain_dashes(item.title or (doc.title if doc else "") or ""),
                stored=stored,
                stored_at=ctx.stored_at.get(_key(item.url)) if stored else None,
                tested_url=is_ours,
                our_site=project.is_our_url(item.url),
                beyond_top10=deep,
            )
        )
        if is_ours:
            if board.real_position is None:
                board.real_position = item.position
            continue
        if deep:
            continue
        incumbents.append((item, doc if stored else None))
    site_hits = [row for row in board.real_results if row.our_site]
    if site_hits:
        board.real_site_position = site_hits[0].position
        board.real_site_url = site_hits[0].url
    board.coverage_total = len(incumbents)
    board.coverage_stored = sum(1 for _item, doc in incumbents if doc is not None)
    board.thin = board.coverage_total > 0 and board.coverage_stored < board.coverage_total * THIN_SHARE
    board.coverage_text = (
        f"{board.coverage_stored} of {board.coverage_total} REAL top 10 results are stored and scored"
        + (". THIN: under half, so the content model slot says little." if board.thin else ".")
    )
    board.predicted_of = len(incumbents) + 1
    if not incumbents:
        if board.real_position is not None:
            board.error = (
                f"This URL is the only REAL top {TOP_DEPTH} result recorded for this search (REAL #{board.real_position}), "
                "so there is no other page to place it against. No position invented."
            )
        else:
            board.error = f"No REAL results in positions 1 to {TOP_DEPTH} were recorded for this search, so nothing to place against. No position invented."
        return board
    if ctx.topic is not None:
        board.topic = ctx.topic.check(search, base)
        if board.topic.on_topic is False or board.topic.status == "not checked":
            board.error = board.topic.text
            return board
    _site_slot(ctx, board, search, url, incumbents, base, compare)
    model, model_kind = ctx.model_for(search)
    board.model = model_kind
    scored = [(item, doc) for item, doc in incumbents if doc is not None]
    if model is None:
        board.error = "The engine has no model yet: fewer than two searches have two or more stored REAL results. No position invented."
        return board
    if not scored:
        board.error = "None of the other REAL results for this search are stored, so the content model has nothing to compare with. No position invented."
        return board
    ours = _our_doc(ctx, base, url)
    docs = [doc for _item, doc in scored] + [ours]
    raw = raw_features(search, intent, docs, ctx.today)
    examples = [RankExample(search, doc.url, 0.0, row, intent) for doc, row in zip(docs, raw)]
    scores = predict_scores(model, examples)
    values = [scores[id(example)] for example in examples]
    your_score = values[-1]
    score_by_key = {_key(item.url): values[index] for index, (item, _doc) in enumerate(scored)}
    in_order = [score_by_key.get(_key(item.url)) for item, _doc in incumbents]
    limit = len(incumbents) + 1
    position = max(1, min(int(insertion(your_score, in_order)), limit))
    board.predicted_position = position
    board.google_low, board.google_high = google_range(
        [item.position for item, _doc in incumbents], position, any(row.beyond_top10 for row in board.real_results)
    )
    # The range over the unstored incumbents, from the same insertion rule: none of them above you, then all of them.
    low_fill = [score if score is not None else -math.inf for score in in_order]
    high_fill = [score if score is not None else math.inf for score in in_order]
    best = max(1, min(int(insertion(your_score, low_fill)), limit))
    worst = max(1, min(int(insertion(your_score, high_fill)), limit))
    board.predicted_best, board.predicted_worst = min(best, worst, position), max(best, worst, position)
    for row in board.real_results:
        score = score_by_key.get(_key(row.url))
        if score is not None and not row.tested_url and not row.beyond_top10:
            row.model_scores_above_you = score > your_score
    if position > 1:
        board.above = _neighbor(incumbents[position - 2])
    if position - 1 < len(incumbents):
        board.below = _neighbor(incumbents[position - 1])
    board.confidence, board.confidence_note = _confidence(ctx, values, board.thin)
    if board.topic is not None and board.topic.status == "uncertain":
        board.confidence = "UNSURE"
        board.confidence_note = board.topic.text
    scaled = normalize_rows(raw)
    linear = [dot(HAND_WEIGHTS, row) for row in scaled]
    board.linear_position = 1 + sum(1 for value in linear[:-1] if value > linear[-1])
    if compare:
        gains = ctx.gains(model)
        raw_by_key = {_key(doc.url): (doc, raw[index]) for index, doc in enumerate(docs[:-1])}
        for item, doc in incumbents:
            their = score_by_key.get(_key(item.url))
            if their is None or their <= your_score:
                continue
            board.beaten_by.append(_comparison(item, doc, raw_by_key.get(_key(item.url)), ours, raw[-1], gains, their, your_score, project))
        score_of = {comp.url: comp for comp in board.above_slot}
        for item, _doc in incumbents:
            comp = score_of.get(item.url)
            their = score_by_key.get(_key(item.url))
            if comp is not None and their is not None:
                comp.model_scores_above_you = their > your_score
                comp.score_gap = _round(their - your_score, 4)
    return board


def google_range(positions: Sequence[int], slot: int, deeper: bool = False) -> tuple[int, int | None]:
    """The Google positions a slot stands for, from the REAL positions of its neighbors (incumbents in REAL order).

    With no gaps in the REAL list this is (slot, slot). Below REAL #a and above REAL #b it is a + 1 to b. Past the
    last REAL result of a list with gaps, or when deeper REAL results exist past unknown ones (deeper=True),
    the positions in between are unknown, so high is None ('or lower').
    """
    above = positions[slot - 2] if slot > 1 else 0
    low = above + 1
    if slot - 1 < len(positions):
        return low, max(low, int(positions[slot - 1]))
    contiguous = list(positions) == list(range(1, len(positions) + 1))
    return low, (low if contiguous and not deeper else None)


def google_label(low: int | None, high: int | None) -> str:
    if low is None:
        return "none"
    if high is None:
        return f"#{low} or lower"
    if high == low:
        return f"#{low}"
    return f"#{low} to #{high}"


def _site_slot(
    ctx: ProjectContext,
    board: SearchBoard,
    search: str,
    url: str,
    incumbents: list[tuple[SerpResult, PageDoc | None]],
    base: PageDoc | None = None,
    compare: bool = True,
) -> None:
    """The headline PREDICTED slot from site history. Needs REAL results only, not stored HTML."""
    if ctx.site_model is None or ctx.site_index is None or not incumbents:
        return
    band = ctx.site_backtest.band if ctx.site_backtest is not None else 3
    slot = site_history_position(ctx.site_model, ctx.site_index, url, search, [item.url for item, _doc in incumbents], band=band)
    if slot is None:
        return
    board.site_position = slot.position
    board.site_position_of = slot.of
    board.site_position_low = slot.low
    board.site_position_high = slot.high
    board.your_site_searches = slot.your_site_searches
    board.site_google_low, board.site_google_high = google_range(
        [item.position for item, _doc in incumbents], slot.position, any(row.beyond_top10 for row in board.real_results)
    )
    others = count_of(slot.your_site_searches, "other search", "other searches")
    board.site_note = (
        f"PREDICTED from where your site and these sites appear across your {ctx.site_model.n_searches} searches "
        f"(your site is in the REAL top 10 of {others}, not counting this URL). {CONTENT_BLIND_NOTE}"
    )
    if slot.position > 1:
        board.site_above = _neighbor(incumbents[slot.position - 2])
    if slot.position - 1 < len(incumbents):
        board.site_below = _neighbor(incumbents[slot.position - 1])
    if board.real_position is not None:
        board.site_check = site_check_text(slot.position, board.real_position, board.real_pulled)
    if compare:
        excluded = frozenset({search})
        for index, (item, doc) in enumerate(incumbents[: slot.position - 1]):
            theirs = ctx.site_index.count(site_key(item.url), excluded, url)
            board.above_slot.append(
                _site_comparison(
                    ctx.project, item, doc, base, url, search, slot.your_site_searches, theirs, 1.0 - slot.p_above[index], slot.expected_above
                )
            )


def _site_comparison(
    project: Project,
    item: SerpResult,
    doc: PageDoc | None,
    base: PageDoc | None,
    url: str,
    search: str,
    yours: int,
    theirs: int,
    p_above_you: float,
    expected_above: float | None = None,
) -> Comparison:
    """A REAL result above the headline slot: the site counts that put it there, then REAL facts next to yours."""
    from aeo.ranksim.relevance import search_words, words_found

    domain = item.domain or domain_of(item.url)
    comp = Comparison(
        real_position=item.position,
        url=item.url,
        domain=domain,
        title=plain_dashes(item.title or (doc.title if doc else "") or ""),
        stored=doc is not None,
        model_scores_above_you=None,
        their_site_searches=theirs,
        your_site_searches=yours,
        p_above_you=_round(p_above_you, 3),
        own_site=project.is_our_url(item.url),
    )
    if comp.own_site:
        comp.note = (
            "This is a page of your own site. A new page would compete with it for this search; "
            "Google usually shows only one or two pages of a site."
        )
    else:
        expected = (
            f" Your slot keeps the REAL order and comes after the {expected_above:.1f} pages site history expects to outrank you in all, "
            "so the REAL results ahead of that point sit above you."
            if expected_above is not None
            else ""
        )
        comp.note = (
            f"Site history: its site is in the REAL top 10 of {count_of(theirs, 'other search', 'other searches')}, yours in {yours}, "
            f"so it gets a {round(100 * p_above_you)}% PREDICTED chance of outranking you.{expected} Editing your page does not change this."
        )
    words = search_words(search)
    if words:
        their_title = item.title or (doc.title if doc else "")
        theirs_found = len(words_found(words, their_title)[0])
        yours_found = len(words_found(words, f"{base.title} {base.h1}")[0]) if base is not None else None
        fact = f"Search words in the title: theirs {theirs_found} of {len(words)}"
        if yours_found is not None:
            fact += f", yours (title or main heading) {yours_found} of {len(words)}"
        comp.facts.append(fact + ".")
    if doc is not None and base is not None and doc.word_count:
        comp.facts.append(f"Words on the page: theirs {doc.word_count}, yours {base.word_count}.")
    return comp


def site_check_text(predicted: int, real: int, pulled: str) -> str:
    """This URL already ranks: how far the site history slot is from its REAL position here."""
    if predicted == real:
        return f"Check (site history): PREDICTED #{predicted} and REAL #{real} ({pulled}). It matches here."
    gap = abs(real - predicted)
    side = "optimistic" if predicted < real else "pessimistic"
    return f"Check (site history): PREDICTED #{predicted} but REAL #{real} ({pulled}), {gap} {'place' if gap == 1 else 'places'} {side} here."


def _our_doc(ctx: ProjectContext, base: PageDoc, url: str) -> PageDoc:
    """This HTML with the link signals of the URL it will live at."""
    from aeo.ranksim.whatif import _copy_graph

    doc = copy.copy(base)
    doc.url = url
    existing = _lookup(ctx.pages, url)
    if existing is not None and existing is not base:
        _copy_graph(existing, doc)
        doc.orphan = existing.orphan
        doc.link_depth = existing.link_depth
        doc.crawl_inlinks = existing.crawl_inlinks
    elif existing is None:
        donor = _site_donor(ctx.pages, domain_of(url))
        if donor is not None:
            doc.domain_rank = donor.domain_rank
            doc.homepage_authority = donor.homepage_authority
    return doc


def _site_donor(pages: dict, domain: str) -> PageDoc | None:
    best = None
    for key, doc in pages.items():
        if key != doc.url or domain_of(doc.url) != domain:
            continue
        if best is None or (doc.domain_rank, doc.homepage_authority) > (best.domain_rank, best.homepage_authority):
            best = doc
    return best


def _neighbor(pair: tuple[SerpResult, PageDoc | None]) -> Neighbor:
    item, doc = pair
    return Neighbor(
        real_position=item.position,
        url=item.url,
        domain=item.domain or domain_of(item.url),
        title=plain_dashes(item.title or (doc.title if doc else "") or ""),
        stored=doc is not None,
    )


def _confidence(ctx: ProjectContext, values: list[float], thin: bool) -> tuple[str, str]:
    order = sorted(range(len(values)), key=lambda index: -values[index])
    ours = len(values) - 1
    place = order.index(ours)
    gaps = []
    if place > 0:
        gaps.append(abs(values[ours] - values[order[place - 1]]))
    if place + 1 < len(order):
        gaps.append(abs(values[ours] - values[order[place + 1]]))
    label, note = ctx.fit.calibration.product_label(min(gaps) if gaps else 0.0, synthetic=_is_synthetic(ctx.project))
    if thin and label != "UNSURE":
        return "UNSURE", "Fewer than half of the REAL results are stored, so this stays UNSURE. " + note
    return label, note


def _comparison(item, doc, raw_pair, ours: PageDoc, our_raw: dict, gains: dict, their_score, your_score, project: Project) -> Comparison:
    domain = item.domain or domain_of(item.url)
    row = Comparison(
        real_position=item.position,
        url=item.url,
        domain=domain,
        title=plain_dashes(item.title or (doc.title if doc else "") or ""),
        stored=doc is not None,
        model_scores_above_you=None if their_score is None else their_score > your_score,
        score_gap=None if their_score is None else _round(their_score - your_score, 4),
    )
    if doc is None or raw_pair is None:
        row.note = "Not stored, so it was not scored or compared. It holds its REAL position."
        return row
    their_raw = raw_pair[1]
    row.page_gaps = _gaps(PAGE_FEATURES, their_raw, our_raw, doc, ours, gains, limit=3)
    signals = [
        FeatureGap(
            feature="domain",
            label="domain",
            yours=None,
            theirs=None,
            yours_text=domain_of(ours.url) or "",
            theirs_text=domain,
            fix="Web-wide authority is not known: no backlink data source is configured.",
        )
    ]
    signals.extend(_gaps(SITE_FEATURES, their_raw, our_raw, doc, ours, gains, limit=3))
    row.site_signals = signals
    return row


def _gaps(names, their_raw: dict, our_raw: dict, their_doc: PageDoc, our_doc: PageDoc, gains: dict, limit: int) -> list[FeatureGap]:
    found: list[tuple[float, FeatureGap]] = []
    for name in names:
        if name == "speed" and our_doc.fetch_ms is None:
            continue  # your page was uploaded, never fetched, so its speed is unknown, not slow
        theirs = their_raw.get(name)
        yours = our_raw.get(name)
        if theirs is None or yours is None:
            continue
        their_missing = theirs != theirs
        your_missing = yours != yours
        if their_missing and your_missing:
            continue
        if their_missing:
            continue
        if your_missing:
            yours_value = 0.0
        else:
            yours_value = float(yours)
        better = _ahead(name, float(theirs), yours_value)
        if better <= MIN_GAP:
            continue
        weight = gains.get(name, 0.0) * better + better
        found.append(
            (
                weight,
                FeatureGap(
                    feature=name,
                    label=LABELS.get(name, name),
                    yours=None if your_missing else _round(yours_value, 4),
                    theirs=_round(float(theirs), 4),
                    yours_text=_feature_text(name, None if your_missing else yours_value, our_doc),
                    theirs_text=_feature_text(name, float(theirs), their_doc),
                    fix=_fix(name, our_doc, their_doc),
                ),
            )
        )
    found.sort(key=lambda pair: -pair[0])
    return [gap for _weight, gap in found[:limit]]


def _ahead(name: str, theirs: float, yours: float) -> float:
    """How far they are ahead on this feature, 0 when they are not."""
    if name in {"thin_content", "link_depth", "orphan", "site_radius", "ad_density"}:
        return yours - theirs  # lower is better
    return theirs - yours


def _feature_text(name: str, value: float | None, doc: PageDoc) -> str:
    if value is None:
        return "no match" if name.startswith("bm25") else "not measured"
    if name == "word_count":
        return f"{doc.word_count} words"
    if name in {"has_table", "has_faq", "has_headings", "has_body", "orphan"}:
        return "yes" if value >= 0.5 else "no"
    if name == "thin_content":
        return f"under {THIN_WORD_GATE} words" if value >= 0.5 else f"{doc.word_count} words"
    if name == "freshness":
        return f"dated {doc.updated}" if doc.updated else "no visible date"
    if name == "links":
        return f"{int(value)} links"
    if name in {"link_depth", "crawl_inlinks"}:
        return str(int(value))
    return f"{value:.2f}"


def _fix(name: str, our_doc: PageDoc | None = None, their_doc: PageDoc | None = None) -> str:
    if name == "word_count" and our_doc is not None and their_doc is not None and our_doc.word_count > their_doc.word_count:
        return (
            f"The engine prefers 800 to 2500 words. This page has {our_doc.word_count}. "
            "Cut repetition, not useful content, and do not pad shorter pages to match."
        )
    if name in SITE_FIXES:
        return SITE_FIXES[name]
    label = LABELS.get(name, name)
    return FIXES.get(name) or CONCRETE_FIXES.get(label) or f"Close the gap on {label}."


def _query_score(row: SearchBoard) -> QueryScore:
    return QueryScore(
        query=row.search,
        predicted_rank=row.predicted_position,
        predicted_of=row.predicted_of,
        confidence=row.confidence,
        linear_rank=row.linear_position,
        error=row.error,
        labeled=row.coverage_total,
        in_set=row.coverage_stored,
        missing=row.coverage_total - row.coverage_stored,
        labeled_position=float(row.real_position) if row.real_position is not None else None,
        on_topic=row.topic.on_topic if row.topic is not None else None,
    )


def _stored_copy(ctx: ProjectContext, url: str) -> tuple[PageDoc | None, str | None]:
    """The stored copy of this URL: the engine's page set first, then the shared rank database."""
    key = _key(url)
    doc = _lookup(ctx.pages, url)
    if doc is not None:
        when = ctx.stored_at.get(key)
        text = f"stored copy of this URL ({'stored ' + _stamp(when) if when else 'store date unknown'})"
        if ctx.seeded.get(key):
            text += f", seeded from {ctx.seeded[key]}, not a crawl of the live site"
        return doc, text
    if not ctx.database:
        return None, None
    from aeo.ranksim.tester import seed_origin, stored_html

    try:
        found = stored_html(ctx.database, url)
    except Exception:  # noqa: BLE001 - a missing database only means no stored copy
        found = None
    if found is None:
        return None, None
    html, stored_url, fetched = found
    text = f"stored copy of this URL ({stored_url}, stored {fetched}, outside the engine's page set)"
    origin = seed_origin(stored_url, html)
    if origin:
        text += f", seeded from {origin}, not a crawl of the live site"
    return page_from_html(url, html, is_ours=True), text


def _within(low_high: tuple[int | None, int | None], limit: int) -> bool:
    _low, high = low_high
    return high is not None and high <= limit


def _summary(ctx: ProjectContext, rows: list[SearchBoard], url: str) -> BoardSummary:
    """Counts use the headline slot (site history when it ran) in Google positions. Off-topic searches count as not placed."""
    total = len(rows)
    scored = [row for row in rows if row.headline() is not None]
    top3 = sum(1 for row in scored if _within(row.headline_google(), 3))
    top10 = sum(1 for row in scored if _within(row.headline_google(), 10))
    content = [row for row in rows if row.predicted_position is not None]
    real10 = sum(1 for row in rows if row.real_position is not None and row.real_position <= 10)
    site10 = sum(1 for row in rows if row.real_site_position is not None and row.real_site_position <= 10)
    on = sum(1 for row in rows if row.topic is not None and row.topic.on_topic is True)
    off = sum(1 for row in rows if row.topic is not None and row.topic.on_topic is False)
    unchecked = sum(1 for row in rows if row.topic is not None and row.topic.on_topic is None)
    pulled = _pulled_text([item for item in ctx.google.searches if item.results and item.search in set(ctx.project.searches)])
    text = (
        f"PREDICTED top 3 on {top3} of {total} searches, top 10 on {top10} of {total}. "
        f"REAL ({pulled}): top 10 on {real10} of {total}."
    )
    text += f" Your site ({ctx.project.domain}) has some page in the REAL top 10 on {site10} of {total}."
    if off:
        text += f" PREDICTED off topic on {off} of {total}, so not in the top 10 there."
    if unchecked:
        text += f" {unchecked} could not be checked (no readable text)."
    rest = total - len(scored) - off - unchecked
    if rest > 0:
        text += f" {rest} searches got no PREDICTED position (see each search)."
    return BoardSummary(
        text=text,
        searches=total,
        scored=len(scored),
        predicted_top3=top3,
        predicted_top10=top10,
        real_top10=real10,
        real_site_top10=site10,
        real_pulled=pulled,
        method=ctx.slot_method,
        content_top3=sum(1 for row in content if _within((row.google_low, row.google_high), 3)),
        content_top10=sum(1 for row in content if _within((row.google_low, row.google_high), 10)),
        on_topic=on,
        off_topic=off,
        not_checked=unchecked,
    )


def plain_lead(board: ProjectBoard) -> str:
    """One sentence a founder can read first. Every number in it is labeled."""
    return plain_lead_from_dict(board.to_dict())


def plain_lead_from_dict(data: dict) -> str:
    """plain_lead() for a board stored as JSON (ProjectBoard.to_dict())."""
    summary = data.get("summary") or {}
    total = int(summary.get("searches") or 0)
    pulled = summary.get("real_pulled") or PULL_DATE_NOT_RECORDED
    real = (
        f" REAL ({pulled}): this exact URL is in the top 10 for {summary.get('real_top10', 0)} of them; "
        f"some page of your site is for {summary.get('real_site_top10', 0)}."
    )
    if int(data.get("schema") or 1) < 2:
        if not summary.get("scored"):
            return f"No PREDICTED positions yet: none of the {total} searches has REAL Google results to compare with."
        return (
            f"Older result (made before the on-topic check): PREDICTED top 3 on {summary.get('predicted_top3', 0)} of {total} key searches "
            "from site history alone, which does not read the page." + real
        )
    on = int(summary.get("on_topic") or 0)
    off = int(summary.get("off_topic") or 0)
    unchecked = int(summary.get("not_checked") or 0)
    if not summary.get("scored") and not off and not unchecked:
        return f"No PREDICTED positions yet: none of the {total} searches has REAL Google results to compare with." + (real if summary.get("real_top10") or summary.get("real_site_top10") else "")
    lead = f"PREDICTED: your page is on topic for {on} of your {total} key searches"
    if off:
        lead += f" and off topic for {off}, where it is PREDICTED not to make the top 10"
    if unchecked:
        lead += f"; {unchecked} could not be checked because the page has almost no readable text"
    lead += "."
    if on and summary.get("scored"):
        lead += (
            f" Where it is on topic, your site's track record puts it in Google's top 3 on {summary.get('predicted_top3', 0)}"
            " (that slot comes from your site, not the page text)."
        )
    return lead + real


def model_check_text(predicted: int | None, real: int | None, pulled: str) -> str:
    """How far the engine is off on a page whose REAL position is known: the stored copy of this URL."""
    if predicted is None or real is None:
        return ""
    if predicted == real:
        return f"Check: the stored copy of this URL is PREDICTED #{predicted} and is REAL #{real} ({pulled}). The model matches here."
    side = "optimistic" if predicted < real else "pessimistic"
    gap = abs(real - predicted)
    places = "place" if gap == 1 else "places"
    return (
        f"Check: the stored copy of this URL is PREDICTED #{predicted} but is REAL #{real} ({pulled}), "
        f"so on this search the model is {gap} {places} {side}."
    )


def change_text(before: int | None, after: int | None) -> str:
    """'up 2' means 2 places better. Positions are PREDICTED."""
    if before is None or after is None:
        return "n/a"
    delta = before - after
    if delta == 0:
        return "same"
    return f"up {delta}" if delta > 0 else f"down {-delta}"


def _save_version_html(history: TesterHistory, digest: str, html: str) -> None:
    folder = history.path.with_suffix("")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.html"
    if not path.exists():
        path.write_text(html or "", encoding="utf-8")


def _version_html(history: TesterHistory, digest: str) -> str | None:
    path = history.path.with_suffix("") / f"{digest}.html"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


# Plain-language text for a founder


def render_board_text(board: ProjectBoard, *, detail_limit: int = 3) -> str:
    """detail_limit: how many pages per search to explain; a negative number explains all of them."""
    lines = [
        f"{board.project_name} ({board.site}): how {board.url} does on {board.summary.searches} key searches",
        PREDICTED_NOTE,
        f"{REAL_NOTE} Here: {board.google_source or 'no REAL results yet'}.",
        "",
        plain_lead(board),
        "",
        board.summary.text,
        board.top10_note,
        "",
    ]
    if board.todo:
        lines.append("To do, most searches first (from the on-topic check and what the REAL top 10 pages share):")
        for number, item in enumerate(board.todo, start=1):
            shown = ", ".join(item.searches[:4]) + (f" and {len(item.searches) - 4} more" if len(item.searches) > 4 else "")
            lines.append(f"  {number}. [{item.label}] {item.text} ({shown})")
        lines.append("")
    lines.extend(_accuracy_lines(board))
    if board.summary.ai_text:
        lines.append(f"AI engines: {board.summary.ai_text}")
    lines.extend(
        [
            "",
            f"This version: {board.version}, {board.word_count} words, from {board.source}, tested {_stamp(board.tested_at)}.",
        ]
    )
    if board.stored_copy:
        lines.append(f"Compared with the {board.stored_copy}, scored now on the same engine.")
    if board.previous_version:
        lines.append(f"Previous version: {board.previous_version}, rescored now on the same engine.")
    for note in board.notes:
        lines.append(note)
    lines.append("")
    table = [["search", "on topic?", "PREDICTED slot", "likely", "content model", "REAL now", "change vs stored", "change vs prev"]]
    for row in board.searches:
        head = row.headline()
        if head is None:
            slot = "none"
            span = "n/a"
        elif row.site_position is not None:
            slot = f"{row.headline_label()} of {row.site_position_of}"
            span = f"#{row.site_position_low} to #{row.site_position_high}"
        else:
            slot = f"{row.headline_label()} of {row.predicted_of}"
            span = "exact" if row.predicted_best == row.predicted_worst else f"#{row.predicted_best} to #{row.predicted_worst}"
        topic = row.topic.status if row.topic is not None else ("n/a" if row.error else "not checked")
        table.append(
            [
                row.search,
                topic,
                slot,
                span,
                google_label(row.google_low, row.google_high) if row.predicted_position is not None else "none",
                f"#{row.real_position}" if row.real_position is not None else ("not in top 10" if row.real_results else "n/a"),
                row.change_vs_stored,
                row.change_vs_previous,
            ]
        )
    lines.append("on topic? = the PREDICTED on-topic check. An off-topic page gets no slot: it is PREDICTED not to make the top 10.")
    lines.append(f"PREDICTED slot = where the {board.slot_method} predictor puts an on-topic page among the REAL results, which keep their REAL order.")
    lines.append("likely = the slot plus or minus the error the backtest misses by 80% of the time.")
    lines.append("content model = the page-text model (unvalidated, no better than guessing the middle). Changes vs the stored copy and the previous version come from it.")
    lines.append("REAL now = this URL's own REAL Google position. Every slot, range and change here is PREDICTED.")
    lines.extend(_table(table))
    lines.append("")
    for line in board.topic_regressions:
        lines.append(f"NO LONGER ON TOPIC: {line}")
    if board.summary.baseline:
        lines.append(
            f"Against {board.summary.baseline}: better on {board.summary.better}, worse on {board.summary.worse}, "
            f"same on {board.summary.same} searches (PREDICTED by the content model, unvalidated; it does not decide pass or fail)."
        )
    for line in board.regressions:
        lines.append(f"WORSE (content model): {line}")
    for index, row in enumerate(board.searches, start=1):
        lines.append("")
        lines.extend(_search_text(index, len(board.searches), row, board, detail_limit))
    lines.append("")
    lines.append(f"Engine: pull {board.pull_id}. History: {board.history_path}.")
    lines.append(
        ("Saved as " if board.new_version_saved else ("Same HTML as " if board.version != "unsaved" else "Not saved: "))
        + f"{board.version}. Load {board.timing.get('load_seconds', 0):.1f} s, scoring {board.timing.get('score_seconds', 0):.1f} s."
    )
    return plain_dashes("\n".join(lines) + "\n")


def _accuracy_lines(board: ProjectBoard) -> list[str]:
    """How much to trust a PREDICTED slot, measured on this project's REAL results."""
    lines: list[str] = []
    topic = board.topic_backtest
    if topic is not None and topic.text:
        lines.append(f"On-topic check: {topic.text}")
    if board.slot_method == SITE_METHOD and board.slot_backtest is not None:
        lines.append(f"How the PREDICTED slot is made: site history. {CONTENT_BLIND_NOTE}")
        lines.append(f"How accurate it is: {board.slot_backtest.text}")
        if board.slot_group_text:
            lines.append(board.slot_group_text)
    content = board.content_backtest
    if content is not None and content.mae is not None:
        lines.append(f"Content model (unvalidated): {content.text}")
    lines.append(f"Content model order backtest: {board.backtest.text}")
    lines.append(f"Confidence (content model): {board.backtest.calibration_note}")
    return lines


def _search_text(index: int, total: int, row: SearchBoard, board: ProjectBoard, limit: int) -> list[str]:
    lines = [f'{index}/{total} "{row.search}"']
    if row.error and row.predicted_position is None and row.site_position is None and not row.real_results:
        lines.append(f"   REAL results: {row.error}.")
        return lines
    if row.topic is not None:
        lines.append(f"   {row.topic.text}")
    if row.topic_regression:
        lines.append(f"   NO LONGER ON TOPIC against {board.baseline}.")
    if row.site_position is not None:
        where = f"   PREDICTED: your page slots in at {row.headline_label()} of {row.site_position_of} ({SITE_METHOD})"
        where += _between(row.site_above, row.site_below)
        lines.append(where + f". Likely #{row.site_position_low} to #{row.site_position_high}.")
        lines.append(f"   {row.site_note}")
        if row.predicted_position is not None:
            lines.append(
                f"   Content model (unvalidated) PREDICTED {google_label(row.google_low, row.google_high)} of {row.predicted_of}"
                f"{_between(row.above, row.below)}. Confidence {row.confidence}. Linear check PREDICTED #{row.linear_position}."
            )
        elif row.error:
            lines.append(f"   Content model: no position. {row.error}")
    elif row.predicted_position is not None:
        where = f"   PREDICTED: your page slots in at {google_label(row.google_low, row.google_high)} of {row.predicted_of} ({CONTENT_METHOD})"
        lines.append(where + _between(row.above, row.below) + f". Confidence {row.confidence}. Linear check PREDICTED #{row.linear_position}.")
    elif row.topic is None or row.topic.on_topic is True:
        lines.append(f"   PREDICTED: no position. {row.error}")
    if row.predicted_position is not None and row.predicted_best != row.predicted_worst:
        missing = count_of(row.coverage_total - row.coverage_stored, "REAL result is", "REAL results are")
        lines.append(
            f"   {missing} not stored, so the content model slot could be anywhere from #{row.predicted_best} to #{row.predicted_worst}."
        )
    if row.real_position is not None:
        lines.append(f"   REAL ({row.real_pulled}): this URL is #{row.real_position}.")
        if row.site_check:
            lines.append(f"   {row.site_check}")
        if row.model_check:
            lines.append(f"   {row.model_check}")
    else:
        site = f" Your site's best: REAL #{row.real_site_position} {row.real_site_url}." if row.real_site_position else " No page of your site is in them."
        lines.append(f"   REAL ({row.real_pulled}): this URL is not in the {len(row.real_results)} REAL results recorded.{site}")
    lines.append(f"   Coverage: {row.coverage_text}")
    if row.backtest and row.backtest.pairwise is not None:
        lines.append(
            f"   Backtest on this search: pairwise {_num(row.backtest.pairwise)} on {row.backtest.pairs} pairs "
            "(PREDICTED order of its REAL results vs the REAL order, model never saw this search)."
        )
    lines.append(f"   REAL top {len(row.real_results)} ({source_and_date(row.real_source, row.real_pulled)}):")
    inserted = False
    head = row.headline()
    you = f"      >>  YOUR PAGE (PREDICTED {row.headline_label()})"
    for result in row.real_results:
        if head is not None and not inserted and _slot_before(result, row):
            lines.append(you)
            inserted = True
        mark = "stored" if result.stored else "not stored"
        tags = []
        if result.tested_url:
            tags.append("this URL")
        elif result.our_site:
            tags.append("your site")
        if result.beyond_top10:
            tags.append("beyond the top 10, not used")
        tag = f" [{', '.join(tags)}]" if tags else ""
        title = f"  {result.title[:60]}" if result.title else ""
        lines.append(f"      #{result.position:<3} {result.domain}{tag}{title}  ({mark})")
    if head is not None and not inserted:
        lines.append(you)
    shown = row.above_slot if limit < 0 else row.above_slot[:limit]
    if shown:
        lines.append(f"   Why these pages sit above your slot (site history, REAL counts; editing the page does not change it):")
        for comp in shown:
            lines.append(f"      REAL #{comp.real_position} {comp.domain}: {comp.note}")
            for fact in comp.facts:
                lines.append(f"         REAL: {fact}")
        hidden = len(row.above_slot) - len(shown)
        if hidden > 0:
            lines.append(f"   {count_of(hidden, 'more page above your slot is', 'more pages above your slot are')} listed with --details -1 or in --json.")
    unscored = [result for result in row.real_results if not result.stored and not result.tested_url and not result.beyond_top10]
    if row.predicted_position is not None:
        if row.beaten_by:
            lines.append(
                f"   Content model (unvalidated) scores these above you: {count_of(len(row.beaten_by), 'stored page', 'stored pages')} (REAL #"
                + ", #".join(str(comp.real_position) for comp in row.beaten_by)
                + ")."
            )
        else:
            lines.append("   Content model (unvalidated) scores none of the stored pages above you.")
        if unscored:
            lines.append("   Not stored, so unknown: REAL #" + ", #".join(str(result.position) for result in unscored) + ".")
    ordered = sorted(row.beaten_by, key=lambda comp: (comp.score_gap if comp.score_gap is not None else 0.0))
    nearest = ordered if limit < 0 else ordered[:limit]
    if nearest:
        lines.append(f"   Content model (unvalidated) reasons for the {len(nearest)} closest, not proven to move your rank:")
        for comp in nearest:
            lines.append(f"      REAL #{comp.real_position} {comp.domain}:")
            if comp.note:
                lines.append(f"         {comp.note}")
            if comp.page_gaps:
                for gap in comp.page_gaps:
                    lines.append(f"           {gap.label}: yours {gap.yours_text}, theirs {gap.theirs_text}. {gap.fix}")
            elif comp.stored:
                lines.append("         On the page: no feature where they are clearly ahead.")
        hidden = len(row.beaten_by) - len(nearest)
        if hidden > 0:
            lines.append(f"   {count_of(hidden, 'more page is', 'more pages are')} explained with --details -1 or in --json.")
    lines.extend(_ai_text(row))
    return lines


def _ai_text(row: SearchBoard) -> list[str]:
    lines: list[str] = []
    not_run = [engine for engine in row.engines if engine.status.startswith("not run")]
    if not_run:
        lines.append("   AI engines not run here: " + "; ".join(f"{engine.name}: {engine.status}" for engine in not_run) + ".")
    for engine in row.engines:
        if engine in not_run:
            continue
        if engine.status != "ok":
            lines.append(f"   {engine.name}: {engine.status}")
            continue
        cited = (
            f"cites your site in {engine.our_cited_runs} of {engine.ok_runs} answers"
            + (f", first at citation #{engine.our_mean_position:.0f}" if engine.our_mean_position is not None else "")
            if engine.our_cited_runs
            else f"does not cite your site ({engine.ok_runs} answers)"
        )
        top = ", ".join(engine.top_domains[:4]) or "nothing"
        lines.append(f"   {engine.name} REAL ({_stamp(engine.pulled_at)}, {engine.model}): {cited}. Most cited: {top}.")
        if engine.note:
            lines.append(f"      {engine.note}")
    sim = row.simulation
    if sim is not None:
        if sim.status != "ok":
            lines.append(f"   AI answer simulation (PREDICTED): {sim.status}")
        else:
            share = "n/a" if sim.your_share is None else f"{round(100 * sim.your_share)}%"
            lines.append(
                f"   AI answer simulation (PREDICTED, our own LLM {sim.model} answering from the Google top 10, not the real engine): "
                f"your page cited in {sim.your_cited_runs} of {sim.ok_runs} runs ({share})."
            )
        for flag in sim.flags:
            lines.append(f"   WARNING, instruction-like text in a source (the simulation may be swayed by it): {flag}")
    return lines


def _between(above: Neighbor | None, below: Neighbor | None) -> str:
    if above and below:
        return f", between REAL #{above.real_position} {_short(above)} and REAL #{below.real_position} {_short(below)}"
    if above:
        return f", below REAL #{above.real_position} {_short(above)}, under every REAL result"
    if below:
        return f", above REAL #{below.real_position} {_short(below)}, first"
    return ""


def _slot_before(result: RealResult, row: SearchBoard) -> bool:
    if result.tested_url:
        return False
    below = row.site_below if row.site_position is not None else row.below
    if below is not None:
        return result.url == below.url
    return False


def _short(neighbor: Neighbor) -> str:
    return neighbor.domain + (f" ({neighbor.title[:50]})" if neighbor.title else "")


def render_pull_text(report: PullReport) -> str:
    lines = [f"Pull {report.pull_id} for {report.project}: {report.total_seconds:.1f} s in total."]
    lines.append(f"Google: {report.google_note}")
    for step in report.all_steps():
        extra = " (in the background, alongside crawl and train)" if step.step == "engines" else ""
        lines.append(f"  {step.step}: {step.seconds:.1f} s{extra}. {step.detail}")
    lines.append(CRAWL_NOTE)
    problems = [item for item in report.urls if item.code not in {"stored", "fetched_now"}]
    if problems:
        lines.append("Pages we could not fetch now:")
        for item in problems:
            lines.append(f"  {item.status}: {item.url}" + (f" ({item.detail})" if item.detail and item.detail != item.status else ""))
    if report.metrics is not None:
        lines.append(f"Mini engine: {report.metrics.text}")
        weak = [row for row in report.metrics.per_search if row.pairwise is None]
        if weak:
            lines.append("No held-out backtest for: " + ", ".join(row.search for row in weak) + " (too few stored REAL results).")
    for note in report.notes:
        lines.append(note)
    return plain_dashes("\n".join(lines) + "\n")


def render_status_text(status: ProjectStatus) -> str:
    project = status.project
    lines = [
        f"{project['name']} ({project['site']}), slug {project['slug']}, created {_stamp(project['created'])}.",
        f"{len(project['searches'])} key searches.",
    ]
    if status.google is None:
        lines.append(f"No REAL Google results yet. Google: {NOT_RUN_GOOGLE}. Or run project import-serp {project['slug']} FILE.csv.")
    else:
        lines.append(f"Latest REAL Google results: pull {status.google.pull_id}. {status.google.note}")
    table = [["search", "intent", "REAL results", "stored", "pulled", "note"]]
    for search in project["searches"]:
        info = status.coverage.get(search) or {}
        table.append(
            [
                search,
                project["intents"].get(search, ""),
                str(info.get("results", 0)),
                "n/a" if info.get("stored") is None else str(info.get("stored")),
                info.get("pulled") or "n/a",
                info.get("error") or "",
            ]
        )
    lines.extend(_table(table))
    if status.site_backtest is not None:
        lines.append(f"PREDICTED slot (site history): {status.site_backtest.text}")
    if status.report is not None and status.report.metrics is not None:
        lines.append(f"Content model (pull {status.report.pull_id}): {status.report.metrics.text}")
    if status.report is not None and status.report.engines_note:
        lines.append(f"AI engines (pull {status.report.pull_id}): {status.report.engines_note}")
    lines.append("Pulls:")
    for item in status.pulls:
        engine = "engine saved" if item.has_engine else "no engine"
        took = f", {item.total_seconds:.1f} s" if item.total_seconds else ""
        lines.append(f"  {item.pull_id}: {item.what}; {item.searches_with_results} searches have REAL results; {engine}{took}.")
    return plain_dashes("\n".join(lines) + "\n")


def render_list_text(rows: list[ProjectSummary]) -> str:
    if not rows:
        return "No projects yet. Create one with: python -m aeo.ranksim project create NAME --site DOMAIN --searches 'a; b'\n"
    table = [["slug", "name", "site", "searches", "latest pull", "REAL results from"]]
    for row in rows:
        table.append([row.slug, row.name, row.site, str(row.searches), row.latest_pull or "none", row.latest_google or "none"])
    return plain_dashes("\n".join(_table(table)) + "\n")


def _table(rows: list[list[str]]) -> list[str]:
    widths = [max(len(str(row[index])) for row in rows) for index in range(len(rows[0]) - 1)]
    out = []
    for row in rows:
        cells = [str(cell).ljust(widths[index]) for index, cell in enumerate(row[:-1])]
        out.append("  ".join(cells + [str(row[-1])]).rstrip())
    return out


# Small helpers


def clean(value):
    """House style for every output string: no em or en dashes, no NaN."""
    if isinstance(value, str):
        return plain_dashes(value)
    if isinstance(value, float):
        return None if value != value or value in (math.inf, -math.inf) else value
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pulled_label(pulled_at: str | None) -> str:
    if not pulled_at:
        return PULL_DATE_NOT_RECORDED
    text = str(pulled_at)
    if len(text) == 10:
        return f"{text} (date only)"
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return _stamp(stamp)


def _pulled_text(entries: list[SearchResults]) -> str:
    labels = sorted({item.pulled_label for item in entries})
    if not labels:
        return "no REAL results"
    if len(labels) == 1:
        return labels[0]
    return "pulled at different times: " + ", ".join(labels)


def _sources_text(entries: list[SearchResults]) -> str:
    sources = sorted({item.source for item in entries if item.source})
    return "; ".join(sources) if sources else "unknown source"


def _snapshot_label(snapshot: GoogleSnapshot) -> str:
    entries = [item for item in snapshot.searches if item.results]
    return source_and_date(_sources_text(entries), _pulled_text(entries))


def source_and_date(source: str, pulled: str) -> str:
    """'source, pulled', without saying "pull date not recorded" twice."""
    if not pulled or pulled in source:
        return source
    return f"{source}, {pulled}"


def _stamp(value) -> str:
    if value is None:
        return "unknown time"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    value = _aware(value)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _iso(value) -> str | None:
    value = _aware(value)
    return value.isoformat(timespec="seconds") if value is not None else None


def _aware(value):
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _new_pull_id(project: Project, now: datetime | None) -> str:
    base = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pulls_dir = project.dir / "pulls"
    candidate = base
    counter = 2
    while (pulls_dir / candidate).exists():
        candidate = f"{base}-{counter}"
        counter += 1
    (pulls_dir / candidate).mkdir(parents=True, exist_ok=False)
    return candidate


def _drop_empty(folder: Path) -> None:
    try:
        folder.rmdir()
    except OSError:
        pass


def _count_codes(statuses: list[UrlStatus]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in statuses:
        counts[item.code] += 1
    return dict(counts)


def _lookup(pages: dict, url: str) -> PageDoc | None:
    for candidate in _variants(url):
        if candidate in pages:
            return pages[candidate]
    return None


def _variants(url: str) -> list[str]:
    found = [url]
    try:
        normal = normalize_url(url)
    except ValueError:
        return found
    for item in (normal, normalize_url(url, keep_trailing_slash=True), normal.rstrip("/") + "/"):
        if item not in found:
            found.append(item)
    return found


def _key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url


def _unique(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        key = _key(url)
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def _fold(text: str) -> str:
    return " ".join(str(text).casefold().split())


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _project_files(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(path for path in base.glob("*/project.json") if path.is_file())


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict, indent: int | None = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def url_digest(url: str) -> str:
    return hashlib.sha1(_key(url).encode("utf-8")).hexdigest()[:12]
