"""Round 3 page store: the parsed stored HTML for a set of URLs.

For each URL the newest good fetch in the raw store is used (plain HTTP first,
then the headless browser), parsed with the repo extractor (the same one the
ranker uses). A page is usable when its main text has at least MIN_WORDS words.

Only page content is read here. No SERP row, no position, no rank history.
Parsed pages are cached outside git, keyed by the store's page id.
"""

from __future__ import annotations

import pickle
import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from aeo.ranksim.site_history import url_key  # noqa: E402
from scripts.claude.t2_changes import MIN_WORDS, extract_page  # noqa: E402

DB = Path("/workspace/ranksim-data/ranksim_raw.sqlite")
CACHE_DIR = Path("/workspace/ranksim-data/cache/r3_pages")
KEEP_METHODS = ("http", "browser")
MIN_HTML_BYTES = 500


def _connect(db: Path | str = DB) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)


def fetch_index(max_fetched_at: str | None = None, db: Path | str = DB) -> pd.DataFrame:
    """Good fetch records (no HTML): id, url, key, fetched_at, fetch_method, html_len."""
    sql = (
        "SELECT id, url, fetched_at_utc AS fetched_at, fetch_method, http_status, length(html) AS html_len "
        "FROM pages WHERE fetch_method IN ('http', 'browser') AND length(html) >= ? "
        "AND (http_status IS NULL OR http_status = 200)"
    )
    params: list = [MIN_HTML_BYTES]
    if max_fetched_at:
        sql += " AND fetched_at_utc <= ?"
        params.append(max_fetched_at)
    with _connect(db) as con:
        frame = pd.read_sql_query(sql, con, params=params)
    frame["key"] = frame["url"].map(url_key)
    return frame


def _load_cache() -> dict[int, dict]:
    path = CACHE_DIR / "parsed-v1.pkl"
    if path.is_file():
        return pickle.loads(path.read_bytes())
    return {}


def _save_cache(cache: dict[int, dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "parsed-v1.pkl"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(cache, protocol=4))
    tmp.replace(path)


def load_pages(keys: set[str], max_fetched_at: str | None = None, db: Path | str = DB) -> dict[str, dict]:
    """Parsed usable page for each url key that has one.

    The newest fetch whose main text reaches MIN_WORDS wins, plain HTTP before
    browser at equal time. Returned dicts are extract_page() output plus
    page_id, fetch_url, fetched_at, method, links (absolute URLs found on the page).
    """
    index = fetch_index(max_fetched_at, db)
    index = index[index["key"].isin(keys)].copy()
    index["not_http"] = (index["fetch_method"] != "http").astype(int)
    index = index.sort_values(["key", "fetched_at", "not_http", "id"], ascending=[True, False, True, False])
    cache = _load_cache()
    changed = False
    pages: dict[str, dict] = {}
    with _connect(db) as con:
        for key, group in index.groupby("key", sort=True):
            for record in group.itertuples(index=False):
                pid = int(record.id)
                if pid not in cache:
                    row = con.execute("SELECT html FROM pages WHERE id = ?", (pid,)).fetchone()
                    html = (row[0] or "") if row else ""
                    parsed = extract_page(html, record.url)
                    parsed["links"] = _links(html, record.url)
                    cache[pid] = parsed
                    changed = True
                parsed = cache[pid]
                if int(parsed.get("word_count") or 0) >= MIN_WORDS:
                    pages[key] = dict(parsed, page_id=pid, fetch_url=record.url, fetched_at=record.fetched_at, method=record.fetch_method)
                    break
    if changed:
        _save_cache(cache)
    return pages


def _links(html: str, url: str) -> list[tuple[str, str]]:
    """(absolute link, anchor text) for every anchor on the page."""
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    out: list[tuple[str, str]] = []
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except (ValueError, TypeError, AttributeError):
        return out
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(url, href).split("#")[0]
        if absolute.startswith(("http://", "https://")):
            out.append((absolute, anchor.get_text(" ", strip=True)[:200]))
    return out
