"""Round 2 research: does on-page content order two URLs of the SAME site? Measure, do not tune.

Idea under test. When two URLs of one site both rank for one search, site authority is the
same for both, so their order must come from page-level factors. If simple on-page relevance
features order same-site pairs well above 50% on held-out searches, a scorer learned from such
pairs is the closest observational stand-in for "which of two versions of my page ranks higher".

Data (design days only, sealed by scripts.claude.rsdata at DESIGN_MAX_DATE = 2026-11-17):
  SERP rows   rsdata.load_rows(CUTOFF): top-10 pulls and the Nov 16 deep list (absolute 1-30).
  Pages       t2_common.fetch_table(CUTOFF) (sealed rsdata.load_page_index + load_page_html),
              parsed with t2_changes.extract_page (the repo extractor aeo.crawl.extract).
  Nothing dated after 2026-11-17 is loaded; the script asserts it.

Pair universes (a pair is two distinct URLs ranked for the same search, both strictly ordered):
  U1 nov16   per search, the Nov 16 deep list (absolute positions 1-30, one pull) for the 100
             searches that have one; the Nov 16 consensus top 10 for the other searches.
  U2 cons    every top-10 consensus day through Nov 17 (rsdata.consensus_table, cons <= 10).
             One record per (search, URL pair): the order that holds on most days both URLs
             were in the consensus top 10. Pairs tied on every day, or split evenly, are dropped.
  URL identity folds scheme, www, a trailing slash and tracking parameters (srsltid, utm_*,
  gclid, fbclid). Same site = rsdata site_key (registrable domain, subdomains folded).

Reference page per URL: the usable fetch (main text >= 50 words) nearest to Nov 16, then timed
before date-only, then http before browser, then latest. One version per URL for U1 and U2.

Folds (fixed before any accuracy was computed): searches are normalized (lowercase tokens, plural
s stripped, '1' -> 'one', dirtwork(s) -> dirt work, heavyjob -> heavy job, heavybid -> heavy bid,
stopwords and 'best'/'top' dropped). Two searches are linked when they have the same token set,
share a contiguous 3-token core, or one token set (at least 2 tokens) is inside the other.
Linked components go to fold A or B by sha256 of the component's smallest search string.

Pre-stated features of a (search, page). Query terms = tokens of the search that are not
stopwords and have at least 3 characters, plural s stripped, unique.
  title_cov       share of query terms in the HTML <title>
  h1_cov          share of query terms in the first H1
  slug_cov        share of query terms in the URL path tokens (host excluded)
  title_phrase    the whole search (all tokens, in order) occurs in the title
  bm25_body       BM25 of the main text, k1 1.2, b 0.75, IDF over all reference pages
  bm25f           BM25F over title (w 3, b 0.5), H1 (w 3, b 0.5), h1-h3 headings (w 2, b 0.5),
                  body (w 1, b 0.75), k1 1.2, same IDF
  first100_cov    share of query terms in the first 100 words of the main text
  log_words       log(1 + main-text word count)                     (more first)
  url_depth       number of path segments                            (shallower first)
  is_home         path is empty or a single index/home/en segment    (homepage first)
  serp_title_all  the SERP title shown for this URL on this search (latest row on or before the
                  label day) contains every query term
  recency         -log(1 + age in days) of the newest visible date: meta/schema published or
                  updated, a <time> tag, or a full date written in the title or first 200 words
                  (dates after the fetch day are ignored). Missing when no date is found.
Extra descriptive features (Q5 only, never in a model): url_len (shorter first), inlinks (number
of other fetched pages of the same site that link to the URL), age_published (older first),
canonical_elsewhere.

Model (fixed): logistic regression on feature differences (higher-ranked minus lower-ranked),
each pair in both orientations, no intercept, L2 with C = 1.0, columns scaled by the training
fold's root mean square difference. Fit on same-site U1 pairs of one fold, scored on the other
fold, both ways. Nothing is tuned. An exact tie of a model's score counts as half a hit (first
run counted ties as wrong; that only changed the two-feature structure model, which ties often).

Added after the first run, labeled POST-HOC everywhere: an equal-weight vote of the pre-stated
feature directions (nothing fitted), per-search macro averages, single features by position zone,
and in Q4 a no-edit control (usable round 1 rows without a material change) with dates aged to the
same reference day for both versions (the first run aged each version to its own fetch day, which
moved the recency feature on every row without any edit).

Intervals: query bootstrap, 2000 draws, seed 20261119 (scripts/claude/metrics.py).

Outputs (no HTML): evals/claude/r2/samesite/results.json, tables.md, samesite_pairs.csv,
version_rows.csv.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aeo.crawl.extract import extract_html  # noqa: E402
from aeo.textutil import STOPWORDS, tokenize  # noqa: E402
from scripts.claude import metrics as M  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude.t2_changes import MIN_WORDS  # noqa: E402
from scripts.claude.t2_common import extracted, fetch_table  # noqa: E402

OUT = ROOT / "evals/claude/r2/samesite"
LINK_CACHE = Path("/tmp/r2-samesite-links-v1.pkl")
CUTOFF = rs.DESIGN_MAX_DATE
LABEL_DAY = "2026-11-16"
SEED = M.BOOT_SEED
DRAWS = M.BOOT_DRAWS
C_REG = 1.0
TIE = 1e-12

# name, direction (+1: larger value should rank higher, -1: smaller value should), group
FEATURES = [
    ("title_cov", 1, "text"),
    ("h1_cov", 1, "text"),
    ("slug_cov", 1, "text"),
    ("title_phrase", 1, "text"),
    ("bm25_body", 1, "text"),
    ("bm25f", 1, "text"),
    ("first100_cov", 1, "text"),
    ("log_words", 1, "size"),
    ("url_depth", -1, "structure"),
    ("is_home", 1, "structure"),
    ("serp_title_all", 1, "text"),
    ("recency", 1, "fresh"),
]
EXTRA = [("url_len", -1), ("inlinks", 1), ("age_published", 1), ("canonical_elsewhere", -1)]
FEAT_NAMES = [name for name, _d, _g in FEATURES]
DIRECTION = {name: d for name, d, _g in FEATURES} | dict(EXTRA)
GROUPS = {
    "full": FEAT_NAMES,
    "text": [name for name, _d, g in FEATURES if g == "text"],
    "structure": ["url_depth", "is_home"],
    "full_minus_structure": [name for name in FEAT_NAMES if name not in ("url_depth", "is_home")],
}
# Platforms where every URL is a different author. Pre-stated, used only for a robustness row.
UGC = {"reddit.com", "youtube.com", "linkedin.com", "facebook.com", "quora.com", "medium.com", "x.com",
       "twitter.com", "instagram.com", "tiktok.com"}
HOME_SEGS = {"index.html", "index.htm", "index.php", "home", "en", "en-us"}
TRACKING = re.compile(r"^(utm_.*|srsltid|gclid|fbclid|ref)$")

# ---------------------------------------------------------------------------------------------
# Text helpers


def stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token


def toks(text: str) -> list[str]:
    return [stem(token) for token in tokenize(text or "")]


def query_terms(query: str) -> list[str]:
    return list(dict.fromkeys(stem(t) for t in tokenize(query) if t not in STOPWORDS and len(t) >= 3))


def coverage(tokens, terms: list[str]) -> float:
    if not terms:
        return 0.0
    have = set(tokens)
    return sum(1 for term in terms if term in have) / len(terms)


def contains_seq(tokens: list[str], seq: list[str]) -> bool:
    if not seq or len(seq) > len(tokens):
        return False
    width = len(seq)
    return any(tokens[i : i + width] == seq for i in range(len(tokens) - width + 1))


# ---------------------------------------------------------------------------------------------
# Folds (search text only)

_SPLIT = {"dirtwork": "dirt work", "dirtworks": "dirt work", "heavyjob": "heavy job", "heavybid": "heavy bid", "1": "one"}
_GENERIC = {"best", "top"}


def _fold_tokens(query: str) -> list[str]:
    out: list[str] = []
    for token in tokenize(query):
        out.extend(_SPLIT.get(token, token).split())
    return [stem(t) for t in out if t not in STOPWORDS and t not in _GENERIC]


def assign_folds(queries: list[str]) -> tuple[dict[str, str], list[list[str]]]:
    qs = sorted(set(queries))
    parent = {q: q for q in qs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    toks_of = {q: _fold_tokens(q) for q in qs}
    tri = {q: {tuple(t[i : i + 3]) for i in range(len(t) - 2)} for q, t in toks_of.items()}
    for i, a in enumerate(qs):
        for b in qs[i + 1 :]:
            sa, sb = set(toks_of[a]), set(toks_of[b])
            small = sa if len(sa) <= len(sb) else sb
            if sa == sb or (tri[a] & tri[b]) or (len(small) >= 2 and (sa <= sb or sb <= sa)):
                union(a, b)
    comps: dict[str, list[str]] = defaultdict(list)
    for q in qs:
        comps[find(q)].append(q)
    fold = {}
    for root, members in comps.items():
        bit = int(hashlib.sha256(min(members).encode("utf-8")).hexdigest(), 16) % 2
        for q in members:
            fold[q] = "AB"[bit]
    return fold, sorted(comps.values(), key=lambda m: (-len(m), m[0]))


# ---------------------------------------------------------------------------------------------
# URLs


def ident(url: str) -> str:
    parts = urlsplit(str(url).strip())
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = parts.path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k.lower())]
    return host + path + ("?" + urlencode(query) if query else "")


def url_features(url: str) -> dict:
    parts = urlsplit(str(url))
    segs = [seg for seg in parts.path.split("/") if seg]
    home = len(segs) == 0 or (len(segs) == 1 and segs[0].lower() in HOME_SEGS)
    return {
        "url_depth": float(len(segs)),
        "is_home": float(home),
        "slug_tokens": toks(" ".join(segs)),
        "url_len": float(len(ident(url))),
    }


# ---------------------------------------------------------------------------------------------
# Dates

_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTH_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_RES = [
    ("ymd", re.compile(r"\b(20[0-2]\d)-(\d{1,2})-(\d{1,2})\b")),
    ("mdy", re.compile(_MONTH_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20[0-2]\d)\b", re.I)),
    ("dmy", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_RE + r",?\s+(20[0-2]\d)\b", re.I)),
    ("my", re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20[0-2]\d)\b", re.I)),
]


def _mk(year: int, month: int, day: int) -> pd.Timestamp | None:
    try:
        return pd.Timestamp(year=int(year), month=int(month), day=int(day))
    except (ValueError, TypeError):
        return None


def text_dates(text: str) -> list[pd.Timestamp]:
    found: list[pd.Timestamp] = []
    for kind, pattern in _DATE_RES:
        for m in pattern.finditer(text or ""):
            g = m.groups()
            if kind == "ymd":
                stamp = _mk(g[0], g[1], g[2])
            elif kind == "mdy":
                stamp = _mk(g[2], _MON[g[0][:3].lower()], g[1])
            elif kind == "dmy":
                stamp = _mk(g[2], _MON[g[1][:3].lower()], g[0])
            else:
                stamp = _mk(g[1], _MON[g[0][:3].lower()], 1)
            if stamp is not None:
                found.append(stamp)
    return found


def meta_date(text: str) -> pd.Timestamp | None:
    if not text:
        return None
    try:
        stamp = pd.to_datetime(str(text).strip(), utc=True, errors="coerce")
    except (ValueError, TypeError, OverflowError):
        return None
    if stamp is None or pd.isna(stamp):
        found = text_dates(str(text))
        return max(found) if found else None
    return stamp.tz_convert(None).normalize()


def date_features(page: dict, fetch_day: str) -> dict:
    limit = pd.Timestamp(fetch_day) + pd.Timedelta(days=1)
    floor = pd.Timestamp("2000-01-01")
    published = meta_date(page.get("published") or "")
    updated = meta_date(page.get("updated") or "")
    words = (page.get("main_text") or "").split()
    candidates = [d for d in (published, updated) if d is not None]
    candidates += text_dates(page.get("title") or "") + text_dates(" ".join(words[:200]))
    candidates = [d for d in candidates if floor <= d <= limit]
    newest = max(candidates) if candidates else None
    age = (pd.Timestamp(fetch_day) - newest).days if newest is not None else None
    pub_ok = published is not None and floor <= published <= limit
    return {
        "recency": -math.log1p(max(0, age)) if age is not None else np.nan,
        "age_published": float((pd.Timestamp(fetch_day) - published).days) if pub_ok else np.nan,
    }


# ---------------------------------------------------------------------------------------------
# BM25 / BM25F

BM25F_FIELDS = {"title": (3.0, 0.5), "h1": (3.0, 0.5), "head": (2.0, 0.5), "body": (1.0, 0.75)}
K1 = 1.2


class Corpus:
    def __init__(self, docs: dict[str, dict[str, list[str]]]):
        self.n = len(docs)
        self.df: Counter = Counter()
        lengths: dict[str, list[int]] = defaultdict(list)
        for fields in docs.values():
            seen: set[str] = set()
            for name, tokens in fields.items():
                lengths[name].append(len(tokens))
                seen.update(tokens)
            self.df.update(seen)
        self.avg = {name: max(1e-9, float(np.mean(values))) for name, values in lengths.items()}

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))

    def bm25(self, body: list[str], terms: list[str]) -> float:
        counts = Counter(body)
        norm = 1.0 - 0.75 + 0.75 * len(body) / self.avg["body"]
        score = 0.0
        for term in terms:
            tf = counts.get(term, 0)
            if tf:
                score += self.idf(term) * tf * (K1 + 1.0) / (tf + K1 * norm)
        return score

    def bm25f(self, fields: dict[str, list[str]], terms: list[str]) -> float:
        counts = {name: Counter(tokens) for name, tokens in fields.items()}
        score = 0.0
        for term in terms:
            pseudo = 0.0
            for name, (weight, b) in BM25F_FIELDS.items():
                tf = counts[name].get(term, 0)
                if tf:
                    pseudo += weight * tf / (1.0 - b + b * len(fields[name]) / self.avg[name])
            if pseudo:
                score += self.idf(term) * pseudo / (K1 + pseudo)
        return score


def page_fields(page: dict) -> dict[str, list[str]]:
    return {
        "title": toks(page.get("title") or ""),
        "h1": toks(page.get("h1") or ""),
        "head": toks(" ".join(page.get("headings") or [])),
        "body": toks(page.get("main_text") or ""),
    }


# ---------------------------------------------------------------------------------------------
# Pair universes


def _ranked_lists_u1(rows: pd.DataFrame, cons: pd.DataFrame) -> dict[str, dict]:
    """search -> {'source', 'items': {ident: (pos, url, site)}} for the Nov 16 lists."""
    out: dict[str, dict] = {}
    deep = rows[(rows["kind"] == "deep") & (rows["day"] == LABEL_DAY)]
    for query, group in deep.groupby("query", sort=True):
        items: dict[str, tuple] = {}
        for pos, url, site in sorted(zip(group["position"], group["url"], group["site"], strict=True)):
            key = ident(url)
            if key not in items:
                items[key] = (int(pos), url, site)
        out[query] = {"source": "deep", "items": items}
    top = cons[(cons["day"] == LABEL_DAY) & (cons["cons"] <= 10)]
    for query, group in top.groupby("query", sort=True):
        if query in out:
            continue
        items = {}
        for pos, url, site in sorted(zip(group["cons"], group["url"], group["site"], strict=True)):
            key = ident(url)
            if key not in items:
                items[key] = (int(pos), url, site)
        out[query] = {"source": "top10", "items": items}
    return out


def pairs_u1(lists: dict[str, dict]) -> tuple[pd.DataFrame, int]:
    records = []
    tied = 0
    for query, entry in lists.items():
        items = sorted(entry["items"].items(), key=lambda kv: (kv[1][0], kv[0]))
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (ka, (pa, ua, sa)), (kb, (pb, ub, sb)) = items[i], items[j]
                if pa == pb:
                    tied += 1
                    continue
                records.append({"query": query, "source": entry["source"], "hi": ka, "lo": kb, "pos_hi": pa, "pos_lo": pb,
                                "url_hi": ua, "url_lo": ub, "site_hi": sa, "site_lo": sb})
    frame = pd.DataFrame.from_records(records)
    frame["same_site"] = frame["site_hi"] == frame["site_lo"]
    frame["gap"] = frame["pos_lo"] - frame["pos_hi"]
    return frame, tied


def pairs_u2(cons: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    top = cons[cons["cons"] <= 10]
    votes: dict[tuple, list] = defaultdict(lambda: [0, 0, 0, [], [], None, None, None, None])  # a wins, b wins, days, pos_a, pos_b, url_a, url_b, site_a, site_b
    instances = 0
    tied_instances = 0
    for (query, day), group in top.groupby(["query", "day"], sort=True):
        items: dict[str, tuple] = {}
        for pos, url, site in sorted(zip(group["cons"], group["url"], group["site"], strict=True)):
            key = ident(url)
            if key not in items:
                items[key] = (int(pos), url, site)
        keys = sorted(items)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                pa, ua, sa = items[a]
                pb, ub, sb = items[b]
                if pa == pb:
                    tied_instances += 1
                    continue
                instances += 1
                rec = votes[(query, a, b)]
                rec[0 if pa < pb else 1] += 1
                rec[2] += 1
                rec[3].append(pa)
                rec[4].append(pb)
                rec[5], rec[6], rec[7], rec[8] = ua, ub, sa, sb
    records = []
    split = 0
    for (query, a, b), (wa, wb, days, posa, posb, ua, ub, sa, sb) in votes.items():
        if wa == wb:
            split += 1
            continue
        a_hi = wa > wb
        hi, lo = (a, b) if a_hi else (b, a)
        records.append({
            "query": query, "source": "cons", "hi": hi, "lo": lo,
            "pos_hi": float(np.mean(posa if a_hi else posb)), "pos_lo": float(np.mean(posb if a_hi else posa)),
            "url_hi": ua if a_hi else ub, "url_lo": ub if a_hi else ua, "site_hi": sa if a_hi else sb, "site_lo": sb if a_hi else sa,
            "days": days, "agree_days": max(wa, wb), "unanimous": min(wa, wb) == 0,
        })
    frame = pd.DataFrame.from_records(records)
    frame["same_site"] = frame["site_hi"] == frame["site_lo"]
    frame["gap"] = frame["pos_lo"] - frame["pos_hi"]
    return frame, {"pair_day_instances": instances, "tied_instances": tied_instances, "split_pairs_dropped": split}


# ---------------------------------------------------------------------------------------------
# Reference pages


def reference_pages(idents: set[str]) -> tuple[dict[str, dict], pd.DataFrame]:
    fetches = fetch_table(CUTOFF)
    assert fetches["day"].max() <= CUTOFF, "page fetch after the design cutoff"
    assert (fetches["fetched_at"] < (pd.Timestamp(CUTOFF) + pd.Timedelta(days=1)).strftime("%Y-%m-%d") + "T05:00:00").all()
    fetches = fetches.copy()
    fetches["ident"] = fetches["url"].map(ident)
    fetches = fetches[fetches["ident"].isin(idents)].copy()
    fetches["dist"] = (pd.to_datetime(fetches["day"]) - pd.Timestamp(LABEL_DAY)).abs().dt.days
    fetches["not_http"] = (fetches["fetch_method"] != "http").astype(int)
    fetches["neg_time"] = -pd.to_datetime(fetches["fetched_at"], utc=True, format="ISO8601").astype("int64")
    fetches = fetches.sort_values(["ident", "dist", "date_only", "not_http", "neg_time", "id"])
    candidates = {key: list(zip(g["id"].astype(int), g["url"], g["day"], g["fetch_method"], g["date_only"]))
                  for key, g in fetches.groupby("ident", sort=True)}
    pages: dict[str, dict] = {}
    rank = 0
    pending = set(candidates)
    while pending:
        batch = [(key, candidates[key][rank]) for key in sorted(pending) if rank < len(candidates[key])]
        if not batch:
            break
        parsed = extracted([(pid, url) for _key, (pid, url, _d, _m, _o) in batch])
        for key, (pid, url, day, method, date_only) in batch:
            page = parsed[pid]
            if int(page["word_count"]) >= MIN_WORDS:
                pages[key] = dict(page, page_id=pid, fetch_url=url, fetch_day=day, method=method, date_only=bool(date_only))
                pending.discard(key)
        pending = {key for key in pending if rank + 1 < len(candidates[key])}
        rank += 1
    return pages, fetches


def page_links(pages: dict[str, dict]) -> dict[str, set[str]]:
    cache: dict[int, list[str]] = {}
    if LINK_CACHE.is_file():
        cache = pickle.loads(LINK_CACHE.read_bytes())
    changed = False
    out = {}
    for key, page in pages.items():
        pid = int(page["page_id"])
        if pid not in cache:
            try:
                cache[pid] = extract_html(rs.load_page_html(pid), page["fetch_url"]).links
            except (ValueError, TypeError, AttributeError):
                cache[pid] = []
            changed = True
        out[key] = {ident(link) for link in cache[pid] if link.startswith(("http://", "https://"))}
    if changed:
        LINK_CACHE.write_bytes(pickle.dumps(cache, protocol=4))
    return out


# ---------------------------------------------------------------------------------------------
# Features


def page_static(page: dict, url: str, own_ident: str, corpus_fields: dict) -> dict:
    """Search-independent parts of a page's features."""
    fields = corpus_fields
    canon = page.get("canonical") or ""
    return {
        "fields": fields,
        "first100": fields["body"][:100],
        "log_words": math.log1p(int(page["word_count"])),
        "canonical_elsewhere": float(bool(canon) and ident(canon) != own_ident),
        **date_features(page, page["fetch_day"]),
    }


def features_for(query: str, url: str, static: dict, serp_title: str, corpus: Corpus, inlinks: float) -> dict:
    terms = query_terms(query)
    phrase = toks(query)
    fields = static["fields"]
    urlf = url_features(url)
    return {
        "title_cov": coverage(fields["title"], terms),
        "h1_cov": coverage(fields["h1"], terms),
        "slug_cov": coverage(urlf["slug_tokens"], terms),
        "title_phrase": float(contains_seq(fields["title"], phrase)),
        "bm25_body": corpus.bm25(fields["body"], terms),
        "bm25f": corpus.bm25f(fields, terms),
        "first100_cov": coverage(static["first100"], terms),
        "log_words": static["log_words"],
        "url_depth": urlf["url_depth"],
        "is_home": urlf["is_home"],
        "serp_title_all": float(bool(terms) and coverage(toks(serp_title or ""), terms) == 1.0),
        "recency": static["recency"],
        "url_len": urlf["url_len"],
        "inlinks": float(inlinks),
        "age_published": static["age_published"],
        "canonical_elsewhere": static["canonical_elsewhere"],
    }


def serp_titles(rows: pd.DataFrame, max_day: str) -> dict[tuple[str, str], str]:
    part = rows[rows["day"] <= max_day].copy()
    part["ident"] = part["url"].map(ident)
    part = part.sort_values(["day", "fetched_at", "position"])
    latest = part.groupby(["query", "ident"]).tail(1)
    return {(q, k): (t or "") for q, k, t in zip(latest["query"], latest["ident"], latest["title"], strict=True)}


def attach(frame: pd.DataFrame, feats: dict[tuple[str, str], dict]) -> pd.DataFrame:
    names = FEAT_NAMES + [name for name, _d in EXTRA]
    hi = np.array([[feats[(q, k)][n] for n in names] for q, k in zip(frame["query"], frame["hi"], strict=True)], dtype=float)
    lo = np.array([[feats[(q, k)][n] for n in names] for q, k in zip(frame["query"], frame["lo"], strict=True)], dtype=float)
    out = frame.copy()
    for index, name in enumerate(names):
        out[f"hi_{name}"] = hi[:, index]
        out[f"lo_{name}"] = lo[:, index]
        out[f"d_{name}"] = hi[:, index] - lo[:, index]  # NaN when either side is missing
    return out


# ---------------------------------------------------------------------------------------------
# Scoring helpers


def rate(frame: pd.DataFrame, col: str) -> dict:
    result = M.bootstrap_rate(frame, col)
    if result.get("n"):
        result["hits"] = float(frame[col].sum())
    return result


def boot_two(first: pd.DataFrame, second: pd.DataFrame, col: str) -> dict:
    """Rate in ``first`` minus rate in ``second`` with a joint query bootstrap (same draws and seed)."""
    queries = sorted(set(first["query"]) | set(second["query"]))
    pos = {q: i for i, q in enumerate(queries)}
    sums = np.zeros((len(queries), 4))
    for j, frame in enumerate((first, second)):
        grouped = frame.groupby("query")[col].agg(["sum", "size"])
        for q, (s, n) in grouped.iterrows():
            sums[pos[q], 2 * j] = s
            sums[pos[q], 2 * j + 1] = n
    rng = np.random.default_rng(SEED)
    index = rng.integers(0, len(queries), size=(DRAWS, len(queries)))
    drawn = sums[index].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        stats = drawn[:, 0] / drawn[:, 1] - drawn[:, 2] / drawn[:, 3]
    stats = stats[np.isfinite(stats)]
    point = sums[:, 0].sum() / sums[:, 1].sum() - sums[:, 2].sum() / sums[:, 3].sum()
    return {"diff": float(point), "lo": float(np.percentile(stats, 2.5)), "hi": float(np.percentile(stats, 97.5))}


def single_feature(frame: pd.DataFrame, name: str) -> dict:
    diff = frame[f"d_{name}"] * DIRECTION[name]
    missing = diff.isna()
    tie = (~missing) & (diff.abs() <= TIE)
    decided = frame[(~missing) & (~tie)].assign(_win=(diff[(~missing) & (~tie)] > 0).astype(float))
    half = frame.assign(_half=np.where(missing | tie, 0.5, (diff > 0).astype(float)))
    return {
        "pairs": int(len(frame)),
        "missing": int(missing.sum()),
        "ties": int(tie.sum()),
        "decided": int(len(decided)),
        "decided_rate": rate(decided, "_win"),
        "all_pairs_ties_half": rate(half, "_half"),
    }


def fit_model(train: pd.DataFrame, feats: list[str]) -> dict:
    from sklearn.linear_model import LogisticRegression

    diffs = np.nan_to_num(train[[f"d_{f}" for f in feats]].to_numpy(dtype=float), nan=0.0)
    scale = np.sqrt(np.mean(diffs**2, axis=0))
    scale[scale == 0] = 1.0
    x = np.vstack([diffs / scale, -diffs / scale])
    y = np.concatenate([np.ones(len(diffs)), np.zeros(len(diffs))])
    clf = LogisticRegression(C=C_REG, fit_intercept=False, max_iter=5000)
    clf.fit(x, y)
    coef = clf.coef_[0]
    return {"feats": list(feats), "scale": scale.tolist(), "coef": coef.tolist(), "per_unit": (coef / scale).tolist(),
            "train_pairs": int(len(train))}


def margin(model: dict, frame: pd.DataFrame) -> np.ndarray:
    diffs = np.nan_to_num(frame[[f"d_{f}" for f in model["feats"]]].to_numpy(dtype=float), nan=0.0)
    return diffs @ np.asarray(model["per_unit"])


def page_score(model: dict, values: dict) -> float:
    total = 0.0
    for feat, weight in zip(model["feats"], model["per_unit"], strict=True):
        value = values.get(feat, 0.0)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        total += weight * float(value)
    return total


def credit(values: np.ndarray) -> np.ndarray:
    """1 when the higher-ranked page scores higher, 0 when lower, 0.5 on an exact tie."""
    values = np.asarray(values, dtype=float)
    return np.where(np.abs(values) <= TIE, 0.5, (values > 0).astype(float))


def cross_fold(train_frame: pd.DataFrame, test_frame: pd.DataFrame, feats: list[str]) -> tuple[pd.Series, dict]:
    """Fit on fold A score fold B and the reverse. Returns correctness (ties 0.5) aligned to test_frame."""
    correct = pd.Series(np.nan, index=test_frame.index)
    models = {}
    for test_fold, train_fold in (("B", "A"), ("A", "B")):
        model = fit_model(train_frame[train_frame["fold"] == train_fold], feats)
        models[f"fit_{train_fold}_score_{test_fold}"] = model
        part = test_frame[test_frame["fold"] == test_fold]
        if len(part):
            correct.loc[part.index] = credit(margin(model, part))
    return correct, models


def sign_vote(frame: pd.DataFrame, feats: list[str]) -> np.ndarray:
    """POST-HOC diagnostic: sum over features of sign(pre-stated direction * difference). Nothing fitted."""
    total = np.zeros(len(frame))
    for feat in feats:
        diff = np.nan_to_num(frame[f"d_{feat}"].to_numpy(dtype=float) * DIRECTION[feat], nan=0.0)
        total += np.sign(np.where(np.abs(diff) <= TIE, 0.0, diff))
    return total


def macro(frame: pd.DataFrame, col: str) -> dict:
    """Mean over searches of each search's pair accuracy, query bootstrap (same draws and seed)."""
    per = frame.groupby("query")[col].mean().to_numpy(dtype=float)
    if not len(per):
        return {"rate": None, "lo": None, "hi": None, "queries": 0}
    rng = np.random.default_rng(SEED)
    index = rng.integers(0, len(per), size=(DRAWS, len(per)))
    stats = per[index].mean(axis=1)
    return {"rate": float(per.mean()), "lo": float(np.percentile(stats, 2.5)), "hi": float(np.percentile(stats, 97.5)),
            "queries": int(len(per))}


def gap_bucket(gap: float) -> str:
    if gap <= 2:
        return "1-2"
    if gap <= 5:
        return "3-5"
    if gap <= 10:
        return "6-10"
    return "11+"


def zone(row_hi: float, row_lo: float) -> str:
    if row_lo <= 10:
        return "both top10"
    if row_hi <= 10:
        return "one top10"
    return "both 11-30"


# ---------------------------------------------------------------------------------------------
# Q1 counts


def count_block(frame: pd.DataFrame, usable: pd.Series, all_queries: int) -> dict:
    same = frame[frame["same_site"]]
    cross = frame[~frame["same_site"]]
    su = same[usable.loc[same.index]]
    cu = cross[usable.loc[cross.index]]
    per_search = same.groupby("query").size().reindex(sorted(set(frame["query"])), fill_value=0)
    sites_all = same["site_hi"].value_counts()
    sites_use = su["site_hi"].value_counts()
    return {
        "searches_with_lists": int(all_queries),
        "pairs_total": int(len(frame)),
        "same_site_pairs": int(len(same)),
        "same_site_per_search_mean": float(per_search.mean()),
        "same_site_per_search_median": float(per_search.median()),
        "same_site_per_search_max": int(per_search.max()),
        "searches_with_same_site_pair": int((per_search > 0).sum()),
        "same_site_sites": int(same["site_hi"].nunique()),
        "same_site_top5": [[site, int(n)] for site, n in sites_all.head(5).items()],
        "same_site_top5_share": float(sites_all.head(5).sum() / max(1, len(same))),
        "same_site_usable_pairs": int(len(su)),
        "same_site_usable_searches": int(su["query"].nunique()),
        "same_site_usable_sites": int(su["site_hi"].nunique()),
        "same_site_usable_top5": [[site, int(n)] for site, n in sites_use.head(5).items()],
        "same_site_usable_top5_share": float(sites_use.head(5).sum() / max(1, len(su))),
        "cross_site_pairs": int(len(cross)),
        "cross_site_usable_pairs": int(len(cu)),
        "cross_site_usable_searches": int(cu["query"].nunique()),
        "cross_site_usable_sites": int(len(set(cu["site_hi"]) | set(cu["site_lo"]))),
    }


# ---------------------------------------------------------------------------------------------


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = rs.load_rows(CUTOFF)
    max_day = str(rows["day"].max())
    assert max_day <= CUTOFF, f"loaded SERP day {max_day} is after the design cutoff {CUTOFF}"
    cons = rs.consensus_table(rows)
    assert str(cons["day"].max()) <= CUTOFF
    groups = rs.query_groups(rows)
    fold, comps = assign_folds(list(rows["query"].unique()))
    results: dict = {
        "label": "OBSERVED positions (REAL, Serper); model scores PREDICTED",
        "design_cutoff": CUTOFF, "max_serp_day_loaded": max_day, "label_day_u1": LABEL_DAY,
        "folds": {
            "searches": dict(Counter(fold.values())),
            "components": len(comps),
            "largest_components": [[len(c), c[0]] for c in comps[:6]],
        },
    }

    lists = _ranked_lists_u1(rows, cons)
    u1, u1_tied = pairs_u1(lists)
    u2, u2_info = pairs_u2(cons)
    for frame in (u1, u2):
        frame["fold"] = frame["query"].map(fold)
        frame["group"] = frame["query"].map(lambda q: groups.get(q, "other"))

    needed = set(u1["hi"]) | set(u1["lo"]) | set(u2["hi"]) | set(u2["lo"])
    pages, fetches = reference_pages(needed)
    max_fetch_day = max(page["fetch_day"] for page in pages.values())
    assert max_fetch_day <= CUTOFF, "reference page after the design cutoff"
    results["max_page_fetch_day_loaded"] = max_fetch_day
    results["pages"] = {
        "urls_in_pairs": len(needed),
        "urls_with_any_fetch": int(fetches["ident"].nunique()),
        "urls_with_usable_page": len(pages),
        "fetch_day": dict(Counter(p["fetch_day"] for p in pages.values())),
        "method": dict(Counter(f"{p['method']}{':date-only' if p['date_only'] else ''}" for p in pages.values())),
    }

    # Duplicate content inside a pair (same title and main text): dropped from analysis, counted.
    fingerprint = {k: hashlib.sha1(((p.get("title") or "") + "\n" + (p.get("main_text") or "")).encode()).hexdigest()
                   for k, p in pages.items()}

    corpus_fields = {key: page_fields(page) for key, page in pages.items()}
    corpus = Corpus(corpus_fields)
    links = page_links(pages)
    inlink_count: Counter = Counter()
    site_of = {key: rs.site_key("https://" + key) for key in pages}
    for src, targets in links.items():
        for target in targets:
            if target != src and target in pages and site_of.get(target) == site_of[src]:
                inlink_count[target] += 1
    url_of: dict[str, str] = {}
    for frame in (u1, u2):
        for side in ("hi", "lo"):
            for key, url in zip(frame[side], frame[f"url_{side}"], strict=True):
                url_of.setdefault(key, url)
    statics = {key: page_static(page, url_of.get(key, page["fetch_url"]), key, corpus_fields[key]) for key, page in pages.items()}

    def build_feats(frame: pd.DataFrame, title_day: str) -> dict:
        titles = serp_titles(rows, title_day)
        feats = {}
        for side in ("hi", "lo"):
            for q, key in zip(frame["query"], frame[side], strict=True):
                if (q, key) in feats or key not in pages:
                    continue
                feats[(q, key)] = features_for(q, url_of[key], statics[key], titles.get((q, key), ""), corpus, inlink_count.get(key, 0))
        return feats

    analysed = {}
    for name, frame, title_day in (("u1", u1, LABEL_DAY), ("u2", u2, CUTOFF)):
        usable = frame["hi"].isin(pages) & frame["lo"].isin(pages)
        dup = usable & (frame["hi"].map(fingerprint) == frame["lo"].map(fingerprint))
        block = count_block(frame, usable & ~dup, frame["query"].nunique())
        block["same_site_duplicate_content_dropped"] = int((dup & frame["same_site"]).sum())
        block["cross_site_duplicate_content_dropped"] = int((dup & ~frame["same_site"]).sum())
        if name == "u1":
            block["tied_positions_dropped"] = u1_tied
            deep_rows = rows[(rows["kind"] == "deep") & (rows["day"] == LABEL_DAY)]
            block["deep_list_repeated_url_rows"] = int(deep_rows.assign(_i=deep_rows["url"].map(ident)).duplicated(["query", "_i"]).sum())
            block["deep_list_rows"] = int(len(deep_rows))
            block["by_source"] = {src: {"searches": int(g["query"].nunique()), "same_site": int(g["same_site"].sum()),
                                        "cross_site": int((~g["same_site"]).sum())} for src, g in frame.groupby("source")}
            same = frame[frame["same_site"]]
            block["same_site_by_zone"] = Counter(zone(h, l) for h, l in zip(same["pos_hi"], same["pos_lo"], strict=True))
        else:
            block.update(u2_info)
            same = frame[frame["same_site"]]
            block["same_site_unanimous"] = int(same["unanimous"].sum())
            block["same_site_multi_day"] = int((same["days"] >= 2).sum())
            block["searches_by_group"] = dict(Counter(groups.get(q, "other") for q in frame["query"].unique()))
        results[f"q1_{name}"] = block
        frame = frame[usable & ~dup].copy()
        analysed[name] = attach(frame, build_feats(frame, title_day))

    u1a, u2a = analysed["u1"], analysed["u2"]
    same1 = u1a[u1a["same_site"]].copy()
    cross1 = u1a[~u1a["same_site"]].copy()
    same2 = u2a[u2a["same_site"]].copy()
    cross2 = u2a[~u2a["same_site"]].copy()
    results["fold_pairs"] = {
        "u1_same_site": same1.groupby("fold").size().to_dict(),
        "u1_same_site_searches": same1.groupby("fold")["query"].nunique().to_dict(),
        "u2_same_site": same2.groupby("fold").size().to_dict(),
    }

    # ---- Q2 single features
    q2 = {}
    for name in FEAT_NAMES + [n for n, _d in EXTRA]:
        entry = {"direction": "larger first" if DIRECTION[name] > 0 else "smaller first"}
        for label, s_frame, c_frame in (("u1", same1, cross1), ("u2", same2, cross2)):
            s = single_feature(s_frame, name)
            c = single_feature(c_frame, name)
            sd = s_frame[s_frame[f"d_{name}"].notna() & (s_frame[f"d_{name}"].abs() > TIE)]
            cd = c_frame[c_frame[f"d_{name}"].notna() & (c_frame[f"d_{name}"].abs() > TIE)]
            sd = sd.assign(_w=((sd[f"d_{name}"] * DIRECTION[name]) > 0).astype(float))
            cd = cd.assign(_w=((cd[f"d_{name}"] * DIRECTION[name]) > 0).astype(float))
            entry[label] = {"same_site": s, "cross_site": c,
                            "same_minus_cross_decided": boot_two(sd, cd, "_w") if len(sd) and len(cd) else None}
        q2[name] = entry
    results["q2_single_features"] = q2

    # ---- Q3 model, fold-safe
    q3: dict = {}
    corr, models = cross_fold(same1, same1, GROUPS["full"])
    same1["model_correct"] = corr
    q3["models"] = models
    q3["u1_same_site"] = rate(same1, "model_correct")
    q3["u1_same_site_by_fold"] = {f: rate(same1[same1["fold"] == f], "model_correct") for f in ("A", "B")}
    ccorr, _ = cross_fold(same1, cross1, GROUPS["full"])
    cross1["model_correct"] = ccorr
    q3["u1_cross_site_same_model"] = rate(cross1, "model_correct")
    q3["u1_same_minus_cross"] = boot_two(same1, cross1, "model_correct")
    s2corr, _ = cross_fold(same1, same2, GROUPS["full"])
    same2["model_correct"] = s2corr
    c2corr, _ = cross_fold(same1, cross2, GROUPS["full"])
    cross2["model_correct"] = c2corr
    q3["u2_same_site"] = rate(same2, "model_correct")
    q3["u2_same_site_unanimous_multi_day"] = rate(same2[same2["unanimous"] & (same2["days"] >= 2)], "model_correct")
    q3["u2_cross_site_same_model"] = rate(cross2, "model_correct")
    # A model fit on cross-site pairs, for reference (same fold rule).
    xcorr, xmodels = cross_fold(cross1, same1, GROUPS["full"])
    q3["u1_same_site_scored_by_cross_site_model"] = rate(same1.assign(_x=xcorr), "_x")
    q3["cross_site_model_weights"] = {k: dict(zip(v["feats"], np.round(v["coef"], 4).tolist(), strict=True)) for k, v in xmodels.items()}

    # Baselines on the same pairs: shorter URL first, more query terms in the title first.
    for base, feat in (("shorter_url_first", "url_len"), ("more_title_terms_first", "title_cov")):
        for label, frame in (("u1_same", same1), ("u1_cross", cross1), ("u2_same", same2)):
            q3[f"baseline_{base}_{label}"] = single_feature(frame, feat)
    q3["model_minus_title_baseline_u1_same_ties_half"] = M.bootstrap_difference(
        same1.assign(_b=np.where(same1["d_title_cov"].abs() <= TIE, 0.5, (same1["d_title_cov"] > 0).astype(float))),
        "model_correct", "_b")
    q3["model_minus_url_baseline_u1_same_ties_half"] = M.bootstrap_difference(
        same1.assign(_b=np.where(same1["d_url_len"].abs() <= TIE, 0.5, (same1["d_url_len"] < 0).astype(float))),
        "model_correct", "_b")

    # Leave one site out, fold-safe: train on the other fold without site S, score S in this fold.
    loso = pd.Series(np.nan, index=same1.index)
    for test_fold, train_fold in (("B", "A"), ("A", "B")):
        train = same1[same1["fold"] == train_fold]
        test = same1[same1["fold"] == test_fold]
        for site, part in test.groupby("site_hi"):
            model = fit_model(train[train["site_hi"] != site], GROUPS["full"])
            loso.loc[part.index] = credit(margin(model, part))
    same1["loso_correct"] = loso
    q3["u1_same_site_leave_one_site_out"] = rate(same1, "loso_correct")
    top5 = [site for site, _n in same1["site_hi"].value_counts().head(5).items()]
    q3["top5_sites"] = top5
    q3["per_top_site"] = {site: {"model": rate(same1[same1["site_hi"] == site], "model_correct"),
                                 "loso": rate(same1[same1["site_hi"] == site], "loso_correct")} for site in top5}
    q3["per_top_site_single_decided"] = {
        site: {feat: single_feature(same1[same1["site_hi"] == site], feat)["decided_rate"]
               for feat in ("title_cov", "h1_cov", "slug_cov", "bm25f", "url_depth")} for site in top5}
    q3["fold_site_pairs"] = {f: [[s, int(n)] for s, n in g["site_hi"].value_counts().head(5).items()] + [["all", int(len(g))]]
                             for f, g in same1.groupby("fold")}
    rest = same1[~same1["site_hi"].isin(top5)]
    q3["u1_same_site_without_top5_sites"] = {"model": rate(rest, "model_correct"), "loso": rate(rest, "loso_correct")}
    q3["u1_same_site_without_ugc"] = rate(same1[~same1["site_hi"].isin(UGC)], "model_correct")
    # Search-group and position-zone breakdowns.
    same1["zone"] = [zone(h, l) for h, l in zip(same1["pos_hi"], same1["pos_lo"], strict=True)]
    same1["gap_bucket"] = same1["gap"].map(gap_bucket)
    cross1["gap_bucket"] = cross1["gap"].map(gap_bucket)
    q3["u1_same_site_by_zone"] = {z: rate(g, "model_correct") for z, g in same1.groupby("zone")}
    q3["u1_same_site_by_group"] = {z: rate(g, "model_correct") for z, g in same1.groupby("group")}
    q3["u1_cross_site_by_zone"] = {z: rate(g, "model_correct") for z, g in cross1.assign(
        zone=[zone(h, l) for h, l in zip(cross1["pos_hi"], cross1["pos_lo"], strict=True)]).groupby("zone")}
    q3["u1_by_gap"] = {b: {"same": rate(same1[same1["gap_bucket"] == b], "model_correct"),
                           "cross": rate(cross1[cross1["gap_bucket"] == b], "model_correct")} for b in ("1-2", "3-5", "6-10", "11+")}
    share = same1["gap_bucket"].value_counts(normalize=True)
    cross_by = cross1.groupby("gap_bucket")["model_correct"].mean()
    q3["u1_cross_site_gap_matched_point"] = float(sum(share[b] * cross_by.get(b, np.nan) for b in share.index))
    # Pair-level overlap across folds (the same URL pair ranked for searches in both folds).
    pair_id = same1.apply(lambda r: tuple(sorted((r["hi"], r["lo"]))), axis=1)
    folds_of_pair = defaultdict(set)
    for pid, f in zip(pair_id, same1["fold"], strict=True):
        folds_of_pair[pid].add(f)
    same1["pair_in_both_folds"] = [len(folds_of_pair[pid]) > 1 for pid in pair_id]
    q3["u1_same_site_pairs_in_both_folds"] = int(same1["pair_in_both_folds"].sum())
    q3["u1_same_site_pairs_only_one_fold"] = rate(same1[~same1["pair_in_both_folds"]], "model_correct")
    q3["u1_same_site_distinct_url_pairs"] = int(pair_id.nunique())
    q3["u1_same_site_distinct_urls"] = int(len(set(same1["hi"]) | set(same1["lo"])))
    q3["model_ties"] = int((same1["model_correct"] == 0.5).sum())
    # Per-search macro averages (each search counts once, whatever its number of same-site pairs).
    same1["_title_half"] = credit(same1["d_title_cov"].to_numpy())
    same1["_bm25f_half"] = credit(same1["d_bm25f"].to_numpy())
    same1["_url_half"] = credit(-same1["d_url_len"].to_numpy())
    q3["u1_same_site_macro"] = {"model": macro(same1, "model_correct"), "loso": macro(same1, "loso_correct"),
                                "more_title_terms_first_ties_half": macro(same1, "_title_half"),
                                "bm25f_ties_half": macro(same1, "_bm25f_half"),
                                "shorter_url_first_ties_half": macro(same1, "_url_half")}
    # Pre-stated single features by position zone (decided pairs only).
    q3["u1_same_site_single_by_zone"] = {
        feat: {z: single_feature(g, feat)["decided_rate"] for z, g in same1.groupby("zone")} for feat in ("bm25f", "title_cov", "url_depth")
    }
    # POST-HOC diagnostic, added after the fitted model came out at chance: an equal-weight vote of the
    # pre-stated feature directions. Nothing is fitted, so it is the same on every fold.
    same1["vote_full"] = credit(sign_vote(same1, GROUPS["full"]))
    same1["vote_text"] = credit(sign_vote(same1, GROUPS["text"]))
    cross1["vote_full"] = credit(sign_vote(cross1, GROUPS["full"]))
    cross1["vote_text"] = credit(sign_vote(cross1, GROUPS["text"]))
    q3["post_hoc_equal_weight_vote"] = {
        "note": "POST-HOC. Added after the pre-stated fitted model scored at chance. Not a pre-stated test.",
        "u1_same_full": rate(same1, "vote_full"), "u1_same_text": rate(same1, "vote_text"),
        "u1_cross_full": rate(cross1, "vote_full"), "u1_cross_text": rate(cross1, "vote_text"),
        "u1_same_text_without_top5_sites": rate(same1[~same1["site_hi"].isin(top5)], "vote_text"),
        "u1_same_text_macro": macro(same1, "vote_text"),
        "u1_same_text_by_zone": {z: rate(g, "vote_text") for z, g in same1.groupby("zone")},
        "u1_same_text_without_youtube": rate(same1[same1["site_hi"] != "youtube.com"], "vote_text"),
        "u2_same_full": rate(same2.assign(_v=credit(sign_vote(same2, GROUPS["full"]))), "_v"),
        "u2_same_text": rate(same2.assign(_v=credit(sign_vote(same2, GROUPS["text"]))), "_v"),
    }
    q3["u1_same_site_zone_sites"] = {z: [[s, int(n)] for s, n in g["site_hi"].value_counts().head(4).items()]
                                     for z, g in same1.groupby("zone")}
    results["q3_model"] = q3

    # ---- Q5 structure versus text
    q5: dict = {}
    group_models = {}
    for gname, feats in GROUPS.items():
        c, group_models[gname] = cross_fold(same1, same1, feats)
        same1[f"m_{gname}"] = c
        q5[f"model_{gname}"] = rate(same1, f"m_{gname}")
    tied = same1[(same1["d_url_depth"].abs() <= TIE) & (same1["hi_is_home"] == 0) & (same1["lo_is_home"] == 0)]
    informative = same1.drop(tied.index)
    q5["structure_tied_pairs"] = int(len(tied))
    q5["structure_informative_pairs"] = int(len(informative))
    q5["on_structure_tied"] = {g: rate(tied, f"m_{g}") for g in ("full", "text")}
    q5["on_structure_tied_title_cov"] = single_feature(tied, "title_cov")
    q5["on_structure_tied_bm25f"] = single_feature(tied, "bm25f")
    q5["on_structure_informative"] = {g: rate(informative, f"m_{g}") for g in ("full", "text", "structure")}
    q5["on_structure_tied_post_hoc_text_vote"] = rate(tied, "vote_text")
    q5["on_structure_informative_post_hoc_text_vote"] = rate(informative, "vote_text")
    q5["on_structure_informative_bm25f"] = single_feature(informative, "bm25f")
    # Head to head on structure-informative pairs: structure call (homepage first, else shallower first)
    # against the BM25F call. When they disagree, which one matches the real order?
    struct_call = np.where(informative["hi_is_home"] != informative["lo_is_home"],
                           np.sign(informative["hi_is_home"] - informative["lo_is_home"]),
                           np.sign(-informative["d_url_depth"]))
    text_call = np.sign(informative["d_bm25f"].to_numpy())
    disagree = (struct_call != 0) & (text_call != 0) & (struct_call != text_call)
    agree_mask = (struct_call != 0) & (struct_call == text_call)
    q5["structure_vs_bm25f_on_informative"] = {
        "pairs": int(len(informative)),
        "agree": int(agree_mask.sum()), "agree_and_right": int(((struct_call > 0) & agree_mask).sum()),
        "disagree": int(disagree.sum()), "disagree_structure_right": int(((struct_call > 0) & disagree).sum()),
        "disagree_bm25f_right": int(((text_call > 0) & disagree).sum()),
    }
    home = same1[(same1["hi_is_home"] + same1["lo_is_home"]) == 1]
    q5["pairs_with_one_homepage"] = int(len(home))
    q5["homepage_ranks_higher"] = rate(home.assign(_h=home["hi_is_home"].astype(float)), "_h")
    q5["text_minus_structure"] = M.bootstrap_difference(same1, "m_text", "m_structure")
    q5["full_minus_text"] = M.bootstrap_difference(same1, "m_full", "m_text")
    q5["full_minus_structure"] = M.bootstrap_difference(same1, "m_full", "m_structure")
    q5["extra_descriptive"] = {name: single_feature(same1, name) for name, _d in EXTRA}
    q5["inlink_coverage"] = {"pages_with_any_same_site_inlink": int(sum(1 for v in inlink_count.values() if v > 0)),
                             "pages": len(pages)}
    q5["canonical_elsewhere_pairs"] = int(((same1["hi_canonical_elsewhere"] + same1["lo_canonical_elsewhere"]) > 0).sum())
    results["q5_structure_vs_text"] = q5

    # ---- Q4 version sensitivity on the round 1 material changes
    results["q4_version_sensitivity"], version_rows = version_sensitivity(rows, models, group_models["text"], fold, corpus)

    # ---- Save
    keep = ["query", "fold", "group", "source", "hi", "lo", "pos_hi", "pos_lo", "gap", "site_hi", "zone"] + \
           [f"d_{n}" for n in FEAT_NAMES + [x for x, _d in EXTRA]] + ["model_correct", "loso_correct"] + [f"m_{g}" for g in GROUPS]
    same1[keep].to_csv(OUT / "samesite_pairs.csv", index=False)
    version_rows.to_csv(OUT / "version_rows.csv", index=False)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    (OUT / "tables.md").write_text(tables_md(results) + "\n", encoding="utf-8")
    print(tables_md(results))
    return 0


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    if isinstance(value, Counter):
        return dict(value)
    return str(value)


def version_sensitivity(rows: pd.DataFrame, models: dict, text_models: dict, fold: dict, corpus: Corpus) -> tuple[dict, pd.DataFrame]:
    """Score the before and after version of each round 1 page change with the fold-safe model.

    The search's fold picks the model fitted on the other fold. URL features and the SERP title are
    held at their day-A values and every date is aged to the same reference day (fetch day B), so
    only the page content can move the score. Material rows are the round 1 test rows; usable rows
    without a material change are a no-edit control (same pipeline, same models).
    """
    from scripts.claude import t2_dataset

    data = t2_dataset.build_rows(max_date=CUTOFF)
    assert str(data["day_b"].max()) <= CUTOFF and str(data["serp_day_b"].max()) <= CUTOFF
    use = data[data["usable"].astype(bool)].copy()
    pairs = t2_dataset.build(CUTOFF)["pairs"][["key", "method", "day_a", "day_b", "id_a", "id_b"]]
    use = use.merge(pairs, on=["key", "method", "day_a", "day_b"], how="left", validate="many_to_one")
    assert use["id_a"].notna().all()
    ids = sorted(set(zip(use["id_a"].astype(int), use["url"])) | set(zip(use["id_b"].astype(int), use["url"])))
    parsed = extracted(ids)
    titles_by_day: dict[str, dict] = {}
    records = []
    for row in use.itertuples(index=False):
        test_fold = fold.get(row.query)
        train_fold = "B" if test_fold == "A" else "A"
        model = models[f"fit_{train_fold}_score_{test_fold}"]
        text_model = text_models[f"fit_{train_fold}_score_{test_fold}"]
        if row.serp_day_a not in titles_by_day:
            titles_by_day[row.serp_day_a] = serp_titles(rows, row.serp_day_a)
        serp_title = titles_by_day[row.serp_day_a].get((row.query, ident(row.url)), "")
        values = {}
        for side, pid in (("before", int(row.id_a)), ("after", int(row.id_b))):
            page = dict(parsed[pid], fetch_day=row.day_b)
            fields = page_fields(page)
            static = page_static(page, row.url, ident(row.url), fields)
            values[side] = features_for(row.query, row.url, static, serp_title, corpus, 0)
        delta = page_score(model, values["after"]) - page_score(model, values["before"])
        changed = [f for f in FEAT_NAMES if not _same(values["before"][f], values["after"][f])]
        records.append({
            "query": row.query, "fold": test_fold, "url": row.url, "site": row.site, "day_a": row.day_a, "day_b": row.day_b,
            "material": bool(row.material), "material_reasons": row.material_reasons, "edit_tokens": int(row.edit_tokens),
            "pos_a": int(row.pos_a), "pos_b": int(row.pos_b), "move": row.move,
            "score_before": page_score(model, values["before"]), "score_after": page_score(model, values["after"]),
            "delta": delta, "unchanged": abs(delta) <= TIE, "features_changed": "+".join(changed),
            "text_delta": page_score(text_model, values["after"]) - page_score(text_model, values["before"]),
            "vote_text_delta": _vote_delta(values["before"], values["after"], GROUPS["text"]),
            "vote_full_delta": _vote_delta(values["before"], values["after"], GROUPS["full"]),
            **{f"{f}_before": values["before"][f] for f in FEAT_NAMES},
            **{f"{f}_after": values["after"][f] for f in FEAT_NAMES},
        })
    frame = pd.DataFrame.from_records(records)
    summary = {"note": "Model scores PREDICTED. URL features and SERP title held at day A; dates aged to fetch day B for both versions."}
    for label, part in (("material", frame[frame["material"]]), ("control_no_material_change", frame[~frame["material"]])):
        moved = part[part["move"] != "none"]
        nonzero = moved[~moved["unchanged"]]
        agree = ((nonzero["delta"] > 0) & (nonzero["move"] == "up")) | ((nonzero["delta"] < 0) & (nonzero["move"] == "down"))
        nz_text = moved[moved["text_delta"].abs() > TIE]
        head_changed = part["features_changed"].map(lambda v: any(f in v.split("+") for f in ("title_cov", "h1_cov", "title_phrase")))
        summary[label] = {
            "rows": int(len(part)),
            "fetch_pairs": int(part[["url", "day_a", "day_b"]].drop_duplicates().shape[0]),
            "urls": int(part["url"].nunique()),
            "moved_rows": int(len(moved)),
            "moved_up": int((moved["move"] == "up").sum()),
            "moved_down": int((moved["move"] == "down").sum()),
            "score_unchanged_all_rows": int(part["unchanged"].sum()),
            "score_unchanged_moved_rows": int(moved["unchanged"].sum()),
            "moved_rows_nonzero_delta": int(len(nonzero)),
            "sign_agrees_with_move": int(agree.sum()),
            "text_model_nonzero_moved": int(len(nz_text)),
            "text_model_sign_agrees": int((((nz_text["text_delta"] > 0) & (nz_text["move"] == "up")) |
                                           ((nz_text["text_delta"] < 0) & (nz_text["move"] == "down"))).sum()),
            "rows_where_a_title_or_h1_feature_changed": int(head_changed.sum()),
            "post_hoc_text_vote_nonzero_moved": int((moved["vote_text_delta"] != 0).sum()),
            "post_hoc_text_vote_sign_agrees": int((((moved["vote_text_delta"] > 0) & (moved["move"] == "up")) |
                                                   ((moved["vote_text_delta"] < 0) & (moved["move"] == "down"))).sum()),
            "post_hoc_full_vote_nonzero_moved": int((moved["vote_full_delta"] != 0).sum()),
            "post_hoc_full_vote_sign_agrees": int((((moved["vote_full_delta"] > 0) & (moved["move"] == "up")) |
                                                   ((moved["vote_full_delta"] < 0) & (moved["move"] == "down"))).sum()),
            "feature_change_counts": dict(Counter(f for v in part["features_changed"] for f in v.split("+") if f)),
            "median_abs_delta": float(part["delta"].abs().median()) if len(part) else None,
        }
    return summary, frame


def _vote_delta(before: dict, after: dict, feats: list[str]) -> int:
    """POST-HOC: equal-weight vote of pre-stated directions on after minus before (missing counts 0)."""
    total = 0
    for feat in feats:
        a, b = before[feat], after[feat]
        if a is None or b is None or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
            continue
        diff = (float(b) - float(a)) * DIRECTION[feat]
        total += 0 if abs(diff) <= TIE else (1 if diff > 0 else -1)
    return total


def _same(a, b) -> bool:
    a_nan = a is None or (isinstance(a, float) and math.isnan(a))
    b_nan = b is None or (isinstance(b, float) and math.isnan(b))
    if a_nan or b_nan:
        return a_nan and b_nan
    return abs(float(a) - float(b)) <= TIE


# ---------------------------------------------------------------------------------------------
# Tables


def _r(entry: dict) -> str:
    if not entry or not entry.get("n"):
        return "0/0"
    hits = float(entry["hits"])
    shown = f"{hits:.0f}" if hits.is_integer() else f"{hits:.1f}"
    return f"{shown}/{entry['n']} ({entry['rate']:.3f}) [{entry['lo']:.3f}, {entry['hi']:.3f}]"


def _d(entry: dict | None) -> str:
    if not entry or entry.get("diff") is None:
        return "n/a"
    return f"{entry['diff']:+.3f} [{entry['lo']:+.3f}, {entry['hi']:+.3f}]"


def tables_md(res: dict) -> str:
    lines = ["# Same-site pairs: generated tables (scripts/claude/r2_samesite_pairs.py)", "",
             f"Design cutoff {res['design_cutoff']}. Max SERP day loaded {res['max_serp_day_loaded']}. "
             f"Max page fetch day loaded {res['max_page_fetch_day_loaded']}. Positions REAL, model scores PREDICTED.", "",
             f"Folds: {res['folds']['searches']} searches, {res['folds']['components']} components. "
             f"Same-site U1 pairs per fold {res['fold_pairs']['u1_same_site']}, searches {res['fold_pairs']['u1_same_site_searches']}. "
             f"Largest search groups: {res['folds']['largest_components']}.", "",
             "## Q1 counts", "", "| count | U1 Nov 16 lists | U2 top-10 consensus days |", "| --- | ---: | ---: |"]
    a, b = res["q1_u1"], res["q1_u2"]
    for key in ("searches_with_lists", "pairs_total", "same_site_pairs", "same_site_per_search_mean", "same_site_per_search_median",
                "same_site_per_search_max", "searches_with_same_site_pair", "same_site_sites", "same_site_top5_share",
                "same_site_usable_pairs", "same_site_usable_searches", "same_site_usable_sites", "same_site_usable_top5_share",
                "same_site_duplicate_content_dropped", "cross_site_pairs", "cross_site_usable_pairs", "cross_site_usable_searches",
                "cross_site_usable_sites"):
        fa, fb = a.get(key), b.get(key)
        fmt = (lambda v: f"{v:.2f}" if isinstance(v, float) else str(v))
        lines.append(f"| {key} | {fmt(fa)} | {fmt(fb)} |")
    lines += ["", f"U1 top-5 sites (all same-site pairs): {a['same_site_top5']}", f"U1 top-5 sites (usable): {a['same_site_usable_top5']}",
              f"U2 top-5 sites (usable): {b['same_site_usable_top5']}", f"U1 by source: {a['by_source']}",
              f"U1 same-site pairs by zone: {dict(a['same_site_by_zone'])}",
              f"U1 deep list: {a['deep_list_repeated_url_rows']} of {a['deep_list_rows']} rows repeat a URL already listed higher in the same list",
              f"U2: pair-day instances {b['pair_day_instances']}, tied instances {b['tied_instances']}, split pairs dropped "
              f"{b['split_pairs_dropped']}, same-site unanimous {b['same_site_unanimous']}, same-site seen on 2+ days {b['same_site_multi_day']}",
              f"Pages: {res['pages']}", ""]
    lines += ["## Q2 single features (share where the page with the better feature value ranks higher; ties and missing left out)", "",
              "| feature | direction | U1 same-site decided | ties / missing | U1 cross-site decided | ties / missing | same minus cross |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, entry in res["q2_single_features"].items():
        s, c = entry["u1"]["same_site"], entry["u1"]["cross_site"]
        lines.append(f"| {name} | {entry['direction']} | {_r(s['decided_rate'])} | {s['ties']} / {s['missing']} | {_r(c['decided_rate'])} | "
                     f"{c['ties']} / {c['missing']} | {_d(entry['u1']['same_minus_cross_decided'])} |")
    lines += ["", "U2 (consensus days) same-site and cross-site, decided pairs:", "", "| feature | U2 same-site | U2 cross-site |", "| --- | --- | --- |"]
    for name, entry in res["q2_single_features"].items():
        lines.append(f"| {name} | {_r(entry['u2']['same_site']['decided_rate'])} | {_r(entry['u2']['cross_site']['decided_rate'])} |")
    q3 = res["q3_model"]
    lines += ["", "## Q3 model (fit on one fold, scored on the other)", "", "| set | accuracy |", "| --- | --- |"]
    for key in ("u1_same_site", "u1_cross_site_same_model", "u2_same_site", "u2_same_site_unanimous_multi_day", "u2_cross_site_same_model",
                "u1_same_site_leave_one_site_out", "u1_same_site_without_ugc", "u1_same_site_scored_by_cross_site_model",
                "u1_same_site_pairs_only_one_fold"):
        lines.append(f"| {key} | {_r(q3[key])} |")
    for f in ("A", "B"):
        lines.append(f"| u1_same_site fold {f} | {_r(q3['u1_same_site_by_fold'][f])} |")
    lines.append(f"| without top-5 sites, model | {_r(q3['u1_same_site_without_top5_sites']['model'])} |")
    lines.append(f"| without top-5 sites, leave-one-site-out | {_r(q3['u1_same_site_without_top5_sites']['loso'])} |")
    lines += ["", f"Same minus cross (model): {_d(q3['u1_same_minus_cross'])}. Gap-matched cross-site point: {q3['u1_cross_site_gap_matched_point']:.3f}. "
              f"Same-site pairs present in both folds: {q3['u1_same_site_pairs_in_both_folds']}. Model ties (scored 0.5): {q3['model_ties']}. "
              f"Distinct same-site URL pairs: {q3['u1_same_site_distinct_url_pairs']} on {q3['u1_same_site_distinct_urls']} URLs.", "",
              "Per-search macro averages (each search weighs the same), U1 same-site:", "", "| scorer | macro accuracy |", "| --- | --- |"]
    for key, e in q3["u1_same_site_macro"].items():
        lines.append(f"| {key} | {e['rate']:.3f} [{e['lo']:.3f}, {e['hi']:.3f}] ({e['queries']} searches) |")
    lines += ["", "Single features by position zone, U1 same-site, decided pairs:", "", "| feature | zone | decided |", "| --- | --- | --- |"]
    for feat, zones in q3["u1_same_site_single_by_zone"].items():
        for z, e in zones.items():
            lines.append(f"| {feat} | {z} | {_r(e)} |")
    ph = q3["post_hoc_equal_weight_vote"]
    lines += ["", f"POST-HOC equal-weight vote of pre-stated directions ({ph['note']}):", "", "| vote | accuracy (ties half) |", "| --- | --- |"]
    for key in ("u1_same_full", "u1_same_text", "u1_cross_full", "u1_cross_text", "u1_same_text_without_top5_sites"):
        lines.append(f"| {key} | {_r(ph[key])} |")
    for key in ("u1_same_text_without_youtube", "u2_same_full", "u2_same_text"):
        lines.append(f"| {key} | {_r(ph[key])} |")
    for z, e in ph["u1_same_text_by_zone"].items():
        lines.append(f"| u1_same_text, zone {z} | {_r(e)} |")
    m = ph["u1_same_text_macro"]
    lines.append(f"| u1_same_text macro | {m['rate']:.3f} [{m['lo']:.3f}, {m['hi']:.3f}] |")
    lines += ["", f"Same-site sites by zone: {q3['u1_same_site_zone_sites']}"]
    lines += ["", "| cross-site zone | model |", "| --- | --- |"]
    for key, entry in q3["u1_cross_site_by_zone"].items():
        lines.append(f"| {key} | {_r(entry)} |")
    lines += ["",
              "| baseline | set | decided | ties | all pairs, ties as half |", "| --- | --- | --- | --- | --- |"]
    for base in ("shorter_url_first", "more_title_terms_first"):
        for label in ("u1_same", "u1_cross", "u2_same"):
            e = q3[f"baseline_{base}_{label}"]
            lines.append(f"| {base} | {label} | {_r(e['decided_rate'])} | {e['ties']} | {_r(e['all_pairs_ties_half'])} |")
    lines += ["", f"Model minus title baseline (ties half), U1 same-site: {_d(q3['model_minus_title_baseline_u1_same_ties_half'])}",
              f"Model minus shorter-URL baseline (ties half), U1 same-site: {_d(q3['model_minus_url_baseline_u1_same_ties_half'])}", "",
              "| top site | model | leave-one-site-out |", "| --- | --- | --- |"]
    for site, entry in q3["per_top_site"].items():
        lines.append(f"| {site} | {_r(entry['model'])} | {_r(entry['loso'])} |")
    lines += ["", "Single features on the top sites, decided pairs:", "", "| site | feature | decided |", "| --- | --- | --- |"]
    for site, feats in q3["per_top_site_single_decided"].items():
        for feat, e in feats.items():
            lines.append(f"| {site} | {feat} | {_r(e)} |")
    lines += ["", f"Same-site pairs by fold and site: {q3['fold_site_pairs']}"]
    lines += ["", "| zone or group | model |", "| --- | --- |"]
    for key, entry in list(q3["u1_same_site_by_zone"].items()) + list(q3["u1_same_site_by_group"].items()):
        lines.append(f"| {key} | {_r(entry)} |")
    lines += ["", "| position gap | same-site model | cross-site model |", "| --- | --- | --- |"]
    for bucket, entry in q3["u1_by_gap"].items():
        lines.append(f"| {bucket} | {_r(entry['same'])} | {_r(entry['cross'])} |")
    lines += ["", "Model weights (standardized coefficients):", ""]
    for key, model in q3["models"].items():
        lines.append(f"- {key} (train pairs {model['train_pairs']}): " + ", ".join(f"{f} {c:+.3f}" for f, c in zip(model["feats"], model["coef"], strict=True)))
    q4 = res["q4_version_sensitivity"]
    lines += ["", "## Q4 version sensitivity", "", "```", json.dumps(q4, indent=1, default=_json_default), "```"]
    q5 = res["q5_structure_vs_text"]
    lines += ["", "## Q5 structure versus text (same-site U1, fold-safe)", "", "| model or slice | accuracy |", "| --- | --- |"]
    for g in GROUPS:
        lines.append(f"| model {g} | {_r(q5[f'model_{g}'])} |")
    for g, e in q5["on_structure_tied"].items():
        lines.append(f"| structure-tied pairs ({q5['structure_tied_pairs']}), model {g} | {_r(e)} |")
    for g, e in q5["on_structure_informative"].items():
        lines.append(f"| structure-informative pairs ({q5['structure_informative_pairs']}), model {g} | {_r(e)} |")
    lines.append(f"| structure-tied, title_cov decided | {_r(q5['on_structure_tied_title_cov']['decided_rate'])} |")
    lines.append(f"| structure-tied, bm25f decided | {_r(q5['on_structure_tied_bm25f']['decided_rate'])} |")
    lines.append(f"| homepage ranks higher ({q5['pairs_with_one_homepage']} pairs with one homepage) | {_r(q5['homepage_ranks_higher'])} |")
    lines.append(f"| structure-tied, POST-HOC text vote | {_r(q5['on_structure_tied_post_hoc_text_vote'])} |")
    lines.append(f"| structure-informative, POST-HOC text vote | {_r(q5['on_structure_informative_post_hoc_text_vote'])} |")
    lines.append(f"| structure-informative, bm25f decided | {_r(q5['on_structure_informative_bm25f']['decided_rate'])} |")
    lines += ["", f"Structure call versus BM25F call on structure-informative pairs: {q5['structure_vs_bm25f_on_informative']}"]
    lines += ["", f"Text minus structure: {_d(q5['text_minus_structure'])}. Full minus text: {_d(q5['full_minus_text'])}. "
              f"Full minus structure: {_d(q5['full_minus_structure'])}.", "", "| extra feature | decided | ties / missing |", "| --- | --- | --- |"]
    for name, e in q5["extra_descriptive"].items():
        lines.append(f"| {name} | {_r(e['decided_rate'])} | {e['ties']} / {e['missing']} |")
    lines.append(f"\nInlink coverage: {q5['inlink_coverage']}. Pairs with a canonical pointing elsewhere: {q5['canonical_elsewhere_pairs']}.")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
