"""Engine setup, full-text tables, and the optional pgvector column."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from aeo.config import Settings
from aeo.models import Base
from aeo.rank.vectors import format_vector_literal

logger = logging.getLogger(__name__)

_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker] = {}
_listener_ready = False


def _register_sqlite_pragmas() -> None:
    global _listener_ready
    if _listener_ready:
        return

    @event.listens_for(Engine, "connect")
    def _on_connect(dbapi_conn, _record) -> None:
        if "sqlite" not in type(dbapi_conn).__module__:
            return
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:
            logger.debug("SQLite WAL mode was not enabled", exc_info=True)
        cursor.close()

    _listener_ready = True


def get_engine(settings: Settings) -> Engine:
    from sqlalchemy import create_engine

    url = settings.database_url
    if url not in _engines:
        _register_sqlite_pragmas()
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
                path = url.removeprefix("sqlite:///")
                if path:
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
        else:
            kwargs["pool_pre_ping"] = True
        _engines[url] = create_engine(url, **kwargs)
    return _engines[url]


def session_factory(settings: Settings) -> sessionmaker:
    if settings.database_url not in _factories:
        engine = get_engine(settings)
        Base.metadata.create_all(engine)
        _prepare_fulltext(engine, settings)
        _factories[settings.database_url] = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _factories[settings.database_url]


def _prepare_fulltext(engine: Engine, settings: Settings) -> None:
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                        body,
                        chunk_id UNINDEXED,
                        tokenize='unicode61'
                    )
                    """
                )
            )
        return
    if engine.dialect.name != "postgresql":
        return
    dim = int(settings.embedding_dim)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text(f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector({dim})"))
        conn.execute(
            text(
                """
                ALTER TABLE chunks
                ADD COLUMN IF NOT EXISTS tsv tsvector
                GENERATED ALWAYS AS (to_tsvector('english', coalesce(body, ''))) STORED
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv)"))


def replace_chunk_index(session: Session, chunk_ids_to_drop: list[str]) -> None:
    if not chunk_ids_to_drop:
        return
    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        return
    for chunk_id in chunk_ids_to_drop:
        session.execute(text("DELETE FROM chunks_fts WHERE chunk_id = :id"), {"id": chunk_id})


def insert_chunk_index(session: Session, *, chunk_id: str, body: str, embedding: list[float]) -> None:
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        session.execute(
            text("INSERT INTO chunks_fts(body, chunk_id) VALUES (:body, :id)"),
            {"body": body, "id": chunk_id},
        )
        return
    if bind.dialect.name == "postgresql" and embedding:
        session.execute(
            text("UPDATE chunks SET embedding = CAST(:vector AS vector) WHERE id = :id"),
            {"vector": format_vector_literal(embedding), "id": chunk_id},
        )


def fts_search(session: Session, query_tokens: list[str], limit: int = 50) -> list[str]:
    """Candidate chunk ids from the stored full-text index. Ranking still uses Okapi BM25."""
    if not query_tokens:
        return []
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        match = " OR ".join(token.replace('"', "") for token in query_tokens[:12])
        rows = session.execute(
            text("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH :match LIMIT :limit"),
            {"match": match, "limit": limit},
        ).all()
        return [row[0] for row in rows]
    if bind.dialect.name == "postgresql":
        match = " | ".join(query_tokens[:12])
        rows = session.execute(
            text(
                """
                SELECT id FROM chunks
                WHERE tsv @@ to_tsquery('english', :match)
                LIMIT :limit
                """
            ),
            {"match": match, "limit": limit},
        ).all()
        return [row[0] for row in rows]
    return []
