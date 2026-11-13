"""SQLite index plus a parquet copy. Both live under data/index/, outside git."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aeo.ranksim.localindex.parquet import write_parquet
from aeo.ranksim.localindex.records import Attempt, Candidate, PageRecord, attempts_json, outlinks_json, rank_band

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  url TEXT PRIMARY KEY,
  final_url TEXT,
  canonical TEXT,
  title TEXT,
  meta_description TEXT,
  headings TEXT,
  main_text TEXT,
  word_count INTEGER,
  schema_types TEXT,
  content_hash TEXT,
  published TEXT,
  updated TEXT,
  source TEXT,
  captured_on TEXT,
  status TEXT,
  miss_reason TEXT,
  attempts TEXT,
  pagerank REAL,
  domain_rank REAL,
  domain TEXT,
  anchor_text TEXT,
  inlinks TEXT,
  outlinks TEXT,
  table_count INTEGER
);
CREATE TABLE IF NOT EXISTS candidates (
  url TEXT,
  query TEXT,
  position INTEGER,
  source TEXT,
  snapshot_id TEXT,
  rank_band TEXT,
  PRIMARY KEY (url, query, source, snapshot_id)
);
CREATE TABLE IF NOT EXISTS edges (
  src TEXT,
  dst TEXT,
  anchor TEXT,
  PRIMARY KEY (src, dst, anchor)
);
CREATE TABLE IF NOT EXISTS domain_stats (
  domain TEXT PRIMARY KEY,
  pages INTEGER,
  full_text INTEGER,
  outlinks INTEGER,
  inlinks INTEGER,
  mean_pagerank REAL
);
CREATE TABLE IF NOT EXISTS builds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started TEXT,
  finished TEXT,
  mode TEXT,
  credit_cap INTEGER,
  credits_spent INTEGER,
  search_credits INTEGER,
  scrape_credits INTEGER,
  offline INTEGER
);
"""

PAGE_COLUMNS = [
    ("url", "string"),
    ("final_url", "string"),
    ("canonical", "string"),
    ("title", "string"),
    ("meta_description", "string"),
    ("headings", "string"),
    ("main_text", "string"),
    ("word_count", "int"),
    ("schema_types", "string"),
    ("content_hash", "string"),
    ("published", "string"),
    ("updated", "string"),
    ("source", "string"),
    ("captured_on", "string"),
    ("status", "string"),
    ("miss_reason", "string"),
    ("attempts", "string"),
    ("pagerank", "float"),
    ("domain_rank", "float"),
    ("domain", "string"),
    ("anchor_text", "string"),
    ("inlinks", "string"),
    ("outlinks", "string"),
    ("table_count", "int"),
]


class IndexStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "index.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def upsert_page(self, page: PageRecord) -> None:
        self.db.execute(
            """
            INSERT INTO pages (
              url, final_url, canonical, title, meta_description, headings, main_text,
              word_count, schema_types, content_hash, published, updated, source,
              captured_on, status, miss_reason, attempts, pagerank, domain_rank,
              domain, anchor_text, inlinks, outlinks, table_count
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
              final_url=excluded.final_url,
              canonical=excluded.canonical,
              title=excluded.title,
              meta_description=excluded.meta_description,
              headings=excluded.headings,
              main_text=excluded.main_text,
              word_count=excluded.word_count,
              schema_types=excluded.schema_types,
              content_hash=excluded.content_hash,
              published=excluded.published,
              updated=excluded.updated,
              source=excluded.source,
              captured_on=excluded.captured_on,
              status=excluded.status,
              miss_reason=excluded.miss_reason,
              attempts=excluded.attempts,
              pagerank=excluded.pagerank,
              domain_rank=excluded.domain_rank,
              domain=excluded.domain,
              anchor_text=excluded.anchor_text,
              inlinks=excluded.inlinks,
              outlinks=excluded.outlinks,
              table_count=excluded.table_count
            """,
            _page_tuple(page),
        )
        self.db.commit()

    def update_signals(self, page: PageRecord) -> None:
        self.db.execute(
            "UPDATE pages SET pagerank=?, domain_rank=?, anchor_text=?, inlinks=? WHERE url=?",
            (page.pagerank, page.domain_rank, page.anchor_text, json.dumps(page.inlinks), page.url),
        )

    def pages(self) -> dict[str, PageRecord]:
        found: dict[str, PageRecord] = {}
        for row in self.db.execute("SELECT * FROM pages"):
            record = _page_from_row(row)
            found[record.url] = record
        return found

    def upsert_candidate(self, item: Candidate) -> None:
        self.db.execute(
            """
            INSERT INTO candidates (url, query, position, source, snapshot_id, rank_band)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url, query, source, snapshot_id) DO UPDATE SET
              position=excluded.position,
              rank_band=excluded.rank_band
            """,
            (item.url, item.query, item.position, item.source, item.snapshot_id, item.band),
        )
        self.db.commit()

    def candidates(self) -> list[Candidate]:
        rows = []
        for row in self.db.execute("SELECT url, query, position, source, snapshot_id FROM candidates"):
            position = row["position"]
            rows.append(
                Candidate(
                    url=row["url"],
                    query=row["query"],
                    position=None if position is None else int(position),
                    source=row["source"],
                    snapshot_id=row["snapshot_id"] or "",
                )
            )
        return rows

    def replace_edges(self, edges: list[tuple[str, str, str]]) -> None:
        self.db.execute("DELETE FROM edges")
        self.db.executemany("INSERT OR REPLACE INTO edges (src, dst, anchor) VALUES (?, ?, ?)", edges)

    def edges(self) -> list[tuple[str, str, str]]:
        return [(row["src"], row["dst"], row["anchor"]) for row in self.db.execute("SELECT src, dst, anchor FROM edges")]

    def replace_domain_stats(self, rows: list[dict]) -> None:
        self.db.execute("DELETE FROM domain_stats")
        self.db.executemany(
            """
            INSERT INTO domain_stats (domain, pages, full_text, outlinks, inlinks, mean_pagerank)
            VALUES (:domain, :pages, :full_text, :outlinks, :inlinks, :mean_pagerank)
            """,
            rows,
        )

    def domain_stats(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM domain_stats ORDER BY domain")]

    def add_build(self, **fields) -> None:
        self.db.execute(
            """
            INSERT INTO builds (started, finished, mode, credit_cap, credits_spent, search_credits, scrape_credits, offline)
            VALUES (:started, :finished, :mode, :credit_cap, :credits_spent, :search_credits, :scrape_credits, :offline)
            """,
            fields,
        )
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()

    def write_parquet(self) -> None:
        page_rows = []
        for row in self.db.execute("SELECT * FROM pages ORDER BY url"):
            page_rows.append({name: row[name] if row[name] is not None else _empty(kind) for name, kind in PAGE_COLUMNS})
        write_parquet(self.directory / "pages.parquet", PAGE_COLUMNS, page_rows)
        candidate_columns = [
            ("url", "string"),
            ("query", "string"),
            ("position", "int"),
            ("source", "string"),
            ("snapshot_id", "string"),
            ("rank_band", "string"),
        ]
        candidate_rows = []
        for item in self.candidates():
            candidate_rows.append(
                {
                    "url": item.url,
                    "query": item.query,
                    "position": -1 if item.position is None else item.position,
                    "source": item.source,
                    "snapshot_id": item.snapshot_id,
                    "rank_band": rank_band(item.position),
                }
            )
        write_parquet(self.directory / "candidates.parquet", candidate_columns, candidate_rows)
        edge_rows = [{"src": src, "dst": dst, "anchor": anchor} for src, dst, anchor in self.edges()]
        write_parquet(
            self.directory / "edges.parquet",
            [("src", "string"), ("dst", "string"), ("anchor", "string")],
            edge_rows,
        )
        stat_columns = [
            ("domain", "string"),
            ("pages", "int"),
            ("full_text", "int"),
            ("outlinks", "int"),
            ("inlinks", "int"),
            ("mean_pagerank", "float"),
        ]
        write_parquet(self.directory / "domain_stats.parquet", stat_columns, self.domain_stats())

    def write_coverage(self, report: dict) -> Path:
        path = self.directory / "coverage.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return path

    def write_estimate(self, lines: list[str]) -> None:
        (self.directory / "credit_estimate.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _empty(kind: str):
    if kind == "int":
        return 0
    if kind == "float":
        return 0.0
    return ""


def _page_tuple(page: PageRecord) -> tuple:
    return (
        page.url,
        page.final_url,
        page.canonical,
        page.title,
        page.meta_description,
        json.dumps(page.headings, ensure_ascii=False),
        page.main_text,
        page.word_count,
        json.dumps(page.schema_types, ensure_ascii=False),
        page.content_hash,
        page.published or "",
        page.updated or "",
        page.source,
        page.captured_on,
        page.status,
        page.miss_reason,
        attempts_json(page.attempts),
        page.pagerank,
        page.domain_rank,
        page.domain,
        page.anchor_text,
        json.dumps(page.inlinks),
        outlinks_json(page.outlinks),
        page.table_count,
    )


def _page_from_row(row: sqlite3.Row) -> PageRecord:
    headings = _load_list(row["headings"])
    schema_types = _load_list(row["schema_types"])
    inlinks = _load_list(row["inlinks"])
    outlinks = []
    for item in _load_list(row["outlinks"]):
        if isinstance(item, dict):
            outlinks.append((str(item.get("url") or ""), str(item.get("anchor") or "")))
    attempts = []
    for item in _load_list(row["attempts"]):
        if isinstance(item, dict):
            attempts.append(Attempt(str(item.get("method") or ""), bool(item.get("ok")), str(item.get("reason") or "")))
    return PageRecord(
        url=row["url"],
        final_url=row["final_url"] or "",
        canonical=row["canonical"] or "",
        title=row["title"] or "",
        meta_description=row["meta_description"] or "",
        headings=[str(item) for item in headings],
        main_text=row["main_text"] or "",
        word_count=int(row["word_count"] or 0),
        schema_types=[str(item) for item in schema_types],
        outlinks=outlinks,
        content_hash=row["content_hash"] or "",
        published=row["published"] or None,
        updated=row["updated"] or None,
        source=row["source"] or "",
        captured_on=row["captured_on"] or "",
        status=row["status"] or "missing",
        miss_reason=row["miss_reason"] or "",
        attempts=attempts,
        pagerank=float(row["pagerank"] or 0.0),
        domain_rank=float(row["domain_rank"] or 0.0),
        domain=row["domain"] or "",
        anchor_text=row["anchor_text"] or "",
        inlinks=[str(item) for item in inlinks],
        table_count=int(row["table_count"] or 0),
    )


def _load_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []
