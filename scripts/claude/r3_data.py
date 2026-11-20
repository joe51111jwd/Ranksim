"""Round 3 data layer: site-restricted SERP lists pulled by scripts/claude/r3_pull.py.

Reads the chunk files in evals/claude/r3/serp/<label>/ (the same bytes that went
into the raw store's serp_raw table). Positions are REAL Google positions from
Serper for a search of the form ``site:<site> <query>``. Nothing here predicts.

A "list" is one (label, site, query): the pages of one site in the order Google
returned them for the query. Pairs inside a list hold the site fixed.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from aeo.ranksim.site_history import site_key, url_key  # noqa: E402

SERP_DIR = REPO / "evals" / "claude" / "r3" / "serp"
_SITE_Q = re.compile(r"^site:(\S+)\s+(.*)$")


def split_site_query(q: str) -> tuple[str | None, str]:
    """('example.com', 'some search') for 'site:example.com some search', else (None, q)."""
    match = _SITE_Q.match(q.strip())
    if not match:
        return None, q.strip()
    return match.group(1).lower(), match.group(2).strip()


def chunk_paths(label: str) -> list[Path]:
    return sorted((SERP_DIR / label).glob("chunk-*.json.gz"))


def load_label(label: str) -> pd.DataFrame:
    """One row per organic result of every request stored under ``label``.

    Columns: label, q, site, query, page, fetched_at, position, url, key, url_site,
    title, snippet, date, n_results. A request with no organic results gives no rows
    (see ``load_requests`` for the request-level table).
    """
    records = []
    for path in chunk_paths(label):
        payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        for item in payload["items"]:
            body = json.loads(item["raw"])
            site, query = split_site_query(item["q"])
            organic = body.get("organic") or []
            for result in organic:
                url = result.get("link")
                if not url or result.get("position") is None:
                    continue
                records.append(
                    {
                        "label": label,
                        "q": item["q"],
                        "site": site,
                        "query": query,
                        "page": int(item.get("page") or 1),
                        "fetched_at": item["start"],
                        "position": int(result["position"]),
                        "url": url,
                        "key": url_key(url),
                        "url_site": site_key(url),
                        "title": result.get("title") or "",
                        "snippet": result.get("snippet") or "",
                        "date": result.get("date") or "",
                        "n_results": len(organic),
                    }
                )
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame
    # A URL can repeat inside one response. Keep its best position.
    frame = frame.sort_values(["q", "page", "position"]).drop_duplicates(["label", "q", "page", "key"], keep="first")
    return frame.reset_index(drop=True)


def load_requests(label: str) -> pd.DataFrame:
    """One row per request: label, q, site, query, page, fetched_at, n_results, credits."""
    records = []
    for path in chunk_paths(label):
        payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        for item in payload["items"]:
            body = json.loads(item["raw"])
            site, query = split_site_query(item["q"])
            records.append(
                {
                    "label": label,
                    "q": item["q"],
                    "site": site,
                    "query": query,
                    "page": int(item.get("page") or 1),
                    "fetched_at": item["start"],
                    "n_results": len(body.get("organic") or []),
                    "credits": body.get("credits"),
                }
            )
    return pd.DataFrame.from_records(records)


def list_pairs(frame: pd.DataFrame) -> pd.DataFrame:
    """All ordered pairs inside each list. key_hi is the page Google put first.

    Columns: label, q, site, query, key_hi, key_lo, url_hi, url_lo, pos_hi, pos_lo.
    Only results on the list's own site are paired (a site: search can return a
    sister domain; those rows are dropped here).
    """
    out = []
    for (label, q), group in frame.groupby(["label", "q"], sort=True):
        site = group["site"].iloc[0]
        own = group[group["url_site"] == site_key("https://" + site)] if site else group
        items = own.sort_values(["page", "position"])[["key", "url", "position", "page"]].to_records(index=False)
        ranked = [(key, url, (int(page) - 1) * 10 + int(position)) for key, url, position, page in items]
        for i in range(len(ranked)):
            for j in range(i + 1, len(ranked)):
                out.append(
                    {
                        "label": label,
                        "q": q,
                        "site": site,
                        "query": group["query"].iloc[0],
                        "key_hi": ranked[i][0],
                        "key_lo": ranked[j][0],
                        "url_hi": ranked[i][1],
                        "url_lo": ranked[j][1],
                        "pos_hi": ranked[i][2],
                        "pos_lo": ranked[j][2],
                    }
                )
    return pd.DataFrame.from_records(out)
