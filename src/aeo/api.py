"""REST API. OpenAPI is served at /docs and /openapi.json."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from aeo import __version__
from aeo.config import Settings, get_settings
from aeo.crawl.urls import assert_safe_url, domain_of, same_site
from aeo.db import session_factory
from aeo.errors import AeoError, ConflictError, NotFoundError, ProviderConfigError, UnsafeURL
from aeo.jobs import drain, enqueue
from aeo.models import ABTest, CalibrationObservation, Job, Page, Question, Simulation, Site
from aeo.providers.factory import resolve_provider_names, synthetic_flags
from aeo.serialize import (
    ab_payload,
    job_payload,
    observation_payload,
    page_detail,
    page_summary,
    simulation_results,
)
from aeo.sim.calibrate import calibration_report, normalize_engine
from aeo.sim.calibrate import Observation as ScoreObservation

router = APIRouter()


class SeedPageIn(BaseModel):
    url: str
    html: str = Field(min_length=1, max_length=1_500_000)


def _clean_questions(value: list[str]) -> list[str]:
    cleaned = []
    for question in value:
        text = " ".join(question.split())
        if not text:
            raise ValueError("Questions cannot be empty")
        if len(text) > 500:
            raise ValueError("Questions must be 500 characters or fewer")
        cleaned.append(text)
    return cleaned


class SimulationIn(BaseModel):
    site_url: str
    sitemap_url: str | None = None
    pages: list[SeedPageIn] = Field(default_factory=list, max_length=50)
    questions: list[str] = Field(min_length=1, max_length=10)
    n_runs: int = Field(default=5, ge=1, le=10)
    max_pages: int = Field(default=20, ge=1, le=50)
    render_js: bool = False
    fetch_competitors: bool = True
    search_provider: str | None = None
    llm_provider: str | None = None
    embedding_provider: str | None = None

    @field_validator("questions")
    @classmethod
    def clean_questions(cls, value: list[str]) -> list[str]:
        return _clean_questions(value)


class SiteIn(BaseModel):
    url: str
    sitemap_url: str | None = None
    pages: list[SeedPageIn] = Field(default_factory=list, max_length=50)
    max_pages: int = Field(default=20, ge=1, le=50)
    render_js: bool = False


class QuestionsIn(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=10)
    n_runs: int = Field(default=5, ge=1, le=10)
    fetch_competitors: bool = True
    search_provider: str | None = None
    llm_provider: str | None = None
    embedding_provider: str | None = None

    @field_validator("questions")
    @classmethod
    def clean_questions(cls, value: list[str]) -> list[str]:
        return _clean_questions(value)


class ABIn(BaseModel):
    change_type: str
    page_url: str
    html: str | None = Field(default=None, max_length=1_500_000)

    @model_validator(mode="after")
    def draft_needs_html(self) -> "ABIn":
        if self.change_type not in {"draft", "recrawl"}:
            raise ValueError("change_type must be draft or recrawl")
        if self.change_type == "draft" and not (self.html and self.html.strip()):
            raise ValueError("html is required when change_type is draft")
        return self


class CalibrationIn(BaseModel):
    engine: str
    cited: bool
    mentioned: bool
    citation_position: int | None = Field(default=None, ge=1, le=20)
    question_id: str | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def position_matches_cited(self) -> "CalibrationIn":
        if not self.cited and self.citation_position is not None:
            raise ValueError("citation_position must be empty when cited is false")
        return self


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="AEO Simulator",
        version=__version__,
        description=(
            "Sandbox search and answer-engine simulator. Citation share is the fraction of "
            "simulated answers that cited the submitted domain. Mock providers are labeled. "
            "Results are never official ChatGPT, Perplexity, or Google AI Overview output."
        ),
    )
    app.state.settings = settings
    app.state.session_factory = session_factory(settings)
    app.include_router(router)

    @app.exception_handler(ProviderConfigError)
    async def _provider(_request: Request, exc: ProviderConfigError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(NotFoundError)
    async def _missing(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict(_request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(UnsafeURL)
    async def _unsafe(_request: Request, exc: UnsafeURL) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(AeoError)
    async def _aeo(_request: Request, exc: AeoError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


def get_db(request: Request):
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def _prepare_url(value: str | None, settings: Settings) -> str | None:
    if value is None:
        return None
    try:
        return assert_safe_url(value, allow_private=settings.crawl_allow_private, resolve=False)
    except UnsafeURL:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _providers(settings: Settings, search: str | None, llm: str | None, embedding: str | None):
    try:
        return resolve_provider_names(settings, search=search, llm=llm, embedding=embedding)
    except ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _seed_pages(session: Session, site: Site, simulation: Simulation | None, pages: list[SeedPageIn], settings: Settings) -> None:
    for seed in pages:
        url = _prepare_url(seed.url, settings)
        assert url is not None
        if not same_site(url, site.url):
            raise HTTPException(status_code=422, detail=f"{url} is not on {site.domain}")
        session.add(
            Page(
                id=str(uuid4()),
                site_id=site.id,
                simulation_id=simulation.id if simulation else None,
                url=url,
                final_url=url,
                role="user",
                origin="seed",
                provider_label="user_supplied",
                synthetic=False,
                status="seeded",
                is_current=True,
                raw_html=seed.html,
                robots_status="not_fetched",
            )
        )


def _new_simulation(
    session: Session,
    settings: Settings,
    *,
    site: Site,
    questions: list[str],
    n_runs: int,
    fetch_competitors: bool,
    search: str | None,
    llm: str | None,
    embedding: str | None,
    reuse_existing_pages: bool,
    sitemap_url: str | None,
    max_pages: int,
    render_js: bool,
) -> Simulation:
    search_name, llm_name, embedding_name, warnings = _providers(settings, search, llm, embedding)
    search_synthetic, llm_synthetic, embedding_synthetic, synthetic = synthetic_flags(
        search_name, llm_name, embedding_name
    )
    sim = Simulation(
        id=str(uuid4()),
        site_id=site.id,
        status="queued",
        search_provider=search_name,
        llm_provider=llm_name,
        embedding_provider=embedding_name,
        search_synthetic=search_synthetic,
        llm_synthetic=llm_synthetic,
        embedding_synthetic=embedding_synthetic,
        synthetic=synthetic,
        warnings_json=warnings,
        max_pages=max_pages,
        render_js=render_js,
        fetch_competitors=fetch_competitors,
        reuse_existing_pages=reuse_existing_pages,
        sitemap_url=sitemap_url,
    )
    session.add(sim)
    session.flush()
    for position, text in enumerate(questions):
        session.add(
            Question(
                id=str(uuid4()),
                simulation_id=sim.id,
                site_id=site.id,
                position=position,
                text=text,
                n_runs=n_runs,
                status="queued",
            )
        )
    job = enqueue(session, "simulation", {"simulation_id": sim.id})
    sim.job_id = job.id
    return sim


def _finish(session: Session, settings: Settings) -> None:
    session.commit()
    if settings.process_jobs_inline:
        drain(session, settings)


@router.get("/health", tags=["meta"])
def health(request: Request) -> dict:
    settings = get_app_settings(request)
    dialect = "unknown"
    try:
        factory = request.app.state.session_factory
        session = factory()
        try:
            dialect = session.get_bind().dialect.name
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "service": "aeo-simulator", "error": str(exc)}
    return {"status": "ok", "service": "aeo-simulator", "version": __version__, "database": dialect}


@router.get("/", tags=["meta"])
def root() -> dict:
    return {
        "service": "aeo-simulator",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
    }


@router.get("/v1/providers", tags=["meta"])
def providers(settings: Settings = Depends(get_app_settings)) -> dict:
    try:
        search_name, llm_name, embedding_name, warnings = resolve_provider_names(settings)
        error = None
    except ProviderConfigError as exc:
        search_name = settings.search_provider
        llm_name = settings.llm_provider
        embedding_name = settings.embedding_provider
        warnings = []
        error = str(exc)
    search_synthetic, llm_synthetic, embedding_synthetic, synthetic = synthetic_flags(
        search_name if search_name in {"brave", "mock", "none"} else "mock",
        llm_name if llm_name in {"openai", "mock"} else "mock",
        embedding_name if embedding_name in {"openai", "mock"} else "mock",
    )
    return {
        "search_provider": search_name,
        "llm_provider": llm_name,
        "embedding_provider": embedding_name,
        "search_is_synthetic": search_synthetic,
        "llm_is_synthetic": llm_synthetic,
        "embeddings_are_synthetic": embedding_synthetic,
        "synthetic": synthetic,
        "brave_api_key_configured": bool(settings.brave_api_key),
        "openai_api_key_configured": bool(settings.openai_api_key),
        "embedding_dim": settings.embedding_dim,
        "answers_are_official_engine_results": False,
        "warnings": warnings,
        "error": error,
    }


@router.post("/v1/simulations", status_code=201, tags=["simulations"])
def create_simulation(
    body: SimulationIn,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    site_url = _prepare_url(body.site_url, settings)
    sitemap_url = _prepare_url(body.sitemap_url, settings)
    assert site_url is not None
    site = Site(
        id=str(uuid4()),
        url=site_url,
        domain=domain_of(site_url),
        status="queued",
        sitemap_url=sitemap_url,
        max_pages=body.max_pages,
        render_js=body.render_js,
    )
    session.add(site)
    session.flush()
    sim = _new_simulation(
        session,
        settings,
        site=site,
        questions=body.questions,
        n_runs=body.n_runs,
        fetch_competitors=body.fetch_competitors,
        search=body.search_provider,
        llm=body.llm_provider,
        embedding=body.embedding_provider,
        reuse_existing_pages=False,
        sitemap_url=sitemap_url,
        max_pages=body.max_pages,
        render_js=body.render_js,
    )
    _seed_pages(session, site, sim, body.pages, settings)
    _finish(session, settings)
    session.refresh(sim)
    session.refresh(site)
    return simulation_results(session, sim, site)


@router.post("/v1/sites", status_code=201, tags=["sites"])
def create_site(
    body: SiteIn,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    site_url = _prepare_url(body.url, settings)
    sitemap_url = _prepare_url(body.sitemap_url, settings)
    assert site_url is not None
    site = Site(
        id=str(uuid4()),
        url=site_url,
        domain=domain_of(site_url),
        status="queued",
        sitemap_url=sitemap_url,
        max_pages=body.max_pages,
        render_js=body.render_js,
    )
    session.add(site)
    session.flush()
    _seed_pages(session, site, None, body.pages, settings)
    job = enqueue(session, "index_site", {"site_id": site.id})
    _finish(session, settings)
    session.refresh(site)
    pages = session.query(Page).filter(Page.site_id == site.id).all()
    return {
        "id": site.id,
        "url": site.url,
        "domain": site.domain,
        "status": site.status,
        "error": site.error,
        "warnings": site.warnings_json or [],
        "job_id": job.id,
        "pages": [page_summary(page) for page in pages],
    }


@router.get("/v1/sites/{site_id}", tags=["sites"])
def get_site(site_id: str, session: Session = Depends(get_db)) -> dict:
    site = session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    pages = session.query(Page).filter(Page.site_id == site.id).all()
    return {
        "id": site.id,
        "url": site.url,
        "domain": site.domain,
        "status": site.status,
        "error": site.error,
        "warnings": site.warnings_json or [],
        "pages": [page_summary(page) for page in pages],
    }


@router.post("/v1/sites/{site_id}/questions", status_code=201, tags=["sites"])
def add_questions(
    site_id: str,
    body: QuestionsIn,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    site = session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    if site.status != "ready":
        raise HTTPException(status_code=409, detail=f"Site is {site.status}; wait until it is ready")
    sim = _new_simulation(
        session,
        settings,
        site=site,
        questions=body.questions,
        n_runs=body.n_runs,
        fetch_competitors=body.fetch_competitors,
        search=body.search_provider,
        llm=body.llm_provider,
        embedding=body.embedding_provider,
        reuse_existing_pages=True,
        sitemap_url=site.sitemap_url,
        max_pages=site.max_pages,
        render_js=site.render_js,
    )
    _finish(session, settings)
    session.refresh(sim)
    session.refresh(site)
    return simulation_results(session, sim, site)


@router.get("/v1/simulations/{simulation_id}", tags=["simulations"])
def get_simulation(simulation_id: str, session: Session = Depends(get_db)) -> dict:
    sim = session.get(Simulation, simulation_id)
    if sim is None:
        raise HTTPException(status_code=404, detail="Simulation not found")
    site = session.get(Site, sim.site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return simulation_results(session, sim, site)


@router.get("/v1/simulations/{simulation_id}/results", tags=["simulations"])
def get_results(simulation_id: str, session: Session = Depends(get_db)) -> dict:
    return get_simulation(simulation_id, session)


@router.get("/v1/pages/{page_id}", tags=["pages"])
def get_page(
    page_id: str,
    include_html: bool = Query(default=False),
    session: Session = Depends(get_db),
) -> dict:
    page = session.get(Page, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return page_detail(page, include_html=include_html)


@router.post("/v1/questions/{question_id}/ab", status_code=201, tags=["ab"])
def create_ab(
    question_id: str,
    body: ABIn,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    question = session.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    if question.status != "completed":
        raise HTTPException(status_code=409, detail="Question runs are not complete yet")
    page_url = _prepare_url(body.page_url, settings)
    assert page_url is not None
    ab = ABTest(
        id=str(uuid4()),
        question_id=question.id,
        page_url=page_url,
        change_type=body.change_type,
        draft_html=body.html,
        status="queued",
    )
    session.add(ab)
    session.flush()
    job = enqueue(session, "ab_test", {"ab_test_id": ab.id})
    ab.job_id = job.id
    _finish(session, settings)
    session.refresh(ab)
    return ab_payload(session, ab)


@router.get("/v1/ab/{ab_test_id}", tags=["ab"])
def get_ab(ab_test_id: str, session: Session = Depends(get_db)) -> dict:
    ab = session.get(ABTest, ab_test_id)
    if ab is None:
        raise HTTPException(status_code=404, detail="A/B test not found")
    return ab_payload(session, ab)


@router.post("/v1/calibration", status_code=201, tags=["calibration"])
def log_observation(body: CalibrationIn, session: Session = Depends(get_db)) -> dict:
    try:
        engine = normalize_engine(body.engine)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    predicted_share = None
    predicted_mention = None
    if body.question_id:
        question = session.get(Question, body.question_id)
        if question is None:
            raise HTTPException(status_code=404, detail="Question not found")
        predicted_share = question.citation_share
        predicted_mention = question.mention_rate
    row = CalibrationObservation(
        id=str(uuid4()),
        question_id=body.question_id,
        engine=engine,
        cited=body.cited,
        citation_position=body.citation_position,
        mentioned=body.mentioned,
        notes=body.notes,
        predicted_citation_share=predicted_share,
        predicted_mention_rate=predicted_mention,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return observation_payload(row)


@router.get("/v1/calibration/accuracy", tags=["calibration"])
def accuracy(
    engine: str | None = None,
    session: Session = Depends(get_db),
) -> dict:
    query = session.query(CalibrationObservation)
    if engine:
        try:
            engine = normalize_engine(engine)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        query = query.filter(CalibrationObservation.engine == engine)
    rows = query.all()
    report = calibration_report(
        [
            ScoreObservation(
                engine=row.engine,
                cited=row.cited,
                predicted_citation_share=row.predicted_citation_share,
            )
            for row in rows
        ]
    )
    report["engine"] = engine
    return report


@router.get("/v1/jobs/{job_id}", tags=["jobs"])
def get_job(job_id: str, session: Session = Depends(get_db)) -> dict:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_payload(job)
