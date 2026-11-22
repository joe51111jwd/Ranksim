"""When did a site start? First-seen evidence from several public sources.

What is REAL here: each piece of evidence is a public fact with its source.
- The first month a site has an Open PageRank score (from the materialised
  history; see strength_lists.OpenPageRankHistory).
- The first Common Crawl domain-ranks release the site appears in (from the
  materialised releases; only releases that searched the site count).
- The registry's registration date from RDAP, for small sets, fetched slowly
  and cached on disk.

What is PREDICTED here: `best_start_date` is an estimate built from those
facts by a fixed rule. It is labelled as an estimate with an uncertainty in
days and the flags that apply. It is not a measured launch date.

Facts that limit the evidence, flagged and never hidden:
- A registration date is not a launch date. A domain can sit unused for years.
- RDAP gives the CURRENT registration. A dropped domain that was registered
  again looks new. When the registration is more than a year after the site
  was first seen, the estimate is flagged `possibly_reregistered`.
- First appearance in Common Crawl means the domain was first seen as a link
  target in a 3-crawl window, not that it was crawled or launched.
- Open PageRank only holds domains in the current (2026-10) top 10 million,
  so a first OPR month exists only for survivors (`survivorship_limited`).
- A site already present in the first snapshot a source has is
  `left_censored`: it started on or before that snapshot, no earlier bound.

Each evidence value is an `asof.Dated` whose snapshot date is when the fact
became knowable: the publication date of the OPR month column or the Common
Crawl release, and for RDAP the registration date itself (a registration on
date R is a fact from R on; a later re-registration would show a later R).

The precedence of `best_start_date` (constants below):
1. Registration R and first-seen upper bound U with R <= U and U - R <= 365
   days: the midpoint of [R, U], uncertainty half the gap (at least
   `MIN_UNCERTAINTY_DAYS`).
2. R more than 365 days after U: the domain was seen before its current
   registration. Estimate R (the current owner cannot start earlier),
   uncertainty `REGISTRATION_UNCERTAINTY_DAYS`, flag `possibly_reregistered`.
3. R after U by up to 365 days: inconsistent sources. Estimate from first
   seen (rule F below), flag `registration_after_first_seen`.
4. R more than 365 days before U: a dormant or aged domain. Estimate from
   first seen (rule F), flag `registered_long_before_first_seen`.
5. Only U, not left censored: rule F.
6. Only U and left censored: no estimate (None). The site started on or before U.
7. Only R: estimate R, uncertainty `REGISTRATION_UNCERTAINTY_DAYS`.
8. Nothing: no estimate.
Rule F. When a source also says when the site was last absent (A: the end of
the previous OPR month column, or of the previous searched Common Crawl
release), the estimate is the midpoint of [A minus `FIRST_SEEN_LAG_DAYS`, U]
with half its width as the uncertainty. Being absent from a list does not
prove a site was not live, hence the lag. Without A it is U minus
`FIRST_SEEN_LAG_DAYS`, uncertainty `FIRST_SEEN_UNCERTAINTY_DAYS`.
The lag of 90 days reflects the catalog's measured OPR lag behind registration
of about 1 to 6 months. It is a rough constant, not a fitted value.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import httpx
import pyarrow.parquet as pq

from aeo.ranksim.forecast.asof import Dated, add_months, as_date, parse_utc, utc_iso
from aeo.ranksim.forecast.sources.strength_lists import (
    CommonCrawlDomainRanks,
    OpenPageRankHistory,
    default_root,
    opr_month_published,
    record_source,
    wanted_sites,
)

RDAP_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
RDAP_BOOTSTRAP_MAX_AGE_DAYS = 7
RDAP_MIN_INTERVAL_SECONDS = 1.0
RDAP_MAX_INTERVAL_SECONDS = 30.0
RDAP_TIMEOUT_SECONDS = 30.0
RDAP_FINAL_STATUSES = frozenset({"ok", "not_found", "no_rdap_service", "no_registration_event", "bad_response"})

REREGISTERED_GAP_DAYS = 365
DORMANT_GAP_DAYS = 365
FIRST_SEEN_LAG_DAYS = 90
FIRST_SEEN_UNCERTAINTY_DAYS = 120
REGISTRATION_UNCERTAINTY_DAYS = 180
MIN_UNCERTAINTY_DAYS = 15

SOURCE_OPR = "openpagerank_first_month"
SOURCE_CC = "commoncrawl_first_release"
SOURCE_RDAP = "rdap_registration"


# ---------------------------------------------------------------------------
# RDAP


def parse_bootstrap(payload: dict[str, Any]) -> dict[str, str]:
    """IANA RDAP bootstrap (dns.json) -> {tld: base URL ending in /}. HTTPS is preferred; an HTTP-only base is upgraded."""
    out: dict[str, str] = {}
    for service in payload.get("services", []):
        if len(service) < 2:
            continue
        tlds, urls = service[0], service[1]
        https = [url for url in urls if url.lower().startswith("https://")]
        chosen = https[0] if https else ("https://" + urls[0].split("://", 1)[-1] if urls else None)
        if not chosen:
            continue
        if not chosen.endswith("/"):
            chosen += "/"
        for tld in tlds:
            out[tld.lower().strip(".")] = chosen
    return out


def rdap_url(domain: str, bootstrap: dict[str, str]) -> str | None:
    """The RDAP domain URL for a registrable domain, by the longest matching suffix in the bootstrap."""
    labels = [part for part in domain.lower().strip(".").split(".") if part]
    for index in range(1, len(labels)):
        suffix = ".".join(labels[index:])
        if suffix in bootstrap:
            return f"{bootstrap[suffix]}domain/{domain.lower()}"
    return None


def parse_rdap(payload: dict[str, Any]) -> dict[str, Any]:
    """Only the events and status of an RDAP domain answer. Entities (which can hold personal data) are dropped."""
    events: dict[str, str] = {}
    for event in payload.get("events", []) or []:
        action = str(event.get("eventAction", "")).strip().lower()
        when = event.get("eventDate")
        if action and when and action not in events:
            events[action] = str(when)
    return {
        "ldh_name": str(payload.get("ldhName") or "").lower() or None,
        "registration": events.get("registration"),
        "expiration": events.get("expiration"),
        "last_changed": events.get("last changed"),
        "rdap_status": list(payload.get("status") or []),
    }


def _interleave_by_server(sites: list[str], bootstrap: dict[str, str]) -> list[str]:
    """Sites ordered round robin across RDAP servers, so one slow registry does not hold up the others."""
    groups: dict[str, list[str]] = {}
    for site in sites:
        url = rdap_url(site, bootstrap)
        groups.setdefault(urlsplit(url).netloc if url else "", []).append(site)
    ordered: list[str] = []
    queues = [groups[key] for key in sorted(groups)]
    index = 0
    while any(queues):
        for queue in queues:
            if index < len(queue):
                ordered.append(queue[index])
        index += 1
        queues = [queue for queue in queues if index < len(queue)]
    return ordered


@dataclass
class RdapLookup:
    """Registration dates from RDAP, rate limited per host, cached on disk, resumable.

    Every site gets a record. Final statuses (ok, not_found, no_rdap_service, ...)
    are never asked again. Transient failures (rate_limited, error) are retried on
    the next run. Records live in cache/domain_age/rdap.jsonl, one JSON per line.
    """

    root: Path | None = None
    client: httpx.Client | None = None
    min_interval: float = RDAP_MIN_INTERVAL_SECONDS
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] | None = None
    _interval: dict[str, float] = field(default_factory=dict, repr=False)
    _last: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.root = default_root(self.root)

    def _wait(self, url: str) -> None:
        """Per-host spacing. A host that answered 429 is asked more slowly for the rest of the run."""
        host = urlsplit(url).netloc
        interval = self._interval.get(host, self.min_interval)
        last = self._last.get(host)
        now = time.monotonic()
        if last is not None and now - last < interval:
            self.sleep(interval - (now - last))
        self._last[host] = time.monotonic()

    def _slow_down(self, url: str, retry_after: str | None) -> None:
        host = urlsplit(url).netloc
        current = self._interval.get(host, self.min_interval)
        asked = float(retry_after) if retry_after and retry_after.strip().isdigit() else 0.0
        self._interval[host] = min(RDAP_MAX_INTERVAL_SECONDS, max(current * 2.0, asked, 1.0))

    @property
    def cache_path(self) -> Path:
        return Path(self.root) / "cache" / "domain_age" / "rdap.jsonl"

    @property
    def bootstrap_path(self) -> Path:
        return Path(self.root) / "public" / "rdap" / "dns.json"

    def _client(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(timeout=RDAP_TIMEOUT_SECONDS, follow_redirects=True,
                                       headers={"User-Agent": "ranksim-forecast-data/1.0 (research loader)",
                                                "Accept": "application/rdap+json, application/json"})
        return self.client

    def bootstrap(self) -> dict[str, str]:
        """The TLD -> RDAP server map. Refreshed when older than 7 days; the old copy is used if refresh fails."""
        path = self.bootstrap_path
        fresh = path.exists() and (time.time() - path.stat().st_mtime) < RDAP_BOOTSTRAP_MAX_AGE_DAYS * 86400
        if not fresh:
            try:
                response = self._client().get(RDAP_BOOTSTRAP_URL)
                response.raise_for_status()
                payload = response.json()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.content)
                record_source(path.parent, "dns.json", {"url": RDAP_BOOTSTRAP_URL, "fetched_at": utc_iso(datetime.now(timezone.utc)),
                                                        "publication": payload.get("publication"), "bytes": len(response.content)})
                return parse_bootstrap(payload)
            except (httpx.HTTPError, ValueError) as error:
                if not path.exists():
                    raise
                if self.log:
                    self.log(f"RDAP bootstrap refresh failed ({error}). Using the saved copy.")
        return parse_bootstrap(json.loads(path.read_text(encoding="utf-8")))

    def cached(self) -> dict[str, dict[str, Any]]:
        """The last record per site."""
        out: dict[str, dict[str, Any]] = {}
        if self.cache_path.exists():
            with open(self.cache_path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        record = json.loads(line)
                        out[record["site"]] = record
        return out

    def _append(self, record: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def fetch_one(self, site: str, bootstrap: dict[str, str]) -> dict[str, Any]:
        """One RDAP request for one site. Never raises for a network or registry failure: it returns a status."""
        base: dict[str, Any] = {"site": site, "fetched_at": utc_iso(datetime.now(timezone.utc)), "registration": None,
                                "expiration": None, "last_changed": None, "http_status": None}
        url = rdap_url(site, bootstrap)
        if url is None:
            return {**base, "status": "no_rdap_service", "url": None}
        self._wait(url)
        try:
            response = self._client().get(url)
        except httpx.HTTPError as error:
            return {**base, "status": "error", "url": url, "error": type(error).__name__}
        base.update({"url": url, "http_status": response.status_code})
        if response.status_code == 404:
            return {**base, "status": "not_found"}
        if response.status_code == 429:
            self._slow_down(url, response.headers.get("retry-after"))
            return {**base, "status": "rate_limited"}
        if response.status_code != 200:
            return {**base, "status": "error"}
        try:
            parsed = parse_rdap(response.json())
        except ValueError:
            return {**base, "status": "bad_response"}
        status = "ok" if parsed["registration"] else "no_registration_event"
        return {**base, **parsed, "status": status}

    def lookup(self, sites: Iterable[str], max_requests: int | None = None) -> dict[str, dict[str, Any]]:
        """Records for the sites. Only sites without a final cached record cause a request."""
        wanted = sorted(wanted_sites(sites))
        cache = self.cached()
        todo = [site for site in wanted if cache.get(site, {}).get("status") not in RDAP_FINAL_STATUSES]
        if max_requests is not None:
            todo = todo[:max_requests]
        if todo:
            bootstrap = self.bootstrap()
            for site in _interleave_by_server(todo, bootstrap):
                record = self.fetch_one(site, bootstrap)
                self._append(record)
                cache[site] = record
                if record["status"] == "rate_limited" and self.log:
                    self.log(f"RDAP rate limited at {record['url']}. That server is now asked more slowly.")
        return {site: cache[site] for site in wanted if site in cache}

    def registration_dates(self, sites: Iterable[str]) -> dict[str, Dated]:
        """Cached registration dates as Dated evidence (no network). Failures stay missing."""
        out: dict[str, Dated] = {}
        cache = self.cached()
        for site in wanted_sites(sites):
            record = cache.get(site)
            if not record or record.get("status") != "ok" or not record.get("registration"):
                continue
            registered = parse_utc(record["registration"]).date()
            out[site] = Dated(value={"date": registered.isoformat(), "kind": "registration",
                                     "fetched_at": record["fetched_at"], "url": record.get("url")},
                              source=SOURCE_RDAP, snapshot_date=registered)
        return out


# ---------------------------------------------------------------------------
# First seen in the materialised lists


def opr_first_month(sites: Iterable[str], root: str | Path | None = None) -> dict[str, Dated]:
    """The first Open PageRank month with a score, per site that was searched and has one."""
    opr = OpenPageRankHistory(default_root(root))
    months = opr.months()
    if not months:
        return {}
    site_set = wanted_sites(sites)
    firsts = opr.first_months(site_set)
    out: dict[str, Dated] = {}
    for site in site_set:
        first = firsts.get(site)
        if not first:
            continue
        seen_by = add_months(as_date(first), 1) - timedelta(days=1)
        index = months.index(first) if first in months else 0
        absent_by = (add_months(as_date(months[index - 1]), 1) - timedelta(days=1)).isoformat() if index > 0 else None
        out[site] = Dated(value={"date": seen_by.isoformat(), "kind": "first_seen", "first_month": first,
                                 "absent_by": absent_by, "left_censored": index == 0, "survivorship_limited": True},
                          source=SOURCE_OPR, snapshot_date=opr_month_published(first))
    return out


def cc_first_release(sites: Iterable[str], root: str | Path | None = None) -> dict[str, Dated]:
    """The first materialised Common Crawl release (by data window) that contains the site.

    Only releases whose searched list holds the site count. `left_censored` is True when the
    earliest such release already has it. `absent_in` names the last searched release without it.
    """
    cc = CommonCrawlDomainRanks(default_root(root))
    known = cc.releases()
    order = sorted(cc.materialised_releases(), key=lambda rid: known[rid]["data_end"])
    wanted = wanted_sites(sites)
    searched_before: set[str] = set()
    last_absent: dict[str, str] = {}
    out: dict[str, Dated] = {}
    for release in order:
        searched = cc.searched(release) & wanted
        parquet_path = cc.cache_dir / f"{release}.parquet"
        present = set(pq.read_table(parquet_path, columns=["site"]).column("site").to_pylist()) if parquet_path.exists() else set()
        info = known[release]
        for site in searched:
            if site in out:
                continue
            if site in present:
                absent = last_absent.get(site)
                out[site] = Dated(value={"date": info["data_end"].isoformat(), "kind": "first_seen", "release": release,
                                         "absent_in": absent,
                                         "absent_by": known[absent]["data_end"].isoformat() if absent else None,
                                         "left_censored": site not in searched_before},
                                  source=SOURCE_CC, snapshot_date=info["published"])
            else:
                last_absent[site] = release
        searched_before |= searched
    return out


def evidence(sites: Iterable[str], root: str | Path | None = None) -> dict[str, list[Dated]]:
    """All cached first-seen and registration evidence per site. No network."""
    base = default_root(root)
    site_set = wanted_sites(sites)
    out: dict[str, list[Dated]] = {site: [] for site in site_set}
    for table in (opr_first_month(site_set, base), cc_first_release(site_set, base), RdapLookup(base).registration_dates(site_set)):
        for site, dated in table.items():
            out[site].append(dated)
    return out


# ---------------------------------------------------------------------------
# The estimate


@dataclass(frozen=True)
class StartDateEstimate:
    """PREDICTED start date of a site, from REAL evidence. estimate None means no estimate can be made."""

    site: str
    estimate: date | None
    uncertainty_days: int | None
    method: str
    lower: date | None
    upper: date | None
    flags: tuple[str, ...]
    evidence: tuple[Dated, ...]
    ignored_after_as_of: tuple[str, ...] = ()
    kind: str = "PREDICTED"


def best_start_date(
    site: str,
    evidence_list: Iterable[Dated] | None = None,
    root: str | Path | None = None,
    as_of: date | str | None = None,
) -> StartDateEstimate:
    """The start-date estimate for one site by the precedence in the module docstring.

    With `as_of`, evidence whose snapshot date is later is not used and is listed in `ignored_after_as_of`.
    """
    key = next(iter(wanted_sites([site])), site)
    items = list(evidence_list) if evidence_list is not None else evidence([key], root).get(key, [])
    ignored: list[str] = []
    if as_of is not None:
        limit = as_date(as_of)
        kept = []
        for item in items:
            if item.snapshot_date is None or item.snapshot_date > limit:
                ignored.append(item.source)
            else:
                kept.append(item)
        items = kept
    flags: list[str] = []
    seen = [item for item in items if item.value.get("kind") == "first_seen"]
    registered = [item for item in items if item.value.get("kind") == "registration"]
    upper: date | None = None
    upper_censored = False
    for item in seen:
        when = as_date(item.value["date"])
        if upper is None or when < upper:
            upper = when
            upper_censored = bool(item.value.get("left_censored"))
        if item.value.get("survivorship_limited") and "survivorship_limited" not in flags:
            flags.append("survivorship_limited")
    lower = min((as_date(item.value["date"]) for item in registered), default=None)
    absent_by = max((as_date(item.value["absent_by"]) for item in seen
                     if item.value.get("absent_by") and upper is not None and as_date(item.value["absent_by"]) < upper), default=None)
    if upper_censored:
        flags.append("left_censored")
    if lower is not None:
        flags.append("registration_is_not_launch")

    def from_first_seen(bound: date) -> tuple[date, int, str]:
        if absent_by is not None:
            low = absent_by - timedelta(days=FIRST_SEEN_LAG_DAYS)
            width = (bound - low).days
            return low + timedelta(days=width // 2), max(MIN_UNCERTAINTY_DAYS, (width + 1) // 2), "between_last_absent_and_first_seen"
        return bound - timedelta(days=FIRST_SEEN_LAG_DAYS), FIRST_SEEN_UNCERTAINTY_DAYS, "first_seen_minus_lag"

    estimate: date | None
    uncertainty: int | None
    if lower is not None and upper is not None:
        gap = (upper - lower).days
        if gap < -REREGISTERED_GAP_DAYS:
            flags.append("possibly_reregistered")
            estimate, uncertainty, method = lower, REGISTRATION_UNCERTAINTY_DAYS, "registration_after_reregistration"
        elif gap < 0:
            flags.append("registration_after_first_seen")
            estimate, uncertainty, method = from_first_seen(upper)
        elif gap > DORMANT_GAP_DAYS:
            flags.append("registered_long_before_first_seen")
            estimate, uncertainty, method = from_first_seen(upper)
            if upper_censored:
                estimate, uncertainty, method = None, None, "started_on_or_before_upper"
        else:
            estimate = lower + timedelta(days=gap // 2)
            uncertainty = max(MIN_UNCERTAINTY_DAYS, (gap + 1) // 2)
            method = "between_registration_and_first_seen"
    elif upper is not None:
        if upper_censored:
            estimate, uncertainty, method = None, None, "started_on_or_before_upper"
        else:
            estimate, uncertainty, method = from_first_seen(upper)
    elif lower is not None:
        estimate, uncertainty, method = lower, REGISTRATION_UNCERTAINTY_DAYS, "registration_only"
    else:
        estimate, uncertainty, method = None, None, "no_evidence"
    return StartDateEstimate(site=key, estimate=estimate, uncertainty_days=uncertainty, method=method, lower=lower,
                             upper=upper, flags=tuple(flags), evidence=tuple(items), ignored_after_as_of=tuple(ignored))


def main(argv: list[str] | None = None) -> int:
    """rdap: look up registration dates for a small list. estimate: summarise start-date estimates (cache only)."""
    import argparse
    import sys
    from collections import Counter

    from aeo.ranksim.forecast.sources.strength_lists import read_sites_file

    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.forecast.sources.domain_age")
    sub = parser.add_subparsers(dest="command", required=True)
    rdap = sub.add_parser("rdap", help="RDAP registration dates for a small list of sites (slow, cached)")
    rdap.add_argument("--sites", required=True)
    rdap.add_argument("--max", type=int, default=200, help="at most this many requests in this run")
    rdap.add_argument("--root", default=None)
    est = sub.add_parser("estimate", help="start-date estimates from cached evidence")
    est.add_argument("--sites", required=True)
    est.add_argument("--root", default=None)
    est.add_argument("--out", default=None, help="write one JSON line per site here")
    args = parser.parse_args(argv)
    sites = read_sites_file(args.sites)
    if args.command == "rdap":
        records = RdapLookup(args.root, log=lambda message: print(message, file=sys.stderr)).lookup(sites, max_requests=args.max)
        print(json.dumps(dict(Counter(record["status"] for record in records.values())), sort_keys=True))
        return 0
    table = evidence(sites, args.root)
    methods: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    handle = open(args.out, "w", encoding="utf-8") if args.out else None
    try:
        for site in sorted(table):
            item = best_start_date(site, table[site])
            methods[item.method] += 1
            flags.update(item.flags)
            if handle:
                handle.write(json.dumps({"site": site, "estimate": item.estimate.isoformat() if item.estimate else None,
                                         "uncertainty_days": item.uncertainty_days, "method": item.method,
                                         "lower": item.lower.isoformat() if item.lower else None,
                                         "upper": item.upper.isoformat() if item.upper else None,
                                         "flags": list(item.flags), "sources": [d.source for d in item.evidence],
                                         "label": item.kind}) + "\n")
    finally:
        if handle:
            handle.close()
    print(json.dumps({"sites": len(table), "methods": dict(methods), "flags": dict(flags)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
