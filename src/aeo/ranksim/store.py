"""SQLite (or Postgres) storage for the focused rank corpus."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import JSON

from aeo.ranksim.keywords import Keyword
from aeo.ranksim.serp import SerpHit


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class RsKeyword(Base):
    __tablename__ = "rs_keywords"

    query: Mapped[str] = mapped_column(String(500), primary_key=True)
    intent: Mapped[str] = mapped_column(String(32))
    cluster: Mapped[str] = mapped_column(String(32))
    suggested_target_page: Mapped[str] = mapped_column(String(2000))
    page_exists: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str] = mapped_column(Text, default="")


class RsSerp(Base):
    __tablename__ = "rs_serp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(500), index=True)
    position: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(32))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    is_google: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RsPage(Base):
    __tablename__ = "rs_pages"

    url: Mapped[str] = mapped_column(String(2000), primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="fetched")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    h1: Mapped[str] = mapped_column(Text, default="")
    headings_text: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    has_faq: Mapped[bool] = mapped_column(Boolean, default=False)
    link_count: Mapped[int] = mapped_column(Integer, default=0)
    fetch_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authority: Mapped[float | None] = mapped_column(Float, nullable=True)
    authority_source: Mapped[str] = mapped_column(String(64), default="unavailable")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RsWeight(Base):
    __tablename__ = "rs_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    weights_json: Mapped[dict] = mapped_column(JSON)
    spearman: Mapped[float | None] = mapped_column(Float, nullable=True)
    ndcg_at_10: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_queries: Mapped[int] = mapped_column(Integer, default=0)
    n_pairs: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(32))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    is_google: Mapped[bool] = mapped_column(Boolean, default=False)
    authority_used: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RsRankCheck(Base):
    __tablename__ = "rs_rank_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(500), index=True)
    checked_on: Mapped[str] = mapped_column(String(10), index=True)
    our_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    our_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    is_google: Mapped[bool] = mapped_column(Boolean, default=False)
    top_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RsTestRun(Base):
    __tablename__ = "rs_test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_label: Mapped[str] = mapped_column(String(2000))
    weight_label: Mapped[str] = mapped_column(String(64))
    synthetic_corpus: Mapped[bool] = mapped_column(Boolean, default=False)
    is_google: Mapped[bool] = mapped_column(Boolean, default=False)
    summary_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_factories: dict[str, sessionmaker] = {}


def database_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    return url


def session_factory(url: str) -> sessionmaker:
    url = database_url(url)
    if url not in _factories:
        engine = create_engine(url, future=True)
        Base.metadata.create_all(engine)
        _factories[url] = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _factories[url]


def open_session(url: str) -> Session:
    return session_factory(url)()


def save_keywords(session: Session, rows: list[Keyword]) -> None:
    for row in rows:
        current = session.get(RsKeyword, row.query)
        if current is None:
            session.add(
                RsKeyword(
                    query=row.query,
                    intent=row.intent,
                    cluster=row.cluster,
                    suggested_target_page=row.suggested_target_page,
                    page_exists=row.page_exists,
                    notes=row.notes,
                )
            )
        else:
            current.intent = row.intent
            current.cluster = row.cluster
            current.suggested_target_page = row.suggested_target_page
            current.page_exists = row.page_exists
            current.notes = row.notes
    session.commit()


def replace_serp(session: Session, query: str, hits: list[SerpHit]) -> None:
    session.query(RsSerp).filter(RsSerp.query == query).delete(synchronize_session=False)
    for hit in hits:
        session.add(
            RsSerp(
                query=query,
                position=hit.position,
                url=hit.url,
                title=hit.title,
                snippet=hit.snippet,
                provider=hit.provider,
                synthetic=hit.synthetic,
                is_google=hit.is_google,
            )
        )
    session.commit()


def upsert_page(session: Session, **fields) -> None:
    url = fields["url"]
    current = session.get(RsPage, url)
    if current is None:
        session.add(RsPage(**fields))
    else:
        for key, value in fields.items():
            setattr(current, key, value)
    session.commit()


def save_weights(session: Session, **fields) -> RsWeight:
    row = RsWeight(**fields)
    session.add(row)
    session.commit()
    return row


def latest_weights(session: Session) -> RsWeight | None:
    return session.scalars(select(RsWeight).order_by(RsWeight.id.desc())).first()


def serp_for_query(session: Session, query: str) -> list[RsSerp]:
    return list(session.scalars(select(RsSerp).where(RsSerp.query == query).order_by(RsSerp.position)).all())


def queries_with_serp(session: Session) -> list[str]:
    return list(session.scalars(select(RsSerp.query).distinct()).all())


def page_by_url(session: Session, url: str) -> RsPage | None:
    return session.get(RsPage, url)


def save_rank_checks(session: Session, rows: list[RsRankCheck]) -> None:
    session.add_all(rows)
    session.commit()


def latest_checks(session: Session) -> list[RsRankCheck]:
    latest = session.scalars(select(RsRankCheck.checked_on).order_by(RsRankCheck.checked_on.desc())).first()
    if not latest:
        return []
    return list(session.scalars(select(RsRankCheck).where(RsRankCheck.checked_on == latest)).all())


def save_test_run(session: Session, **fields) -> RsTestRun:
    row = RsTestRun(**fields)
    session.add(row)
    session.commit()
    return row


def latest_test(session: Session) -> RsTestRun | None:
    return session.scalars(select(RsTestRun).order_by(RsTestRun.id.desc())).first()
