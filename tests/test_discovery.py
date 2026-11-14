"""Search discovery, competitor discovery, competitor profiles and content gaps. Fakes only; no network."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from test_autopilot import _lines, _site_pages, _token
from test_project import SITE, TODAY, FakeCrawler

from aeo.ranksim import autopilot
from aeo.ranksim import competitor_profiles as cp
from aeo.ranksim import discovery_next as dn
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
    {"q": "dirt work management software", "weight": 1200, "source": "gsc"},
    {"q": "smartcrew login", "weight": 900, "source": "gsc"},
    {"q": "smart crew app", "weight": 400, "source": "gsc"},
    {"q": "heavy civil field management software", "weight": 35, "source": "gsc"},
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
    found = dn.discover(pages, "SmartCrew", host=SC, google=_google(), extras=EXTRAS, plugin_rows=GSC)
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
    found = dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, plugin_rows=rows)
    merged = [item for item in found.searches if dn.same_search(item.q, "all in one dirt work software")]
    assert len(merged) == 1
    assert "gsc" in merged[0].sources and merged[0].variants


def test_the_list_is_capped_and_the_rest_is_reported():
    rows = [{"q": f"excavation {word} software", "weight": 100, "source": "gsc"} for word in ("trench", "grading", "hauling", "paving", "utility", "demolition", "site", "pipe", "septic", "pond", "land", "road", "drainage", "fill", "stump", "rock", "concrete", "asphalt", "survey", "erosion", "foundation", "basement")]
    found = dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, plugin_rows=rows)
    assert len(found.searches) == dn.MAX_SEARCHES
    assert any(item["reason"] == "over the limit" for item in found.dropped)
    assert len(dn.discover(_smartcrew_pages(), "SmartCrew", host=SC, plugin_rows=rows, limit=5).searches) == 5


def test_discovery_degrades_without_google_or_extras():
    pages = _smartcrew_pages()
    plain = dn.discover(pages, "SmartCrew", host=SC)
    assert plain.queries and set(plain.to_dict()["source_counts"]) <= dn.OWN_SOURCES
    assert dn.serp_extras(None) == {} and dn.serp_extras({"searches": [{"search": "x", "results": []}]}) == {}
    stored = {"searches": [{"search": "a", "related_searches": [{"query": "b c"}], "people_also_ask": [{"question": "what is d?"}]}]}
    assert dn.serp_extras(stored) == {"a": {"related": ["b c"], "paa": ["what is d?"]}}
    assert dn.discover_searches(pages, "SmartCrew", host=SC) == plain.queries
    assert dn.discover([], "SmartCrew", host=SC).queries == []
    bad = dn.discover(pages, "SmartCrew", host=SC, plugin_rows=[{"q": "excavation pond software", "weight": "lots"}, {"weight": 3}])
    assert "excavation pond software" in bad.queries


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
    share = cp.share_rows(_google(), SC)
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
    sections = cp.report_sections(project, None, settings.ranksim_database_url, report["rankings"], pages=pages, plugin_rows=[{"q": "grading crew software", "weight": 10, "source": "gsc"}])
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
