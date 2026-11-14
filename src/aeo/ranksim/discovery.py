"""Search discovery: the searches a site competes on, found automatically and labeled with where each came from.

Sources, each scored and labeled:

- the site's own title parts, H1s, H2s and URL slugs;
- related searches and people also ask stored with a Google pull (``searches[i].features`` in ``google.json``);
- titles of other sites' pages that rank for the site's searches;
- plugin ``discovery_sources`` rows, for example Search Console queries.

Every search gets an intent from transparent word rules, a relevance to what the site sells, and a value from its
intent. Brand searches are left out (people searching your name already find you), near duplicates are merged and the
list is capped at ``MAX_SEARCHES`` because each search costs one Google credit per pull.

The scale (shared with plugins, see ``aeo.ranksim.plugins``): one title or H1 phrase scores 3.0, a slug 2.0, an H2
1.0, a related search 2.0, a people also ask question 1.25, another site's title 1.0 per page. Phrases the site or
Google wrote get +0.5 for 2 to 5 words and +1.0 for a buying intent. A plugin row is a real search: it counts its
own weight (3.0 when it has none) and no heading rule or bonus applies to it. A search needs ``MIN_SCORE`` (2.5) in
total. The order is that total, times relevance, times the value of its intent.

Google Autocomplete is deliberately not used: its terms do not clearly permit automated use.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from aeo.crawl.urls import domain_of

from aeo.ranksim.site import SitePage

MAX_SEARCHES = 20  # one Google credit per search per pull, so this stays a constant
MAX_DROPPED = 60
MIN_SCORE = 2.5  # one title or H1 phrase passes; an H2 alone passes only when it reads like a buying search
SOURCE_WEIGHT = 3.0  # a plugin row with no weight counts as much as a page title phrase
MAX_SOURCE_PHRASE = 100
LENGTH_BONUS = 0.5  # 2 to 5 words reads like a search
BUYING_BONUS = 1.0  # commercial or transactional intent

# Base weight of one sighting from each source.
WEIGHTS = {
    "title": 3.0,
    "h1": 3.0,
    "slug": 2.0,
    "h2": 1.0,
    "related": 2.0,
    "paa": 1.25,
    "competitor": 1.0,
}
SOURCE_CAP = 6.0  # one page source can't outvote everything else by repeating itself (plugin rows are not capped)
MIN_RELEVANCE_OWN = 0.34  # a phrase from one page of the site must share a word with its home and product pages
MIN_RELEVANCE_OUTSIDE = 0.5  # searches from Google or other sites must share words with what the site sells
COMPETITOR_MIN_PAGES = 2  # a phrase from other sites' titles needs two pages

VALUE = {"transactional": 1.0, "commercial": 0.9, "informational": 0.6, "navigational": 0.25}

GENERIC = {
    "home", "homepage", "about", "about us", "contact", "contact us", "blog", "pricing", "login", "log in", "sign in",
    "sign up", "privacy policy", "privacy", "terms", "terms of service", "terms and conditions", "careers", "faq",
    "faqs", "news", "resources", "support", "help", "menu", "search", "page not found", "cookie policy", "sitemap",
    "thank you", "our team", "get started", "learn more", "read more", "request a demo", "book a demo", "features",
    "testimonials", "case studies", "partners", "press", "events", "jobs", "team", "products", "services", "solutions",
    "home service", "home services", "all posts", "all articles", "privacy notice", "legal", "accessibility", "cookie settings", "our story", "about the company",
}
LABEL_STARTS = (
    "questions", "frequently asked", "common questions", "articles", "guides", "resources", "related", "latest", "more ",
    "why choose", "how it works", "what our", "what people", "get in touch", "ready to", "trusted by", "join ",
    "see how", "learn ", "explore", "read ", "browse ", "featured", "popular", "recent", "head to head",
    "built for", "made for", "everything you", "start ", "try ", "talk to", "contact ", "our ", "meet ", "about ",
    "see ", "watch ", "download ", "request ", "schedule a", "plans", "table of contents",
    "in this article", "key takeaways", "conclusion", "final thoughts", "summary", "next steps", "share this",
    "subscribe", "follow us", "stay ", "thanks", "hear from", "loved by", "customer stories",
)
LABEL_ENDS = (" us", " here", " today", " now", " below", " more", " resources", " updates", " news", " insights", " stories", " events", " webinars")
# Labels that stay labels even when they end on a product word ("guides for running a dirtwork company").
STRONG_LABEL_STARTS = (
    "questions", "frequently asked", "common questions", "articles", "guides", "resources", "related", "latest",
    "why choose", "how it works", "what our", "what people", "get in touch", "ready to", "trusted by", "join ",
    "featured", "popular", "recent", "head to head", "built for", "made for", "everything you", "talk to", "our ",
    "meet ", "about ", "plans", "table of contents", "in this article", "key takeaways", "conclusion",
    "final thoughts", "summary", "next steps", "share this", "subscribe", "follow us", "stay ", "thanks", "hear from",
    "loved by", "customer stories", "product updates", "top picks", "use of ", "no charge", "refer ", "what it ",
    "key differences", "differences between", "pros and cons", "what real ", "what users", "what customers",
    "honest review", "real reviews",
    "what you ", "how we ", "who we ", "what we ",
)
# Words that only appear in legal pages and documents.
LEGAL = re.compile(r"\b(terms|conditions|privacy|policy|policies|agreement|agreements|clauses?|gdpr|cookies?|adequacy|sub ?processors?|disclaimer|copyright|eula|dpa|liability|indemn\w*|warranty|warranties|governing law|supplemental terms|acceptable use)\b")
# Press, release and event phrases: they name a moment, not something people search for.
NEWS = re.compile(r"\b(q[1-4]|release|released|releases|announces?|announcing|announcement|award|awards|nominees?|top workplaces?|workplace award|webinar|summit|conference|boot ?camp|podcast|episode|partnership|partners with|acquires|acquisition|named|wins|celebrat\w*|anniversary|usa today|press)\b")
MARKETING = {
    "trusted", "powering", "powered", "deliver", "delivers", "delivering", "unified", "unify", "ultimate", "innovative",
    "revolutionary", "seamless", "seamlessly", "greatness", "aligns", "empowering", "reimagined", "smarter", "always",
    "never", "everything", "industry first", "end to end", "game changer", "world class", "best in class", "next generation",
}
# Common words of other languages: a phrase with one of these is not an English search.
FOREIGN = {
    "und", "der", "die", "das", "fur", "mit", "y", "el", "los", "las", "del", "para", "con", "et", "les", "des", "du",
    "pour", "het", "een", "voor", "och", "dos", "em", "ou", "terminos", "privacidad", "datenschutz", "agb",
    "rechtliches", "juridico", "mentions", "legales", "uber", "nous", "sobre", "nosotros", "contacto", "preise",
}
LEADING = ("the ", "a ", "an ", "our ", "your ", "welcome to ", "meet ", "introducing ")
# Words that make a sentence a tagline, not a search.
PRONOUNS = {"you", "your", "youre", "we", "were", "our", "us", "i", "my", "me", "yours", "ours"}
SITE_VOICE = {"we", "our", "us", "ours", "were", "it", "its", "you", "your"}  # a question in the site's own voice ("what we do") is site copy
TAGLINE_VERBS = {
    "run", "get", "build", "grow", "stop", "save", "make", "start", "take", "keep", "see", "meet", "discover", "join",
    "simplify", "streamline", "power", "transform", "unlock", "boost", "spend", "win", "never", "say", "work", "do",
    "let", "bring", "turn", "put", "focus", "finish", "empower", "elevate", "ditch", "switch",
}
# Words a product or service search ends on. A long title is cut around them.
HEADS = {
    "software", "app", "apps", "tool", "tools", "platform", "platforms", "system", "systems", "solution", "solutions",
    "program", "programs", "crm", "erp", "service", "services", "company", "companies", "contractor", "contractors",
    "agency", "agencies", "consultant", "consultants", "provider", "providers", "alternative", "alternatives",
    "template", "templates", "calculator", "tracker", "management", "estimating", "scheduling", "dispatch",
    "takeoff", "takeoffs",
} | {
    "plumber", "plumbers", "plumbing", "lawyer", "lawyers", "attorney", "attorneys", "dentist", "dentists",
    "electrician", "electricians", "roofer", "roofers", "roofing", "builder", "builders", "cleaner", "cleaners",
    "cleaning", "repair", "repairs", "installation", "installer", "installers", "mechanic", "doctor", "clinic",
    "therapist", "accountant", "accountants", "realtor", "realtors", "insurance", "firm", "firms", "studio",
    "shop", "store", "supplier", "suppliers", "manufacturer", "manufacturers", "rental", "rentals", "course",
    "courses", "school", "lessons", "kit", "kits", "products",
}
CONNECTORS = re.compile(r",|;|\s(?:for|with|that|to|and|or|by|from|which|so|built|designed|made|without|will|actually|in a|is|are)\s")
# A phrase with one of these reads like a sentence, so it is cut into its product phrases even when it is short.
SENTENCE = re.compile(r"\s(?:that|which|so|built|designed|made|without|will|actually|in a|is|are|by)\s")
STOP = {
    "a", "an", "the", "and", "or", "for", "with", "to", "of", "in", "on", "at", "by", "from", "is", "are", "be", "it",
    "its", "this", "that", "as", "your", "you", "our", "we", "us", "how", "what", "why", "when", "who", "which",
    "do", "does", "can", "vs", "versus", "best", "top", "new", "free", "online", "near", "me", "i", "my", "all", "one",
}
MODIFIERS = {"best", "top", "good", "great", "free", "online", "cheap", "the", "a", "an", "latest"}
SPLIT = re.compile(r"\s*[|•·»]\s*|\s+[-–—:/]\s+|:\s+")
DIRECTION = {"to", "from", "vs", "versus", "into", "for"}  # word order matters: "pdf to word" is not "word to pdf"
YEAR = re.compile(r"\b(19|20)\d\d\b")

# Intent rules, checked in order. Each rule is (intent, words); a phrase matches when it contains one of the words.
NAVIGATIONAL = ("login", "log in", "sign in", "signin", "my account", "account login", "customer service number",
                "phone number", "customer support", "contact number", "headquarters", "careers", "jobs at")
TRANSACTIONAL = ("buy", "price", "prices", "pricing", "cost", "costs", "quote", "quotes", "demo", "free trial",
                 "near me", "hire", "order online", "order now", "coupon", "discount", "for sale", "download", "subscription",
                 "cheap", "affordable", "rates", "book online", "sign up for")
# Strong question words always start a question; weak ones ("will writing service") only with "?" or a subject next.
QUESTION_START = ("how", "what", "why", "when", "where", "who", "which")
WEAK_QUESTION_START = ("is", "are", "can", "does", "do", "should", "will", "could", "would")
QUESTION_SUBJECT = {"i", "you", "we", "it", "a", "an", "the", "my", "your", "they", "there", "this", "that"}
COMMERCIAL_WORDS = ("best", "top", "vs", "versus", "alternative", "alternatives", "compare", "comparison", "review",
                    "reviews", "software", "app", "apps", "tool", "tools", "platform", "system", "systems", "company",
                    "companies", "contractor", "contractors", "service", "services", "solution", "solutions",
                    "program", "provider", "providers", "crm", "erp", "agency", "agencies", "consultant") + tuple(
    sorted({"plumber", "plumbers", "lawyer", "lawyers", "attorney", "attorneys", "dentist", "dentists", "electrician",
            "electricians", "roofer", "roofers", "builder", "builders", "cleaner", "cleaners", "repair", "repairs",
            "installation", "installer", "mechanic", "clinic", "therapist", "accountant", "realtor", "insurance",
            "firm", "shop", "store", "supplier", "rental", "rentals", "course", "courses"}))
INFORMATIONAL = ("guide", "tips", "ideas", "examples", "example", "meaning", "definition", "tutorial", "checklist",
                 "ways", "steps", "template", "templates", "history", "types", "explained", "learn", "process")
BRAND_AFFIXES = ("hq", "app", "inc", "co", "get", "try", "use", "the", "my", "go", "io")


# -- text helpers -----------------------------------------------------------------------------------


def clean(text: str) -> str:
    """Lowercase words only: "All-in-1 Dirt Work Software!" gives "all in one dirt work software"."""
    value = unicodedata.normalize("NFKD", (text or "").lower())
    value = "".join(char for char in value if not unicodedata.combining(char))  # "münchen" gives "munchen"
    value = value.replace("&amp;", "&").replace("’", "").replace("'", "").replace("&", " and ")
    value = re.sub(r"[^\w ]+|_", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\ball in 1\b", "all in one", value)
    changed = True
    while changed:  # "meet the team" loses "meet " and then "the "
        changed = False
        for lead in LEADING:
            if value.startswith(lead) and len(value) > len(lead):
                value = value[len(lead):].strip()
                changed = True
    return value


def stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def stems(text: str) -> list[str]:
    return [stem(word) for word in clean(text).split()]


def content_stems(text: str) -> set[str]:
    return {item for item in stems(text) if item not in STOP and len(item) > 1}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def same_search(a: str, b: str) -> bool:
    """Near duplicates: plural s, word order, "dirtwork" vs "dirt work", and "best" or "top" in front."""
    sa, sb = stems(a), stems(b)
    if "".join(sa) == "".join(sb):
        return True
    if sorted(sa) == sorted(sb) and not (set(sa) & DIRECTION):
        return True
    core_a = [word for word in sa if word not in MODIFIERS and not YEAR.fullmatch(word)]
    core_b = [word for word in sb if word not in MODIFIERS and not YEAR.fullmatch(word)]
    if core_a and "".join(core_a) == "".join(core_b):
        return True
    shorter, longer = sorted((core_a, core_b), key=len)
    if shorter and len(longer) > len(shorter) + 1 and longer[len(shorter)] == "for" and "".join(longer[: len(shorter)]) == "".join(shorter):
        return True  # "grading contractor software for machine hours" is "grading contractor software"
    set_a, set_b = set(sa), set(sb)
    if set_a & DIRECTION and set_a == set_b:
        return False  # same words, different direction
    return min(len(set_a), len(set_b)) >= 4 and _jaccard(set_a, set_b) >= 0.8


# -- which pages say what the site sells --------------------------------------------------------------------

LANGUAGES = {
    "de", "es", "fr", "nl", "pt", "it", "ja", "ko", "zh", "ru", "pl", "sv", "da", "no", "nb", "fi", "tr", "ar", "he",
    "cs", "hu", "ro", "el", "id", "th", "vi", "uk", "bg", "hr", "sk", "sl", "lt", "lv", "et", "ms", "hi",
}
# Paths that never say what a site sells: legal, utility, hiring, press, events, staging.
SKIP_SEGMENT = re.compile(
    r"^(?:legal|privacy(?:-policy)?|terms(?:-[a-z-]+)?|tos|eula|cookies?(?:-policy)?|gdpr|dpa|imprint|impressum|"
    r"accessibility|security|trust|careers?|jobs|press(?:-releases?)?|news(?:room)?|media|schedule|demo|demos|"
    r"book-a-demo|request-a-demo|referrals?|refer|signup|sign-up|register|registration|login|log-in|signin|sign-in|"
    r"account|my-account|cart|checkout|thank-you|thanks|unsubscribe|dev|dev-[a-z0-9-]+|staging|test|tests|tag|tags|"
    r"category|categories|author|authors|feed|search|sitemap|summit|events?|webinars?|podcasts?|partners?|"
    r"partner-program|affiliates?|investors?|status|rechtliches|juridico|stripeit|wp-[a-z-]+|cdn-cgi|amp|print)$",
    re.I,
)
CORE_SEGMENT = re.compile(
    r"^(?:products?|features?|solutions?|industr(?:y|ies)|compare|comparisons?|vs|versus|alternatives?|platform|"
    r"software|who-we-serve|use-cases?|integrations?|pricing|why-[a-z0-9-]+|services?|apps?|tools?)$",
    re.I,
)
CORE_SLUG = re.compile(r"(?:^|-)(?:software|app|platform|alternatives?|vs|versus|compare|crm|tool|tools|system|pricing)(?:-|$)", re.I)
BLOG_SEGMENT = re.compile(r"^(?:blog|blogs|resources?|guides?|learn|articles?|insights|library|academy|knowledge|help|support|docs|faq|case-studies|customers?|stories)$", re.I)


def page_class(url: str) -> str:
    """"home", "core" (product, feature, solution, industry, compare pages), "page", "blog" or "skip" (legal,
    utility, press, events, staging and other-language pages, which never say what the site sells)."""
    try:
        path = unquote(urlsplit(url).path or "/")
    except ValueError:
        return "skip"
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if not segments:
        return "home"
    first = segments[0].lower()
    lang = re.fullmatch(r"([a-z]{2})(?:[-_][a-z]{2,4})?", first)
    if lang and lang.group(1) in LANGUAGES:
        return "skip"
    if any(SKIP_SEGMENT.match(segment) for segment in segments):
        return "skip"
    if re.search(r"(?:^|-)(?:terms|privacy|cookie|legal|press-release|careers?)(?:-|$)", segments[-1].lower()):
        return "skip"
    if any(BLOG_SEGMENT.match(segment) for segment in segments[:-1]) or BLOG_SEGMENT.match(first):
        return "blog"
    if len(segments) > 1 and re.match(r"(?:what|how|does|do|will|can|where|when|is|are|should)-", segments[-1].lower()):
        return "blog"  # an FAQ answer ("/heavyjob/what-is-heavyjob/") explains a product, it doesn't target a search
    if any(CORE_SEGMENT.match(segment) for segment in segments) or CORE_SLUG.search(segments[-1]):
        return "core"
    return "page"


def _core_rank(url: str) -> int:
    """Among product pages: compare and alternative pages and "<x> software" pages first, then features and
    solutions, then industries, then hub pages that only list the others (/compare/, /industries/)."""
    segments = [segment.lower() for segment in urlsplit(url).path.strip("/").split("/") if segment]
    last = segments[-1] if segments else ""
    if len(segments) == 1 and CORE_SEGMENT.match(last):
        return 3
    if re.search(r"(?:^|-)(?:alternatives?|vs|versus|compare)(?:-|$)", last) or segments[0] in {"compare", "vs", "versus", "alternatives"}:
        return 0
    if re.search(r"(?:^|-)(?:software|app|platform|crm|tool|tools|system)(?:-|$)", last):
        return 0
    if segments[0] in {"industries", "industry", "who-we-serve", "use-cases", "use-case"}:
        return 2
    return 1


def rank_sitemaps(locs: list[str]) -> list[str]:
    """Child sitemaps of a sitemap index, most useful first. Legal, author, tag, press, news, media and event maps are
    left out; product, page and solution maps come before the blog."""
    def rank(url: str) -> int | None:
        name = (urlsplit(url).path or "").lower().rsplit("/", 1)[-1]
        if re.search(r"legal|author|tag|categor|press|news|media|release|event|webinar|image|video|ebook|white-paper|whitepaper|podcast|career|job", name):
            return None
        if re.search(r"product|solution|feature|industr|compare|alternative|service|app|homepage|home|page|main|core|landing", name):
            return 0 if not re.search(r"homepage", name) else 1
        if re.search(r"post|blog|article|guide|resource|case|topic|checklist|training|library|customer", name):
            return 3
        return 2

    ranked = [(rank(url), index, url) for index, url in enumerate(locs)]
    return [url for value, _index, url in sorted(item for item in ranked if item[0] is not None)]


def _unique_urls(urls: list[str]) -> list[str]:
    seen, out = set(), []
    for url in urls:
        key = url.rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out


def rank_site_urls(urls: list[str], limit: int) -> list[str]:
    """The site pages worth reading, best first, at most ``limit``: product, compare and industry pages, then other
    pages, then a share of the blog. Legal, utility, press, staging and other-language pages are left out."""
    order = {"home": 0, "core": 1, "page": 2, "blog": 3}
    kept = [url for url in _unique_urls(urls) if page_class(url) != "skip"]
    ranked = sorted(kept, key=lambda url: (order[page_class(url)], _core_rank(url) if page_class(url) == "core" else 0, urlsplit(url).path.strip("/").count("/"), len(url)))
    blog_cap = max(3, limit // 3)
    picked, blog = [], 0
    for url in ranked:
        if page_class(url) == "blog":
            if blog >= blog_cap:
                continue
            blog += 1
        picked.append(url)
        if len(picked) >= limit:
            return picked
    for url in ranked:  # room left: more blog posts rather than nothing
        if len(picked) >= limit:
            break
        if url not in picked:
            picked.append(url)
    return picked[: max(0, limit)]


# -- brand ------------------------------------------------------------------------------------------


def host_tokens(host: str) -> set[str]:
    """The domain name as search words: smartcrewhq.com gives {"smartcrewhq", "smartcrew"}."""
    label = base_domain(host).split(".")[0]
    if not label:
        return set()
    tokens = {label, label.replace("-", "")}
    for affix in BRAND_AFFIXES:  # only when a real name is left: "smartcrewhq" gives "smartcrew", "roofco" keeps "roofco"
        if label.endswith(affix) and len(label) - len(affix) >= 5:
            tokens.add(label[: -len(affix)])
        if label.startswith(affix) and len(label) - len(affix) >= 5:
            tokens.add(label[len(affix):])
    tokens.update(part for part in label.split("-") if len(part) > 3)
    return {token for token in tokens if len(token) >= 2}


SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or", "go", "gob", "nic", "ltd", "plc", "sch"}


def base_domain(domain: str) -> str:
    """The registrable domain: help.hcss.com gives hcss.com, blog.mitti.co.uk gives mitti.co.uk."""
    parts = (domain or "").lower().strip(".").removeprefix("www.").split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else (domain or "").lower()


@dataclass(frozen=True)
class Brand:
    """How a brand appears in a search. A two word brand blocks the pair, never each word alone."""

    phrases: tuple[str, ...]  # "dirt crew"
    words: tuple[str, ...]  # "dirtcrew", "dirtcrewhq"

    @classmethod
    def of(cls, brand: str, host: str = "") -> "Brand":
        phrase = clean(re.sub(r"\.(com|net|org|io|co|ai|app|dev|us|uk)\b", "", brand or "", flags=re.I))
        phrases = {phrase} if phrase and " " in phrase else set()
        if " and " in f" {phrase} ":
            phrases.add(phrase.replace(" and ", " "))
        words = set(host_tokens(host)) if host else set()
        if phrase:
            words.add(phrase.replace(" ", ""))
        return cls(tuple(sorted(phrases)), tuple(sorted(word for word in words if len(word) >= 2)))

    def in_phrase(self, phrase: str) -> bool:
        text = f" {clean(phrase)} "
        if any(f" {item} " in text for item in self.phrases):
            return True
        words = text.split()
        runs = {"".join(words[i:j]) for i in range(len(words)) for j in range(i + 2, min(len(words), i + 4) + 1)}
        for word in self.words:
            if f" {word} " in text:
                return True
            if len(word) >= 5 and word in runs:  # "dirt crew" written "dirtcrew", whole words only
                return True
        squashed_phrases = {item.replace(" ", "") for item in self.phrases}
        return bool(squashed_phrases & set(words))


# -- intent -----------------------------------------------------------------------------------------


def _has(phrase: str, words) -> str | None:
    text = f" {phrase} "
    for word in words:
        if f" {word} " in text:
            return word
    return None


def classify_intent(phrase: str, *, brands: set[str] | None = None) -> tuple[str, str]:
    """Intent and the rule that decided it. First match wins."""
    text = clean(phrase)
    if not text:
        return "informational", "empty"
    product = text.split()[-1] in HEADS  # "customer service software" is a product, not a way to reach a company
    hit = None if product else _has(text, NAVIGATIONAL)
    if hit:
        return "navigational", f'has "{hit}"'
    hit = _has(text, TRANSACTIONAL)
    if hit:
        return "transactional", f'has "{hit}"'
    if _question(text) or phrase.strip().endswith("?"):
        return "informational", "a question"
    hit = _has(text, COMMERCIAL_WORDS)
    if hit in {"vs", "versus"}:
        sides = re.split(r" (?:vs|versus) ", text)
        concepts = all(side.split() and side.split()[-1].endswith(("ing", "tion", "ment", "ity", "ness", "ance", "ence")) and not set(side.split()) & HEADS for side in sides)
        if concepts:
            return "informational", "compares two ideas"  # "overbilling vs underbilling"
    if hit:
        return "commercial", f'has "{hit}"'
    hit = _has(text, INFORMATIONAL)
    if hit:
        return "informational", f'has "{hit}"'
    words = text.split()
    for brand in brands or set():
        if brand and (brand in words or brand == text.replace(" ", "")):
            return "navigational", f'names "{brand}"'
    return "informational", "no buying words"


# -- candidates --------------------------------------------------------------------------------------


@dataclass
class Sighting:
    source: str
    weight: float
    detail: str = ""
    page: str | None = None


@dataclass
class Found:
    q: str
    sightings: list[Sighting] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    intent: str = "informational"
    intent_rule: str = ""
    relevance: float = 0.0
    evidence: float = 0.0
    score: float = 0.0

    @property
    def sources(self) -> list[str]:
        return sorted({item.source for item in self.sightings}, key=lambda name: -self.by_source().get(name, 0))

    def by_source(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for item in self.sightings:
            totals[item.source] += item.weight
        return {name: min(SOURCE_CAP, value) if name in WEIGHTS else value for name, value in totals.items()}

    @property
    def page(self) -> str | None:
        best: dict[str, float] = defaultdict(float)
        for item in self.sightings:
            if item.page:
                best[item.page] += item.weight
        return max(best.items(), key=lambda kv: kv[1])[0] if best else None

    @property
    def own(self) -> bool:
        return any(item.source in OWN_SOURCES for item in self.sightings)

    def to_dict(self) -> dict:
        details: dict[str, list[str]] = defaultdict(list)
        for item in self.sightings:
            if item.detail and item.detail not in details[item.source] and len(details[item.source]) < 3:
                details[item.source].append(item.detail)
        return {
            "q": self.q,
            "score": round(self.score, 2),
            "intent": self.intent,
            "intent_rule": self.intent_rule,
            "relevance": round(self.relevance, 2),
            "source": self.sources[0] if self.sources else "",
            "sources": [
                {"source": name, "weight": round(weight, 2), "detail": details.get(name, [])}
                for name, weight in sorted(self.by_source().items(), key=lambda kv: -kv[1])
            ],
            "variants": self.variants,
            "page": self.page,
        }


OWN_SOURCES = {"title", "h1", "h2", "slug"}


@dataclass
class Discovery:
    searches: list[Found]
    dropped: list[dict]
    limit: int
    brand: str

    @property
    def queries(self) -> list[str]:
        return [item.q for item in self.searches]

    def to_dict(self) -> dict:
        counts: Counter = Counter()
        for item in self.searches:
            counts.update(set(item.sources))
        return {
            "limit": self.limit,
            "brand": self.brand,
            "searches": [item.to_dict() for item in self.searches],
            "dropped": self.dropped[:MAX_DROPPED],
            "source_counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        }


def _label_reason(phrase: str) -> str | None:
    if phrase in GENERIC:
        return "a page label"
    if phrase.startswith(STRONG_LABEL_STARTS) or phrase.endswith(LABEL_ENDS):
        return "a section label"
    if phrase.split()[-1:] and phrase.split()[-1] in HEADS:
        return None  # "contact center software" names a product
    if phrase.startswith(LABEL_STARTS) or phrase.endswith(LABEL_ENDS):
        return "a section label"
    return None


# Words that say what kind of page or product it is, not what it is about. A phrase made only of these is too general.
GENERAL = {
    "best", "top", "good", "great", "free", "online", "new", "pricing", "price", "prices", "features", "feature",
    "alternative", "alternatives", "review", "reviews", "comparison", "compare", "vs", "versus", "and", "or", "for",
    "the", "a", "an", "guide", "overview", "options", "software", "app", "apps", "tool", "tools", "platform",
    "solution", "solutions", "system", "systems", "program", "service", "services", "company", "companies", "online",
    "whats", "what", "which", "is", "are", "you", "need", "right",
}
# Size words: "midsized contractors" names an audience, not a search.
AUDIENCE_SIZE = {"small", "smaller", "midsized", "mid", "medium", "large", "larger", "growing", "local", "busy", "independent"}


def _general(phrase: str) -> bool:
    words = phrase.split()
    return bool(words) and all(word in GENERAL or YEAR.fullmatch(word) for word in words)


def _noise_reason(phrase: str, source: str) -> str | None:
    """Phrases that are never searches people type: legal wording, press and release names, other languages,
    marketing lines and lists of features."""
    words = phrase.split()
    if not words:
        return "empty"
    if LEGAL.search(phrase) and words[-1] not in HEADS:  # "privacy policy" is legal, "liability insurance" is a product
        return "legal wording"
    if NEWS.search(phrase):
        return "news or an event"
    if set(words) & FOREIGN:
        return "not English"
    padded = f" {phrase} "
    if any(f" {item} " in padded for item in MARKETING):
        return "a tagline"
    if source in OWN_SOURCES and " and " in padded and words[-1] not in HEADS:
        return "a list of features"
    if source in OWN_SOURCES and " or " in padded:
        return "a choice, not a search"
    if " in one " in padded and " all in one " not in padded:
        return "a tagline"
    if len(words) == 2 and words[0] in AUDIENCE_SIZE:
        return "an audience"
    return None


def _question(phrase: str) -> bool:
    words = phrase.split()
    if not words:
        return False
    if words[0] in QUESTION_START:
        return True
    return words[0] in WEAK_QUESTION_START and len(words) > 1 and words[1] in QUESTION_SUBJECT


def _tagline_reason(phrase: str) -> str | None:
    words = re.sub(r"\bnear me\b", "", phrase).split()
    if _question(phrase):
        return "a tagline" if any(word in SITE_VOICE for word in words) else None
    if any(word in PRONOUNS and not (word == "us" and index == 0) for index, word in enumerate(words)):
        return "a tagline"
    if words and words[0] in TAGLINE_VERBS and words[-1] not in HEADS:
        return "a tagline"
    return None


def _cut(text: str, max_words: int) -> list[tuple[str, float]]:
    """A long title part is cut into the product phrases inside it, with a weight factor for each. Commas and
    connector words split; a piece is kept only when it ends on a product or service word. The audience after "for"
    is never a search alone, but "<product> for <audience>" is when it stays short."""
    phrase = clean(text)
    if len(phrase.split()) <= max_words and (not SENTENCE.search(f" {phrase} ") or _question(phrase)):
        return [(phrase, 1.0)]
    out: list[tuple[str, float]] = []
    raw = re.sub(r"\ball[\s-]in[\s-](?:one|1)\b", "all in one", (text or "").lower())
    parts = CONNECTORS.split(f" {raw} ")
    joins = [match.group(0).strip() for match in CONNECTORS.finditer(f" {raw} ")]
    previous = ""
    audience = False
    for index, chunk in enumerate(parts):
        chunk_words = clean(chunk).split()
        joiner = joins[index - 1] if index else ""
        if audience and joiner in {"and", "or", ","}:
            continue  # "for paving and excavation contractors": still the audience, never a search alone
        audience = joiner == "for"
        if joiner == "for":
            if not (previous and 1 <= len(chunk_words) <= 3 and chunk_words[-1].endswith("s")):
                previous = ""
                continue
            product = previous.split()
            if len(product) + 1 + len(chunk_words) > max_words and " ".join(product[:3]) == "all in one":
                product = product[3:]  # "all in one" is a modifier; the product is what follows
            joined = " ".join(product + ["for"] + chunk_words)
            if len(joined.split()) <= max_words:
                out.append((joined, 0.6))
        elif 2 <= len(chunk_words) <= max_words and chunk_words[-1] in HEADS:
            out.append((" ".join(chunk_words), 0.8))
        previous = " ".join(chunk_words) if chunk_words and chunk_words[-1] in HEADS else ""
    return out


def _gather(text: str, source: str, weight: float, page: str | None, *, max_words: int = 6, detail: str = "") -> list[tuple[str, Sighting] | tuple[None, dict]]:
    """Phrases from one piece of text, or the reason it was left out."""
    if re.search(r"(?:\.\.\.|…)\s*$", text or ""):
        return [(None, {"q": clean(text), "reason": "cut off", "source": source})]  # "Embedded Software Supplemental…"
    phrase = clean(text)
    if not phrase:
        return []
    if _label_reason(phrase):
        return [(None, {"q": phrase, "reason": _label_reason(phrase), "source": source})]
    if len(phrase.split()) < 2:
        return []
    out: list = []
    for piece, factor in _cut(text, max_words):
        piece = " ".join(word for word in piece.split() if not YEAR.fullmatch(word))  # searchers rarely type the year
        piece = re.sub(r"\s+(?:in|for|of|from|on|to)$", "", piece)  # "buildertrend pricing in" after the year went
        piece = re.sub(r"^\d{1,2}\s+(?=(?:best|top)\b)", "", piece)  # "7 best joist alternatives" is "best joist alternatives"
        piece = re.sub(r"^(.+\b(?:alternatives?|vs \S+(?: \S+)?))\s+for\s+.+$", r"\1", piece)  # "projul alternative for dirtwork crews"
        reason = _label_reason(piece) or _tagline_reason(piece) or ("too general" if _general(piece) else None) or _noise_reason(piece, source)
        words = piece.split()
        if not reason and not 2 <= len(words) <= max_words:
            reason = "too long" if len(words) > max_words else "one word"
        if not reason and sum(1 for word in words if word.isdigit()) and not YEAR.search(piece) and not re.search(r"\btop \d+\b|\b\d+ best\b|\b24 (?:7|hour|hours)\b|\b\d+ (?:hour|day|minute|week)\b", piece):
            reason = "a number or price"
        if reason:
            out.append((None, {"q": piece, "reason": reason, "source": source}))
            continue
        out.append((piece, Sighting(source=source, weight=weight * factor, detail=detail or text.strip()[:80], page=page)))
    return out


def slug_phrase(url: str) -> str | None:
    """The words of a URL's last path part: /dirt-work-software/ gives "dirt work software"."""
    path = unquote(urlsplit(url).path or "").strip("/")
    if not path:
        return None
    last = path.split("/")[-1]
    last = re.sub(r"\.(html?|php|aspx?)$", "", last)
    if re.fullmatch(r"[\d\-_]+|p=?\d+|page\d*", last):
        return None
    words = [word for word in re.split(r"[-_+]+", last.lower()) if word]
    if len(words) < 2 or any(len(word) > 20 for word in words) or all(len(word) <= 2 for word in words):
        return None  # "en-gb" is a locale, not a search
    if any(re.search(r"[a-z]", word) and re.search(r"\d", word) for word in words):
        return None  # ids and hashes: "3f2b9c1e-ab12"
    if sum(1 for word in words if word.isdigit()) and not YEAR.search(" ".join(words)):
        return None
    return " ".join(words)


def _covers(want: list[str], text: str) -> float:
    """Share of the search's words in a text, counting compounds ("dirtwork" in "dirt work" and the reverse)."""
    have = set(stems(text))
    squashed = "".join(stems(text))
    if not want:
        return 0.0
    hit = sum(1 for word in want if word in have or (len(word) >= 4 and word in squashed))
    return hit / len(want)


def best_page(search: str, pages) -> str | None:
    """The site page that best targets a search: its title and H1 cover the search's words, its address says the
    same thing, and nothing else. Legal and utility pages never count."""
    want = [word for word in stems(search) if word not in STOP or word in {"vs", "best", "top"}]
    want = [word for word in want if word not in {"best", "top", "vs"}] or want
    if len(clean(search).split()) < 2 or not want:
        return None
    best, best_score = None, 0.0
    for page in pages or []:
        if getattr(page, "status", "fetched") != "fetched":
            continue
        url = _attr(page, "url")
        kind = page_class(url)
        if kind == "skip":
            continue
        heading = f"{_attr(page, 'title')} {_attr(page, 'h1')}"
        slug = slug_phrase(url) or ""
        cover = _covers(want, heading)
        slug_cover = _covers(want, slug)
        slug_words = [word for word in stems(slug) if word not in STOP]
        precision = sum(1 for word in slug_words if word in want) / len(slug_words) if slug_words else 0.0
        if cover < 0.6 and slug_cover < 0.8:
            continue
        score = cover + 0.6 * slug_cover + 0.3 * precision + (0.1 if kind in {"core", "home"} else 0.0)
        if score > best_score:
            best, best_score = url, score
    return best


def _attr(page, name: str) -> str:
    value = getattr(page, name, "")
    return value if isinstance(value, str) else ""


def _vocabulary(pages) -> Counter:
    """What the site sells, as word stems weighted by where they appear on its own pages."""
    vocab: Counter = Counter()
    for page in pages:
        if getattr(page, "status", "fetched") != "fetched":
            continue
        for text, weight in ((_attr(page, "title"), 3), (_attr(page, "h1"), 3), (_attr(page, "meta"), 2), (" ".join((getattr(page, "h2s", None) or [])[:12]), 1), (slug_phrase(_attr(page, "url")) or "", 2)):
            for item in content_stems(text or ""):
                vocab[item] += weight
    return vocab


def relevance(phrase: str, vocab: Counter) -> float:
    words = content_stems(phrase)
    if not words:
        return 0.0
    hits = 0.0
    for word in words:
        if vocab.get(word):
            hits += 1.0
        elif any(word.startswith(item) and len(item) >= 4 for item in vocab) or any(item.startswith(word) and len(word) >= 4 for item in vocab):
            hits += 0.75  # "dirtwork" vs "dirt", "excavating" vs "excavation"
    return hits / len(words)


def _safe_domain(item) -> str:
    try:
        return (getattr(item, "domain", "") or domain_of(item.url) or "").lower()
    except (ValueError, TypeError, AttributeError):
        return ""


def _serp_brands(google, vocab: Counter) -> set[str]:
    """Other sites' names. A name that is also a word the site uses ("roofing") is a topic, not a brand."""
    brands: set[str] = set()
    for entry in getattr(google, "searches", None) or []:
        for item in entry.results:
            brands |= host_tokens(_safe_domain(item))
    return {brand for brand in brands if stem(brand) not in vocab}


# -- the main function --------------------------------------------------------------------------------


def discover(
    pages,
    brand: str,
    *,
    host: str = "",
    google=None,
    serp_features: dict | None = None,
    extra: list[dict] | None = None,
    is_ours=None,
    limit: int = MAX_SEARCHES,
) -> Discovery:
    """Merge every source into one ranked, deduplicated, brand free list of searches.

    pages: the site's own SitePage list. google: the latest GoogleSnapshot or None. serp_features: per search
    ``{"related": [...], "paa": [...]}`` (see ``serp_extras``). extra: ``{"q", "weight", "source"}`` rows from the
    ``discovery_sources`` hook. is_ours(url) tells the site's own results apart (defaults to the host).
    """
    own_brand = Brand.of(brand, host)
    host_bare = (host or "").lower().removeprefix("www.")
    if is_ours is None:
        def is_ours(url: str) -> bool:  # noqa: E306 - small default
            try:
                found = (urlsplit(url).hostname or "").lower().removeprefix("www.")
            except ValueError:
                return False
            return bool(host_bare) and (found == host_bare or found.endswith("." + host_bare))

    raw: dict[str, Found] = {}
    dropped: list[dict] = []

    def add(items):
        for phrase, item in items:
            if phrase is None:
                dropped.append(item)
                continue
            if own_brand.in_phrase(phrase):
                dropped.append({"q": phrase, "reason": "your brand", "source": item.source})
                continue
            raw.setdefault(phrase, Found(q=phrase)).sightings.append(item)

    readable = [page for page in pages if getattr(page, "status", "fetched") == "fetched" and page_class(_attr(page, "url")) != "skip"]
    for page in readable:
        page = SitePage(url=_attr(page, "url"), status="fetched", title=_attr(page, "title"), meta=_attr(page, "meta"), h1=_attr(page, "h1"), h2s=[text for text in (getattr(page, "h2s", None) or []) if isinstance(text, str)])
        parts = [part for part in SPLIT.split(page.title or "") if part.strip()]
        for part in parts:
            add(_gather(part, "title", WEIGHTS["title"], page.url))
        if page.h1 and clean(page.h1) not in {clean(part) for part in parts}:
            add(_gather(page.h1, "h1", WEIGHTS["h1"], page.url))
        elif page.h1:
            for phrase, item in _gather(page.h1, "h1", 0.5, page.url):  # an H1 equal to the title confirms it a little
                if phrase:
                    add([(phrase, item)])
        for text in page.h2s[:8]:
            add(_gather(text, "h2", WEIGHTS["h2"], page.url, max_words=6))
        slug = slug_phrase(page.url)
        if slug:
            add(_gather(slug, "slug", WEIGHTS["slug"], page.url, detail=urlsplit(page.url).path))

    for search, found in (serp_features or {}).items() if isinstance(serp_features, dict) else []:
        if not isinstance(found, dict):
            continue
        for text in _texts(found.get("related")):
            add(_gather(text, "related", WEIGHTS["related"], None, detail=f'related to "{search}"'))
        for text in _texts(found.get("paa")):
            add(_gather(text, "paa", WEIGHTS["paa"], None, max_words=9, detail=f'asked on "{search}"'))

    if google is not None:
        seen_pages: dict[str, set] = defaultdict(set)
        sightings: dict[str, list[Sighting]] = defaultdict(list)
        for entry in google.searches:
            for result in entry.results:
                domain = _safe_domain(result)
                try:
                    ours = is_ours(result.url)
                except ValueError:
                    ours = False
                if not 1 <= (result.position or 0) <= 10 or ours or not result.title or not domain:
                    continue
                their = Brand.of("", domain)
                for part in SPLIT.split(result.title):
                    for phrase, item in _gather(part, "competitor", WEIGHTS["competitor"], None, detail=domain):
                        if phrase is None or their.in_phrase(phrase) or result.url in seen_pages[phrase]:
                            continue
                        seen_pages[phrase].add(result.url)
                        sightings[phrase].append(item)
        for phrase, items in sightings.items():
            if len(seen_pages[phrase]) >= COMPETITOR_MIN_PAGES:
                add([(phrase, item) for item in items])

    for phrase, weight, source, detail in _source_rows(extra):
        if own_brand.in_phrase(phrase):
            dropped.append({"q": phrase, "reason": "your brand", "source": source})
            continue
        raw.setdefault(phrase, Found(q=phrase)).sightings.append(Sighting(source=source, weight=weight, detail=detail))

    core = [page for page in readable if page_class(_attr(page, "url")) in {"home", "core"}]
    vocab = _vocabulary(core if len(core) >= 2 else readable)  # what the site sells: its home and product pages
    brands = _serp_brands(google, vocab) | set(own_brand.words)
    # Near duplicates pool their evidence before any threshold: two "drain cleaning tip(s)" H2s count together.
    groups: list[Found] = []
    for phrase, found in sorted(raw.items(), key=lambda kv: (-sum(kv[1].by_source().values()), -len(kv[1].by_source()), len(kv[0]), kv[0])):
        twin = next((item for item in groups if same_search(item.q, phrase)), None)
        if twin is None:
            groups.append(found)
        else:
            twin.sightings.extend(found.sightings)
            twin.variants.append(phrase)
    scored: list[Found] = []
    for found in groups:
        phrase = found.q
        found.intent, found.intent_rule = classify_intent(phrase, brands=brands)
        found.relevance = relevance(phrase, vocab)
        by_source = found.by_source()
        page_sources = [name for name in by_source if name in WEIGHTS]
        page_total = sum(by_source[name] for name in page_sources) * (1 + 0.25 * (len(page_sources) - 1) if len(page_sources) > 1 else 1)
        found.evidence = page_total + sum(value for name, value in by_source.items() if name not in WEIGHTS)
        if page_sources:
            found.evidence += LENGTH_BONUS if 2 <= len(phrase.split()) <= 5 else 0.0
            found.evidence += BUYING_BONUS if found.intent in {"commercial", "transactional"} else 0.0
        if found.evidence < MIN_SCORE:
            dropped.append({"q": phrase, "reason": "not enough signal", "source": found.sources[0]})
            continue
        if set(page_sources) == {"slug"} and len(by_source) == 1 and len(phrase.split()) < 3:
            dropped.append({"q": phrase, "reason": "only in a page address", "source": "slug"})
            continue
        own_labels = [name for name in page_sources if name in OWN_SOURCES]
        if own_labels and len(by_source) == len(own_labels) and len({item.page for item in found.sightings}) == 1 and found.relevance < MIN_RELEVANCE_OWN:
            dropped.append({"q": phrase, "reason": "not what the site sells", "source": found.sources[0]})
            continue
        if not found.own and found.relevance < MIN_RELEVANCE_OUTSIDE and not any(source not in WEIGHTS for source in by_source):
            dropped.append({"q": phrase, "reason": "off topic", "source": found.sources[0]})
            continue
        found.score = found.evidence * (0.5 + found.relevance) * VALUE[found.intent]
        scored.append(found)
    scored.sort(key=lambda item: (-item.score, len(item.q), item.q))

    picked: list[Found] = []
    pattern_counts: Counter = Counter()
    for found in scored:
        pattern = _pattern(found.q)
        if pattern and pattern_counts[pattern] >= PATTERN_CAP[pattern]:
            dropped.append({"q": found.q, "reason": f"enough {pattern} searches", "source": found.sources[0]})
            continue
        if len(picked) >= limit:
            dropped.append({"q": found.q, "reason": "over the limit", "source": found.sources[0]})
            continue
        picked.append(found)
        if pattern:
            pattern_counts[pattern] += 1
    return Discovery(searches=picked, dropped=_unique_drops(dropped), limit=limit, brand=brand)


# A site with 40 "X alternative" pages would fill the list with them; category searches matter more.
PATTERN_CAP = {"alternative": 8, "versus": 5}


def _pattern(query: str) -> str | None:
    words = query.split()
    if any(word in {"alternative", "alternatives"} for word in words):
        return "alternative"
    if any(word in {"vs", "versus"} for word in words):
        return "versus"
    return None


def _texts(items) -> list[str]:
    out = []
    for item in items or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            value = item.get("query") or item.get("question") or item.get("q") or item.get("title")
            if value:
                out.append(str(value))
    return out


def _unique_drops(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        key = item["q"]
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def is_tracked(query: str, variants, searches) -> bool:
    """True when the site already tracks this search or a near duplicate of it."""
    return any(search == query or search in (variants or []) or same_search(query, search) for search in searches)


def describe(searches: list[str], found: Discovery, *, brands: set[str] | None = None) -> list[dict]:
    """Intent and sources for every tracked search: from discovery when it found the same search, otherwise the
    intent rules alone and the source "added" (the person or an import added it)."""
    out = []
    for search in searches:
        match = next((item for item in found.searches if item.q == search or search in item.variants or same_search(item.q, search)), None)
        if match is not None:
            data = match.to_dict()
            data["q"] = search
        else:
            intent, rule = classify_intent(search, brands=brands)
            data = {"q": search, "intent": intent, "intent_rule": rule, "source": "added", "sources": [{"source": "added", "weight": 0, "detail": []}], "variants": [], "page": None}
        out.append(data)
    return out


def _source_rows(rows) -> list[tuple[str, float, str, str]]:
    """Usable plugin rows as (phrase, weight, source, detail). A row is a real search, so the heading rules (word
    count, section labels, numbers) do not apply. Rows that are not a dict, have no text, run over 100 characters,
    or have a weight that is not a positive number are dropped. A row without a weight counts 3.0."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not isinstance(row.get("q"), str):
            continue
        weight = row.get("weight", SOURCE_WEIGHT)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
            continue
        text = " ".join(row["q"].split())
        phrase = clean(text) if len(text) <= MAX_SOURCE_PHRASE else ""
        if not phrase:
            continue
        source = re.sub(r"[^a-z0-9_-]", "", str(row.get("source") or "plugin").lower())[:24] or "plugin"
        if source in WEIGHTS or source in OWN_SOURCES or source == "added":
            source = f"plugin-{source}"  # a plugin row never passes for one of the site's own sources
        saved = row.get("detail")
        if isinstance(saved, str) and saved:
            detail = saved[:200]  # a row saved for the report keeps what it said
        else:
            details = [f"weight {float(weight):g}"]
            for key in ("impressions", "clicks", "position"):
                value = row.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    details.append(f"{key} {value:g}")
            detail = ", ".join(details)
        out.append((phrase, float(weight), source, detail))
    return out


def discover_searches(pages: list[SitePage], brand: str, *, limit: int = MAX_SEARCHES, host: str = "", extra: list[dict] | None = None, google=None, serp_features=None) -> list[str]:
    """The searches to track, best first. See the module docstring for the sources and the scale. With no extra rows
    the result is what the site's pages (and Google data, when given) alone give."""
    return discover(pages, brand, host=host, google=google, serp_features=serp_features, extra=extra, limit=limit).queries


def plugin_rows(project, pages, ui) -> list[dict]:
    from aeo.ranksim import plugins

    return plugins.collect("discovery_sources", project=project, pages=pages, ui=ui)


def discover_with_plugins(pages: list[SitePage], brand: str, *, host: str = "", project=None, ui=None, limit: int = MAX_SEARCHES) -> list[str]:
    """discover_searches with every plugin's discovery_sources rows merged in, plus the project's stored Google data
    when there is a project. The rows are kept so the report can show where each search came from.

    project is None on a site's first analysis (the project is created after its searches are found).
    """
    rows = plugin_rows(project, pages, ui)
    google = features = None
    if project is not None:
        save_rows(project, rows)
        google, features = google_inputs(project)
    elif rows and host:
        _stash_rows(ui, host, rows)  # the project does not exist yet; load_rows picks these up
    return discover_searches(pages, brand, limit=limit, host=host, extra=rows, google=google, serp_features=features)


def _rows_path(project):
    from aeo.ranksim import plugins

    return plugins.data_dir(project, "discover") / "rows.json"


def save_rows(project, rows: list[dict]) -> None:
    import json

    try:
        clean_rows = [{"q": phrase, "weight": weight, "source": source, "detail": detail} for phrase, weight, source, detail in _source_rows(rows)]
        _rows_path(project).write_text(json.dumps(clean_rows), encoding="utf-8")
    except (OSError, ValueError, TypeError, AttributeError):
        pass  # the rows only label sources in the report; discovery itself already used them


def _stash_key(host: str) -> str:
    return re.sub(r"[^a-z0-9.-]", "", (host or "").lower().removeprefix("www."))[:200] or "site"


def _stash_rows(ui, host: str, rows: list[dict]) -> None:
    """Rows from a site's first analysis, before its project exists: <projects root>/_plugins/discover/pending/."""
    import json

    from aeo.ranksim import plugins

    try:
        folder = plugins.workspace_dir(ui, "discover") / "pending"
        folder.mkdir(parents=True, exist_ok=True)
        clean_rows = [{"q": phrase, "weight": weight, "source": source, "detail": detail} for phrase, weight, source, detail in _source_rows(rows)]
        (folder / f"{_stash_key(host)}.json").write_text(json.dumps(clean_rows), encoding="utf-8")
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def load_rows(project) -> list[dict]:
    """The plugin rows saved for a project. Rows stashed by its first analysis move into the project here."""
    import json

    try:
        data = json.loads(_rows_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, AttributeError):
        data = None
        try:
            pending = project.dir.parent / "_plugins" / "discover" / "pending" / f"{_stash_key(project.domain)}.json"
            data = json.loads(pending.read_text(encoding="utf-8"))
            _rows_path(project).write_text(json.dumps(data), encoding="utf-8")
            pending.unlink()
        except (OSError, ValueError, TypeError, AttributeError):
            return []
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def google_inputs(project):
    """The latest Google snapshot and its SERP features ({search: {"related", "paa"}}), or Nones."""
    from aeo.ranksim.project import latest_google

    try:
        google = latest_google(project)
    except Exception:  # noqa: BLE001 - discovery works without Google data
        google = None
    features: dict = {}
    try:
        from aeo.ranksim.plugins import tracking  # the track session's helper, when it is installed

        found = tracking.serp_features(project)
        if isinstance(found, dict):
            features = {str(search): _features(value) for search, value in found.items() if isinstance(value, dict)}
    except Exception:  # noqa: BLE001 - optional
        features = {}
    if not features:
        features = serp_extras(latest_google_json(project))
    return google, {search: value for search, value in features.items() if value["related"] or value["paa"]}


def latest_google_json(project) -> dict | None:
    """The newest pull's raw ``google.json``."""
    import json

    from aeo.ranksim.project import list_pulls

    try:
        pulls = list_pulls(project)
    except Exception:  # noqa: BLE001
        return None
    for info in reversed(pulls):
        try:
            return json.loads((project.dir / "pulls" / info.pull_id / "google.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def _features(entry: dict) -> dict:
    related = entry.get("related_searches") or entry.get("related") or entry.get("relatedSearches") or []
    paa = entry.get("people_also_ask") or entry.get("paa") or entry.get("peopleAlsoAsk") or []
    return {"related": _texts(related), "paa": _texts(paa)}


def serp_extras(google_json: dict | None) -> dict[str, dict]:
    """Related searches and people also ask per search, from a stored ``google.json`` dict. Missing keys are fine."""
    out: dict[str, dict] = {}
    for entry in (google_json or {}).get("searches") or [] if isinstance(google_json, dict) else []:
        if not isinstance(entry, dict):
            continue
        search = str(entry.get("search") or "")
        found = _features(entry)
        features = entry.get("features")
        if isinstance(features, dict):  # the tracking plugin's shape: searches[i].features
            nested = _features(features)
            found = {"related": found["related"] or nested["related"], "paa": found["paa"] or nested["paa"]}
        if search and (found["related"] or found["paa"]):
            out[search] = found
    return out


# -- names older code imports ------------------------------------------------------------------------------

COMMERCIAL = ("software", "app", "apps", "tool", "tools", "alternative", "alternatives", " vs ", "best ", "cost", "price", "how to", "platform", "system", "contractor", "contractors", "what is ")
_clean = clean
brand_tokens = host_tokens


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
    tokens = sorted(host_tokens(host), key=len)
    text = " ".join(f"{page.title} {page.h1}" for page in readable)
    for token in tokens:
        match = re.search(rf"\b({re.escape(token)})\b", text, re.I)
        if match and len(token) >= 4:
            return match.group(1) if not match.group(1).islower() else match.group(1).title()
    return tokens[-1].replace("-", " ").title() if tokens else host
