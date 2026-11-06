"""The three-screen workspace: competitor setup, one-query comparisons, and draft tests.

This module reads the project engine through project.py's functions. It does
not train, change, or re-weight the ranker. It adds:

- a sidecar file per project (workspace.json) with the competitor domains and
  what kind of data the project holds (observed, historical, or synthetic);
- CSV checks with line numbers before anything is imported;
- compare_search: one query, the observed order of the stored candidates next to
  the PREDICTED order the held-out model gives the same candidates;
- run_draft: an original page and a draft scored against the same candidates
  with the same model, saved under data/projects/<slug>/drafts/;
- a synthetic demo fixture with .example domains, so the app works with no keys
  and no private data.

House rules: PREDICTED ranks only order the candidates supplied. They are not
Google ranks. Missing HTML is n/a, never guessed. Small score gaps are UNSURE.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aeo.crawl.urls import domain_of

WORKSPACE_FILE = "workspace.json"
DRAFTS_DIR = "drafts"
MAX_CSV_ROWS = 20000
MAX_CSV_ERRORS = 12
MAX_COMPETITORS = 25
DRAFT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")

DATASET_OBSERVED = "observed"
DATASET_HISTORICAL = "historical"
DATASET_SYNTHETIC = "synthetic"
DATASET_NONE = "none"

SYNTHETIC_SOURCE = "SYNTHETIC example data, invented for this demo. Not Google results."
HISTORICAL_SOURCE = "Historical SmartCrew export (smartcrew/serp.csv), pull date not recorded"
SCOPE_NOTE = (
    "PREDICTED ranks order only the candidates listed here: the stored pages from the observed results for this query. "
    "They are not a Google rank and say nothing about the rest of Google's index."
)
CONTENT_LIMIT_NOTE = (
    "In backtests on the stored data, this content model ordered held-out queries no better than a simple baseline. "
    "Treat a PREDICTED order as a check of what the page text shows, not a forecast."
)
DRAFT_LIMIT_NOTE = (
    "A better PREDICTED position means the draft scores higher on this model against these candidates. "
    "It does not show that Google will rank the page higher."
)
AI_KEYS = "OPENROUTER_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY"


class WorkspaceError(ValueError):
    """A user-facing problem with input or project state. The text is safe to show."""


# ---------------------------------------------------------------------------
# Sidecar: competitors and dataset kind
# ---------------------------------------------------------------------------


@dataclass
class WorkspaceMeta:
    dataset: str = DATASET_NONE
    dataset_label: str = ""
    competitors: list[str] = field(default_factory=list)
    updated: str = ""

    @property
    def synthetic(self) -> bool:
        return self.dataset == DATASET_SYNTHETIC


def load_meta(project) -> WorkspaceMeta:
    path = Path(project.dir) / WORKSPACE_FILE
    if not path.is_file():
        return WorkspaceMeta()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WorkspaceMeta()
    return WorkspaceMeta(
        dataset=str(data.get("dataset") or DATASET_NONE),
        dataset_label=str(data.get("dataset_label") or ""),
        competitors=[str(item) for item in data.get("competitors") or [] if item],
        updated=str(data.get("updated") or ""),
    )


def save_meta(project, meta: WorkspaceMeta) -> None:
    meta.updated = _now()
    path = Path(project.dir) / WORKSPACE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_competitors(text: str, own_domain: str = "") -> tuple[list[str], list[str]]:
    """Domains from URLs or bare domains, one per line or comma separated. Returns (domains, problems)."""
    found: list[str] = []
    problems: list[str] = []
    for raw in re.split(r"[\n,]+", text or ""):
        item = raw.strip()
        if not item:
            continue
        candidate = item if "://" in item else f"https://{item}"
        domain = domain_of(candidate)
        if not domain or "." not in domain or " " in item or len(item) > 300:
            problems.append(f"'{item[:80]}' is not a domain or URL.")
            continue
        if own_domain and (domain == own_domain or domain.endswith("." + own_domain)):
            problems.append(f"{domain} is your own site, so it is not listed as a competitor.")
            continue
        if domain not in found:
            found.append(domain)
    if len(found) > MAX_COMPETITORS:
        problems.append(f"At most {MAX_COMPETITORS} competitors. The first {MAX_COMPETITORS} were kept.")
        found = found[:MAX_COMPETITORS]
    return found, problems


def role_of(project, meta: WorkspaceMeta, url: str) -> str:
    """you, competitor, or other."""
    if project.is_our_url(url):
        return "you"
    domain = domain_of(url)
    for name in meta.competitors:
        if domain == name or domain.endswith("." + name):
            return "competitor"
    return "other"


# ---------------------------------------------------------------------------
# CSV check (before import)
# ---------------------------------------------------------------------------


@dataclass
class CsvCheck:
    ok: bool
    rows: int = 0
    queries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    more_errors: int = 0


def check_csv(data: bytes) -> CsvCheck:
    """Check a query,url,position file and report problems by line. Nothing is imported here."""
    if not data.strip():
        return CsvCheck(ok=False, errors=["The file is empty."])
    if b"\x00" in data[:4096]:
        return CsvCheck(ok=False, errors=["This is not a text CSV file. Save it as CSV UTF-8 and try again."])
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return CsvCheck(ok=False, errors=["The file is not UTF-8 text. In Excel, save as 'CSV UTF-8'."])
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        return CsvCheck(ok=False, errors=["The file has no header row. The first line must be: query,url,position"])
    names = [name.strip().lower() for name in header]

    def pick(options: tuple[str, ...]) -> int | None:
        for option in options:
            if option in names:
                return names.index(option)
        return None

    q_col = pick(("query", "top queries", "queries"))
    u_col = pick(("url", "page", "landing page"))
    p_col = pick(("position", "average position"))
    missing = [label for label, col in (("query", q_col), ("url", u_col), ("position", p_col)) if col is None]
    if missing:
        shown = ", ".join(name for name in names if name)[:120] or "nothing"
        return CsvCheck(
            ok=False,
            errors=[f"Missing column{'s' if len(missing) > 1 else ''}: {', '.join(missing)}. The header row has: {shown}. Expected: query,url,position"],
        )
    errors: list[str] = []
    warnings: list[str] = []
    more = 0
    queries: list[str] = []
    seen_pairs: dict[tuple[str, str], int] = {}
    seen_positions: dict[tuple[str, int], int] = {}
    count = 0

    def problem(text_: str) -> None:
        nonlocal more
        if len(errors) < MAX_CSV_ERRORS:
            errors.append(text_)
        else:
            more += 1

    try:
        for line_no, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue
            count += 1
            if count > MAX_CSV_ROWS:
                problem(f"More than {MAX_CSV_ROWS:,} rows. Split the file.")
                break
            cells = row + [""] * (max(q_col, u_col, p_col) + 1 - len(row))
            query = " ".join(cells[q_col].split())
            url = cells[u_col].strip()
            position_text = cells[p_col].strip()
            if not query:
                problem(f"Line {line_no}: the query is empty.")
                continue
            if not url:
                problem(f"Line {line_no}: the url is empty.")
                continue
            if not re.match(r"^https?://[^\s/]+\.[^\s/]+", url, re.I):
                problem(f"Line {line_no}: '{url[:80]}' is not a full http(s) URL.")
                continue
            try:
                position = float(position_text)
            except ValueError:
                problem(f"Line {line_no}: position '{position_text[:20]}' is not a number.")
                continue
            if not math.isfinite(position) or position < 1:
                problem(f"Line {line_no}: position {position_text} must be 1 or more.")
                continue
            if position > 100:
                warnings.append(f"Line {line_no}: position {position_text} is past 100. Only positions 1 to 10 are used as candidates.")
            key = (query.lower(), url.lower().rstrip("/"))
            if key in seen_pairs:
                warnings.append(f"Line {line_no}: {url[:80]} is listed twice for '{query}' (also line {seen_pairs[key]}). The best position is kept.")
            else:
                seen_pairs[key] = line_no
            slot = (query.lower(), int(round(position)))
            if slot in seen_positions and slot[1] <= 10:
                warnings.append(f"Line {line_no}: two URLs at position {slot[1]} for '{query}' (also line {seen_positions[slot]}).")
            else:
                seen_positions.setdefault(slot, line_no)
            if query not in queries:
                queries.append(query)
    except csv.Error as exc:
        problem(f"The CSV could not be read: {exc}.")
    if count == 0 and not errors:
        errors.append("The file has a header row but no data rows.")
    return CsvCheck(ok=not errors, rows=count, queries=queries, errors=errors, warnings=warnings[:MAX_CSV_ERRORS], more_errors=more)


# ---------------------------------------------------------------------------
# Coverage: what is stored for each query (works before an engine exists)
# ---------------------------------------------------------------------------


@dataclass
class QueryCoverage:
    search: str
    candidates: int
    stored: int
    missing: list[str]
    yours: int | None  # observed position of your site, if listed
    competitors: list[str]
    source: str
    pulled: str
    error: str = ""


def coverage(project, database: str, meta: WorkspaceMeta | None = None) -> list[QueryCoverage]:
    from aeo.ranksim.project import TOP_DEPTH, _key, _stored_index, latest_google, loose_key

    meta = meta or load_meta(project)
    google = latest_google(project)
    if google is None:
        return []
    try:
        index = _stored_index(database)
    except Exception:  # noqa: BLE001 - no database yet means nothing stored
        index = {}
    rows: list[QueryCoverage] = []
    by_search = google.by_search()
    for search in project.searches:
        entry = by_search.get(search)
        results = sorted(entry.results, key=lambda item: item.position) if entry else []
        seen: set[str] = set()
        top = []
        for item in results:
            key = loose_key(item.url)
            if key in seen or not 1 <= item.position <= TOP_DEPTH:
                continue
            seen.add(key)
            top.append(item)
        stored = [item for item in top if (index.get(_key(item.url)) or {}).get("has_html")]
        yours = next((item.position for item in top if project.is_our_url(item.url)), None)
        comps = sorted({domain_of(item.url) for item in top if role_of(project, meta, item.url) == "competitor"})
        rows.append(
            QueryCoverage(
                search=search,
                candidates=len(top),
                stored=len(stored),
                missing=[item.url for item in top if item not in stored],
                yours=yours,
                competitors=comps,
                source=entry.source if entry else "",
                pulled=entry.pulled_label if entry and results else "",
                error="" if results else ((entry.error if entry else "") or "no observed results for this query"),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Comparison: one query, observed order next to PREDICTED order
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    observed: int
    url: str
    domain: str
    title: str
    role: str
    stored: bool
    stored_at: str = ""
    seeded_from: str = ""
    predicted: int | None = None
    score: float | None = None
    word_count: int | None = None
    h1: str = ""
    has_table: bool | None = None
    has_faq: bool | None = None
    link_count: int | None = None
    updated: str = ""
    shift: int | None = None  # observed rank among scored candidates minus PREDICTED rank (positive: model puts it higher)
    evidence: list[dict] = field(default_factory=list)  # feature, label, value text, rank among scored


@dataclass
class ComparisonView:
    search: str
    intent: str
    source: str
    pulled: str
    dataset: str
    candidates: list[Candidate]
    scored: int
    total: int
    deeper: int
    your_url: str | None
    your_observed: int | None
    your_predicted: int | None
    confidence: str
    confidence_note: str
    agreement: float | None  # Spearman between observed and PREDICTED order of the scored candidates
    query_pairwise: float | None
    query_pairs: int | None
    overall_pairwise: float | None
    overall_queries: int
    model_kind: str
    notes: list[str] = field(default_factory=list)
    error: str = ""


EVIDENCE_FEATURES = (
    "bm25_title", "bm25_h1", "bm25_headings", "bm25_body", "topic_coverage", "word_count", "intent_match",
    "has_table", "has_faq", "freshness", "links", "domain_rank", "crawl_inlinks",
)


def compare_search(ctx, search: str, meta: WorkspaceMeta | None = None) -> ComparisonView:
    from aeo.ranksim.features import raw_features
    from aeo.ranksim.lambdamart import RankExample, predict_scores
    from aeo.ranksim.project import TOP_DEPTH, _key, _lookup, guess_intent, loose_key

    project = ctx.project
    meta = meta or load_meta(project)
    intent = project.intents.get(search) or guess_intent(search)
    entry = ctx.google.by_search().get(search)
    view = ComparisonView(
        search=search,
        intent=intent,
        source=entry.source if entry else "",
        pulled=entry.pulled_label if entry else "",
        dataset=meta.dataset,
        candidates=[],
        scored=0,
        total=0,
        deeper=0,
        your_url=None,
        your_observed=None,
        your_predicted=None,
        confidence="n/a",
        confidence_note="",
        agreement=None,
        query_pairwise=None,
        query_pairs=None,
        overall_pairwise=ctx.metrics.pairwise,
        overall_queries=ctx.metrics.n_searches,
        model_kind="",
    )
    backtest = ctx.metrics.for_search(search)
    if backtest is not None:
        view.query_pairwise, view.query_pairs = backtest.pairwise, backtest.pairs
    if entry is None or not entry.results:
        view.error = "No observed results are recorded for this query, so there is nothing to compare. Import a CSV that includes it."
        return view
    seen: set[str] = set()
    docs: dict[str, object] = {}
    for item in sorted(entry.results, key=lambda result: result.position):
        key = loose_key(item.url)
        if key in seen:
            continue
        seen.add(key)
        if not 1 <= item.position <= TOP_DEPTH:
            view.deeper += 1
            continue
        doc = _lookup(ctx.pages, item.url)
        stored = doc is not None and bool((doc.title or doc.body or "").strip())
        cand = Candidate(
            observed=item.position,
            url=item.url,
            domain=item.domain or domain_of(item.url),
            title=(item.title or (doc.title if stored else "") or "").strip(),
            role=role_of(project, meta, item.url),
            stored=stored,
            stored_at=_short_date(ctx.stored_at.get(_key(item.url))) if stored else "",
            seeded_from=str(ctx.seeded.get(_key(item.url)) or "") if stored else "",
        )
        if stored:
            cand.word_count = int(doc.word_count)
            cand.h1 = (doc.h1 or "").strip()
            cand.has_table = bool(doc.table_count)
            cand.has_faq = bool(doc.has_faq)
            cand.link_count = int(doc.link_count)
            cand.updated = doc.updated or ""
            docs[item.url] = doc
        view.candidates.append(cand)
        if cand.role == "you" and view.your_url is None:
            view.your_url, view.your_observed = item.url, item.position
    view.total = len(view.candidates)
    model, kind = ctx.model_for(search)
    view.model_kind = kind
    scored = [cand for cand in view.candidates if cand.stored]
    view.scored = len(scored)
    if (ctx.metrics.n_searches or 0) < 2:
        view.error = (
            "The model is always tested on a query it did not learn from, so it needs at least two queries with stored pages. "
            "This project has fewer, so no PREDICTED order is shown. Add queries and build again."
        )
        return view
    if model is None:
        view.error = "The engine has no model yet: it needs at least two queries with two or more stored pages. No order was invented."
        return view
    if len(scored) < 2:
        view.error = (
            f"Only {len(scored)} of {view.total} candidates have stored HTML, so there is no order to predict. "
            "Run 'Fetch pages and build' on the Setup screen, or check the crawl log for blocked pages."
        )
        return view
    page_list = [docs[cand.url] for cand in scored]
    raw = raw_features(search, intent, page_list, ctx.today)
    examples = [RankExample(search, doc.url, 0.0, row, intent) for doc, row in zip(page_list, raw)]
    scores = predict_scores(model, examples)
    values = [scores[id(example)] for example in examples]
    order = sorted(range(len(scored)), key=lambda index: (-values[index], scored[index].observed))
    for rank, index in enumerate(order, start=1):
        scored[index].predicted = rank
        scored[index].score = round(float(values[index]), 4)
    observed_rank = {id(cand): rank for rank, cand in enumerate(sorted(scored, key=lambda c: c.observed), start=1)}
    for cand in scored:
        cand.shift = observed_rank[id(cand)] - int(cand.predicted or 0)
    view.agreement = _spearman([observed_rank[id(cand)] for cand in scored], [int(cand.predicted or 0) for cand in scored])
    # Evidence: each feature's value and where it ranks among the scored candidates.
    for name in EVIDENCE_FEATURES:
        column = [row.get(name) for row in raw]
        valid = [value for value in column if value is not None and value == value]
        if not valid:
            continue
        for index, cand in enumerate(scored):
            value = column[index]
            if value is None or value != value:
                continue
            better = sum(1 for other in valid if _better(name, other, value))
            cand.evidence.append(
                {
                    "feature": name,
                    "label": _feature_label(name),
                    "value": _value_text(name, float(value), page_list[index]),
                    "rank": better + 1,
                    "of": len(valid),
                }
            )
    if view.your_url is not None:
        mine = next(cand for cand in view.candidates if cand.url == view.your_url)
        view.your_predicted = mine.predicted
        if mine.stored:
            mine_index = scored.index(mine)
            view.confidence, view.confidence_note = _gap_confidence(ctx, values, mine_index, thin=view.scored * 2 < view.total)
        else:
            view.confidence_note = "Your page is in the observed results but its HTML is not stored, so it has no PREDICTED position."
    if view.scored < view.total:
        view.notes.append(
            f"{view.total - view.scored} of {view.total} candidates have no stored HTML. They keep their observed position and get PREDICTED n/a."
        )
    if view.deeper:
        view.notes.append(f"{view.deeper} observed results below position {TOP_DEPTH} are not used as candidates.")
    if view.scored * 2 < view.total:
        view.notes.append("Under half of the candidates are stored, so every PREDICTED position on this query is UNSURE.")
    return view


def _gap_confidence(ctx, values: list[float], index: int, thin: bool) -> tuple[str, str]:
    order = sorted(range(len(values)), key=lambda i: -values[i])
    place = order.index(index)
    gaps = []
    if place > 0:
        gaps.append(abs(values[index] - values[order[place - 1]]))
    if place + 1 < len(order):
        gaps.append(abs(values[index] - values[order[place + 1]]))
    from aeo.ranksim.project import _is_synthetic

    label, note = ctx.fit.calibration.product_label(
        min(gaps) if gaps else 0.0,
        synthetic=_is_synthetic(ctx.project),
    )
    if thin and label != "UNSURE":
        return "UNSURE", "Under half of the candidates are stored, so this stays UNSURE. " + note
    return label, note


def _spearman(a: list[int], b: list[int]) -> float | None:
    n = len(a)
    if n < 3:
        return None
    d2 = sum((x - y) ** 2 for x, y in zip(a, b))
    return round(1 - 6 * d2 / (n * (n * n - 1)), 3)


def _better(name: str, other: float, value: float) -> bool:
    if other != other:
        return False
    if name in {"thin_content", "link_depth", "orphan", "site_radius", "ad_density"}:
        return other < value
    return other > value


def _feature_label(name: str) -> str:
    from aeo.ranksim.attribution import LABELS

    return {
        "bm25_title": "query words in title",
        "bm25_h1": "query words in H1",
        "bm25_headings": "query words in headings",
        "bm25_body": "query words in body",
        "topic_coverage": "topic coverage",
        "word_count": "length",
        "intent_match": "matches search intent",
        "has_table": "has a table",
        "has_faq": "has an FAQ",
        "freshness": "visible date",
        "links": "links on page",
        "domain_rank": "site link signal (crawl only)",
        "crawl_inlinks": "links in from crawled pages",
    }.get(name, LABELS.get(name, name))


def _value_text(name: str, value: float, doc) -> str:
    if name == "word_count":
        return f"{doc.word_count} words"
    if name in {"has_table", "has_faq"}:
        return "yes" if value >= 0.5 else "no"
    if name == "freshness":
        return f"dated {doc.updated}" if doc.updated else "no visible date"
    if name == "links":
        return f"{int(value)}"
    if name == "crawl_inlinks":
        return f"{int(value)}"
    return f"{value:.2f}"


def your_url(ctx, search: str, meta: WorkspaceMeta | None = None) -> str | None:
    """The best observed position of your own site for this query, or None."""
    entry = ctx.google.by_search().get(search)
    if entry is None:
        return None
    for item in sorted(entry.results, key=lambda result: result.position):
        if ctx.project.is_our_url(item.url):
            return item.url
    return None


# ---------------------------------------------------------------------------
# Draft test: original vs draft against the same candidates
# ---------------------------------------------------------------------------

PAGE_FACTS = (
    ("title", "Title"),
    ("h1", "H1"),
    ("word_count", "Words"),
    ("headings", "Subheadings (H2 to H6)"),
    ("table_count", "Tables"),
    ("has_faq", "FAQ section"),
    ("link_count", "Links"),
    ("updated", "Visible date"),
)


def candidate_set(ctx, search: str, meta: WorkspaceMeta, which: str, exclude_url: str) -> tuple[list, list, str]:
    """(stored candidates, missing candidates, plain label) for a draft test. Excludes the page under test."""
    from aeo.ranksim.project import TOP_DEPTH, _lookup, loose_key

    entry = ctx.google.by_search().get(search)
    if entry is None or not entry.results:
        raise WorkspaceError("No observed results are recorded for this query, so there are no candidates to test against.")
    skip = loose_key(exclude_url) if exclude_url else ""
    seen: set[str] = set()
    stored: list = []
    missing: list = []
    for item in sorted(entry.results, key=lambda result: result.position):
        key = loose_key(item.url)
        if key in seen or key == skip or not 1 <= item.position <= TOP_DEPTH:
            seen.add(key)
            continue
        seen.add(key)
        role = role_of(ctx.project, meta, item.url)
        if role == "you":
            continue  # your other pages are not your competition here
        if which == "competitors" and role != "competitor":
            continue
        doc = _lookup(ctx.pages, item.url)
        if doc is not None and (doc.title or doc.body or "").strip():
            stored.append((item, doc))
        else:
            missing.append(item)
    label = "named competitors only" if which == "competitors" else "all observed candidates"
    return stored, missing, label


def _facts(doc, html: str = "") -> dict:
    return {
        "title": (doc.title or "").strip(),
        "h1": (doc.h1 or "").strip(),
        "word_count": int(doc.word_count),
        "headings": len(re.findall(r"<h[2-6][\s>]", html or "", re.I)),
        "table_count": int(doc.table_count),
        "has_faq": "yes" if doc.has_faq else "no",
        "link_count": int(doc.link_count),
        "updated": doc.updated or "none",
    }


def _place(ctx, search: str, intent: str, model, candidates: list, page) -> dict:
    """Score candidates plus one page in one batch. Returns the PREDICTED order and the page's slot."""
    from aeo.ranksim.features import raw_features
    from aeo.ranksim.lambdamart import RankExample, predict_scores

    docs = [doc for _item, doc in candidates] + [page]
    raw = raw_features(search, intent, docs, ctx.today)
    examples = [RankExample(search, doc.url, 0.0, row, intent) for doc, row in zip(docs, raw)]
    scores = predict_scores(model, examples)
    values = [float(scores[id(example)]) for example in examples]
    mine = values[-1]
    position = 1 + sum(1 for value in values[:-1] if value > mine)
    order = sorted(range(len(docs)), key=lambda index: (-values[index], index))
    listing = []
    for rank, index in enumerate(order, start=1):
        if index == len(docs) - 1:
            listing.append({"predicted": rank, "page": True, "score": round(mine, 4)})
        else:
            item, _doc = candidates[index]
            listing.append({"predicted": rank, "page": False, "url": item.url, "domain": item.domain or domain_of(item.url), "observed": item.position, "score": round(values[index], 4)})
    confidence, note = _gap_confidence(ctx, values, len(values) - 1, thin=False)
    features = {name: (None if raw[-1].get(name) is None or raw[-1].get(name) != raw[-1].get(name) else round(float(raw[-1][name]), 4)) for name in EVIDENCE_FEATURES}
    return {"position": position, "of": len(docs), "score": round(mine, 4), "order": listing, "confidence": confidence, "confidence_note": note, "features": features}


def run_draft(
    ctx,
    search: str,
    draft_html: str,
    url: str,
    *,
    original_html: str | None = None,
    original_label: str = "",
    which: str = "all",
    meta: WorkspaceMeta | None = None,
    ai_notes=None,
) -> dict:
    """Score the original (if any) and the draft against the same candidates with the same held-out model."""
    from aeo.ranksim.loop import page_from_html
    from aeo.ranksim.project import _our_doc, guess_intent

    project = ctx.project
    meta = meta or load_meta(project)
    if search not in project.searches:
        raise WorkspaceError("Pick a query from this project.")
    if not (draft_html or "").strip():
        raise WorkspaceError("Paste the draft HTML or text. Nothing was run.")
    intent = project.intents.get(search) or guess_intent(search)
    model, kind = ctx.model_for(search)
    if model is None:
        raise WorkspaceError("The engine has no model yet. Build it on the Setup screen first. Nothing was run.")
    if (ctx.metrics.n_searches or 0) < 2:
        raise WorkspaceError("The model needs at least two queries with stored pages so it is tested on a query it did not learn from. Nothing was run.")
    candidates, missing, set_label = candidate_set(ctx, search, meta, which, url)
    if not candidates:
        if which == "competitors":
            raise WorkspaceError(
                "None of your named competitors has a stored page in the observed results for this query. "
                "Pick 'All observed candidates', or add competitors that appear for it."
            )
        raise WorkspaceError("No candidate for this query has stored HTML, so there is nothing to test against. Build the engine on the Setup screen.")
    draft_html = _as_html(draft_html)
    draft_doc = _our_doc(ctx, page_from_html(url, draft_html, is_ours=True), url)
    original_doc = None
    if original_html is not None and original_html.strip():
        original_html = _as_html(original_html)
        original_doc = _our_doc(ctx, page_from_html(url, original_html, is_ours=True), url)

    def placed(doc):
        # Same rule as the engine's board: a page the on-topic check does not pass gets no slot at all.
        if ctx.topic is not None:
            check = ctx.topic.check(search, doc)
            if check.on_topic is False or check.status == "not checked":
                return {"position": None, "of": len(candidates) + 1, "score": None, "order": [], "confidence": "n/a", "confidence_note": "", "features": {}, "unplaced": _observed_text(check.text)}
            placed_row = _place(ctx, search, intent, model, candidates, doc)
            if check.status == "uncertain":
                placed_row["confidence"] = "UNSURE"
                placed_row["confidence_note"] = _observed_text(check.text)
                placed_row["unplaced"] = ""
                placed_row["topic_note"] = placed_row["confidence_note"]
            return placed_row
        return _place(ctx, search, intent, model, candidates, doc)

    after = placed(draft_doc)
    before = placed(original_doc) if original_doc is not None else None
    topic_after = topic_before = None
    if ctx.topic is not None:
        check = ctx.topic.check(search, draft_doc)
        topic_after = {"on_topic": check.on_topic, "status": check.status, "text": check.text, "missing": list(check.missing)[:8]}
        if original_doc is not None:
            check_b = ctx.topic.check(search, original_doc)
            topic_before = {"on_topic": check_b.on_topic, "status": check_b.status, "text": check_b.text, "missing": list(check_b.missing)[:8]}
    facts_after = _facts(draft_doc, draft_html)
    facts_before = _facts(original_doc, original_html or "") if original_doc is not None else None
    from aeo.ranksim.features import THIN_WORD_GATE

    notes = []
    if draft_doc.word_count < THIN_WORD_GATE:
        notes.append(
            f"The draft has {draft_doc.word_count} words. This model ranks pages under {THIN_WORD_GATE} words behind every fuller page, "
            "so its PREDICTED position mostly reflects length."
        )
    if missing:
        notes.append(f"{len(missing)} candidate{'s' if len(missing) != 1 else ''} in this set had no stored HTML and were left out.")
    backtest = ctx.metrics.for_search(search)
    run = {
        "id": "",
        "project": project.slug,
        "search": search,
        "intent": intent,
        "url": url,
        "which": which,
        "set_label": set_label,
        "candidates": len(candidates),
        "missing_candidates": [item.url for item in missing],
        "original_label": original_label if original_doc is not None else "",
        "has_original": original_doc is not None,
        "before": before,
        "after": after,
        "facts_before": facts_before,
        "facts_after": facts_after,
        "topic_before": topic_before,
        "topic_after": topic_after,
        "model_kind": kind,
        "calibration_note": ctx.fit.calibration.note,
        "calibration_held_out": bool(ctx.fit.calibration.held_out),
        "query_pairwise": backtest.pairwise if backtest else None,
        "query_pairs": backtest.pairs if backtest else None,
        "overall_pairwise": ctx.metrics.pairwise,
        "source": (ctx.google.by_search().get(search).source if ctx.google.by_search().get(search) else ""),
        "pulled": (ctx.google.by_search().get(search).pulled_label if ctx.google.by_search().get(search) else ""),
        "dataset": meta.dataset,
        "created": _now(),
        "ai": {"status": "off", "text": ""},
        "notes": notes,
    }
    if before is not None and before.get("position") is not None and after.get("position") is not None:
        run["change"] = before["position"] - after["position"]  # positive: the draft is PREDICTED higher
    if ai_notes is not None:
        run["ai"] = ai_notes(run)
    return run


def _observed_text(text: str) -> str:
    """The engine's on-topic text speaks of 'pages Google ranks'. Here the pages are the observed candidates."""
    return (
        (text or "")
        .replace("the most repetitive readable page Google ranks in this project", "the most repetitive readable observed page in this project")
        .replace("Every readable page Google ranks here", "Every readable observed page here")
        .replace("the pages Google ranks here", "the observed pages here")
        .replace("pages Google ranks here", "observed pages here")
        .replace("some page Google ranks here", "some observed page here")
        .replace("the pages Google ranks for", "the observed pages for")
        .replace("PREDICTED: not in the top 10.", "PREDICTED: not placed among these candidates.")
    )


def engine_state(project) -> tuple[bool, str]:
    """(ready, reason). Ready means the newest build trained a model on at least two queries with stored pages."""
    from aeo.ranksim.project import list_pulls, load_pull_report

    pulls = [item for item in list_pulls(project) if item.finished and item.has_engine]
    if not pulls:
        return False, "not built yet"
    report = load_pull_report(project, pulls[-1].pull_id)
    metrics = report.metrics if report else None
    if metrics is None or (metrics.n_searches or 0) < 2 or not (metrics.n_pairs or 0):
        return False, "the last build stored too few pages to train a model (it needs two or more queries with two or more stored pages)"
    return True, "ready"


def _as_html(text: str) -> str:
    """Plain pasted text becomes paragraphs so it can be read as a page. HTML is left as is."""
    if re.search(r"<\s*(html|body|p|h1|h2|div|title|article|section)\b", text, re.I):
        return text
    import html as _html

    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if not blocks:
        return text
    lines = blocks[0].splitlines()
    first = lines[0].strip()[:160]
    rest = [" ".join(lines[1:]).strip()] if len(lines) > 1 and " ".join(lines[1:]).strip() else []
    parts = [f"<html><head><title>{_html.escape(first)}</title></head><body><h1>{_html.escape(first)}</h1>"]
    for block in rest + blocks[1:]:
        parts.append(f"<p>{_html.escape(' '.join(block.split()))}</p>")
    parts.append("</body></html>")
    return "".join(parts)


def save_draft_run(project, run: dict, original_html: str | None, draft_html: str, database: str | None = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{secrets.token_hex(3)}"
    folder = Path(project.dir) / DRAFTS_DIR / run_id
    folder.mkdir(parents=True, exist_ok=True)
    run["id"] = run_id
    (folder / "draft.html").write_text(draft_html, encoding="utf-8")
    if original_html is not None and original_html.strip():
        (folder / "original.html").write_text(original_html, encoding="utf-8")
    (folder / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    if database and run.get("dataset") != DATASET_SYNTHETIC:
        _record_draft_ledger(database, run, draft_html)
    return run_id


def _candidate_html_sha256(database: str, url: str) -> str | None:
    """SHA-256 of the stored fetched HTML for this URL, or None when that HTML is absent."""
    from aeo.ranksim.ledger import _sha
    from aeo.ranksim.tester import stored_html

    found = stored_html(database, url)
    if found is None or not (found[0] or "").strip():
        return None
    return _sha(found[0])


def _record_draft_ledger(database: str, run: dict, draft_html: str) -> None:
    """Append one PREDICTED draft to the audit database ledger. Synthetic runs are not recorded."""
    if run.get("dataset") in {DATASET_SYNTHETIC, DATASET_HISTORICAL, DATASET_NONE}:
        return
    after = run.get("after") or {}
    if after.get("position") is None:
        return
    url = str(run.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return
    from aeo.ranksim.ledger import Call, record_prediction
    from aeo.ranksim.store import open_session

    shown = str(after.get("confidence") or "UNSURE")
    note = str(after.get("confidence_note") or run.get("calibration_note") or "")
    if not note:
        note = "PREDICTED slot among supplied candidates. Not a Google position."
    calls = []
    candidates = []
    yours = int(after["position"])
    for item in after.get("order") or []:
        if item.get("page") or not item.get("url"):
            continue
        calls.append(
            Call(
                competitor_url=item["url"],
                predicted="above" if yours < int(item["predicted"]) else "below",
                score_gap=None,
                confidence=shown,
                note=note,
            )
        )
        candidates.append(
            {
                "url": item["url"],
                "html_sha256": _candidate_html_sha256(database, item["url"]),
                "labeled_position": item.get("observed"),
            }
        )
    session = open_session(database)
    try:
        record_prediction(
            session,
            kind="workspace_draft",
            query=run["search"],
            subject_url=url,
            subject_html=draft_html,
            predicted_rank=yours,
            predicted_of=int(after.get("of") or yours),
            candidates=candidates,
            calls=calls,
            confidence=shown,
            calibration_note=note,
            project_slug=str(run.get("project") or ""),
            provenance={
                "dataset": run.get("dataset") or "",
                "source": "workspace",
                "model_kind": run.get("model_kind") or "",
                "instrument": "lambdamart",
                "features": "FEATURE_NAMES",
                "held_out": bool(run.get("calibration_held_out")),
            },
        )
    finally:
        session.close()


def record_observed_import(
    database: str,
    project,
    meta: WorkspaceMeta,
    data: bytes,
    filename: str,
    observed_on: str | None,
    *,
    public_demo: bool = False,
) -> int:
    """Append dated CSV rows as imported observations. Returns how many queries were written.

    A row is recorded when all of these hold:
    - the server is not the public demo
    - the project dataset is ``none`` or already ``observed`` (a dated file is observed evidence)
    - the dataset is not ``synthetic`` and not ``historical``
    - the upload includes a YYYY-MM-DD date the positions were seen

    The caller still marks a ``none`` project ``observed`` after a successful import.
    That label means an imported file, not a live Google capture. Synthetic and historical
    projects are demonstrations and are not recorded. The same project, file hash, date,
    and query are not written twice. The row keeps the project slug, file name, sha256,
    and upload time. ``observed_on`` is the date from the upload, not an invented clock time.
    The note says the file is not a live Google pull.
    """
    if public_demo or not observed_on or not re.match(r"^\d{4}-\d{2}-\d{2}$", observed_on):
        return 0
    if meta.dataset not in {DATASET_NONE, DATASET_OBSERVED}:
        return 0
    import hashlib
    import tempfile
    from collections import defaultdict
    from datetime import datetime, timezone

    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.ledger import record_outcome
    from aeo.ranksim.store import open_session

    folder_file = Path(filename)
    digest = hashlib.sha256(data).hexdigest()
    uploaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    grouped: dict[str, dict[str, int]] = defaultdict(dict)
    with tempfile.NamedTemporaryFile(suffix=".csv") as handle:
        handle.write(data)
        handle.flush()
        labels = load_serp_file(handle.name)
    for label in labels:
        grouped[label.query][label.url] = int(label.position)
    session = open_session(database)
    written = 0
    try:
        from aeo.ranksim.ledger import RsOutcome

        for query, positions in grouped.items():
            folded = " ".join(query.split())
            already = (
                session.query(RsOutcome)
                .filter(
                    RsOutcome.project_slug == project.slug,
                    RsOutcome.source_sha256 == digest,
                    RsOutcome.observed_on == observed_on,
                    RsOutcome.query == folded,
                )
                .first()
            )
            if already is not None:
                continue
            record_outcome(
                session,
                query=query,
                observed_on=observed_on,
                provider="imported_csv",
                positions=positions,
                depth=max(positions.values()) if positions else None,
                notes="Imported file. Not a live Google pull.",
                project_slug=project.slug,
                source_file=folder_file.name,
                source_sha256=digest,
                uploaded_at=uploaded_at,
            )
            written += 1
    finally:
        session.close()
    return written


def load_draft_run(project, run_id: str) -> dict | None:
    if not DRAFT_ID_RE.match(run_id or ""):
        return None
    path = Path(project.dir) / DRAFTS_DIR / run_id / "run.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def draft_html_of(project, run_id: str, which: str) -> str | None:
    if not DRAFT_ID_RE.match(run_id or "") or which not in {"draft", "original"}:
        return None
    path = Path(project.dir) / DRAFTS_DIR / run_id / f"{which}.html"
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def list_draft_runs(project, limit: int = 12) -> list[dict]:
    folder = Path(project.dir) / DRAFTS_DIR
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.iterdir(), reverse=True):
        if not DRAFT_ID_RE.match(path.name):
            continue
        run = load_draft_run(project, path.name)
        if run is not None:
            found.append(run)
        if len(found) >= limit:
            break
    return found


def ai_notes_text(llm, run: dict) -> dict:
    """Optional AI notes from a configured provider. Sends the measured differences only, never the page text."""
    facts = {"before": run.get("facts_before"), "after": run.get("facts_after")}
    summary = {
        "query": run["search"],
        "candidates": run["candidates"],
        "predicted_before": (run.get("before") or {}).get("position"),
        "predicted_after": run["after"]["position"],
        "facts": facts,
        "topic_after": (run.get("topic_after") or {}).get("status"),
        "missing_topic_words": (run.get("topic_after") or {}).get("missing"),
    }
    system = (
        "You review SEO page drafts. You get measured facts, not the page. Write at most 4 short bullet points on what changed "
        "between the original and the draft and what is still missing for the query. Say plainly that the PREDICTED position "
        "is a model guess among the listed candidates, not a Google rank. Do not promise ranking gains."
    )
    try:
        reply = llm.complete(system, json.dumps(summary))
    except Exception as exc:  # noqa: BLE001 - shown to the user, never retried silently
        return {"status": "error", "text": f"The AI provider call failed: {type(exc).__name__}. No notes were added."}
    text = (getattr(reply, "text", "") or "").strip()
    if not text:
        return {"status": "error", "text": "The AI provider returned no text. No notes were added."}
    return {"status": "ok", "text": text[:2000], "provider": getattr(llm, "name", "")}


# ---------------------------------------------------------------------------
# Synthetic demo fixture (.example domains, no private data, no network)
# ---------------------------------------------------------------------------

DEMO_NAME = "Northwind Fencing (synthetic demo)"
DEMO_SLUG = "synthetic-demo"
DEMO_SITE = "www.northwind-fencing.example"
DEMO_COMPETITORS = ["fenceguide.example", "yardpro.example", "picketline.example"]

_TOPICS = {
    "vinyl fence installation cost": ["vinyl", "fence", "installation", "cost", "price", "linear foot", "labor", "panels", "posts", "quote"],
    "how long does a cedar fence last": ["cedar", "fence", "lifespan", "years", "rot", "stain", "seal", "weather", "maintenance", "posts"],
    "chain link fence vs vinyl": ["chain link", "vinyl", "fence", "cost", "privacy", "durability", "maintenance", "security", "look", "compare"],
    "fence permit requirements": ["permit", "fence", "height", "property line", "setback", "zoning", "HOA", "survey", "inspection", "city"],
    "best fence for privacy": ["privacy", "fence", "board on board", "vinyl", "height", "gaps", "noise", "neighbors", "panels", "wood"],
    "aluminum fence pros and cons": ["aluminum", "fence", "pros", "cons", "rust", "pool", "strength", "cost", "style", "maintenance"],
}

# (domain, path pattern, quality 0..1). Positions follow quality with noise, so the model has something to learn.
_SITES = [
    ("fenceguide.example", "guides", 0.86),
    ("yardpro.example", "blog", 0.74),
    ("www.northwind-fencing.example", "resources", 0.62),
    ("picketline.example", "learn", 0.66),
    ("homecost-hub.example", "costs", 0.58),
    ("diy-backyard.example", "projects", 0.45),
    ("forum.fencetalk.example", "threads", 0.38),
    ("cityfence-supply.example", "products", 0.5),
    ("buildwiki.example", "wiki", 0.55),
]
# Pages the fixture fetcher refuses, so the demo shows missing HTML honestly.
_BLOCKED = {"forum.fencetalk.example": "HTTP 403", "cityfence-supply.example": "blocked by robots.txt"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def synthetic_serp() -> list[tuple[str, str, int]]:
    """query, url, position rows. Deterministic."""
    rng = random.Random(20261105)
    rows = []
    for index, search in enumerate(_TOPICS):
        sites = list(_SITES)
        if index % 3 == 2:
            sites = [site for site in sites if not site[0].startswith("www.northwind")]  # your site is missing on some queries
        ranked = sorted(sites, key=lambda site: -(site[2] + rng.uniform(-0.18, 0.18)))[:8]
        for position, (domain, path, _quality) in enumerate(ranked, start=1):
            rows.append((search, f"https://{domain}/{path}/{_slug(search)}/", position))
    return rows


def synthetic_csv() -> str:
    lines = ["query,url,position"]
    lines.extend(f"{query},{url},{position}" for query, url, position in synthetic_serp())
    return "\n".join(lines) + "\n"


_FILLER = [
    "Most homeowners compare three or four options before they decide.",
    "Local rules and soil conditions change the answer more than people expect.",
    "We measured this on real projects and kept notes on what went wrong.",
    "Prices in this guide are examples for this demo, not quotes.",
    "Ask for a written estimate that lists materials, labor, and removal separately.",
    "Gates and corners add cost because they need stronger posts.",
    "A careful layout before digging saves money later.",
    "Check the warranty terms for both the material and the installation.",
]


def synthetic_page(url: str) -> str | None:
    """HTML for one synthetic URL, or None when the fixture refuses it."""
    domain = domain_of(url)
    site = next((item for item in _SITES if item[0] == domain or item[0].endswith(domain)), None)
    if site is None or domain in _BLOCKED:
        return None
    search = next((query for query in _TOPICS if f"/{_slug(query)}/" in url), None)
    if search is None:
        return None
    quality = site[2]
    rng = random.Random(f"{url}|page")
    words = _TOPICS[search]
    brand = domain.split(".")[0].replace("www", "").replace("-", " ").strip().title() or "Site"
    if domain.startswith("www."):
        brand = domain.split(".")[1].replace("-", " ").title()
    styles = [
        f"{search.capitalize()}: what to expect",
        f"{search.capitalize()} explained",
        f"A homeowner's guide to {search}",
        f"{search.capitalize()} | {brand}",
        f"{search.capitalize()}: notes from real projects",
    ]
    if quality > 0.5 or rng.random() < 0.3:
        title = styles[sum(map(ord, domain)) % len(styles)]
    else:
        title = f"{words[1].capitalize()} tips from {brand}"
    sections = max(2, int(2 + quality * 6 + rng.uniform(-1, 1)))
    paragraphs_per = max(1, int(1 + quality * 3))
    body = []
    for number in range(sections):
        topic = words[(number + 2) % len(words)]
        heading = f"{topic.title()} and {words[0]}" if number % 2 == 0 else f"What to know about {topic}"
        body.append(f"<h2>{heading}</h2>")
        for _ in range(paragraphs_per):
            picked = rng.sample(words, k=min(4, len(words)))
            sentence = (
                f"When people search for {search}, they usually want to know about {picked[0]} and {picked[1]}. "
                f"Here is how {picked[2]} affects the result, and why {picked[3]} matters for a {words[1]} project. "
            )
            body.append("<p>" + sentence + " ".join(rng.sample(_FILLER, k=3)) + "</p>")
    if quality > 0.6:
        rows = "".join(f"<tr><td>{word.title()}</td><td>Example value {i + 1}</td></tr>" for i, word in enumerate(words[2:6]))
        body.append(f"<table><tr><th>Item</th><th>Example</th></tr>{rows}</table>")
    if quality > 0.7:
        body.append(
            "<h2>FAQ</h2><h3>How much does it cost?</h3><p>It depends on length, material, and access. These figures are examples.</p>"
            f"<h3>How long does it take?</h3><p>Most {words[1]} jobs in this demo take two to four days.</p>"
        )
    links = "".join(f'<a href="https://{domain}/{site[1]}/related-{i}/">Related {i}</a> ' for i in range(int(3 + quality * 8)))
    date = f"2026-{int(3 + quality * 7):02d}-{int(10 + rng.random() * 15):02d}"
    return (
        f"<!doctype html><html><head><title>{title}</title><meta name=\"description\" content=\"Synthetic demo page about {search}.\"></head>"
        f"<body><article><h1>{title}</h1><p><time datetime=\"{date}\">Updated {date}</time></p>"
        + "".join(body)
        + f"<nav>{links}</nav></article></body></html>"
    )


def synthetic_fetcher(urls: list[str]):
    """A FetchBatch for project.pull: serves fixture HTML, refuses everything else. No network."""
    from aeo.ranksim.loop import FetchResult

    found = []
    for url in urls:
        domain = domain_of(url)
        html = synthetic_page(url)
        if html is not None:
            found.append(FetchResult(url=url, html=html, status="fetched", fetch_ms=120.0))
        elif domain in _BLOCKED and "robots" in _BLOCKED[domain]:
            found.append(FetchResult(url=url, html="", status="blocked", error="blocked by robots.txt"))
        else:
            reason = _BLOCKED.get(domain, "HTTP 404")
            found.append(FetchResult(url=url, html="", status="error", error=f"{reason} (synthetic fixture: no page for this URL)"))
    return found


def offline_fetcher(urls: list[str]):
    """A FetchBatch that never touches the network. Used for the historical demo: only pages already stored count."""
    from aeo.ranksim.loop import FetchResult

    return [FetchResult(url=url, html="", status="error", error="not fetched: offline demo, no stored copy") for url in urls]


def demo_ready(root) -> bool:
    """True when the synthetic demo project exists and has a trained engine."""
    from aeo.ranksim.project import ProjectError, list_pulls, load_project

    try:
        project = load_project(DEMO_SLUG, root=root)
    except (ProjectError, FileNotFoundError, OSError, ValueError):
        return False
    return any(item.has_engine for item in list_pulls(project))


def load_synthetic_demo(root, settings, database: str):
    """Create (or rebuild) the synthetic demo project and build its engine. No keys, no network."""
    from aeo.ranksim.project import ProjectError, create_project, import_serp_csv, load_project, pull

    try:
        project = load_project(DEMO_SLUG, root=root)
    except (ProjectError, FileNotFoundError, OSError, ValueError):
        project = None
    if project is None:
        searches = list(_TOPICS)
        project = create_project(DEMO_NAME, DEMO_SITE, searches, root=root, slug=DEMO_SLUG, discover_sitemap=False, max_site_pages=0)
    meta = load_meta(project)
    meta.dataset = DATASET_SYNTHETIC
    meta.dataset_label = SYNTHETIC_SOURCE
    if not meta.competitors:
        meta.competitors = list(DEMO_COMPETITORS)
    save_meta(project, meta)
    folder = Path(project.dir) / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "synthetic-serp.csv"
    path.write_text(synthetic_csv(), encoding="utf-8")
    import_serp_csv(project, path, None, SYNTHETIC_SOURCE, label_source="synthetic")
    report = pull(
        project,
        settings,
        database=database,
        skip_google=True,
        fetcher=synthetic_fetcher,
        engines=[],
        fresh_days=0,
        workers=2,
        delay_seconds=0.0,
    )
    return project, report


def load_historical_demo(root, settings, database: str):
    """The SmartCrew export with the HTML saved in the repository. Local only; never on a public deployment."""
    from aeo.ranksim.bootstrap import PR8_V3, _bundle_html
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.project import (
        ProjectError,
        _stored_index,
        _key,
        clean_searches,
        create_project,
        import_serp_csv,
        load_project,
        pull,
    )
    from aeo.ranksim.saved_html import load_saved_html
    from aeo.ranksim.store import open_session

    serp = Path("smartcrew/serp.csv")
    if not serp.is_file():
        raise WorkspaceError("smartcrew/serp.csv is not in this checkout, so the historical demo is not available.")
    slug = "smartcrew-historical"
    try:
        project = load_project(slug, root=root)
    except (ProjectError, FileNotFoundError, OSError, ValueError):
        searches = clean_searches(label.query for label in load_serp_file(serp))
        project = create_project("SmartCrew (historical export)", "www.smartcrewhq.com", searches, root=root, slug=slug, discover_sitemap=False, max_site_pages=0)
    meta = load_meta(project)
    meta.dataset = DATASET_HISTORICAL
    meta.dataset_label = HISTORICAL_SOURCE
    if not meta.competitors:
        meta.competitors = ["hcss.com", "getonecrew.com", "procore.com"]
    save_meta(project, meta)
    # Store the saved HTML that ships in the repository. Pages already stored are left alone.
    have = _stored_index(database)
    saved, _missing = load_saved_html()
    bundled = _bundle_html(PR8_V3)
    session = open_session(database)
    try:
        for url, html in {**saved, **bundled}.items():
            if html.strip() and not (have.get(_key(url)) or {}).get("has_html"):
                store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
    finally:
        session.close()
    import_serp_csv(project, serp, None, HISTORICAL_SOURCE)
    report = pull(
        project,
        settings,
        database=database,
        skip_google=True,
        fetcher=offline_fetcher,
        engines=[],
        fresh_days=36500,
        workers=2,
        delay_seconds=0.0,
    )
    return project, report


def _short_date(value) -> str:
    if not value:
        return ""
    text = str(value)
    return text[:10]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
