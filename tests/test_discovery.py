"""Search discovery, competitor discovery, competitor profiles and content gaps. Fakes only; no network."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from test_autopilot import _lines, _site_pages, _token
from test_project import SITE, TODAY, FakeCrawler

from aeo.ranksim import autopilot
from aeo.ranksim import competitors as cp
from aeo.ranksim import discovery as dn
from aeo.ranksim.project import GoogleSnapshot, SearchResults, SerpResult, load_project
from aeo.ranksim.serve import create_app
from tests.test_hosted import FakeGoogle
from tests.test_serve_project import make_settings

SC = "smartcrewhq.example"
SC_HOME = f"https://{SC}/"


def _page(url: str, title: str, h1: str = "", h2s=(), body: str = "", schema: dict | None = None) -> str:
    ld = f'<script type="application/ld+json">{json.dumps(schema)}</script>' if schema else ""
    heads = "".join(f"<h2>{text}</h2><p>{body}</p>" for text in h2s)
    return f"<!doctype html><html><head><title>{title}</title>{ld}</head><body><main><h1>{h1}</h1><p>{body}</p>{heads}</main></body></html>"


def _smartcrew_pages():
    """A site with tagline titles and a brand that is also part of the domain (smartcrewhq)."""
    html = {
        SC_HOME: _page(SC_HOME, "SmartCrew | Run your crew, not your paperwork", "The all-in-one dirt work software for excavation contractors", ["Why crews switch", "Dispatch, timecards and job costing in one app", "Questions people ask"]),
        f"{SC_HOME}all-in-one-dirt-work-software/": _page("", "All-in-One Dirtwork Software for Excavation Contractors | SmartCrew", "All-in-one dirt work software", ["Excavation job costing software", "Pricing"]),
        f"{SC_HOME}hcss-alternative/": _page("", "HCSS Alternative: Switch in a Week | SmartCrew", "The HCSS alternative built for dirt work", ["HCSS vs SmartCrew", "Heavy civil software without the bloat"]),
        f"{SC_HOME}excavation-software/": _page("", "Excavation Software That Your Crew Will Actually Use | SmartCrew", "Excavation software", ["Excavation scheduling software"]),
        f"{SC_HOME}about/": _page("", "About us | SmartCrew", "About SmartCrew"),
        f"{SC_HOME}login/": _page("", "SmartCrew login", "Sign in"),
        f"{SC_HOME}blog/2026/05/123/": _page("", "Get more done this spring | SmartCrew", "Get more done", ["Read more"]),
    }
    return [autopilot.page_facts(url, text) for url, text in html.items()]


def _result(position, url, title=""):
    from aeo.crawl.urls import domain_of

    return SerpResult(position=position, url=url, domain=domain_of(url), title=title)


def _google() -> GoogleSnapshot:
    """REAL-shaped Google results for three SmartCrew searches."""
    rows = {
        "all in one dirt work software": [
            ("https://www.g2.com/categories/excavation", "Best Excavation Software 2026 | G2"),
            ("https://mitti.example/dirt-work-software", "Dirt Work Software for Excavation Contractors | Mitti"),
            ("https://www.reddit.com/r/Construction/comments/abc/", "Best all in one dirt work software? : r/Construction"),
            (f"{SC_HOME}all-in-one-dirt-work-software/", "All-in-One Dirtwork Software | SmartCrew"),
            ("https://getonecrew.example/excavation", "Excavation Software for Contractors | OneCrew"),
            ("https://www.capterra.com/excavation-software/", "Best Excavation Software 2026 - Reviews & Pricing"),
            ("https://www.youtube.com/watch?v=xyz", "Dirt work software demo - YouTube"),
        ],
        "hcss alternative": [
            ("https://constructionbids.example/blog/hcss-alternative", "Top 7 HCSS Alternatives for Heavy Construction"),
            ("https://mitti.example/hcss-alternative", "The Best HCSS Alternative for Dirt Work | Mitti"),
            ("https://help.hcss.example/s/article/alternates", "How to Setup Package Alternates in HeavyBid"),
            ("https://getonecrew.example/post/hcss-heavyjob-alternatives", "HCSS HeavyJob Alternatives for Excavation Contractors"),
            ("https://www.g2.com/products/hcss/competitors/alternatives", "Top 10 HCSS Alternatives & Competitors | G2"),
        ],
        "excavation software": [
            ("https://getonecrew.example/excavation", "Excavation Software for Contractors | OneCrew"),
            ("https://mitti.example/excavation", "Excavation Software for Contractors | Mitti"),
            ("https://constructionbids.example/blog/best-excavation-software", "10 Best Excavation Software Tools Ranked"),
            ("https://dirtnews.example/excavation-software-guide", "Excavation software buyers guide"),
            (f"{SC_HOME}excavation-software/", "Excavation Software | SmartCrew"),
        ],
    }
    return GoogleSnapshot(
        pull_id="p1",
        written_at="2026-11-09T00:00:00Z",
        kind="import",
        searches=[SearchResults(search=q, results=[_result(i, url, title) for i, (url, title) in enumerate(items, start=1)], source="import", provider="csv", pulled_at="2026-11-09T00:00:00Z") for q, items in rows.items()],
    )


EXTRAS = {
    "all in one dirt work software": {
        "related": ["dirt work software free", "excavation contractor software", "dirt bike for sale", "smartcrew reviews"],
        "paa": ["What is the best software for excavation contractors?", "How much does excavation software cost?"],
    },
    "excavation software": {"related": ["excavation contractor software", "excavation estimating software"], "paa": ["What is the best software for excavation contractors?"]},
}

GSC = [
    {"q": "dirt work management software", "weight": 5.0, "source": "gsc", "impressions": 1200},
    {"q": "smartcrew login", "weight": 4.0, "source": "gsc", "impressions": 900},
    {"q": "smart crew app", "weight": 3.5, "source": "gsc", "impressions": 400},
    {"q": "heavy civil field management software", "weight": 2.6, "source": "gsc", "impressions": 35},
]


# -- search discovery -----------------------------------------------------------------------------------


def test_two_word_brand_blocks_the_pair_not_each_word():
    pages = [autopilot.page_facts(url, html) for url, html in _site_pages().items() if url.startswith(f"https://{SITE}/") and not url.endswith(".xml")]
    brand = autopilot.brand_of(pages, SITE)
    assert brand == "Dirt Crew"
    found = dn.discover(pages, brand, host=SITE)
    queries = found.queries
    assert "dirt work software" in queries and "excavation crew app" in queries
    assert not any("dirt crew" in q or "dirtcrew" in q for q in queries)
    assert "about us" not in queries
    assert any(item["q"] == "dirt crew" and item["reason"] == "your brand" for item in found.dropped)
    b = dn.Brand.of("Dirt Crew", SITE)
    assert b.in_phrase("dirt crew pricing") and b.in_phrase("dirtcrew app")
    assert not b.in_phrase("dirt work crew software") and not b.in_phrase("crew dirt")


def test_tagline_titles_give_searches_not_slogans():
    pages = _smartcrew_pages()
    brand = autopilot.brand_of(pages, SC)
    assert brand == "SmartCrew"
    found = dn.discover(pages, brand, host=SC)
    queries = found.queries
    assert "all in one dirt work software" in queries or "all in one dirtwork software" in queries
    assert "excavation software" in queries and "hcss alternative" in queries
    for slogan in ("run your crew not your paperwork", "switch in a week", "get more done this spring", "get more done"):
        assert slogan not in queries
    assert not any("smartcrew" in q or "smart crew" in q for q in queries)
    assert "questions people ask" not in queries and "read more" not in queries and "pricing" not in queries
    reasons = {item["q"]: item["reason"] for item in found.dropped}
    assert reasons.get("run your crew not your paperwork") == "a tagline"
    # every search says where it came from and links the page that targets it
    row = next(item for item in found.to_dict()["searches"] if item["q"] == "excavation software")
    assert {"title", "h1", "slug"} <= {source["source"] for source in row["sources"]}
    assert row["page"] == f"{SC_HOME}excavation-software/"
    assert row["intent"] == "commercial" and row["intent_rule"] == 'has "software"'


def test_long_titles_are_cut_into_product_phrases():
    assert dn._cut("All-in-one dirtwork software for excavation contractors", 6) == [
        ("all in one dirtwork software", 0.8),
        ("dirtwork software for excavation contractors", 0.6),
    ]
    assert dn._cut("Excavation crew app for dispatch, timecards, daily logs and job costing", 6) == [("excavation crew app", 0.8)]
    assert dn._cut("Dirt work software", 6) == [("dirt work software", 1.0)]


@pytest.mark.parametrize(
    "phrase,intent",
    [
        ("smartcrew login", "navigational"),
        ("excavation software pricing", "transactional"),
        ("excavation contractors near me", "transactional"),
        ("how to bid excavation jobs", "informational"),
        ("What is the best software for excavation contractors?", "informational"),
        ("best excavation software", "commercial"),
        ("hcss alternative", "commercial"),
        ("procore vs hcss", "commercial"),
        ("excavation job costing guide", "informational"),
        ("hcss heavyjob", "navigational"),
        ("soil compaction", "informational"),
    ],
)
def test_intent_rules(phrase, intent):
    got, rule = dn.classify_intent(phrase, brands={"hcss", "procore"})
    assert got == intent, (phrase, got, rule)
    assert rule


def test_sources_merge_score_and_stay_labeled():
    pages = _smartcrew_pages()
    found = dn.discover(pages, "SmartCrew", host=SC, google=_google(), serp_features=EXTRAS, extra=GSC)
    data = found.to_dict()
    by_q = {item["q"]: item for item in data["searches"]}
    # related searches on topic come in; off topic and branded ones do not
    assert "excavation contractor software" in by_q
    assert {source["source"] for source in by_q["excavation contractor software"]["sources"]} >= {"related"}
    assert "dirt bike for sale" not in by_q
    assert any(item["q"] == "dirt bike for sale" and item["reason"] == "off topic" for item in data["dropped"])
    assert "smartcrew reviews" not in by_q
    # Search Console rows arrive through plugin rows, weighted by impressions; branded ones are dropped
    assert "dirt work management software" in by_q and by_q["dirt work management software"]["source"] == "gsc"
    assert "smartcrew login" not in by_q and "smart crew app" not in by_q
    # a question people also ask on two searches is kept as an informational search
    question = "what is the best software for excavation contractors"
    assert question in by_q and by_q[question]["intent"] == "informational"
    # a phrase from other sites' titles needs two pages; "excavation software for contractors" has three
    competitor_sourced = [item for item in data["searches"] if any(source["source"] == "competitor" for source in item["sources"])]
    assert competitor_sourced
    assert all("mitti" not in item["q"] and "onecrew" not in item["q"] for item in data["searches"])
    assert data["source_counts"]["title"] >= 3
    scores = [item["score"] for item in data["searches"]]
    assert scores == sorted(scores, reverse=True)
    assert len(found.queries) == len(set(found.queries)) <= dn.MAX_SEARCHES


def test_near_duplicates_merge_into_one_search():
    assert dn.same_search("all in 1 dirt work software", "all in one dirtwork software")
    assert dn.same_search("best dirt work software", "dirt work software")
    assert dn.same_search("excavation software 2026", "excavation software")
    assert dn.same_search("software excavation", "excavation softwares")
    assert not dn.same_search("dirt work management software", "dirt work software")
    assert not dn.same_search("excavation software", "excavation estimating software")
    rows = [
        {"q": "all in 1 dirt work software", "weight": 50, "source": "gsc"},
        {"q": "best all in one dirtwork software", "weight": 10, "source": "gsc"},
    ]
    found = dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, extra=rows)
    merged = [item for item in found.searches if dn.same_search(item.q, "all in one dirt work software")]
    assert len(merged) == 1
    assert "gsc" in merged[0].sources and merged[0].variants


def test_the_list_is_capped_and_the_rest_is_reported():
    rows = [{"q": f"excavation {word} software", "weight": 100, "source": "gsc"} for word in ("trench", "grading", "hauling", "paving", "utility", "demolition", "site", "pipe", "septic", "pond", "land", "road", "drainage", "fill", "stump", "rock", "concrete", "asphalt", "survey", "erosion", "foundation", "basement")]
    found = dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, extra=rows)
    assert len(found.searches) == dn.MAX_SEARCHES
    assert any(item["reason"] == "over the limit" for item in found.dropped)
    assert len(dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, extra=rows, limit=5).searches) == 5


def test_discovery_degrades_without_google_or_extras():
    pages = _smartcrew_pages()
    plain = dn.discover(pages, "SmartCrew", host=SC)
    assert plain.queries and set(plain.to_dict()["source_counts"]) <= dn.OWN_SOURCES
    assert dn.serp_extras(None) == {} and dn.serp_extras({"searches": [{"search": "x", "results": []}]}) == {}
    stored = {"searches": [{"search": "a", "related_searches": [{"query": "b c"}], "people_also_ask": [{"question": "what is d?"}]}]}
    assert dn.serp_extras(stored) == {"a": {"related": ["b c"], "paa": ["what is d?"]}}
    assert dn.discover_searches(pages, "SmartCrew", host=SC) == plain.queries
    assert dn.discover([], "SmartCrew", host=SC).queries == []
    bad = dn.discover(pages, "SmartCrew", host=SC, extra=[{"q": "excavation pond software", "weight": "lots"}, {"weight": 3}, "text row"])
    assert "excavation pond software" not in bad.queries and bad.queries == plain.queries  # invalid rows are dropped


def test_slugs_read_as_words():
    assert dn.slug_phrase("https://x.example/dirt-work-software/") == "dirt work software"
    assert dn.slug_phrase("https://x.example/blog/2026/05/123/") is None
    assert dn.slug_phrase("https://x.example/about/") is None
    assert dn.slug_phrase("https://x.example/") is None
    assert dn.slug_phrase("https://x.example/excavation_software.html") == "excavation software"


# -- competitor discovery ---------------------------------------------------------------------------------


def test_competitors_are_businesses_and_directories_are_their_own_group():
    rows = cp.classify_domains(_google(), SC)
    kinds = {row["domain"]: row["kind"] for row in rows}
    assert kinds["g2.com"] == "directory" and kinds["capterra.com"] == "directory"
    assert kinds["reddit.com"] == "forum" and kinds["youtube.com"] == "video"
    assert kinds["smartcrewhq.example"] == "you"
    assert kinds["mitti.example"] == "competitor" and kinds["getonecrew.example"] == "competitor"
    assert kinds["help.hcss.example"] == "competitor"
    assert kinds["dirtnews.example"] == "publisher"
    assert all(row["reason"] for row in rows)
    picked = cp.pick_competitors(_google(), SC)
    assert picked[:2] == ["mitti.example", "getonecrew.example"]  # in all three searches, near the top
    assert "g2.com" not in picked and "reddit.com" not in picked and SC not in picked
    vis = {row["domain"]: row["visibility"] for row in rows}
    assert vis["mitti.example"] == pytest.approx((11 - 2) / 10 + (11 - 2) / 10 + (11 - 2) / 10)
    share = cp.share_of_top10(_google(), SC)
    assert share[0]["slots"] >= share[-1]["slots"] and {"domain", "slots", "searches", "best", "average", "you", "platform", "kind", "reason"} <= set(share[0])
    assert cp.classify_domains(None, SC) == [] and cp.pick_competitors(None, SC) == []


def test_list_article_sites_with_no_product_are_publishers():
    listicle = _page("", "Top 7 HCSS Alternatives", "Top 7", ["1. Mitti"], "words " * 50, {"@type": "BlogPosting"})
    pages = [cp.page_profile("https://constructionbids.example/blog/a", listicle), cp.page_profile("https://constructionbids.example/blog/b", listicle)]
    kind, reason = cp.classify_domain("constructionbids.example", titles=["Top 7 HCSS Alternatives", "10 Best Excavation Software Tools Ranked"], pages=pages)
    assert kind == "publisher" and "list articles" in reason
    shop = _page("", "Top 7 HCSS Alternatives", "Top 7", ["Book a demo"], "Book a demo today", {"@type": "SoftwareApplication"})
    kind, _ = cp.classify_domain("vendor.example", titles=["Top 7 HCSS Alternatives", "Best excavation software"], pages=[cp.page_profile("https://vendor.example/a", shop)])
    assert kind == "competitor"
    assert cp.classify_domain("community.procore.com")[0] == "forum"
    assert cp.classify_domain("www.osha.gov")[0] == "publisher"
    assert cp.classify_domain("constructionreviews.example")[0] == "directory"
    assert cp.classify_domain("www.capterra.co.uk")[0] == "directory"


# -- profiles ------------------------------------------------------------------------------------------------


def _docs() -> dict[str, str]:
    body = "Track job costing for every excavation job. Crew timecards sync daily. Job costing reports by phase. " * 20
    return {
        "https://mitti.example/dirt-work-software": _page("", "Dirt Work Software | Mitti", "Dirt work software", ["Job costing", "Pricing plans", "Crew timecards"], body, {"@type": "SoftwareApplication", "offers": {"@type": "Offer"}}),
        "https://mitti.example/excavation": _page("", "Excavation Software | Mitti", "Excavation software", ["Job costing by phase", "Pricing"], body, {"@graph": [{"@type": "SoftwareApplication"}, {"@type": "FAQPage"}]}),
        "https://getonecrew.example/excavation": _page("", "Excavation Software | OneCrew", "Excavation software", ["Excavation job costing", "Simple pricing", "Daily logs"], body + "Daily logs for crews. " * 10, {"@type": "FAQPage"}),
        "https://www.g2.com/categories/excavation": _page("", "Best Excavation Software", "Best excavation software", ["Top picks"], "Ratings and reviews. " * 40),
        f"{SC_HOME}all-in-one-dirt-work-software/": _page("", "All-in-One Dirtwork Software | SmartCrew", "All-in-one dirt work software", ["Daily logs"], "Daily logs for crews in the field. " * 30),
        f"{SC_HOME}excavation-software/": _page("", "Excavation Software | SmartCrew", "Excavation software", ["Daily logs"], "Daily logs for crews in the field. " * 30),
    }


def test_profiles_show_what_each_competitor_ranks_with():
    profiles = cp.competitor_profiles(_google(), SC, _docs())
    assert set(profiles) == {"competitors", "directories", "platforms", "publishers"}
    mitti = profiles["competitors"][0]
    assert mitti["domain"] == "mitti.example"
    assert {row["q"] for row in mitti["rankings"]} == {"all in one dirt work software", "hcss alternative", "excavation software"}
    assert mitti["average"] == 2.0 and mitti["best"] == 2 and mitti["searches"] == 3
    assert mitti["titles"]["separator"] == "|" and mitti["titles"]["brand_in_title"] == 1.0
    assert "software" in mitti["titles"]["common_words"]
    assert mitti["words"]["pages"] == 2 and mitti["words"]["median"] > 100
    assert {"type": "SoftwareApplication", "pages": 2} in mitti["schema"]
    assert mitti["best_pages"][0]["url"].startswith("https://mitti.example/") and "searches" in mitti["best_pages"][0]
    assert mitti["pages_stored"] == 2 and mitti["pages_ranking"] == 3
    assert [row["domain"] for row in profiles["directories"]] == ["g2.com", "capterra.com"]
    assert {row["domain"] for row in profiles["platforms"]} == {"reddit.com", "youtube.com"}
    assert not any(row["domain"] == SC for group in profiles.values() for row in group)
    # without stored pages the profile still has rankings and title patterns, and says nothing about words
    bare = cp.competitor_profiles(_google(), SC, {})
    assert bare["competitors"][0]["words"] is None and bare["competitors"][0]["rankings"]


# -- gaps ----------------------------------------------------------------------------------------------------


def _rankings():
    google = _google()
    rows = []
    for entry in google.searches:
        mine = next((item for item in entry.results if item.url.startswith(SC_HOME)), None)
        rows.append({
            "q": entry.search,
            "position": mine.position if mine else None,
            "url": mine.url if mine else None,
            "top": [{"position": item.position, "url": item.url, "you": item.url.startswith(SC_HOME)} for item in entry.results],
        })
    return rows


def test_gaps_name_what_the_pages_above_share_with_evidence():
    gaps = {row["q"]: row for row in cp.content_gaps(_rankings(), _docs())}
    gap = gaps["excavation software"]
    assert gap["page"] == f"{SC_HOME}excavation-software/" and gap["page_stored"]
    assert set(gap["compared"]) == {"https://getonecrew.example/excavation", "https://mitti.example/excavation"}
    topics = {item["topic"].lower(): item for item in gap["topics"]}
    assert any("job costing" in topic for topic in topics)
    assert any("pricing" in topic for topic in topics)
    assert not any("daily logs" in topic for topic in topics)  # you have that section
    pricing = next(item for topic, item in topics.items() if "pricing" in topic)
    assert pricing["pages"] == 2 and pricing["of"] == 2 and {e["url"] for e in pricing["evidence"]} == set(gap["compared"])
    terms = {item["term"]: item for item in gap["terms"]}
    assert "job costing" in terms and set(terms["job costing"]["evidence"]) == set(gap["compared"])
    assert "daily log" not in terms and "crew" not in terms  # you use those words
    assert {item["type"] for item in gap["schema"]} == {"FAQPage"}  # only one of two pages has SoftwareApplication
    assert gap["words"]["yours"] and gap["words"]["median_above"] > gap["words"]["yours"]
    # the search you rank 4th for compares the three stored pages above you, skipping the unstored ones
    first = gaps["all in one dirt work software"]
    assert set(first["compared"]) == {"https://www.g2.com/categories/excavation", "https://mitti.example/dirt-work-software"}
    # not ranking and nothing stored: says so
    assert gaps["hcss alternative"]["page"] is None and gaps["hcss alternative"].get("note")


# -- end to end with the store ---------------------------------------------------------------------------


def test_report_sections_read_the_store(tmp_path):
    settings = make_settings(tmp_path, serpapi_key="sk-test-discover")
    app = create_app(
        settings=settings,
        projects_root=tmp_path / "projects",
        public_demo=False,
        pull_options={"serp_provider": FakeGoogle(), "fetcher": FakeCrawler(_site_pages()), "today": TODAY, "workers": 4},
    )
    client = TestClient(app)
    done = _lines(client.post("/api/v1/sites", json={"site": SITE}, headers={"x-ranksim-csrf": _token(client)}))[-1]
    assert done["type"] == "done"
    report = client.get(f"/api/v1/projects/{done['slug']}/report").json()
    project = load_project(done["slug"], root=tmp_path / "projects")
    pages = autopilot.stored_site_pages(project, settings.ranksim_database_url)
    # the API report already carries the sections
    assert {"discovery", "competitor_profiles", "gaps"} <= set(report) and report["discovery"]["searches"]
    dn.save_rows(project, [{"q": "grading crew software", "weight": 10, "source": "gsc"}, "junk"])
    sections = cp.report_sections(project, None, settings.ranksim_database_url, report, pages=pages)
    assert {"discovery", "competitor_profiles", "gaps"} <= set(sections)
    queries = {item["q"]: item for item in sections["discovery"]["searches"]}
    assert "dirt work software" in queries and queries["dirt work software"]["tracked"]
    assert "grading crew software" in queries and not queries["grading crew software"]["tracked"]
    profiles = sections["competitor_profiles"]["competitors"]
    assert profiles and all(row["domain"].endswith(".example") and not row["domain"].endswith("dirtcrew.example") for row in profiles)
    assert any(row["words"] for row in profiles)  # the pull stored their pages
    gap = next(row for row in sections["gaps"] if row["q"] == "dirt work software")
    assert gap["page_stored"] is False or gap["page"]
    assert len(gap["compared"]) >= 2 and (gap["topics"] or gap["terms"])
    json.dumps(sections)  # the report is JSON


# -- regressions from adversarial review ------------------------------------------------------------------


def _titles(titles, brand, host, **kwargs):
    pages = [autopilot.SitePage(url=f"https://{host}/p{i}/", status="fetched", title=t) for i, t in enumerate(titles)]
    return dn.discover(pages, brand, host=host, **kwargs)


def test_brand_affixes_never_block_ordinary_words():
    assert _titles(["Roof repair services | Roof Co"], "Roof Co", "roofco.com").queries == ["roof repair services"]
    assert _titles(["Man and van services | Mango"], "Mango", "mango.com").queries == ["man and van services"]
    assert _titles(["Law professional liability insurance | Law Pro"], "Law Pro", "lawpro.com").queries == ["law professional liability insurance"]
    assert _titles(["3M safety products | 3M"], "3M", "3m.com").queries == []
    assert _titles(["Monday CRM software | monday.com"], "monday.com", "").queries == []
    assert _titles(["Smith Plumbing and Heating | Smith Plumbing & Heating"], "Smith Plumbing & Heating", "smithph.com").queries == []


def test_real_searches_are_not_mistaken_for_labels_or_taglines():
    kept = set(_titles(
        ["Contact center software | Acme", "All season tires | Acme", "Browser testing tool | Acme", "Power washing services | Acme",
         "Work order software | Acme", "Austin Plumber / Drain Cleaning | Acme", "Klempner München | Acme"],
        "Acme", "acme.com",
        extra=[{"q": "plumber near me", "weight": 50}, {"q": "us immigration lawyer", "weight": 50}, {"q": "24 hour plumber"}, 5, None],
    ).queries)
    assert {"contact center software", "all season tires", "browser testing tool", "power washing services", "work order software",
            "austin plumber", "drain cleaning", "klempner munchen", "plumber near me", "us immigration lawyer", "24 hour plumber"} <= kept
    dropped = _titles(["What We Do | Acme", "How can we help you? | Acme", "Meet the Team | Acme"], "Acme", "acme.com")
    assert dropped.queries == []


def test_intent_respects_products_and_weak_question_words():
    for phrase in ("customer service software", "will writing service", "criminal trial lawyer", "emergency plumber austin", "virtual phone number service"):
        assert dn.classify_intent(phrase)[0] == "commercial", phrase
    assert dn.classify_intent("can i deduct software costs")[0] == "transactional"  # "costs" wins before the question rule
    assert dn.classify_intent("is it worth hiring a roofer")[0] == "informational"


def test_dedupe_keeps_direction_and_place_names():
    assert not dn.same_search("pdf to word converter", "word to pdf converter")
    assert not dn.same_search("york plumber", "new york plumber")
    assert dn.same_search("mens shoes", "men's shoes")
    pooled = _titles(["x | Acme"], "Acme", "acme.com")
    assert pooled.queries == []
    pages = [autopilot.SitePage(url="https://acme.com/a/", status="fetched", title="Acme", h2s=["Drain cleaning tips"]),
             autopilot.SitePage(url="https://acme.com/b/", status="fetched", title="Acme", h2s=["Drain Cleaning Tip"])]
    found = dn.discover(pages, "Acme", host="acme.com").queries
    assert len(found) == 1 and dn.same_search(found[0], "drain cleaning tips")  # two H2s together pass


def test_bad_inputs_do_not_crash_discovery():
    google = GoogleSnapshot("p", "", "import", [SearchResults("q", [SerpResult(1, "http://[bad/x", "", "Bad title here")], "import", "csv", None)])
    assert dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, google=google, serp_features=["not", "a", "dict"], extra=["excavation pond software", 3, None]).queries
    assert dn.serp_extras(["x"]) == {} and dn.serp_extras({"searches": ["x", None]}) == {}
    assert dn.slug_phrase("https://x.com/en-gb/") is None and dn.slug_phrase("https://x.com/p/3f2b9c1e-ab12-cd34/") is None


def test_classification_handles_country_sites_names_and_subdomains():
    assert cp.classify_domain("www.amazon.co.uk")[0] == "marketplace"
    assert cp.classify_domain("capterra.ca")[0] == "directory"
    assert cp.classify_domain("unimelb.edu.au")[0] == "publisher"
    assert cp.classify_domain("fieldwire.com")[0] == "competitor"
    assert cp.classify_domain("newstore.com")[0] == "competitor"
    assert cp.classify_domain("useinsider.com")[0] == "competitor"
    assert cp.classify_domain("reviewtrackers.com")[0] == "competitor"
    assert cp.classify_domain("www.bbc.co.uk")[0] == "publisher" and cp.classify_domain("techcrunch.com")[0] == "publisher"
    assert cp.classify_domain("news.ycombinator.com")[0] == "forum"
    kind, reason = cp.classify_domain("play.google.com")
    assert kind == "marketplace" and "play.google.com" in reason
    rows = {row["domain"]: row for row in cp.classify_domains(_google(), SC)}
    assert "hcss.example" in rows or "help.hcss.example" in rows  # one host alone keeps its name
    two = GoogleSnapshot("p", "", "import", [
        SearchResults("a", [_result(1, "https://hcss.example/x", "HCSS"), _result(2, "https://help.hcss.example/y", "Help")], "import", "csv", None),
    ])
    merged = {row["domain"]: row for row in cp.classify_domains(two, SC)}
    assert list(merged) == ["hcss.example"] and merged["hcss.example"]["hosts"] == ["hcss.example", "help.hcss.example"]


def test_list_articles_that_mention_pricing_are_still_publishers():
    article = _page("", "Top 7 HCSS Alternatives", "Top 7", ["1. Mitti"], "Mitti pricing starts at $50. Free trial available. " * 10, {"@type": "BlogPosting"})
    pages = [cp.page_profile("https://bids.example/a", article), cp.page_profile("https://bids.example/b", article)]
    assert cp.classify_domain("bids.example", titles=["Top 7 HCSS Alternatives", "10 Best Excavation Software"], pages=pages)[0] == "publisher"


def test_gaps_ignore_chrome_brand_word_forms_and_duplicates():
    body = "Smartcrew is slower. Crew scheduling and estimating for every job. Crew scheduling made easy. Estimating tools. " * 8
    footer = "<footer><h3>Company</h3><h3>Resources</h3><h3>Subscribe to our newsletter</h3></footer>"
    a = _page("", "A", "A", ["Crew scheduling", "Estimating"], body).replace("</body>", footer + "</body>")
    b = _page("", "B", "B", ["Crew scheduling tips", "Estimating jobs"], body).replace("</body>", footer + "</body>")
    mine = _page("", "Mine", "Mine", ["Schedule your crew", "Estimate jobs"], "We schedule crews and estimate every job for smartcrew users. " * 10)
    rows = [{"q": "crew scheduling software", "position": 5, "url": f"{SC_HOME}x/", "top": [
        {"position": 1, "url": "https://a.example/1", "you": False},
        {"position": 2, "url": "https://a.example/1", "you": False},
        {"position": 3, "url": "https://b.example/2", "you": False},
        {"position": None, "url": "https://c.example/3", "you": False},
        {"position": 5, "url": f"{SC_HOME}x/", "you": True},
    ]}]
    gap = cp.content_gaps(rows, {"https://a.example/1": a, "https://b.example/2": b, f"{SC_HOME}x/": mine}, own_domain=SC)[0]
    assert gap["compared"] == ["https://a.example/1", "https://b.example/2"]
    topics = {item["topic"].lower() for item in gap["topics"]}
    assert not topics & {"company", "resources", "subscribe to our newsletter"}
    assert not any("scheduling" in topic or "estimating" in topic for topic in topics), topics
    terms = {item["term"] for item in gap["terms"]}
    assert not any("smartcrew" in term for term in terms), terms
    assert not any(term in {"crew scheduling", "estimating", "made easy"} for term in terms), terms
    unstored = cp.content_gaps([{**rows[0], "url": None, "position": None}], {"https://a.example/1": a, "https://b.example/2": b})[0]
    assert unstored["checked"] is False and unstored["note"]


def test_one_bad_page_never_breaks_the_report():
    deep = '<script type="application/ld+json">' + "[" * 100000 + "]" * 100000 + "</script>"
    nested = '<script type="application/ld+json">' + '{"a":' * 1500 + "1" + "}" * 1500 + "</script>"
    for html in (deep, nested):
        profile = cp.page_profile("https://bad.example/", f"<html><head>{html}</head><body><h1>x</h1></body></html>")
        assert isinstance(profile["schema"], list)
    json.dumps(cp.competitor_profiles(_google(), SC, {"https://mitti.example/excavation": deep}), default=list)


def test_serp_features_in_the_tracking_shape_are_read():
    stored = {"searches": [
        {"search": "excavation software", "results": [], "features": {
            "related_searches": ["excavation contractor software", "excavation estimating software"],
            "people_also_ask": [{"question": "What is the best software for excavation contractors?", "snippet": "x", "url": "https://a.example/"}],
        }},
        {"search": "hcss alternative", "results": [], "features": {"related_searches": [], "people_also_ask": []}},
        {"search": "old", "related_searches": ["old shape"]},
    ]}
    features = dn.serp_extras(stored)
    assert features["excavation software"]["related"] == ["excavation contractor software", "excavation estimating software"]
    assert features["excavation software"]["paa"] == ["What is the best software for excavation contractors?"]
    assert "hcss alternative" not in features and features["old"]["related"] == ["old shape"]
    found = dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, serp_features=features)
    assert "excavation contractor software" in found.queries


def test_plugin_rows_use_the_shared_scale():
    pages = _smartcrew_pages()
    base = dn.discover_searches(pages, "SmartCrew", host=SC)
    assert "grading estimates" not in dn.discover_searches(pages, "SmartCrew", host=SC, extra=[{"q": "grading estimates", "weight": 2.0}])
    assert "grading estimates" in dn.discover_searches(pages, "SmartCrew", host=SC, extra=[{"q": "grading estimates", "weight": 2.5}])
    assert "grading estimates" in dn.discover_searches(pages, "SmartCrew", host=SC, extra=[{"q": "grading estimates"}])  # 3.0 by default
    row = next(item for item in dn.discover(pages, "SmartCrew", host=SC, extra=[{"q": "grading estimates", "source": "gsc", "impressions": 40}]).to_dict()["searches"] if item["q"] == "grading estimates")
    assert row["source"] == "gsc" and "impressions 40" in row["sources"][0]["detail"][0]
    assert dn.discover_searches(pages, "SmartCrew", host=SC, extra=[]) == base


def test_discover_with_plugins_keeps_rows_for_the_report(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(dn, "plugin_rows", lambda project, pages, ui: [{"q": "grading crew software", "weight": 4.0, "source": "gsc"}])
    project = SimpleNamespace(dir=tmp_path / "p", domain=SC)
    (tmp_path / "p").mkdir()
    found = dn.discover_with_plugins(_smartcrew_pages(), "SmartCrew", host=SC, project=project)
    assert "grading crew software" in found
    assert dn.load_rows(project) == [{"q": "grading crew software", "weight": 4.0, "source": "gsc", "detail": "weight 4"}]
    assert dn.discover_with_plugins(_smartcrew_pages(), "SmartCrew", host=SC) == found  # no project: nothing stored, same list


def test_general_phrases_and_filler_words_are_left_out():
    found = _titles(["Busybusy: Pricing, Features and Best Alternatives | Acme", "Best Software | Acme", "Best field service software | Acme"], "Acme", "acme.com")
    assert "best field service software" in found.queries
    assert not {"pricing features and best alternatives", "features and best alternatives", "best software"} & set(found.queries)
    assert any(item["reason"] == "too general" for item in found.dropped)


def test_profiles_take_titles_from_stored_pages_when_results_have_none():
    google = GoogleSnapshot("p", "", "import", [SearchResults("excavation software", [
        _result(1, "https://mitti.example/dirt-work-software"), _result(2, "https://mitti.example/excavation")], "import", "csv", None)])
    mitti = cp.competitor_profiles(google, SC, _docs())["competitors"][0]
    assert mitti["titles"]["count"] == 2 and mitti["titles"]["separator"] == "|"
    assert mitti["best_pages"][0]["title"].endswith("| Mitti")


def test_every_tracked_search_gets_an_intent_and_a_source():
    found = dn.discover(_smartcrew_pages(), "SmartCrew", host=SC)
    rows = {row["q"]: row for row in dn.describe(["excavation software", "best excavation software", "how to bid a grading job", "smartcrew login"], found, brands={"smartcrew"})}
    assert rows["excavation software"]["source"] in {"title", "h1", "slug"} and rows["excavation software"]["intent"] == "commercial"
    assert rows["best excavation software"]["sources"] and rows["best excavation software"]["source"] != "added"  # a near duplicate
    assert rows["how to bid a grading job"]["source"] == "added" and rows["how to bid a grading job"]["intent"] == "informational"
    assert rows["smartcrew login"]["intent"] == "navigational"


def test_report_sections_are_cached_until_their_inputs_change(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, serpapi_key="sk-test-discover")
    app = create_app(settings=settings, projects_root=tmp_path / "projects", public_demo=False,
                     pull_options={"serp_provider": FakeGoogle(), "fetcher": FakeCrawler(_site_pages()), "today": TODAY, "workers": 4})
    client = TestClient(app)
    slug = _lines(client.post("/api/v1/sites", json={"site": SITE}, headers={"x-ranksim-csrf": _token(client)}))[-1]["slug"]
    first = client.get(f"/api/v1/projects/{slug}/report").json()
    project = load_project(slug, root=tmp_path / "projects")
    assert (project.dir / "plugins" / "discover" / "sections.json").is_file()
    calls = []
    real = cp.competitor_profiles
    monkeypatch.setattr(cp, "competitor_profiles", lambda *a, **k: calls.append(1) or real(*a, **k))
    again = client.get(f"/api/v1/projects/{slug}/report").json()
    assert not calls and again["competitor_profiles"] == first["competitor_profiles"] and again["gaps"] == first["gaps"]
    dn.save_rows(project, [{"q": "grading crew software", "weight": 4.0, "source": "gsc"}])  # new input: computed again
    third = client.get(f"/api/v1/projects/{slug}/report").json()
    assert calls and any(item["q"] == "grading crew software" for item in third["discovery"]["searches"])


def test_gap_labels_drop_numbering_and_codes():
    body = "Link el4 here. Accounting integration matters. Accounting integration again. " * 6
    a = _page("", "A", "A", ["4. Which industries use it?"], body)
    b = _page("", "B", "B", ["2) Which industries use it"], body)
    gap = cp.gap_for("x software", None, None, [("https://a.example/", a), ("https://b.example/", b)])
    assert [item["topic"] for item in gap["topics"]] == ["Which industries use it"]
    terms = {item["term"] for item in gap["terms"]}
    assert "accounting integration" in terms and not any("el4" in term for term in terms)


def test_plugin_rows_cannot_pose_as_page_sources_or_be_multiplied():
    pages = _smartcrew_pages()
    posing = dn.discover(pages, "SmartCrew", host=SC, extra=[{"q": "grading crew app", "weight": 1.5, "source": "title"}])
    assert "grading crew app" not in posing.queries  # 1.5 alone is under 2.5, whatever it calls itself
    row = next(item for item in dn.discover(pages, "SmartCrew", host=SC, extra=[{"q": "grading crew app", "weight": 20, "source": "h1"}]).to_dict()["searches"] if item["q"] == "grading crew app")
    assert row["source"] == "plugin-h1" and row["score"] > 0
    plain = {item.q: item for item in dn.discover(pages, "SmartCrew", host=SC).searches}
    boosted = {item.q: item for item in dn.discover(pages, "SmartCrew", host=SC, extra=[{"q": "excavation software", "weight": 10, "source": "gsc"}]).searches}
    assert boosted["excavation software"].evidence == pytest.approx(plain["excavation software"].evidence + 10)


def test_near_duplicates_of_tracked_searches_are_not_suggested():
    assert dn.is_tracked("best excavation software", [], ["excavation software"])
    assert dn.is_tracked("x", ["excavation software"], ["excavation software"])
    assert not dn.is_tracked("excavation estimating software", [], ["excavation software"])


def test_rows_from_a_first_analysis_reach_the_new_project(tmp_path, monkeypatch):
    from types import SimpleNamespace

    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(dn, "plugin_rows", lambda project, pages, ui: [{"q": "grading crew software", "weight": 4.0, "source": "gsc", "impressions": 120}])
    found = dn.discover_with_plugins(_smartcrew_pages(), "SmartCrew", host="www." + SC, project=None, ui=SimpleNamespace(root=root))
    assert "grading crew software" in found
    project = SimpleNamespace(dir=root / "smartcrew", domain=SC)
    project.dir.mkdir()
    rows = dn.load_rows(project)
    assert rows == [{"q": "grading crew software", "weight": 4.0, "source": "gsc", "detail": "weight 4, impressions 120"}]
    assert not (root / "_plugins" / "discover" / "pending" / f"{SC}.json").exists()
    assert dn.load_rows(project) == rows  # now saved in the project
    detail = dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, extra=rows).to_dict()["searches"]
    assert any(item["q"] == "grading crew software" and "impressions 120" in item["sources"][0]["detail"][0] for item in detail)


def test_google_inputs_use_the_real_tracking_helper(tmp_path):
    from aeo.ranksim.plugins import tracking
    from aeo.ranksim.project import create_project

    project = create_project("SmartCrew", SC, ["excavation software"], root=tmp_path)
    snapshot = _google().to_dict()
    for entry in snapshot["searches"]:
        if entry["search"] == "excavation software":
            entry["features"] = {
                "related_searches": ["excavation contractor software"],
                "people_also_ask": [{"question": "What is the best software for excavation contractors?", "snippet": "x", "url": "https://a.example/"}],
            }
    folder = project.dir / "pulls" / "20261109T000000Z"
    folder.mkdir(parents=True)
    (folder / "google.json").write_text(json.dumps(snapshot), encoding="utf-8")
    assert "excavation software" in tracking.serp_features(project)
    google, features = dn.google_inputs(project)
    assert google is not None and features == {"excavation software": {
        "related": ["excavation contractor software"], "paa": ["What is the best software for excavation contractors?"]}}
    found = dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, google=google, serp_features=features)
    assert "excavation contractor software" in found.queries


def _contractor_page(name):
    return _page("", f"Excavation & Site Work Services | {name}", f"{name} Excavation", ["Our services", "Service area"],
                 "Family owned excavation contractor serving Cape Cod. Licensed and insured. Excavation services, septic systems and driveways. "
                 "Call us at (508) 555-1234 for a free estimate. Residential and commercial work. " * 5,
                 {"@type": "HomeAndConstructionBusiness"})


def _vendor_page(name):
    return _page("", f"Excavation Software | {name}", "Excavation software for contractors", ["Pricing", "Integrations"],
                 "Our cloud-based software and mobile app run dispatch and timecards. Book a demo or start a free trial. Plans start at $40 per user. " * 5,
                 {"@type": "SoftwareApplication"})


def test_offering_tells_software_vendors_from_service_businesses():
    assert cp.offering(["Excavation software for contractors. Book a demo. Mobile app. Per user pricing."])[0] == "software"
    assert cp.offering(["Family owned excavation contractor serving Cape Cod. Free estimate. Call (508) 555-1234."])[0] == "service"
    assert cp.offering(["Excavation"])[0] is None
    # software for contractors is not a contractor: audience words are not evidence
    assert cp.offering(["Dirt work software for excavation contractors and site work crews. Book a demo. Mobile app."])[0] == "software"


def test_a_local_contractor_in_a_software_sites_results_is_not_a_competitor():
    google = GoogleSnapshot("p", "", "import", [SearchResults("excavation software", [
        _result(1, "https://mitti.example/excavation", "Excavation Software | Mitti"),
        _result(2, "https://dirtworkscapecod.example/", "Dirt Works Cape Cod", ),
        _result(3, f"{SC_HOME}excavation-software/", "Excavation Software | SmartCrew"),
    ], "import", "csv", None)])
    docs = {"https://mitti.example/excavation": _vendor_page("Mitti"), "https://dirtworkscapecod.example/": _contractor_page("Dirt Works"),
            f"{SC_HOME}excavation-software/": _vendor_page("SmartCrew")}
    profiles = cp.competitor_profiles(google, SC, docs)
    assert [row["domain"] for row in profiles["competitors"]] == ["mitti.example"]
    service = next(row for row in profiles["platforms"] if row["domain"] == "dirtworkscapecod.example")
    assert service["kind"] == "service" and "local service business" in service["reason"]
    # with no stored pages and no snippets nothing is known, so the old answer stands
    assert "dirtworkscapecod.example" in cp.pick_competitors(google, SC)
    # Google snippets alone are enough when they are clear
    google.searches[0].results[1].snippet = "Family owned excavation contractor serving Cape Cod. Free estimates. Licensed and insured."
    google.searches[0].results[2].snippet = "SmartCrew is cloud-based software with a mobile app. Book a demo."
    assert "dirtworkscapecod.example" not in cp.pick_competitors(google, SC)
    # a contractor's site sees software vendors as software, not competitors
    rows = {row["domain"]: row for row in cp.classify_domains(google, SC, profiles={url: cp.page_profile(url, html) for url, html in docs.items()}, own_offer="service")}
    assert rows["mitti.example"]["kind"] == "software"
