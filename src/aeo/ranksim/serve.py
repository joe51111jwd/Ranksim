"""Local demo server for the pre-publish rank check."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

from aeo.config import Settings
from aeo.crawl.fetch import Crawler
from aeo.ranksim.check import check_draft, score_site, suggest_links
from aeo.ranksim.confidence import LooFit, loo_fit
from aeo.ranksim.corpus import load_corpus_docs
from aeo.ranksim.demo import render_page, render_space_page
from aeo.ranksim.space import build_space, domains_from_labels, read_competitors
from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.judge import relevance_experiment
from aeo.ranksim.keywords import build_keywords
from aeo.ranksim.lambdamart import build_examples
from aeo.ranksim.linkgraph import LinkGraph, attach_graph, load_graph
from aeo.ranksim.llm import resolve_llm
from aeo.ranksim.benchmark import corpus_fingerprint, feature_names_sha, file_sha256, git_commit, lightgbm_version
from aeo.ranksim.features import FEATURE_NAMES
from aeo.ranksim.ledger import record_check
from aeo.ranksim.store import RsPage, open_session

SAMPLE_QUERY = "hcss heavyjob alternative"
SAMPLE_URL = "https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative/"
SAMPLE_FILE = Path("smartcrew/pr8_v3/compare-hcss-heavyjob-alternative.html")
GRAPH_PATH = Path("smartcrew/linkgraph.json")


class DemoState:
    def __init__(self, settings: Settings, serp_file: str, database: str) -> None:
        self.settings = settings
        self.database = database
        self.today = date.today()
        self.labels = load_serp_file(serp_file)
        self.queries = list(dict.fromkeys(row.query for row in self.labels))
        intents = {row.query: row.intent for row in build_keywords()}
        self.intent_for = intents
        from aeo.ranksim.bootstrap import seed_corpus_if_empty

        print(seed_corpus_if_empty(database))
        session = open_session(database)
        try:
            self.pages = load_corpus_docs(session)
            stored = [
                (url, html or "")
                for url, html in session.query(RsPage.url, RsPage.raw_html).filter(RsPage.status == "fetched").all()
            ]
        finally:
            session.close()
        self.provenance = {
            "labels_file": serp_file,
            "labels_sha256": file_sha256(serp_file),
            "corpus_sha256": corpus_fingerprint(stored),
            "corpus_pages": len(stored),
            "git_commit": git_commit(),
            "lightgbm": lightgbm_version(),
            "features_sha": feature_names_sha(list(FEATURE_NAMES)),
            "model": "lambdamart leave-one-query-out (the checked query held out)",
            "as_of": self.today.isoformat(),
        }
        self.graph = load_graph(str(GRAPH_PATH)) if GRAPH_PATH.is_file() else LinkGraph(
            notes=["No linkgraph.json yet. Run python -m aeo.ranksim graph."]
        )
        attach_graph(self.pages, self.graph)
        examples, uncovered, _withheld = build_examples(self.labels, self.pages, intents, today=self.today)
        self.uncovered = uncovered
        self.fit: LooFit = loo_fit(examples, keep_models=True)
        self.llm = resolve_llm(settings)
        self.relevance_note = relevance_experiment(examples, None).note
        self.site_rows = score_site(self.pages, self.labels, self.fit.models, self.queries, self.today)
        self.fixes = suggest_links(self.graph, self.pages, self.labels, self.fit.models, today=self.today)
        competitors = read_competitors(Path("smartcrew/competitors.csv"))
        if not competitors:
            competitors = domains_from_labels(self.labels, "smartcrewhq.com")
        self.space = build_space(
            self.labels,
            self.pages,
            self.fit.models,
            competitors,
            our_domain="smartcrewhq.com",
            today=self.today,
        )

    def accuracy_line(self) -> str:
        pairwise = "n/a" if self.fit.pairwise is None else f"{self.fit.pairwise:.3f}"
        ndcg = "n/a" if self.fit.ndcg is None else f"{self.fit.ndcg:.3f}"
        spearman = "n/a" if self.fit.spearman is None else f"{self.fit.spearman:.3f}"
        top3 = "n/a" if self.fit.top3 is None else f"{self.fit.top3:.3f}"
        return (
            f"Backtest on the pages stored now, leave-one-query-out, PREDICTED: pairwise {pairwise}, "
            f"NDCG at 10 {ndcg}, Spearman {spearman}, top-3 hit rate {top3}. "
            f"{self.fit.n_queries} queries, {self.fit.n_pairs} pairs, {self.uncovered} labeled rows had no stored HTML. "
            "An earlier fuller crawl (122 of 135 rows) scored pairwise 0.534 after the monotone and thin-page fixes. "
            "These are not the same test."
        )

    def llm_note(self) -> str:
        if self.llm.available:
            return f"AI layer: {self.llm.name}. Explanations and rewrites are model text about PREDICTED ranks."
        return "AI layer: stub. No OPENROUTER_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY is set. Explanations are templates. Rewrites do not run."

    def page(self, *, selected: str, url_value: str, html_value: str, result=None, error: str = "") -> HTMLResponse:
        html = render_page(
            queries=self.queries,
            selected=selected or (self.queries[0] if self.queries else ""),
            url_value=url_value,
            html_value=html_value,
            result=result,
            accuracy_line=self.accuracy_line(),
            calibration_note=self.fit.calibration.note,
            llm_note=self.llm_note(),
            relevance_note=self.relevance_note,
            site_rows=self.site_rows,
            graph=self.graph,
            fixes=self.fixes,
            error=error,
        )
        return HTMLResponse(html)

    def sample_html(self) -> str:
        if SAMPLE_FILE.is_file():
            return SAMPLE_FILE.read_text(encoding="utf-8", errors="replace")
        return "<html><head><title>HCSS alternative for dirtwork contractors</title></head><body><h1>HCSS alternative</h1><p>Draft.</p></body></html>"


def create_app(state: DemoState) -> FastAPI:
    app = FastAPI(title="Pre-publish rank check")

    @app.get("/", response_class=HTMLResponse)
    def home(demo: int = 0) -> HTMLResponse:
        if not demo:
            return state.page(selected=SAMPLE_QUERY, url_value=SAMPLE_URL, html_value="")
        result = check_draft(
            query=SAMPLE_QUERY,
            html=state.sample_html(),
            url=SAMPLE_URL,
            labels=state.labels,
            pages=state.pages,
            model=state.fit.models.get(SAMPLE_QUERY),
            calibration=state.fit.calibration,
            today=state.today,
            intent=state.intent_for.get(SAMPLE_QUERY, "comparison"),
            llm=state.llm,
            # Loading a page must never spend money. AI runs only when a person ticks the box on /check.
            run_ai=False,
        )
        return state.page(selected=SAMPLE_QUERY, url_value=SAMPLE_URL, html_value="", result=result, error=result.error)

    @app.get("/space", response_class=HTMLResponse)
    def space() -> HTMLResponse:
        return HTMLResponse(render_space_page(state.space, state.llm_note()))

    @app.post("/check", response_class=HTMLResponse)
    def check(query: str = Form(""), url: str = Form(""), html: str = Form(""), ai: str = Form("")) -> HTMLResponse:
        query = " ".join(query.split())
        url = url.strip()
        if query not in state.fit.models:
            return state.page(selected=query, url_value=url, html_value=html, error="That query is not in the stored Google file. No rank was invented.")
        draft = html.strip()
        if not draft and url:
            draft = _fetch_html(state.settings, url)
            if not draft:
                return state.page(selected=query, url_value=url, html_value=html, error=f"Could not fetch {url}. No page text was invented.")
        if not draft:
            return state.page(selected=query, url_value=url, html_value="", error="Paste HTML or provide a URL.")
        if not url:
            url = "https://draft.local/page"
        result = check_draft(
            query=query,
            html=draft,
            url=url,
            labels=state.labels,
            pages=state.pages,
            model=state.fit.models.get(query),
            calibration=state.fit.calibration,
            today=state.today,
            intent=state.intent_for.get(query, "commercial"),
            llm=state.llm,
            run_ai=ai == "1",
        )
        _record(state, result, draft)
        return state.page(selected=query, url_value=url, html_value="" if url != "https://draft.local/page" else draft, result=result, error=result.error)

    return app


def _record(state: DemoState, result, html: str) -> None:
    """Append the check to the prediction ledger. A ledger failure never blocks the answer."""
    session = open_session(state.database)
    try:
        record_check(session, result, html=html, calibration=state.fit.calibration, provenance=state.provenance)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Prediction ledger write failed: {exc}")
    finally:
        session.close()


def _fetch_html(settings: Settings, url: str) -> str:
    crawler = Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=0,
        allow_private=settings.crawl_allow_private,
        timeout=settings.request_timeout_seconds,
    )
    try:
        fetched = crawler.fetch(url)
    except Exception:
        return ""
    finally:
        crawler.close()
    if fetched.blocked or fetched.error or not fetched.html:
        return ""
    return fetched.html
