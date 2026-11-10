"""robots.txt per RFC 9309, the same on every Python version.

The stdlib parser applies the first matching rule in file order and, on Python 3.11 and 3.12,
treats ``*`` and ``$`` literally, so a rule such as ``Disallow: /*.pdf$`` was ignored there. This
matcher follows RFC 9309:

- groups: consecutive ``user-agent`` lines start a group; the crawler obeys every group whose
  user agent equals its product token (case-insensitive), merged, else every ``*`` group, else
  nothing is disallowed; rules before the first ``user-agent`` line belong to no group;
- rules: ``allow``/``disallow`` patterns match from the start of the path and query, ``*`` matches
  any characters and a final ``$`` anchors the end; an empty pattern matches nothing. Matching is
  near-linear in the pattern and path, so a hostile rule with many ``*`` cannot stall a crawl;
- percent-encoding (2.2.2): characters outside printable ASCII are encoded, encoded unreserved
  characters (letters, digits, ``-._~``) are decoded and other escapes use upper-case hex, on both
  the pattern and the path;
- the longest matching pattern wins, and an allow wins a tie; ``/robots.txt`` is always allowed.

A leading UTF-8 byte order mark is ignored, as Google's parser does.

``crawl-delay`` is not in RFC 9309; it is read from the selected group when present.
A missing file allows the crawl (the caller passes an empty body).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

_SAFE = "!$&'()*+,-./:;=?@_~%"


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    crawl_delay: float | None = None


def product_token(user_agent: str) -> str:
    """The name robots.txt groups match: letters, digits, '_' and '-' before the first '/' or space."""
    match = re.match(r"\s*([A-Za-z0-9_-]+)", user_agent or "")
    return match.group(1).lower() if match else ""


class Robots:
    def __init__(self, groups: list[_Group]) -> None:
        self._groups = groups

    def _selected(self, user_agent: str) -> list[_Group]:
        token = product_token(user_agent)
        named = [group for group in self._groups if token and token in group.agents]
        return named or [group for group in self._groups if "*" in group.agents]

    def can_fetch(self, user_agent: str, url: str) -> bool:
        parts = urlsplit(url)
        path = parts.path or "/"
        if path == "/robots.txt":
            return True
        target = _encode(path + (f"?{parts.query}" if parts.query else ""))
        best_length = -1
        allowed = True
        for group in self._selected(user_agent):
            for allow, pattern in group.rules:
                if not pattern or not _matches(pattern, target):
                    continue
                length = len(pattern)
                if length > best_length or (length == best_length and allow):
                    best_length = length
                    allowed = allow
        return allowed

    def crawl_delay(self, user_agent: str) -> float | None:
        for group in self._selected(user_agent):
            if group.crawl_delay is not None:
                return group.crawl_delay
        return None


def parse_robots(body: str) -> Robots:
    groups: list[_Group] = []
    current: _Group | None = None
    collecting_agents = False
    text = body or ""
    if text.startswith("\ufeff"):
        text = text[1:]  # a byte order mark would hide the first user-agent line
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or not collecting_agents:
                current = _Group()
                groups.append(current)
            current.agents.append(value.lower() if value != "*" else "*")
            collecting_agents = True
            continue
        if current is None:
            continue  # a rule before any user-agent line belongs to no group
        collecting_agents = False
        if key in {"allow", "disallow"}:
            current.rules.append((key == "allow", _encode(value)))
        elif key == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                continue
            if delay >= 0 and current.crawl_delay is None:
                current.crawl_delay = delay
    return Robots(groups)


def crawl_delay_seconds(parser: Robots, user_agent: str, *, cap: float = 10.0) -> float | None:
    delay = parser.crawl_delay(user_agent)
    if delay is None:
        return None
    return min(float(delay), cap)


_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")


def _normalise_escape(match: re.Match) -> str:
    char = chr(int(match.group(1), 16))
    return char if char in _UNRESERVED else "%" + match.group(1).upper()


def _encode(text: str) -> str:
    """Percent-encode characters outside printable ASCII, decode escaped unreserved characters and
    upper-case the hex of the rest, so patterns and paths compare the same (RFC 9309 2.2.2)."""
    return _ESCAPE.sub(_normalise_escape, quote(text, safe=_SAFE))


def _matches(pattern: str, target: str) -> bool:
    """``*`` matches any run of characters and a final ``$`` anchors the end.

    Each literal piece is found at its leftmost position after the previous one, which is exact
    when ``*`` is the only wildcard and takes near-linear time. A regex here backtracked: a rule
    with eight ``*`` against a 61-character path took 10 to 60 s.
    """
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    first, *rest = body.split("*")
    if not target.startswith(first):
        return False
    if not rest:
        return target == first if anchored else True
    position, end = len(first), len(target)
    if anchored:
        last = rest.pop()
        if not target.endswith(last) or end - len(last) < position:
            return False
        end -= len(last)
    for piece in rest:
        found = target.find(piece, position, end)
        if found < 0:
            return False
        position = found + len(piece)
    return True
