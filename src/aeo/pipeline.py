"""Crawl, retrieve, and score a simulation. This is what the job worker runs."""

from __future__ import annotations

import logging
from dataclasses import asdict
from uuid import uuid4

from sqlalchemy.orm import Session

from aeo.config import Settings
from aeo.crawl.fetch import Fetched
from aeo.crawl.urls import domain_of, normalize_url
from aeo.db import replace_chunk_index
from aeo.errors import ConflictError, NotFoundError, UnsafeURL
from aeo.indexing import index_html
from aeo.models import ABTest, Chunk, Page, Question, Run, Simulation, Site, Snapshot, utcnow
from aeo.providers.factory import (
    build_crawler,
    build_embedder,
    build_js_renderer,
    build_llm,
    build_search,
)
from aeo.providers.llm import PassageContext
from aeo.providers.search import SearchProvider
from aeo.rank.bm25 import bm25_scores
from aeo.rank.hybrid import RankedDoc
from aeo.sim.citations import (
    AnswerScore,
    QuestionMetrics,
    SourcePassage,
    aggregate_scores,
    competitor_citation_share,
    score_answer,
)
from aeo.sim.diagnostics import (
    DiagnosticSignals,
    build_html_checks,
    diagnose,
)
from aeo.sim.diff import diff_metrics
from aeo.sim.fanout import expand_question
from aeo.sim.retrieve import retrieve_passages

logger = logging.getLogger(__name__)

SEARCH_QUERY_LIMIT = 3
HITS_PER_QUERY = 4
MAX_COMPETITORS = 6
PER_QUERY_LIMIT = 8
RAW_TEXT_MIN_WORDS = 40
EMPTY_SHELL_WORDS = 8


class _Runtime:
    def __init__(self, settings: Settings, search_name: str, llm_name: str, embedding_name: str, render_js: bool) -> None:
        self.settings = settings
        self.search: SearchProvider = build_search(settings, search_name)
        self.llm = build_llm(settings, llm_name)
        self.embedder = build_embedder(settings, embedding_name)
        self.crawler = build_crawler(settings)
        self.renderer = build_js_renderer(render_js)

    def close(self) -> None:
        for obj in (self.search, self.llm, self.embedder, self.crawler):
            close = getattr(obj, "close", None)
            if close:
                close()


def run_index_site(session: Session, settings: Settings, site_id: str) -> dict:
    site = session.get(Site, site_id)
    if site is None:
        raise NotFoundError(f"Site {site_id} was not found")
    from aeo.providers.factory import _resolve_embedding

    warnings: list[str] = []
    embedding_name = _resolve_embedding(settings, settings.embedding_provider, warnings)
    embedder = build_embedder(settings, embedding_name)
    crawler = build_crawler(settings)
    renderer = build_js_renderer(site.render_js)
    try:
        site.status = "indexing"
        session.commit()
        index_user_site(
            session,
            site,
            simulation_id=None,
            embedder=embedder,
            crawler=crawler,
            renderer=renderer,
            render_js=site.render_js,
            sitemap_url=site.sitemap_url,
            max_pages=site.max_pages,
            warnings=warnings,
        )
        site.status = "ready"
        site.warnings_json = warnings
        site.finished_at = utcnow()
        site.error = None
        session.commit()
        return {"site_id": site.id, "status": site.status}
    finally:
        for obj in (embedder, crawler):
            close = getattr(obj, "close", None)
            if close:
                close()


def run_simulation(session: Session, settings: Settings, simulation_id: str) -> dict:
    sim = session.get(Simulation, simulation_id)
    if sim is None:
        raise NotFoundError(f"Simulation {simulation_id} was not found")
    site = session.get(Site, sim.site_id)
    if site is None:
        raise NotFoundError(f"Site {sim.site_id} was not found")
    runtime = _Runtime(
        settings,
        sim.search_provider,
        sim.llm_provider,
        sim.embedding_provider,
        sim.render_js,
    )
    warnings = list(sim.warnings_json or [])
    try:
        sim.status = "indexing"
        site.status = "indexing"
        session.commit()
        if _needs_user_index(session, sim, site.id):
            index_user_site(
                session,
                site,
                simulation_id=sim.id,
                embedder=runtime.embedder,
                crawler=runtime.crawler,
                renderer=runtime.renderer,
                render_js=sim.render_js,
                sitemap_url=sim.sitemap_url,
                max_pages=sim.max_pages,
                warnings=warnings,
            )
        site.status = "ready"
        sim.warnings_json = warnings
        sim.status = "running"
        session.commit()
        questions = (
            session.query(Question)
            .filter(Question.simulation_id == sim.id)
            .order_by(Question.position, Question.id)
            .all()
        )
        for question in questions:
            run_question(session, settings, sim, site, question, runtime, warnings, refanout=True)
            sim.warnings_json = warnings
            session.commit()
        sim.status = "completed"
        sim.error = None
        sim.finished_at = utcnow()
        sim.warnings_json = warnings
        site.finished_at = utcnow()
        site.error = None
        session.commit()
        return {"simulation_id": sim.id, "status": sim.status}
    finally:
        runtime.close()


def run_ab(session: Session, settings: Settings, ab_test_id: str) -> dict:
    ab = session.get(ABTest, ab_test_id)
    if ab is None:
        raise NotFoundError(f"A/B test {ab_test_id} was not found")
    question = session.get(Question, ab.question_id)
    if question is None:
        raise NotFoundError("Question was not found")
    if question.status != "completed" or question.citation_share is None:
        raise ConflictError("The question has no completed runs to snapshot")
    sim = session.get(Simulation, question.simulation_id)
    site = session.get(Site, question.site_id)
    if sim is None or site is None:
        raise NotFoundError("Simulation was not found")
    current = _current_user_page(session, site.id, ab.page_url)
    if current is None:
        raise ConflictError(f"No current user page matches {ab.page_url}")

    before = _snapshot(session, question, label="before", content_hash=current.content_hash)
    ab.before_snapshot_id = before.id
    ab.status = "running"
    session.commit()

    runtime = _Runtime(
        settings,
        sim.search_provider,
        sim.llm_provider,
        sim.embedding_provider,
        sim.render_js,
    )
    try:
        after_hash = _apply_page_change(session, ab, current, runtime)
        session.query(Run).filter(Run.question_id == question.id).delete(synchronize_session=False)
        warnings = list(sim.warnings_json or [])
        run_question(session, settings, sim, site, question, runtime, warnings, refanout=False)
        after = _snapshot(session, question, label="after", content_hash=after_hash)
        before_metrics = _metrics_from_snapshot(before, question.n_runs)
        after_metrics = _metrics_from_snapshot(after, question.n_runs)
        diff = diff_metrics(
            before_metrics,
            after_metrics,
            diagnosis_before=before.diagnosis,
            diagnosis_after=after.diagnosis,
        ).as_dict()
        diff["content_hash_before"] = before.content_hash
        diff["content_hash_after"] = after.content_hash
        diff["page_url"] = ab.page_url
        diff["change_type"] = ab.change_type
        ab.after_snapshot_id = after.id
        ab.diff_json = diff
        ab.status = "completed"
        ab.error = None
        ab.finished_at = utcnow()
        session.commit()
        return {"ab_test_id": ab.id, "status": ab.status}
    finally:
        runtime.close()


def index_user_site(
    session: Session,
    site: Site,
    *,
    simulation_id: str | None,
    embedder,
    crawler,
    renderer,
    render_js: bool,
    sitemap_url: str | None,
    max_pages: int,
    warnings: list[str],
) -> None:
    seeds = (
        session.query(Page)
        .filter(Page.site_id == site.id, Page.role == "user", Page.status == "seeded", Page.is_current.is_(True))
        .all()
    )
    if seeds:
        _add_warning(warnings, "User pages were indexed from submitted HTML, not crawled.")
        for page in seeds:
            if simulation_id and page.simulation_id is None:
                page.simulation_id = simulation_id
            index_html(session, page, page.raw_html or "", embedder)
        return

    if render_js and not renderer.available:
        _add_warning(
            warnings,
            "JavaScript rendering was requested but Playwright is not installed. Only raw HTML was indexed.",
        )
    else:
        _add_warning(warnings, "User pages were fetched as raw HTML. JavaScript was not executed.")

    batch = crawler.crawl(site.url, sitemap_url=sitemap_url, max_pages=max_pages)
    for note in batch.warnings:
        _add_warning(warnings, note)
    for fetched in batch.pages:
        _store_fetched_page(
            session,
            fetched,
            embedder=embedder,
            renderer=renderer,
            site_id=site.id,
            simulation_id=simulation_id,
            question_id=None,
            role="user",
            origin="crawl",
            provider_label="crawl",
            synthetic=False,
            warnings=warnings,
        )


def run_question(
    session: Session,
    settings: Settings,
    sim: Simulation,
    site: Site,
    question: Question,
    runtime: _Runtime,
    warnings: list[str],
    *,
    refanout: bool,
) -> None:
    question.status = "running"
    question.error = None
    if refanout or not question.subqueries_json:
        fanout = expand_question(question.text, runtime.llm)
        question.subqueries_json = fanout.subqueries
        question.fanout_provider = fanout.provider
        question.fanout_synthetic = fanout.synthetic
        if refanout:
            _delete_competitors(session, question.id)
        if sim.fetch_competitors and runtime.search.name != "none":
            _index_competitors(session, sim, question, runtime, warnings)
        elif sim.fetch_competitors and runtime.search.name == "none":
            _add_warning(warnings, "Competitor search is off, so no rival pages were indexed.")
    elif sim.fetch_competitors and runtime.search.name == "none":
        _add_warning(warnings, "Competitor search is off, so no rival pages were indexed.")

    scope = _load_scope(session, site.id, question.id)
    docs = [
        RankedDoc(
            id=item.chunk.id,
            text=item.chunk.body,
            embedding=list(item.chunk.embedding_json or []),
            ordinal=item.chunk.ordinal,
        )
        for item in scope
    ]
    by_id = {item.chunk.id: item for item in scope}
    passages, trace = retrieve_passages(
        list(question.subqueries_json or []),
        docs,
        runtime.embedder,
        rrf_k=settings.rrf_k,
        per_query_limit=PER_QUERY_LIMIT,
        final_limit=settings.final_passages,
    )
    offered: list[PassageContext] = []
    fused_public: list[dict] = []
    for passage in passages:
        item = by_id.get(passage.id)
        if item is None:
            continue
        marker = f"S{passage.rank}"
        offered.append(
            PassageContext(
                marker=marker,
                passage_id=item.chunk.id,
                url=item.page.final_url or item.page.url,
                domain=domain_of(item.page.final_url or item.page.url),
                heading=item.chunk.heading_path,
                text=item.chunk.body,
                synthetic=item.page.synthetic,
            )
        )
        fused_public.append(
            {
                "id": item.chunk.id,
                "rank": passage.rank,
                "score": passage.score,
                "url": item.page.final_url or item.page.url,
                "domain": domain_of(item.page.final_url or item.page.url),
                "role": item.page.role,
                "heading": item.chunk.heading_path,
                "snippet": " ".join(item.chunk.body.split())[:240],
                "synthetic": item.page.synthetic,
                "provider": item.page.provider_label,
            }
        )
    question.retrieval_json = {"subqueries": trace, "fused": fused_public}

    session.query(Run).filter(Run.question_id == question.id).delete(synchronize_session=False)
    sources = [
        SourcePassage(
            marker_num=index + 1,
            passage_id=passage.passage_id,
            url=passage.url,
            domain=passage.domain,
        )
        for index, passage in enumerate(offered)
    ]
    scores: list[AnswerScore] = []
    for run_index in range(question.n_runs):
        answer = runtime.llm.answer(question.text, offered, run_index)
        score = score_answer(answer, sources, site.domain)
        scores.append(score)
        session.add(
            Run(
                id=str(uuid4()),
                question_id=question.id,
                run_index=run_index,
                answer_text=answer,
                llm_provider=runtime.llm.name,
                synthetic=bool(runtime.llm.synthetic),
                cited=score.cited,
                citation_position=score.citation_position,
                mentioned=score.mentioned,
                passages_json=[asdict(passage) for passage in offered],
                citations_json=[asdict(citation) for citation in score.citations],
                unknown_markers_json=score.unknown_markers,
            )
        )
    metrics = aggregate_scores(scores)
    question.citation_share = metrics.citation_share
    question.mention_rate = metrics.mention_rate
    question.mean_citation_position = metrics.mean_citation_position
    report = _diagnose_question(session, site, question, fused_public, scores)
    question.diagnosis = report["label"]
    question.diagnosis_json = report
    question.status = "completed"


def _needs_user_index(session: Session, sim: Simulation, site_id: str) -> bool:
    seeded = (
        session.query(Page)
        .filter(Page.site_id == site_id, Page.role == "user", Page.status == "seeded", Page.is_current.is_(True))
        .count()
    )
    if seeded:
        return True
    ready = (
        session.query(Page)
        .filter(Page.site_id == site_id, Page.role == "user", Page.status == "fetched", Page.is_current.is_(True))
        .count()
    )
    if sim.reuse_existing_pages and ready:
        return False
    return True


def _index_competitors(session, sim: Simulation, question: Question, runtime: _Runtime, warnings: list[str]) -> None:
    seen: set[str] = set()
    hits = []
    for subquery in list(question.subqueries_json or [])[:SEARCH_QUERY_LIMIT]:
        try:
            found = runtime.search.search(subquery, count=HITS_PER_QUERY)
        except Exception as exc:
            raise RuntimeError(f"Search provider {runtime.search.name} failed: {exc}") from exc
        for hit in found:
            try:
                key = normalize_url(hit.url)
            except ValueError:
                continue
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)
            if len(hits) >= MAX_COMPETITORS:
                break
        if len(hits) >= MAX_COMPETITORS:
            break
    if not hits:
        _add_warning(
            warnings,
            f"Search provider {runtime.search.name} returned no results for this question. No competitor pages were invented.",
        )
        return
    if runtime.search.synthetic:
        _add_warning(
            warnings,
            "Competitor pages for this run are synthetic mock documents, not pages fetched from the live web.",
        )
    for hit in hits:
        if hit.inline_html:
            page = Page(
                id=str(uuid4()),
                site_id=None,
                simulation_id=sim.id,
                question_id=question.id,
                url=normalize_url(hit.url),
                final_url=normalize_url(hit.url),
                role="competitor",
                origin="search",
                provider_label=hit.provider,
                synthetic=True,
                status="seeded",
                is_current=True,
                robots_status="not_fetched",
                raw_html=hit.inline_html,
            )
            session.add(page)
            session.flush()
            index_html(session, page, hit.inline_html, runtime.embedder)
            continue
        try:
            fetched = runtime.crawler.fetch(hit.url)
        except UnsafeURL as exc:
            _add_warning(warnings, f"Skipped competitor URL {hit.url}: {exc}")
            continue
        _store_fetched_page(
            session,
            fetched,
            embedder=runtime.embedder,
            renderer=runtime.renderer,
            site_id=None,
            simulation_id=sim.id,
            question_id=question.id,
            role="competitor",
            origin="search",
            provider_label=hit.provider,
            synthetic=hit.synthetic,
            warnings=warnings,
        )


def _store_fetched_page(
    session: Session,
    fetched: Fetched,
    *,
    embedder,
    renderer,
    site_id: str | None,
    simulation_id: str | None,
    question_id: str | None,
    role: str,
    origin: str,
    provider_label: str,
    synthetic: bool,
    warnings: list[str],
) -> None:
    try:
        url = normalize_url(fetched.final_url or fetched.url)
    except ValueError:
        url = fetched.url
    page = Page(
        id=str(uuid4()),
        site_id=site_id,
        simulation_id=simulation_id,
        question_id=question_id,
        url=url,
        final_url=url,
        role=role,
        origin=origin,
        provider_label=provider_label,
        synthetic=synthetic,
        status="queued",
        is_current=True,
        robots_status=fetched.robots_status,
    )
    session.add(page)
    session.flush()
    if fetched.blocked:
        page.status = "blocked"
        page.error = fetched.error
        return
    if fetched.error or not fetched.html:
        page.status = "error"
        page.error = fetched.error or "empty response"
        return
    rendered_html = None
    if renderer.available:
        try:
            rendered_html = renderer.render(page.final_url or page.url)
        except Exception as exc:  # noqa: BLE001 — render is optional
            _add_warning(warnings, f"JavaScript render failed for {page.url}: {exc}")
    index_html(session, page, fetched.html, embedder, rendered_html=rendered_html)


def _delete_competitors(session: Session, question_id: str) -> None:
    pages = session.query(Page).filter(Page.question_id == question_id, Page.role == "competitor").all()
    if not pages:
        return
    page_ids = [page.id for page in pages]
    chunk_ids = [row[0] for row in session.query(Chunk.id).filter(Chunk.page_id.in_(page_ids)).all()]
    if chunk_ids:
        session.query(Chunk).filter(Chunk.id.in_(chunk_ids)).delete(synchronize_session=False)
        replace_chunk_index(session, chunk_ids)
    for page in pages:
        session.delete(page)
    session.flush()


def _load_scope(session: Session, site_id: str, question_id: str) -> list[_Scoped]:
    user_pages = (
        session.query(Page)
        .filter(
            Page.site_id == site_id,
            Page.role == "user",
            Page.is_current.is_(True),
            Page.status == "fetched",
        )
        .all()
    )
    competitor_pages = (
        session.query(Page)
        .filter(
            Page.question_id == question_id,
            Page.role == "competitor",
            Page.is_current.is_(True),
            Page.status == "fetched",
        )
        .all()
    )
    pages = {page.id: page for page in [*user_pages, *competitor_pages]}
    if not pages:
        return []
    chunks = session.query(Chunk).filter(Chunk.page_id.in_(list(pages))).all()
    return [_Scoped(chunk, pages[chunk.page_id]) for chunk in chunks]


class _Scoped:
    def __init__(self, chunk: Chunk, page: Page) -> None:
        self.chunk = chunk
        self.page = page


def _diagnose_question(
    session: Session,
    site: Site,
    question: Question,
    fused: list[dict],
    scores: list[AnswerScore],
) -> dict:
    user_pages = (
        session.query(Page)
        .filter(Page.site_id == site.id, Page.role == "user", Page.is_current.is_(True))
        .all()
    )
    indexed = [page for page in user_pages if page.status == "fetched" and page.raw_word_count >= RAW_TEXT_MIN_WORDS]
    blocked = [page for page in user_pages if page.status == "blocked"]
    failed = [page for page in user_pages if page.status == "error"]
    thin = [
        page
        for page in user_pages
        if page.status == "fetched" and page.raw_word_count < EMPTY_SHELL_WORDS
    ]
    candidates = indexed or [page for page in user_pages if page.status == "fetched"]
    best = None
    best_score = 0.0
    if candidates:
        score_rows = bm25_scores(question.text, [page.main_text or "" for page in candidates])
        best_index = max(range(len(candidates)), key=lambda index: (score_rows[index], -index))
        best = candidates[best_index]
        best_score = float(score_rows[best_index])
    user_ranks = [item["rank"] for item in fused if item["role"] == "user"]
    checks = None
    if best is not None:
        checks = build_html_checks(
            title=best.title,
            meta_description=best.meta_description,
            canonical=best.canonical,
            h1=best.h1,
            raw_word_count=best.raw_word_count,
            updated=best.updated_label,
            published=best.published_label,
            main_text=best.main_text or "",
            question=question.text,
            js_dependent=best.js_dependent,
        )
    competitor_pages = (
        session.query(Page)
        .filter(
            Page.question_id == question.id,
            Page.role == "competitor",
            Page.is_current.is_(True),
            Page.status == "fetched",
        )
        .count()
    )
    signals = DiagnosticSignals(
        user_pages_indexed=len(indexed),
        user_pages_blocked=len(blocked),
        user_pages_failed=len(failed),
        thin_shell=bool(thin) and not indexed,
        js_dependent=bool(best and best.js_dependent and best.raw_word_count < RAW_TEXT_MIN_WORDS),
        raw_text_usable=bool(best and best.raw_word_count >= RAW_TEXT_MIN_WORDS and not best.js_dependent),
        best_page_found=best is not None,
        best_page_bm25=best_score,
        user_in_top_passages=bool(user_ranks),
        best_user_passage_rank=min(user_ranks) if user_ranks else None,
        citation_share=question.citation_share or 0.0,
        mention_rate=question.mention_rate or 0.0,
        competitor_pages_indexed=competitor_pages,
        competitor_citation_share=competitor_citation_share(scores, site.domain),
        html=checks,
    )
    label, reasons = diagnose(signals)
    return {
        "label": label,
        "reasons": reasons,
        "html_checks": checks.as_dict() if checks else None,
        "best_page_url": best.url if best else None,
        "signals": {
            "user_pages_indexed": signals.user_pages_indexed,
            "user_pages_blocked": signals.user_pages_blocked,
            "user_pages_failed": signals.user_pages_failed,
            "thin_shell": signals.thin_shell,
            "js_dependent": signals.js_dependent,
            "best_page_bm25": signals.best_page_bm25,
            "user_in_top_passages": signals.user_in_top_passages,
            "best_user_passage_rank": signals.best_user_passage_rank,
            "competitor_pages_indexed": signals.competitor_pages_indexed,
            "competitor_citation_share": signals.competitor_citation_share,
        },
    }


def _snapshot(session: Session, question: Question, *, label: str, content_hash: str | None) -> Snapshot:
    runs = session.query(Run).filter(Run.question_id == question.id).order_by(Run.run_index).all()
    snap = Snapshot(
        id=str(uuid4()),
        question_id=question.id,
        label=label,
        citation_share=question.citation_share or 0.0,
        mention_rate=question.mention_rate or 0.0,
        mean_citation_position=question.mean_citation_position,
        diagnosis=question.diagnosis or "unknown",
        content_hash=content_hash,
        summary_json={
            "runs": [
                {
                    "run_index": run.run_index,
                    "cited": run.cited,
                    "citation_position": run.citation_position,
                    "mentioned": run.mentioned,
                    "llm_provider": run.llm_provider,
                    "synthetic": run.synthetic,
                    "answer": run.answer_text,
                    "citations": run.citations_json,
                }
                for run in runs
            ],
            "diagnosis": question.diagnosis_json,
            "subqueries": question.subqueries_json,
        },
    )
    session.add(snap)
    session.flush()
    return snap


def _metrics_from_snapshot(snapshot: Snapshot, n_runs: int) -> QuestionMetrics:
    return QuestionMetrics(
        citation_share=snapshot.citation_share,
        mention_rate=snapshot.mention_rate,
        mean_citation_position=snapshot.mean_citation_position,
        n_runs=n_runs,
        cited_runs=0,
        mentioned_runs=0,
    )


def _apply_page_change(session: Session, ab: ABTest, current: Page, runtime: _Runtime) -> str | None:
    current.is_current = False
    new_page = Page(
        id=str(uuid4()),
        site_id=current.site_id,
        simulation_id=current.simulation_id,
        question_id=None,
        url=current.url,
        role="user",
        origin="draft" if ab.change_type == "draft" else "crawl",
        provider_label="user_supplied" if ab.change_type == "draft" else "crawl",
        synthetic=False,
        status="queued",
        is_current=True,
    )
    session.add(new_page)
    session.flush()
    if ab.change_type == "draft":
        if not ab.draft_html:
            raise ConflictError("A draft change requires HTML")
        index_html(session, new_page, ab.draft_html, runtime.embedder)
        new_page.final_url = current.url
        return new_page.content_hash
    if ab.change_type != "recrawl":
        raise ConflictError(f"Unknown change type {ab.change_type}")
    try:
        fetched = runtime.crawler.fetch(current.url)
    except UnsafeURL as exc:
        new_page.status = "error"
        new_page.error = str(exc)
        return None
    new_page.robots_status = fetched.robots_status
    new_page.final_url = fetched.final_url or current.url
    if fetched.blocked or fetched.error or not fetched.html:
        new_page.status = "blocked" if fetched.blocked else "error"
        new_page.error = fetched.error or "empty response"
        return None
    rendered = None
    if runtime.renderer.available:
        try:
            rendered = runtime.renderer.render(new_page.final_url or new_page.url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("JS render failed during recrawl: %s", exc)
    index_html(session, new_page, fetched.html, runtime.embedder, rendered_html=rendered)
    return new_page.content_hash


def _current_user_page(session: Session, site_id: str, url: str) -> Page | None:
    target = normalize_url(url)
    pages = (
        session.query(Page)
        .filter(Page.site_id == site_id, Page.role == "user", Page.is_current.is_(True))
        .all()
    )
    for page in pages:
        try:
            if normalize_url(page.url) == target:
                return page
        except ValueError:
            continue
    return None


def _add_warning(warnings: list[str], message: str) -> None:
    if message and message not in warnings:
        warnings.append(message)
