"""What every publishing connector does, and the helpers they share.

A connector turns field changes (title tag, meta description, main heading, body content) into platform API calls:

    check()                       -> short account label; raises ConnectorError when the credentials do not work
    prepare(url, before, after)   -> Prepared: the item found, a row per change saying if and how it will be applied,
                                     warnings, the platform payload, and the previous values needed for rollback
    apply(prepared)               -> result dict (links, ids, "live" or "pending" or "pull_request")
    rollback(entry)               -> result dict, using entry["previous"] and entry["remote"]

Every outbound call goes through ``self.http`` (an httpx.Client, mocked in tests). Calls to a user-supplied host
(WordPress, webhooks) are checked against private addresses first. Error text never includes credentials.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

from aeo.crawl.urls import _is_public_ip, assert_safe_url, normalize_url
from aeo.errors import UnsafeURL

from ..htmlparts import EMBED_TAGS, FIELD_LABELS, PageParts, changes as field_changes, lost_embeds, short, soup_of, text_of

TIMEOUT = 20.0


class ConnectorError(RuntimeError):
    """A message safe to show the owner."""


@dataclass
class Prepared:
    url: str
    target: dict  # what was found on the platform (ids, type, link)
    rows: list[dict] = field(default_factory=list)  # one per change: field, label, before, after, apply, note
    warnings: list[dict] = field(default_factory=list)  # {code, text, confirm: bool}
    payload: dict = field(default_factory=dict)
    previous: dict = field(default_factory=dict)
    can_rollback: bool = True
    live_after_apply: bool = True  # False for a pull request or a save that still needs a site publish

    def applied_fields(self) -> list[str]:
        return [row["field"] for row in self.rows if row.get("apply")]

    def public(self) -> dict:
        return {
            "url": self.url,
            "target": {k: v for k, v in self.target.items() if not k.startswith("_")},
            "rows": [{**row, "before": short(row.get("before", "")), "after": short(row.get("after", ""))} for row in self.rows],
            "warnings": self.warnings,
            "can_rollback": self.can_rollback,
            "live_after_apply": self.live_after_apply,
        }


class Connector:
    kind = ""
    label = ""
    secret_fields: tuple[str, ...] = ()
    setting_fields: tuple[str, ...] = ()
    default_regions: tuple[str, ...] = ()
    summary = ""  # what it can change, plain words
    limits: tuple[str, ...] = ()  # what it cannot change, plain words

    def __init__(self, settings: dict, secret: dict, http: httpx.Client, *, allow_private: bool = False) -> None:
        self.settings = dict(settings or {})
        self.secret = dict(secret or {})
        self.http = http
        self.allow_private = allow_private

    # -- to implement ------------------------------------------------------

    def check(self) -> str:
        raise NotImplementedError

    def prepare(self, url: str, before_html: str, after_html: str, *, slug: str = "") -> Prepared:
        raise NotImplementedError

    def apply(self, prepared: Prepared, *, options: dict | None = None) -> dict:
        raise NotImplementedError

    def rollback(self, entry: dict) -> dict:
        raise NotImplementedError

    def inspect(self, url: str = "") -> list[dict]:
        """Step by step check for the guided connect flow: [{key, label, ok, detail}]. Never saves anything.
        The default is one step, the credential check. Connectors with a guided flow say more."""
        try:
            account = self.check()
            return [{"key": "login", "label": "Credentials", "ok": True, "detail": f"Connected to {account}."}]
        except ConnectorError as exc:
            return [{"key": "login", "label": "Credentials", "ok": False, "detail": str(exc)}]

    # -- shared helpers ----------------------------------------------------

    @classmethod
    def describe(cls) -> dict:
        return {
            "kind": cls.kind,
            "label": cls.label,
            "settings": list(cls.setting_fields),
            "secrets": list(cls.secret_fields),
            "summary": cls.summary,
            "limits": list(cls.limits),
        }

    @classmethod
    def clean_settings(cls, settings: dict) -> dict:
        return {k: str(v).strip()[:500] for k, v in (settings or {}).items() if k in cls.setting_fields and v not in (None, "")}

    @classmethod
    def clean_secret(cls, secret: dict) -> dict:
        found = {k: str(v).strip() for k, v in (secret or {}).items() if k in cls.secret_fields and v}
        missing = [k for k in cls.secret_fields if k not in found]
        if missing:
            raise ConnectorError(f"Missing {', '.join(missing).replace('_', ' ')}.")
        return found

    def regions(self) -> list[str]:
        custom = [s.strip() for s in str(self.settings.get("content_selector", "")).split(",") if s.strip()]
        return custom + list(self.default_regions)

    def safe_url(self, url: str) -> str:
        """A user-supplied host must be public. Raises ConnectorError otherwise."""
        try:
            return assert_safe_url(url, allow_private=self.allow_private, resolve=True, keep_trailing_slash=True)
        except (UnsafeURL, ValueError) as exc:
            raise ConnectorError(f"That address cannot be used: {exc}") from exc

    def pinned(self, url: str) -> tuple[str, dict, dict]:
        """Resolve a user-supplied host once, refuse it unless every address is public, and dial that address.

        Returns (url with the IP, extra headers, httpx extensions). The Host header and TLS server name stay the
        original name, so certificates are still checked against it. This closes the gap where a name answers a
        public address to the check and a private one to the real connection (DNS rebinding).
        """
        if self.allow_private:
            return url, {}, {}
        parts = urlsplit(url)
        host = parts.hostname or ""
        try:
            ipaddress.ip_address(host)
            return url, {}, {}  # an IP literal was already checked by safe_url
        except ValueError:
            pass
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ConnectorError(f"{host} could not be resolved.") from exc
        addresses = [ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos]
        if not addresses or not all(_is_public_ip(a) for a in addresses):
            raise ConnectorError(f"{host} points at a private address, so it cannot be used.")
        ip = addresses[0]
        shown = f"[{ip}]" if ip.version == 6 else str(ip)
        netloc = f"{shown}:{parts.port}" if parts.port else shown
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")), {"Host": parts.netloc}, {"sni_hostname": host}

    def request(self, method: str, url: str, *, expect=(200, 201), pin: bool = False, **kwargs) -> httpx.Response:
        if pin:
            url, headers, extensions = self.pinned(url)
            if headers:
                kwargs["headers"] = {**(kwargs.get("headers") or {}), **headers}
                kwargs["extensions"] = extensions
        try:
            response = self.http.request(method, url, timeout=TIMEOUT, **kwargs)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{self.label} could not be reached ({type(exc).__name__}).") from exc
        if expect and response.status_code not in expect:
            raise ConnectorError(f"{self.label} said {response.status_code}: {platform_message(response)}")
        return response

    @staticmethod
    def rows_for(before: PageParts, after: PageParts) -> list[dict]:
        return [{**change, "apply": False, "note": ""} for change in field_changes(before, after)]

    @staticmethod
    def embed_warning(before: PageParts, after: PageParts, rows: list[dict]) -> list[dict]:
        body = next((row for row in rows if row["field"] == "body" and row.get("apply")), None)
        lost = lost_embeds(before, after) if body else []
        if not lost:
            return []
        return [
            {
                "code": "embeds_removed",
                "confirm": True,
                "text": f"The edited body has fewer {', '.join(lost)} elements than the live page. Publishing the body removes them.",
            }
        ]


def document_embeds(html: str) -> dict:
    soup = soup_of(html)
    return {tag: len(soup.find_all(tag)) for tag in EMBED_TAGS}


def document_embed_warning(before_html: str, after_html: str) -> list[dict]:
    """For connectors that send the whole page: scripts, iframes or forms lost anywhere in the document."""
    old, new = document_embeds(before_html), document_embeds(after_html)
    lost = [tag for tag in EMBED_TAGS if old.get(tag, 0) > new.get(tag, 0)]
    if not lost:
        return []
    return [
        {
            "code": "embeds_removed",
            "confirm": True,
            "text": f"The edited page has fewer {', '.join(lost)} elements than the live page. Publishing it removes them.",
        }
    ]


def platform_message(response: httpx.Response) -> str:
    """A short, credential-free error from a platform response."""
    try:
        data = response.json()
    except ValueError:
        return short(text_of(response.text) or response.reason_phrase or "error", 160)
    if isinstance(data, dict):
        for key in ("message", "msg", "error", "errors", "code"):
            if data.get(key):
                value = data[key]
                if isinstance(value, list):
                    value = "; ".join(str(v.get("message", v)) if isinstance(v, dict) else str(v) for v in value[:3])
                elif isinstance(value, dict):
                    value = value.get("message") or str(value)
                return short(str(value), 160)
    return short(str(data), 160)


def path_of(url: str) -> str:
    path = urlsplit(url).path or "/"
    return path if path == "/" else "/" + path.strip("/")


def same_page(left: str, right: str) -> bool:
    """URLs equal apart from scheme, www, and a trailing slash."""
    try:
        a, b = urlsplit(normalize_url(left)), urlsplit(normalize_url(right))
    except ValueError:
        return False
    host = lambda parts: (parts.hostname or "").removeprefix("www.")  # noqa: E731
    return host(a) == host(b) and (a.path.rstrip("/") or "/") == (b.path.rstrip("/") or "/")


SEPARATORS = " |-:\u00b7\u2013\u2014\u2022\u00bb/"


def strip_suffix_title(old_title: str, item_title: str, new_title: str) -> str | None:
    """'Old | Site' with item 'Old' and new 'New | Site' -> 'New'. None when the suffix pattern does not hold."""
    if not item_title or not old_title.startswith(item_title):
        return None
    suffix = old_title[len(item_title):]
    if suffix and suffix[0] not in SEPARATORS:  # 'Old' must be a whole word prefix, not 'Olden'
        return None
    if suffix and not new_title.endswith(suffix):
        return None
    candidate = new_title[: len(new_title) - len(suffix)] if suffix else new_title
    return candidate.strip() or None


def region_matches(before: PageParts, platform_html: str) -> bool:
    """The page region we found is really this item's content (same visible text)."""
    return bool(before.region_selector) and text_of(platform_html) == before.region_text


def blank_label(field: str) -> str:
    return FIELD_LABELS.get(field, field)


SHORTCODE = re.compile(r"\[[a-zA-Z_][\w-]*[^\]]*\]")
