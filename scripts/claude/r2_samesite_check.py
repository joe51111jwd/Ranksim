"""Frozen same-site content check on held-out days (round 2, Target 2 diagnostic). Nothing is fitted.

  python scripts/claude/r2_samesite_check.py --prereg evals/preregistered/claude-r2-samesite --days 2026-11-18,2026-11-19
  python scripts/claude/r2_samesite_check.py --design --days 2026-11-16,2026-11-17

What it measures. When two URLs of the same site are both in one search's consensus top 10, does a
fixed text-match rule put the higher one first? The design study (scripts/claude/r2_samesite_pairs.py,
evals/claude/r2/samesite/notes.md) found about 0.60 within a site against about 0.52 across sites.
This script repeats that check on other days with the scorers imported unchanged from that study.

This is a diagnostic, not proof that an edit will move rank. It compares two different pages that
already rank, at one point in time. It does not show that changing a page changes its rank. Every
scorer output is PREDICTED. Positions are REAL Google positions from Serper (consensus of the day's pulls).

Pairs. For each listed day and each search in groups o30 and a70 (rsdata.query_groups, computed from
SERP rows dated 2026-11-17 or earlier so held-out sources cannot change membership), the consensus top
10 of that day (rsdata.consensus_table, cons <= 10). Pairs are built by r2_samesite_pairs.pairs_u2
itself: URL identity folds scheme, www, a trailing slash and tracking parameters, two URLs at the same
consensus position are skipped, and across several days one record per (search, URL pair) is labeled
by the order that holds on most days (exact splits are dropped). Same site means the same `site`
column (registrable domain). Cross-site pairs of the same lists are the reference. A per-day table
uses each day alone.

Pages. For each URL, the usable stored fetch (main text of at least 50 words, extracted with
t2_changes.extract_page, the extractor and rule the design study used) nearest in calendar days to the
pair's day, never after the last listed day. Fetches come from t2_common.fetch_table(last listed day),
which reads rsdata.load_page_index and keeps http and browser fetches of at least 500 bytes with status
200 or unrecorded; HTML comes from rsdata.load_page_html. Ties on distance go, as in the design study,
to a timed fetch before a date-only one, http before browser, then the latest fetch, then the lowest id.
The pair's day is the day itself in the per-day table. For a pooled record it is the latest listed day
on which the pair was strictly ordered in the consensus top 10. A pair without a usable page for both
URLs is counted and left out. A pair whose two pages have the same title and main text is counted and
left out, as in the design study. No shared /tmp cache is read or written; pages are extracted in
memory for this run only.

Scorers, both imported from r2_samesite_pairs and used unchanged:
  bm25f       Corpus.bm25f over title (w 3, b 0.5), H1 (w 3, b 0.5), h1-h3 headings (w 2, b 0.5) and
              main text (w 1, b 0.75), k1 1.2. Higher first.
  text vote   sign_vote over the design study's "text" feature group with its pre-stated directions,
              minus serp_title_all: title_cov, h1_cov, slug_cov, title_phrase, bm25_body, bm25f,
              first100_cov (7 features). The design study's eight-feature vote also held
              serp_title_all. It is left out here because the SERP title is Google's own output.
  Not used in either scorer: the SERP title, internal links, URL depth, homepage, word count, dates.
  A tie (equal BM25F, or a vote sum of 0) counts as half a hit.

Corpus statistics for BM25 and BM25F (document frequency and average field lengths) are computed the
way the design study computed them: one reference page per URL, over every URL that appears in any
pair (same-site or cross-site) of this run. Here the reference page is the usable fetch nearest the
last listed day, so only pages available up to the last listed day enter.

History split. A pair counts as seen when both URLs appeared in one stored list for that search dated
on or before min(2026-11-17, the day before the first listed day): any single top-10 pull, or the deep
list (absolute positions 1-30) of a day. New pairs are the only fully independent ones. A stricter
split, new on every search, also checks the lists of all other searches.

Intervals are 95% query bootstraps, 2000 draws, seed 20261119 (scripts/claude/metrics.py, through the
design study's rate and boot_two helpers).

Held-out mode (no --design): every listed day must be 2026-11-18 or later. The script calls
r2_score.require_frozen_dir on the --prereg folder before it loads anything, refuses to overwrite an
existing output unless --force, and writes to evals/claude/heldout-r2/claude-r2-samesite/.
Design mode (--design): every listed day must be 2026-11-17 or earlier. Output goes to
evals/claude/r2/samesite-check-design/, with a comparison against the design study's numbers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import r2_samesite_pairs as ss  # noqa: E402
from scripts.claude import r2_score  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude import score_heldout as sh  # noqa: E402
from scripts.claude import t2_common as tc  # noqa: E402
from scripts.claude.t2_changes import MIN_WORDS, extract_page  # noqa: E402

HELDOUT_OUT = ROOT / "evals/claude/heldout-r2/claude-r2-samesite"
DESIGN_OUT = ROOT / "evals/claude/r2/samesite-check-design"
FIRST_HELDOUT_DAY = sh.FIRST_HELDOUT_DAY
GROUPS_USED = ("o30", "a70")
TOP = 10

BM25F = "bm25f"
VOTE_FEATURES = [name for name in ss.GROUPS["text"] if name != "serp_title_all"]
EXCLUDED_FEATURES = ["serp_title_all", "inlinks", "url_depth", "is_home", "log_words", "recency", "url_len",
                     "age_published", "canonical_elsewhere"]
SCORERS = {"bm25f": "bm25f_credit", "text_vote": "vote_credit"}

PREDICTION = {
    "text": "BM25F and the text vote each order held-out same-site top-10 pairs between 0.55 and 0.65 and above their cross-site rate",
    "low": 0.55,
    "high": 0.65,
}

# The design study's numbers on its consensus universe U2 (every top-10 consensus day through
# 2026-11-17, every search group, pages nearest 2026-11-16, corpus over U1 and U2 pages).
DESIGN_STUDY_U2 = {
    "same_site_pairs": 159,
    "same_site_searches": 54,
    "text_vote_8_with_serp_title": {"hits": 96.5, "n": 159, "rate": 0.6069},
    "bm25f": {"hits": 96.0, "n": 159, "rate": 0.6038},
    "bm25f_cross_site": {"hits": 1926.0, "n": 3741, "rate": 0.5148},
}

PAIR_COLUMNS = ["query", "source", "hi", "lo", "pos_hi", "pos_lo", "url_hi", "url_lo", "site_hi", "site_lo",
                "days", "agree_days", "unanimous", "same_site", "gap"]

assert len(VOTE_FEATURES) == 7 and BM25F in VOTE_FEATURES
assert not set(VOTE_FEATURES) & set(EXCLUDED_FEATURES)

# ---------------------------------------------------------------------------------------------
# Days and modes


def parse_days(text: str) -> list[str]:
    days = sorted({part.strip() for part in str(text).split(",") if part.strip()})
    if not days:
        raise SystemExit("--days needs at least one YYYY-MM-DD day.")
    for day in days:
        try:
            ok = pd.Timestamp(day).strftime("%Y-%m-%d") == day
        except ValueError:
            ok = False
        if not ok:
            raise SystemExit(f"Not a YYYY-MM-DD day: {day!r}")
    return days


def check_days(days: list[str], design: bool) -> None:
    if design:
        late = [day for day in days if day > rs.DESIGN_MAX_DATE]
        if late:
            raise SystemExit(f"--design only runs on days through {rs.DESIGN_MAX_DATE}. Sealed days given: {late}")
    else:
        early = [day for day in days if day < FIRST_HELDOUT_DAY]
        if early:
            raise SystemExit(f"Held-out mode only scores days from {FIRST_HELDOUT_DAY}. Design days given: {early}. Use --design.")


def seen_cutoff(days: list[str]) -> str:
    """Last day whose lists count as already seen: min(design cutoff, day before the first listed day)."""
    before = (pd.Timestamp(days[0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return min(rs.DESIGN_MAX_DATE, before)


def out_stem(days: list[str]) -> str:
    return "samesite-" + "_".join(days)


# ---------------------------------------------------------------------------------------------
# Pairs


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(columns=PAIR_COLUMNS)


def build_pairs(cons: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """r2_samesite_pairs.pairs_u2 on the given consensus rows, with an empty result instead of an error."""
    if cons[cons["cons"] <= TOP].empty:
        return _empty_pairs(), {"pair_day_instances": 0, "tied_instances": 0, "split_pairs_dropped": 0}
    try:
        return ss.pairs_u2(cons)
    except KeyError:  # pairs_u2 cannot label columns of an empty record list
        return _empty_pairs(), {"pair_day_instances": 0, "tied_instances": 0, "split_pairs_dropped": 0}


def _unordered(query: str, a: str, b: str) -> tuple[str, str, str]:
    return (query, a, b) if a <= b else (query, b, a)


def pair_tables(cons: pd.DataFrame, days: list[str], groups: dict[str, str], groups_used) -> tuple[pd.DataFrame, dict, dict]:
    """Pooled records over the listed days and one table per day. Adds ref_day to every record."""
    part = cons[cons["day"].isin(days)]
    if groups_used is not None:
        part = part[part["query"].map(lambda q: groups.get(q, "other")).isin(groups_used)]
    per_day: dict[str, pd.DataFrame] = {}
    info: dict = {"per_day": {}}
    last_seen: dict[tuple[str, str, str], str] = {}
    for day in days:
        frame, day_info = build_pairs(part[part["day"] == day])
        frame = frame.copy()
        frame["ref_day"] = day
        per_day[day] = frame
        info["per_day"][day] = day_info
        for q, hi, lo in zip(frame["query"], frame["hi"], frame["lo"], strict=True):
            key = _unordered(q, hi, lo)
            last_seen[key] = max(day, last_seen.get(key, day))
    pooled, pooled_info = build_pairs(part)
    pooled = pooled.copy()
    pooled["ref_day"] = [last_seen[_unordered(q, hi, lo)] for q, hi, lo in zip(pooled["query"], pooled["hi"], pooled["lo"], strict=True)]
    info["pooled"] = pooled_info
    info["searches_with_lists"] = {day: int(part[(part["day"] == day) & (part["cons"] <= TOP)]["query"].nunique()) for day in days}
    return pooled, per_day, info


def seen_sets(rows: pd.DataFrame, cutoff: str) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]]]:
    """URL pairs that shared one stored list dated <= cutoff: per search, and on any search.

    A list is one top-10 pull (query, source, fetched_at) or one day's deep list (query, day, source).
    """
    part = rows[rows["day"] <= cutoff]
    by_search: set[tuple[str, str, str]] = set()
    any_search: set[tuple[str, str]] = set()
    lists = []
    top = part[part["kind"] == "top10"]
    for (query, _s, _f), group in top.groupby(["query", "source", "fetched_at"], sort=True):
        lists.append((query, group["url"]))
    deep = part[part["kind"] == "deep"]
    for (query, _d, _s), group in deep.groupby(["query", "day", "source"], sort=True):
        lists.append((query, group["url"]))
    for query, urls in lists:
        keys = sorted({ss.ident(url) for url in urls})
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                by_search.add((query, keys[i], keys[j]))
                any_search.add((keys[i], keys[j]))
    return by_search, any_search


def mark_history(frame: pd.DataFrame, by_search: set, any_search: set) -> pd.DataFrame:
    out = frame.copy()
    keys = [_unordered(q, hi, lo) for q, hi, lo in zip(out["query"], out["hi"], out["lo"], strict=True)]
    out["seen_this_search"] = [key in by_search for key in keys]
    out["seen_any_search"] = [(key[1], key[2]) in any_search for key in keys]
    return out


# ---------------------------------------------------------------------------------------------
# Pages


class PageStore:
    """extract_page() output per page id, kept in memory for one run. No HTML is written anywhere."""

    def __init__(self, load_html=None, extract=None):
        self.load_html = load_html or rs.load_page_html
        self.extract = extract or extract_page
        self.cache: dict[int, dict] = {}

    def get(self, page_id: int, url: str) -> dict:
        pid = int(page_id)
        if pid not in self.cache:
            self.cache[pid] = self.extract(self.load_html(pid), url)
        return self.cache[pid]


def page_candidates(fetches: pd.DataFrame, idents: set[str]) -> dict[str, pd.DataFrame]:
    part = fetches.copy()
    part["ident"] = part["url"].map(ss.ident)
    part = part[part["ident"].isin(idents)].copy()
    part["not_http"] = (part["fetch_method"] != "http").astype(int)
    part["neg_time"] = -pd.to_datetime(part["fetched_at"], utc=True, format="ISO8601").astype("int64")
    return {key: group for key, group in part.groupby("ident", sort=True)}


def pick_page(candidates: pd.DataFrame | None, ref_day: str, store: PageStore) -> dict | None:
    """The usable fetch nearest ref_day (design study order of tie-breaks). None when no fetch is usable."""
    if candidates is None or candidates.empty:
        return None
    order = candidates.assign(dist=(pd.to_datetime(candidates["day"]) - pd.Timestamp(ref_day)).abs().dt.days)
    order = order.sort_values(["dist", "date_only", "not_http", "neg_time", "id"])
    for pid, url, day, method, date_only in zip(order["id"], order["url"], order["day"], order["fetch_method"], order["date_only"], strict=True):
        page = store.get(int(pid), url)
        if int(page.get("word_count") or 0) >= MIN_WORDS:
            return dict(page, page_id=int(pid), fetch_url=url, fetch_day=day, method=method, date_only=bool(date_only))
    return None


def fingerprint(page: dict) -> str:
    """Same duplicate-content key as the design study: title plus main text."""
    return hashlib.sha1(((page.get("title") or "") + "\n" + (page.get("main_text") or "")).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------
# Features and scoring


def analyse(frame: pd.DataFrame, pages_at: dict, corpus: ss.Corpus, url_of: dict, titles: dict) -> tuple[pd.DataFrame, dict]:
    """Drop pairs without two usable pages or with duplicate content, attach features, score. Returns (scored, counts)."""
    if frame.empty:
        usable = np.zeros(0, dtype=bool)
        dup = np.zeros(0, dtype=bool)
    else:
        hk = list(zip(frame["hi"], frame["ref_day"], strict=True))
        lk = list(zip(frame["lo"], frame["ref_day"], strict=True))
        usable = np.array([h in pages_at and l in pages_at for h, l in zip(hk, lk, strict=True)], dtype=bool)
        dup = np.array([u and fingerprint(pages_at[h]) == fingerprint(pages_at[l]) for u, h, l in zip(usable, hk, lk, strict=True)], dtype=bool)
    same = frame["same_site"].to_numpy(dtype=bool) if len(frame) else np.zeros(0, dtype=bool)
    counts = {
        "pairs_total": int(len(frame)),
        "same_site_pairs": int(same.sum()),
        "cross_site_pairs": int((~same).sum()),
        "same_site_without_usable_html": int((same & ~usable).sum()),
        "cross_site_without_usable_html": int((~same & ~usable).sum()),
        "same_site_duplicate_content_dropped": int((same & dup).sum()),
        "cross_site_duplicate_content_dropped": int((~same & dup).sum()),
    }
    keep = frame[usable & ~dup].copy()
    parts = []
    statics: dict[tuple[str, str], dict] = {}
    for day, part in keep.groupby("ref_day", sort=True):
        feats: dict[tuple[str, str], dict] = {}
        for side in ("hi", "lo"):
            for q, key in zip(part["query"], part[side], strict=True):
                if (q, key) in feats:
                    continue
                page = pages_at[(key, day)]
                if (key, day) not in statics:
                    statics[(key, day)] = ss.page_static(page, url_of[key], key, ss.page_fields(page))
                feats[(q, key)] = ss.features_for(q, url_of[key], statics[(key, day)], titles.get((q, key), ""), corpus, 0)
        parts.append(ss.attach(part, feats))
    scored = pd.concat(parts) if parts else keep
    if len(scored):
        scored["bm25f_credit"] = ss.credit(scored["d_bm25f"].to_numpy(dtype=float))
        scored["vote_sum"] = ss.sign_vote(scored, VOTE_FEATURES)
        scored["vote_credit"] = ss.credit(scored["vote_sum"].to_numpy(dtype=float))
        if titles:
            scored["vote8_credit"] = ss.credit(ss.sign_vote(scored, ss.GROUPS["text"]))
    else:
        for col in ("bm25f_credit", "vote_sum", "vote_credit") + (("vote8_credit",) if titles else ()):
            scored[col] = pd.Series(dtype=float)
    return scored.sort_index(), counts


def scorer_block(same: pd.DataFrame, cross: pd.DataFrame, col: str) -> dict:
    decided = same[same[col] != 0.5] if len(same) else same
    return {
        "same_site": ss.rate(same, col),
        "cross_site": ss.rate(cross, col),
        "same_minus_cross": ss.boot_two(same, cross, col) if len(same) and len(cross) else None,
        "same_site_ties": int((same[col] == 0.5).sum()) if len(same) else 0,
        "same_site_decided": ss.rate(decided, col),
    }


def subset_block(frame: pd.DataFrame, scorers: dict[str, str]) -> dict:
    same = frame[frame["same_site"].astype(bool)] if len(frame) else frame
    cross = frame[~frame["same_site"].astype(bool)] if len(frame) else frame
    return {
        "same_site_pairs": int(len(same)),
        "same_site_searches": int(same["query"].nunique()) if len(same) else 0,
        "same_site_sites": int(same["site_hi"].nunique()) if len(same) else 0,
        "cross_site_pairs": int(len(cross)),
        "cross_site_searches": int(cross["query"].nunique()) if len(cross) else 0,
        **{name: scorer_block(same, cross, col) for name, col in scorers.items()},
    }


def summarize(scored: pd.DataFrame, counts: dict, scorers: dict[str, str]) -> dict:
    same = scored[scored["same_site"].astype(bool)] if len(scored) else scored
    # Most pairs first, then site name, so a tie in counts always picks the same five sites.
    sites = same["site_hi"].value_counts().sort_index().sort_values(ascending=False, kind="stable") if len(same) else pd.Series(dtype=int)
    top5 = list(sites.head(5).index)
    rest = same[~same["site_hi"].isin(top5)] if len(same) else same
    block = subset_block(scored, scorers)
    out = {
        "counts": dict(counts, **{
            "same_site_usable_pairs": block["same_site_pairs"],
            "same_site_searches": block["same_site_searches"],
            "same_site_sites": block["same_site_sites"],
            "same_site_top5_sites": [[site, int(n)] for site, n in sites.head(5).items()],
            "same_site_top5_share": float(sites.head(5).sum() / len(same)) if len(same) else None,
            "same_site_distinct_url_pairs": int(len({tuple(sorted(p)) for p in zip(same["hi"], same["lo"], strict=True)})) if len(same) else 0,
            "cross_site_usable_pairs": block["cross_site_pairs"],
            "cross_site_searches": block["cross_site_searches"],
        }),
        "scorers": {},
        "by_history": {},
    }
    for name, col in scorers.items():
        out["scorers"][name] = dict(block[name], same_site_without_top5_sites=ss.rate(rest, col))
    if len(scored):
        seen = scored["seen_this_search"].astype(bool)
        seen_any = scored["seen_any_search"].astype(bool)
        splits = {"new_pair_this_search": scored[~seen], "seen_pair_this_search": scored[seen],
                  "new_pair_every_search": scored[~seen_any]}
    else:
        splits = {"new_pair_this_search": scored, "seen_pair_this_search": scored, "new_pair_every_search": scored}
    out["by_history"] = {name: subset_block(part, scorers) for name, part in splits.items()}
    return out


def prediction_check(pooled_summary: dict, mode: str) -> dict:
    low, high = PREDICTION["low"], PREDICTION["high"]
    parts = {}
    for name in SCORERS:
        entry = pooled_summary["scorers"][name]
        same = entry["same_site"].get("rate")
        cross = entry["cross_site"].get("rate")
        parts[f"{name}_between_{low}_and_{high}"] = {
            "observed_same_site": same, "same_site": entry["same_site"],
            "held": bool(same is not None and low <= same <= high),
        }
        parts[f"{name}_above_cross_site"] = {
            "observed_same_site": same, "observed_cross_site": cross, "same_minus_cross": entry["same_minus_cross"],
            "held": bool(same is not None and cross is not None and same > cross),
            "difference_interval_above_zero": bool(entry["same_minus_cross"] and entry["same_minus_cross"]["lo"] > 0),
        }
    return {
        "prediction": PREDICTION["text"],
        "label": "PREDICTED before the held-out days were opened. Observed values are scorer outputs (PREDICTED) on REAL orders.",
        "mode": mode if mode == "heldout" else "design dry run, not a test",
        "population": "pooled records (one per search and URL pair across the listed days), searches o30 and a70, "
                      "consensus top 10, pairs with usable HTML for both pages, duplicate content dropped",
        "rule": f"Point estimates, ties count half. 'between' means {low} <= same-site rate <= {high}. "
                "'above' means same-site rate > cross-site rate of the same scorer on the same lists. "
                "difference_interval_above_zero is reported and is not part of the prediction.",
        "parts": parts,
        "held": bool(all(part["held"] for part in parts.values())),
    }


# ---------------------------------------------------------------------------------------------
# Run


def run_check(rows: pd.DataFrame, cons: pd.DataFrame, fetches: pd.DataFrame, store: PageStore, days: list[str],
              groups: dict[str, str], *, groups_used=GROUPS_USED, cutoff: str | None = None,
              serp_title_day: str | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Score both fixed rules on one set of days. Returns (report blocks, pooled pairs, per-day pairs)."""
    last_day = days[-1]
    cutoff = cutoff or seen_cutoff(days)
    pooled, per_day, info = pair_tables(cons, days, groups, groups_used)
    by_search, any_search = seen_sets(rows, cutoff)
    pooled = mark_history(pooled, by_search, any_search)
    per_day = {day: mark_history(frame, by_search, any_search) for day, frame in per_day.items()}

    frames = [pooled] + list(per_day.values())
    idents = set()
    url_of: dict[str, str] = {}
    for frame in frames:
        for side in ("hi", "lo"):
            idents.update(frame[side])
            for key, url in zip(frame[side], frame[f"url_{side}"], strict=True):
                url_of.setdefault(key, url)
    candidates = page_candidates(fetches, idents)
    needs = {(key, day) for frame in frames for side in ("hi", "lo") for key, day in zip(frame[side], frame["ref_day"], strict=True)}
    needs |= {(key, last_day) for key in idents}
    pages_at: dict[tuple[str, str], dict] = {}
    for key, day in sorted(needs):
        page = pick_page(candidates.get(key), day, store)
        if page is not None:
            pages_at[(key, day)] = page
    corpus_pages = {key: pages_at[(key, last_day)] for key in sorted(idents) if (key, last_day) in pages_at}
    corpus = ss.Corpus({key: ss.page_fields(page) for key, page in corpus_pages.items()})
    titles = ss.serp_titles(rows, serp_title_day) if serp_title_day else {}
    scorers = dict(SCORERS, **({"text_vote_8_with_serp_title": "vote8_credit"} if titles else {}))

    scored_pooled, counts_pooled = analyse(pooled, pages_at, corpus, url_of, titles)
    report = {
        "days": days,
        "search_groups": list(groups_used) if groups_used is not None else "all",
        "seen_cutoff": cutoff,
        "pairs": info,
        "pages": {
            "urls_in_pairs": len(idents),
            "urls_with_any_fetch": len(candidates),
            "urls_with_usable_page": len(corpus_pages),
            "page_versions_used": len(pages_at),
            "max_fetch_day_used": max((p["fetch_day"] for p in pages_at.values()), default=None),
            "fetch_day_of_corpus_pages": dict(sorted(Counter(p["fetch_day"] for p in corpus_pages.values()).items())),
            "method_of_corpus_pages": dict(Counter(f"{p['method']}{':date-only' if p['date_only'] else ''}" for p in corpus_pages.values())),
        },
        "corpus": {"documents": corpus.n, "avg_field_tokens": {k: round(v, 3) for k, v in corpus.avg.items()}},
        "pooled": summarize(scored_pooled, counts_pooled, scorers),
        "per_day": {},
    }
    by_day = []
    for day, frame in per_day.items():
        scored, counts = analyse(frame, pages_at, corpus, url_of, titles)
        report["per_day"][day] = summarize(scored, counts, scorers)
        by_day.append(scored.assign(day=day))
    report["pooled"]["counts"]["same_site_multi_day"] = int(((scored_pooled["days"] >= 2) & scored_pooled["same_site"].astype(bool)).sum()) if len(scored_pooled) else 0
    per_day_frame = pd.concat(by_day, ignore_index=True) if by_day else pd.DataFrame()
    return report, scored_pooled, per_day_frame


CSV_COLUMNS = ["query", "group", "ref_day", "days", "agree_days", "hi", "lo", "pos_hi", "pos_lo", "site_hi", "site_lo",
               "same_site", "seen_this_search", "seen_any_search"] + [f"d_{name}" for name in VOTE_FEATURES] + \
              ["bm25f_credit", "vote_sum", "vote_credit"]


def _csv(frame: pd.DataFrame, groups: dict[str, str], extra: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=extra + CSV_COLUMNS)
    out = frame.assign(group=frame["query"].map(lambda q: groups.get(q, "other")))
    return out[[c for c in extra + CSV_COLUMNS if c in out.columns]]


def _json_default(value):
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


# ---------------------------------------------------------------------------------------------
# Tables


def _r(entry: dict | None) -> str:
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
    lines = [f"# Same-site content check ({res['mode']}): generated tables (scripts/claude/r2_samesite_check.py)", "",
             f"Days {', '.join(res['days'])}. Searches {res['search_groups']}. History cutoff {res['seen_cutoff']}. "
             f"Max SERP day loaded {res['max_serp_day_loaded']}. Max page fetch day used {res['pages']['max_fetch_day_used']}.",
             "Positions REAL (consensus of the day's pulls). Scorer outputs PREDICTED. Ties count half. "
             "A diagnostic, not proof that an edit moves rank.", ""]
    scorers = list(res["pooled"]["scorers"])
    for title, block in [("Pooled over the listed days", res["pooled"])] + [(f"Day {d}", b) for d, b in res["per_day"].items()]:
        c = block["counts"]
        lines += [f"## {title}", "",
                  f"Same-site pairs {c['same_site_pairs']}, without usable HTML {c['same_site_without_usable_html']}, duplicate content "
                  f"{c['same_site_duplicate_content_dropped']}, scored {c['same_site_usable_pairs']} on {c['same_site_searches']} searches and "
                  f"{c['same_site_sites']} sites ({c['same_site_distinct_url_pairs']} distinct URL pairs). Top 5 sites {c['same_site_top5_sites']}, "
                  f"share {c['same_site_top5_share'] if c['same_site_top5_share'] is None else round(c['same_site_top5_share'], 3)}. "
                  f"Cross-site pairs scored {c['cross_site_usable_pairs']} on {c['cross_site_searches']} searches.", "",
                  "| scorer | same-site | cross-site | same minus cross | same-site ties | same-site decided | same-site without top 5 sites |",
                  "| --- | --- | --- | --- | ---: | --- | --- |"]
        for name in scorers:
            e = block["scorers"][name]
            lines.append(f"| {name} | {_r(e['same_site'])} | {_r(e['cross_site'])} | {_d(e['same_minus_cross'])} | {e['same_site_ties']} | "
                         f"{_r(e['same_site_decided'])} | {_r(e['same_site_without_top5_sites'])} |")
        lines += ["", "| history split | same-site pairs / searches / sites | scorer | same-site | cross-site (pairs) | same minus cross |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for split, part in block["by_history"].items():
            for name in scorers:
                e = part[name]
                lines.append(f"| {split} | {part['same_site_pairs']} / {part['same_site_searches']} / {part['same_site_sites']} | {name} | "
                             f"{_r(e['same_site'])} | {_r(e['cross_site'])} ({part['cross_site_pairs']}) | {_d(e['same_minus_cross'])} |")
        lines.append("")
    check = res["prediction_check"]
    lines += ["## Prediction check", "", f"Prediction: {check['prediction']}. Mode: {check['mode']}.", "",
              "| part | observed | held |", "| --- | --- | --- |"]
    for name, part in check["parts"].items():
        shown = f"same {part['observed_same_site']:.3f}" if part["observed_same_site"] is not None else "n/a"
        if "observed_cross_site" in part and part["observed_cross_site"] is not None:
            shown += f", cross {part['observed_cross_site']:.3f}, diff {_d(part['same_minus_cross'])}"
        lines.append(f"| {name} | {shown} | {part['held']} |")
    lines += ["", f"All parts held: {check['held']}.", ""]
    if "design_comparison" in res:
        comp = res["design_comparison"]
        lines += ["## Design comparison (design mode only)", "", comp["note"], "", "| universe | scorer | same-site | cross-site |", "| --- | --- | --- | --- |"]
        for label, block in (("these days, o30 and a70", res["pooled"]), ("all design consensus days, all groups", comp["design_universe"]["pooled"])):
            for name, e in block["scorers"].items():
                lines.append(f"| {label} | {name} | {_r(e['same_site'])} | {_r(e['cross_site'])} |")
        ref = comp["design_study_u2"]
        lines += [f"| design study U2 | text_vote_8_with_serp_title | {ref['text_vote_8_with_serp_title']['hits']}/{ref['text_vote_8_with_serp_title']['n']} "
                  f"({ref['text_vote_8_with_serp_title']['rate']:.3f}) | n/a |",
                  f"| design study U2 | bm25f | {ref['bm25f']['hits']}/{ref['bm25f']['n']} ({ref['bm25f']['rate']:.3f}) | "
                  f"{ref['bm25f_cross_site']['hits']}/{ref['bm25f_cross_site']['n']} ({ref['bm25f_cross_site']['rate']:.3f}) |", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------


def load(last_day: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str], pd.DataFrame]:
    rows = rs.load_rows(last_day)
    if len(rows) and str(rows["day"].max()) > last_day:
        raise AssertionError("SERP row after the last listed day")
    cons = rs.consensus_table(rows)
    groups = rs.query_groups(rows[rows["day"] <= rs.DESIGN_MAX_DATE])
    fetches = tc.fetch_table(last_day)
    if len(fetches) and str(fetches["day"].max()) > last_day:
        raise AssertionError("page fetch after the last listed day")
    return rows, cons, groups, fetches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", required=True, help="Comma-separated YYYY-MM-DD days.")
    parser.add_argument("--prereg", help="Preregistration folder (required without --design).")
    parser.add_argument("--design", action="store_true", help="Run on design days (2026-11-17 or earlier) only.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing held-out output (identical rerun only).")
    parser.add_argument("--allow-incomplete", action="store_true", help="Score a day on which a search has fewer than three pulls.")
    args = parser.parse_args(argv)
    days = parse_days(args.days)
    check_days(days, args.design)
    manifest: dict = {}
    if args.design:
        out = DESIGN_OUT
    else:
        if not args.prereg:
            raise SystemExit("--prereg is required for a held-out run.")
        manifest = r2_score.require_frozen_dir((ROOT / args.prereg).resolve())
        out = HELDOUT_OUT
        sh.refuse_overwrite(out / f"{out_stem(days)}.json", args.force)

    rows, cons, groups, fetches = load(days[-1])
    if args.design:
        assert str(rows["day"].max()) <= rs.DESIGN_MAX_DATE and str(fetches["day"].max()) <= rs.DESIGN_MAX_DATE
    pulls = {}
    for day in days:
        pulls[day] = sh.pull_counts(cons, groups, day)
        if not pulls[day]["searches"]:
            raise SystemExit(f"No consensus top 10 for searches {GROUPS_USED} on {day}.")
        sh.require_complete(pulls[day], args.allow_incomplete)

    store = PageStore()
    body, pooled, per_day = run_check(rows, cons, fetches, store, days, groups,
                                      serp_title_day=days[-1] if args.design else None)
    report = {
        "label": "Positions REAL (Serper consensus). Scorer outputs PREDICTED. Diagnostic only: not proof that an edit moves rank.",
        "mode": "design" if args.design else "heldout",
        "prereg": Path(args.prereg).name if args.prereg else None,
        "manifest_frozen_at": manifest.get("frozen_at_utc"),
        "max_serp_day_loaded": str(rows["day"].max()),
        "max_page_fetch_day_loaded": str(fetches["day"].max()) if len(fetches) else None,
        "pulls": pulls,
        "scorer_definitions": {
            "bm25f": {"fields": {k: {"weight": w, "b": b} for k, (w, b) in ss.BM25F_FIELDS.items()}, "k1": ss.K1,
                      "source": "scripts.claude.r2_samesite_pairs.Corpus.bm25f", "direction": "higher first"},
            "text_vote": {"features": {name: ("larger first" if ss.DIRECTION[name] > 0 else "smaller first") for name in VOTE_FEATURES},
                          "source": "scripts.claude.r2_samesite_pairs.sign_vote", "tie": "vote sum 0 counts half"},
            "excluded_from_both": EXCLUDED_FEATURES,
            "corpus_rule": "one page per URL in any pair of this run (same-site or cross-site), the usable fetch nearest the last listed day",
            "page_rule": "usable fetch (>= 50 main-text words) nearest the pair's day, not after the last listed day; "
                         "pair's day is the day (per-day) or the latest listed day the pair was strictly ordered (pooled)",
        },
        **body,
    }
    report["prediction_check"] = prediction_check(report["pooled"], report["mode"])
    if args.design:
        all_days = sorted(d for d in cons["day"].unique() if d <= rs.DESIGN_MAX_DATE)
        universe, _p, _d = run_check(rows, cons, fetches, store, all_days, groups, groups_used=None,
                                     serp_title_day=rs.DESIGN_MAX_DATE)
        report["design_comparison"] = {
            "note": "Design mode only. The design study's U2 used every consensus day through 2026-11-17 and every search group, "
                    "pages nearest 2026-11-16, a corpus over its U1 deep-list and U2 pages, and an eight-feature vote that "
                    "includes serp_title_all. The second universe below reruns this script's pair, page and corpus rules on "
                    "all design consensus days and all groups, so the two rows separate the pair definition from the rest.",
            "design_study_u2": DESIGN_STUDY_U2,
            "design_universe": {"days": all_days, "pooled": {"counts": universe["pooled"]["counts"], "scorers": universe["pooled"]["scorers"]},
                                "corpus": universe["corpus"]},
        }

    out.mkdir(parents=True, exist_ok=True)
    stem = out_stem(days)
    _csv(pooled, groups, []).to_csv(out / f"{stem}-pairs.csv", index=False)
    _csv(per_day, groups, ["day"]).to_csv(out / f"{stem}-pairs-by-day.csv", index=False)
    (out / f"{stem}.md").write_text(tables_md(report) + "\n", encoding="utf-8")
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2, default=_json_default) + "\n", encoding="utf-8")
    print(tables_md(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
