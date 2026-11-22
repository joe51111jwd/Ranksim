"""New-site cohorts from free sources: registration-day lists, RDAP, DNS and Common Crawl.

What is REAL here: every registration day comes from the git history of a public
newly-registered-domain list (cenk/nrd, shreshta-labs/newly-registered-domains).
Registration dates, statuses and name servers come from the registries' RDAP
servers. DNS answers come from Google's public DNS-over-HTTPS service. Captures,
page counts and page text come from Common Crawl's public page index and WARC
files. Nothing in this module is PREDICTED. The launch date is an ESTIMATE made
by a stated rule from those observations, and it always carries its source and
its uncertainty in days.

Limits the audit found (docs/plan/PLAN_AUDIT.md B4 and E6, PUBLIC_DATA_CATALOG.md),
each handled in code and not only in a comment:

- A registration date is not a launch date. Median domain age at a public launch
  was 281 days. So `launch_date` comes only from the first Common Crawl capture
  of real content, never from the registration day. A site without content
  evidence has launch_date None.
- A re-registered expired domain looks new. Captures in a crawl that ended
  before the registration day set `possibly_re_registered`. An RDAP registration
  date more than REGISTRATION_MATCH_DAYS after the list day sets
  `re_registered_after_listing` (the first owner let it go). An RDAP date more
  than REGISTRATION_MATCH_DAYS before the list day sets
  `registration_older_than_list` (the list did not see a new registration).
- The rebrand trap. A "new" domain can be an old site's new name. Phrases such
  as "formerly" on the launch page, or a site that is already large in its first
  crawl, set `possible_rebrand`.
- Parked domains. Parking or marketplace name servers, parking text on the
  page, or redirects to a domain marketplace set `parked`.
- Survivorship. Dead and never-crawled sites STAY in the cohort table with
  their flags. The funnel counts every stage. Missing evidence stays None.
- Coverage. cenk/nrd history starts 2025-08-14. shreshta has history from
  2024-01-31, but it is a 10,000-domain sample that is mostly .se, .sk, .ee and
  .nu from July 2024 on, and those TLDs have no free RDAP. `REPOS` records the
  first day each list covers, and a request outside it is refused.
- Current versus past. RDAP and DNS describe the domain TODAY. They are stored
  with the UTC time they were read, used only for flags and the funnel, and
  never as launch-month features.

Personal data is kept out. Only RDAP events, statuses and name servers are
read. Registrant names, emails, phone numbers and user names are never stored.
Stored page text is scrubbed of email addresses and phone numbers.

Downloads go under data/forecast/public/nrd/<list>/ and
data/forecast/public/commoncrawl/. Derived tables go under
data/forecast/cache/newsites/. Every fetch is resumable: a file or a per-domain
answer already on disk is not fetched again. Every host is called one request
at a time with a minimum gap.

CLI: python -m aeo.ranksim.forecast.sources.newsites nrd|cohort ...
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import zlib
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import httpx

from ...site_history import site_key
from ..asof import Dated, parse_utc, utc_iso
from ..config import EXCLUDED_SITES, forecast_paths

# ----------------------------------------------------------------------------------------------
# Constants. Every threshold is named once.
# ----------------------------------------------------------------------------------------------

USER_AGENT = "ranksim-research/1.0 (+public data, polite)"
HTTP_TIMEOUT_SECONDS = 60.0
RAW_GITHUB = "https://raw.githubusercontent.com"
CC_DATA = "https://data.commoncrawl.org"
CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"
RDAP_BOOTSTRAP = "https://rdap.org/domain/"
RDAP_VERISIGN = {"com": "https://rdap.verisign.com/com/v1/", "net": "https://rdap.verisign.com/net/v1/"}
DOH_URL = "https://dns.google/resolve"

# Minimum seconds between two requests to one host. Polite on every host.
MIN_GAP_GITHUB = 1.0
MIN_GAP_RDAP = 0.5
MIN_GAP_DOH = 0.1
MIN_GAP_CC = 0.05
RETRIES = 3
BACKOFF_SECONDS = 10.0

CAP_SALT = "ranksim-nrd-cap-v1"
DEFAULT_CAP_PER_DAY = 100
DEFAULT_TLDS = ("com",)

# A registration date from RDAP within this many days of the list day is the same registration.
REGISTRATION_MATCH_DAYS = 10
# Distinct HTML pages captured in the first crawl with content above which a "new" site looks like a moved old site.
REBRAND_MIN_PAGES_FIRST_CRAWL = 100
# Stored page text is cut to this many characters after scrubbing.
PAGE_TEXT_MAX_CHARS = 5000
# A home page with fewer visible characters than this is not counted as real content.
MIN_CONTENT_CHARS = 200
CLUSTER_WINDOW = 16384
CLUSTER_MARGIN = 4 * CLUSTER_WINDOW

# Parking and domain-marketplace name servers. OUR LIST: the services named in the catalogs
# (namefind, sedoparking, bodis) plus widely documented parking and for-sale services.
PARKING_NS_SUFFIXES: tuple[str, ...] = (
    "sedoparking.com",
    "parkingcrew.net",
    "bodis.com",
    "above.com",
    "abovedomains.com",
    "afternic.com",
    "dan.com",
    "undeveloped.com",
    "parklogic.com",
    "dns-parking.com",
    "cashparking.com",
    "smartname.com",
    "namefind.com",
    "uniregistrymarket.link",
    "parked.com",
    "voodoo.com",
    "domainmarket.com",
    "hugedomains.com",
    "brandbucket.com",
    "squadhelp.com",
    "atom.com",
    "sav.com",
    "efty.com",
    "parkweb.co",
    "dns-expired.com",
)
MARKETPLACE_HOSTS: tuple[str, ...] = (
    "sedo.com",
    "afternic.com",
    "dan.com",
    "hugedomains.com",
    "godaddy.com",
    "brandbucket.com",
    "squadhelp.com",
    "atom.com",
    "undeveloped.com",
    "efty.com",
    "domainmarket.com",
    "sav.com",
    "namecheap.com",
    "porkbun.com",
    "spaceship.com",
    "bodis.com",
    "parkingcrew.net",
)
PARKING_PHRASES: tuple[str, ...] = (
    "this domain is for sale",
    "domain is for sale",
    "buy this domain",
    "this domain may be for sale",
    "make an offer",
    "domain parking",
    "parked free",
    "parked domain",
    "this domain has been registered",
    "domain registered",
    "coming soon",
    "under construction",
    "website coming soon",
    "future home of",
    "is parked",
    "related searches",
    "inquire about this domain",
    "get this domain",
    "index of /",
    "default web site page",
    "welcome to nginx",
    "apache2 default page",
    "it works!",
    "account suspended",
)
REBRAND_PHRASES: tuple[str, ...] = (
    "formerly known as",
    "formerly",
    "previously known as",
    "our new name",
    "has a new name",
    "we have rebranded",
    "we've rebranded",
    "rebranded",
    "is now part of",
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().\-]{7,}\d)(?!\w)")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_public(source: str) -> Path:
    return forecast_paths().root / "public" / source


def default_cache() -> Path:
    return forecast_paths().cache / "newsites"


def make_client(timeout: float = HTTP_TIMEOUT_SECONDS) -> httpx.Client:
    """A sync httpx client that follows redirects and verifies TLS (the proxy CA comes from SSL_CERT_FILE)."""
    return httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT})


class Polite:
    """One request at a time per host, at least `gap` seconds apart. Thread safe."""

    def __init__(self, gap: float, *, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.gap = float(gap)
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last is not None and now - self._last < self.gap:
                self._sleep(self.gap - (now - self._last))
            self._last = self._clock()


def _get(http: httpx.Client, url: str, polite: Polite, *, headers: Mapping[str, str] | None = None,
         sleep: Callable[[float], None] = time.sleep, ok_statuses: Iterable[int] = (200, 206)) -> httpx.Response:
    """GET with polite spacing. Retries a dropped connection, 429 and 5xx. Other statuses are returned as they are."""
    wanted = set(ok_statuses)
    last: str = ""
    for attempt in range(RETRIES + 1):
        polite.wait()
        try:
            response = http.get(url, headers=dict(headers or {}))
        except httpx.TransportError as error:
            last = type(error).__name__
        else:
            if response.status_code in wanted or (response.status_code < 500 and response.status_code != 429):
                return response
            last = f"HTTP {response.status_code}"
        if attempt < RETRIES:
            sleep(BACKOFF_SECONDS * (attempt + 1))
    raise ConnectionError(f"{url} did not answer after {RETRIES + 1} tries ({last})")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record_source(directory: Path, key: str, entry: Mapping[str, Any]) -> None:
    """SOURCES.json beside downloads: url, fetch time and sha256 of what was read."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "SOURCES.json"
    payload: dict[str, Any] = {}
    if target.is_file():
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except ValueError:
            payload = {}
    files = payload.setdefault("files", {})
    files[key] = dict(entry)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


def _write_gz_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(data, mtime=0))
    os.replace(tmp, path)


class JsonlCache:
    """Append-only per-key JSON lines. The last line for a key wins. Used to resume per-domain lookups."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and "key" in record:
                    self._data[str(record["key"])] = record

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def put(self, key: str, record: Mapping[str, Any]) -> dict[str, Any]:
        row = {"key": key, **record}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            self._data[key] = row
        return row


# ----------------------------------------------------------------------------------------------
# Registration-day lists from git history.
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NrdRepo:
    """One public newly-registered-domain list kept in git. `first_day` is the first day its history covers."""

    key: str
    repo: str
    path: str
    first_day: date
    note: str

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.repo}"

    def raw_url(self, sha: str) -> str:
        return f"{RAW_GITHUB}/{self.repo}/{sha}/{self.path}"


REPOS: dict[str, NrdRepo] = {
    "cenk": NrdRepo(
        "cenk",
        "cenk/nrd",
        "nrd-last-10-days.txt",
        date(2025, 8, 14),
        "WhoisDS free feed, about 70,000 domains a day, all TLDs. History starts 2025-08-14 (older history was squashed).",
    ),
    "shreshta": NrdRepo(
        "shreshta",
        "shreshta-labs/newly-registered-domains",
        "nrd-1w.csv",
        date(2024, 1, 31),
        "10,000-domain weekly sample. Mostly .com until June 2024, then mostly .se, .sk, .ee and .nu (no free RDAP).",
    ),
}


@dataclass(frozen=True)
class CommitDay:
    """The last commit of one UTC day in a list's history."""

    day: date
    sha: str
    committed_at: str


@dataclass(frozen=True)
class DailyList:
    """Domains that first appear in a list on `day` (the diff against the previous day's last commit)."""

    repo: str
    day: date
    sha: str
    previous_sha: str
    list_updated_at: str | None
    total_new: int
    by_tld: dict[str, int]
    path: str


@dataclass(frozen=True)
class Registration:
    """A domain and the day a public list first showed it. REAL. `registered_on` is None until RDAP confirms it."""

    site: str
    listed_on: date
    source: str
    tld: str
    registered_on: date | None = None


def _run_git(args: Sequence[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(["git", "-c", "gc.auto=0", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout


def list_commit_days(repo: NrdRepo, git_dir: Path, *, refresh: bool = False, clone_url: str | None = None) -> list[CommitDay]:
    """The last commit of each UTC day, oldest first. A blobless bare clone holds only commits and trees.

    The clone is made once and kept, so the commit list is pinned until refresh=True.
    """
    if not git_dir.exists():
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "-q", "--bare", "--filter=blob:none", clone_url or repo.clone_url, str(git_dir)])
    elif refresh:
        _run_git(["--git-dir", str(git_dir), "fetch", "-q", "origin", "+refs/heads/*:refs/heads/*"])
    lines = _run_git(["--git-dir", str(git_dir), "log", "--format=%H %cI", "HEAD", "--", repo.path]).splitlines()
    last_of_day: dict[date, CommitDay] = {}
    for line in lines:
        sha, _, stamp = line.strip().partition(" ")
        if not sha or not stamp:
            continue
        moment = parse_utc(stamp)
        day = moment.date()
        current = last_of_day.get(day)
        if current is None or utc_iso(moment) > current.committed_at:
            last_of_day[day] = CommitDay(day, sha, utc_iso(moment))
    return [last_of_day[day] for day in sorted(last_of_day)]


def parse_list(text: str) -> tuple[set[str], str | None]:
    """Domains of one list file and its "Last update" time. Comment lines start with #. Domains are lower case."""
    domains: set[str] = set()
    updated: str | None = None
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("#"):
            match = re.search(r"Last update:\s*(.+?)\s*UTC", value)
            if match:
                try:
                    updated = utc_iso(datetime.strptime(match.group(1), "%d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc))
                except ValueError:
                    updated = None
            continue
        domain = value.split(",")[0].strip().lower().rstrip(".")
        if domain and "." in domain and " " not in domain:
            domains.add(domain)
    return domains, updated


class NrdFetcher:
    """Builds daily registration lists from a list's git history. Resumable and polite.

    `mode` "raw" reads each version through raw.githubusercontent.com. `mode`
    "git" reads blobs from the blobless clone on demand (git fetches each blob
    once). Both save the file gzipped under raw/<sha>/ before diffing it.
    """

    def __init__(
        self,
        repo: NrdRepo,
        *,
        root: Path | None = None,
        http: httpx.Client | None = None,
        mode: str = "raw",
        sleep: Callable[[float], None] = time.sleep,
        clone_url: str | None = None,
        polite: Polite | None = None,
    ) -> None:
        if mode not in ("raw", "git"):
            raise ValueError("mode must be raw or git")
        self.repo = repo
        self.root = Path(root) if root else default_public("nrd") / repo.key
        self.http = http
        self.mode = mode
        self._sleep = sleep
        self.clone_url = clone_url
        self.polite = polite or Polite(MIN_GAP_GITHUB, sleep=sleep)
        self.bytes_downloaded = 0

    @property
    def git_dir(self) -> Path:
        return self.root / "repo.git"

    def commit_days(self, *, refresh: bool = False) -> list[CommitDay]:
        return list_commit_days(self.repo, self.git_dir, refresh=refresh, clone_url=self.clone_url)

    def _raw_path(self, sha: str) -> Path:
        return self.root / "raw" / sha / (self.repo.path + ".gz")

    def file_text(self, sha: str) -> str:
        """The list file at commit `sha`, from disk when already fetched."""
        target = self._raw_path(sha)
        if target.is_file():
            return gzip.decompress(target.read_bytes()).decode("utf-8", errors="replace")
        if self.mode == "git":
            content = subprocess.run(
                ["git", "-c", "gc.auto=0", "--git-dir", str(self.git_dir), "show", f"{sha}:{self.repo.path}"],
                check=True, capture_output=True,
            ).stdout
            url = f"git:{self.repo.clone_url}@{sha}:{self.repo.path}"
        else:
            if self.http is None:
                raise ValueError("mode raw needs an http client")
            url = self.repo.raw_url(sha)
            response = _get(self.http, url, self.polite, sleep=self._sleep)
            if response.status_code != 200:
                raise ConnectionError(f"{url} answered HTTP {response.status_code}")
            content = response.content
        self.bytes_downloaded += len(content)
        _write_gz_atomic(target, content)
        _record_source(self.root, f"raw/{sha}/{self.repo.path}", {
            "url": url, "fetched_at": utc_iso(_utcnow()), "sha256": _sha256_bytes(content), "bytes": len(content),
        })
        return content.decode("utf-8", errors="replace")

    def daily_lists(self, start: date, end: date, *, refresh: bool = False) -> tuple[list[DailyList], list[dict[str, Any]]]:
        """One DailyList per day from start to end (inclusive). Days without a commit on the day before are skipped and reported."""
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        if start <= self.repo.first_day:
            raise ValueError(
                f"{self.repo.repo} history starts {self.repo.first_day}. A daily diff needs the day before, so start after it."
            )
        days = {item.day: item for item in self.commit_days(refresh=refresh)}
        made: list[DailyList] = []
        skipped: list[dict[str, Any]] = []
        day = start
        while day <= end:
            today, before = days.get(day), days.get(day - timedelta(days=1))
            if today is None or before is None:
                skipped.append({"day": day.isoformat(), "reason": "no commit on this day" if today is None else "no commit on the day before"})
            else:
                made.append(self._daily(today, before))
            day += timedelta(days=1)
        return made, skipped

    def _daily(self, today: CommitDay, before: CommitDay) -> DailyList:
        out = self.root / "daily" / f"{today.day.isoformat()}.txt.gz"
        meta = out.with_name(out.name.replace(".txt.gz", ".json"))
        if out.is_file() and meta.is_file():
            info = json.loads(meta.read_text(encoding="utf-8"))
            if info.get("sha") == today.sha and info.get("previous_sha") == before.sha:
                return DailyList(**{**info, "day": date.fromisoformat(info["day"])})
        current, updated = parse_list(self.file_text(today.sha))
        previous, _ = parse_list(self.file_text(before.sha))
        new = sorted(current - previous)
        by_tld = Counter(domain.rsplit(".", 1)[-1] for domain in new)
        _write_gz_atomic(out, ("\n".join(new) + "\n").encode("utf-8"))
        daily = DailyList(
            repo=self.repo.repo, day=today.day, sha=today.sha, previous_sha=before.sha, list_updated_at=updated,
            total_new=len(new), by_tld=dict(sorted(by_tld.items())), path=str(out),
        )
        meta.write_text(json.dumps({**asdict(daily), "day": daily.day.isoformat()}, indent=2, sort_keys=True), encoding="utf-8")
        return daily


def cap_order(domain: str) -> str:
    """The frozen order used to cap a day's list: sha256 of a fixed salt and the domain."""
    return hashlib.sha256(f"{CAP_SALT}|{domain}".encode("utf-8")).hexdigest()


def registrations_from(daily: Iterable[DailyList], *, tlds: Sequence[str] | None = DEFAULT_TLDS,
                       cap_per_day: int | None = DEFAULT_CAP_PER_DAY) -> list[Registration]:
    """Registrations of the given days, filtered by TLD and capped per day in the frozen cap order. Excluded sites are dropped."""
    wanted = {tld.lower().lstrip(".") for tld in tlds} if tlds else None
    out: list[Registration] = []
    for item in daily:
        domains = gzip.decompress(Path(item.path).read_bytes()).decode("utf-8").split()
        chosen = [d for d in domains if (wanted is None or d.rsplit(".", 1)[-1] in wanted)]
        chosen = [d for d in chosen if site_key(d) == d and d not in EXCLUDED_SITES]
        chosen.sort(key=cap_order)
        if cap_per_day is not None:
            chosen = chosen[:cap_per_day]
        out.extend(Registration(site=d, listed_on=item.day, source=item.repo, tld=d.rsplit(".", 1)[-1]) for d in sorted(chosen))
    return out


# ----------------------------------------------------------------------------------------------
# RDAP and DNS (current state, read today).
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RdapRecord:
    """What RDAP says about a domain TODAY. Only events, statuses and name servers. No entities, no contacts."""

    site: str
    checked_at: str
    status: str  # found, not_found, no_rdap, error
    http_status: int | None
    registration: str | None
    expiration: str | None
    last_changed: str | None
    statuses: tuple[str, ...]
    nameservers: tuple[str, ...]


def parse_rdap(site: str, payload: Mapping[str, Any], *, checked_at: str, http_status: int | None) -> RdapRecord:
    """Keep only events, statuses and name servers from an RDAP domain answer."""
    events: dict[str, str] = {}
    for event in payload.get("events") or []:
        if isinstance(event, Mapping) and event.get("eventAction") and event.get("eventDate"):
            action = str(event["eventAction"]).lower()
            try:
                events.setdefault(action, utc_iso(parse_utc(str(event["eventDate"]))))
            except ValueError:
                continue
    servers = []
    for server in payload.get("nameservers") or []:
        if isinstance(server, Mapping) and server.get("ldhName"):
            servers.append(str(server["ldhName"]).lower().rstrip("."))
    statuses = tuple(sorted(str(item) for item in payload.get("status") or [] if isinstance(item, str)))
    return RdapRecord(site, checked_at, "found", http_status, events.get("registration"), events.get("expiration"),
                      events.get("last changed"), statuses, tuple(sorted(set(servers))))


class RdapLookup:
    """RDAP registration dates, one domain at a time, cached per domain. .com and .net go to Verisign directly."""

    def __init__(self, http: httpx.Client, cache_path: Path, *, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime] = _utcnow, bootstrap: Mapping[str, str] | None = None,
                 polite: Polite | None = None) -> None:
        self.http = http
        self.cache = JsonlCache(cache_path)
        self._sleep = sleep
        self._now = now
        self.bootstrap = dict(bootstrap or RDAP_VERISIGN)
        self.polite = polite or Polite(MIN_GAP_RDAP, sleep=sleep)

    def url_for(self, site: str) -> str:
        tld = site.rsplit(".", 1)[-1]
        base = self.bootstrap.get(tld)
        return f"{base.rstrip('/')}/domain/{site}" if base else f"{RDAP_BOOTSTRAP}{site}"

    def lookup(self, site: str) -> RdapRecord:
        cached = self.cache.get(site)
        if cached is not None:
            return _rdap_from(cached)
        checked = utc_iso(self._now())
        try:
            response = _get(self.http, self.url_for(site), self.polite, headers={"Accept": "application/rdap+json"},
                            sleep=self._sleep, ok_statuses=(200, 404))
        except ConnectionError:
            return RdapRecord(site, checked, "error", None, None, None, None, (), ())  # not cached: retried next run
        if response.status_code == 404:
            body = response.text.lower()
            status = "no_rdap" if "no rdap" in body else "not_found"
            record = RdapRecord(site, checked, status, 404, None, None, None, (), ())
        elif response.status_code == 200:
            try:
                record = parse_rdap(site, response.json(), checked_at=checked, http_status=200)
            except ValueError:
                record = RdapRecord(site, checked, "error", 200, None, None, None, (), ())
        else:
            record = RdapRecord(site, checked, "error", response.status_code, None, None, None, (), ())
        if record.status != "error":
            self.cache.put(site, asdict(record))
        return record


def _rdap_from(row: Mapping[str, Any]) -> RdapRecord:
    return RdapRecord(
        site=row["site"], checked_at=row["checked_at"], status=row["status"], http_status=row.get("http_status"),
        registration=row.get("registration"), expiration=row.get("expiration"), last_changed=row.get("last_changed"),
        statuses=tuple(row.get("statuses") or ()), nameservers=tuple(row.get("nameservers") or ()),
    )


@dataclass(frozen=True)
class DnsRecord:
    """Whether a domain resolves to an address TODAY (DNS-over-HTTPS). `rcode` 0 is NOERROR, 3 is NXDOMAIN."""

    site: str
    checked_at: str
    rcode: int | None
    addresses: int | None
    resolves: bool | None


class DnsLookup:
    """A-record lookups through Google public DNS-over-HTTPS, cached per domain."""

    def __init__(self, http: httpx.Client, cache_path: Path, *, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime] = _utcnow, polite: Polite | None = None) -> None:
        self.http = http
        self.cache = JsonlCache(cache_path)
        self._sleep = sleep
        self._now = now
        self.polite = polite or Polite(MIN_GAP_DOH, sleep=sleep)

    def lookup(self, site: str) -> DnsRecord:
        cached = self.cache.get(site)
        if cached is not None:
            return DnsRecord(cached["site"], cached["checked_at"], cached.get("rcode"), cached.get("addresses"), cached.get("resolves"))
        checked = utc_iso(self._now())
        try:
            response = _get(self.http, f"{DOH_URL}?name={site}&type=A", self.polite,
                            headers={"Accept": "application/dns-json"}, sleep=self._sleep)
            payload = response.json() if response.status_code == 200 else None
        except (ConnectionError, ValueError):
            payload = None
        if not isinstance(payload, Mapping):
            return DnsRecord(site, checked, None, None, None)
        rcode = payload.get("Status") if isinstance(payload.get("Status"), int) else None
        answers = [a for a in payload.get("Answer") or [] if isinstance(a, Mapping) and a.get("type") == 1]
        record = DnsRecord(site, checked, rcode, len(answers), bool(rcode == 0 and answers))
        self.cache.put(site, asdict(record))
        return record


def parking_nameservers(nameservers: Sequence[str]) -> bool | None:
    """True when any name server belongs to a parking or marketplace service. None when there are none to judge."""
    if not nameservers:
        return None
    return any(ns == suffix or ns.endswith("." + suffix) for ns in nameservers for suffix in PARKING_NS_SUFFIXES)


# ----------------------------------------------------------------------------------------------
# Common Crawl: the page index (cluster.idx plus CDX blocks) and WARC records.
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Crawl:
    """One Common Crawl crawl and the UTC window it ran in."""

    crawl_id: str
    start: datetime
    end: datetime


def load_crawls(path: Path | None = None, *, http: httpx.Client | None = None) -> dict[str, Crawl]:
    """Crawls from collinfo.json (data/forecast/public/commoncrawl/collinfo.json, fetched once when missing)."""
    target = path or default_public("commoncrawl") / "collinfo.json"
    if not target.is_file():
        if http is None:
            raise FileNotFoundError(f"{target} is missing and no http client was given to fetch it")
        response = _get(http, CC_COLLINFO, Polite(MIN_GAP_CC))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        _record_source(target.parent, target.name, {"url": CC_COLLINFO, "fetched_at": utc_iso(_utcnow()),
                                                    "sha256": _sha256_bytes(response.content)})
    out: dict[str, Crawl] = {}
    for item in json.loads(target.read_text(encoding="utf-8")):
        try:
            out[item["id"]] = Crawl(item["id"], parse_utc(item["from"]), parse_utc(item["to"]))
        except (KeyError, ValueError):
            continue
    return out


def surt_prefix(site: str) -> str:
    """The SURT form of a registrable domain without the closing bracket: example.com becomes com,example."""
    return ",".join(reversed(site.lower().strip(".").split(".")))


@dataclass(frozen=True)
class ClusterLine:
    key: str
    cdx_file: str
    offset: int
    length: int


@dataclass
class CdxSummary:
    """What one crawl captured for one registrable domain (any host under it)."""

    crawl: str
    site: str
    captures: int = 0
    html_200: int = 0
    pages_200: int = 0
    first_capture: str | None = None
    first_html_200: str | None = None
    statuses: dict[str, int] = field(default_factory=dict)
    offsite_redirects: list[str] = field(default_factory=list)
    marketplace_redirect: bool = False
    home: dict[str, Any] | None = None


def _cdx_time(stamp: str) -> str | None:
    try:
        return utc_iso(datetime.strptime(stamp[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc))
    except ValueError:
        return None


def summarize_cdx(crawl: str, site: str, lines: Iterable[str]) -> CdxSummary:
    """Fold the CDX lines of one domain in one crawl into counts, the first capture and the home page record."""
    summary = CdxSummary(crawl=crawl, site=site)
    pages: set[str] = set()
    offsite: set[str] = set()
    best: tuple[int, str, str] | None = None
    for line in lines:
        surt, _, rest = line.partition(" ")
        stamp, _, blob = rest.partition(" ")
        try:
            record = json.loads(blob)
        except ValueError:
            continue
        summary.captures += 1
        status = str(record.get("status") or "")
        summary.statuses[status] = summary.statuses.get(status, 0) + 1
        moment = _cdx_time(stamp)
        if moment and (summary.first_capture is None or moment < summary.first_capture):
            summary.first_capture = moment
        mime = f"{record.get('mime') or ''} {record.get('mime-detected') or ''}".lower()
        if status.startswith("3") and record.get("redirect"):
            target = site_key(str(record["redirect"]))
            if target and target != site:
                offsite.add(target)
                if any(target == host or target.endswith("." + host) for host in MARKETPLACE_HOSTS):
                    summary.marketplace_redirect = True
        if status == "200" and "html" in mime:
            summary.html_200 += 1
            url = str(record.get("url") or "")
            pages.add(url.split("#")[0])
            if moment and (summary.first_html_200 is None or moment < summary.first_html_200):
                summary.first_html_200 = moment
            path = surt.split(")", 1)[1] if ")" in surt else surt
            host_part = surt.split(")", 1)[0]
            rank = 0 if path in ("/", "") and host_part == surt_prefix(site) else (1 if path in ("/", "") else 2)
            candidate = (rank, moment or "", url)
            if best is None or candidate < best:
                best = candidate
                summary.home = {
                    "url": url, "captured_at": moment, "filename": record.get("filename"),
                    "offset": int(record.get("offset") or 0), "length": int(record.get("length") or 0),
                    "charset": record.get("charset"), "languages": record.get("languages"),
                }
    summary.pages_200 = len(pages)
    summary.offsite_redirects = sorted(offsite)
    return summary


class CommonCrawlIndex:
    """Point lookups in Common Crawl's page index without the CDX server.

    For one crawl it downloads only the slice of cluster.idx that covers the
    requested domains (found by a binary search with range reads), then
    range-reads one CDX block per group of domains, and keeps a per-domain
    summary. Blocks are not stored. A domain whose summary is on disk is not
    looked up again.
    """

    def __init__(self, http: httpx.Client, *, public_root: Path | None = None, cache_root: Path | None = None,
                 sleep: Callable[[float], None] = time.sleep, polite: Polite | None = None, base: str = CC_DATA) -> None:
        self.http = http
        self.public_root = Path(public_root) if public_root else default_public("commoncrawl")
        self.cache_root = Path(cache_root) if cache_root else default_cache() / "cc"
        self._sleep = sleep
        self.polite = polite or Polite(MIN_GAP_CC, sleep=sleep)
        self.base = base.rstrip("/")
        self.bytes_downloaded = 0
        self.requests = 0

    def _cluster_url(self, crawl: str) -> str:
        return f"{self.base}/cc-index/collections/{crawl}/indexes/cluster.idx"

    def _range(self, url: str, start: int, end: int) -> bytes:
        response = _get(self.http, url, self.polite, headers={"Range": f"bytes={start}-{end}"}, sleep=self._sleep)
        self.requests += 1
        if response.status_code not in (200, 206):
            raise ConnectionError(f"{url} answered HTTP {response.status_code} for bytes {start}-{end}")
        data = response.content
        if response.status_code == 200 and (start > 0 or len(data) > end - start + 1):
            data = data[start : end + 1]  # a server that ignores Range sends everything
        self.bytes_downloaded += len(data)
        return data

    def _size(self, url: str) -> int:
        response = _get(self.http, url, self.polite, headers={"Range": "bytes=0-0"}, sleep=self._sleep)
        self.requests += 1
        total = response.headers.get("content-range", "").rpartition("/")[2]
        if total.isdigit():
            return int(total)
        if response.status_code == 200:
            return len(response.content)
        raise ConnectionError(f"{url} gave no size")

    def _first_key_after(self, url: str, offset: int, size: int) -> str | None:
        data = self._range(url, offset, min(size - 1, offset + CLUSTER_WINDOW - 1))
        text = data.decode("utf-8", errors="replace")
        if offset > 0:
            cut = text.find("\n")
            if cut < 0:
                return None
            text = text[cut + 1 :]
        line = text.split("\n", 1)[0]
        return line if line and "\t" in line else None

    def _find_offset(self, url: str, size: int, target: str) -> int:
        """A byte offset at or before the last cluster line whose key is below `target`."""
        lo, hi = 0, size
        while hi - lo > CLUSTER_WINDOW:
            mid = (lo + hi) // 2
            key = self._first_key_after(url, mid, size)
            if key is None or key.split("\t", 1)[0] >= target:
                hi = mid
            else:
                lo = mid
        return lo

    @staticmethod
    def _covering_slice(folder: Path, low: str, high: str) -> Path | None:
        """A slice already on disk whose key range covers [low, high], so a resumed run downloads nothing new."""
        sources = folder / "SOURCES.json"
        if not sources.is_file():
            return None
        try:
            files = json.loads(sources.read_text(encoding="utf-8")).get("files", {})
        except ValueError:
            return None
        for name, entry in sorted(files.items()):
            path = folder / name
            if path.is_file() and str(entry.get("low", "\uffff")) <= low and str(entry.get("high", "")) >= high:
                return path
        return None

    def cluster_slice(self, crawl: str, low: str, high: str) -> list[ClusterLine]:
        """Cluster lines covering keys from `low` to `high`, cached under public/commoncrawl/cluster/<crawl>/."""
        folder = self.public_root / "cluster" / crawl
        name = hashlib.sha256(f"{low}|{high}".encode()).hexdigest()[:16]
        target = folder / f"slice-{name}.idx.gz"
        covering = self._covering_slice(folder, low, high)
        if covering is not None:
            target = covering
        if target.is_file():
            text = gzip.decompress(target.read_bytes()).decode("utf-8", errors="replace")
        else:
            url = self._cluster_url(crawl)
            size = self._size(url)
            start = max(0, self._find_offset(url, size, low) - CLUSTER_MARGIN)
            end = min(size - 1, self._find_offset(url, size, high) + CLUSTER_WINDOW + CLUSTER_MARGIN)
            data = self._range(url, start, end)
            text = data.decode("utf-8", errors="replace")
            lines = text.split("\n")
            if start > 0:
                lines = lines[1:]
            if end < size - 1:
                lines = lines[:-1]
            text = "\n".join(lines)
            _write_gz_atomic(target, text.encode("utf-8"))
            _record_source(folder, target.name, {"url": url, "bytes": f"{start}-{end}", "low": low, "high": high,
                                                 "fetched_at": utc_iso(_utcnow()), "sha256": _sha256_bytes(text.encode("utf-8"))})
        out: list[ClusterLine] = []
        for line in text.split("\n"):
            parts = line.split("\t")
            if len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit():
                out.append(ClusterLine(parts[0], parts[1], int(parts[2]), int(parts[3])))
        out.sort(key=lambda item: item.key)
        return out

    def lookup(self, crawl: str, sites: Sequence[str], *, log: Callable[[str], None] | None = None) -> dict[str, CdxSummary]:
        """A CdxSummary per site for one crawl. Sites already summarised on disk are read back, not fetched."""
        cache = JsonlCache(self.cache_root / f"{crawl}.jsonl")
        out: dict[str, CdxSummary] = {}
        todo = []
        for site in sorted(set(sites)):
            cached = cache.get(site)
            if cached is not None:
                out[site] = CdxSummary(**{k: v for k, v in cached.items() if k != "key"})
            else:
                todo.append(site)
        if not todo:
            return out
        prefixes = {site: surt_prefix(site) for site in todo}
        lines = self.cluster_slice(crawl, min(prefixes.values()) + ")", max(prefixes.values()) + "-")
        keys = [line.key for line in lines]
        groups: dict[tuple[int, ...], list[str]] = {}
        for site, prefix in prefixes.items():
            low, high = prefix + ")", prefix + "-"
            first = max(0, bisect.bisect_right(keys, low) - 1)
            blocks = [first]
            nxt = first + 1
            while nxt < len(keys) and keys[nxt] < high:
                blocks.append(nxt)
                nxt += 1
            groups.setdefault(tuple(blocks), []).append(site)
        done = 0
        for blocks, members in sorted(groups.items()):
            found: dict[str, list[str]] = {site: [] for site in members}
            for index in blocks:
                block = lines[index]
                url = f"{self.base}/cc-index/collections/{crawl}/indexes/{block.cdx_file}"
                text = gzip.decompress(self._range(url, block.offset, block.offset + block.length - 1)).decode("utf-8", errors="replace")
                for row in text.split("\n"):
                    for site in members:
                        prefix = prefixes[site]
                        if row.startswith(prefix + ")") or row.startswith(prefix + ","):
                            found[site].append(row)
            for site in members:
                summary = summarize_cdx(crawl, site, found[site])
                cache.put(site, asdict(summary))
                out[site] = summary
            done += len(members)
            if log and done % 200 < len(members):
                log(f"{crawl}: {done}/{len(todo)} domains looked up, {self.bytes_downloaded / 1e6:.0f} MB so far")
        return out


# ----------------------------------------------------------------------------------------------
# Page text from a WARC record.
# ----------------------------------------------------------------------------------------------


class _TextParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.meta_description: str | None = None
        self.headings: dict[str, list[str]] = {"h1": [], "h2": [], "h3": []}
        self.text: list[str] = []
        self._stack: list[str] = []
        self._skip = 0
        self._heading: str | None = None
        self._heading_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self._skip += 1
        if tag == "meta":
            values = {k.lower(): (v or "") for k, v in attrs}
            if values.get("name", "").lower() == "description" and self.meta_description is None:
                self.meta_description = values.get("content", "").strip() or None
        if tag in self.headings:
            self._heading, self._heading_buf = tag, []
        self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == self._heading:
            text = " ".join("".join(self._heading_buf).split())
            if text:
                self.headings[tag].append(text)
            self._heading = None
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._stack and self._stack[-1] == "title":
            self.title.append(data)
            return
        if self._heading:
            self._heading_buf.append(data)
        self.text.append(data)


def scrub(text: str | None) -> str | None:
    """Remove email addresses and phone numbers. Personal data is never stored."""
    if text is None:
        return None
    return PHONE_RE.sub("[phone]", EMAIL_RE.sub("[email]", text))


@dataclass(frozen=True)
class PageText:
    """The launch-era home page of a site as Common Crawl stored it. REAL, scrubbed of emails and phone numbers."""

    site: str
    crawl: str
    url: str
    captured_at: str
    http_status: int | None
    title: str | None
    meta_description: str | None
    h1: tuple[str, ...]
    h2: tuple[str, ...]
    h3: tuple[str, ...]
    text: str
    visible_chars: int
    parked_text: bool
    rebrand_text: bool
    sha256: str

    def dated(self) -> Dated:
        """Title, meta description and headings as one Dated text, dated by the capture day."""
        parts = [self.title or "", self.meta_description or "", *self.h1, *self.h2, *self.h3]
        return Dated(" | ".join(part for part in parts if part), f"commoncrawl:{self.crawl}:{self.url}", parse_utc(self.captured_at).date())


def parse_warc_record(data: bytes) -> tuple[int | None, str | None, bytes]:
    """(HTTP status, content type, body) from one decompressed WARC response record."""
    head, _, rest = data.partition(b"\r\n\r\n")
    http_head, _, body = rest.partition(b"\r\n\r\n")
    status: int | None = None
    content_type: str | None = None
    lines = http_head.split(b"\r\n")
    if lines and lines[0].startswith(b"HTTP/"):
        parts = lines[0].split()
        if len(parts) > 1 and parts[1].isdigit():
            status = int(parts[1])
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-type":
            content_type = value.strip().decode("latin-1")
    return status, content_type, body


def _phrase_hit(text: str, phrases: Sequence[str]) -> bool:
    lowered = " ".join(text.lower().split())
    return any(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", lowered) for phrase in phrases)


def page_text(site: str, crawl: str, url: str, captured_at: str, data: bytes, charset: str | None = None) -> PageText:
    """Parse a WARC response record into scrubbed title, headings and visible text."""
    status, content_type, body = parse_warc_record(data)
    encoding = charset
    if content_type and "charset=" in content_type.lower():
        encoding = content_type.lower().split("charset=", 1)[1].split(";")[0].strip() or encoding
    try:
        markup = body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        markup = body.decode("utf-8", errors="replace")
    parser = _TextParser()
    try:
        parser.feed(markup)
        parser.close()
    except (AssertionError, ValueError):
        pass
    title = scrub(" ".join("".join(parser.title).split()) or None)
    visible = " ".join(html.unescape(" ".join(parser.text)).split())
    clean = scrub(visible) or ""
    heads = {k: tuple(scrub(v) or "" for v in parser.headings[k][:20]) for k in parser.headings}
    meta = scrub(parser.meta_description)
    whole = " ".join([title or "", meta or "", clean])
    return PageText(
        site=site, crawl=crawl, url=url, captured_at=captured_at, http_status=status, title=title, meta_description=meta,
        h1=heads["h1"], h2=heads["h2"], h3=heads["h3"], text=clean[:PAGE_TEXT_MAX_CHARS], visible_chars=len(visible),
        parked_text=_phrase_hit(whole[:3000], PARKING_PHRASES) or len(visible) < MIN_CONTENT_CHARS,
        rebrand_text=_phrase_hit(whole, REBRAND_PHRASES), sha256=_sha256_bytes(data),
    )


class PageFetcher:
    """Fetches one WARC record per site with a range read, parses it, and caches the parsed text (never the raw HTML)."""

    def __init__(self, http: httpx.Client, cache_path: Path, *, sleep: Callable[[float], None] = time.sleep,
                 polite: Polite | None = None, base: str = CC_DATA) -> None:
        self.http = http
        self.cache = JsonlCache(cache_path)
        self._sleep = sleep
        self.polite = polite or Polite(MIN_GAP_CC, sleep=sleep)
        self.base = base.rstrip("/")
        self.bytes_downloaded = 0

    def fetch(self, summary: CdxSummary) -> PageText | None:
        home = summary.home
        if not home or not home.get("filename") or not home.get("length"):
            return None
        key = f"{summary.crawl}|{summary.site}"
        cached = self.cache.get(key)
        if cached is not None:
            row = {k: v for k, v in cached.items() if k != "key"}
            for name in ("h1", "h2", "h3"):
                row[name] = tuple(row.get(name) or ())
            return PageText(**row)
        start = int(home["offset"])
        end = start + int(home["length"]) - 1
        url = f"{self.base}/{home['filename']}"
        try:
            response = _get(self.http, url, self.polite, headers={"Range": f"bytes={start}-{end}"}, sleep=self._sleep)
            raw = response.content
            if response.status_code == 200 and len(raw) > end - start + 1:
                raw = raw[start : end + 1]
            self.bytes_downloaded += len(raw)
            data = gzip.decompress(raw)
        except (ConnectionError, OSError, EOFError, zlib.error):
            return None
        parsed = page_text(summary.site, summary.crawl, str(home["url"]), str(home["captured_at"]), data, home.get("charset"))
        self.cache.put(key, asdict(parsed))
        return parsed


# ----------------------------------------------------------------------------------------------
# The cohort.
# ----------------------------------------------------------------------------------------------

COHORT_COLUMNS: tuple[str, ...] = (
    "site", "registered_on", "registered_on_source", "listed_on", "source", "tld",
    "rdap_status", "rdap_registered_on", "nameservers", "ns_parking", "dns_resolves", "current_state_checked_at",
    "cc_crawls_checked", "cc_pre_registration_captures", "cc_first_seen", "cc_first_content", "cc_pages_first_content_crawl",
    "home_url", "home_title",
    "launch_date", "launch_date_source", "launch_uncertainty_days",
    "parked", "parked_now", "possibly_re_registered", "possible_rebrand", "no_content_evidence",
    "redirects_offsite", "re_registered_after_listing", "registration_older_than_list", "not_registered_now",
    "resolvable_or_crawled", "content_evidence", "kept",
)


@dataclass(frozen=True)
class CohortRow:
    """One registered domain and every piece of evidence about it. Dead and never-crawled domains stay in.

    REAL: listed_on, rdap_*, nameservers, dns_resolves, cc_* and home_*. ESTIMATED by a rule:
    launch_date (the day of the first Common Crawl capture of real content; the true launch is up to
    launch_uncertainty_days earlier). Flags are rules on the REAL evidence. None means not known.
    `parked` means parked with no launch-era content. `parked_now` is today's name servers only and
    never removes a site (a site that launched and later died is still kept).
    """

    site: str
    registered_on: date | None
    registered_on_source: str | None
    listed_on: date
    source: str
    tld: str
    rdap_status: str | None
    rdap_registered_on: date | None
    nameservers: tuple[str, ...]
    ns_parking: bool | None
    dns_resolves: bool | None
    current_state_checked_at: str | None
    cc_crawls_checked: tuple[str, ...]
    cc_pre_registration_captures: int | None
    cc_first_seen: date | None
    cc_first_content: date | None
    cc_pages_first_content_crawl: int | None
    home_url: str | None
    home_title: str | None
    launch_date: date | None
    launch_date_source: str | None
    launch_uncertainty_days: int | None
    parked: bool
    parked_now: bool
    possibly_re_registered: bool
    possible_rebrand: bool
    no_content_evidence: bool
    redirects_offsite: bool
    re_registered_after_listing: bool
    registration_older_than_list: bool
    not_registered_now: bool
    resolvable_or_crawled: bool
    content_evidence: bool
    kept: bool


@dataclass(frozen=True)
class Funnel:
    """Counts at each stage. Every stage counts all domains that reached it. Nothing is dropped from the table."""

    registered: int
    resolvable_or_crawled: int
    with_content_evidence: int
    kept: int
    flags: dict[str, int]

    def lines(self) -> list[str]:
        out = [
            f"Registered: {self.registered}.",
            f"Resolvable or crawled: {self.resolvable_or_crawled}.",
            f"With content evidence: {self.with_content_evidence}.",
            f"Kept: {self.kept}.",
        ]
        out += [f"Flag {name}: {count}." for name, count in sorted(self.flags.items())]
        return out


@dataclass
class CohortResult:
    rows: list[CohortRow]
    funnel: Funnel
    pages: dict[str, PageText]
    report: dict[str, Any]

    def page_texts(self) -> dict[str, list[Dated]]:
        """Launch-era page text per kept or flagged site, as Dated values for select_targets."""
        return {site: [page.dated()] for site, page in sorted(self.pages.items())}


@dataclass(frozen=True)
class CrawlPlan:
    """Which crawls to check: `before` ended before the registrations (re-registration evidence), `after` follow them."""

    before: tuple[str, ...]
    after: tuple[str, ...]


def _date_or_none(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return parse_utc(text).date()
    except ValueError:
        return None


def assess(
    registration: Registration,
    *,
    rdap: RdapRecord | None,
    dns: DnsRecord | None,
    crawls: Mapping[str, Crawl],
    plan: CrawlPlan,
    cdx: Mapping[str, CdxSummary | None],
    pages: Mapping[str, PageText | None],
) -> CohortRow:
    """Apply the frozen rules to one domain's evidence. Pure: no network."""
    rdap_day = _date_or_none(rdap.registration) if rdap else None
    registered_on, registered_source = None, None
    after_listing = older_than_list = False
    if registration.registered_on is not None:
        registered_on, registered_source = registration.registered_on, "given"
    if rdap_day is not None:
        gap = (rdap_day - registration.listed_on).days
        if abs(gap) <= REGISTRATION_MATCH_DAYS:
            registered_on, registered_source = rdap_day, "rdap"
        elif gap > REGISTRATION_MATCH_DAYS:
            after_listing = True
        else:
            older_than_list = True
    if registered_on is None:
        registered_on, registered_source = registration.listed_on, f"list:{registration.source}"
    reg_moment = datetime.combine(registered_on, datetime.min.time(), tzinfo=timezone.utc)

    pre_captures: int | None = None
    for crawl_id in plan.before:
        summary = cdx.get(crawl_id)
        crawl = crawls.get(crawl_id)
        if summary is None or crawl is None or crawl.end >= reg_moment:
            continue
        pre_captures = (pre_captures or 0) + summary.captures
    checked = tuple(c for c in plan.before + plan.after if cdx.get(c) is not None)

    after_ids = sorted((c for c in plan.after if cdx.get(c) is not None and c in crawls), key=lambda c: crawls[c].start)
    first_seen: str | None = None
    first_content: tuple[str, CdxSummary, PageText | None] | None = None
    previous_empty_end: datetime | None = None
    offsite = marketplace = False
    rebrand_size = rebrand_text = parked_text = False
    for crawl_id in after_ids:
        summary = cdx[crawl_id]
        assert summary is not None
        if crawls[crawl_id].end < reg_moment:
            continue
        if summary.first_capture and (first_seen is None or summary.first_capture < first_seen):
            first_seen = summary.first_capture
        if first_content is not None:
            continue  # later crawls describe what happened after launch, not the launch
        # Launch-era evidence only: crawls up to and including the first one with real content.
        offsite = offsite or bool(summary.offsite_redirects)
        marketplace = marketplace or summary.marketplace_redirect
        page = pages.get(crawl_id)
        if page is not None and page.parked_text:
            parked_text = True
        real = summary.html_200 > 0 and (page is None or not page.parked_text)
        if real:
            first_content = (crawl_id, summary, page)
        else:
            previous_empty_end = crawls[crawl_id].end

    launch_date = launch_source = None
    uncertainty: int | None = None
    pages_first: int | None = None
    home_url = home_title = None
    if first_content is not None:
        crawl_id, summary, page = first_content
        captured = parse_utc(summary.first_html_200).date() if summary.first_html_200 else crawls[crawl_id].start.date()
        lower = registered_on
        if previous_empty_end is not None and previous_empty_end.date() > lower:
            lower = previous_empty_end.date()
        launch_date = captured
        launch_source = f"cc_first_content_capture:{crawl_id}"
        uncertainty = max(0, (captured - lower).days)
        pages_first = summary.pages_200
        rebrand_size = summary.pages_200 >= REBRAND_MIN_PAGES_FIRST_CRAWL
        if page is not None:
            rebrand_text = page.rebrand_text
            home_url, home_title = page.url, page.title
        elif summary.home:
            home_url = summary.home.get("url")

    ns_parking = parking_nameservers(rdap.nameservers) if rdap and rdap.status == "found" else None
    not_registered = bool(rdap and rdap.status == "not_found")
    crawled_after = first_seen is not None
    resolvable_or_crawled = bool(dns and dns.resolves) or crawled_after
    content = first_content is not None
    # Survivorship rule (PLAN_AUDIT B4): today's parking name servers, today's DNS and today's RDAP never
    # remove a site that showed real content at launch. A site that launched and later died stays kept.
    parked = not content and (bool(ns_parking) or parked_text or marketplace)
    alias = not content and offsite
    re_reg = bool(pre_captures)
    rebrand = rebrand_size or rebrand_text
    kept = content and not re_reg and not rebrand and not older_than_list
    states = [x.checked_at for x in (rdap, dns) if x is not None]
    return CohortRow(
        site=registration.site, registered_on=registered_on, registered_on_source=registered_source,
        listed_on=registration.listed_on, source=registration.source, tld=registration.tld,
        rdap_status=rdap.status if rdap else None, rdap_registered_on=rdap_day,
        nameservers=tuple(rdap.nameservers) if rdap else (), ns_parking=ns_parking,
        dns_resolves=dns.resolves if dns else None, current_state_checked_at=max(states) if states else None,
        cc_crawls_checked=checked, cc_pre_registration_captures=pre_captures,
        cc_first_seen=_date_or_none(first_seen), cc_first_content=launch_date, cc_pages_first_content_crawl=pages_first,
        home_url=home_url, home_title=home_title,
        launch_date=launch_date, launch_date_source=launch_source, launch_uncertainty_days=uncertainty,
        parked=parked, parked_now=bool(ns_parking), possibly_re_registered=re_reg, possible_rebrand=rebrand,
        no_content_evidence=not content, redirects_offsite=alias, re_registered_after_listing=after_listing, registration_older_than_list=older_than_list,
        not_registered_now=not_registered, resolvable_or_crawled=resolvable_or_crawled, content_evidence=content, kept=kept,
    )


FLAG_COLUMNS = ("parked", "parked_now", "possibly_re_registered", "possible_rebrand", "no_content_evidence", "redirects_offsite",
                "re_registered_after_listing", "registration_older_than_list", "not_registered_now")


def funnel_of(rows: Sequence[CohortRow]) -> Funnel:
    return Funnel(
        registered=len(rows),
        resolvable_or_crawled=sum(row.resolvable_or_crawled for row in rows),
        with_content_evidence=sum(row.content_evidence for row in rows),
        kept=sum(row.kept for row in rows),
        flags={name: sum(bool(getattr(row, name)) for row in rows) for name in FLAG_COLUMNS},
    )


def cohort(
    registrations: Sequence[Registration],
    *,
    http: httpx.Client,
    plan: CrawlPlan,
    crawls: Mapping[str, Crawl] | None = None,
    cache_root: Path | None = None,
    public_root: Path | None = None,
    use_rdap: bool = True,
    use_dns: bool = True,
    use_cc: bool = True,
    fetch_pages: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = _utcnow,
    log: Callable[[str], None] | None = None,
) -> CohortResult:
    """Collect evidence for every registration and apply the frozen rules. Every domain stays in the table.

    Resumable: RDAP, DNS, CDX summaries and parsed pages are cached per domain
    under cache_root, so a second run fetches only what is missing.
    """
    started = time.monotonic()
    cache = Path(cache_root) if cache_root else default_cache()
    public = Path(public_root) if public_root else forecast_paths().root / "public"
    crawl_map = dict(crawls) if crawls is not None else load_crawls(public / "commoncrawl" / "collinfo.json", http=http)
    for crawl_id in plan.before + plan.after:
        if crawl_id not in crawl_map:
            raise ValueError(f"crawl {crawl_id} is not in the crawl list")
    regs = sorted({reg.site: reg for reg in registrations if reg.site not in EXCLUDED_SITES}.values(), key=lambda r: r.site)
    sites = [reg.site for reg in regs]
    bootstrap = dict(RDAP_VERISIGN)
    dns_json = public / "rdap" / "dns.json"
    if dns_json.is_file():
        try:
            for entry in json.loads(dns_json.read_text(encoding="utf-8")).get("services", []):
                tlds, urls = entry[0], entry[1]
                for tld in tlds:
                    if urls and tld not in bootstrap:
                        bootstrap[tld] = urls[0]
        except (ValueError, IndexError, TypeError):
            pass
    rdap_records: dict[str, RdapRecord] = {}
    dns_records: dict[str, DnsRecord] = {}
    if use_rdap:
        rdap = RdapLookup(http, cache / "rdap.jsonl", sleep=sleep, now=now, bootstrap=bootstrap)
        for index, site in enumerate(sites, start=1):
            rdap_records[site] = rdap.lookup(site)
            if log and index % 200 == 0:
                log(f"RDAP: {index}/{len(sites)}")
    if use_dns:
        dns = DnsLookup(http, cache / "dns.jsonl", sleep=sleep, now=now)
        for index, site in enumerate(sites, start=1):
            dns_records[site] = dns.lookup(site)
            if log and index % 500 == 0:
                log(f"DNS: {index}/{len(sites)}")
    cdx: dict[str, dict[str, CdxSummary]] = {}
    pages: dict[str, dict[str, PageText | None]] = {site: {} for site in sites}
    cc_bytes = page_bytes = 0
    if use_cc:
        index = CommonCrawlIndex(http, public_root=public / "commoncrawl", cache_root=cache / "cc", sleep=sleep)
        for crawl_id in plan.before + plan.after:
            cdx[crawl_id] = index.lookup(crawl_id, sites, log=log)
            if log:
                log(f"{crawl_id}: done, {index.bytes_downloaded / 1e6:.0f} MB downloaded from Common Crawl so far")
        cc_bytes = index.bytes_downloaded
        if fetch_pages:
            fetcher = PageFetcher(http, cache / "pages.jsonl", sleep=sleep)
            for crawl_id in plan.after:
                for site in sites:
                    summary = cdx[crawl_id].get(site)
                    if summary is not None and summary.html_200 > 0:
                        pages[site][crawl_id] = fetcher.fetch(summary)
            page_bytes = fetcher.bytes_downloaded
    rows: list[CohortRow] = []
    first_pages: dict[str, PageText] = {}
    for reg in regs:
        row = assess(
            reg, rdap=rdap_records.get(reg.site), dns=dns_records.get(reg.site), crawls=crawl_map, plan=plan,
            cdx={crawl_id: cdx.get(crawl_id, {}).get(reg.site) for crawl_id in plan.before + plan.after},
            pages=pages.get(reg.site, {}),
        )
        rows.append(row)
        if row.launch_date_source:
            crawl_id = row.launch_date_source.split(":", 1)[1]
            page = pages.get(reg.site, {}).get(crawl_id)
            if page is not None:
                first_pages[reg.site] = page
    funnel = funnel_of(rows)
    report = {
        "registrations": len(regs),
        "crawls": {"before": list(plan.before), "after": list(plan.after)},
        "commoncrawl_bytes": cc_bytes,
        "page_bytes": page_bytes,
        "seconds": round(time.monotonic() - started, 1),
        "funnel": asdict(funnel),
    }
    return CohortResult(rows=rows, funnel=funnel, pages=first_pages, report=report)


def rows_to_frame(rows: Sequence[CohortRow]):
    """The cohort table as a pandas DataFrame with COHORT_COLUMNS, in order."""
    import pandas as pd

    records = []
    for row in rows:
        record = asdict(row)
        record["nameservers"] = list(row.nameservers)
        record["cc_crawls_checked"] = list(row.cc_crawls_checked)
        records.append(record)
    frame = pd.DataFrame.from_records(records, columns=list(COHORT_COLUMNS))
    for name in ("cc_pre_registration_captures", "cc_pages_first_content_crawl", "launch_uncertainty_days"):
        frame[name] = frame[name].astype("Int64")
    return frame


def write_cohort(result: CohortResult, out_dir: Path, name: str) -> dict[str, str]:
    """Write cohort_<name>.parquet, pages_<name>.jsonl and funnel_<name>.json. Returns their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = rows_to_frame(result.rows)
    table_path = out_dir / f"cohort_{name}.parquet"
    tmp = table_path.with_name(table_path.name + ".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, table_path)
    pages_path = out_dir / f"pages_{name}.jsonl"
    with pages_path.open("w", encoding="utf-8") as handle:
        for site, page in sorted(result.pages.items()):
            handle.write(json.dumps(asdict(page), sort_keys=True) + "\n")
    funnel_path = out_dir / f"funnel_{name}.json"
    funnel_path.write_text(json.dumps({**result.report, "funnel_lines": result.funnel.lines()}, indent=2, sort_keys=True, default=str),
                           encoding="utf-8")
    return {"cohort": str(table_path), "pages": str(pages_path), "funnel": str(funnel_path)}


def load_pages(path: Path) -> dict[str, PageText]:
    out: dict[str, PageText] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            for name in ("h1", "h2", "h3"):
                row[name] = tuple(row.get(name) or ())
            out[row["site"]] = PageText(**row)
    return out


# ----------------------------------------------------------------------------------------------
# CLI.
# ----------------------------------------------------------------------------------------------


def _log(message: str) -> None:
    print(f"[{utc_iso(_utcnow())}] {message}", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="newsites", description="New-site cohorts from free sources.")
    sub = parser.add_subparsers(dest="command", required=True)
    nrd = sub.add_parser("nrd", help="Build daily registration lists from a list's git history.")
    nrd.add_argument("--repo", choices=sorted(REPOS), default="cenk")
    nrd.add_argument("--start", required=True)
    nrd.add_argument("--end", required=True)
    nrd.add_argument("--mode", choices=("raw", "git"), default="raw")
    co = sub.add_parser("cohort", help="Collect evidence for capped registrations and write the cohort table.")
    co.add_argument("--repo", choices=sorted(REPOS), default="cenk")
    co.add_argument("--start", required=True)
    co.add_argument("--end", required=True)
    co.add_argument("--tld", action="append", default=None)
    co.add_argument("--cap-per-day", type=int, default=DEFAULT_CAP_PER_DAY)
    co.add_argument("--before", action="append", default=[], help="crawl id that ended before the registrations")
    co.add_argument("--after", action="append", default=[], help="crawl id after the registrations")
    co.add_argument("--name", required=True)
    co.add_argument("--no-pages", action="store_true")
    args = parser.parse_args(argv)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    with make_client() as http:
        fetcher = NrdFetcher(REPOS[args.repo], http=http, mode=getattr(args, "mode", "raw"))
        began = time.monotonic()
        daily, skipped = fetcher.daily_lists(start, end)
        _log(f"daily lists: {len(daily)} days, {sum(d.total_new for d in daily)} new domains, skipped {skipped}, "
             f"{fetcher.bytes_downloaded / 1e6:.1f} MB from GitHub in {time.monotonic() - began:.0f} s")
        if args.command == "nrd":
            print(json.dumps([{**asdict(d), "day": d.day.isoformat()} for d in daily], indent=2))
            return 0
        regs = registrations_from(daily, tlds=tuple(args.tld or DEFAULT_TLDS), cap_per_day=args.cap_per_day)
        _log(f"registrations after the TLD filter and the cap: {len(regs)}")
        result = cohort(regs, http=http, plan=CrawlPlan(tuple(args.before), tuple(args.after)), log=_log, fetch_pages=not args.no_pages)
        paths = write_cohort(result, default_cache(), args.name)
        result.report["github_bytes"] = fetcher.bytes_downloaded
        result.report["daily_lists"] = [{"day": d.day.isoformat(), "total_new": d.total_new, "com": d.by_tld.get("com", 0)} for d in daily]
        Path(paths["funnel"]).write_text(json.dumps({**result.report, "funnel_lines": result.funnel.lines()}, indent=2, default=str),
                                         encoding="utf-8")
        for line in result.funnel.lines():
            print(line)
        print(json.dumps({"paths": paths, "seconds": result.report["seconds"], "commoncrawl_mb": round(result.report["commoncrawl_bytes"] / 1e6, 1),
                          "page_mb": round(result.report["page_bytes"] / 1e6, 1), "github_mb": round(fetcher.bytes_downloaded / 1e6, 1)}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
