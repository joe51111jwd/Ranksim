"""Competitors, their profiles, and content gaps.

1. ``classify_domains``: every domain in the top 10 gets a kind (competitor, directory, marketplace, forum, social,
   video, publisher, platform, you) and a reason. Known lists first, then transparent heuristics.
2. ``pick_competitors``: real competitors ranked by visibility across the site's searches.
3. ``competitor_profiles``: what each competitor ranks for, its best pages, title patterns, word counts, structured
   data. Directories, platforms and publishers are reported as their own groups.
4. ``content_gaps``: per search, the section topics, terms and structured data that the pages above the user's page
   share and the user's page lacks, with the pages that have each one.

Pure functions over a GoogleSnapshot and ``{url: html}``. ``stored_html`` and ``discovery_inputs`` read the store.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from statistics import median
from urllib.parse import urlsplit

from aeo.crawl.urls import domain_of
from aeo.ranksim.discovery_next import STOP, _label_reason, base_domain, clean, content_stems, host_tokens, stem

MAX_COMPETITORS = 5
MAX_PROFILES = 8
MAX_GAP_TOPICS = 10
MAX_GAP_TERMS = 15
COMMON_SHARE = 0.4  # a gap is something at least 40% of the pages above you have
COMMON_MIN_PAGES = 2

DIRECTORIES = {
    "g2.com", "capterra.com", "getapp.com", "softwareadvice.com", "trustradius.com", "sourceforge.net", "slashdot.org",
    "crozdesk.com", "softwaresuggest.com", "saasworthy.com", "goodfirms.co", "clutch.co", "yelp.com", "angi.com",
    "angieslist.com", "homeadvisor.com", "thumbtack.com", "bbb.org", "yellowpages.com", "manta.com", "houzz.com",
    "trustpilot.com", "producthunt.com", "alternativeto.net", "saashub.com", "gartner.com", "selecthub.com",
    "technologyadvice.com", "softwarereviews.com", "featuredcustomers.com", "crunchbase.com", "zoominfo.com",
    "buildzoom.com", "porch.com", "bark.com", "expertise.com", "nextdoor.com", "tripadvisor.com", "mapquest.com",
    "softwareconnect.com", "itqlick.com", "financesonline.com", "comparecamp.com", "getapp.co.uk", "capterra.co.uk",
    "softwareadvice.co.uk", "g2crowd.com", "stackshare.io", "builtin.com", "glassdoor.com", "indeed.com",
    "constructconnect.com", "dodge.construction.com", "bluebook.com", "thebluebook.com",
}
MARKETPLACES = {
    "amazon.com", "ebay.com", "etsy.com", "walmart.com", "alibaba.com", "aliexpress.com", "appsumo.com",
    "homedepot.com", "lowes.com", "target.com", "bestbuy.com", "apps.apple.com", "play.google.com",
    "marketplace.atlassian.com", "apps.shopify.com", "wordpress.org", "chrome.google.com",
}
FORUMS = {"reddit.com", "quora.com", "stackexchange.com", "stackoverflow.com", "contractortalk.com", "tapatalk.com", "ycombinator.com", "discord.com"}
SOCIAL = {"facebook.com", "linkedin.com", "x.com", "twitter.com", "instagram.com", "tiktok.com", "pinterest.com", "threads.net", "tumblr.com", "snapchat.com"}
VIDEO = {"youtube.com", "youtu.be", "vimeo.com", "wistia.com", "dailymotion.com", "twitch.tv", "loom.com", "spotify.com", "podcasts.apple.com"}
PUBLISHERS = {
    "wikipedia.org", "medium.com", "forbes.com", "nytimes.com", "wsj.com", "businessinsider.com", "investopedia.com",
    "techradar.com", "pcmag.com", "zdnet.com", "cnet.com", "techrepublic.com", "nerdwallet.com", "fool.com",
    "entrepreneur.com", "inc.com", "constructiondive.com", "enr.com", "forconstructionpros.com", "equipmentworld.com",
    "theverge.com", "wired.com", "investors.com", "bloomberg.com", "reuters.com", "cnbc.com", "usnews.com",
    "substack.com", "wikihow.com", "britannica.com", "merriam-webster.com", "fastcompany.com", "hbr.org",
    "bbc.co.uk", "bbc.com", "cnn.com", "techcrunch.com", "theguardian.com", "nbcnews.com", "foxnews.com", "usatoday.com",
    "washingtonpost.com", "npr.org", "apnews.com", "huffpost.com", "buzzfeed.com", "vox.com", "axios.com",
    "mashable.com", "engadget.com", "venturebeat.com", "time.com", "theatlantic.com", "economist.com", "ft.com",
    "latimes.com", "nypost.com", "independent.co.uk", "telegraph.co.uk", "dailymail.co.uk", "businessinsider.com",
}
PLATFORMS = {"google.com", "apple.com", "microsoft.com", "github.com", "adobe.com", "support.google.com"}
GROUP = {
    "competitor": "competitors",
    "directory": "directories",
    "marketplace": "directories",
    "forum": "platforms",
    "social": "platforms",
    "video": "platforms",
    "platform": "platforms",
    "publisher": "publishers",
}
# A domain name ending in one of these words ("constructionreviews") is a directory; one starting with it
# ("reviewtrackers") is usually a product, so only the end counts.
DIRECTORY_WORDS = ("reviews", "compare", "comparison", "directory", "listings", "finder", "ratings", "top10", "toptens")
# Only words that are rarely the end of a product name. "wire" (Fieldwire), "insider" (useinsider) would misfire.
PUBLISHER_WORDS = ("news", "magazine", "journal", "digest", "weekly", "gazette", "tribune")
# A known site in another country ("amazon.co.uk", "capterra.ca") matches by its name. Generic names never do.
AMBIGUOUS_NAMES = {"target", "porch", "bark", "expertise", "builtin", "indeed", "time", "inc", "fool", "wired", "vox", "medium"}
FORUM_PREFIXES = ("community.", "forum.", "forums.", "discuss.", "answers.", "discourse.")
LISTICLE = re.compile(r"\b(best|top \d+|\d+ best|\d+ top|alternatives? to|vs\.?|versus|compared|reviews?|ranked)\b", re.I)
SELLING_SCHEMA = {"product", "softwareapplication", "webapplication", "mobileapplication", "offer", "aggregateoffer", "service", "localbusiness", "homeandconstructionbusiness", "generalcontractor"}
ARTICLE_SCHEMA = {"article", "newsarticle", "blogposting", "reportagenewsarticle"}
SELLING_WORDS = re.compile(r"\b(book a demo|request a demo|get a demo|schedule a demo|start (?:your |a )?free trial|try it free|start free|see pricing|get a quote|request a quote|buy now|add to cart|sign up free)\b", re.I)
BUTTONS = re.compile(r"<(a|button)\b[^>]*>(.*?)</\1>", re.I | re.S)
CHROME = re.compile(r"<(footer|nav|aside|header)\b.*?</\1\s*>", re.I | re.S)
MAX_HTML = 1_500_000
TEXT_STOP = STOP | {
    "will", "has", "have", "had", "was", "were", "been", "not", "but", "they", "them", "their", "there", "these",
    "those", "than", "then", "also", "more", "most", "into", "out", "up", "about", "over", "any", "each", "other",
    "some", "such", "only", "own", "same", "so", "too", "very", "just", "get", "use", "used", "using", "make", "need",
    "like", "many", "much", "may", "one", "two", "three", "if", "no", "yes", "he", "she", "his", "her", "who", "whom",
    "would", "could", "should", "i", "it's", "you're", "we're", "let", "lets", "help", "helps", "see", "way", "well",
    "here", "where", "while", "without", "within", "every", "all", "both", "few", "between", "through", "after",
    "before", "because", "what's", "click", "read", "learn", "contact", "privacy", "policy", "cookie", "cookies",
    "copyright", "rights", "reserved", "menu", "home", "s", "t", "don't", "can't", "re", "ve", "ll", "dont", "cant",
    "made", "easy", "easier", "simple", "simply", "fast", "faster", "better", "quick", "quickly", "really", "now",
    "today", "even", "still", "always", "never", "back", "first", "last", "next", "want", "know", "take", "keep", "go",
    "going", "come", "find", "give", "look", "start", "try", "able", "sure", "lot", "lots", "thing", "things", "etc",
    "youll", "youre", "were", "weve", "its", "thats", "whats", "great", "good", "every", "everything", "anything",
    "something", "via", "per", "within", "around", "across", "less", "get", "got", "set", "new", "make", "makes",
}


def norm(word: str) -> str:
    """Word form key for matching: "scheduling", "schedules" and "schedule" all give "schedul"."""
    word = stem(word)
    for suffix in ("ing", "ed", "er", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _domain(item) -> str:
    try:
        return (getattr(item, "domain", "") or domain_of(item.url) or "").lower().removeprefix("www.")
    except (ValueError, TypeError, AttributeError):
        return ""


def _known(host: str, base: str, known: set[str]) -> str | None:
    """The known entry this host matches: exact host, its registrable domain, or the same name in another country."""
    for candidate in (host, base):
        if candidate in known:
            return candidate
    name = base.split(".")[0]
    if len(name) >= 4 and name not in AMBIGUOUS_NAMES:
        for entry in known:
            if entry.count(".") == 1 and entry.split(".")[0] == name:
                return entry
    return None


# -- page facts --------------------------------------------------------------------------------------


def page_profile(url: str, html: str) -> dict:
    """What a page is made of: headings, word count, structured data types, term counts, selling signals."""
    from aeo.crawl.extract import extract_html

    html = (html or "")[:MAX_HTML]
    # Footer, nav, aside and header hold site chrome ("Company", "Resources", "Subscribe"), not the page's content.
    body = CHROME.sub(" ", html)
    try:
        found = extract_html(body, url)
        types: list[str] = []
        for obj in found.schema_objects or []:
            types.extend(_schema_types(obj))
    except (RecursionError, ValueError, TypeError):
        return _empty_profile(url)
    headings = [
        {"level": int(item.get("level") or 0), "text": str(item.get("text") or "").strip()}
        for item in found.headings or []
        if str(item.get("text") or "").strip()
    ]
    headings = [item for item in headings if item["level"] in (2, 3) and _is_section(item["text"])]
    terms, surface = _terms(found.main_text or "", url)
    buttons = " ".join(re.sub(r"<[^>]+>", " ", match.group(2)) for match in BUTTONS.finditer(html[:400_000]))
    return {
        "url": url,
        "title": (found.title or "").strip(),
        "h1": (found.h1 or "").strip(),
        "headings": headings[:60],
        "words": int(found.word_count or 0),
        "schema": sorted(set(types)),
        "terms": terms,
        "surface": surface,
        "selling": bool(SELLING_WORDS.search(buttons)),
    }


CONTENT_LABELS = {"pricing", "features", "faq", "faqs", "testimonials", "case studies", "integrations", "reviews", "how it works", "frequently asked questions"}


def _is_section(text: str) -> bool:
    """A heading that names content. Site chrome ("Company", "Resources", "Subscribe") is left out."""
    phrase = clean(text)
    return bool(phrase) and (phrase in CONTENT_LABELS or not _label_reason(phrase)) and phrase not in {"company", "product", "legal", "follow us", "newsletter"}


def _empty_profile(url: str) -> dict:
    return {"url": url, "title": "", "h1": "", "headings": [], "words": 0, "schema": [], "terms": Counter(), "surface": {}, "selling": False, "unreadable": True}


def _profile(cache: dict, url: str, html: str) -> dict:
    if url not in cache:
        cache[url] = page_profile(url, html)
    return cache[url]


def _schema_types(obj, depth: int = 0) -> list[str]:
    out: list[str] = []
    if depth > 8:
        return out
    if isinstance(obj, dict):
        kind = obj.get("@type")
        if isinstance(kind, str):
            out.append(kind)
        elif isinstance(kind, list):
            out.extend(str(item) for item in kind if isinstance(item, str))
        for key in ("@graph", "mainEntity", "itemListElement", "offers", "publisher"):
            value = obj.get(key)
            if isinstance(value, (dict, list)):
                out.extend(_schema_types(value, depth + 1))
    elif isinstance(obj, list):
        for item in obj[:40]:
            out.extend(_schema_types(item, depth + 1))
    return [item.split("/")[-1] for item in out if item]


def _terms(text: str, url: str) -> tuple[Counter, dict[str, str]]:
    """One and two word terms keyed by word form (``norm``), stop words and the page's own brand out. Also the
    most common way each term is written, for display."""
    try:
        own = host_tokens(domain_of(url)) if url else set()
    except ValueError:
        own = set()
    counts: Counter = Counter()
    forms: dict[str, Counter] = defaultdict(Counter)
    for sentence in re.split(r"[.!?;:\n]+", text or ""):  # word pairs never cross a sentence end
        raw = clean(sentence).split()
        words = [norm(word) for word in raw]

        def ok(index: int) -> bool:
            word = raw[index]
            return word not in TEXT_STOP and stem(word) not in TEXT_STOP and len(word) >= 3 and not word.isdigit() and stem(word) not in own and word not in own

        for index, word in enumerate(words):
            if not ok(index):
                continue
            counts[word] += 1
            forms[word][raw[index]] += 1
            if index + 1 < len(words) and ok(index + 1):
                key = f"{word} {words[index + 1]}"
                counts[key] += 1
                forms[key][f"{raw[index]} {raw[index + 1]}"] += 1
    return counts, {key: found.most_common(1)[0][0] for key, found in forms.items()}


# -- 1. classify domains ------------------------------------------------------------------------------


def classify_domain(domain: str, *, titles: list[str] | None = None, pages: list[dict] | None = None) -> tuple[str, str]:
    """Kind and reason for one domain. ``pages`` are page_profile dicts for its ranking pages, when stored."""
    host = (domain or "").lower().removeprefix("www.")
    base = base_domain(host)
    for kind, known in (("marketplace", MARKETPLACES), ("forum", FORUMS), ("video", VIDEO), ("directory", DIRECTORIES), ("social", SOCIAL), ("publisher", PUBLISHERS), ("platform", PLATFORMS)):
        if host in known:
            return kind, f"{host} is a known {kind}"
    for kind, known in (("directory", DIRECTORIES), ("marketplace", MARKETPLACES), ("forum", FORUMS), ("social", SOCIAL), ("video", VIDEO), ("publisher", PUBLISHERS), ("platform", PLATFORMS)):
        match = _known(host, base, known)
        if match:
            return kind, f"{match} is a known {kind}"
    if host.startswith(FORUM_PREFIXES):
        return "forum", "a community or forum site"
    tld = host.rsplit(".", 1)[-1]
    if tld in {"gov", "edu", "mil"} or base.split(".")[-2:-1] in (["gov"], ["edu"], ["ac"], ["gob"]):
        return "publisher", "a government or school site"
    label = base.split(".")[0]
    parts = label.split("-")
    for word in DIRECTORY_WORDS:
        if label.endswith(word) or word in parts:
            return "directory", f'the name ends in "{word}"'
    for word in PUBLISHER_WORDS:
        if label.endswith(word) or word in parts:
            return "publisher", f'the name ends in "{word}"'
    pages = pages or []
    titles = [title for title in (titles or []) if title]
    product_schema = any(set(t.lower() for t in page.get("schema") or []) & SELLING_SCHEMA for page in pages)
    buttons = any(page.get("selling") for page in pages)
    articles_only = bool(pages) and all(set(t.lower() for t in page.get("schema") or []) <= ARTICLE_SCHEMA | {"organization", "website", "webpage", "breadcrumblist", "person", "imageobject", "itemlist", "faqpage"} for page in pages)
    listicles = len(titles) >= 2 and all(LISTICLE.search(title) for title in titles)
    if product_schema:
        return "competitor", "marks up a product or service on its ranking pages"
    if listicles and articles_only:
        return "publisher", "only ranks with list articles"
    if buttons:
        return "competitor", "has demo, trial or quote buttons on its ranking pages"
    return "competitor", "a business site in your results"


def classify_domains(google, own_domain: str, *, profiles: dict[str, dict] | None = None, is_ours=None) -> list[dict]:
    """Every domain in the top 10: kind, reason, slots, searches, positions, visibility. Sorted by visibility."""
    if google is None:
        return []
    own = (own_domain or "").lower().removeprefix("www.")
    profiles = profiles or {}
    by_host: dict[str, list[dict]] = defaultdict(list)
    for entry in google.searches:
        for item in entry.results:
            domain = _domain(item)
            if domain and 1 <= (item.position or 0) <= 10:
                by_host[domain].append({"q": entry.search, "position": item.position, "url": item.url, "title": item.title or ""})

    def ours(url: str) -> bool:
        try:
            return bool(is_ours(url))
        except ValueError:
            return False

    # A business's subdomains (help.hcss.com, hcss.com) are one competitor; a forum or app store subdomain is not.
    grouped: dict[str, dict] = {}
    for host, found in by_host.items():
        mine = bool(own) and (host == own or host.endswith("." + own)) or (is_ours is not None and all(ours(hit["url"]) for hit in found))
        if mine:
            kind, reason = "you", "your site"
        else:
            kind, reason = classify_domain(host, titles=[hit["title"] for hit in found], pages=[profiles[hit["url"]] for hit in found if hit["url"] in profiles])
        key = base_domain(host) if kind in {"competitor", "you"} else host
        slot = grouped.setdefault(key, {"hosts": {}, "hits": []})
        slot["hosts"][host] = (kind, reason, len(found))
        slot["hits"].extend(found)
    rows = []
    for domain, slot in grouped.items():
        found = slot["hits"]
        kind, reason, _count = max(slot["hosts"].values(), key=lambda item: item[2])
        if len(slot["hosts"]) > 1 and kind != "you":
            reason = f"{reason}; includes {', '.join(sorted(slot['hosts']))}"
        if domain not in slot["hosts"] and len(slot["hosts"]) == 1:
            domain = next(iter(slot["hosts"]))  # one subdomain alone keeps its own name
        best_per_search: dict[str, int] = {}
        for hit in found:
            best_per_search[hit["q"]] = min(best_per_search.get(hit["q"], 99), hit["position"])
        positions = [hit["position"] for hit in found]
        rows.append(
            {
                "domain": domain,
                "kind": kind,
                "group": "you" if kind == "you" else GROUP[kind],
                "reason": reason,
                "slots": len(found),
                "searches": len(best_per_search),
                "best": min(positions),
                "average": round(sum(positions) / len(positions), 1),
                "visibility": round(sum((11 - position) / 10 for position in best_per_search.values()), 2),
                "you": kind == "you",
                "platform": kind not in {"competitor", "you"},
                "hosts": sorted(slot["hosts"]),
                "hits": sorted(found, key=lambda hit: (hit["position"], hit["q"])),
            }
        )
    rows.sort(key=lambda row: (-row["visibility"], -row["slots"], row["average"], row["domain"]))
    return rows


def share_rows(google, own_domain: str, **kwargs) -> list[dict]:
    """``share_of_top10`` rows with kind and reason added (the old keys keep their meaning, sorted the old way)."""
    rows = [{key: value for key, value in row.items() if key != "hits"} for row in classify_domains(google, own_domain, **kwargs)]
    rows.sort(key=lambda row: (-row["slots"], row["average"]))
    return rows


def pick_competitors(google, own_domain: str, *, limit: int = MAX_COMPETITORS, **kwargs) -> list[str]:
    return [row["domain"] for row in classify_domains(google, own_domain, **kwargs) if row["kind"] == "competitor"][:limit]


# -- 2. profiles ---------------------------------------------------------------------------------------


def title_patterns(titles: list[str], domain: str) -> dict:
    titles = [title.strip() for title in titles if title and title.strip()]
    if not titles:
        return {}
    separators = Counter()
    for title in titles:
        for sep in ("|", " - ", " – ", " — ", ":", "»", "·"):
            if sep in title:
                separators[sep.strip()] += 1
    brand = host_tokens(domain)
    with_brand = sum(1 for title in titles if any(token in clean(title).replace(" ", "") for token in brand))
    words: Counter = Counter()
    for title in titles:
        words.update({word for word in content_stems(title) if word not in brand and len(word) > 2})
    return {
        "count": len(titles),
        "average_length": round(sum(len(title) for title in titles) / len(titles)),
        "separator": separators.most_common(1)[0][0] if separators else None,
        "brand_in_title": round(with_brand / len(titles), 2),
        "with_year": round(sum(1 for title in titles if re.search(r"\b20\d\d\b", title)) / len(titles), 2),
        "with_number": round(sum(1 for title in titles if re.search(r"\b\d{1,2}\b", title)) / len(titles), 2),
        "listicle": round(sum(1 for title in titles if LISTICLE.search(title)) / len(titles), 2),
        "common_words": [word for word, count in words.most_common(8) if count >= 2 or len(titles) == 1][:6],
    }


def competitor_profiles(google, own_domain: str, docs: dict[str, str] | None = None, *, limit: int = MAX_PROFILES, is_ours=None, cache: dict | None = None) -> dict:
    """Profiles of competitors, plus directories, platforms and publishers as their own groups."""
    docs = docs or {}
    cache = {} if cache is None else cache
    profiles = {url: _profile(cache, url, html) for url, html in docs.items() if html}
    rows = classify_domains(google, own_domain, profiles=profiles, is_ours=is_ours)
    out: dict[str, list] = {"competitors": [], "directories": [], "platforms": [], "publishers": []}
    for row in rows:
        if row["kind"] == "you":
            continue
        group = out[row["group"]]
        if len(group) >= limit:
            continue
        pages: dict[str, dict] = {}
        for hit in row["hits"]:
            page = pages.setdefault(hit["url"], {"url": hit["url"], "title": hit["title"], "searches": [], "best": hit["position"]})
            page["searches"].append({"q": hit["q"], "position": hit["position"]})
            page["best"] = min(page["best"], hit["position"])
            if not page["title"]:
                page["title"] = hit["title"]
        for page in pages.values():
            facts = profiles.get(page["url"])
            page["words"] = facts["words"] if facts else None
            page["schema"] = facts["schema"] if facts else []
            page["stored"] = facts is not None
        best_pages = sorted(pages.values(), key=lambda page: (-len(page["searches"]), page["best"]))
        stored = [profiles[url] for url in pages if url in profiles]
        word_counts = [facts["words"] for facts in stored if facts["words"]]
        schema: Counter = Counter()
        for facts in stored:
            schema.update(set(facts["schema"]))
        h2_counts = [sum(1 for item in facts["headings"] if item["level"] == 2) for facts in stored]
        group.append(
            {
                "domain": row["domain"],
                "kind": row["kind"],
                "reason": row["reason"],
                "visibility": row["visibility"],
                "searches": row["searches"],
                "slots": row["slots"],
                "average": row["average"],
                "best": row["best"],
                "rankings": [{"q": hit["q"], "position": hit["position"], "url": hit["url"], "title": hit["title"]} for hit in row["hits"]],
                "best_pages": best_pages[:5],
                "titles": title_patterns([page["title"] for page in pages.values()], row["domain"]),
                "words": {"median": int(median(word_counts)), "min": min(word_counts), "max": max(word_counts), "pages": len(word_counts)} if word_counts else None,
                "schema": [{"type": kind, "pages": count} for kind, count in schema.most_common(8)],
                "h2_median": int(median(h2_counts)) if h2_counts else None,
                "pages_stored": len(stored),
                "pages_ranking": len(pages),
            }
        )
    return out


# -- 3. content gaps -----------------------------------------------------------------------------------


def _topic_key(text: str) -> set[str]:
    return {norm(word) for word in clean(text).split() if word not in TEXT_STOP and stem(word) not in TEXT_STOP and len(word) > 2 and not word.isdigit()}


def _same_topic(a: set[str], b: set[str]) -> bool:
    """Two headings are one topic when they share two words and half of the shorter one, or are the same word."""
    if not a or not b:
        return False
    shared = len(a & b)
    if len(a) == 1 or len(b) == 1:
        return shared == 1 and max(len(a), len(b)) <= 2  # "Pricing" and "Pricing plans", never "Job costing software"
    return shared >= 2 and shared / min(len(a), len(b)) >= 0.5


def _cluster(headings: list[tuple[str, str]]) -> list[dict]:
    """Group headings from several pages into topics (see ``_same_topic``)."""
    topics: list[dict] = []
    for url, text in headings:
        key = _topic_key(text)
        if not key:
            continue
        home = next((topic for topic in topics if _same_topic(key, topic["key"])), None)
        if home is None:
            home = {"key": set(key), "pages": {}, "texts": Counter()}
            topics.append(home)
        home["pages"].setdefault(url, text)
        home["texts"][text] += 1
    return topics


def _common(count: int, total: int) -> bool:
    return count >= COMMON_MIN_PAGES and count >= math.ceil(COMMON_SHARE * total)


def gap_for(search: str, page_url: str | None, page_html: str | None, above: list[tuple[str, str]], *, own_domain: str = "", cache: dict | None = None) -> dict:
    """Gaps between one page and the pages above it for one search. ``above`` is ``[(url, html)]``."""
    cache = {} if cache is None else cache
    seen: set[str] = set()
    pages = []
    for url, html in above:
        if html and url not in seen:
            seen.add(url)
            profile = _profile(cache, url, html)
            if not profile.get("unreadable"):
                pages.append(profile)
    mine = _profile(cache, page_url or "", page_html) if page_html else None
    result = {"q": search, "page": page_url, "page_stored": mine is not None, "checked": mine is not None, "compared": [page["url"] for page in pages], "topics": [], "terms": [], "schema": [], "words": None}
    if len(pages) < COMMON_MIN_PAGES:
        result["note"] = "Fewer than two pages above yours are stored, so there is nothing to compare yet."
        return result
    if mine is None:
        result["note"] = "Your page for this search isn't stored, so this is what the pages above share, not checked against yours."
    own_tokens = host_tokens(own_domain) if own_domain else set()
    total = len(pages)
    my_heading_keys = [_topic_key(item["text"]) for item in (mine["headings"] if mine else [])]
    if mine and mine.get("h1"):
        my_heading_keys.append(_topic_key(mine["h1"]))
    my_terms = mine["terms"] if mine else Counter()

    for topic in _cluster([(page["url"], item["text"]) for page in pages for item in page["headings"]]):
        if not _common(len(topic["pages"]), total):
            continue
        if any(_same_topic(key, topic["key"]) for key in my_heading_keys):
            continue
        label = min(topic["texts"], key=lambda text: (-topic["texts"][text], len(text)))
        core = sorted(topic["key"], key=lambda word: -sum(1 for text in topic["pages"].values() if word in _topic_key(text)))[:3]
        result["topics"].append(
            {
                "topic": label,
                "pages": len(topic["pages"]),
                "of": total,
                "in_your_text": bool(mine) and all(my_terms.get(word) for word in core),
                "evidence": [{"url": url, "heading": text} for url, text in topic["pages"].items()][:6],
            }
        )
    result["topics"].sort(key=lambda item: (-item["pages"], item["topic"]))
    result["topics"] = result["topics"][:MAX_GAP_TOPICS]

    seen_in: dict[str, list[str]] = defaultdict(list)
    uses: dict[str, int] = Counter()
    surface: dict[str, Counter] = defaultdict(Counter)
    own_norms = {norm(token) for token in own_tokens} | own_tokens
    for page in pages:
        for term, count in page["terms"].items():
            if count >= 2 and not (set(term.split()) & own_norms):  # your own name is never a gap
                seen_in[term].append(page["url"])
                uses[term] += count
                surface[term][page.get("surface", {}).get(term, term)] += count
    query_words = {norm(word) for word in clean(search).split()}
    terms = []
    for term, urls in seen_in.items():
        if not _common(len(urls), total) or my_terms.get(term) or (mine and all(my_terms.get(word) for word in term.split())):
            continue  # a pair counts as covered when both words are on your page ("schedule crews" covers "crew scheduling")
        if " " not in term and any(term in other.split() and other in seen_in and len(seen_in[other]) >= len(urls) for other in seen_in if " " in other):
            continue  # "job cost" covers "job"
        terms.append({"term": surface[term].most_common(1)[0][0], "pages": len(urls), "of": total, "uses": uses[term], "in_search": bool(set(term.split()) & query_words), "evidence": urls[:6]})
    terms.sort(key=lambda item: (-item["in_search"], -item["pages"], -item["uses"], item["term"]))
    result["terms"] = terms[:MAX_GAP_TERMS]

    schema: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        for kind in page["schema"]:
            schema[kind].append(page["url"])
    mine_schema = set(mine["schema"]) if mine else set()
    result["schema"] = [
        {"type": kind, "pages": len(urls), "of": total, "evidence": urls[:6]}
        for kind, urls in sorted(schema.items(), key=lambda kv: -len(kv[1]))
        if _common(len(urls), total) and kind not in mine_schema
    ]
    counts = [page["words"] for page in pages if page["words"]]
    if counts:
        result["words"] = {"yours": mine["words"] if mine else None, "median_above": int(median(counts)), "max_above": max(counts)}
    return result


def content_gaps(rankings: list[dict], docs: dict[str, str], *, is_ours=None, own_domain: str = "", limit: int = 20, cache: dict | None = None) -> list[dict]:
    """Gaps for each search in ``report["rankings"]`` shape: q, position, url, top [{position, url, you}]."""
    cache = {} if cache is None else cache

    def ours(url: str) -> bool:
        try:
            return bool(is_ours and is_ours(url))
        except ValueError:
            return False

    out = []
    for row in rankings[:limit]:
        top = sorted((item for item in row.get("top") or [] if item.get("url")), key=lambda item: item.get("position") or 99)
        if not top:
            continue
        mine = row.get("position")
        above = [
            (item["url"], docs.get(item["url"]))
            for item in top
            if not item.get("you") and not ours(item["url"]) and (mine is None or (item.get("position") or 99) < mine)
        ]
        url = row.get("url")
        out.append(gap_for(row["q"], url, docs.get(url) if url else None, [(link, html) for link, html in above if html], own_domain=own_domain, cache=cache))
    return out


# -- store readers ---------------------------------------------------------------------------------------


def stored_html(database: str, urls: list[str]) -> dict[str, str]:
    """Raw HTML of fetched pages, keyed by the URLs asked for."""
    from aeo.ranksim.project import loose_key
    from aeo.ranksim.store import RsPage, open_session

    urls = [url for url in urls if url]
    if not urls:
        return {}
    session = open_session(database)
    try:
        wanted = set(urls) | {url.rstrip("/") for url in urls} | {url.rstrip("/") + "/" for url in urls}
        found = {}
        for row in session.query(RsPage).filter(RsPage.url.in_(sorted(wanted))).all():
            if row.status == "fetched" and (row.raw_html or "").strip():
                found[loose_key(row.url)] = row.raw_html
    finally:
        session.close()
    return {url: found[loose_key(url)] for url in urls if loose_key(url) in found}


def latest_google_json(project) -> dict | None:
    """The newest pull's raw ``google.json``, which may carry related searches and people also ask."""
    import json

    from aeo.ranksim.project import list_pulls

    for info in reversed(list_pulls(project)):
        path = project.dir / "pulls" / info.pull_id / "google.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def report_sections(project, meta, database: str, rankings: list[dict], *, plugin_rows: list[dict] | None = None, pages=None) -> dict:
    """``discovery``, ``competitor_profiles`` and ``gaps`` for ``build_report``. Never raises on missing data."""
    from aeo.ranksim import discovery_next
    from aeo.ranksim.project import latest_google

    google = latest_google(project)
    urls = sorted({item["url"] for row in rankings for item in row.get("top") or []} | {row["url"] for row in rankings if row.get("url")})
    docs = stored_html(database, urls)
    sections: dict = {}
    if pages is not None:
        from aeo.ranksim.autopilot import brand_of

        brand = brand_of(pages, project.domain)
        found = discovery_next.discover(
            pages,
            brand,
            host=project.domain,
            google=google,
            extras=discovery_next.serp_extras(latest_google_json(project)),
            plugin_rows=plugin_rows,
            is_ours=project.is_our_url,
        )
        sections["discovery"] = found.to_dict()
        tracked = set(project.searches)
        for item in sections["discovery"]["searches"]:
            item["tracked"] = item["q"] in tracked
    cache: dict = {}
    sections["competitor_profiles"] = competitor_profiles(google, project.domain, docs, is_ours=project.is_our_url, cache=cache)
    sections["gaps"] = content_gaps(rankings, docs, is_ours=project.is_our_url, own_domain=project.domain, cache=cache)
    return sections
