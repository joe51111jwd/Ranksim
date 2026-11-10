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
    # Provenance. `source` says where the stored HTML came from: crawl, saved_html (a file a
    # person saved, capture date unknown), draft_bundle (unpublished draft HTML stored under a
    # live URL; never used as an evaluation example), graph_crawl, or unknown (rows written
    # before this column existed). The last attempt fields record a failed refetch without
    # erasing the last good snapshot.
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, default="crawl")
    html_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Where redirects ended when this page was fetched (None: not fetched by the crawler, or a row
    # from before this column) and the page's own rel=canonical (None: none declared).
    final_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Client-rendered shell (aeo.ranksim.jsshell). None: not checked yet (older rows).
    js_shell: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_attempt_error: Mapped[str | None] = mapped_column(Text, nullable=True)


PAGE_SOURCES = ("crawl", "saved_html", "draft_bundle", "graph_crawl", "unknown")
EVALUATION_EXCLUDED_SOURCES = frozenset({"draft_bundle"})


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
    # "ok" or "error". NULL on rows written before this column existed.
    status: Mapped[str | None] = mapped_column(String(16), nullable=True, default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
        from aeo.ranksim import ledger  # noqa: F401  (registers the ledger tables on Base)

        engine = create_engine(url, future=True)
        Base.metadata.create_all(engine)
        _add_missing_columns(engine)
        _factories[url] = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _factories[url]


def open_session(url: str) -> Session:
    return session_factory(url)()


def close_database(url: str) -> None:
    """Dispose of a cached engine, e.g. for a temporary database that is about to be deleted."""
    factory = _factories.pop(database_url(url), None)
    if factory is not None:
        factory.kw["bind"].dispose()


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


def _add_missing_columns(engine) -> None:
    """Additive migration: add columns that newer code declares to tables an older version made.

    create_all creates missing tables but never alters existing ones, so a database from an
    earlier checkout would fail on the new provenance columns. Nullable columns are added as
    NULL, which the code reads as source "unknown". A NOT NULL column is added only when it has a
    server default (the ledger's project and file columns, added as ``DEFAULT ''`` exactly as the
    integration branch does), and older rows get that default.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            default = column.server_default
            if not column.nullable and default is None:
                continue
            ddl = column.type.compile(dialect=engine.dialect)
            if default is not None:
                arg = getattr(default, "arg", "")
                ddl += " DEFAULT '" + arg.replace("'", "''") + "'" if isinstance(arg, str) else f" DEFAULT {arg}"
            with engine.begin() as connection:
                connection.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {ddl}'))


def upsert_page(session: Session, **fields) -> None:
    """Store a page. A failed fetch never erases an earlier good snapshot.

    A good fetch (status "fetched" with HTML) replaces the stored snapshot and stamps
    fetched_at and html_sha256. A failed or empty fetch of a URL that already has a good
    snapshot only records last_attempt_*; the old HTML stays and keeps its own fetched_at.
    """
    import hashlib

    url = fields["url"]
    now = utcnow()
    good = fields.get("status") == "fetched" and bool((fields.get("raw_html") or "").strip())
    fields.setdefault("last_attempt_at", now)
    fields.setdefault("last_attempt_status", fields.get("status"))
    fields.setdefault("last_attempt_error", fields.get("error"))
    if good:
        fields.setdefault("fetched_at", now)
        fields["html_sha256"] = hashlib.sha256(fields["raw_html"].encode("utf-8", "replace")).hexdigest()
    current = session.get(RsPage, url)
    if current is None:
        session.add(RsPage(**fields))
    elif good or not (current.status == "fetched" and (current.raw_html or "").strip()):
        for key, value in fields.items():
            setattr(current, key, value)
    else:
        current.last_attempt_at = fields["last_attempt_at"]
        current.last_attempt_status = fields["last_attempt_status"]
        current.last_attempt_error = fields["last_attempt_error"]
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
    """Find a stored page by URL, tolerating the trailing-slash and normalization variants.

    Pages are stored under the URL they were labeled or fetched with (90 of 135 SmartCrew label
    URLs end in "/"), while some callers look them up by normalize_url, which strips the slash.
    An exact-key lookup alone missed most pages.
    """
    found = session.get(RsPage, url)
    if found is not None:
        return found
    from aeo.crawl.urls import normalize_url

    try:
        candidates = [normalize_url(url), normalize_url(url, keep_trailing_slash=True)]
    except ValueError:
        return None
    candidates += [candidate + "/" for candidate in candidates if not candidate.endswith("/")]
    for candidate in dict.fromkeys(candidates):
        if candidate == url:
            continue
        found = session.get(RsPage, candidate)
        if found is not None:
            return found
    return None


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
