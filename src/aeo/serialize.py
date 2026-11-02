"""JSON shapes returned by the API. Raw HTML stays off the default payloads."""

from __future__ import annotations

from sqlalchemy.orm import Session

from aeo.models import ABTest, CalibrationObservation, Job, Page, Question, Run, Simulation, Site


def iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def provider_block(sim: Simulation) -> dict:
    return {
        "search": sim.search_provider,
        "llm": sim.llm_provider,
        "embeddings": sim.embedding_provider,
        "search_is_synthetic": sim.search_synthetic,
        "llm_is_synthetic": sim.llm_synthetic,
        "embeddings_are_synthetic": sim.embedding_synthetic,
        "synthetic": sim.synthetic,
        "answers_are_official_engine_results": False,
        "compared_against_live_web": sim.search_provider == "brave" and not sim.search_synthetic,
    }


def page_summary(page: Page) -> dict:
    return {
        "id": page.id,
        "url": page.url,
        "final_url": page.final_url,
        "role": page.role,
        "origin": page.origin,
        "provider": page.provider_label,
        "synthetic": page.synthetic,
        "status": page.status,
        "error": page.error,
        "title": page.title,
        "h1": page.h1,
        "meta_description": page.meta_description,
        "canonical": page.canonical,
        "word_count": page.word_count,
        "raw_word_count": page.raw_word_count,
        "js_dependent": page.js_dependent,
        "content_hash": page.content_hash,
        "robots_status": page.robots_status,
        "is_current": page.is_current,
        "published": page.published_label,
        "updated": page.updated_label,
        "question_id": page.question_id,
    }


def page_detail(page: Page, *, include_html: bool) -> dict:
    payload = page_summary(page)
    payload.update(
        {
            "headings": page.headings_json or [],
            "schema": page.schema_json or [],
            "links": page.links_json or [],
            "main_text": page.main_text,
        }
    )
    if include_html:
        payload["raw_html"] = page.raw_html
        payload["rendered_html"] = page.rendered_html
    return payload


def run_payload(run: Run) -> dict:
    return {
        "id": run.id,
        "run_index": run.run_index,
        "llm_provider": run.llm_provider,
        "synthetic": run.synthetic,
        "answer": run.answer_text,
        "cited": run.cited,
        "citation_position": run.citation_position,
        "mentioned": run.mentioned,
        "citations": run.citations_json or [],
        "unknown_markers": run.unknown_markers_json or [],
        "passages": run.passages_json or [],
    }


def question_payload(session: Session, question: Question) -> dict:
    runs = session.query(Run).filter(Run.question_id == question.id).order_by(Run.run_index).all()
    competitors = (
        session.query(Page)
        .filter(Page.question_id == question.id, Page.role == "competitor")
        .all()
    )
    diagnosis = question.diagnosis_json or {}
    retrieval = question.retrieval_json or {}
    return {
        "id": question.id,
        "question": question.text,
        "status": question.status,
        "error": question.error,
        "n_runs": question.n_runs,
        "subqueries": question.subqueries_json or [],
        "fanout_provider": question.fanout_provider,
        "fanout_is_synthetic": question.fanout_synthetic,
        "simulated_citation_share": question.citation_share,
        "simulated_mention_rate": question.mention_rate,
        "mean_citation_position": question.mean_citation_position,
        "diagnosis": question.diagnosis,
        "diagnosis_reasons": diagnosis.get("reasons") or [],
        "html_checks": diagnosis.get("html_checks"),
        "best_page_url": diagnosis.get("best_page_url"),
        "signals": diagnosis.get("signals"),
        "top_passages": retrieval.get("fused") or [],
        "retrieval_trace": retrieval.get("subqueries") or [],
        "competitors": [page_summary(page) for page in competitors],
        "runs": [run_payload(run) for run in runs],
    }


def simulation_results(session: Session, sim: Simulation, site: Site) -> dict:
    questions = (
        session.query(Question)
        .filter(Question.simulation_id == sim.id)
        .order_by(Question.position, Question.id)
        .all()
    )
    pages = (
        session.query(Page)
        .filter(Page.site_id == site.id, Page.role == "user")
        .order_by(Page.is_current.desc(), Page.url)
        .all()
    )
    return {
        "id": sim.id,
        "status": sim.status,
        "error": sim.error,
        "job_id": sim.job_id,
        "created_at": iso(sim.created_at),
        "finished_at": iso(sim.finished_at),
        "site": {
            "id": site.id,
            "url": site.url,
            "domain": site.domain,
            "status": site.status,
        },
        "providers": provider_block(sim),
        "warnings": sim.warnings_json or [],
        "questions": [question_payload(session, question) for question in questions],
        "pages": [page_summary(page) for page in pages],
    }


def job_payload(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "attempts": job.attempts,
        "error": job.error,
        "result": job.result_json,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
    }


def ab_payload(session: Session, ab: ABTest) -> dict:
    from aeo.models import Snapshot

    before = session.get(Snapshot, ab.before_snapshot_id) if ab.before_snapshot_id else None
    after = session.get(Snapshot, ab.after_snapshot_id) if ab.after_snapshot_id else None
    return {
        "id": ab.id,
        "question_id": ab.question_id,
        "page_url": ab.page_url,
        "change_type": ab.change_type,
        "status": ab.status,
        "error": ab.error,
        "job_id": ab.job_id,
        "diff": ab.diff_json,
        "before": _snapshot_public(before),
        "after": _snapshot_public(after),
        "created_at": iso(ab.created_at),
        "finished_at": iso(ab.finished_at),
    }


def _snapshot_public(snapshot) -> dict | None:
    if snapshot is None:
        return None
    return {
        "id": snapshot.id,
        "label": snapshot.label,
        "simulated_citation_share": snapshot.citation_share,
        "simulated_mention_rate": snapshot.mention_rate,
        "mean_citation_position": snapshot.mean_citation_position,
        "diagnosis": snapshot.diagnosis,
        "content_hash": snapshot.content_hash,
        "runs": (snapshot.summary_json or {}).get("runs") or [],
    }


def observation_payload(row: CalibrationObservation) -> dict:
    return {
        "id": row.id,
        "question_id": row.question_id,
        "engine": row.engine,
        "cited": row.cited,
        "citation_position": row.citation_position,
        "mentioned": row.mentioned,
        "notes": row.notes,
        "predicted_citation_share": row.predicted_citation_share,
        "predicted_mention_rate": row.predicted_mention_rate,
        "created_at": iso(row.created_at),
    }
