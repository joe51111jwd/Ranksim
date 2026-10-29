import httpx
from fastapi.testclient import TestClient

from aeo.api import create_app
from aeo.db import fts_search, session_factory
from aeo.indexing import index_html
from aeo.models import Page
from aeo.providers.embeddings import MockEmbedder
from aeo.providers.factory import use_http_client
from aeo.providers.llm import choose_mock_indices
from tests.conftest import make_settings
from uuid import uuid4


def _html() -> str:
    paragraph = (
        "The best trail mix for long hikes is almonds, pumpkin seeds, dried cranberries, and dark chocolate. "
        "Northwind packs this trail mix for long hikes at about 100 calories per ounce so it stays light. "
    )
    return f"""<!doctype html>
<html><head>
<title>Best trail mix for long hikes | Northwind</title>
<meta name="description" content="The best trail mix for long hikes balances salt, carbs, and weight.">
<link rel="canonical" href="https://northwind.example/trail-mix">
<meta property="article:modified_time" content="2026-01-15">
<script type="application/ld+json">{{"@type":"Article","dateModified":"2026-01-15"}}</script>
</head>
<body><h1>Best trail mix for long hikes</h1><p>{paragraph * 4}</p></body></html>"""


def _payload() -> dict:
    return {
        "site_url": "https://northwind.example",
        "pages": [{"url": "https://northwind.example/trail-mix", "html": _html()}],
        "questions": [
            "What is the best trail mix for long hikes?",
            "What is the capital of France?",
        ],
        "n_runs": 5,
        "search_provider": "mock",
        "llm_provider": "mock",
        "embedding_provider": "mock",
        "fetch_competitors": True,
    }


def _share_from_policy(passages: list[dict], user_domain: str, n_runs: int) -> float:
    if n_runs == 0:
        return 0.0
    cited = 0
    for run_index in range(n_runs):
        chosen = choose_mock_indices(len(passages), run_index)
        domains = [passages[index]["domain"] for index in chosen]
        if user_domain in domains:
            cited += 1
    return cited / n_runs


def test_simulation_scores_match_the_mock_policy_and_do_not_invent_sources(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post("/v1/simulations", json=_payload())
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "completed"
        assert body["providers"]["synthetic"] is True
        assert body["providers"]["answers_are_official_engine_results"] is False
        assert body["providers"]["compared_against_live_web"] is False
        assert body["providers"]["search"] == "mock"
        assert any("synthetic" in warning.lower() or "mock" in warning.lower() for warning in body["warnings"])

        by_question = {item["question"]: item for item in body["questions"]}
        on_topic = by_question["What is the best trail mix for long hikes?"]
        off_topic = by_question["What is the capital of France?"]

        assert on_topic["fanout_is_synthetic"] is True
        assert on_topic["subqueries"][0] == on_topic["question"]
        assert on_topic["html_checks"]["has_h1"] is True
        assert on_topic["html_checks"]["has_title"] is True
        assert on_topic["html_checks"]["has_meta_description"] is True
        assert on_topic["html_checks"]["has_canonical"] is True
        assert on_topic["html_checks"]["text_in_raw_html"] is True
        assert on_topic["html_checks"]["has_updated_date"] is True
        assert on_topic["simulated_citation_share"] == _share_from_policy(
            on_topic["top_passages"], "northwind.example", 5
        )
        assert on_topic["simulated_citation_share"] > 0
        assert on_topic["diagnosis"] == "likely_cited"
        for run in on_topic["runs"]:
            assert run["synthetic"] is True
            assert "mock" in run["answer"].lower()
            cited_urls = {item["url"] for item in run["citations"]}
            offered = {item["url"] for item in run["passages"]}
            assert cited_urls <= offered
        recomputed = sum(1 for run in on_topic["runs"] if run["cited"]) / 5
        assert on_topic["simulated_citation_share"] == recomputed
        assert all(item["synthetic"] is True and item["provider"] == "mock" for item in on_topic["competitors"])

        assert off_topic["simulated_citation_share"] == 0
        assert off_topic["diagnosis"] == "missing_page"

        logged = client.post(
            "/v1/calibration",
            json={
                "engine": "chatgpt",
                "cited": True,
                "mentioned": True,
                "citation_position": 1,
                "question_id": on_topic["id"],
                "notes": "Observed by hand in a real ChatGPT session.",
            },
        )
        assert logged.status_code == 201, logged.text
        assert logged.json()["predicted_citation_share"] == on_topic["simulated_citation_share"]
        assert logged.json()["engine"] == "chatgpt"

        accuracy = client.get("/v1/calibration/accuracy")
        assert accuracy.status_code == 200
        report = accuracy.json()
        assert report["observations"] == 1
        assert report["overall"]["accuracy"] is not None
        assert "logged observations" in report["note"]

        empty = client.get("/v1/calibration/accuracy?engine=perplexity")
        assert empty.json()["observations"] == 0
        assert empty.json()["overall"]["brier_score"] is None

        draft = client.post(
            f"/v1/questions/{on_topic['id']}/ab",
            json={
                "change_type": "draft",
                "page_url": "https://northwind.example/trail-mix",
                "html": "<html><head><title>Office plants</title></head><body><h1>Office plants</h1><p>The lobby fern needs water. This draft is not about hiking food.</p></body></html>",
            },
        )
        assert draft.status_code == 201, draft.text
        diff = draft.json()["diff"]
        assert diff["citation_share_after"] < diff["citation_share_before"]
        assert diff["improved"] is False
        assert diff["content_hash_before"] != diff["content_hash_after"]
        assert diff["regressions"]

        refused = client.post(
            "/v1/simulations",
            json={
                "site_url": "https://northwind.example",
                "pages": [{"url": "https://northwind.example/a", "html": "<html><title>A</title><p>words</p></html>"}],
                "questions": ["Anything?"],
                "search_provider": "brave",
            },
        )
        assert refused.status_code == 400
        assert "BRAVE_API_KEY" in refused.json()["detail"]

        providers = client.get("/v1/providers").json()
        assert providers["answers_are_official_engine_results"] is False
        assert providers["search_provider"] == "mock"
        schema = client.get("/openapi.json").json()
        for path in (
            "/v1/simulations",
            "/v1/questions/{question_id}/ab",
            "/v1/calibration",
            "/v1/calibration/accuracy",
            "/health",
        ):
            assert path in schema["paths"]
        missing = client.get("/v1/simulations/does-not-exist")
        assert missing.status_code == 404


def test_sqlite_full_text_index_finds_a_passage(tmp_path):
    settings = make_settings(tmp_path)
    factory = session_factory(settings)
    session = factory()
    page = Page(
        id=str(uuid4()),
        url="https://northwind.example/trail-mix",
        role="user",
        origin="seed",
        provider_label="user_supplied",
        status="seeded",
        raw_html=_html(),
    )
    session.add(page)
    session.flush()
    index_html(session, page, _html(), MockEmbedder())
    session.commit()
    found = fts_search(session, ["almonds"])
    assert page.id
    chunks = found
    assert chunks
    assert fts_search(session, ["zzzqqq"]) == []
    session.close()


def test_site_crawl_records_robots_blocks(tmp_path):
    settings = make_settings(tmp_path, crawl_allow_private=True, search_provider="none")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url).split("?")[0])
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
        if url.rstrip("/").endswith("example.com"):
            return httpx.Response(
                200,
                text="<html><title>Home</title><body><h1>Home</h1><p>Public text about example pages.</p><a href='/secret'>secret</a><a href='/about'>about</a></body></html>",
            )
        if url.endswith("/about"):
            return httpx.Response(200, text="<html><title>About</title><body><h1>About</h1><p>About the example site and its pages for visitors.</p></body></html>")
        if url.endswith("/secret"):
            raise AssertionError("secret was fetched")
        return httpx.Response(404, text="no")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    use_http_client(settings, http)
    try:
        app = create_app(settings)
        with TestClient(app) as client:
            response = client.post(
                "/v1/simulations",
                json={
                    "site_url": "https://example.com/",
                    "questions": ["What are example pages?"],
                    "n_runs": 1,
                    "max_pages": 6,
                    "fetch_competitors": False,
                    "search_provider": "none",
                },
            )
    finally:
        use_http_client(settings, None)
        http.close()
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["providers"]["compared_against_live_web"] is False
    blocked = [page for page in body["pages"] if page["status"] == "blocked"]
    assert any(page["url"].endswith("/secret") for page in blocked)
    assert all(not url.endswith("/secret") for url in requested)
