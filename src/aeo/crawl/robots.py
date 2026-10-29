"""robots.txt parsing. A missing file allows the crawl. A parse uses the stdlib."""

from __future__ import annotations

from urllib.robotparser import RobotFileParser


def parse_robots(body: str) -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse(body.splitlines())
    return parser


def crawl_delay_seconds(parser: RobotFileParser, user_agent: str, *, cap: float = 10.0) -> float | None:
    delay = parser.crawl_delay(user_agent)
    if delay is None:
        delay = parser.crawl_delay("*")
    if delay is None:
        return None
    return min(float(delay), cap)
