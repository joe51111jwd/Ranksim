"""Free off-page signals. A domain the source did not publish stays missing, not zero.

Wired without a signup key:
- Majestic Million CSV (referring subnets). Free download, CC BY 3.0.
  https://downloads.majestic.com/majestic_million.csv
- Tranco top list (popularity, not a backlink count). Free download.
  https://tranco-list.eu/top-1m.csv.zip
- Common Crawl domain PageRank, when a local ranks file is already on disk.
  The public file is free and keyless and is multiple gigabytes, so this
  client does not download it.

Needs a free signup key. Not requested here:
- Open PageRank (https://www.domcop.com/openpagerank/). Wired, off until
  OPEN_PAGERANK_API_KEY is set.
- Moz Link API (https://moz.com/products/api). Not wired.
- Majestic API beyond the Million CSV (https://developer.majestic.com/). Not wired.

Clicks, impressions, and on-site behavior are not available. Nothing here
invents them. navboost stays withheld.
"""

from __future__ import annotations

import csv
import math
import zipfile
from pathlib import Path

import httpx

OPEN_PAGERANK_URL = "https://openpagerank.com/api/v1.0/getPageRank"
MAJESTIC_MILLION_URL = "https://downloads.majestic.com/majestic_million.csv"
TRANCO_LIST_URL = "https://tranco-list.eu/top-1m.csv.zip"


def load_majestic_million(path: str | Path) -> dict[str, float]:
    """Map a domain to log1p(referring subnets). Absent domains are omitted."""
    found: dict[str, float] = {}
    with Path(path).open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        domain_key = fields.get("domain")
        subnet_key = fields.get("refsubnets") or fields.get("refsubnets".lower())
        if subnet_key is None:
            for name in reader.fieldnames:
                if name.strip().lower().replace("_", "") == "refsubnets":
                    subnet_key = name
                    break
        if not domain_key or not subnet_key:
            raise ValueError("Majestic Million CSV needs Domain and RefSubNets columns")
        for row in reader:
            domain = (row.get(domain_key) or "").strip().lower()
            raw = (row.get(subnet_key) or "").strip()
            if not domain or not raw:
                continue
            try:
                subnets = float(raw)
            except ValueError:
                continue
            found[domain] = math.log1p(max(0.0, subnets))
    return found


def load_tranco_popularity(path: str | Path) -> dict[str, float]:
    """Map a listed domain to 1/log2(rank+1). A domain that is not listed is omitted."""
    file_path = Path(path)
    if file_path.suffix == ".zip":
        with zipfile.ZipFile(file_path) as archive:
            name = archive.namelist()[0]
            lines = archive.read(name).decode("utf-8", "replace").splitlines()
    else:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    found: dict[str, float] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.lower().startswith("rank,"):
            continue
        rank_text, domain = line.split(",", 1)
        try:
            rank = int(rank_text)
        except ValueError:
            continue
        if rank <= 0:
            continue
        found[domain.strip().lower()] = 1.0 / math.log2(rank + 1.0)
    return found


def load_cc_pagerank(path: str | Path) -> dict[str, float]:
    """Common Crawl domain PageRank from a local ranks file. Missing domains are omitted."""
    from aeo.ranksim.snapshot_signals import load_cc_domain_ranks

    ranks = load_cc_domain_ranks(path)
    return {domain: float(row["pr_val"]) for domain, row in ranks.items() if "pr_val" in row}


def open_pagerank_scores(
    domains: list[str],
    api_key: str | None,
    client: httpx.Client,
) -> dict[str, float | None]:
    """Open PageRank decimal scores. No key means no request and an empty map."""
    names = [domain.strip().lower() for domain in domains if domain and domain.strip()]
    if not api_key or not names:
        return {}
    response = client.get(
        OPEN_PAGERANK_URL,
        params=[("domains[]", domain) for domain in names],
        headers={"API-OPR": api_key},
    )
    response.raise_for_status()
    payload = response.json()
    found: dict[str, float | None] = {}
    for row in payload.get("response") or []:
        domain = str(row.get("domain") or "").strip().lower()
        if not domain:
            continue
        decimal = row.get("page_rank_decimal")
        if isinstance(decimal, (int, float)) and not isinstance(decimal, bool):
            found[domain] = float(decimal)
        else:
            found[domain] = None
    return found


def signal_tables(
    *,
    majestic: dict[str, float] | None = None,
    tranco: dict[str, float] | None = None,
    cc_pagerank: dict[str, float] | None = None,
    open_pagerank: dict[str, float | None] | None = None,
) -> dict[str, dict[str, float | None]]:
    """The optional parts of the unknown-content score. Empty tables add nothing."""
    tables: dict[str, dict[str, float | None]] = {}
    if majestic:
        tables["majestic_refsubnets"] = dict(majestic)
    if tranco:
        tables["tranco_popularity"] = dict(tranco)
    if cc_pagerank:
        tables["cc_pagerank"] = dict(cc_pagerank)
    if open_pagerank:
        tables["open_pagerank"] = {key: value for key, value in open_pagerank.items() if value is not None}
    return tables
