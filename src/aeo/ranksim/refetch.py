"""Fetch snapshot URLs that have no stored HTML, and record which method worked.

The report counts recoveries. Page bodies stay next to the report, outside git.
Serper scrape runs only when a key is present and the credit cap is above zero.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from aeo.crawl.layered import LayeredFetcher
from aeo.crawl.urls import normalize_url
from aeo.ranksim.groundtruth import load_serp_file


def _key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url.strip()


def missing_urls(snapshot: Path) -> list[str]:
    """SERP URLs whose manifest row has no HTML file. Order follows the file."""
    snapshot = Path(snapshot)
    saved: set[str] = set()
    with (snapshot / "html_manifest.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row.get("file") or "").strip():
                saved.add(_key(row.get("url") or ""))
    found: list[str] = []
    seen: set[str] = set()
    for label in load_serp_file(snapshot / "serp_serper_live.csv"):
        key = _key(label.url)
        if key in saved or key in seen:
            continue
        seen.add(key)
        found.append(label.url)
    return found


def recovery_summary(pages: list[dict]) -> dict:
    """Count how many missing URLs each method recovered."""
    methods = Counter(page.get("method") or "none" for page in pages)
    recovered = sum(count for method, count in methods.items() if method not in {"", "none"})
    by_method = {method: count for method, count in sorted(methods.items()) if method not in {"", "none"}}
    return {
        "missing": len(pages),
        "recovered": recovered,
        "still_missing": len(pages) - recovered,
        "by_method": by_method,
    }


def write_recovery(out: Path, pages: list, *, snapshot_name: str) -> dict:
    """Write the JSON report and any HTML that a method actually returned."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    html_dir = out.parent / "recovered_html"
    html_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for page in pages:
        method = page.method or "none"
        record = {
            "url": page.url,
            "method": method,
            "attempts": page.attempt_dicts(),
            "title": page.title,
            "word_count": len((page.visible_text or "").split()),
        }
        if page.method and page.html:
            name = hashlib.sha256(page.url.encode("utf-8")).hexdigest()[:16] + ".html"
            (html_dir / name).write_text(page.html, encoding="utf-8")
            record["file"] = f"recovered_html/{name}"
        records.append(record)
    report = {
        "label": "PREDICTED",
        "snapshot": snapshot_name,
        "summary": recovery_summary(records),
        "pages": records,
        "serper": "not called unless SERPER_API_KEY is set and the credit cap is above zero",
        "clicks": "Clicks and on-site behavior are not available.",
    }
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(
    snapshot: str,
    out: str,
    *,
    delay_seconds: float = 1.0,
    serper_credit_cap: int = 0,
    serper_api_key: str | None = None,
    user_agent: str = "RankSimResearchBot/0.1 (baseline evidence; contact owner)",
) -> int:
    urls = missing_urls(Path(snapshot))
    fetcher = LayeredFetcher(
        user_agent=user_agent,
        delay_seconds=delay_seconds,
        serper_api_key=serper_api_key,
        serper_credit_cap=serper_credit_cap,
        use_browser=True,
    )
    pages = []
    try:
        for url in urls:
            pages.append(fetcher.fetch(url))
    finally:
        fetcher.close()
    report = write_recovery(Path(out), pages, snapshot_name=Path(snapshot).name)
    summary = report["summary"]
    print(
        f"{summary['recovered']} of {summary['missing']} missing pages recovered "
        f"({summary['by_method'] or 'none'})."
    )
    return 0
