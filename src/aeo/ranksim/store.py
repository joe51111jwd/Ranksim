"""SQLite (or Postgres) storage for the focused rank corpus."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import UniqueConstraint, Boolean, DateTime, Float, Integer, String, Text, create_engine, select
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
    # The collection this row belongs to. None: a row from before collections (one per query, as
    # the old code replaced the list on every collect).
    collection_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class RsSerpCollection(Base):
    """One collected results list for one query. Collections are appended, never replaced, so two
    collections of a query are both kept. Locale and device are what the request asked for; None
    means the request did not say (the provider's default applied)."""

    __tablename__ = "rs_serp_collections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    query: Mapped[str] = mapped_column(String(500), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    is_google: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32), default="live")  # live or label_file
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    country: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    device: Mapped[str | None] = mapped_column(String(16), nullable=True)
    requested_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    signature: Mapped[str] = mapped_column(String(64), default="")
    # False: a label file with no date. collected_at then holds the import time, which is not
    # when the list was observed. None: a row from before this column (see collection_dated).
    dated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class RsFinalRun(Base):
    """A model configuration scored on a frozen final split. Kept in the main database, keyed by the
    split itself (``snapshot.final_key``: the final queries and their parsed labels), so re-freezing
    the same split into a new snapshot directory does not allow a second final run of the same
    configuration. Older rows keep the key they were written with and are still looked up."""

    __tablename__ = "rs_final_runs"
    __table_args__ = (UniqueConstraint("final_key", "model_id", name="uq_final_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    final_key: Mapped[str] = mapped_column(String(64), index=True)
    model_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    snapshot_dir: Mapped[str] = mapped_column(String(1000), default="")
    summary: Mapped[dict] = mapped_column(JSON)


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


def append_serp(
    session: Session,
    query: str,
    hits: list[SerpHit],
    *,
    provider: str | None = None,
    synthetic: bool | None = None,
    is_google: bool | None = None,
    country: str | None = None,
    language: str | None = None,
    device: str | None = None,
    requested_depth: int | None = None,
    collected_at: datetime | None = None,
    source: str = "live",
) -> str | None:
    """Store one collected list as a new collection; earlier collections stay. Returns its id, or
    None when the query already has this exact collection, at any point in its history (not only
    as its latest one). A live collect is its own observation unless its collection time is the
    same, so a re-collect that found the same list is kept. A label file matches on its date, or
    on its list alone when it has no date, so re-importing the same file adds nothing on any day,
    even after a live collect of the query. An empty list is a real observation and is kept."""
    import uuid

    first = hits[0] if hits else None
    provider = provider or (first.provider if first else "unknown")
    synthetic = bool(first.synthetic if synthetic is None and first else synthetic)
    is_google = bool(first.is_google if is_google is None and first else is_google)
    dated = collected_at is not None or source == "live"
    collected_at = collected_at or utcnow()
    if source == "live":
        when = collected_at.isoformat()
    elif dated:
        when = collected_at.date().isoformat()
    else:
        when = "undated"
    body = "|".join(f"{hit.position}:{hit.url}" for hit in sorted(hits, key=lambda item: item.position))
    signature = _collection_signature(source, provider, when, body)
    seen = session.query(RsSerpCollection).filter(
        RsSerpCollection.query == query, RsSerpCollection.signature == signature
    ).first()
    if seen is None and not dated:
        # A database from before the dated column signed an undated import with its import day.
        legacy = session.query(RsSerpCollection).filter(
            RsSerpCollection.query == query,
            RsSerpCollection.source == source,
            RsSerpCollection.provider == provider,
            RsSerpCollection.dated.is_(None),
        )
        seen = next(
            (row for row in legacy if _collection_signature(source, provider, row.collected_at.date().isoformat(), body) == row.signature),
            None,
        )
    if seen is not None:
        if seen.dated is None and source != "live":
            seen.dated = dated  # a re-import now says whether that older row carried a date
            session.commit()
        return None
    collection_id = uuid.uuid4().hex
    session.add(
        RsSerpCollection(
            id=collection_id,
            query=query,
            provider=provider,
            synthetic=synthetic,
            is_google=is_google,
            source=source,
            collected_at=collected_at,
            country=country,
            language=language,
            device=device,
            requested_depth=requested_depth,
            hits=len(hits),
            signature=signature,
            dated=dated,
        )
    )
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
                fetched_at=collected_at,
                collection_id=collection_id,
            )
        )
    session.commit()
    return collection_id


def replace_serp(session: Session, query: str, hits: list[SerpHit]) -> None:
    """Kept for callers on other branches. It no longer deletes: it appends a collection."""
    append_serp(session, query, hits)


def _collection_signature(source: str, provider: str, when: str, body: str) -> str:
    import hashlib

    return hashlib.sha256(f"{source}|{provider}|{when}|{body}".encode()).hexdigest()


def latest_collection(session: Session, query: str) -> RsSerpCollection | None:
    """The query's latest collection (see pick_latest)."""
    return pick_latest(session.query(RsSerpCollection).filter(RsSerpCollection.query == query).all())


def pick_latest(collections: list[RsSerpCollection]) -> RsSerpCollection | None:
    """The latest of one query's collections, by collection time.

    A label file with no date was stored at its import time, which says nothing about when its
    list was observed. So it is the latest only when the query has no dated, non-synthetic
    collection. Importing an old undated file after a live collect does not replace the live list.
    """
    if any(collection_dated(c) and not c.synthetic for c in collections):
        collections = [c for c in collections if collection_dated(c)]
    if not collections:
        return None
    return max(collections, key=lambda c: (c.collected_at, c.recorded_at or c.collected_at, c.id))


def serp_collections(session: Session, query: str | None = None, *, include_synthetic: bool = False) -> list[RsSerpCollection]:
    rows = session.query(RsSerpCollection)
    if query is not None:
        rows = rows.filter(RsSerpCollection.query == query)
    if not include_synthetic:
        rows = rows.filter(RsSerpCollection.synthetic.is_(False))
    return list(rows.order_by(RsSerpCollection.query, RsSerpCollection.collected_at, RsSerpCollection.recorded_at).all())


def collection_dated(collection: RsSerpCollection) -> bool:
    """True when collected_at is when the list was observed. A live collect always is. A label
    file is only when it carried a date; an older label_file row that did not record this is
    treated as undated."""
    if collection.dated is not None:
        return bool(collection.dated)
    return collection.source == "live"


def collection_hits(session: Session, collection_id: str) -> list[RsSerp]:
    return list(session.scalars(select(RsSerp).where(RsSerp.collection_id == collection_id).order_by(RsSerp.position)).all())


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
    """The query's latest collected list (older collections are kept; see serp_collections)."""
    latest = latest_collection(session, query)
    if latest is not None:
        return collection_hits(session, latest.id)
    return list(
        session.scalars(
            select(RsSerp).where(RsSerp.query == query, RsSerp.collection_id.is_(None)).order_by(RsSerp.position)
        ).all()
    )


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
