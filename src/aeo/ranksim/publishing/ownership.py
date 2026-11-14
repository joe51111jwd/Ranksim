"""Prove a site is yours before anything is pushed live.

Three proofs, each with a random per-site token:

- meta: ``<meta name="ranksim-verification" content="TOKEN">`` on the homepage.
- file: ``/.well-known/ranksim-verification.txt`` whose text contains the token.
- dns:  a TXT record ``ranksim-verification=TOKEN`` on the domain, read over DNS-over-HTTPS (Cloudflare, then Google).

Web pages are read through the caller's fetch function, which in the app is the robots-respecting, private-address
refusing Crawler. A DNS proof covers the domain and its subdomains; a meta or file proof covers that host only.

Verified state is re-checked when it is older than ``STALE_AFTER``. A proof that is gone revokes ownership. A check that
could not run (network trouble) keeps a recent success for ``GRACE`` so a flaky resolver never blocks a publish, but
never extends it past that.
"""

from __future__ import annotations

import re
import secrets
import time
from typing import Callable

import httpx
from bs4 import BeautifulSoup

from aeo.crawl.urls import domain_of

META_NAME = "ranksim-verification"
FILE_PATH = "/.well-known/ranksim-verification.txt"
TXT_PREFIX = "ranksim-verification="
METHODS = ("meta", "file", "dns")
STALE_AFTER = 24 * 3600.0
GRACE = 7 * 24 * 3600.0
DOH_RESOLVERS = (
    ("https://cloudflare-dns.com/dns-query", {"accept": "application/dns-json"}),
    ("https://dns.google/resolve", {"accept": "application/dns-json"}),
)
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")

Fetch = Callable[[str], "tuple[str, str]"]  # url -> (body, error)


class OwnershipError(ValueError):
    pass


def site_key(value: str) -> str:
    """Normalize a URL or bare domain to the lowercase domain without www."""
    raw = (value or "").strip().lower()
    if "://" not in raw:
        raw = "https://" + raw
    domain = domain_of(raw)
    if not _DOMAIN.match(domain):
        raise OwnershipError("Enter a site address such as example.com.")
    return domain


def new_token() -> str:
    return "rs-" + secrets.token_urlsafe(18).replace("-", "a").replace("_", "b")


def new_record(domain: str, now: float) -> dict:
    return {
        "domain": domain,
        "token": new_token(),
        "created": now,
        "verified": False,
        "method": None,
        "scope": None,
        "verified_at": None,
        "checked_at": None,
        "last_check": None,
    }


def instructions(record: dict) -> dict:
    """What the owner adds for each method. The token is not a secret: it only works on a site you control."""
    token, domain = record["token"], record["domain"]
    return {
        "meta": {
            "label": "Meta tag",
            "steps": f"Add this tag inside the <head> of your homepage, https://{domain}/.",
            "value": f'<meta name="{META_NAME}" content="{token}">',
        },
        "file": {
            "label": "File",
            "steps": f"Upload a text file to https://{domain}{FILE_PATH} that holds this line.",
            "value": token,
        },
        "dns": {
            "label": "DNS record",
            "steps": f"Add a TXT record on {domain} with this value. DNS changes can take a while to show up.",
            "value": f"{TXT_PREFIX}{token}",
        },
    }


# ---------------------------------------------------------------------------
# The three checks. Each returns (found, detail, definitive). definitive=False means the check could not run.
# ---------------------------------------------------------------------------


def check_meta(domain: str, token: str, fetch: Fetch) -> tuple[bool, str, bool]:
    errors = []
    for host in (domain, "www." + domain):
        html, error = fetch(f"https://{host}/")
        if error or not html:
            errors.append(error or "empty response")
            continue
        soup = BeautifulSoup(html, "lxml")
        for meta in soup.find_all("meta"):
            if str(meta.get("name", "")).strip().lower() == META_NAME:
                if secrets.compare_digest(str(meta.get("content", "")).strip(), token):
                    return True, f"Found the meta tag on https://{host}/.", True
                return False, f"The meta tag on https://{host}/ holds a different token.", True
        return False, f"No ranksim-verification meta tag on https://{host}/.", True
    return False, f"The homepage could not be read ({errors[-1]}).", False


def check_file(domain: str, token: str, fetch: Fetch) -> tuple[bool, str, bool]:
    errors = []
    for host in (domain, "www." + domain):
        body, error = fetch(f"https://{host}{FILE_PATH}")
        if error and ("404" in error or "410" in error):
            return False, f"No file at https://{host}{FILE_PATH}.", True
        if error or not body:
            errors.append(error or "empty file")
            continue
        lines = {line.strip() for line in body.splitlines() if line.strip()}
        if any(secrets.compare_digest(line, token) or secrets.compare_digest(line, TXT_PREFIX + token) for line in lines):
            return True, f"Found the token in https://{host}{FILE_PATH}.", True
        return False, f"https://{host}{FILE_PATH} does not hold the token.", True
    return False, f"The file could not be read ({errors[-1]}).", False


def txt_records(domain: str, client: httpx.Client) -> tuple[list[str] | None, str]:
    """TXT strings for a name over DNS-over-HTTPS. (None, reason) when no resolver answered."""
    reason = "no resolver answered"
    for endpoint, headers in DOH_RESOLVERS:
        try:
            response = client.get(endpoint, params={"name": domain, "type": "TXT"}, headers=headers, timeout=8.0)
        except httpx.HTTPError as exc:
            reason = f"{type(exc).__name__}"
            continue
        if response.status_code != 200:
            reason = f"HTTP {response.status_code}"
            continue
        try:
            data = response.json()
        except ValueError:
            reason = "unreadable answer"
            continue
        status = data.get("Status")
        if status == 3:  # NXDOMAIN: the name does not exist, a real answer
            return [], "the domain has no DNS records"
        if status != 0:
            reason = f"DNS status {status}"
            continue
        found = []
        for answer in data.get("Answer") or []:
            if answer.get("type") != 16:
                continue
            raw = str(answer.get("data", ""))
            # Long TXT values arrive as several quoted strings: "abc" "def" -> abcdef
            pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', raw)
            found.append("".join(pieces) if pieces else raw.strip())
        return found, ""
    return None, reason


def check_dns(domain: str, token: str, client: httpx.Client) -> tuple[bool, str, bool]:
    records, reason = txt_records(domain, client)
    if records is None:
        return False, f"DNS could not be checked ({reason}).", False
    wanted = TXT_PREFIX + token
    if any(secrets.compare_digest(value.strip(), wanted) for value in records):
        return True, f"Found the TXT record on {domain}.", True
    if any(value.strip().startswith(TXT_PREFIX) for value in records):
        return False, f"The TXT record on {domain} holds a different token.", True
    if reason:
        return False, f"No TXT record found: {reason}.", True
    return False, f"No ranksim-verification TXT record on {domain} yet.", True


def run_checks(
    record: dict,
    *,
    fetch: Fetch,
    dns_client: httpx.Client,
    methods: list[str] | tuple[str, ...] | None = None,
    now: float | None = None,
) -> dict:
    """Try the methods in order and update the record in place. Returns the record."""
    now = time.time() if now is None else now
    wanted = [m for m in (methods or METHODS) if m in METHODS] or list(METHODS)
    attempts = []
    for method in wanted:
        try:
            if method == "meta":
                found, detail, definitive = check_meta(record["domain"], record["token"], fetch)
            elif method == "file":
                found, detail, definitive = check_file(record["domain"], record["token"], fetch)
            else:
                found, detail, definitive = check_dns(record["domain"], record["token"], dns_client)
        except Exception as exc:  # noqa: BLE001 - a broken check is reported, never fatal
            found, detail, definitive = False, f"The check failed ({type(exc).__name__}).", False
        attempts.append({"method": method, "found": found, "detail": detail, "definitive": definitive})
        if found:
            record.update(
                verified=True,
                method=method,
                scope="domain" if method == "dns" else "host",
                verified_at=now,
                checked_at=now,
                last_check={"at": now, "ok": True, "method": method, "detail": detail, "attempts": attempts},
            )
            return record
    could_not_run = all(not a["definitive"] for a in attempts)
    keep = (
        could_not_run
        and record.get("verified")
        and record.get("verified_at") is not None
        and now - float(record["verified_at"]) < GRACE
    )
    if not keep:
        record.update(verified=False, method=None, scope=None, verified_at=None)
    record.update(
        checked_at=now,
        last_check={
            "at": now,
            "ok": bool(keep),
            "method": None,
            "detail": " ".join(a["detail"] for a in attempts),
            "attempts": attempts,
            "kept_by_grace": bool(keep),
        },
    )
    return record


def is_stale(record: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    checked = record.get("checked_at")
    return checked is None or now - float(checked) >= STALE_AFTER


def covers(record: dict, url: str) -> bool:
    """Whether a verified record lets this URL be published."""
    if not record.get("verified"):
        return False
    try:
        domain = site_key(url)
    except OwnershipError:
        return False
    if domain == record["domain"]:
        return True
    return record.get("scope") == "domain" and domain.endswith("." + record["domain"])


def public(record: dict) -> dict:
    view = {key: record.get(key) for key in ("domain", "token", "verified", "method", "scope", "verified_at", "checked_at", "last_check")}
    view["instructions"] = instructions(record)
    return view
