"""Autopilot: type a site, get a report.

1. read_site: fetch the homepage and the pages its sitemap lists (robots.txt respected), and store them.
2. discover_searches: the searches the site is written for, taken from its own titles and headings.
3. The project's normal pull asks Google for each search, fetches every ranking page and trains the engine.
4. pick_competitors: the domains that take the most top 10 slots across those searches.
5. build_report: where the site ranks, who it is up against, and specific issues with a comment on each.

Nothing is invented. A search the site does not rank for says so. Without a Google results key on the server, the
report still checks the site's own pages and says that rankings need search data.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from urllib.parse import urljoin, urlsplit

from aeo.crawl.urls import domain_of

MAX_SITE_PAGES = 30
MAX_SEARCHES = 15
TITLE_LIMIT = 60
META_LIMIT = 160
THIN_WORDS = 300

# Sites that rank for almost anything. They stay in the rankings but are never called a competitor.
PLATFORMS = {
    "youtube.com", "reddit.com", "wikipedia.org", "quora.com", "facebook.com", "linkedin.com", "amazon.com",
    "pinterest.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "medium.com", "yelp.com", "github.com",
    "apple.com", "google.com", "microsoft.com", "forbes.com", "indeed.com", "glassdoor.com",
}
GENERIC = {
    "home", "homepage", "about", "about us", "contact", "contact us", "blog", "pricing", "login", "log in", "sign in",
    "sign up", "privacy policy", "privacy", "terms", "terms of service", "terms and conditions", "careers", "faq",
    "faqs", "news", "resources", "support", "help", "menu", "search", "page not found", "cookie policy", "sitemap",
    "thank you", "our team", "get started", "learn more", "read more", "request a demo", "book a demo", "features",
    "testimonials", "case studies", "partners", "press", "events", "jobs", "team", "products", "services", "solutions",
}
LEADING = ("the ", "a ", "an ", "our ", "your ", "welcome to ", "meet ", "introducing ")
# Section labels, not searches: "Questions people ask", "Guides for ...", "Why choose us".
LABEL_STARTS = (
    "questions", "frequently asked", "common questions", "articles", "guides", "resources", "related", "latest", "more ",
    "why choose", "why ", "how it works", "what our", "what people", "get in touch", "ready to", "trusted by", "join ",
    "see how", "learn ", "explore", "read ", "browse", "all ", "featured", "popular", "recent", "head to head",
    "where ", "who ", "built for", "made for", "everything you", "start ", "try ", "talk to", "contact", "our ",
    "meet ", "about ", "see ", "watch ", "download", "sign ", "book ", "request ", "schedule ", "pricing", "plans",
)
BRAND_AFFIXES = ("hq", "app", "inc", "co", "get", "try", "use", "the", "my", "go", "io")
COMMERCIAL = ("software", "app", "apps", "tool", "tools", "alternative", "alternatives", " vs ", "best ", "cost", "price", "how to", "platform", "system", "contractor", "contractors", "what is ")
MIN_SCORE = 2.5  # a title or H1 phrase always passes; an H2 alone passes only when it reads like a search
SPLIT = re.compile(r"\s+[|\-–—:•·]\s+|\s*[|•·]\s*")


@dataclass
class SitePage:
    url: str
    status: str  # fetched, blocked, error
    error: str = ""
    title: str = ""
    meta: str = ""
    h1: str = ""
    h1_count: int = 0
    h2s: list[str] = field(default_factory=list)
    words: int = 0
    tables: int = 0
    has_faq: bool = False
    updated: str | None = None
    links: list[str] = field(default_factory=list)
    fetch_ms: float | None = None

    @property
    def path(self) -> str:
        parts = urlsplit(self.url)
        return parts.path or "/"


def page_facts(url: str, html: str, status: str = "fetched", error: str = "", fetch_ms: float | None = None) -> SitePage:
    from aeo.crawl.extract import extract_html

    if status != "fetched" or not (html or "").strip():
        return SitePage(url=url, status=status or "error", error=error, fetch_ms=fetch_ms)
    found = extract_html(html, url)
    h2s = [str(item.get("text") or "").strip() for item in found.headings if str(item.get("level") or item.get("tag") or "").lower() in {"2", "h2"}]
    lower = html.lower()
    return SitePage(
        url=url,
        status="fetched",
        title=(found.title or "").strip(),
        meta=(found.meta_description or "").strip(),
        h1=(found.h1 or "").strip(),
        h1_count=len(re.findall(r"<h1[\s>]", lower)),
        h2s=[text for text in h2s if text][:12],
        words=int(found.word_count or 0),
        tables=lower.count("<table"),
        has_faq="faq" in " ".join(str(item.get("text") or "") for item in found.headings).lower() or "faqpage" in lower,
        updated=found.updated or found.published,
        links=list(found.links or []),
        fetch_ms=fetch_ms,
    )


# -- 1. read the site -----------------------------------------------------------------------------


def read_site(site: str, settings, database: str, *, fetcher=None, max_pages: int = MAX_SITE_PAGES, progress=None) -> list[SitePage]:
    """Fetch the homepage and up to max_pages of the site's own pages, store them, and return their facts."""
    from aeo.crawl.sitemap import parse_sitemap
    from aeo.ranksim.loop import FetchResult
    from aeo.ranksim.project import _crawler_batch, _record_fetch, clean_site
    from aeo.ranksim.store import open_session

    host = clean_site(site)
    home = f"https://{host}/"
    fetch = fetcher or _crawler_batch(settings, 0.2)
    say = progress or (lambda *_args: None)

    say("Reading your homepage.", 0, 1)
    first = fetch([home])[0]
    home_page = page_facts(home, first.html, first.status, first.error or "", first.fetch_ms)
    results: list[tuple[str, FetchResult]] = [(home, first)]

    urls: list[str] = []
    for start in (f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"):
        try:
            got = fetch([start])[0]
        except Exception:  # noqa: BLE001 - a missing sitemap is normal
            continue
        if got.status != "fetched" or not got.html:
            continue
        try:
            kind, locs = parse_sitemap(got.html)
        except ValueError:
            continue
        if kind == "urlset":
            urls.extend(locs)
        else:
            for child in locs[:3]:
                item = fetch([child])[0]
                if item.status == "fetched" and item.html:
                    try:
                        child_kind, child_locs = parse_sitemap(item.html)
                    except ValueError:
                        continue
                    if child_kind == "urlset":
                        urls.extend(child_locs)
                if len(urls) >= max_pages * 3:
                    break
        if urls:
            break
    if not urls:
        urls = [urljoin(home, link) for link in home_page.links]
    own = [url for url in _unique(urls) if _same_site(url, host) and _is_page(url) and url.rstrip("/") != home.rstrip("/")]
    own = _spread(own, max_pages - 1)
    for index, url in enumerate(own, start=1):
        say(f"Reading {urlsplit(url).path or '/'}", index, len(own))
        results.append((url, fetch([url])[0]))

    session = open_session(database)
    try:
        for url, result in results:
            _record_fetch(session, url, result, None, True, [])
        session.commit()
    finally:
        session.close()
    pages = [home_page] + [page_facts(url, result.html, result.status, result.error or "", result.fetch_ms) for url, result in results[1:]]
    return pages


def _unique(items):
    seen = set()
    out = []
    for item in items:
        key = item.rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _same_site(url: str, host: str) -> bool:
    bare = host.removeprefix("www.")
    found = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return found == bare


def _is_page(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return not re.search(r"\.(jpg|jpeg|png|gif|svg|webp|pdf|zip|xml|css|js|ico|mp4|mp3|woff2?)$", path) and "/wp-content/" not in path and "/tag/" not in path


def _spread(urls: list[str], limit: int) -> list[str]:
    """Shallow pages first (they are usually the ones that target searches), then the rest in sitemap order."""
    ranked = sorted(urls, key=lambda url: (urlsplit(url).path.strip("/").count("/"), len(url)))
    return ranked[: max(0, limit)]


# -- 2. find the searches the site is written for -----------------------------------------------------


def brand_tokens(host: str) -> set[str]:
    """The site's own name as search words: smartcrewhq.com gives {"smartcrewhq", "smartcrew"}."""
    label = host.removeprefix("www.").split(".")[0].lower()
    tokens = {label, label.replace("-", "")}
    for affix in BRAND_AFFIXES:
        if label.endswith(affix) and len(label) - len(affix) >= 3:
            tokens.add(label[: -len(affix)])
        if label.startswith(affix) and len(label) - len(affix) >= 3:
            tokens.add(label[len(affix):])
    tokens.update(part for part in label.split("-") if len(part) > 2)
    return {token for token in tokens if token}


def brand_of(pages: list[SitePage], host: str) -> str:
    """The site's name: the title part most pages repeat, or the domain name with the casing the pages use."""
    counts: Counter = Counter()
    for page in pages:
        parts = [part.strip() for part in SPLIT.split(page.title or "") if part.strip()]
        if len(parts) > 1:
            counts.update({parts[-1]: 1, parts[0]: 0.5})
    readable = [page for page in pages if page.status == "fetched"]
    if counts:
        name, seen = counts.most_common(1)[0]
        # A brand is a short name, not a tagline: "SmartCrew", not "All-in-one dirtwork software: HCSS ...".
        if seen >= max(2, len(readable) * 0.3) and len(name) <= 28 and len(name.split()) <= 3:
            return name
    tokens = sorted(brand_tokens(host), key=len)
    text = " ".join(f"{page.title} {page.h1}" for page in readable)
    for token in tokens:
        match = re.search(rf"\b({re.escape(token)})\b", text, re.I)
        if match and len(token) >= 4:
            return match.group(1) if not match.group(1).islower() else match.group(1).title()
    return tokens[-1].replace("-", " ").title() if tokens else host


def _clean(text: str) -> str:
    value = text.lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9&' ]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" '")
    for lead in LEADING:
        if value.startswith(lead):
            value = value[len(lead):]
    return value.strip()


def discover_searches(pages: list[SitePage], brand: str, *, limit: int = MAX_SEARCHES, host: str = "") -> list[str]:
    """Searches the site's own pages are written for: title parts and H1s first, then H2s. Branded phrases, section
    labels and price fragments are dropped; near duplicates are merged."""
    brand_phrase = _clean(brand)
    brand_words = {brand_phrase.replace(" ", "")} if brand_phrase else set()
    if brand_phrase and " " not in brand_phrase:
        brand_words.add(brand_phrase)
    if host:
        brand_words |= brand_tokens(host)
    brand_words = {word for word in brand_words if len(word) > 2}
    scores: Counter = Counter()
    for page in pages:
        if page.status != "fetched":
            continue
        candidates = [(part, 3.0) for part in SPLIT.split(page.title or "")]
        candidates.append((page.h1, 3.0))
        candidates.extend((text, 1.0) for text in page.h2s[:6])
        for text, weight in candidates:
            phrase = _clean(text or "")
            words = phrase.split()
            if not 2 <= len(words) <= 6 or phrase in GENERIC or len(phrase) > 60:
                continue
            if (brand_phrase and f" {brand_phrase} " in f" {phrase} ") or any(word in brand_words or any(word.startswith(b) for b in brand_words if len(b) >= 5) for word in words):
                continue  # people searching your name already find you; these are not the searches you compete on
            if phrase.startswith(LABEL_STARTS) or phrase.endswith((" us", " here", " today", " now", " is", " facts")):
                continue
            if phrase.count(" and ") >= 1 and weight < 3 and not any(marker in f" {phrase} " for marker in ("software", "apps", "tools")):
                continue
            if sum(1 for word in words if word.isdigit()) >= 1 and not re.search(r"\b(19|20)\d\d\b", phrase):
                continue
            bonus = 0.5 if 2 <= len(words) <= 5 else 0.0
            if any(marker in f" {phrase} " for marker in COMMERCIAL):
                bonus += 1.0
            scores[phrase] += weight + bonus
    picked: list[str] = []
    for phrase, score in sorted(scores.items(), key=lambda item: (-item[1], len(item[0]))):
        if score < MIN_SCORE:
            break
        tokens = set(phrase.split())
        if any(_jaccard(tokens, set(other.split())) >= 0.6 for other in picked):
            continue
        picked.append(phrase)
        if len(picked) >= limit:
            break
    return picked


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


# -- 4. competitors ---------------------------------------------------------------------------------


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


# -- 5. the report --------------------------------------------------------------------------------


def stored_site_pages(project, database: str, limit: int = 80) -> list[SitePage]:
    from aeo.ranksim.store import RsPage, open_session

    session = open_session(database)
    try:
        rows = session.query(RsPage).filter(RsPage.domain.in_([project.domain, "www." + project.domain.removeprefix("www.")])).limit(limit * 2).all()
        pages = []
        for row in rows:
            if not project.is_our_url(row.url):
                continue
            pages.append(page_facts(row.url, row.raw_html or "", row.status or "error", row.error or "", row.fetch_ms))
    finally:
        session.close()
    pages.sort(key=lambda page: (urlsplit(page.url).path.strip("/").count("/"), page.url))
    return pages[:limit]


def _pages_for(database: str, urls: list[str]) -> dict[str, SitePage]:
    from aeo.ranksim.project import loose_key
    from aeo.ranksim.store import RsPage, open_session

    if not urls:
        return {}
    session = open_session(database)
    try:
        wanted = set(urls) | {url.rstrip("/") for url in urls} | {url.rstrip("/") + "/" for url in urls}
        rows = session.query(RsPage).filter(RsPage.url.in_(sorted(wanted))).all()
        found = {}
        for row in rows:
            if row.status == "fetched" and (row.raw_html or "").strip():
                found[loose_key(row.url)] = page_facts(row.url, row.raw_html)
    finally:
        session.close()
    return {url: found[loose_key(url)] for url in urls if loose_key(url) in found}


def build_report(project, meta, database: str, *, ctx=None, google_ready: bool = False) -> dict:
    """Rankings, competitors and issues for one project, from what is stored. Positions are Google's; predicted
    positions, when an engine is built, are the model's order among the stored candidates."""
    from aeo.ranksim.project import latest_google, loose_key

    google = latest_google(project)
    own_pages = stored_site_pages(project, database)
    readable = [page for page in own_pages if page.status == "fetched"]
    by_search = google.by_search() if google is not None else {}
    result_urls = [item.url for entry in (google.searches if google else []) for item in entry.results if 1 <= item.position <= 10]
    docs = _pages_for(database, result_urls)
    competitors = set(meta.competitors or [])

    rankings = []
    for search in project.searches:
        entry = by_search.get(search)
        results = sorted(entry.results, key=lambda item: item.position) if entry else []
        top = [item for item in results if 1 <= item.position <= 10]
        mine = next((item for item in top if project.is_our_url(item.url)), None)
        predicted = None
        if ctx is not None and mine is not None:
            try:
                from aeo.ranksim import workspace as ws

                view = ws.compare_search(ctx, search, meta)
                predicted = {"position": view.your_predicted, "of": view.scored}
            except Exception:  # noqa: BLE001 - the report never fails on one search
                predicted = None
        rankings.append(
            {
                "q": search,
                "position": mine.position if mine else None,
                "url": mine.url if mine else _best_page(search, readable),
                "predicted": predicted,
                "checked": bool(results),
                "top": [
                    {
                        "position": item.position,
                        "url": item.url,
                        "domain": (item.domain or domain_of(item.url)).removeprefix("www."),
                        "title": item.title or (docs.get(item.url).title if docs.get(item.url) else ""),
                        "you": project.is_our_url(item.url),
                        "competitor": (item.domain or domain_of(item.url)).removeprefix("www.") in competitors,
                    }
                    for item in top
                ],
            }
        )

    share = share_of_top10(google, project.domain)
    for row in share:
        row["competitor"] = row["domain"] in competitors
    issues = audit(project, own_pages, rankings, docs)
    ranked = [row for row in rankings if row["position"]]
    checked = [row for row in rankings if row["checked"]]
    return {
        "site": project.site,
        "name": project.name,
        "pages_read": len(readable),
        "pages_failed": len(own_pages) - len(readable),
        "search_data": bool(checked),
        "google_ready": google_ready,
        "pulled": next((entry.pulled_label for entry in (google.searches if google else []) if entry.results), ""),
        "kpis": {
            "searches": len(project.searches),
            "checked": len(checked),
            "top10": len(ranked),
            "top3": sum(1 for row in ranked if row["position"] <= 3),
            "average": round(sum(row["position"] for row in ranked) / len(ranked), 1) if ranked else None,
            "issues": len(issues),
            "high": sum(1 for issue in issues if issue["severity"] == "high"),
        },
        "rankings": rankings,
        "share": share[:12],
        "competitors": list(meta.competitors or []),
        "issues": issues,
        "pages": [
            {"url": page.url, "path": page.path, "status": page.status, "title": page.title, "words": page.words, "issues": sum(1 for issue in issues if issue.get("page") == page.url)}
            for page in own_pages
        ],
    }


def _tokens(text: str) -> set[str]:
    return {word for word in _clean(text).split() if len(word) > 2 and word not in {"and", "the", "for", "with", "your", "you"}}


def _best_page(search: str, pages: list[SitePage]) -> str | None:
    want = _tokens(search)
    best, score = None, 0.0
    for page in pages:
        have = _tokens(f"{page.title} {page.h1}")
        overlap = len(want & have) / len(want) if want else 0.0
        if overlap > score:
            best, score = page.url, overlap
    return best if score >= 0.6 and len(want) >= 2 else None


def _issue(severity: str, kind: str, title: str, comment: str, *, page: str | None = None, search: str | None = None, fix: str = "") -> dict:
    key = re.sub(r"[^a-z0-9]+", "-", f"{kind}-{page or ''}-{search or ''}".lower()).strip("-")[:120]
    return {"id": key, "severity": severity, "kind": kind, "title": title, "comment": comment, "page": page, "search": search, "fix": fix}


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "..."


def audit(project, pages: list[SitePage], rankings: list[dict], docs: dict[str, SitePage]) -> list[dict]:
    """Specific issues, each with a plain comment that names the page, the numbers and what to change."""
    issues: list[dict] = []
    titles: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        if page.status != "fetched":
            reason = page.error or page.status
            issues.append(
                _issue("high", "unreadable", f"We couldn't read {page.path}", f"It answered with {reason}. If Google gets the same, this page can't rank.", page=page.url, fix="Check the page loads for visitors and isn't blocked by robots.txt.")
            )
            continue
        if page.title:
            titles[page.title.strip().lower()].append(page.url)
        if not page.title:
            issues.append(_issue("high", "title-missing", f"{page.path} has no title", "Google shows the title as the blue link. Without one it makes one up.", page=page.url, fix="Add a title that starts with the search this page should win."))
        elif len(page.title) > TITLE_LIMIT:
            issues.append(
                _issue("medium", "title-long", f"Title is {len(page.title)} characters", f"Google shows about {TITLE_LIMIT}, so \"{_short(page.title, TITLE_LIMIT)}\" is roughly what people see on {page.path}.", page=page.url, fix="Move the main search to the front and cut the rest.")
            )
        elif len(page.title) < 25:
            issues.append(_issue("low", "title-short", f"Title is only {len(page.title)} characters", f"\"{page.title}\" leaves room to name what {page.path} is about.", page=page.url, fix="Add the main search this page should rank for."))
        if not page.meta:
            issues.append(_issue("medium", "meta-missing", f"{page.path} has no meta description", "Google will pull a line from the page for the snippet, which is often not the line you want.", page=page.url, fix="Write one or two sentences, about 150 characters, that answer the search."))
        elif len(page.meta) > META_LIMIT:
            issues.append(_issue("low", "meta-long", f"Meta description is {len(page.meta)} characters", f"Google cuts it near {META_LIMIT}, so the end of the sentence on {page.path} won't show.", page=page.url, fix="Shorten it and put the point first."))
        if not page.h1:
            issues.append(_issue("high", "h1-missing", f"{page.path} has no H1", "The main heading tells Google and readers what the page is about.", page=page.url, fix="Add one H1 that matches the title's main search."))
        elif page.h1_count > 1:
            issues.append(_issue("low", "h1-many", f"{page.h1_count} H1 headings on {page.path}", "More than one main heading makes the page's topic less clear.", page=page.url, fix="Keep one H1 and turn the others into H2s."))
        if page.words < THIN_WORDS:
            issues.append(
                _issue("medium", "thin", f"{page.path} has {page.words} words", f"Pages under {THIN_WORDS} words rarely win competitive searches, and the model ranks pages under 150 words behind every fuller page.", page=page.url, fix="Add the details a buyer needs: prices, steps, comparisons, questions.")
            )
    for title, urls in titles.items():
        if len(urls) > 1:
            paths = ", ".join(urlsplit(url).path or "/" for url in urls[:3])
            issues.append(_issue("medium", "title-duplicate", f"{len(urls)} pages share one title", f"{paths} all use \"{_short(title, 50)}\". Google may show only one of them.", page=urls[0], fix="Give each page its own title for its own search."))

    own_by_url = {page.url: page for page in pages if page.status == "fetched"}
    for row in rankings:
        if not row["checked"]:
            continue
        search, top = row["q"], row["top"]
        leaders = [item for item in top if not item["you"]][:3]
        names = ", ".join(item["domain"] for item in leaders)
        if row["position"] is None:
            page = own_by_url.get(row["url"]) if row["url"] else None
            if page is not None:
                issues.append(
                    _issue("high", "not-ranking", f"Not in the top 10 for \"{search}\"", f"{page.path} is about this search but isn't on the first page. {names} are the top 3.", page=page.url, search=search, fix="Compare your page with theirs in the editor and test changes.")
                )
            else:
                issues.append(
                    _issue("high", "no-page", f"No page for \"{search}\"", f"Your site uses this phrase but no page targets it, and it isn't in the top 10. {names} hold the top 3.", search=search, fix="Write a page for it and test it in the editor before you publish.")
                )
            continue
        mine = own_by_url.get(row["url"])
        if row["position"] <= 3 or mine is None:
            continue
        rivals = [docs[item["url"]] for item in leaders if item["url"] in docs]
        notes = []
        if rivals:
            mid = median(doc.words for doc in rivals)
            if mid and mine.words < 0.65 * mid:
                notes.append(f"the top 3 average {int(mid):,} words and yours has {mine.words:,}")
            tables = sum(1 for doc in rivals if doc.tables)
            if tables >= 2 and not mine.tables:
                notes.append(f"{tables} of them have a table and yours doesn't")
            faqs = sum(1 for doc in rivals if doc.has_faq)
            if faqs >= 2 and not mine.has_faq:
                notes.append(f"{faqs} of them answer common questions in an FAQ")
        want = _tokens(search)
        missing = sorted(want - _tokens(mine.title))
        if missing:
            notes.append(f"your title is missing \"{' '.join(missing)}\"")
        detail = "; ".join(notes) if notes else f"{names} are ahead of you"
        issues.append(
            _issue("medium", "behind", f"#{row['position']} for \"{search}\"", f"{mine.path} is on page one, but {detail}.", page=mine.url, search=search, fix="Open it in the editor, make the change, and test it.")
        )
    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda issue: (order[issue["severity"]], issue["kind"] not in {"not-ranking", "no-page", "behind"}, issue["title"]))
    return issues
