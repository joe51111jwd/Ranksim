"""Pairwise order of a known top 10. Scores are PREDICTED. Positions are labels.

The candidate set is the outcome top 10. Outcome positions are never features.
A page with no HTML still gets a score. History is earlier snapshots only.

Within-query z-scores use that query's candidate set, which a reranker can see.
Global mean and standard deviation are fit on the training rows and stored with
the model. They are not recomputed on the evaluation snapshot.
"""

from __future__ import annotations

import csv
import json
import math
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from aeo.crawl.extract import extract_html
from aeo.crawl.urls import domain_of
from aeo.ranksim.bm25f import bm25f_scores
from aeo.ranksim.features import freshness_score, url_text
from aeo.ranksim.full_coverage import content_tokens, load_html_dir, population_z, url_query_overlap
from aeo.ranksim.metrics import mean_metric, ndcg_at_k, spearman
from aeo.ranksim.offpage import load_majestic_million, load_tranco_popularity
from aeo.ranksim.site_history import site_key, url_key
from aeo.ranksim.snapshot_signals import page_type_features
from aeo.textutil import tokenize_for_search

PAIR_MODES = ("all", "surviving_surviving", "new_vs_surviving", "new_vs_new")
Z_FEATURES = ("bm25f", "word_count_log", "tranco_popularity", "majestic_log", "cc_pagerank", "cc_harmonic")
HISTORY_FEATURES = (
    "has_history",
    "last_relevance",
    "url_mean_relevance",
    "appearances",
    "trend_relevance",
    "history_domain_gap",
    "domain_mean_relevance",
    "domain_query_relevance",
    "domain_seen",
    "domain_count_log",
    "replaced_domain_relevance",
    "url_overlap",
    "url_transfer",
    "domain_transfer",
    "url_path_depth",
    "serp_title_coverage",
    "serp_title_phrase",
)
CONTENT_FEATURES = (
    "content_present",
    "bm25f",
    "title_coverage",
    "h1_coverage",
    "body_coverage",
    "serp_vocab_coverage",
    "word_count_log",
    "freshness",
    "freshness_known",
    "pt_review",
    "pt_forum",
    "pt_article",
    "pt_listicle",
    "pt_home",
    "pt_video",
    "pt_docs",
    "pt_product",
    "ix_review_alternative",
    "ix_listicle_list",
    "ix_listicle_alternative",
    "ix_forum_alternative",
    "ix_product_list",
)
OFFPAGE_TM_FEATURES = ("tranco_listed", "tranco_popularity", "majestic_listed", "majestic_log")
OFFPAGE_CC_FEATURES = ("cc_listed", "cc_pagerank", "cc_harmonic")
SERP_EXTRA_FEATURES = ("serp_snippet_coverage", "serp_date_known", "serp_date_freshness", "niche_title_coverage")
ALL_FEATURES = HISTORY_FEATURES + CONTENT_FEATURES + OFFPAGE_TM_FEATURES + OFFPAGE_CC_FEATURES
MONOTONE_UP = frozenset(
    {
        "has_history",
        "last_relevance",
        "url_mean_relevance",
        "appearances",
        "domain_mean_relevance",
        "domain_query_relevance",
        "domain_seen",
        "replaced_domain_relevance",
        "url_overlap",
        "url_transfer",
        "domain_transfer",
        "serp_title_coverage",
        "serp_title_phrase",
        "content_present",
        "bm25f",
        "title_coverage",
        "h1_coverage",
        "body_coverage",
        "serp_vocab_coverage",
        "freshness",
        "freshness_known",
        "tranco_listed",
        "tranco_popularity",
        "majestic_listed",
        "majestic_log",
        "cc_listed",
        "cc_pagerank",
        "cc_harmonic",
    }
)
# Fixed before any held-out score. Higher score is better. last_relevance is 0 for a new URL.
PRECOMMITTED_WEIGHTS = {
    "last_relevance": 5.0,
    "domain_query_relevance": 1.0,
    "domain_mean_relevance": 0.5,
    "url_transfer": 0.8,
    "domain_transfer": 0.5,
    "replaced_domain_relevance": 0.7,
    "serp_title_coverage": 8.0,
    "serp_title_phrase": 1.5,
    "url_overlap": 6.0,
    "bm25f": 0.8,
    "title_coverage": 4.0,
    "h1_coverage": 2.0,
    "body_coverage": 1.5,
    "serp_vocab_coverage": 2.0,
    "freshness": 0.6,
    "content_present": 0.3,
    "tranco_popularity": 0.4,
    "majestic_log": 0.4,
    "cc_pagerank": 0.4,
    "ix_review_alternative": 1.2,
    "ix_listicle_list": 1.0,
    "ix_forum_alternative": 0.8,
    "ix_product_list": 0.5,
}
BLEND_ALPHAS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
_LISTICLE = re.compile(r"\b(best|top\s+\d+|\d+\s+best|alternatives)\b", re.I)
_DAY = re.compile(r"(20\d{2}-\d{2}-\d{2})")


@dataclass
class SnapRow:
    query: str
    url: str
    position: float
    title: str
    url_key: str
    site: str
    snippet: str = ""
    serp_date: str = ""


@dataclass
class Snap:
    day: str
    rows: list[SnapRow]
    directory: Path | None = None
    serp_path: Path | None = None


@dataclass
class PageView:
    title: str = ""
    h1: str = ""
    headings: str = ""
    body: str = ""
    word_count: int = 0
    published: str | None = None
    updated: str | None = None
    types: dict[str, float] = field(default_factory=dict)
    present: bool = False


@dataclass
class Offpage:
    tranco: dict[str, float] = field(default_factory=dict)
    majestic: dict[str, float] = field(default_factory=dict)
    cc_pagerank: dict[str, float] = field(default_factory=dict)
    cc_harmonic: dict[str, float] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    def lookup(self, table: dict[str, float], url: str) -> float | None:
        site = site_key(url)
        if site in table:
            return table[site]
        host = domain_of(url)
        if host in table:
            return table[host]
        return None


@dataclass
class Row:
    day: str
    query: str
    url: str
    url_key: str
    position: float
    bucket: str
    group: str
    features: dict[str, float]


def relevance(position: float) -> float:
    if position <= 0 or position >= 11:
        return 0.0
    return float(11.0 - position)


def query_group(query: str) -> str:
    text = query.lower()
    if "alternative" in text:
        return "alternative"
    if "best" in text or text.startswith("top "):
        return "list"
    return "other"


def day_from_path(path: Path) -> str:
    match = _DAY.search(str(path))
    if not match:
        raise ValueError(f"No YYYY-MM-DD in {path}")
    return match.group(1)


def _dedupe(rows: list[SnapRow]) -> list[SnapRow]:
    best: dict[tuple[str, str], SnapRow] = {}
    for row in rows:
        key = (row.query, row.url_key)
        current = best.get(key)
        if current is None or row.position < current.position:
            best[key] = row
    return list(best.values())


def _serp_snippet_and_date(directory: Path, csv_row: dict, key: str) -> tuple[str, str]:
    """Snippet and visible date from the saved Serper payload. The position field is not read."""
    name = (csv_row.get("raw") or "").strip()
    if not name:
        return "", ""
    path = (directory / name).resolve()
    cache = getattr(_serp_snippet_and_date, "_cache", None)
    if cache is None:
        cache = {}
        setattr(_serp_snippet_and_date, "_cache", cache)
    if path not in cache:
        found: dict[str, tuple[str, str]] = {}
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                payload = {}
            for org in payload.get("organic") or []:
                link = (org.get("link") or "").strip()
                if not link:
                    continue
                try:
                    found_key = url_key(link)
                except ValueError:
                    continue
                found[found_key] = ((org.get("snippet") or "").strip(), (org.get("date") or "").strip())
        cache[path] = found
    return cache[path].get(key, ("", ""))


def _serp_date_freshness(text: str, today: date) -> tuple[float, float]:
    raw = (text or "").strip()
    if not raw:
        return 0.0, 0.0
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            stamp = datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        return freshness_score(stamp.isoformat(), today), 1.0
    return 0.0, 0.0


def load_serp_snapshot(directory: Path, day: str | None = None) -> Snap:
    """Read serp_serper_live.csv. The directory name may carry the day."""
    directory = Path(directory)
    serp = directory / "serp_serper_live.csv"
    if not serp.is_file():
        found = sorted(directory.glob("serp*.csv"))
        if not found:
            raise ValueError(f"{directory} has no serp csv")
        serp = found[0]
    stamp = day or day_from_path(directory)
    rows: list[SnapRow] = []
    with serp.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{serp} has no header")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        query_key = fields.get("query")
        url_key_name = fields.get("link") or fields.get("url")
        position_key = fields.get("position")
        title_key = fields.get("title")
        if not query_key or not url_key_name or not position_key:
            raise ValueError(f"{serp} needs query, link, and position")
        for raw in reader:
            query = " ".join((raw.get(query_key) or "").split())
            url = (raw.get(url_key_name) or "").strip()
            if not query or not url:
                continue
            try:
                position = float(raw.get(position_key) or "")
            except ValueError:
                continue
            if position <= 0:
                continue
            title = (raw.get(title_key) or "").strip() if title_key else ""
            try:
                key = url_key(url)
            except ValueError:
                key = url
            snippet, serp_date = _serp_snippet_and_date(directory, raw, key)
            rows.append(
                SnapRow(
                    query=query,
                    url=url,
                    position=position,
                    title=title,
                    url_key=key,
                    site=site_key(url),
                    snippet=snippet,
                    serp_date=serp_date,
                )
            )
    if not rows:
        raise ValueError(f"{serp} has no rows")
    return Snap(day=stamp, rows=rows, directory=directory, serp_path=serp)


def parse_html(url: str, html: str) -> PageView:
    """Page fields used as features. A parse failure becomes an empty page, not a dropped row."""
    try:
        extracted = extract_html(html or "", url)
        types = page_type_features(url, html or "")
    except (ValueError, TypeError, AttributeError):
        return PageView()
    title = extracted.title or ""
    h1 = extracted.h1 or ""
    headings = " ".join(str(item.get("text") or "") for item in extracted.headings)
    body = (extracted.main_text or "")[:12000]
    if not (title.strip() or h1.strip() or headings.strip() or body.strip()):
        return PageView(types=types)
    return PageView(
        title=title,
        h1=h1,
        headings=headings,
        body=body,
        word_count=int(extracted.word_count or 0),
        published=extracted.published,
        updated=extracted.updated,
        types=types,
        present=True,
    )


def pages_from_html(html_map: dict[str, str]) -> dict[str, PageView]:
    return {key: parse_html(key, html) for key, html in html_map.items()}


def load_pages(directory: Path) -> dict[str, PageView]:
    manifest = Path(directory) / "html_manifest.csv"
    if not manifest.is_file():
        return {}
    html_map, _info = load_html_dir(directory)
    return pages_from_html(html_map)


def load_cc_ranks(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    """Common Crawl domain ranks. Accepts a TSV with domain, pr_val, harmonicc_val."""
    pagerank: dict[str, float] = {}
    harmonic: dict[str, float] = {}
    with Path(path).open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            return {}, {}
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        domain_key = fields.get("domain") or fields.get("#domain")
        pr_key = fields.get("pr_val") or fields.get("pagerank")
        harmonic_key = fields.get("harmonicc_val") or fields.get("harmonic")
        if not domain_key or not pr_key:
            raise ValueError(f"{path} needs domain and pr_val columns")
        for raw in reader:
            domain = (raw.get(domain_key) or "").strip().lower()
            if not domain:
                continue
            try:
                pagerank[domain] = float(raw.get(pr_key) or "")
            except ValueError:
                continue
            if harmonic_key:
                try:
                    harmonic[domain] = float(raw.get(harmonic_key) or "")
                except ValueError:
                    continue
    return pagerank, harmonic


def write_offpage_cache(path: Path, offpage: Offpage, domains: list[str]) -> None:
    """Small lookup for the domains these snapshots actually used. Missing stays blank, not zero."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["domain", "tranco_popularity", "majestic_log", "cc_pagerank", "cc_harmonic"])
        for domain in sorted(set(domains)):
            writer.writerow(
                [
                    domain,
                    "" if domain not in offpage.tranco else f"{offpage.tranco[domain]:.8g}",
                    "" if domain not in offpage.majestic else f"{offpage.majestic[domain]:.8g}",
                    "" if domain not in offpage.cc_pagerank else f"{offpage.cc_pagerank[domain]:.8g}",
                    "" if domain not in offpage.cc_harmonic else f"{offpage.cc_harmonic[domain]:.8g}",
                ]
            )


def load_offpage_cache(path: Path) -> Offpage:
    found = Offpage(sources={"cache": str(path)})
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for raw in reader:
            domain = (raw.get("domain") or "").strip().lower()
            if not domain:
                continue
            for column, table in (
                ("tranco_popularity", found.tranco),
                ("majestic_log", found.majestic),
                ("cc_pagerank", found.cc_pagerank),
                ("cc_harmonic", found.cc_harmonic),
            ):
                text = (raw.get(column) or "").strip()
                if not text:
                    continue
                table[domain] = float(text)
    return found


def load_offpage(tranco: Path | None, majestic: Path | None, cc: Path | None) -> Offpage:
    found = Offpage()
    if tranco is not None:
        found.tranco = load_tranco_popularity(tranco)
        found.sources["tranco"] = str(tranco)
    if majestic is not None:
        found.majestic = load_majestic_million(majestic)
        found.sources["majestic"] = str(majestic)
    if cc is not None:
        pagerank, harmonic = load_cc_ranks(cc)
        found.cc_pagerank = pagerank
        found.cc_harmonic = harmonic
        found.sources["cc"] = str(cc)
    return found


def _coverage(tokens: set[str], wanted: set[str]) -> float:
    if not wanted:
        return 0.0
    return len(tokens & wanted) / len(wanted)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    if union == 0:
        return 0.0
    return len(left & right) / union


def _freshness(page: PageView | None, today: date) -> tuple[float, float]:
    if page is None:
        return 0.0, 0.0
    stamp = page.updated or page.published or ""
    if not stamp:
        return 0.0, 0.0
    try:
        datetime.fromisoformat(stamp[:10])
    except ValueError:
        return 0.0, 0.0
    return freshness_score(stamp, today), 1.0


def _intent(query: str) -> tuple[float, float]:
    text = query.lower()
    alternative = 1.0 if "alternative" in text else 0.0
    listish = 1.0 if "best" in text or text.startswith("top ") else 0.0
    return alternative, listish


def _zscore_rows(rows: list[Row]) -> None:
    for name in Z_FEATURES:
        values = [float(row.features.get(name, 0.0)) for row in rows]
        centered = population_z(values)
        # population_z treats every number as present. Reuse it and overwrite.
        for row, value in zip(rows, centered, strict=True):
            row.features[name] = float(value)


def build_transition(history: list[Snap], outcome: Snap, pages: dict[str, PageView], offpage: Offpage) -> list[Row]:
    """Features for one later snapshot. History days must all be earlier than the outcome day."""
    if any(snap.day >= outcome.day for snap in history):
        raise ValueError(f"History days must be before {outcome.day}")
    if not history:
        raise ValueError("Need at least one earlier snapshot")
    ordered = sorted(history, key=lambda snap: snap.day)
    site_rels: dict[str, list[float]] = defaultdict(list)
    niche_df: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    site_query_rels: dict[tuple[str, str], list[float]] = defaultdict(list)
    url_query_rels: dict[tuple[str, str], list[float]] = defaultdict(list)
    url_other: dict[str, list[tuple[str, float]]] = defaultdict(list)
    site_other: dict[str, list[tuple[str, float]]] = defaultdict(list)
    flat: list[float] = []
    for snap in ordered:
        seen: set[tuple[str, str]] = set()
        for row in snap.rows:
            key = (row.query, row.url_key)
            if key in seen:
                continue
            seen.add(key)
            rel = relevance(row.position)
            flat.append(rel)
            site_rels[row.site].append(rel)
            site_query_rels[(row.site, row.query)].append(rel)
            url_query_rels[(row.url_key, row.query)].append(rel)
            url_other[row.url_key].append((row.query, rel))
            site_other[row.site].append((row.query, rel))
            wanted_history = content_tokens(row.query)
            for token in tokenize_for_search(row.title.lower()):
                if len(token) < 3 or token in wanted_history:
                    continue
                niche_df[row.query][token] += 1
    global_mean = sum(flat) / len(flat) if flat else 0.0
    previous: dict[tuple[str, str], SnapRow] = {}
    previous_site: dict[tuple[str, str], list[SnapRow]] = defaultdict(list)
    seen_prev: set[tuple[str, str]] = set()
    for row in ordered[-1].rows:
        key = (row.query, row.url_key)
        if key in seen_prev:
            continue
        seen_prev.add(key)
        previous[key] = row
        previous_site[(row.query, row.site)].append(row)
    outcome_rows = _dedupe(outcome.rows)
    candidates: dict[str, set[str]] = defaultdict(set)
    for row in outcome_rows:
        candidates[row.query].add(row.url_key)
    query_terms = {row.query: content_tokens(row.query) for row in outcome_rows}
    today = date.fromisoformat(outcome.day)
    grouped: dict[str, list[Row]] = defaultdict(list)
    token_sets: dict[str, list[set[str]]] = defaultdict(list)
    for row in outcome_rows:
        page = pages.get(row.url_key)
        present = bool(page and page.present)
        wanted = query_terms[row.query]
        survived = previous.get((row.query, row.url_key))
        host_rels = site_rels.get(row.site, [])
        domain_mean = (sum(host_rels) / len(host_rels)) if host_rels else global_mean
        domain_query_values = site_query_rels.get((row.site, row.query), [])
        domain_query = sum(domain_query_values) / len(domain_query_values) if domain_query_values else 0.0
        own = url_query_rels.get((row.url_key, row.query), [])
        last = relevance(survived.position) if survived is not None else 0.0
        trend = 0.0
        if survived is not None and len(own) >= 2:
            earlier = own[:-1]
            trend = last - (sum(earlier) / len(earlier))
        replaced = 0.0
        for old in previous_site.get((row.query, row.site), []):
            if old.url_key not in candidates[row.query]:
                replaced = max(replaced, relevance(old.position))
        transfer = 0.0
        for other_query, rel in url_other.get(row.url_key, []):
            if other_query == row.query:
                continue
            transfer = max(transfer, rel * _jaccard(wanted, query_terms.get(other_query) or content_tokens(other_query)))
        domain_transfer = 0.0
        for other_query, rel in site_other.get(row.site, []):
            if other_query == row.query:
                continue
            domain_transfer = max(domain_transfer, rel * _jaccard(wanted, query_terms.get(other_query) or content_tokens(other_query)))
        serp_tokens = set(tokenize_for_search(row.title.lower())) if row.title else set()
        snippet_tokens = set(tokenize_for_search(row.snippet.lower())) if row.snippet else set()
        serp_fresh, serp_fresh_known = _serp_date_freshness(row.serp_date, today)
        niche_counts = niche_df.get(row.query) or {}
        niche_terms = {token for token, count in niche_counts.items() if count >= 2}
        title_tokens: set[str] = set()
        h1_tokens: set[str] = set()
        body_tokens: set[str] = set()
        if present and page is not None:
            title_tokens = set(tokenize_for_search(page.title.lower()))
            h1_tokens = set(tokenize_for_search(page.h1.lower()))
            body_tokens = set(tokenize_for_search(f"{page.headings} {page.body}".lower()))
        fresh, fresh_known = _freshness(page if present else None, today)
        types = page.types if page is not None else page_type_features(row.url, "")
        if not types:
            types = page_type_features(row.url, "")
        listicle_title = " ".join(part for part in ((page.title if present and page else ""), row.title) if part)
        pt_listicle = 1.0 if _LISTICLE.search(listicle_title or "") else 0.0
        pt_product = 1.0 if float(types.get("pt_schema_product", 0.0)) else 0.0
        alternative, listish = _intent(row.query)
        path = urlsplit(row.url).path or "/"
        depth = float(len([part for part in path.split("/") if part]))
        doc_tokens = serp_tokens | title_tokens | h1_tokens | body_tokens
        features = {
            "has_history": 1.0 if survived is not None else 0.0,
            "last_relevance": last,
            "url_mean_relevance": (sum(own) / len(own)) if own else 0.0,
            "appearances": float(len(own)),
            "trend_relevance": trend,
            "history_domain_gap": (domain_mean - last) if survived is not None else 0.0,
            "domain_mean_relevance": domain_mean,
            "domain_query_relevance": domain_query,
            "domain_seen": 1.0 if host_rels else 0.0,
            "domain_count_log": math.log1p(len(host_rels)),
            "replaced_domain_relevance": replaced,
            "url_overlap": url_query_overlap(row.query, row.url),
            "url_transfer": transfer,
            "domain_transfer": domain_transfer,
            "url_path_depth": depth,
            "serp_title_coverage": _coverage(serp_tokens, wanted),
            "serp_title_phrase": 1.0 if wanted and wanted <= serp_tokens else 0.0,
            "serp_snippet_coverage": _coverage(snippet_tokens, wanted),
            "serp_date_known": serp_fresh_known,
            "serp_date_freshness": serp_fresh,
            "niche_title_coverage": (len((doc_tokens | snippet_tokens) & niche_terms) / len(niche_terms)) if niche_terms else 0.0,
            "content_present": 1.0 if present else 0.0,
            "bm25f": 0.0,
            "title_coverage": _coverage(title_tokens, wanted) if present else 0.0,
            "h1_coverage": _coverage(h1_tokens, wanted) if present else 0.0,
            "body_coverage": _coverage(body_tokens, wanted) if present else 0.0,
            "serp_vocab_coverage": 0.0,
            "word_count_log": math.log1p(page.word_count) if present and page is not None else 0.0,
            "freshness": fresh,
            "freshness_known": fresh_known,
            "pt_review": float(types.get("pt_review", 0.0)),
            "pt_forum": float(types.get("pt_forum", 0.0)),
            "pt_article": float(types.get("pt_article", 0.0)),
            "pt_listicle": pt_listicle,
            "pt_home": float(types.get("pt_home", 0.0)),
            "pt_video": float(types.get("pt_video", 0.0)),
            "pt_docs": float(types.get("pt_docs", 0.0)),
            "pt_product": pt_product,
            "ix_review_alternative": float(types.get("pt_review", 0.0)) * alternative,
            "ix_listicle_list": pt_listicle * listish,
            "ix_listicle_alternative": pt_listicle * alternative,
            "ix_forum_alternative": float(types.get("pt_forum", 0.0)) * alternative,
            "ix_product_list": pt_product * listish,
            "tranco_listed": 0.0,
            "tranco_popularity": 0.0,
            "majestic_listed": 0.0,
            "majestic_log": 0.0,
            "cc_listed": 0.0,
            "cc_pagerank": 0.0,
            "cc_harmonic": 0.0,
        }
        tranco = offpage.lookup(offpage.tranco, row.url)
        if tranco is not None:
            features["tranco_listed"] = 1.0
            features["tranco_popularity"] = float(tranco)
        majestic = offpage.lookup(offpage.majestic, row.url)
        if majestic is not None:
            features["majestic_listed"] = 1.0
            features["majestic_log"] = float(majestic)
        pagerank = offpage.lookup(offpage.cc_pagerank, row.url)
        harmonic = offpage.lookup(offpage.cc_harmonic, row.url)
        if pagerank is not None or harmonic is not None:
            features["cc_listed"] = 1.0
            features["cc_pagerank"] = float(pagerank or 0.0)
            features["cc_harmonic"] = float(harmonic or 0.0)
        built = Row(
            day=outcome.day,
            query=row.query,
            url=row.url,
            url_key=row.url_key,
            position=float(row.position),
            bucket="surviving" if survived is not None else "new",
            group=query_group(row.query),
            features=features,
        )
        # Hold the document fields for BM25 on the row object via a private key.
        fields_doc = {
            "title": (page.title if present and page else "") or row.title,
            "h1": page.h1 if present and page else "",
            "headings": page.headings if present and page else "",
            "url": url_text(row.url),
            "anchor": "",
            "body": page.body if present and page else "",
        }
        built.features["_doc_title"] = 0.0  # placeholder so the dict stays numeric after pop
        grouped[row.query].append(built)
        token_sets[row.query].append(doc_tokens)
        built.features.pop("_doc_title", None)
        setattr(built, "_doc", fields_doc)
    rows: list[Row] = []
    for query, group in grouped.items():
        docs = [getattr(row, "_doc") for row in group]
        for value, row in zip(bm25f_scores(query, docs), group, strict=True):
            row.features["bm25f"] = float(value)
            delattr(row, "_doc")
        # Shared vocabulary of this candidate set, excluding the query's own terms.
        df: dict[str, int] = defaultdict(int)
        for tokens in token_sets[query]:
            for token in tokens:
                if token in query_terms[query] or len(token) < 3:
                    continue
                df[token] += 1
        threshold = max(2, math.ceil(0.3 * len(group)))
        consensus = {token for token, count in df.items() if count >= threshold}
        if consensus:
            for row, tokens in zip(group, token_sets[query], strict=True):
                row.features["serp_vocab_coverage"] = len(tokens & consensus) / len(consensus)
        _zscore_rows(group)
        rows.extend(group)
    return rows


def _mode_keeps(left: str, right: str, mode: str) -> bool:
    if mode == "all":
        return True
    if mode == "surviving_surviving":
        return left == "surviving" and right == "surviving"
    if mode == "new_vs_new":
        return left == "new" and right == "new"
    if mode == "new_vs_surviving":
        return left != right
    raise ValueError(mode)


def evaluate_scores(rows: list[Row], scores: list[float]) -> dict:
    """Pairwise accuracy. Ties are not correct and stay in the denominator. No row is dropped."""
    if len(rows) != len(scores):
        raise ValueError("scores and rows differ in length")
    grouped: dict[str, list[tuple[Row, float]]] = defaultdict(list)
    for row, score in zip(rows, scores, strict=True):
        grouped[row.query].append((row, float(score)))
    blocks: dict[str, dict] = {}
    per_query: dict[str, list[tuple[int, int]]] = {}
    for mode in PAIR_MODES:
        correct = wrong = ties = 0
        query_counts: list[tuple[int, int]] = []
        used = 0
        for pairs in grouped.values():
            got_correct = got_wrong = got_ties = 0
            saw = False
            for left in range(len(pairs)):
                for right in range(len(pairs)):
                    if pairs[left][0].position >= pairs[right][0].position:
                        continue
                    if not _mode_keeps(pairs[left][0].bucket, pairs[right][0].bucket, mode):
                        continue
                    saw = True
                    if pairs[left][1] > pairs[right][1]:
                        got_correct += 1
                    elif pairs[left][1] < pairs[right][1]:
                        got_wrong += 1
                    else:
                        got_ties += 1
            if not saw:
                continue
            used += 1
            correct += got_correct
            wrong += got_wrong
            ties += got_ties
            query_counts.append((got_correct, got_correct + got_wrong + got_ties))
        total = correct + wrong + ties
        blocks[mode] = {
            "queries": used,
            "pairs": total,
            "correct": correct,
            "wrong": wrong,
            "ties": ties,
            "pairwise": None if total == 0 else correct / total,
        }
        per_query[mode] = query_counts
    ndcgs: list[float | None] = []
    rhos: list[float | None] = []
    for pairs in grouped.values():
        if len(pairs) < 2:
            continue
        order = sorted(range(len(pairs)), key=lambda index: (-pairs[index][1], pairs[index][0].url))
        gains = {str(index): relevance(pairs[index][0].position) for index in range(len(pairs))}
        ndcgs.append(ndcg_at_k([str(index) for index in order], gains, k=10))
        rhos.append(spearman([score for _row, score in pairs], [row.position for row, _score in pairs]))
    ndcg = mean_metric(ndcgs)
    rho = mean_metric(rhos)
    return {
        "blocks": blocks,
        "per_query": per_query,
        "ndcg_at_10": None if ndcg is None else ndcg,
        "spearman": None if rho is None else rho,
        "rows": len(rows),
        "queries": len(grouped),
        "scored_rows": len(rows),
    }


def bootstrap_ci(query_counts: list[tuple[int, int]], *, draws: int = 1000, seed: int = 20261116) -> tuple[float | None, float | None]:
    """Percentile interval of pairwise accuracy, resampling queries with replacement."""
    if not query_counts:
        return None, None
    rng = random.Random(seed)
    size = len(query_counts)
    stats: list[float] = []
    for _ in range(draws):
        correct = total = 0
        for _draw in range(size):
            got, pairs = query_counts[rng.randrange(size)]
            correct += got
            total += pairs
        stats.append(correct / total if total else 0.0)
    stats.sort()
    low = stats[int((draws - 1) * 0.025)]
    high = stats[int((draws - 1) * 0.975)]
    return low, high


def _solve(augmented: list[list[float]]) -> list[float] | None:
    matrix = [row[:] for row in augmented]
    width = len(matrix)
    for col in range(width):
        pivot = max(range(col, width), key=lambda row: abs(matrix[row][col]))
        if abs(matrix[pivot][col]) < 1e-12:
            return None
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        scale = matrix[col][col]
        for col_index in range(col, width + 1):
            matrix[col][col_index] /= scale
        for row in range(width):
            if row == col:
                continue
            factor = matrix[row][col]
            if factor == 0.0:
                continue
            for col_index in range(col, width + 1):
                matrix[row][col_index] -= factor * matrix[col][col_index]
    return [matrix[row][width] for row in range(width)]


def _usable_features(rows: list[Row], names: tuple[str, ...] | list[str]) -> list[str]:
    used = []
    for name in names:
        values = [float(row.features.get(name, 0.0)) for row in rows]
        if not values:
            continue
        if max(values) - min(values) > 1e-12:
            used.append(name)
    return used


def _mean_std(rows: list[Row], names: list[str]) -> tuple[list[float], list[float]]:
    means: list[float] = []
    stds: list[float] = []
    for name in names:
        values = [float(row.features.get(name, 0.0)) for row in rows]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        std = math.sqrt(variance)
        means.append(mean)
        stds.append(std if std > 1e-12 else 1.0)
    return means, stds


def _vector(row: Row, names: list[str], means: list[float], stds: list[float]) -> list[float]:
    return [(float(row.features.get(name, 0.0)) - mean) / std for name, mean, std in zip(names, means, stds, strict=True)]


@dataclass
class LinearModel:
    kind: str
    features: list[str]
    weights: list[float]
    mean: list[float]
    std: list[float]
    intercept: float = 0.0
    lam: float = 0.0
    family: str = ""

    def predict(self, rows: list[Row]) -> list[float]:
        scores = []
        for row in rows:
            total = self.intercept
            for name, weight, mean, std in zip(self.features, self.weights, self.mean, self.std, strict=True):
                total += weight * ((float(row.features.get(name, 0.0)) - mean) / std)
            scores.append(total)
        return scores

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "family": self.family,
            "features": self.features,
            "weights": self.weights,
            "mean": self.mean,
            "std": self.std,
            "intercept": self.intercept,
            "lam": self.lam,
            "label": "PREDICTED",
        }


@dataclass
class PersistenceModel:
    family: str = "persistence_ranked_last"

    def predict(self, rows: list[Row]) -> list[float]:
        scores = []
        for row in rows:
            if row.features.get("has_history"):
                scores.append(float(row.features.get("last_relevance", 0.0)) - 11.0)
            else:
                scores.append(-1000.0)
        return scores

    def to_dict(self) -> dict:
        return {"kind": "persistence", "family": self.family, "label": "PREDICTED"}


@dataclass
class PrecommittedModel:
    weights: dict[str, float]
    family: str = "precommitted_blend"

    def predict(self, rows: list[Row]) -> list[float]:
        scores = []
        for row in rows:
            total = 0.0
            for name, weight in self.weights.items():
                total += weight * float(row.features.get(name, 0.0))
            scores.append(total)
        return scores

    def to_dict(self) -> dict:
        return {"kind": "precommitted", "family": self.family, "weights": self.weights, "label": "PREDICTED"}


@dataclass
class FrozenStyleModel:
    """New URLs get a position line. Survivors get the negative of the last position."""

    intercept: float
    weights: dict[str, float]
    family: str = "frozen_style"

    def predict(self, rows: list[Row]) -> list[float]:
        scores = []
        for row in rows:
            if row.features.get("has_history"):
                scores.append(float(row.features.get("last_relevance", 0.0)) - 11.0)
            else:
                position_hat = self.intercept
                for name, weight in self.weights.items():
                    position_hat += weight * float(row.features.get(name, 0.0))
                scores.append(-position_hat)
        return scores

    def to_dict(self) -> dict:
        return {
            "kind": "frozen_style",
            "family": self.family,
            "intercept": self.intercept,
            "weights": self.weights,
            "label": "PREDICTED",
        }


@dataclass
class BlendModel:
    base: LinearModel
    alpha: float
    family: str = "blend_persistence"

    def predict(self, rows: list[Row]) -> list[float]:
        return [score + self.alpha * float(row.features.get("last_relevance", 0.0)) for row, score in zip(rows, self.base.predict(rows), strict=True)]

    def to_dict(self) -> dict:
        payload = self.base.to_dict()
        return {"kind": "blend", "family": self.family, "alpha": self.alpha, "base": payload, "label": "PREDICTED"}


@dataclass
class GroupModel:
    global_model: LinearModel
    groups: dict[str, LinearModel]
    family: str = "ridge_query_groups"

    def predict(self, rows: list[Row]) -> list[float]:
        scores = [0.0] * len(rows)
        buckets: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            buckets[row.group if row.group in self.groups else ""].append(index)
        for key, indexes in buckets.items():
            model = self.global_model if key == "" else self.groups[key]
            part = model.predict([rows[index] for index in indexes])
            for index, score in zip(indexes, part, strict=True):
                scores[index] = score
        return scores

    def to_dict(self) -> dict:
        return {
            "kind": "groups",
            "family": self.family,
            "global": self.global_model.to_dict(),
            "groups": {name: model.to_dict() for name, model in self.groups.items()},
            "label": "PREDICTED",
        }


@dataclass
class TwoHeadModel:
    survivor: LinearModel
    new: LinearModel
    bias: float
    family: str = "two_head_calibrated"

    def predict(self, rows: list[Row]) -> list[float]:
        survivor_scores = self.survivor.predict(rows)
        new_scores = self.new.predict(rows)
        scores = []
        for row, survivor_score, new_score in zip(rows, survivor_scores, new_scores, strict=True):
            if row.features.get("has_history"):
                scores.append(survivor_score)
            else:
                scores.append(new_score + self.bias)
        return scores

    def to_dict(self) -> dict:
        return {
            "kind": "two_head",
            "family": self.family,
            "bias": self.bias,
            "survivor": self.survivor.to_dict(),
            "new": self.new.to_dict(),
            "label": "PREDICTED",
        }


@dataclass
class LockedNewModel:
    """Survivors keep yesterday's order. Only new URLs get a learned score."""

    new_model: LinearModel
    scale: float
    bias: float
    family: str = "persistence_locked_full"

    def predict(self, rows: list[Row]) -> list[float]:
        raw = self.new_model.predict(rows)
        scores = []
        for row, value in zip(rows, raw, strict=True):
            if row.features.get("has_history"):
                scores.append(float(row.features.get("last_relevance", 0.0)))
            else:
                scores.append(self.scale * value + self.bias)
        return scores

    def to_dict(self) -> dict:
        return {
            "kind": "locked_new",
            "family": self.family,
            "scale": self.scale,
            "bias": self.bias,
            "new_model": self.new_model.to_dict(),
            "label": "PREDICTED",
        }


@dataclass
class BoostingModel:
    features: list[str]
    mean: list[float]
    std: list[float]
    model_text: str
    family: str = "lambdamart"
    monotone: bool = False

    def predict(self, rows: list[Row]) -> list[float]:
        import lightgbm as lgb

        import numpy as np

        matrix = np.asarray([_vector(row, self.features, self.mean, self.std) for row in rows], dtype=np.float64)
        booster = lgb.Booster(model_str=self.model_text)
        predicted = booster.predict(matrix)
        return [float(value) for value in predicted]

    def to_dict(self) -> dict:
        return {
            "kind": "lambdamart",
            "family": self.family,
            "features": self.features,
            "mean": self.mean,
            "std": self.std,
            "model_text": self.model_text,
            "monotone": self.monotone,
            "label": "PREDICTED",
        }


@dataclass
class EnsembleModel:
    left: object
    right: object
    left_mean: float
    left_std: float
    right_mean: float
    right_std: float
    family: str = "ensemble_ridge_lambdamart"

    def predict(self, rows: list[Row]) -> list[float]:
        left_scores = self.left.predict(rows)
        right_scores = self.right.predict(rows)
        scores = []
        for left, right in zip(left_scores, right_scores, strict=True):
            scores.append(((left - self.left_mean) / self.left_std) + ((right - self.right_mean) / self.right_std))
        return scores

    def to_dict(self) -> dict:
        return {
            "kind": "ensemble",
            "family": self.family,
            "left_mean": self.left_mean,
            "left_std": self.left_std,
            "right_mean": self.right_mean,
            "right_std": self.right_std,
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
            "label": "PREDICTED",
        }


def _fit_ridge_pairs(rows: list[Row], names: list[str], lam: float, pair_weight) -> LinearModel:
    if not names:
        raise ValueError("No varying features to fit")
    means, stds = _mean_std(rows, names)
    width = len(names)
    xtx = [[0.0] * width for _ in range(width)]
    xty = [0.0] * width
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[f"{row.day}\0{row.query}"].append(row)
    for group in grouped.values():
        vectors = [_vector(row, names, means, stds) for row in group]
        for left in range(len(group)):
            for right in range(left + 1, len(group)):
                if group[left].position == group[right].position:
                    continue
                if group[left].position < group[right].position:
                    better, worse = vectors[left], vectors[right]
                    weight = float(pair_weight(group[left], group[right]))
                else:
                    better, worse = vectors[right], vectors[left]
                    weight = float(pair_weight(group[right], group[left]))
                diff = [a - b for a, b in zip(better, worse, strict=True)]
                for a in range(width):
                    xty[a] += weight * diff[a]
                    for b in range(width):
                        xtx[a][b] += weight * diff[a] * diff[b]
    for index in range(width):
        xtx[index][index] += lam
    solved = _solve([xtx[row] + [xty[row]] for row in range(width)])
    if solved is None:
        for index in range(width):
            xtx[index][index] += lam * 100.0
        solved = _solve([xtx[row] + [xty[row]] for row in range(width)])
    if solved is None:
        raise ValueError("Pairwise design is singular")
    return LinearModel(kind="ridge", features=list(names), weights=solved, mean=means, std=stds, lam=lam)


def _fit_pointwise(rows: list[Row], names: list[str], lam: float) -> LinearModel:
    means, stds = _mean_std(rows, names)
    width = len(names) + 1
    xtx = [[0.0] * width for _ in range(width)]
    xty = [0.0] * width
    for row in rows:
        vector = [1.0] + _vector(row, names, means, stds)
        target = relevance(row.position)
        for left in range(width):
            xty[left] += vector[left] * target
            for right in range(width):
                xtx[left][right] += vector[left] * vector[right]
    for index in range(1, width):
        xtx[index][index] += lam
    solved = _solve([xtx[row] + [xty[row]] for row in range(width)])
    if solved is None:
        raise ValueError("Pointwise design is singular")
    return LinearModel(
        kind="pointwise",
        features=list(names),
        weights=solved[1:],
        mean=means,
        std=stds,
        intercept=solved[0],
        lam=lam,
    )


def _unit_weight(_left: Row, _right: Row) -> float:
    return 1.0


def _new_weight(left: Row, right: Row) -> float:
    new_left = left.bucket == "new"
    new_right = right.bucket == "new"
    if new_left and new_right:
        return 5.0
    if new_left or new_right:
        return 3.0
    return 1.0


def _pair_correct(left_pos: float, right_pos: float, left_score: float, right_score: float) -> bool:
    if left_pos == right_pos:
        return False
    if left_pos < right_pos:
        return left_score > right_score
    return right_score > left_score


def _best_bias(rows: list[Row], survivor_scores: list[float], new_scores: list[float]) -> float:
    """One bias on new-URL scores, chosen to order training new-vs-surviving pairs."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[f"{row.day}\0{row.query}"].append(index)
    thresholds: list[tuple[float, int]] = []
    # Each pair is correct on one side of a threshold. Sweep those cut points.
    pair_index = 0
    events: list[tuple[float, int, int]] = []
    for indexes in grouped.values():
        for left in indexes:
            for right in indexes:
                if rows[left].position >= rows[right].position:
                    continue
                if rows[left].bucket == rows[right].bucket:
                    continue
                # left is the better URL.
                if rows[left].bucket == "new":
                    # new + bias > survivor
                    cut = survivor_scores[right] - new_scores[left]
                    events.append((cut, pair_index, 1))  # becomes correct as bias increases past cut
                else:
                    cut = survivor_scores[left] - new_scores[right]
                    events.append((cut, pair_index, -1))  # correct while bias is below cut
                pair_index += 1
    if not events:
        return 0.0
    events.sort()
    # Start below every cut: pairs of type -1 are correct, type +1 are not.
    correct = sum(1 for _cut, _pair, direction in events if direction < 0)
    best_correct = correct
    best_bias = events[0][0] - 1.0
    total = len(events)
    cursor = 0
    while cursor < len(events):
        cut = events[cursor][0]
        while cursor < len(events) and events[cursor][0] == cut:
            _cut, _pair, direction = events[cursor]
            if direction > 0:
                correct += 1
            else:
                correct -= 1
            cursor += 1
        # Just to the right of this cut, type +1 has flipped to correct and type -1 has flipped to wrong.
        # Equality at the cut is a tie, so the open interval to the right is the fair point.
        if correct > best_correct or (correct == best_correct and abs(cut + 1e-6) < abs(best_bias)):
            best_correct = correct
            best_bias = cut + 1e-6
        if correct == total:
            break
    return best_bias


def _ns_correct(rows: list[Row], survivor_scores: list[float], new_scores: list[float], bias: float) -> int:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[f"{row.day}\0{row.query}"].append(index)
    correct = 0
    for indexes in grouped.values():
        for left in indexes:
            for right in indexes:
                if rows[left].position >= rows[right].position:
                    continue
                if rows[left].bucket == rows[right].bucket:
                    continue
                if rows[left].bucket == "new":
                    left_score = new_scores[left] + bias
                    right_score = survivor_scores[right]
                else:
                    left_score = survivor_scores[left]
                    right_score = new_scores[right] + bias
                if left_score > right_score:
                    correct += 1
    return correct


def _best_alpha(rows: list[Row], base_scores: list[float]) -> float:
    best_alpha = 0.0
    best_correct = -1
    for alpha in BLEND_ALPHAS:
        correct = 0
        grouped: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            grouped[f"{row.day}\0{row.query}"].append(index)
        for indexes in grouped.values():
            for left in range(len(indexes)):
                i = indexes[left]
                for right in range(left + 1, len(indexes)):
                    j = indexes[right]
                    if rows[i].position == rows[j].position:
                        continue
                    left_score = base_scores[i] + alpha * float(rows[i].features.get("last_relevance", 0.0))
                    right_score = base_scores[j] + alpha * float(rows[j].features.get("last_relevance", 0.0))
                    if _pair_correct(rows[i].position, rows[j].position, left_score, right_score):
                        correct += 1
        if correct > best_correct:
            best_correct = correct
            best_alpha = alpha
    return best_alpha


def _fit_lgbm(rows: list[Row], names: list[str], *, monotone: bool, family: str) -> BoostingModel:
    import lightgbm as lgb

    means, stds = _mean_std(rows, names)
    grouped: dict[str, list[Row]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        key = f"{row.day}\0{row.query}"
        if key not in grouped:
            order.append(key)
        grouped[key].append(row)
    matrix: list[list[float]] = []
    labels: list[int] = []
    groups: list[int] = []
    for key in order:
        group = grouped[key]
        if len(group) < 2:
            continue
        groups.append(len(group))
        for row in group:
            matrix.append(_vector(row, names, means, stds))
            labels.append(int(round(relevance(row.position))))
    if not groups:
        raise ValueError("LambdaMART needs a query with two URLs")
    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "num_leaves": 7,
        "learning_rate": 0.05,
        "min_data_in_leaf": min(20, max(2, len(matrix) // 10)),
        "lambda_l2": 2.0,
        "feature_fraction": 0.9,
        "verbosity": -1,
        "label_gain": ",".join(str(value) for value in range(11)),
        "seed": 0,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 1,
    }
    if monotone:
        params["monotone_constraints"] = ",".join("1" if name in MONOTONE_UP else "0" for name in names)
    import numpy as np

    dataset = lgb.Dataset(np.asarray(matrix, dtype=np.float64), label=np.asarray(labels, dtype=np.float64), group=groups)
    booster = lgb.train(params, dataset, num_boost_round=80)
    return BoostingModel(
        features=list(names),
        mean=means,
        std=stds,
        model_text=booster.model_to_string(),
        family=family,
        monotone=monotone,
    )


def _score_location(scores: list[float]) -> tuple[float, float]:
    if not scores:
        return 0.0, 1.0
    mean = sum(scores) / len(scores)
    variance = sum((score - mean) ** 2 for score in scores) / len(scores)
    std = math.sqrt(variance)
    return mean, std if std > 1e-12 else 1.0


def fit_experiment(name: str, rows: list[Row]) -> object:
    """Fit one pre-specified family. Rows are training transitions only."""
    if not rows:
        raise ValueError(f"{name} has no training rows")
    if name == "persistence_ranked_last":
        return PersistenceModel()
    if name == "precommitted_blend":
        return PrecommittedModel(weights=dict(PRECOMMITTED_WEIGHTS))
    content = list(HISTORY_FEATURES + CONTENT_FEATURES)
    tranco = content + list(OFFPAGE_TM_FEATURES)
    full = list(ALL_FEATURES)
    specs: dict[str, tuple[list[str], float, object]] = {
        "ridge_history": (list(HISTORY_FEATURES), 10.0, _unit_weight),
        "ridge_content": (content, 10.0, _unit_weight),
        "ridge_tranco_majestic": (tranco, 10.0, _unit_weight),
        "ridge_offpage": (full, 10.0, _unit_weight),
        "ridge_offpage_l1": (full, 1.0, _unit_weight),
        "ridge_offpage_l100": (full, 100.0, _unit_weight),
        "ridge_new_weighted": (full, 10.0, _new_weight),
    }
    if name in specs:
        names, lam, weight = specs[name]
        used = _usable_features(rows, names)
        model = _fit_ridge_pairs(rows, used, lam, weight)
        model.family = name
        return model
    if name == "ridge_pointwise":
        used = _usable_features(rows, full)
        model = _fit_pointwise(rows, used, 10.0)
        model.family = name
        return model
    if name in {"frozen_style_bm25_fixed", "frozen_style_bm25_free"}:
        new_rows = [row for row in rows if row.bucket == "new"]
        if len(new_rows) < 3:
            raise ValueError(f"{name} needs at least 3 new URLs")
        names = ["domain_mean_relevance", "url_overlap"]
        if name == "frozen_style_bm25_free":
            names.append("bm25f")
        used = _usable_features(new_rows, names)
        width = len(used) + 1
        xtx = [[0.0] * width for _ in range(width)]
        xty = [0.0] * width
        for row in new_rows:
            vector = [1.0] + [float(row.features.get(feature, 0.0)) for feature in used]
            target = float(row.position)
            for left in range(width):
                xty[left] += vector[left] * target
                for right in range(width):
                    xtx[left][right] += vector[left] * vector[right]
        solved = _solve([xtx[row] + [xty[row]] for row in range(width)])
        if solved is None:
            raise ValueError(f"{name} design is singular")
        weights = {feature: solved[index + 1] for index, feature in enumerate(used)}
        if name == "frozen_style_bm25_fixed":
            weights["bm25f"] = -1.0
        return FrozenStyleModel(intercept=solved[0], weights=weights, family=name)
    if name == "ridge_query_groups":
        used = _usable_features(rows, full)
        global_model = _fit_ridge_pairs(rows, used, 10.0, _unit_weight)
        global_model.family = name
        groups: dict[str, LinearModel] = {}
        for group_name in ("alternative", "list", "other"):
            subset = [row for row in rows if row.group == group_name]
            pair_count = 0
            by_query: dict[str, int] = defaultdict(int)
            for row in subset:
                by_query[f"{row.day}\0{row.query}"] += 1
            for count in by_query.values():
                pair_count += count * (count - 1) // 2
            if pair_count < 120 or len(subset) < 20:
                continue
            group_used = _usable_features(subset, full)
            if len(group_used) < 2:
                continue
            model = _fit_ridge_pairs(subset, group_used, 10.0, _unit_weight)
            model.family = name
            groups[group_name] = model
        return GroupModel(global_model=global_model, groups=groups, family=name)
    if name == "two_head_calibrated":
        survivors = [row for row in rows if row.bucket == "surviving"]
        newcomers = [row for row in rows if row.bucket == "new"]
        survivor_used = _usable_features(survivors or rows, full)
        new_used = _usable_features(newcomers or rows, full)
        survivor = _fit_ridge_pairs(survivors or rows, survivor_used, 10.0, _unit_weight)
        if len(newcomers) >= 8:
            new_model = _fit_ridge_pairs(newcomers, new_used, 10.0, _unit_weight)
        else:
            new_model = _fit_ridge_pairs(rows, new_used, 10.0, _unit_weight)
        survivor.family = name
        new_model.family = name
        bias = _best_bias(rows, survivor.predict(rows), new_model.predict(rows))
        return TwoHeadModel(survivor=survivor, new=new_model, bias=bias, family=name)
    if name in {"lambdamart", "lambdamart_monotone"}:
        used = _usable_features(rows, full)
        return _fit_lgbm(rows, used, monotone=name == "lambdamart_monotone", family=name)
    if name == "blend_persistence":
        used = _usable_features(rows, full)
        base = _fit_ridge_pairs(rows, used, 10.0, _unit_weight)
        base.family = name
        alpha = _best_alpha(rows, base.predict(rows))
        return BlendModel(base=base, alpha=alpha, family=name)
    if name in {"persistence_locked_full", "persistence_locked_serp"}:
        names = list(ALL_FEATURES if name == "persistence_locked_full" else ALL_FEATURES + SERP_EXTRA_FEATURES)
        newcomers = [row for row in rows if row.bucket == "new"]
        if len(newcomers) < 8:
            raise ValueError(f"{name} needs at least 8 new URLs")
        used = _usable_features(newcomers, names)
        new_model = _fit_ridge_pairs(newcomers, used, 10.0, _unit_weight)
        new_model.family = name
        raw = new_model.predict(rows)
        survivor_scores = [
            float(row.features.get("last_relevance", 0.0)) if row.features.get("has_history") else 0.0 for row in rows
        ]
        best_scale, best_bias, best_correct = 1.0, 0.0, -1
        for scale in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
            scaled = [scale * value for value in raw]
            bias = _best_bias(rows, survivor_scores, scaled)
            correct = _ns_correct(rows, survivor_scores, scaled, bias)
            if correct > best_correct:
                best_scale, best_bias, best_correct = scale, bias, correct
        return LockedNewModel(new_model=new_model, scale=best_scale, bias=best_bias, family=name)
    if name == "ridge_serp_text":
        used = _usable_features(rows, list(ALL_FEATURES + SERP_EXTRA_FEATURES))
        model = _fit_ridge_pairs(rows, used, 10.0, _unit_weight)
        model.family = name
        return model
    if name == "ensemble_ridge_lambdamart":
        used = _usable_features(rows, full)
        left = _fit_ridge_pairs(rows, used, 10.0, _unit_weight)
        right = _fit_lgbm(rows, used, monotone=False, family=name)
        left.family = name
        left_mean, left_std = _score_location(left.predict(rows))
        right_mean, right_std = _score_location(right.predict(rows))
        return EnsembleModel(
            left=left,
            right=right,
            left_mean=left_mean,
            left_std=left_std,
            right_mean=right_mean,
            right_std=right_std,
            family=name,
        )
    raise ValueError(f"Unknown experiment {name}")


def model_from_dict(payload: dict) -> object:
    kind = payload.get("kind")
    if kind == "persistence":
        return PersistenceModel(family=payload.get("family") or "persistence_ranked_last")
    if kind == "precommitted":
        return PrecommittedModel(weights={key: float(value) for key, value in payload["weights"].items()}, family=payload.get("family") or "precommitted_blend")
    if kind == "frozen_style":
        return FrozenStyleModel(
            intercept=float(payload["intercept"]),
            weights={key: float(value) for key, value in payload["weights"].items()},
            family=payload.get("family") or "frozen_style",
        )
    if kind == "ridge" or kind == "pointwise":
        return LinearModel(
            kind=kind,
            features=list(payload["features"]),
            weights=[float(value) for value in payload["weights"]],
            mean=[float(value) for value in payload["mean"]],
            std=[float(value) for value in payload["std"]],
            intercept=float(payload.get("intercept") or 0.0),
            lam=float(payload.get("lam") or 0.0),
            family=payload.get("family") or kind,
        )
    if kind == "blend":
        return BlendModel(base=model_from_dict(payload["base"]), alpha=float(payload["alpha"]), family=payload.get("family") or "blend_persistence")
    if kind == "groups":
        return GroupModel(
            global_model=model_from_dict(payload["global"]),
            groups={name: model_from_dict(body) for name, body in payload["groups"].items()},
            family=payload.get("family") or "ridge_query_groups",
        )
    if kind == "two_head":
        return TwoHeadModel(
            survivor=model_from_dict(payload["survivor"]),
            new=model_from_dict(payload["new"]),
            bias=float(payload["bias"]),
            family=payload.get("family") or "two_head_calibrated",
        )
    if kind == "locked_new":
        return LockedNewModel(
            new_model=model_from_dict(payload["new_model"]),
            scale=float(payload["scale"]),
            bias=float(payload["bias"]),
            family=payload.get("family") or "persistence_locked_full",
        )
    if kind == "lambdamart":
        return BoostingModel(
            features=list(payload["features"]),
            mean=[float(value) for value in payload["mean"]],
            std=[float(value) for value in payload["std"]],
            model_text=payload["model_text"],
            family=payload.get("family") or "lambdamart",
            monotone=bool(payload.get("monotone")),
        )
    if kind == "ensemble":
        return EnsembleModel(
            left=model_from_dict(payload["left"]),
            right=model_from_dict(payload["right"]),
            left_mean=float(payload["left_mean"]),
            left_std=float(payload["left_std"]),
            right_mean=float(payload["right_mean"]),
            right_std=float(payload["right_std"]),
            family=payload.get("family") or "ensemble_ridge_lambdamart",
        )
    raise ValueError(f"Unknown model kind {kind}")


def dump_model(model: object, path: Path, *, extra: dict | None = None) -> None:
    payload = model.to_dict()
    payload["label"] = "PREDICTED"
    if extra:
        payload.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_model(path: Path) -> object:
    return model_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def feature_list(name: str) -> str:
    """The feature block a family is allowed to use, before zero-variance drops."""
    if name == "persistence_ranked_last":
        return "last position only; new URLs tied at -1000"
    if name == "precommitted_blend":
        return "fixed weights on history, title, bm25, page-type interactions, off-page"
    if name == "frozen_style_bm25_fixed":
        return "domain mean, url overlap, bm25 z-weight fixed at -1 on the position line; survivors keep last position"
    if name == "frozen_style_bm25_free":
        return "domain mean, url overlap, bm25 coefficient fit on new URLs; survivors keep last position"
    if name == "ridge_history":
        return "history and SERP-title features"
    if name == "ridge_content":
        return "history plus on-page text, freshness, page type"
    if name == "ridge_tranco_majestic":
        return "history, on-page, Tranco, Majestic"
    if name == "persistence_locked_full":
        return "survivors keep last position; new URLs use history, on-page, Tranco, Majestic, Common Crawl"
    if name == "persistence_locked_serp":
        return "survivors keep last position; new URLs also use the SERP snippet, SERP date, and historical title vocabulary"
    if name == "ridge_serp_text":
        return "full off-page ridge plus SERP snippet, SERP date, and historical title vocabulary"
    if name.startswith("ridge_offpage") or name in {
        "ridge_pointwise",
        "ridge_new_weighted",
        "ridge_query_groups",
        "two_head_calibrated",
        "lambdamart",
        "lambdamart_monotone",
        "blend_persistence",
        "ensemble_ridge_lambdamart",
    }:
        return "history, on-page, Tranco, Majestic, Common Crawl"
    return name


EXPERIMENT_NAMES = (
    "persistence_ranked_last",
    "precommitted_blend",
    "frozen_style_bm25_fixed",
    "frozen_style_bm25_free",
    "ridge_history",
    "ridge_content",
    "ridge_tranco_majestic",
    "ridge_offpage",
    "ridge_offpage_l1",
    "ridge_offpage_l100",
    "ridge_pointwise",
    "ridge_new_weighted",
    "ridge_query_groups",
    "two_head_calibrated",
    "lambdamart",
    "lambdamart_monotone",
    "blend_persistence",
    "ensemble_ridge_lambdamart",
    "persistence_locked_full",
    "persistence_locked_serp",
    "ridge_serp_text",
)

COMPLEXITY = {name: index for index, name in enumerate(EXPERIMENT_NAMES)}


def selection_sort_key(nov9: float, nov11: float, new_new: float, new_surviving: float, complexity: int) -> tuple:
    """Higher is better. The worse early window leads, so a one-window spike does not win."""
    return (min(nov9, nov11), (nov9 + nov11) / 2.0, new_new, new_surviving, -complexity)
