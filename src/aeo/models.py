"""SQLite and Postgres tables for sites, passages, runs, and calibration."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    url: Mapped[str] = mapped_column(String(2000))
    domain: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    sitemap_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    max_pages: Mapped[int] = mapped_column(Integer, default=20)
    render_js: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    search_provider: Mapped[str] = mapped_column(String(32))
    llm_provider: Mapped[str] = mapped_column(String(32))
    embedding_provider: Mapped[str] = mapped_column(String(32))
    search_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    llm_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    embedding_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_pages: Mapped[int] = mapped_column(Integer, default=20)
    render_js: Mapped[bool] = mapped_column(Boolean, default=False)
    fetch_competitors: Mapped[bool] = mapped_column(Boolean, default=True)
    reuse_existing_pages: Mapped[bool] = mapped_column(Boolean, default=False)
    sitemap_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    simulation_id: Mapped[str] = mapped_column(ForeignKey("simulations.id"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    n_runs: Mapped[int] = mapped_column(Integer, default=5)
    subqueries_json: Mapped[list] = mapped_column(JSON, default=list)
    fanout_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fanout_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    diagnosis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diagnosis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    citation_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    mention_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_citation_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrieval_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"), nullable=True, index=True)
    simulation_id: Mapped[str | None] = mapped_column(ForeignKey("simulations.id"), nullable=True, index=True)
    question_id: Mapped[str | None] = mapped_column(ForeignKey("questions.id"), nullable=True, index=True)
    url: Mapped[str] = mapped_column(String(2000))
    final_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    role: Mapped[str] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(32))
    provider_label: Mapped[str] = mapped_column(String(32), default="crawl")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    h1: Mapped[str | None] = mapped_column(Text, nullable=True)
    headings_json: Mapped[list] = mapped_column(JSON, default=list)
    schema_json: Mapped[list] = mapped_column(JSON, default=list)
    links_json: Mapped[list] = mapped_column(JSON, default=list)
    main_text: Mapped[str] = mapped_column(Text, default="")
    published_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_word_count: Mapped[int] = mapped_column(Integer, default=0)
    js_dependent: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    robots_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[list] = mapped_column(JSON, default=list)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), index=True)
    run_index: Mapped[int] = mapped_column(Integer)
    answer_text: Mapped[str] = mapped_column(Text)
    llm_provider: Mapped[str] = mapped_column(String(32))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    cited: Mapped[bool] = mapped_column(Boolean, default=False)
    citation_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mentioned: Mapped[bool] = mapped_column(Boolean, default=False)
    passages_json: Mapped[list] = mapped_column(JSON, default=list)
    citations_json: Mapped[list] = mapped_column(JSON, default=list)
    unknown_markers_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), index=True)
    label: Mapped[str] = mapped_column(String(32))
    citation_share: Mapped[float] = mapped_column(Float)
    mention_rate: Mapped[float] = mapped_column(Float)
    mean_citation_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    diagnosis: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ABTest(Base):
    __tablename__ = "ab_tests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), index=True)
    page_url: Mapped[str] = mapped_column(String(2000))
    change_type: Mapped[str] = mapped_column(String(32))
    draft_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    before_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshots.id"), nullable=True)
    after_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshots.id"), nullable=True)
    diff_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CalibrationObservation(Base):
    __tablename__ = "calibration_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str | None] = mapped_column(ForeignKey("questions.id"), nullable=True, index=True)
    engine: Mapped[str] = mapped_column(String(32))
    cited: Mapped[bool] = mapped_column(Boolean)
    citation_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mentioned: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    predicted_citation_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_mention_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
