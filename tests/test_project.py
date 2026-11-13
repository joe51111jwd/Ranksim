"""Project core: REAL Google pulls, the per-project mini engine, and page tests.

Synthetic fixtures only: a fake SERP provider, a fake crawler, a tiny corpus,
and a SQLite file under tmp_path. No network and no real rank database.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from aeo.config import Settings
from aeo.errors import ProviderConfigError
from aeo.ranksim import cli
from aeo.ranksim import project as proj
from aeo.ranksim.loop import FetchResult
from aeo.ranksim.serp import MockSerpProvider, SerpHit
from aeo.ranksim.store import RsPage, open_session

TODAY = date(2026, 11, 4)
NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
SITE = "www.dirtcrew.example"
OUR_URL = "https://www.dirtcrew.example/dirt-work-software/"
SEARCHES = ["dirt work software", "excavation crew app", "grading job costing", "paving crew scheduling"]
BLOCKED = "https://robots.example/dirt-work-software"
FORBIDDEN = "https://forbidden.example/excavation-crew-app"
BROKEN = "https://broken.example/grading-job-costing"
DASHES = ("\u2014", "\u2013")
SENTENCE = "Crews log hours on each job and the office sees cost by phase and machine every day."
OFF_TOPIC_HTML = (
    "<html><head><title>Lemon tart recipe</title></head><body><h1>Lemon tart</h1>"
    + "".join(
        f"<p>Step {n}: zest {n} lemons, whisk butter with sugar, fold almonds into pastry, chill it {n} hours, then bake golden.</p>"
        for n in range(1, 30)
    )
    + "</body></html>"
)


def page_html(title: str, h1: str, paragraphs: int = 14, extra: str = "") -> str:
    body = "".join(f"<p>{SENTENCE} {extra}</p>" for _ in range(paragraphs))
    return f"<!doctype html><html><head><title>{title}</title></head><body><main><h1>{h1}</h1>{body}</main></body></html>"


def _results() -> dict[str, list[str]]:
    """REAL order per search. Higher results match the search words more."""
    found: dict[str, list[str]] = {}
    for search in SEARCHES:
        slug = search.replace(" ", "-")
        found[search] = [
            f"https://alpha.example/{slug}",
            f"https://beta.example/{slug}",
            f"https://gamma.example/{slug}",
            f"https://delta.example/{slug}",
            f"https://epsilon.example/{slug}",
        ]
    found["dirt work software"].insert(3, OUR_URL)  # we already rank REAL #4 here
    found["dirt work software"].append(BLOCKED)
    found["excavation crew app"].append(FORBIDDEN)
    found["grading job costing"].append(BROKEN)
    return found


def _pages() -> dict[str, str]:
    pages: dict[str, str] = {}
    for search, urls in _results().items():
        words = search.title()
        for index, url in enumerate(urls):
            if url in (BLOCKED, FORBIDDEN, BROKEN, OUR_URL):
                continue
            strength = max(0, 4 - index)
            title = f"{words} " * strength + "Guide" if strength else "Misc notes"
            h1 = words if strength >= 2 else "Notes"
            pages[url] = page_html(title.strip(), h1, extra=(search + " ") * strength)
    pages[OUR_URL] = page_html("Field Tools", "Field tools for crews")
    pages["https://www.dirtcrew.example/"] = page_html("DirtCrew", "Run your dirt work crews")
    return pages


class FakeSerp:
    name = "fakeserp"
    is_google = True
    synthetic = False

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[str] = []

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        self.calls.append(query)
        if query in self.fail:
            raise RuntimeError("quota")
        urls = _results().get(query, [])[:count]
        return [
            SerpHit(position=index, url=url, title=f"Result {index} \u2014 {query}", snippet="", provider=self.name, synthetic=False, is_google=True)
            for index, url in enumerate(urls, start=1)
        ]


class FakeCrawler:
    """Fetch a batch of same-host URLs from a dict. Counts what was asked for."""

    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.pages = dict(pages if pages is not None else _pages())
        self.asked: list[str] = []

    def __call__(self, urls: list[str]) -> list[FetchResult]:
        out = []
        for url in urls:
            self.asked.append(url)
            if url == BLOCKED:
                out.append(FetchResult(url=url, html="", status="blocked", error="Blocked by robots.txt"))
            elif url == FORBIDDEN:
                out.append(FetchResult(url=url, html="", status="error", error="HTTP 403"))
            elif url in self.pages:
                out.append(FetchResult(url=url, html=self.pages[url], status="fetched", fetch_ms=120.0))
            else:
                out.append(FetchResult(url=url, html="", status="error", error="Fetch failed: connection reset"))
        return out


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'aeo.db'}",
        ranksim_database_url=f"sqlite:///{tmp_path / 'rank.db'}",
        serpapi_key=None,
        serper_api_key=None,
        dataforseo_login=None,
        dataforseo_password=None,
        brave_api_key=None,
        crawl_delay_seconds=0,
    )


def _project(tmp_path: Path, **extra) -> proj.Project:
    return proj.create_project("Dirt Crew", SITE, SEARCHES, root=tmp_path / "projects", discover_sitemap=False, **extra)


@pytest.fixture(scope="module")
def pulled(tmp_path_factory):
    """One project with a fake Google pull, a fake crawl, and a trained engine."""
    tmp_path = tmp_path_factory.mktemp("pulled")
    settings = _settings(tmp_path)
    project = _project(tmp_path, site_pages=["https://www.dirtcrew.example/"])
    events: list[proj.ProgressEvent] = []
    crawler = FakeCrawler()
    serp = FakeSerp()
    report = proj.pull(project, settings, serp_provider=serp, fetcher=crawler, progress=events.append, today=TODAY, workers=4)
    return {"tmp": tmp_path, "settings": settings, "project": project, "report": report, "events": events, "crawler": crawler, "serp": serp}


@pytest.fixture()
def ctx(pulled):
    return proj.load_project_context(pulled["project"].slug, root=pulled["tmp"] / "projects", today=TODAY, database=pulled["settings"].ranksim_database_url)


# Projects on disk


def test_create_load_list_and_update(tmp_path):
    made = proj.create_project("Dirt Crew", "https://www.dirtcrew.example/pricing", ["dirt work software", " Dirt  Work Software ", "hcss alternative"], root=tmp_path)
    assert made.slug == "dirt-crew"
    assert made.site == "www.dirtcrew.example"
    assert made.domain == "dirtcrew.example"
    assert made.searches == ["dirt work software", "hcss alternative"]
    assert made.intents["hcss alternative"] == "comparison"
    assert (tmp_path / "dirt-crew" / "project.json").is_file()
    assert proj.load_project("Dirt Crew", root=tmp_path).searches == made.searches
    assert proj.load_project("dirt-crew", root=tmp_path).created == made.created
    with pytest.raises(proj.ProjectError):
        proj.create_project("Dirt Crew", SITE, ["x"], root=tmp_path)
    with pytest.raises(proj.ProjectError):
        proj.load_project("nope", root=tmp_path)
    updated = proj.update_searches("dirt-crew", add=["grading software"], remove=["hcss alternative"], root=tmp_path)
    assert updated.searches == ["dirt work software", "grading software"]
    assert "hcss alternative" not in updated.intents
    rows = proj.list_projects(root=tmp_path)
    assert [row.slug for row in rows] == ["dirt-crew"]
    assert rows[0].searches == 2 and rows[0].latest_pull is None
    assert made.is_our_url("https://blog.dirtcrew.example/x") and not made.is_our_url("https://dirtcrew.example.evil.com/")


def test_split_searches_keeps_commas():
    assert proj.split_searches("a, b; c\nd") == ["a, b", "c", "d"]


# Import


def test_import_serp_csv_seeds_a_real_pull(tmp_path):
    project = _project(tmp_path)
    csv_path = tmp_path / "serp.csv"
    csv_path.write_text(
        "query,url,position\n"
        "dirt work software,https://alpha.example/a,1\n"
        "dirt work software,https://beta.example/b,2\n"
        "Excavation Crew App,https://gamma.example/c,1\n"
        "not ours,https://zeta.example/z,1\n"
    )
    report = proj.import_serp_csv(project, csv_path)
    assert report.searches_imported == ["dirt work software", "excavation crew app"]
    assert report.searches_ignored == ["not ours"]
    assert set(report.searches_missing) == {"grading job costing", "paving crew scheduling"}
    assert report.source.endswith("(pull date not recorded)")
    snapshot = proj.latest_google(project)
    entry = snapshot.by_search()["dirt work software"]
    assert snapshot.kind == "import" and entry.pulled_at is None
    assert entry.pulled_label == proj.PULL_DATE_NOT_RECORDED
    assert [(item.position, item.domain) for item in entry.results] == [(1, "alpha.example"), (2, "beta.example")]
    data = json.loads((project.dir / "pulls" / report.pull_id / "google.json").read_text())
    assert data["real"] is True

    dated = proj.import_serp_csv(project, csv_path, snapshot_date="2026-10-29", add_searches=True)
    assert dated.searches_added == ["not ours"]
    again = proj.latest_google(project).by_search()["not ours"]
    assert again.pulled_label == "2026-10-29 (date only)"
    with pytest.raises(proj.ProjectError):
        proj.import_serp_csv(project, csv_path, snapshot_date="last week")


def test_smartcrew_source_label():
    assert proj._default_source("smartcrew/serp.csv", None) == proj.SMARTCREW_SERP_SOURCE
    assert "pull date not recorded" in proj.SMARTCREW_SERP_SOURCE


# Pull


def test_pull_without_key_or_import_refuses_and_names_env_vars(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ProviderConfigError) as raised:
        proj.pull(project, _settings(tmp_path), fetcher=FakeCrawler(), today=TODAY)
    text = str(raised.value)
    for name in ("SERPAPI_KEY", "SERPER_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"):
        assert name in text
    assert "invented" in text
    assert proj.list_pulls(project) == []  # no half-made pull folder is left behind


def test_pull_refuses_a_synthetic_provider(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ProviderConfigError):
        proj.pull(project, _settings(tmp_path), serp_provider=MockSerpProvider(), fetcher=FakeCrawler(), today=TODAY)


def test_pull_records_real_results_statuses_and_metrics(pulled):
    report = pulled["report"]
    project = pulled["project"]
    snapshot = proj.latest_google(project)
    assert snapshot.kind == "api"
    entry = snapshot.by_search()["dirt work software"]
    assert entry.provider == "fakeserp" and entry.pulled_at
    assert entry.pulled_label.endswith("UTC")
    assert [item.url for item in entry.results] == _results()["dirt work software"]
    assert all(not any(dash in item.title for dash in DASHES) for item in entry.results)
    by_url = {item.url: item for item in report.urls}
    assert by_url[BLOCKED].status == "blocked by robots"
    assert by_url[FORBIDDEN].status == "HTTP 403"
    assert by_url[BROKEN].status == "error"
    assert by_url[OUR_URL].status == "fetched now" and by_url[OUR_URL].ours
    assert "https://www.dirtcrew.example/" in by_url  # listed site page
    assert by_url["https://alpha.example/dirt-work-software"].searches == ["dirt work software"]
    assert report.counts["fetched_now"] == len(_pages())
    assert [step.step for step in report.steps] == ["google", "crawl", "train", "save"]
    assert all(step.seconds >= 0 for step in report.steps)
    assert report.metrics is not None and report.metrics.n_searches == len(SEARCHES)
    assert report.metrics.pairwise is not None and "coin flip" in report.metrics.text
    assert {row.search for row in report.metrics.per_search} == set(SEARCHES)
    folder = project.dir / "pulls" / report.pull_id
    for name in ("google.json", "pull.json", "engine/pages.json", "engine/models.json", "engine/fit.json"):
        assert (folder / name).is_file(), name
    saved = proj.PullReport.from_dict(json.loads((folder / "pull.json").read_text()))
    assert saved.counts == report.counts and saved.metrics.pairwise == report.metrics.pairwise
    session = open_session(pulled["settings"].ranksim_database_url)
    try:
        assert session.get(RsPage, OUR_URL).status == "fetched"
        assert session.get(RsPage, BLOCKED).status == "blocked"
    finally:
        session.close()


def test_pull_progress_has_steps_and_counts(pulled):
    events = pulled["events"]
    steps = [event.step for event in events]
    for step in ("google", "crawl", "train", "done"):
        assert step in steps
    crawl = [event for event in events if event.step == "crawl"]
    assert crawl[-1].done == crawl[-1].total and crawl[-1].total > 0
    google = [event for event in events if event.step == "google" and event.total]
    assert max(event.done for event in google) == len(SEARCHES)
    assert all(not any(dash in event.message for dash in DASHES) for event in events)


def test_skip_google_reuses_real_results_and_fresh_pages(pulled, tmp_path):
    project = pulled["project"]
    crawler = FakeCrawler()
    report = proj.pull(project, pulled["settings"], skip_google=True, fetcher=crawler, today=TODAY)
    assert report.google_kind == "carried"
    assert "skipped" in report.google_note
    snapshot = proj.latest_google(project)
    first = proj.GoogleSnapshot.from_dict(json.loads((project.dir / "pulls" / pulled["report"].pull_id / "google.json").read_text()))
    assert snapshot.carried_from == pulled["report"].pull_id
    # The REAL pull time of each search is kept, not replaced by the carry time.
    assert snapshot.by_search()["dirt work software"].pulled_at == first.by_search()["dirt work software"].pulled_at
    # Fresh stored pages are not fetched again; only the failures are retried.
    assert set(crawler.asked) == {BLOCKED, FORBIDDEN, BROKEN}
    assert report.counts["stored"] == len(_pages())


def test_failed_refresh_keeps_the_stored_copy(tmp_path):
    settings = _settings(tmp_path)
    project = _project(tmp_path)
    proj.pull(project, settings, serp_provider=FakeSerp(), fetcher=FakeCrawler(), today=TODAY)
    session = open_session(settings.ranksim_database_url)
    try:
        row = session.get(RsPage, "https://alpha.example/dirt-work-software")
        row.fetched_at = datetime.now(timezone.utc) - timedelta(days=30)
        kept_html = row.raw_html
        session.commit()
    finally:
        session.close()
    down = FakeCrawler({})  # every refetch fails now
    report = proj.pull(project, settings, skip_google=True, fetcher=down, today=TODAY)
    status = next(item for item in report.urls if item.url == "https://alpha.example/dirt-work-software")
    assert status.code == "kept_stored"
    assert "kept the stored copy" in status.detail
    session = open_session(settings.ranksim_database_url)
    try:
        row = session.get(RsPage, "https://alpha.example/dirt-work-software")
        assert row.status == "fetched" and row.raw_html == kept_html
    finally:
        session.close()


def test_google_failure_for_one_search_is_not_invented(tmp_path):
    settings = _settings(tmp_path)
    project = _project(tmp_path)
    report = proj.pull(project, settings, serp_provider=FakeSerp(fail={"paving crew scheduling"}), fetcher=FakeCrawler(), today=TODAY)
    entry = proj.latest_google(project).by_search()["paving crew scheduling"]
    assert entry.results == [] and "RuntimeError" in entry.error
    assert report.searches_with_results == len(SEARCHES) - 1
    # A second failure keeps the earlier REAL results, with their earlier date.
    proj.pull(project, settings, serp_provider=FakeSerp(fail={"dirt work software"}), fetcher=FakeCrawler(), today=TODAY)
    kept = proj.latest_google(project).by_search()["dirt work software"]
    assert kept.results and "kept the earlier REAL results" in kept.error


def test_pull_with_a_brave_key_only_is_not_google(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"brave_api_key": "x"})
    project = _project(tmp_path)
    with pytest.raises(ProviderConfigError) as raised:
        proj.pull(project, settings, fetcher=FakeCrawler(), today=TODAY)
    assert "SERPAPI_KEY" in str(raised.value)


def test_configured_key_picks_a_google_provider_and_errors_hide_the_key(tmp_path):
    import httpx

    settings = _settings(tmp_path).model_copy(update={"serpapi_key": "secret-key"})
    provider, reason = proj._google_provider(settings, None)
    try:
        assert provider is not None and provider.name == "serpapi" and provider.is_google and reason == ""
    finally:
        provider.close()
    request = httpx.Request("GET", "https://serpapi.com/search.json?api_key=secret-key&q=x")
    error = httpx.HTTPStatusError("boom https://serpapi.com/search.json?api_key=secret-key", request=request, response=httpx.Response(429, request=request))
    assert proj._safe_error(error) == "HTTP 429"
    assert "secret" not in proj._safe_error(RuntimeError("api_key=secret-key"))


# Context and testing a page


def test_load_context_uses_the_saved_engine(ctx, pulled):
    assert ctx.loaded_from.startswith("saved engine")
    assert set(ctx.fit.models) == set(SEARCHES)
    assert ctx.metrics.pairwise == pulled["report"].metrics.pairwise
    assert ctx.load_seconds < 5


def test_model_check_text():
    assert "2 places optimistic" in proj.model_check_text(5, 7, "pull date not recorded")
    assert "1 place pessimistic" in proj.model_check_text(4, 3, "x")
    assert "matches" in proj.model_check_text(3, 3, "x")
    assert proj.model_check_text(None, 3, "x") == ""


def test_insertion_position_default():
    assert proj.insertion_position(0.5, [0.9, None, 0.1, 0.7]) == 3
    assert proj.insertion_position(1.0, [0.2, 0.3]) == 1
    assert proj.insertion_position(0.0, [0.0, 0.5]) == 2  # a tie does not put a page above you


def test_page_scores_every_search_in_real_order(ctx):
    html = page_html("Dirt Work Software", "Dirt Work Software", extra="dirt work software excavation crew grading paving")
    board = proj.test_page(ctx, html, OUR_URL, save=False)
    assert [row.search for row in board.searches] == SEARCHES
    for row in board.searches:
        assert row.error == ""
        assert [item.position for item in row.real_results] == sorted(item.position for item in row.real_results)
        assert row.predicted_position is not None and 1 <= row.predicted_position <= row.predicted_of
        assert row.predicted_of == len([item for item in row.real_results if not item.tested_url]) + 1
        above = sum(1 for item in row.real_results if item.model_scores_above_you)
        assert row.predicted_position == 1 + above
        assert len(row.beaten_by) == above
        assert all(comp.model_scores_above_you and comp.score_gap > 0 for comp in row.beaten_by)
        assert row.linear_position is not None
        assert row.confidence in {"UNSURE", "CLEARER"}
        assert row.backtest is not None and row.backtest.search == row.search
    dirt = board.searches[0]
    assert dirt.real_position == 4  # this URL already ranks REAL #4
    assert dirt.real_site_position == 4
    tested = [item for item in dirt.real_results if item.tested_url]
    assert len(tested) == 1 and tested[0].stored
    assert dirt.coverage_total == 6 and dirt.coverage_stored == 5  # robots-blocked page is not stored
    blocked = next(item for item in dirt.real_results if item.url == BLOCKED)
    assert not blocked.stored and blocked.model_scores_above_you is None
    summary = board.summary.text
    assert summary.startswith(f"PREDICTED top 3 on {board.summary.predicted_top3} of 4 searches, top 10 on {board.summary.predicted_top10} of 4.")
    assert "REAL (" in summary and "today" not in summary and "top 10 on 1 of 4" in summary


def test_neighbors_and_comparisons(ctx):
    html = page_html("Misc", "Misc", paragraphs=12)
    board = proj.test_page(ctx, html, "https://www.dirtcrew.example/new-page/", save=False)
    for row in board.searches:
        incumbents = [item for item in row.real_results if not item.tested_url]
        if row.predicted_position > 1:
            assert row.above.real_position == incumbents[row.predicted_position - 2].position
        else:
            assert row.above is None
        if row.predicted_position <= len(incumbents):
            assert row.below.real_position == incumbents[row.predicted_position - 1].position
        else:
            assert row.below is None
        for comp in row.beaten_by:
            assert comp.stored
            assert comp.site_signals and comp.site_signals[0].feature == "domain"
            assert comp.site_signals[0].yours_text == "dirtcrew.example"
            assert all(gap.feature in proj.PAGE_FEATURES for gap in comp.page_gaps)
            assert all(gap.feature in proj.SITE_FEATURES for gap in comp.site_signals[1:])
    assert any("new page" in note for note in board.notes)


def test_custom_insertion_function_is_used(ctx):
    def always_last(_score, incumbents):
        return len(incumbents) + 1

    board = proj.test_page(ctx, page_html("Dirt Work Software", "Dirt Work Software"), OUR_URL, insertion=always_last, save=False)
    for row in board.searches:
        assert row.predicted_position == row.predicted_of
        assert row.below is None


def test_versions_change_and_regressions(pulled):
    ctx = proj.load_project_context(pulled["project"], today=TODAY, database=pulled["settings"].ranksim_database_url)
    strong = page_html("Dirt Work Software Excavation Crew App", "Dirt Work Software", extra="dirt work software excavation crew app grading job costing paving crew scheduling")
    weak = page_html("Hello", "Hello", paragraphs=3)
    first = proj.test_page(ctx, strong, OUR_URL)
    assert first.version == "v1" and first.new_version_saved
    dirt = first.searches[0]
    assert dirt.real_position == 4 and dirt.model_check.startswith("Check: the stored copy of this URL is PREDICTED #")
    assert all(row.model_check == "" for row in first.searches[1:])  # this URL has no REAL position there
    assert first.stored_copy and "stored copy of this URL" in first.stored_copy
    assert first.baseline == "the stored copy"
    assert all(row.stored_copy_position is not None for row in first.searches)
    same = proj.test_page(ctx, strong, OUR_URL)
    assert same.version == "v1" and not same.new_version_saved
    second = proj.test_page(ctx, weak, OUR_URL)
    assert second.version == "v2" and second.previous_version == "v1"
    for old, new in zip(first.searches, second.searches):
        assert new.previous_position == old.predicted_position  # previous version rescored on this engine
        assert new.change_vs_previous == proj.change_text(old.predicted_position, new.predicted_position)
    worse = [row for row in second.searches if row.flag == "WORSE"]
    assert len(second.regressions) == len(worse) == second.summary.worse
    assert worse, "a 3-paragraph page should lose somewhere against the stored copy"
    history = proj.TesterHistory(second.history_path)
    assert [item.version for item in history.versions()] == ["v1", "v2"]
    assert Path(second.history_path).with_suffix("").is_dir()


def test_search_without_real_results_is_not_run(ctx):
    proj.update_searches(ctx.project, add=["trenching permits"])
    try:
        board = proj.test_page(ctx, page_html("Dirt Work Software", "Dirt Work Software"), OUR_URL, save=False)
        row = board.searches[-1]
        assert row.search == "trenching permits"
        assert row.predicted_position is None and row.real_results == []
        assert row.error == proj.NOT_RUN_GOOGLE
        assert board.summary.searches == 5 and board.summary.scored == 4
    finally:
        proj.update_searches(ctx.project, remove=["trenching permits"])


def test_board_json_and_text_are_labeled_and_dash_free(ctx):
    board = proj.test_page(ctx, page_html("Dirt Work Software \u2014 Crews", "Dirt Work Software"), OUR_URL, save=False)
    data = board.to_dict()
    text = json.dumps(data)
    assert not any(dash in text for dash in DASHES)
    assert data["predicted_note"].startswith("PREDICTED") and data["real_note"].startswith("REAL")
    assert data["searches"][0]["real_results"][0]["position"] == 1
    rendered = proj.render_board_text(board)
    assert not any(dash in rendered for dash in DASHES)
    assert "PREDICTED top 3 on" in rendered and "REAL (" in rendered
    assert "PREDICTED: your page slots in at #" in rendered
    assert "YOUR PAGE (PREDICTED #" in rendered
    for line in rendered.splitlines():
        if "#" in line and any(char.isdigit() for char in line) and line.strip().startswith(("PREDICTED:", "REAL (")):
            assert "PREDICTED" in line or "REAL" in line


def test_status_and_list(pulled):
    status = proj.project_status(pulled["project"])
    assert status.google is not None and status.report is not None
    info = status.coverage["dirt work software"]
    assert info["results"] == 7 and info["stored"] == 6
    text = proj.render_status_text(status)
    assert "Latest REAL Google results" in text and not any(dash in text for dash in DASHES)
    json.dumps(status.to_dict())
    rows = proj.list_projects(root=pulled["tmp"] / "projects")
    assert rows[0].has_engine and rows[0].latest_pull


def test_context_trains_now_when_no_engine_is_saved(tmp_path):
    settings = _settings(tmp_path)
    project = _project(tmp_path)
    proj.pull(project, settings, serp_provider=FakeSerp(), fetcher=FakeCrawler(), today=TODAY)
    csv_path = tmp_path / "serp.csv"
    rows = ["query,url,position"]
    for search, urls in _results().items():
        rows.extend(f"{search},{url},{index}" for index, url in enumerate(urls, start=1))
    csv_path.write_text("\n".join(rows) + "\n")
    proj.import_serp_csv(project, csv_path)
    loaded = proj.load_project_context(project, database=settings.ranksim_database_url, today=TODAY)
    assert loaded.loaded_from.startswith("trained now")
    assert any("No saved engine" in note for note in loaded.notes)
    assert set(loaded.fit.models) == set(SEARCHES)


# CLI


def test_cli_project_flow(tmp_path, monkeypatch, capsys):
    for name in ("SERPAPI_KEY", "SERPER_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "BRAVE_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr(proj, "_crawler_batch", lambda settings, delay: FakeCrawler())
    root = str(tmp_path / "projects")
    database = f"sqlite:///{tmp_path / 'rank.db'}"
    base = ["--database", database, "project"]
    searches_file = tmp_path / "searches.txt"
    searches_file.write_text("\n".join(SEARCHES) + "\n")
    cli.main([*base, "create", "Dirt Crew", "--site", SITE, "--searches-file", str(searches_file), "--projects-dir", root])
    assert "Created project Dirt Crew" in capsys.readouterr().out

    with pytest.raises(SystemExit) as stopped:
        cli.main([*base, "pull", "dirt-crew", "--projects-dir", root])
    assert stopped.value.code == 2
    assert "SERPAPI_KEY" in capsys.readouterr().err

    csv_path = tmp_path / "serp.csv"
    rows = ["query,url,position"]
    for search, urls in _results().items():
        rows.extend(f"{search},{url},{index}" for index, url in enumerate(urls, start=1))
    csv_path.write_text("\n".join(rows) + "\n")
    cli.main([*base, "import-serp", "dirt-crew", str(csv_path), "--projects-dir", root])
    assert "Imported 24 REAL Google results" in capsys.readouterr().out

    cli.main([*base, "pull", "dirt-crew", "--skip-google", "--projects-dir", root])
    out = capsys.readouterr().out
    assert "train" in out and "blocked by robots" in out and "HTTP 403" in out

    page = tmp_path / "page.html"
    page.write_text(page_html("Dirt Work Software", "Dirt Work Software"))
    cli.main([*base, "test", "dirt-crew", str(page), "--url", OUR_URL, "--projects-dir", root])
    out = capsys.readouterr().out
    assert "PREDICTED top 3 on" in out and "REAL (pull date not recorded)" in out
    assert not any(dash in out for dash in DASHES)

    # A weaker but still on-topic page: the content model (unvalidated) calls it worse, which fails only
    # with --fail-on-content-regression. --fail-on-regression checks the on-topic test.
    page.write_text(page_html("Hi", "Hi", paragraphs=2))
    cli.main([*base, "test", "dirt-crew", str(page), "--url", OUR_URL, "--projects-dir", root, "--json", "--fail-on-regression"])
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == "v2" and data["regressions"] and not data["topic_regressions"]
    with pytest.raises(SystemExit) as stopped:
        cli.main([*base, "test", "dirt-crew", str(page), "--url", OUR_URL, "--projects-dir", root, "--json", "--fail-on-content-regression"])
    assert stopped.value.code == 1
    capsys.readouterr()
    # An off-topic page fails --fail-on-regression: no longer on topic where the stored copy was.
    page.write_text(OFF_TOPIC_HTML)
    with pytest.raises(SystemExit) as stopped:
        cli.main([*base, "test", "dirt-crew", str(page), "--url", OUR_URL, "--projects-dir", root, "--json", "--fail-on-regression"])
    assert stopped.value.code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["topic_regressions"] and data["summary"]["off_topic"] == 3 and data["summary"]["uncertain"] == 1
    # A usage error is exit 2, never 1.
    with pytest.raises(SystemExit) as stopped:
        cli.main([*base, "test", "dirt-crew", str(page), "--projects-dir", root])
    assert stopped.value.code == 2
    assert "needs --url" in capsys.readouterr().err

    cli.main([*base, "show", "dirt-crew", "--projects-dir", root])
    assert "Pulls:" in capsys.readouterr().out
    cli.main([*base, "list", "--projects-dir", root])
    assert "dirt-crew" in capsys.readouterr().out
    cli.main([*base, "searches", "dirt-crew", "--add", "trench box rental", "--projects-dir", root])
    assert "trench box rental" in capsys.readouterr().out
