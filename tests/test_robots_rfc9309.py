"""robots.txt per RFC 9309 on every Python version, and no third-party crawl with a placeholder
user agent."""

import socket

import pytest

from aeo.crawl.fetch import Crawler, user_agent_problem
from aeo.crawl.robots import crawl_delay_seconds, parse_robots, product_token
from aeo.errors import ProviderConfigError

UA = "RanksimBot/0.1 (+https://ranksim-crawler.org/bot)"


def allowed(robots: str, path: str, agent: str = UA) -> bool:
    return parse_robots(robots).can_fetch(agent, "https://site.example" + path)


def test_wildcards_and_end_anchor():
    rules = "User-agent: *\nDisallow: /*.pdf$\nDisallow: /private*/data\n"
    assert not allowed(rules, "/files/report.pdf")  # stdlib on 3.11/3.12 allowed this
    assert allowed(rules, "/files/report.pdf?download=1")  # $ anchors the end
    assert not allowed(rules, "/private-area/data/x")
    assert allowed(rules, "/public/data")


def test_longest_match_wins_and_allow_wins_a_tie():
    assert allowed("User-agent: *\nDisallow: /\nAllow: /blog\n", "/blog/post")  # file order does not matter
    assert not allowed("User-agent: *\nAllow: /page\nDisallow: /*.html\n", "/page.html")  # 7 chars beat 5
    assert allowed("User-agent: *\nDisallow: /a\nAllow: /a\n", "/a/b")


def test_group_selection_by_product_token():
    rules = "User-agent: *\nDisallow: /\n\nUser-agent: ranksimbot\nAllow: /\nDisallow: /admin\n"
    assert allowed(rules, "/anything")
    assert not allowed(rules, "/admin/x")
    assert not allowed(rules, "/anything", agent="OtherBot/2.0")
    merged = "User-agent: RanksimBot\nDisallow: /a\n\nUser-agent: ranksimbot\nDisallow: /b\n"
    assert not allowed(merged, "/a") and not allowed(merged, "/b") and allowed(merged, "/c")
    shared = "User-agent: OtherBot\nUser-agent: RanksimBot\nDisallow: /shared\n"
    assert not allowed(shared, "/shared/1")
    assert product_token(UA) == "ranksimbot"


def test_edge_cases():
    assert allowed("Disallow: /\nUser-agent: *\nAllow: /\n", "/x")  # a rule before any group is ignored
    assert allowed("User-agent: *\nDisallow:\n", "/x")  # empty pattern matches nothing
    assert allowed("User-agent: *\nDisallow: /\n", "/robots.txt")
    assert allowed("", "/x")  # no file, no rules
    assert not allowed("User-agent: *\nDisallow: /caf%C3%A9\n", "/café/menu")  # percent-encoding compared alike
    delays = parse_robots("User-agent: *\nCrawl-delay: 3\n\nUser-agent: RanksimBot\nCrawl-delay: 20\n")
    assert crawl_delay_seconds(delays, UA) == 10.0 and crawl_delay_seconds(delays, "OtherBot") == 3.0


def test_user_agent_needs_a_real_contact():
    assert "placeholder" in user_agent_problem("AEOSimulatorBot/0.1 (+https://example.com/aeo-simulator)")
    assert "no contact" in user_agent_problem("ranksim-check (contact: repo owner)")
    assert "placeholder" in user_agent_problem("Bot/1 (bot@example.org)")
    assert user_agent_problem(UA) is None
    assert user_agent_problem("Bot/1 (crawler@ranksim-crawler.org)") is None


def test_a_placeholder_user_agent_does_not_crawl_a_real_host(monkeypatch):
    def public(host, port, *args, **kwargs):
        if host == "competitor.org":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))]
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", public)
    crawler = Crawler(user_agent="AEOSimulatorBot/0.1 (+https://example.com/aeo-simulator)", delay_seconds=0)
    try:
        with pytest.raises(ProviderConfigError, match="CRAWL_USER_AGENT"):
            crawler.fetch("https://competitor.org/")
        # Hosts that do not resolve (synthetic .example sites in mock runs) fail as before.
        assert "Could not resolve" in crawler.fetch("https://northwind.example/").error
    finally:
        crawler.close()
    local = Crawler(user_agent="ranksim-test", allow_private=True)  # local labs and tests are exempt
    local.close()


def test_index_serp_stops_at_the_first_refusal_instead_of_logging_every_url(monkeypatch):
    import aeo.ranksim.index_serp as index_serp
    from aeo.config import get_settings

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))]
    )
    settings = get_settings().model_copy(update={"crawl_user_agent": "AEOSimulatorBot/0.1 (+https://example.com/x)"})
    with pytest.raises(ProviderConfigError):
        index_serp._fetch_host(["https://competitor.org/a", "https://competitor.org/b"], settings, 0)
