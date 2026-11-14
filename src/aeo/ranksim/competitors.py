"""Who the site is up against: every domain in the top 10 across its searches, and the competitors picked from them."""

from __future__ import annotations

from collections import defaultdict

from aeo.crawl.urls import domain_of

# Sites that rank for almost anything. They stay in the rankings but are never called a competitor.
PLATFORMS = {
    "youtube.com", "reddit.com", "wikipedia.org", "quora.com", "facebook.com", "linkedin.com", "amazon.com",
    "pinterest.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "medium.com", "yelp.com", "github.com",
    "apple.com", "google.com", "microsoft.com", "forbes.com", "indeed.com", "glassdoor.com",
}


def _base(domain: str) -> str:
    parts = (domain or "").lower().removeprefix("www.").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (domain or "").lower()


def share_of_top10(google, own_domain: str) -> list[dict]:
    """Every domain in the top 10 across the tracked searches: slots held, best and average position."""
    slots: dict[str, list[int]] = defaultdict(list)
    searches: dict[str, set] = defaultdict(set)
    if google is None:
        return []
    for entry in google.searches:
        for item in entry.results:
            if not 1 <= item.position <= 10:
                continue
            domain = (item.domain or domain_of(item.url)).lower().removeprefix("www.")
            slots[domain].append(item.position)
            searches[domain].add(entry.search)
    own = own_domain.lower().removeprefix("www.")
    rows = []
    for domain, positions in slots.items():
        rows.append(
            {
                "domain": domain,
                "slots": len(positions),
                "searches": len(searches[domain]),
                "best": min(positions),
                "average": round(sum(positions) / len(positions), 1),
                "you": domain == own or domain.endswith("." + own),
                "platform": _base(domain) in PLATFORMS,
            }
        )
    rows.sort(key=lambda row: (-row["slots"], row["average"]))
    return rows


def pick_competitors(google, own_domain: str, *, limit: int = 5) -> list[str]:
    return [row["domain"] for row in share_of_top10(google, own_domain) if not row["you"] and not row["platform"]][:limit]
