"""Candidate URLs for one index build.

The pool is the union of three sources:
- Serper organic results, num up to 100, so positions 11-100 are labels
- every URL in a past snapshot
- internal links of ranking domains that share a query term
"""

from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urlsplit

from aeo.crawl.urls import domain_of, same_site
from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.localindex.records import Candidate, PageRecord, canon_url, page_from_html
from aeo.textutil import salient_terms

SERP_NAMES = ("serp_serper_live.csv", "serp.csv", "slim.csv")
_JUNK_PARTS = (
    "/login",
    "/signin",
    "/sign-in",
    "/signup",
    "/sign-up",
    "/register",
    "/cart",
    "/checkout",
    "/privacy",
    "/terms",
    "/cookie",
    "/account",
    "/wp-admin",
    "/wp-login",
    "/cdn-cgi",
    "/feed",
    "/rss",
)
_ASSET_EXT = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".zip",
    ".css",
    ".js",
    ".mp4",
    ".xml",
    ".ico",
)


def snapshot_serp(snapshot: Path) -> Path | None:
    for name in SERP_NAMES:
        path = snapshot / name
        if path.is_file():
            return path
    return None


def read_queries_file(path: Path) -> list[str]:
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = " ".join(line.split())
        if text and not text.startswith("#"):
            found.append(text)
    return found


def load_snapshot(snapshot: Path) -> tuple[list[Candidate], dict[str, PageRecord]]:
    """Labeled rows plus any HTML already saved beside the SERP."""
    folder = Path(snapshot)
    serp = snapshot_serp(folder)
    if serp is None:
        raise ValueError(f"{folder} has no serp CSV. Expected one of: {', '.join(SERP_NAMES)}")
    snapshot_id = folder.name
    candidates: list[Candidate] = []
    seen: set[tuple[str, str, str, str]] = set()
    for label in load_serp_file(serp):
        url = canon_url(label.url)
        if url is None:
            continue
        position = int(label.position)
        item = Candidate(url=url, query=label.query, position=position, source="snapshot", snapshot_id=snapshot_id)
        key = (item.url, item.query, item.source, item.snapshot_id)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(item)
    pages = _seed_html(folder, candidates)
    return candidates, pages


def _seed_html(folder: Path, candidates: list[Candidate]) -> dict[str, PageRecord]:
    manifest = folder / "html_manifest.csv"
    pages: dict[str, PageRecord] = {}
    if not manifest.is_file():
        for item in candidates:
            pages.setdefault(
                item.url,
                PageRecord(url=item.url, status="missing", miss_reason="no_capture", source="snapshot"),
            )
        return pages
    with manifest.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            url = canon_url(row.get("url") or "")
            if url is None:
                continue
            reason = (row.get("reason") or "").strip()
            captured = (row.get("captured_at") or "").strip()
            final = canon_url(row.get("final_url") or "") or url
            name = (row.get("file") or "").strip()
            html = ""
            if name and not name.startswith("/") and ".." not in Path(name).parts:
                path = folder / name
                if path.is_file():
                    html = path.read_text(encoding="utf-8", errors="replace")
            if html:
                record = page_from_html(
                    url,
                    html,
                    source="snapshot_html",
                    captured_on=captured,
                    final_url=final,
                    miss_reason=reason,
                )
            else:
                record = PageRecord(
                    url=url,
                    final_url=final,
                    source="snapshot",
                    captured_on=captured[:10] if captured else "",
                    status="missing",
                    miss_reason=reason or "no_capture",
                )
            current = pages.get(url)
            if current is None or (record.status == "full" and current.status != "full"):
                pages[url] = record
    for item in candidates:
        pages.setdefault(item.url, PageRecord(url=item.url, status="missing", miss_reason="no_capture", source="snapshot"))
    return pages


def junk_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    if any(path.endswith(ext) for ext in _ASSET_EXT):
        return True
    return any(part in path for part in _JUNK_PARTS)


def link_is_relevant(url: str, anchor: str, query: str) -> bool:
    terms = [term for term in salient_terms(query) if len(term) >= 4]
    if not terms:
        return False
    path = urlsplit(url).path.replace("-", " ").replace("_", " ").replace("/", " ")
    haystack = f"{path} {anchor}".lower()
    return any(term in haystack for term in terms)


def queries_by_domain(candidates: list[Candidate]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for item in candidates:
        if item.position is None and item.source not in {"competitor", "project_site"}:
            continue
        host = domain_of(item.url)
        if host:
            found.setdefault(host, set()).add(item.query)
    return found


def expand_internal(
    candidates: list[Candidate],
    pages: dict[str, PageRecord],
    *,
    depth: int,
    max_internal: int,
) -> list[Candidate]:
    """Add same-domain links that share a query term, up to ``depth`` hops."""
    if depth <= 0 or max_internal <= 0:
        return []
    by_domain = queries_by_domain(candidates)
    known = {(item.url, item.query, item.source, item.snapshot_id) for item in candidates}
    added: list[Candidate] = []
    per_domain: dict[str, int] = {}
    frontier: dict[str, int] = {}
    for item in candidates:
        if item.position is not None or item.source in {"competitor", "project_site"}:
            frontier.setdefault(item.url, 0)
    for hop in range(1, depth + 1):
        sources = [url for url, hops in frontier.items() if hops == hop - 1]
        for source in sources:
            page = pages.get(source)
            if page is None:
                continue
            host = domain_of(source)
            queries = by_domain.get(host, set())
            if not queries:
                continue
            for target, anchor in page.outlinks:
                if not same_site(source, target) or junk_url(target):
                    continue
                matched = [query for query in sorted(queries) if link_is_relevant(target, anchor, query)]
                if not matched:
                    continue
                if per_domain.get(host, 0) >= max_internal and target not in frontier:
                    continue
                fresh = False
                for query in matched:
                    item = Candidate(url=target, query=query, position=None, source="internal", snapshot_id="")
                    key = (item.url, item.query, item.source, item.snapshot_id)
                    if key in known:
                        continue
                    known.add(key)
                    added.append(item)
                    fresh = True
                if target not in frontier:
                    frontier[target] = hop
                    if fresh:
                        per_domain[host] = per_domain.get(host, 0) + 1
    return added
