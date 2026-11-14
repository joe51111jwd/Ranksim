"""The audit: one small fixture page per check, every selector checked against a real HTML parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from aeo.ranksim import audit as A

SITE = "https://www.shovel.example"
ROOT = Path(__file__).resolve().parents[1]
FILLER = " ".join(["Crews log hours, machines and loads from the field so the office sees job cost the same day."] * 30)


def page(head: str = "", body: str = "", *, title: str | None = "Dirt work software for excavation crews | Shovel", meta: str | None = None, extra_head: str = "") -> str:
    meta = meta if meta is not None else "Track crew hours, machines and haul tickets for dirt work jobs in one app, and see job cost against the bid the same day."
    parts = ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">']
    if title is not None:
        parts.append(f"<title>{title}</title>")
    if meta:
        parts.append(f'<meta name="description" content="{meta}">')
    parts += [
        '<link rel="canonical" href="{url}">',
        '<meta property="og:title" content="Shovel">',
        '<meta property="og:description" content="Shovel">',
        '<meta property="og:image" content="https://www.shovel.example/og.png">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage","name":"Shovel"}</script>',
    ]
    head_html = "\n".join(parts) + head + extra_head
    if not body:
        body = f'<main><h1>Dirt work software for excavation crews</h1><p>{FILLER}</p><p>See <a href="/pricing/">pricing for dirt work software</a>, <a href="/features/">crew time tracking features</a> and <a href="/compare/">how Shovel compares</a>.</p></main>'
    return f"<!doctype html><html lang=en><head>{head_html}</head><body>{body}</body></html>"


def run(pages: dict[str, str], **kwargs) -> list[dict]:
    fixed = {url: html.replace("{url}", url) for url, html in pages.items()}
    return A.audit_html(fixed, **kwargs)


def kinds(issues, url=None):
    return [issue["kind"] for issue in issues if url is None or issue.get("page") == url or url in (issue.get("pages") or [])]


def only(issues, kind):
    found = [issue for issue in issues if issue["kind"] == kind]
    assert found, f"no {kind} in {[issue['kind'] for issue in issues]}"
    return found


def assert_selectors_match(issues, pages):
    """Every element selector matches exactly one element, in lxml and in Python's own parser, and the element
    text the issue quotes is that element's text."""
    for issue in issues:
        element = issue.get("element")
        if not element:
            continue
        urls = issue.get("pages") or [issue["page"]]
        for url in urls:
            html = pages[url].replace("{url}", url)
            for parser in ("lxml", "html.parser"):
                soup = BeautifulSoup(html, parser)
                found = soup.select(element["selector"])
                assert len(found) == 1, (issue["kind"], element["selector"], parser, len(found))
                assert found[0].name == element["tag"], (issue["kind"], element)


GOOD = f"{SITE}/"


def test_a_clean_page_has_no_issues():
    issues = run({GOOD: page()})
    assert issues == [], [(issue["kind"], issue["title"]) for issue in issues]


# -- title -----------------------------------------------------------------------------------------


def test_title_missing_long_short_duplicate_and_search():
    pages = {
        f"{SITE}/a/": page(title=None),
        f"{SITE}/b/": page(title="Shovel is the all in one dirt work software for excavation, grading and utility contractors | Shovel"),
        f"{SITE}/c/": page(title="Shovel"),
        f"{SITE}/d/": page(title="Crew time tracking for excavation contractors | Shovel", body="<main><h1>Crew time tracking</h1><p>" + FILLER + "</p></main>"),
        f"{SITE}/e/": page(title="Crew time tracking for excavation contractors | Shovel", body="<main><h1>Machine maintenance logs</h1><p>" + FILLER + "</p></main>"),
    }
    rankings = [{"q": "haul ticket app", "position": None, "url": f"{SITE}/d/", "checked": False, "top": []}]
    issues = run(pages, rankings=rankings)
    missing = only(issues, "title-missing")[0]
    assert missing["page"] == f"{SITE}/a/" and missing["severity"] == "high"
    assert missing["suggestion"].startswith("<title>Dirt work software for excavation crews") and missing["action"] == "append"
    long = only(issues, "title-long")[0]
    assert long["page"] == f"{SITE}/b/" and str(len("Shovel is the all in one dirt work software for excavation, grading and utility contractors | Shovel")) in long["title"]
    assert long["element"]["selector"] == "head > title" and len(long["suggestion"]) <= A.TITLE_MAX
    short = only(issues, "title-short")[0]
    assert short["page"] == f"{SITE}/c/" and "Dirt work software for excavation crews" in short["suggestion"]
    dupes = only(issues, "title-duplicate")
    assert {issue["page"] for issue in dupes} == {f"{SITE}/d/", f"{SITE}/e/"}
    by_page = {issue["page"]: issue for issue in dupes}
    assert by_page[f"{SITE}/e/"]["suggestion"].startswith("Machine maintenance logs") and "/d/" in by_page[f"{SITE}/e/"]["comment"]
    search = only(issues, "title-search")[0]
    assert search["page"] == f"{SITE}/d/" and search["search"] == "haul ticket app"
    assert search["suggestion"].lower().startswith("haul ticket app") and len(search["suggestion"]) <= A.TITLE_MAX
    assert_selectors_match(issues, pages)


def test_suggestions_keep_the_casing_the_page_uses():
    body = "<main><h1>Switching from HCSS</h1><p>" + FILLER + " HCSS HeavyJob users move their crews in a day.</p></main>"
    pages = {f"{SITE}/x/": page(title="Switching tools | Shovel", body=body)}
    rankings = [{"q": "hcss heavyjob alternative", "position": None, "url": f"{SITE}/x/", "checked": False, "top": []}]
    issue = only(run(pages, rankings=rankings), "title-search")[0]
    assert issue["suggestion"].startswith("HCSS HeavyJob alternative")


# -- meta description ----------------------------------------------------------------------------


def test_meta_missing_long_short_and_duplicate():
    long_meta = "Track crew hours, machines and haul tickets for dirt work jobs in one app. " * 3
    shared = "One app for crews and the office, with time, machines and job cost in one place for dirt work."
    pages = {
        f"{SITE}/m1/": page(meta=""),
        f"{SITE}/m2/": page(meta=long_meta.strip()),
        f"{SITE}/m3/": page(meta="Dirt work app."),
        f"{SITE}/m4/": page(meta=shared, title="Machine logs for excavation contractors | Shovel"),
        f"{SITE}/m5/": page(meta=shared, title="Haul tickets for excavation contractors | Shovel"),
    }
    issues = run(pages)
    missing = only(issues, "meta-missing")[0]
    assert missing["page"] == f"{SITE}/m1/" and missing["action"] == "append" and missing["element"]["selector"] == "head"
    assert missing["suggestion"].startswith('<meta name="description" content="Crews log hours')
    long = only(issues, "meta-long")[0]
    assert long["element"] == {"selector": 'meta[name="description"]', "text": A.short(long_meta, 160), "tag": "meta", "attr": "content"}
    assert len(long["suggestion"]) <= A.META_TARGET and long["action"] == "attr"
    short = only(issues, "meta-short")[0]
    assert short["page"] == f"{SITE}/m3/" and short["suggestion"].startswith("Dirt work app. Crews log hours")
    assert {issue["page"] for issue in only(issues, "meta-duplicate")} == {f"{SITE}/m4/", f"{SITE}/m5/"}
    assert_selectors_match(issues, pages)


# -- headings ------------------------------------------------------------------------------------


def test_headings_missing_many_skipped_and_missing_the_search():
    pages = {
        f"{SITE}/h1/": page(body=f"<main><p>{FILLER}</p></main>"),
        f"{SITE}/h2/": page(body=f"<main><h1>Dirt work software</h1><section><h1>Second main heading</h1><p>{FILLER}</p></section></main>"),
        f"{SITE}/h3/": page(body=f"<main><h1>Dirt work software for excavation crews</h1><h2>Time</h2><h4>GPS clock in</h4><p>{FILLER}</p></main>"),
        f"{SITE}/h4/": page(body=f"<main><h1>Welcome</h1><p>{FILLER}</p></main>", title="Haul ticket app for dirt work | Shovel"),
    }
    rankings = [{"q": "haul ticket app", "position": None, "url": f"{SITE}/h4/", "checked": False, "top": []}]
    issues = run(pages, rankings=rankings)
    missing = only(issues, "h1-missing")[0]
    assert missing["page"] == f"{SITE}/h1/" and missing["suggestion"] == "<h1>Dirt work software for excavation crews</h1>" and missing["action"] == "prepend"
    many = only(issues, "h1-many")[0]
    assert many["element"]["text"] == "Second main heading" and many["suggestion"] == "h2" and many["action"] == "retag"
    skip = only(issues, "heading-skip")[0]
    assert skip["element"]["text"] == "GPS clock in" and skip["suggestion"] == "h3"
    search = only(issues, "h1-search")[0]
    assert search["page"] == f"{SITE}/h4/" and search["suggestion"] == "Haul ticket app: Welcome"
    assert_selectors_match(issues, pages)


# -- content -------------------------------------------------------------------------------------


def _row(q, url, position, top_urls, checked=True):
    return {"q": q, "position": position, "url": url, "checked": checked, "top": [{"position": i + 1, "url": u, "domain": A.host_of(u), "you": u.startswith(SITE)} for i, u in enumerate(top_urls)]}


def _rival(words: int, *, faq: bool = False, table: bool = False, types: tuple = ()) -> str:
    text = " ".join(["Haul tickets track every load from pit to site with weights and times."] * (words // 12))
    blocks = "".join(f'<script type="application/ld+json">{{"@context":"https://schema.org","@type":"{kind}"}}</script>' for kind in types)
    extra = ("<h2>FAQ</h2><p>Answers.</p>" if faq else "") + ("<table><tr><td>a</td></tr></table>" if table else "")
    return f"<html><head><title>Haul ticket app</title>{blocks}</head><body><main><h1>Haul ticket app</h1><p>{text}</p>{extra}</main></body></html>"


RIVALS = [f"https://rival{i}.example/haul/" for i in range(1, 4)]


def test_thin_thinner_than_top_three_and_no_section_for_the_search():
    pages = {
        f"{SITE}/thin/": page(body="<main><h1>Haul ticket app</h1><p>Short page about haul tickets.</p></main>", title="Haul ticket app for dirt work | Shovel"),
        f"{SITE}/mid/": page(body=f"<main><h1>Dirt work software for excavation crews</h1><h2>Machines</h2><p>{FILLER}</p></main>"),
    }
    docs = {url: _rival(1500) for url in RIVALS}
    rankings = [_row("haul ticket app", f"{SITE}/thin/", None, RIVALS), _row("electronic haul ticket software", f"{SITE}/mid/", 6, RIVALS + [f"{SITE}/mid/"])]
    issues = run(pages, rankings=rankings, docs=docs)
    thin = [issue for issue in only(issues, "thin") if issue["page"] == f"{SITE}/thin/"]
    assert thin and thin[0]["element"]["selector"] == "body > main"
    versus = only(issues, "thin-vs-top")
    assert {issue["page"] for issue in versus} == {f"{SITE}/thin/", f"{SITE}/mid/"}
    assert "1,488" in versus[0]["comment"] or "1,500" in versus[0]["comment"] or "median" in versus[0]["comment"]
    answer = only(issues, "no-answer")[0]
    assert answer["page"] == f"{SITE}/mid/" and answer["search"] == "electronic haul ticket software"
    assert answer["suggestion"] == "<h2>Electronic haul ticket software</h2>" and answer["action"] == "append"
    assert_selectors_match(issues, pages)


# -- links ---------------------------------------------------------------------------------------


def test_few_links_broken_links_and_generic_anchor_text():
    body = (
        f"<header><nav><a href='/'>Home</a><a href='/pricing/'>Pricing</a></nav></header><main><h1>Dirt work software</h1><p>{FILLER}</p>"
        "<p>Read the <a href='/haul-ticket-app/'>click here</a> guide or the <a href='/old-page/'>old guide</a>.</p></main>"
    )
    target = page(title="Haul ticket app for dirt work | Shovel", body=f"<main><h1>Haul ticket app for dirt work</h1><p>{FILLER}</p></main>")
    pages = {f"{SITE}/links/": page(body=body), f"{SITE}/haul-ticket-app/": target}
    issues = run(pages, statuses={f"{SITE}/old-page/": 404})
    few = [issue for issue in only(issues, "few-links") if issue["page"] == f"{SITE}/links/"][0]
    assert few["page"] == f"{SITE}/links/" and "2 of your other pages" in few["title"]
    broken = only(issues, "broken-link")[0]
    assert broken["element"]["text"] == "old guide" and broken["element"]["attr"] == "href" and "404" in broken["comment"]
    generic = only(issues, "link-generic")[0]
    assert generic["element"]["text"] == "click here" and generic["suggestion"] == "Haul ticket app for dirt work"
    assert_selectors_match(issues, pages)


def test_a_failed_crawl_marks_links_to_it_broken():
    pages = {f"{SITE}/": page(body=f"<main><h1>Dirt work software</h1><p>{FILLER}</p><p><a href='/gone/'>Gone page link</a> <a href='/a/'>a page</a> <a href='/b/'>b page</a> <a href='/c/'>c page</a></p></main>")}
    docs = [A.PageDoc(url, html.replace("{url}", url)) for url, html in pages.items()] + [A.PageDoc(f"{SITE}/gone/", "", status="error", error="HTTP 500", http_status=500)]
    issues = A.audit_html(docs)
    assert "broken-link" in kinds(issues, f"{SITE}/") and "unreadable" in kinds(issues, f"{SITE}/gone/")


# -- images --------------------------------------------------------------------------------------


def test_images_without_alt_and_huge_images_without_size():
    body = (
        f"<main><h1>Dirt work software</h1><p>{FILLER}</p>"
        '<img src="/img/excavator-crew-dashboard.png">'
        '<img src="/img/IMG_2041.jpg">'
        '<img src="/img/site-photo-4000x3000.jpg" alt="Grading crew on site">'
        '<img src="/img/logo.svg" alt="">'
        '<img src="/pixel.gif" width="1" height="1">'
        "</main>"
    )
    pages = {f"{SITE}/img/": page(body=body)}
    issues = run(pages)
    alts = only(issues, "img-alt")
    assert len(alts) == 2
    named = next(issue for issue in alts if "excavator" in issue["element"]["text"])
    assert named["suggestion"] == "Excavator crew dashboard" and named["element"]["attr"] == "alt" and named["action"] == "attr"
    assert next(issue for issue in alts if "IMG_2041" in issue["element"]["text"]).get("suggestion") is None
    huge = only(issues, "img-huge")[0]
    assert "4,000 pixels wide" in huge["comment"] and huge["element"]["text"] == "Grading crew on site"
    assert_selectors_match(issues, pages)


def test_image_size_from_the_crawl_counts():
    url = f"{SITE}/heavy/"
    doc = A.PageDoc(url, page(body=f'<main><h1>Dirt work software</h1><p>{FILLER}</p><img src="/hero.jpg" alt="Crew"></main>').replace("{url}", url), image_bytes={f"{SITE}/hero.jpg": 900_000})
    issue = only(A.audit_html([doc]), "img-huge")[0]
    assert "900 KB" in issue["comment"]


# -- indexability --------------------------------------------------------------------------------


def test_noindex_canonical_elsewhere_robots_blocked_and_errors():
    pages = {
        f"{SITE}/noindex/": page(extra_head='<meta name="robots" content="noindex, follow">'),
        f"{SITE}/canon/": page().replace('<link rel="canonical" href="{url}">', f'<link rel="canonical" href="{SITE}/other/">'),
        f"{SITE}/private/": page(),
    }
    docs = [A.PageDoc(url, html.replace("{url}", url)) for url, html in pages.items()]
    docs.append(A.PageDoc(f"{SITE}/down/", "", status="error", error="timeout"))
    docs.append(A.PageDoc(f"{SITE}/missing/", "<html><body>Not found</body></html>", http_status=404))
    docs.append(A.PageDoc(f"{SITE}/header/", pages[f"{SITE}/private/"].replace("{url}", f"{SITE}/header/"), headers={"X-Robots-Tag": "noindex"}))
    issues = A.audit_html(docs, robots_txt="User-agent: *\nDisallow: /private/\n")
    noindex = [issue for issue in only(issues, "noindex") if issue["page"] == f"{SITE}/noindex/"][0]
    assert noindex["element"] == {"selector": 'meta[name="robots"]', "text": "noindex, follow", "tag": "meta", "attr": "content"}
    assert noindex["suggestion"] == "index, follow" and noindex["severity"] == "high"
    assert any(issue["page"] == f"{SITE}/header/" for issue in only(issues, "noindex"))
    canon = only(issues, "canonical-other")[0]
    assert canon["page"] == f"{SITE}/canon/" and canon["suggestion"] == f"{SITE}/canon/" and canon["element"]["attr"] == "href"
    blocked = only(issues, "robots-blocked")[0]
    assert blocked["page"] == f"{SITE}/private/" and "Disallow: /private/" in blocked["evidence"]
    unreadable = {issue["page"] for issue in only(issues, "unreadable")}
    assert {f"{SITE}/down/", f"{SITE}/missing/"} <= unreadable
    assert "HTTP 404" in next(issue for issue in issues if issue["page"] == f"{SITE}/missing/")["comment"]
    assert set(kinds(issues, f"{SITE}/private/")) == {"robots-blocked"}  # nothing else is said about a page Google can't read
    for issue in issues:
        if issue.get("element"):
            html = next(doc.html for doc in docs if doc.url == issue["page"])
            assert len(BeautifulSoup(html, "lxml").select(issue["element"]["selector"])) == 1


def test_canonical_missing():
    pages = {f"{SITE}/nocanon/": page().replace('<link rel="canonical" href="{url}">', "")}
    issue = only(run(pages), "canonical-missing")[0]
    assert issue["suggestion"] == f'<link rel="canonical" href="{SITE}/nocanon/">' and issue["action"] == "append"


# -- structured data -----------------------------------------------------------------------------


def test_structured_data_missing_invalid_and_gaps_against_the_top_pages():
    no_schema = page().replace('<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage","name":"Shovel"}</script>', "")
    broken = page(extra_head='<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Shovel",}</script>')
    faq_body = (
        f"<main><h1>Haul ticket app</h1><p>{FILLER}</p><h2>How do drivers log a load?</h2><p>They tap the load on the phone and the weight is saved.</p>"
        "<h2>Does it work offline?</h2><p>Yes. Loads sync when the phone is back online.</p></main>"
    )
    pages = {
        f"{SITE}/": no_schema,
        f"{SITE}/broken/": broken,
        f"{SITE}/haul/": page(body=faq_body, title="Haul ticket app for dirt work | Shovel"),
    }
    docs = {url: _rival(400, types=("FAQPage", "Organization")) for url in RIVALS}
    rankings = [_row("haul ticket app", f"{SITE}/haul/", 5, RIVALS + [f"{SITE}/haul/"])]
    issues = run(pages, rankings=rankings, docs=docs, brand="Shovel")
    missing = only(issues, "schema-missing")[0]
    assert missing["page"] == f"{SITE}/" and '"Organization"' in missing["suggestion"] and '"name": "Shovel"' in missing["suggestion"]
    invalid = only(issues, "schema-invalid")[0]
    assert invalid["page"] == f"{SITE}/broken/" and json.loads(invalid["suggestion"])["@type"] == "Organization"
    gaps = {issue["title"]: issue for issue in only(issues, "schema-gap")}
    faq = next(issue for title, issue in gaps.items() if "FAQPage" in title)
    data = json.loads(faq["suggestion"].removeprefix('<script type="application/ld+json">').removesuffix("</script>"))
    assert [item["name"] for item in data["mainEntity"]] == ["How do drivers log a load?", "Does it work offline?"]
    assert data["mainEntity"][1]["acceptedAnswer"]["text"].startswith("Yes. Loads sync")
    assert any("Organization" in title for title in gaps)
    assert_selectors_match(issues, pages)


def test_no_faq_suggestion_without_questions_on_the_page():
    pages = {f"{SITE}/haul/": page(title="Haul ticket app for dirt work | Shovel", body=f"<main><h1>Haul ticket app</h1><p>{FILLER}</p></main>")}
    docs = {url: _rival(400, types=("FAQPage",)) for url in RIVALS}
    issues = run(pages, rankings=[_row("haul ticket app", f"{SITE}/haul/", 5, RIVALS + [f"{SITE}/haul/"])], docs=docs)
    gap = only(issues, "schema-gap")[0]
    assert "suggestion" not in gap and "Write the questions and answers on the page first" in gap["fix"]


# -- social, mobile, URL -------------------------------------------------------------------------


def test_open_graph_twitter_and_viewport():
    html = page(body=f'<main><h1>Dirt work software</h1><p>{FILLER}</p><img src="/shot.png" alt="Dashboard"></main>')
    for tag in ('<meta property="og:title" content="Shovel">', '<meta property="og:image" content="https://www.shovel.example/og.png">', '<meta name="twitter:card" content="summary_large_image">', '<meta name="viewport" content="width=device-width, initial-scale=1">'):
        html = html.replace(tag, "")
    pages = {f"{SITE}/social/": html, f"{SITE}/fixed/": page().replace("width=device-width, initial-scale=1", "width=1024")}
    issues = run(pages)
    og = only(issues, "og-missing")[0]
    assert "og:title" in og["title"] and "og:image" in og["title"] and "og:description" not in og["title"]
    assert f'<meta property="og:image" content="{SITE}/shot.png">' in og["suggestion"] and og["action"] == "append"
    twitter = only(issues, "twitter-missing")[0]
    assert twitter["suggestion"] == '<meta name="twitter:card" content="summary">'
    viewport = [issue for issue in only(issues, "viewport-missing") if issue["page"] == f"{SITE}/social/"][0]
    assert viewport["severity"] == "medium" and "width=device-width" in viewport["suggestion"]
    fixed = [issue for issue in only(issues, "viewport-missing") if issue["page"] == f"{SITE}/fixed/"][0]
    assert fixed["severity"] == "low" and fixed["element"]["attr"] == "content"
    assert_selectors_match(issues, pages)


def test_url_hygiene():
    url = f"{SITE}/Blog/Dirt_Work_Software/?ref=nav"
    issue = only(run({url: page()}), "url-hygiene")[0]
    assert "capital letters" in issue["comment"] and "underscores" in issue["comment"] and "a query string" in issue["comment"]
    assert issue["suggestion"] == f"{SITE}/blog/dirt-work-software/"


# -- pages drawn by scripts ----------------------------------------------------------------------


def test_a_page_drawn_by_scripts_is_flagged_and_points_at_the_hidden_copy():
    body = (
        '<div id="root"><article class="crawl-boot"><h1>Dirt work software for excavation crews</h1><p>' + FILLER[:900] + "</p></article></div>"
        '<script src="/assets/app.js"></script>'
    )
    head = "<style>.crawl-boot{clip:rect(0, 0, 0, 0);white-space:nowrap;width:1px;height:1px;position:absolute;overflow:hidden}</style>"
    pages = {f"{SITE}/": page(head=head, body=body)}
    issues = run(pages)
    issue = only(issues, "script-built")[0]
    assert issue["severity"] == "high" and issue["element"]["tag"] == "article"
    assert "hidden article.crawl-boot block" in issue["comment"] and "0 words" in issue["comment"]
    assert_selectors_match(issues, pages)


def test_an_empty_app_shell_is_flagged():
    pages = {f"{SITE}/": page(body='<div id="app"></div><script src="/bundle.js"></script>')}
    issue = only(run(pages), "script-built")[0]
    assert "only 0 words" in issue["comment"]


def test_smartcrews_homepage_is_drawn_by_scripts():
    path = ROOT / "smartcrew" / "pr8" / "home.html"
    if not path.exists():
        pytest.skip("SmartCrew snapshot not present")
    html = path.read_text()
    url = "https://www.smartcrewhq.com/"
    issues = A.audit_html({url: html})
    issue = only(issues, "script-built")[0]
    assert issue["page"] == url and "crawl-boot" in issue["comment"]
    element = BeautifulSoup(html, "lxml").select(issue["element"]["selector"])
    assert len(element) == 1 and "crawl-boot" in element[0].get("class", [])
    assert issues[0]["kind"] == "script-built"  # the most urgent issue on that page


def test_a_normal_page_with_a_screen_reader_label_is_not_script_built():
    body = f'<main><h1>Dirt work software</h1><span class="sr-only">Skip to content</span><p>{FILLER}</p></main><script src="/app.js"></script>'
    assert "script-built" not in kinds(run({f"{SITE}/": page(body=body)}))


# -- rankings ------------------------------------------------------------------------------------


def test_ranking_issues_per_search():
    pages = {
        f"{SITE}/haul/": page(title="Haul ticket app for dirt work | Shovel", body=f"<main><h1>Haul ticket app</h1><h2>Haul ticket app basics</h2><p>{FILLER}</p></main>"),
        f"{SITE}/crew/": page(title="Crew app | Shovel", body=f"<main><h1>Crew app</h1><h2>Crew time</h2><p>{FILLER}</p></main>"),
    }
    docs = {url: _rival(1800, table=True, faq=True) for url in RIVALS}
    rankings = [
        _row("haul ticket app", f"{SITE}/haul/", None, RIVALS),
        _row("dirt work estimating software", None, None, RIVALS),
        _row("crew time tracking app", f"{SITE}/crew/", 6, RIVALS + [f"{SITE}/crew/"]),
        _row("unchecked search", None, None, [], checked=False),
    ]
    issues = run(pages, rankings=rankings, docs=docs)
    not_ranking = only(issues, "not-ranking")[0]
    assert not_ranking["search"] == "haul ticket app" and "rival1.example" in not_ranking["comment"] and "1,956 words" in not_ranking["comment"]
    no_page = only(issues, "no-page")[0]
    assert no_page["search"] == "dirt work estimating software" and no_page.get("page") is None
    behind = only(issues, "behind")[0]
    assert behind["title"] == '#6 for "crew time tracking app"' and "have a table" in behind["comment"] and "FAQ" in behind["comment"]
    assert 'missing "time tracking"' in behind["comment"] and behind["element"]["selector"] == "head > title"
    assert behind["suggestion"].startswith("Crew time tracking app")
    assert not any(issue.get("search") == "unchecked search" for issue in issues)


# -- priority, duplicates, plugins ---------------------------------------------------------------


def test_severity_order_scores_and_no_duplicates():
    pages = {
        f"{SITE}/": page(title=None, meta=""),
        f"{SITE}/b/": page(title="Shovel"),
        f"{SITE}/c/": page(body="<main><p>tiny</p></main>"),
    }
    issues = run(pages)
    order = [A.SEVERITY_ORDER[issue["severity"]] for issue in issues]
    assert order == sorted(order)
    for severity in ("high", "medium", "low"):
        scores = [issue["score"] for issue in issues if issue["severity"] == severity]
        assert scores == sorted(scores, reverse=True)
    ids = [issue["id"] for issue in issues]
    assert len(ids) == len(set(ids))
    keys = [(issue["kind"], issue.get("page"), issue.get("search"), (issue.get("element") or {}).get("selector"), issue["title"]) for issue in issues]
    assert len(keys) == len(set(keys))
    for issue in issues:
        assert set(issue) >= {"id", "severity", "kind", "title", "comment", "fix", "page", "impact", "effort", "score"}
        assert issue["comment"] and issue["fix"]
        assert "—" not in issue["comment"] + issue["fix"] + issue["title"] and "–" not in issue["comment"] + issue["fix"] + issue["title"]


def test_the_same_template_element_on_many_pages_is_one_issue():
    body = f'<main><h1>Dirt work software</h1><p>{FILLER}</p><a href="/a/">a page</a><a href="/b/">b page</a><a href="/c/">c page</a></main><footer><a href="/blog/">Read more</a></footer>'
    pages = {f"{SITE}/{name}/": page(body=body, title=f"{name.title()} for dirt work contractors | Shovel") for name in ("one", "two", "three")}
    issues = run(pages)
    generic = only(issues, "link-generic")
    assert len(generic) == 1 and generic[0]["count"] == 3 and len(generic[0]["pages"]) == 3
    assert "3 pages" in generic[0]["comment"]
    grouped = A.group_issues(issues)
    assert {group["page"] for group in grouped["pages"]} >= set(pages)


def test_caps_many_repeats_on_one_page():
    body = f"<main><h1>Dirt work software</h1><p>{FILLER}</p>" + "".join(f'<img src="/i/shot-{i}.png">' for i in range(14)) + "</main>"
    issues = run({f"{SITE}/many/": page(body=body)})
    alts = only(issues, "img-alt")
    assert len(alts) == A.PER_PAGE_CAP and any("4 more images" in issue["comment"] for issue in alts)


def test_registry_and_plugin_hook(monkeypatch):
    def custom(ctx):
        return [A.make_issue("custom-check", "Custom", "A plugin said so.", page=ctx.pages[0].url, fix="Do it.", severity="high")]

    def broken(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(A, "plugin_checks", lambda: [custom, broken])
    issues = run({GOOD: page()})
    assert [issue["kind"] for issue in issues] == ["custom-check"]
    assert custom not in A.CHECKS and A.check_titles in A.CHECKS


def test_group_by_search_and_summary():
    pages = {f"{SITE}/haul/": page(title="Shovel", body=f"<main><h1>Welcome</h1><p>{FILLER}</p></main>")}
    issues = run(pages, rankings=[{"q": "haul ticket app", "position": None, "url": f"{SITE}/haul/", "checked": False, "top": []}])
    grouped = A.group_issues(issues)
    assert grouped["searches"][0]["search"] == "haul ticket app" and len(grouped["searches"][0]["ids"]) >= 2
    summary = A.summarize(issues)
    assert summary["total"] == len(issues) and summary["kinds"]["title-search"] == 1


def test_selectors_survive_tables_ids_and_odd_markup():
    body = (
        f'<main><h1>Dirt work software</h1><p>{FILLER}</p><table><tr><td>a</td></tr><tr><td><a href="/x/">click here</a></td></tr></table>'
        '<div id="S:1"><p><a href="/y/">here</a></p></div><div id="cta"><a href="/z/">learn more</a></div></main>'
    )
    pages = {f"{SITE}/odd/": page(body=body)}
    issues = run(pages)
    generic = only(issues, "link-generic")
    assert len(generic) == 3
    assert any(issue["element"]["selector"].startswith("div#cta") for issue in generic)
    assert_selectors_match(issues, pages)
    soup = BeautifulSoup(pages[f"{SITE}/odd/"].replace("<table>", "<table><tbody>").replace("</table>", "</tbody></table>"), "lxml")
    row = next(issue for issue in generic if " tr" in issue["element"]["selector"])
    assert len(soup.select(row["element"]["selector"])) == 1  # still matches after a browser adds tbody


# -- cases from the adversarial review -----------------------------------------------------------


def test_svg_titles_are_not_the_page_title():
    icon = '<svg><title>Menu</title><path d="M0 0"/></svg>'
    pages = {f"{SITE}/svg/": page(body=f"<header>{icon}</header><main><h1>Dirt work software</h1><p>{FILLER}</p></main>"), f"{SITE}/notitle/": page(title=None, body=f"<header>{icon}</header><main><h1>Dirt work</h1><p>{FILLER}</p></main>")}
    issues = run(pages)
    assert "title-short" not in kinds(issues)
    assert "title-missing" in kinds(issues, f"{SITE}/notitle/")
    assert_selectors_match(issues, pages)


def test_rows_in_thead_tbody_and_nested_tables_get_unique_selectors():
    body = (
        f"<main><h1>Dirt work software</h1><p>{FILLER}</p>"
        "<table><thead><tr><th>h</th></tr></thead><tr><td><a href='/x/'>click here</a></td></tr></table>"
        "<table><tr><td>a</td></tr><tr><td><table><tr><td>i</td></tr><tr><td><a href='/y/'>read more</a></td></tr></table><a href='/z/'>here</a></td></tr></table></main>"
    )
    pages = {f"{SITE}/tables/": page(body=body)}
    issues = run(pages)
    generic = only(issues, "link-generic")
    assert len(generic) == 3 and all(issue.get("element") for issue in generic)
    assert_selectors_match(issues, pages)
    browserish = BeautifulSoup(pages[f"{SITE}/tables/"].replace("{url}", f"{SITE}/tables/"), "lxml")
    for table in browserish.find_all("table"):  # what a browser builds: loose rows wrapped in tbody
        loose = [row for row in table.find_all("tr", recursive=False)]
        if loose:
            wrapper = browserish.new_tag("tbody")
            loose[0].insert_before(wrapper)
            for row in loose:
                wrapper.append(row.extract())
    for issue in generic:
        found = browserish.select(issue["element"]["selector"])
        assert len(found) == 1 and found[0].name == "a", issue["element"]["selector"]


def test_markup_a_browser_rebuilds_is_not_pinned():
    body = (
        f"<main><h1>Dirt work software</h1><p>{FILLER}</p>"
        "<div><table><a href='/f/'>click here</a><tr><td>x</td></tr></table></div>"
        "<p><o:p><a href='/h/'>here</a></o:p></p></main>"
    )
    pages = {f"{SITE}/odd/": page(body=body)}
    issues = run(pages)
    for issue in only(issues, "link-generic"):
        assert "element" not in issue, issue["element"]  # listed, but not pinned on a guess


def test_canonical_rel_with_spaces_uses_a_word_match():
    pages = {f"{SITE}/rel/": page().replace('<link rel="canonical" href="{url}">', f'<link rel=" canonical " href="{SITE}/other/">')}
    issue = only(run(pages), "canonical-other")[0]
    assert issue["element"]["selector"] == 'link[rel~="canonical"]'


def test_a_malformed_link_does_not_hide_other_pages_issues():
    pages = {
        f"{SITE}/bad/": page(body=f'<main><h1>Dirt work software</h1><p>{FILLER}</p><a href="http://[bad">click here</a><img src="http://[bad/x.png"></main>'),
        f"{SITE}/ok/": page(body=f'<main><h1>Dirt work software</h1><p>{FILLER}</p><a href="/a/">click here</a></main>'),
    }
    issues = run(pages)
    assert "link-generic" in kinds(issues, f"{SITE}/ok/") and "img-alt" in kinds(issues, f"{SITE}/bad/")


def test_short_pages_without_scripts_or_server_rendered_apps_are_not_script_built():
    modal = '<div style="display:none"><p>' + FILLER[:300] + "</p></div>"
    pages = {
        f"{SITE}/contact/": page(body=f"<main><h1>Contact</h1><p>Call us any day of the week.</p>{modal}</main>"),
        f"{SITE}/ssr/": page(body='<div id="__next"><main><h1>Dirt work</h1><p>' + " ".join(["word"] * 30) + '</p></main></div><script src="/_next/app.js"></script>'),
        f"{SITE}/tw/": page(body='<main><h1>Dirt work</h1><div class="hidden md:block"><p>' + FILLER + "</p></div></main>"),
    }
    assert "script-built" not in kinds(run(pages))


def test_responsive_images_are_not_called_huge():
    body = f'<main><h1>Dirt work software</h1><p>{FILLER}</p><img alt="Dashboard" src="/d-2880.png" srcset="/d-960.png 960w, /d-2880.png 2880w" sizes="(max-width:760px) 260px, 420px"></main>'
    assert "img-huge" not in kinds(run({f"{SITE}/r/": page(body=body)}))


def test_bad_plugin_issues_are_dropped_or_coerced(monkeypatch):
    def sloppy(ctx):
        url = ctx.pages[0].url
        return [
            {"severity": "low", "kind": "p1", "title": 5, "page": url},  # not a string title: dropped
            {"severity": "low", "kind": "p2", "title": "Element is a string", "element": "body", "page": url},
            {"severity": "low", "kind": "p3", "title": "Page is a list", "page": [url]},
            {"severity": "low", "kind": "p4", "title": "Impact is text", "impact": "5", "page": url},
            {"kind": "p5", "title": "No severity"},  # dropped
            "not an issue",
            {"severity": "low", "title": "no kind"},
        ]

    monkeypatch.setattr(A, "plugin_checks", lambda: [sloppy])
    issues = run({GOOD: page()})
    assert sorted(issue["kind"] for issue in issues) == ["p2", "p3", "p4"]
    assert all(isinstance(issue["title"], str) and "element" not in issue for issue in issues)
    assert next(issue for issue in issues if issue["kind"] == "p3")["page"] is None
    assert next(issue for issue in issues if issue["kind"] == "p4")["impact"] == 5


def test_finalize_twice_keeps_merged_pages():
    body = f'<main><h1>Dirt work software</h1><p>{FILLER}</p><a href="/a/">a page</a><a href="/b/">b page</a><a href="/c/">c page</a></main><footer><a href="/blog/">Read more</a></footer>'
    pages = {f"{SITE}/{name}/": page(body=body, title=f"{name.title()} for dirt work contractors | Shovel") for name in ("one", "two", "three")}
    once = run(pages)
    twice = A.finalize([dict(issue) for issue in once])
    generic = only(twice, "link-generic")[0]
    assert generic["count"] == 3 and generic["comment"].count("3 pages") == 1
    assert [issue["id"] for issue in once] == [issue["id"] for issue in twice]


def test_a_page_without_a_head_still_gets_head_checks():
    url = f"{SITE}/nohead/"
    issues = A.audit_html({url: f"<h1>Dirt work software</h1><p>{FILLER}</p>"})
    assert {"title-missing", "meta-missing", "viewport-missing", "og-missing", "schema-missing"} <= set(kinds(issues, url))


# -- through the API and the app shell -------------------------------------------------------------


def test_the_report_pins_issues_on_the_stored_html(tmp_path):
    from fastapi.testclient import TestClient
    from test_autopilot import HOME, _lines, _site_pages, _token
    from test_project import FakeCrawler
    from test_serve_project import make_settings

    from aeo.ranksim.serve import create_app

    stored = _site_pages()
    app = create_app(settings=make_settings(tmp_path), projects_root=tmp_path / "projects", public_demo=False, pull_options={"fetcher": FakeCrawler(stored), "today": "2026-11-14"})
    client = TestClient(app)
    token = _token(client)
    slug = _lines(client.post("/api/v1/sites", json={"site": HOME}, headers={"x-ranksim-csrf": token}))[-1]["slug"]
    issues = client.get(f"/api/v1/projects/{slug}/report").json()["issues"]
    pinned = [issue for issue in issues if issue.get("element")]
    assert pinned and any(issue["kind"] == "title-long" and issue["element"]["selector"] == "head > title" for issue in issues)
    assert any(issue["kind"] == "meta-missing" and issue["element"]["selector"] == "head" for issue in pinned)
    for issue in pinned:  # every pin matches one element of the page as it was stored
        found = BeautifulSoup(stored[issue["page"]], "lxml").select(issue["element"]["selector"])
        assert len(found) == 1, (issue["kind"], issue["element"])
    assert len({issue["id"] for issue in issues}) == len(issues)
    again = client.get(f"/api/v1/projects/{slug}/report").json()["issues"]
    assert again == issues  # the cached run gives the same answer


def test_the_shell_loads_the_annotations_plugin_and_its_styles(tmp_path):
    from fastapi.testclient import TestClient
    from test_serve_project import make_settings

    from aeo.ranksim.serve import create_app

    client = TestClient(create_app(settings=make_settings(tmp_path), projects_root=tmp_path / "projects", public_demo=False))
    shell = client.get("/").text
    assert "/studio/js/plugins/annotations.js" in shell and "/studio/css/plugins/annotations.css" in shell
    for path in ("/studio/js/plugins/annotations.js", "/studio/js/screens/issues.js", "/studio/css/plugins/annotations.css", "/studio/css/issues.css"):
        body = client.get(path)
        assert body.status_code == 200, path
        assert "—" not in body.text and "–" not in body.text, path
    script = client.get("/studio/js/plugins/annotations.js").text
    assert "data-rs" in script and "innerHTML" not in script.replace("box.innerHTML = html", "")  # only the sandboxed template


def test_the_audit_ui_in_a_real_browser(tmp_path):
    """Fix the top issues, then the Publish sheet's publish-fixes list, in Chromium against the e2e fake server.
    Skips when node or Playwright is missing (CI runs Python only)."""
    import os
    import shutil
    import subprocess
    import sys

    e2e = ROOT / "tests" / "e2e"
    node = shutil.which("node")
    if os.environ.get("RANKSIM_SKIP_E2E", "").strip() in {"1", "true", "yes"} or not node:
        pytest.skip("node is not installed or RANKSIM_SKIP_E2E is set")
    if subprocess.run([node, "-e", "require('playwright')"], cwd=e2e, capture_output=True, timeout=60).returncode != 0:
        pytest.skip("Playwright is not installed")
    sys.path.insert(0, str(e2e))
    try:
        from test_e2e import _start
    finally:
        sys.path.remove(str(e2e))
    env = {key: value for key, value in os.environ.items() if not key.endswith(("_API_KEY", "_KEY", "_PASSWORD", "_LOGIN", "_TOKEN", "_SECRET", "_CLIENT_ID"))}
    servers: list = []
    try:
        base = _start(servers, tmp_path / "data", {**env, "E2E_SLOW_SECONDS": "0"})
        run = subprocess.run([node, str(ROOT / "tests" / "audit_browser" / "check.mjs"), "--base", base], cwd=e2e, env=env, capture_output=True, text=True, timeout=600)
    finally:
        for server in servers:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, run.stdout[-3000:] + run.stderr[-3000:]
    result = json.loads(lines[-1])
    assert result["ok"], json.dumps(result, indent=1)
