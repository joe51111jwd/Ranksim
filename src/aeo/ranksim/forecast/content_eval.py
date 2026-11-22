"""Diagnostics of part 4 (the content match scorer) on public data.

These are measurements of one part. They are not marker numbers and they are
never pooled with forecast grades. Every function prints what it was computed on.

What is REAL: the result pages and their order (Google, as recorded by the data
publishers), the scraped landing-page text, DataForSEO's titles, descriptions and
main_domain_rank values. What is PREDICTED: every content score
(`aeo.ranksim.forecast.content`). The measurement is pairwise accuracy: for two
results of the same search, a hit is 1 when the higher-ranked result has the
higher score, 0 when it has the lower score, and 0.5 on a tie. A pair where
either score is missing is left out and counted. Intervals are 95% bootstrap
intervals that resample whole searches (`stats.cluster_bootstrap_ratio`).

(a) `eval_flights`: Kaggle eliasdabbas/flights-serps-and-landing-pages (CC0).
    Google Custom Search API result pages of 2020-03-01, top 10, 200 flight
    searches times 2 countries (gl=us, gl=uk), joined to landing pages scraped on
    2020-03-11. One niche, 2020 data, from Google's search API, not a live page.
(b) `eval_snippets`: DataForSEO sample result pages (US English and the other
    English locales), snippet mode over the top 10, 20 and 50. Snippets are
    written by Google for the search, so snippet accuracy flatters the scorer.
    It is not a page content number.
(c) inside (b): accuracy on pairs whose main_domain_rank differs by less than a
    threshold, against all pairs, and site strength alone on the same pairs
    (one domain_rank line per content score, labelled "..., pairs scored by
    <score>": the pairs of that subset where the score is known on both sides).

Commands:
  python -m aeo.ranksim.forecast.content_eval download-flights
  python -m aeo.ranksim.forecast.content_eval flights
  python -m aeo.ranksim.forecast.content_eval snippets [--searches 400]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from ..site_history import site_key
from .config import BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, forecast_paths
from .content import (
    MODE_CROSS,
    MODE_LEXICAL,
    ContentScorer,
    PageText,
    page_text_from_fields,
)
from .features import excluded_site_mask

LABEL = "DIAGNOSTIC of part 4 on public data. Not a marker number."

FLIGHTS_SOURCE = "kaggle_flights_landing"
FLIGHTS_DATASET = "eliasdabbas/flights-serps-and-landing-pages"
FLIGHTS_FILE = "flights_serp_scrape.csv"
FLIGHTS_ZIP = "flights_serp_scrape.csv.zip"
FLIGHTS_URL = f"https://www.kaggle.com/api/v1/datasets/download/{FLIGHTS_DATASET}/{FLIGHTS_FILE}"
FLIGHTS_ZIP_SHA256 = "0d00327448683a4b14db327015f1d62f985bc35f8419b63f63737d58cb105a4d"
FLIGHTS_LICENCE = "CC0: Public Domain"
# Kaggle's anonymous API answers JSON only to a short User-Agent (datahunt open-data-hubs.verified.md).
KAGGLE_USER_AGENT = "Mozilla/5.0"
HTTP_TIMEOUT = httpx.Timeout(60.0, connect=20.0)
FLIGHTS_H1_SEPARATOR = "@@"

DFS_DATASET_PARTITION = "dataset=dfs_serp"
DFS_US_LOCALE = "US_en"
DFS_COLUMNS = ("locale", "keyword", "keyword_id", "snapshot_date", "rank", "site", "url", "title", "description", "main_domain_rank", "poison_risk")

EVAL_SEED = 20261122
DEFAULT_SNIPPET_SEARCHES = 400
TOP_KS = (10, 20, 50)
GAP_BUCKETS: tuple[tuple[str, int, int], ...] = (("adjacent", 1, 1), ("2 to 4 apart", 2, 4), ("5 or more apart", 5, 10_000))
DOMAIN_RANK_THRESHOLDS = (10, 25, 50)
MIN_USABLE_BODY_STATUS = 200


# ---------------------------------------------------------------------------
# Pairwise accuracy (pure)


@dataclass(frozen=True)
class Accuracy:
    """Pairwise accuracy of one score on one set of pairs."""

    scorer: str
    subset: str
    pairs: int
    searches: int
    unscored_pairs: int
    accuracy: float | None
    ci_low: float | None
    ci_high: float | None

    def line(self) -> str:
        if self.accuracy is None:
            return f"{self.scorer:<22} {self.subset:<34} pairs {self.pairs:>7}  searches {self.searches:>4}  no scored pairs"
        ci = "" if self.ci_low is None else f"  95% [{self.ci_low:.3f}, {self.ci_high:.3f}]"
        miss = f"  unscored {self.unscored_pairs}" if self.unscored_pairs else ""
        return f"{self.scorer:<22} {self.subset:<34} pairs {self.pairs:>7}  searches {self.searches:>4}  accuracy {self.accuracy:.3f}{ci}{miss}"


def build_pairs(frame: pd.DataFrame, *, group_col: str, rank_col: str, keep: Sequence[str]) -> pd.DataFrame:
    """Every pair of two results of the same search with different ranks.

    Columns `<c>_hi` belong to the higher-ranked result (smaller rank), `<c>_lo`
    to the other. Adds `gap` = lo rank minus hi rank.
    """
    cols = [group_col, rank_col, *[c for c in keep if c not in (group_col, rank_col)]]
    base = frame[cols].reset_index(drop=True)
    left = base.rename(columns={c: f"{c}_hi" for c in cols if c != group_col})
    right = base.rename(columns={c: f"{c}_lo" for c in cols if c != group_col})
    pairs = left.merge(right, on=group_col)
    pairs = pairs[pairs[f"{rank_col}_hi"] < pairs[f"{rank_col}_lo"]].reset_index(drop=True)
    pairs["gap"] = pairs[f"{rank_col}_lo"] - pairs[f"{rank_col}_hi"]
    return pairs


def pair_hits(hi: Sequence[float] | np.ndarray, lo: Sequence[float] | np.ndarray) -> np.ndarray:
    """1 when the higher-ranked item scores higher, 0 when lower, 0.5 on a tie, NaN when either is missing."""
    a = np.asarray(hi, dtype=float)
    b = np.asarray(lo, dtype=float)
    out = np.where(a > b, 1.0, np.where(a < b, 0.0, 0.5))
    out[np.isnan(a) | np.isnan(b)] = np.nan
    return out


def accuracy(
    pairs: pd.DataFrame,
    score: str,
    *,
    cluster_col: str,
    subset: str = "all pairs",
    mask: np.ndarray | pd.Series | None = None,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> Accuracy:
    """Accuracy of score column `score` (`<score>_hi`, `<score>_lo`) with a search-cluster bootstrap."""
    from .stats import cluster_bootstrap_ratio

    part = pairs if mask is None else pairs[np.asarray(mask, dtype=bool)]
    hits = pair_hits(part[f"{score}_hi"].to_numpy(), part[f"{score}_lo"].to_numpy())
    scored = ~np.isnan(hits)
    clusters = part[cluster_col].to_numpy()
    n_scored = int(scored.sum())
    if n_scored == 0:
        return Accuracy(score, subset, len(part), int(pd.Series(clusters).nunique()), len(part), None, None, None)
    sums = pd.DataFrame({"c": clusters[scored], "h": hits[scored]}).groupby("c", sort=True)["h"].agg(["sum", "count"])
    low, high = cluster_bootstrap_ratio(sums["sum"].tolist(), sums["count"].tolist(), sums.index.tolist(), draws=draws, seed=seed)
    return Accuracy(
        scorer=score,
        subset=subset,
        pairs=n_scored,
        searches=len(sums),
        unscored_pairs=int(len(part) - n_scored),
        accuracy=float(hits[scored].mean()),
        ci_low=low,
        ci_high=high,
    )


def accuracy_table(pairs: pd.DataFrame, scores: Sequence[str], *, cluster_col: str, same_site_col: str | None) -> list[Accuracy]:
    """All pairs, cross-site and same-site pairs, and the three rank-gap buckets, for every score."""
    rows: list[Accuracy] = []
    for score in scores:
        rows.append(accuracy(pairs, score, cluster_col=cluster_col))
        if same_site_col is not None:
            same = pairs[same_site_col].to_numpy(dtype=bool)
            rows.append(accuracy(pairs, score, cluster_col=cluster_col, subset="cross-site pairs", mask=~same))
            rows.append(accuracy(pairs, score, cluster_col=cluster_col, subset="same-site pairs", mask=same))
        else:
            same = np.zeros(len(pairs), dtype=bool)
        for name, low, high in GAP_BUCKETS:
            gap = pairs["gap"].to_numpy()
            rows.append(accuracy(pairs, score, cluster_col=cluster_col, subset=f"cross-site, {name}", mask=(~same) & (gap >= low) & (gap <= high)))
    return rows


# ---------------------------------------------------------------------------
# Provenance


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def record_source(folder: Path, entry: dict[str, Any]) -> Path:
    """Add or replace one file's provenance in `<folder>/SOURCES.json`."""
    target = folder / "SOURCES.json"
    data: dict[str, Any] = {"entries": []}
    if target.is_file():
        data = json.loads(target.read_text(encoding="utf-8"))
    data["entries"] = [e for e in data.get("entries", []) if e.get("file") != entry.get("file")] + [entry]
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


# ---------------------------------------------------------------------------
# (a) flights: download and load


def flights_dir(root: Path | None = None) -> Path:
    return (root or forecast_paths().root) / "public" / FLIGHTS_SOURCE


def download_flights(dest_dir: Path | None = None, *, client: httpx.Client | None = None) -> Path:
    """Fetch the flights landing-page file (about 20 MB zipped) once, resumable, sha256 checked.

    Anonymous Kaggle download: a 302 to a signed storage URL. Never the whole-dataset bundle.
    """
    folder = dest_dir or flights_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / FLIGHTS_ZIP
    if target.is_file() and sha256_path(target) == FLIGHTS_ZIP_SHA256:
        _record_flights(folder, target, fetched_at=None)
        return target
    part = folder / (FLIGHTS_ZIP + ".part")
    owns = client is None
    http = client or httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False, headers={"User-Agent": KAGGLE_USER_AGENT})
    try:
        first = http.get(FLIGHTS_URL)
        if first.status_code != 302 or "location" not in first.headers:
            raise RuntimeError(f"Kaggle answered {first.status_code} for {FLIGHTS_URL}, expected a 302 to the file")
        signed = first.headers["location"]
        if "archive.zip" in signed or "/bundle/" in signed:
            raise RuntimeError("Kaggle redirected to the whole-dataset bundle. Refusing it.")
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with http.stream("GET", signed, headers=headers) as resp:
            if resp.status_code not in (200, 206):
                raise RuntimeError(f"storage answered {resp.status_code}")
            mode = "ab" if resp.status_code == 206 else "wb"
            with open(part, mode) as fh:
                fh.writelines(resp.iter_bytes(1 << 16))
    finally:
        if owns:
            http.close()
    digest = sha256_path(part)
    if digest != FLIGHTS_ZIP_SHA256:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"flights file sha256 {digest} differs from the pinned {FLIGHTS_ZIP_SHA256}. Deleted.")
    part.replace(target)
    _record_flights(folder, target, fetched_at=_now())
    return target


def _record_flights(folder: Path, target: Path, *, fetched_at: str | None) -> None:
    """Write the provenance entry. A file already on disk without one gets its file time as the fetch time."""
    sources = folder / "SOURCES.json"
    if fetched_at is None and sources.is_file():
        entries = json.loads(sources.read_text(encoding="utf-8")).get("entries", [])
        if any(e.get("file") == FLIGHTS_ZIP and e.get("sha256") == FLIGHTS_ZIP_SHA256 for e in entries):
            return
    when = fetched_at or datetime.fromtimestamp(target.stat().st_mtime, tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record_source(
        folder,
        {
            "file": FLIGHTS_ZIP,
            "url": FLIGHTS_URL,
            "dataset": FLIGHTS_DATASET,
            "licence": FLIGHTS_LICENCE,
            "fetched_at": when,
            "sha256": FLIGHTS_ZIP_SHA256,
            "bytes": target.stat().st_size,
            "note": "anonymous Kaggle download with User-Agent Mozilla/5.0, a 302 to a signed storage URL",
        },
    )


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return str(value)


def load_flights(path: Path | None = None) -> pd.DataFrame:
    """One row per (search, country, rank). REAL result order plus REAL scraped text.

    Columns: search, gl, search_id, rank, url, site, serp_title, serp_snippet,
    title, h1, meta, body, scrape_status, usable. H1 is the first of the
    "@@"-joined H1 values. `usable` = scrape status 200 and some page text.
    Rows of excluded sites are dropped.
    """
    path = path or (flights_dir() / FLIGHTS_ZIP)
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing. Run `python -m aeo.ranksim.forecast.content_eval download-flights`.")
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive, archive.open(FLIGHTS_FILE) as fh:
            raw = pd.read_csv(io.TextIOWrapper(fh, encoding="utf-8"), low_memory=False)
    else:
        raw = pd.read_csv(path, low_memory=False)
    out = pd.DataFrame(
        {
            "search": raw["serp_searchTerms"].map(_text),
            "gl": raw["serp_gl"].map(_text),
            "rank": raw["serp_rank"].astype(int),
            "url": raw["serp_link"].map(_text),
            "serp_title": raw["serp_title"].map(_text),
            "serp_snippet": raw["serp_snippet"].map(_text),
            "title": raw["scrape_title"].map(_text),
            "h1": raw["scrape_h1"].map(lambda v: _text(v).split(FLIGHTS_H1_SEPARATOR)[0]),
            "meta": raw["scrape_meta_desc"].map(_text),
            "body": raw["scrape_body_text"].map(_text),
            "scrape_status": raw["scrape_status"],
            "serp_date": raw["serp_queryTime"].map(lambda v: _text(v)[:10]),
            "scrape_date": raw["scrape_time"].map(lambda v: _text(v)[:10]),
        }
    )
    out["site"] = out["url"].map(lambda u: site_key(u) if u else "")
    out = out[~excluded_site_mask(out["site"]).to_numpy()].reset_index(drop=True)
    out["search_id"] = out["gl"] + "|" + out["search"]
    has_text = (out["title"].str.strip() != "") | (out["h1"].str.strip() != "") | (out["meta"].str.strip() != "") | (out["body"].str.strip() != "")
    out["usable"] = (out["scrape_status"] == MIN_USABLE_BODY_STATUS) & has_text
    return out


def flights_pages(frame: pd.DataFrame) -> list[PageText]:
    return [
        page_text_from_fields(title=r.title, h1=r.h1, meta_description=r.meta, body=r.body, url=r.url)
        for r in frame.itertuples(index=False)
    ]


def score_frame_pages(frame: pd.DataFrame, pages: Sequence[PageText], scorer: ContentScorer, prefix: str) -> pd.DataFrame:
    """Adds <prefix>_head, <prefix>_passage and <prefix>_page columns (NaN where missing)."""
    def nan(value: float | None) -> float:
        return np.nan if value is None else float(value)

    out = frame.copy()
    if scorer.mode == MODE_LEXICAL:
        parts = [scorer.lexical.score_page(s, p) for s, p in zip(frame["search"], pages)]
        out[f"{prefix}_head"] = [nan(x["head"]) for x in parts]
        out[f"{prefix}_passage"] = [nan(x["best_passage"]) for x in parts]
        out[f"{prefix}_page"] = [nan(x["page"]) for x in parts]
        return out
    results = scorer.score_many([(s, p) for s, p in zip(frame["search"], pages)])
    head = [nan(r.parts["head"]) for r in results]
    passage = [nan(r.parts["best_passage"]) for r in results]
    out[f"{prefix}_head"] = head
    out[f"{prefix}_passage"] = passage
    out[f"{prefix}_page"] = [np.nan if (np.isnan(h) and np.isnan(b)) else float(np.nanmax([h, b])) for h, b in zip(head, passage)]
    return out


def score_frame_snippets(frame: pd.DataFrame, scorer: ContentScorer, column: str, *, search_col: str, title_col: str, desc_col: str) -> pd.DataFrame:
    results = scorer.score_snippets(list(zip(frame[search_col], frame[title_col], frame[desc_col])))
    out = frame.copy()
    out[column] = [np.nan if r.value is None else r.value for r in results]
    return out


def _ce_note(cross: ContentScorer) -> str:
    if cross.mode == MODE_CROSS:
        return f"Cross-encoder ran: yes ({cross.model_name})."
    return f"Cross-encoder ran: no. {cross.fallback_reason or 'Lexical only was asked for.'}"


def _print(lines: Iterable[str], echo: bool) -> None:
    if echo:
        for line in lines:
            print(line)


def _write_json(name: str, payload: dict[str, Any], out_dir: Path | None) -> Path:
    folder = out_dir or (forecast_paths().cache / "content_eval")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return target


def eval_flights(
    path: Path | None = None,
    *,
    cross: ContentScorer | None = None,
    lexical: ContentScorer | None = None,
    echo: bool = True,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """(a) Pairwise accuracy on the flights result pages with scraped landing-page text."""
    t0 = time.monotonic()
    frame = load_flights(path)
    usable = frame[frame["usable"]].reset_index(drop=True)
    lexical = lexical or ContentScorer(MODE_LEXICAL)
    cross = cross if cross is not None else ContentScorer(MODE_CROSS)
    pages = flights_pages(usable)
    scored = score_frame_pages(usable, pages, lexical, "lex")
    scores = ["lex_head", "lex_passage", "lex_page", "lex_snippet"]
    scored = score_frame_snippets(scored, lexical, "lex_snippet", search_col="search", title_col="serp_title", desc_col="serp_snippet")
    if cross.mode == MODE_CROSS:
        scored = score_frame_pages(scored, pages, cross, "ce")
        scored = score_frame_snippets(scored, cross, "ce_snippet", search_col="search", title_col="serp_title", desc_col="serp_snippet")
        scores = ["ce_head", "ce_passage", "ce_page", "ce_snippet", *scores]
    # Bootstrap cluster = the search text: the gl=us and gl=uk pages of one search share most results.
    pairs = build_pairs(scored, group_col="search_id", rank_col="rank", keep=["search", "site", *scores])
    pairs["same_site"] = pairs["site_hi"] == pairs["site_lo"]
    pairs["cluster"] = pairs["search_hi"]
    table = accuracy_table(pairs, scores, cluster_col="cluster", same_site_col="same_site")
    header = [
        LABEL,
        "(a) Flights result pages with scraped landing pages.",
        f"Data: Kaggle {FLIGHTS_DATASET} ({FLIGHTS_LICENCE}). Google Custom Search API result pages of {', '.join(sorted(frame['serp_date'].unique()))},",
        f"landing pages scraped on {', '.join(sorted(frame['scrape_date'].unique()))}. One niche (flights), top 10 only, 2020 data, gl=us and gl=uk.",
        f"Rows {len(frame)}, usable pages {len(usable)} (status 200 with some text), result pages {frame['search_id'].nunique()}, searches {frame['search'].nunique()}.",
        f"Pairs {len(pairs)} (cross-site {int((~pairs['same_site']).sum())}, same-site {int(pairs['same_site'].sum())}). Intervals resample searches ({BOOTSTRAP_DRAWS} draws, seed {BOOTSTRAP_SEED}).",
        "Scores: ce = cross-encoder minilm-l6 (untrained), lex = BM25F. head = title . H1 . meta, passage = best 200-word body window,",
        "page = max of the two (ce) or BM25F over all fields (lex), snippet = the result page's own title and snippet (written by Google).",
        _ce_note(cross),
    ]
    _print(header + [a.line() for a in table], echo)
    payload = {
        "label": LABEL,
        "computed_on": header[1:6],
        "seconds": round(time.monotonic() - t0, 1),
        "results": [a.__dict__ for a in table],
    }
    payload["written_to"] = str(_write_json("flights", payload, out_dir))
    return payload


# ---------------------------------------------------------------------------
# (b) and (c) DataForSEO snippets


def dfs_panel_dir(root: Path | None = None) -> Path:
    return (root or forecast_paths().cache) / "panel" / DFS_DATASET_PARTITION


def english_locales(panel_dir: Path) -> list[str]:
    return sorted(p.name.split("=", 1)[1] for p in panel_dir.glob("locale=*_en") if p.is_dir())


def _read_locale(panel_dir: Path, locale: str, columns: Sequence[str], keyword_ids: set[str] | None = None) -> pd.DataFrame:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    tables = []
    for part in sorted((panel_dir / f"locale={locale}").glob("*.parquet")):
        # A part written before the column was renamed carries poison_window. Read it under the new name.
        names = set(pq.read_schema(part).names)
        legacy = "poison_risk" in columns and "poison_risk" not in names and "poison_window" in names
        table = pq.read_table(part, columns=["poison_window" if legacy and c == "poison_risk" else c for c in columns])
        if legacy:
            table = table.rename_columns(["poison_risk" if c == "poison_window" else c for c in table.column_names])
        if keyword_ids is not None:
            table = table.filter(pc.is_in(table["keyword_id"], value_set=pa.array(sorted(keyword_ids))))
        tables.append(table.to_pandas())
    if not tables:
        return pd.DataFrame(columns=list(columns))
    return pd.concat(tables, ignore_index=True)


def sample_dfs_searches(panel_dir: Path, locales: Sequence[str], n: int, *, seed: int = EVAL_SEED) -> pd.DataFrame:
    """Rows (rank <= 50) of n searches drawn at random, with a fixed seed, from the pooled locales."""
    keys: list[tuple[str, str]] = []
    for locale in locales:
        ids = _read_locale(panel_dir, locale, ["keyword_id"])["keyword_id"].unique()
        keys.extend((locale, str(k)) for k in sorted(ids))
    keys.sort()
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(keys), size=min(n, len(keys)), replace=False)
    chosen: dict[str, set[str]] = {}
    for i in sorted(pick.tolist()):
        locale, kid = keys[i]
        chosen.setdefault(locale, set()).add(kid)
    frames = [_read_locale(panel_dir, locale, DFS_COLUMNS, ids) for locale, ids in sorted(chosen.items())]
    rows = pd.concat(frames, ignore_index=True)
    rows = rows[(rows["rank"] <= max(TOP_KS)) & (~rows["poison_risk"].fillna(False).astype(bool)) & (~excluded_site_mask(rows["site"]).to_numpy())]
    rows = rows.sort_values(["locale", "keyword_id", "rank", "url"]).drop_duplicates(["locale", "keyword_id", "rank"]).reset_index(drop=True)
    rows["search_id"] = rows["locale"] + "|" + rows["keyword_id"]
    return rows


def _snippet_report(rows: pd.DataFrame, scores: Sequence[str], name: str) -> tuple[list[str], list[dict[str, Any]]]:
    lines: list[str] = []
    out: list[dict[str, Any]] = []
    depth = rows.groupby("search_id")["rank"].max()
    for k in TOP_KS:
        sub = rows[rows["rank"] <= k]
        pairs = build_pairs(sub, group_col="search_id", rank_col="rank", keep=["site", "main_domain_rank", *scores])
        pairs["same_site"] = pairs["site_hi"] == pairs["site_lo"]
        lines.append(f"-- {name}, top {k}: searches with a result at rank {k} or deeper {int((depth >= k).sum())} of {len(depth)}, pairs {len(pairs)}")
        table = accuracy_table(pairs, scores, cluster_col="search_id", same_site_col="same_site")
        # (c) site strength held roughly fixed
        mdr_hi = pairs["main_domain_rank_hi"].astype(float).to_numpy()
        mdr_lo = pairs["main_domain_rank_lo"].astype(float).to_numpy()
        known = ~np.isnan(mdr_hi) & ~np.isnan(mdr_lo)
        cross_site = ~pairs["same_site"].to_numpy(dtype=bool)
        diff = np.abs(mdr_hi - mdr_lo)
        pairs["domain_rank_hi"] = mdr_hi
        pairs["domain_rank_lo"] = mdr_lo
        # Site strength alone on the same pairs as each content score: the pairs of the subset where that
        # score is known on both sides (the content line leaves the others out as unscored pairs).
        strength_rows: list[Accuracy] = []
        subsets = [("cross-site, domain rank known", known & cross_site)]
        subsets += [(f"cross-site, domain rank diff < {t}", known & cross_site & (diff < t)) for t in DOMAIN_RANK_THRESHOLDS]
        for score in scores:
            scored = ~np.isnan(pair_hits(pairs[f"{score}_hi"].to_numpy(), pairs[f"{score}_lo"].to_numpy()))  # as accuracy() counts them
            for label, mask in subsets:
                table.append(accuracy(pairs, score, cluster_col="search_id", subset=label, mask=mask))
                strength_rows.append(accuracy(pairs, "domain_rank", cluster_col="search_id", subset=f"{label}, pairs scored by {score}",
                                              mask=mask & scored))
        table.extend(strength_rows)
        lines.extend(a.line() for a in table)
        out.extend({"locales": name, "top_k": k, **a.__dict__} for a in table)
    return lines, out


def eval_snippets(
    *,
    panel_dir: Path | None = None,
    searches: int = DEFAULT_SNIPPET_SEARCHES,
    seed: int = EVAL_SEED,
    cross: ContentScorer | None = None,
    lexical: ContentScorer | None = None,
    echo: bool = True,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """(b) snippet-mode pairwise accuracy on DataForSEO sample pages, and (c) with site strength held roughly fixed.

    `searches` searches are drawn from US English and, separately, `searches`
    from the other English locales pooled.
    """
    t0 = time.monotonic()
    folder = panel_dir or dfs_panel_dir()
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is missing. Build the panel with aeo.ranksim.forecast.sources.panel.build_panel().")
    locales = english_locales(folder)
    others = [loc for loc in locales if loc != DFS_US_LOCALE]
    lexical = lexical or ContentScorer(MODE_LEXICAL)
    cross = cross if cross is not None else ContentScorer(MODE_CROSS)
    header = [
        LABEL,
        "(b) Snippet mode on DataForSEO sample result pages, and (c) site strength held roughly fixed.",
        "Snippets (title and description) are written by Google for the search, so this flatters the scorer. It is not page content.",
        f"Data: {folder} (DataForSEO free sample databases, single country-level pulls). Seed {seed}.",
        f"Strength = DataForSEO main_domain_rank (higher ranks higher). Intervals resample searches ({BOOTSTRAP_DRAWS} draws, seed {BOOTSTRAP_SEED}).",
        _ce_note(cross),
    ]
    _print(header, echo)
    results: list[dict[str, Any]] = []
    computed_on = list(header[1:5])
    for name, group in (("US English", [DFS_US_LOCALE]), (f"other English ({len(others)} locales)", others)):
        if not group or not all((folder / f"locale={loc}").is_dir() for loc in group):
            _print([f"-- {name}: no panel files, skipped"], echo)
            continue
        rows = sample_dfs_searches(folder, group, searches, seed=seed)
        rows = score_frame_snippets(rows, lexical, "lex_snippet", search_col="keyword", title_col="title", desc_col="description")
        scores = ["lex_snippet"]
        if cross.mode == MODE_CROSS:
            rows = score_frame_snippets(rows, cross, "ce_snippet", search_col="keyword", title_col="title", desc_col="description")
            scores = ["ce_snippet", *scores]
        dates = rows["snapshot_date"].astype(str)
        mdr_missing = int(rows["main_domain_rank"].isna().sum())
        info = (
            f"-- {name}: {rows['search_id'].nunique()} searches, {len(rows)} results at rank 50 or better, "
            f"locales {sorted(rows['locale'].unique())[:6]}{' ...' if rows['locale'].nunique() > 6 else ''}, "
            f"page dates {dates.min()} to {dates.max()}, results without main_domain_rank {mdr_missing}, "
            f"without title or description {int(((rows['title'].fillna('') == '') & (rows['description'].fillna('') == '')).sum())}"
        )
        computed_on.append(info)
        _print([info], echo)
        lines, out = _snippet_report(rows, scores, name)
        _print(lines, echo)
        results.extend(out)
    payload = {"label": LABEL, "computed_on": computed_on, "seconds": round(time.monotonic() - t0, 1), "results": results}
    payload["written_to"] = str(_write_json("snippets", payload, out_dir))
    return payload


# ---------------------------------------------------------------------------
# CLI


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.forecast.content_eval", description=LABEL)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("download-flights", help="fetch the Kaggle flights landing-page file (about 20 MB)")
    fl = sub.add_parser("flights", help="(a) page-mode accuracy on the flights set")
    fl.add_argument("--lexical-only", action="store_true")
    sn = sub.add_parser("snippets", help="(b) and (c) snippet-mode accuracy on DataForSEO samples")
    sn.add_argument("--searches", type=int, default=DEFAULT_SNIPPET_SEARCHES)
    sn.add_argument("--seed", type=int, default=EVAL_SEED)
    sn.add_argument("--lexical-only", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "download-flights":
        path = download_flights()
        print(f"Flights file at {path}, sha256 {FLIGHTS_ZIP_SHA256}.")
        return 0
    cross = ContentScorer(MODE_LEXICAL) if args.lexical_only else None
    if args.command == "flights":
        eval_flights(cross=cross)
        return 0
    eval_snippets(searches=args.searches, seed=args.seed, cross=cross)
    return 0


if __name__ == "__main__":
    sys.exit(main())
