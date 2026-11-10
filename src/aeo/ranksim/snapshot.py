"""Frozen benchmark snapshots: the labels, every stored page, and a fixed dev/final split.

A benchmark run on a live database changes whenever pages are recrawled or unlabeled pages are
added (in-corpus PageRank and anchors use every stored page). A snapshot pins all of it:

- ``labels.csv``: a byte copy of the label file, with its sha256 in the manifest.
- ``pages.jsonl.gz``: every stored page with its HTML, source, fetch time and HTML sha256.
- ``manifest.json``: hashes, counts, ``as_of``, the intent of each labeled query, and the split.
- ``graph.json``: optional copy of a link-graph file, with its sha256 in the manifest.
- ``results.jsonl``: append-only log of final-split runs (see ``record_final``).

The split assigns whole query clusters (URL-overlap clusters from the label file) to ``dev`` or
``final`` from a hash of each cluster's queries and a seed, so it is deterministic and never puts
near-duplicate queries on both sides. Development work reads only ``dev``; the ``final`` split is
scored once per model configuration and the result is appended, never replaced.

Snapshots hold third-party page HTML. Keep them out of git and do not publish them.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_VERSION = "1"
MANIFEST = "manifest.json"
LABELS = "labels.csv"
PAGES = "pages.jsonl.gz"
GRAPH = "graph.json"
RESULTS = "results.jsonl"


class SnapshotError(Exception):
    pass


def split_clusters(clusters: dict[str, int], *, final_share: float, seed: int) -> tuple[set[str], set[str]]:
    """(dev queries, final queries). Whole clusters only, at least one on each side.

    Clusters are taken in the order of a seeded hash of their queries and added to ``final``
    until it holds ``final_share`` of the queries, so a few large clusters cannot take most of
    the label file.
    """
    groups: dict[int, list[str]] = {}
    for query, cluster in clusters.items():
        groups.setdefault(cluster, []).append(query)
    if len(groups) < 2:
        raise SnapshotError("Need at least 2 query clusters to hold one out as a final test set.")
    ranked = sorted(
        groups.values(),
        key=lambda queries: hashlib.sha256(f"{seed}|{'|'.join(sorted(queries))}".encode()).hexdigest(),
    )
    target = max(1, math.ceil(final_share * len(clusters)))
    final: set[str] = set()
    dev: set[str] = set()
    for index, queries in enumerate(ranked):
        must_leave_dev = index == len(ranked) - 1 and not dev
        if len(final) < target and not must_leave_dev and (not final or len(final) + len(queries) <= target):
            final.update(queries)
        else:
            dev.update(queries)
    return dev, final


def freeze(
    database: str,
    serp_file: str | Path,
    out_dir: str | Path,
    *,
    as_of: str,
    final_share: float = 0.3,
    seed: int = 0,
    cluster_threshold: float = 0.3,
    intents: dict[str, str] | None = None,
    graph_file: str | Path | None = None,
) -> dict:
    from aeo.ranksim.benchmark import corpus_fingerprint, file_sha256, query_clusters
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.store import RsPage, open_session

    out = Path(out_dir)
    if (out / MANIFEST).exists():
        raise SnapshotError(f"{out} already holds a snapshot. Freeze into a new directory; snapshots are not overwritten.")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(serp_file, out / LABELS)
    labels = load_serp_file(out / LABELS)
    if graph_file:
        shutil.copyfile(graph_file, out / GRAPH)
    clusters = query_clusters(labels, cluster_threshold)
    dev, final = split_clusters(clusters, final_share=final_share, seed=seed)
    session = open_session(database)
    stored = []
    try:
        rows = session.query(RsPage).order_by(RsPage.url).all()
        with gzip.open(out / PAGES, "wt", encoding="utf-8") as handle:
            for row in rows:
                record = {
                    "url": row.url,
                    "status": row.status,
                    "error": row.error,
                    "source": row.source or "unknown",
                    "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
                    "fetch_ms": row.fetch_ms,
                    "final_url": row.final_url,
                    "authority": row.authority,
                    "authority_source": row.authority_source,
                    "html_sha256": hashlib.sha256((row.raw_html or "").encode("utf-8", "replace")).hexdigest(),
                    "raw_html": row.raw_html or "",
                }
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                if row.status == "fetched":
                    stored.append((row.url, row.raw_html or ""))
    finally:
        session.close()
    manifest = {
        "snapshot_version": SNAPSHOT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of,
        "labels_sha256": file_sha256(out / LABELS),
        "labels_rows": len(labels),
        "label_source_file": str(serp_file),
        "pages": len(rows),
        "pages_sha256": file_sha256(out / PAGES),
        "corpus_sha256": corpus_fingerprint(stored),
        "graph_sha256": file_sha256(out / GRAPH) if graph_file else None,
        "intents": {query: (intents or {}).get(query, "unknown") for query in sorted({row.query for row in labels})},
        "split": {
            "seed": seed,
            "final_share": final_share,
            "cluster_threshold": cluster_threshold,
            "clusters": len(set(clusters.values())),
            "dev_queries": sorted(dev),
            "final_queries": sorted(final),
        },
        "notice": "Contains third-party page HTML. Keep out of git; do not publish.",
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def load_manifest(snapshot_dir: str | Path) -> dict:
    from aeo.ranksim.benchmark import file_sha256

    folder = Path(snapshot_dir)
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"No readable {MANIFEST} in {folder}: {exc}") from None
    if file_sha256(folder / LABELS) != manifest.get("labels_sha256"):
        raise SnapshotError("labels.csv does not match the manifest hash; the snapshot was changed.")
    if file_sha256(folder / PAGES) != manifest.get("pages_sha256"):
        raise SnapshotError("pages.jsonl.gz does not match the manifest hash; the snapshot was changed.")
    if manifest.get("graph_sha256") and file_sha256(folder / GRAPH) != manifest["graph_sha256"]:
        raise SnapshotError("graph.json does not match the manifest hash; the snapshot was changed.")
    if manifest.get("snapshot_version") != SNAPSHOT_VERSION:
        raise SnapshotError(f"Snapshot version {manifest.get('snapshot_version')!r} is not {SNAPSHOT_VERSION!r}.")
    return manifest


def materialize(snapshot_dir: str | Path, database: str) -> dict:
    """Load a snapshot's pages into an empty database. Derived fields are re-extracted from the
    stored HTML with the current extractor; HTML, source and fetch time are kept as frozen."""
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import RsPage, open_session

    manifest = load_manifest(snapshot_dir)
    session = open_session(database)
    try:
        if session.query(RsPage).count():
            raise SnapshotError("Materialize into an empty database.")
        with gzip.open(Path(snapshot_dir) / PAGES, "rt", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                html = record.get("raw_html") or ""
                if hashlib.sha256(html.encode("utf-8", "replace")).hexdigest() != record.get("html_sha256"):
                    raise SnapshotError(f"Page {record.get('url')} does not match its stored hash.")
                store_fetched_page(
                    session,
                    FetchResult(
                        url=record["url"],
                        html=html if record.get("status") == "fetched" else "",
                        status=record.get("status") or "error",
                        error=record.get("error"),
                        fetch_ms=record.get("fetch_ms"),
                        source=record.get("source") or "unknown",
                        final_url=record.get("final_url"),
                    ),
                )
                row = session.get(RsPage, record["url"])
                if row is not None:
                    if record.get("fetched_at"):
                        row.fetched_at = datetime.fromisoformat(record["fetched_at"])
                    row.authority = record.get("authority")
                    row.authority_source = record.get("authority_source") or "unavailable"
                    session.commit()
    finally:
        session.close()
    return manifest


def labels_path(snapshot_dir: str | Path) -> Path:
    return Path(snapshot_dir) / LABELS


def graph_path(snapshot_dir: str | Path, manifest: dict) -> Path | None:
    return Path(snapshot_dir) / GRAPH if manifest.get("graph_sha256") else None


def final_runs(snapshot_dir: str | Path) -> list[dict]:
    path = Path(snapshot_dir) / RESULTS
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def record_final(snapshot_dir: str | Path, entry: dict) -> None:
    """Append one final-split result. A model configuration is scored on the final split once."""
    model = entry.get("model_id")
    if any(row.get("model_id") == model for row in final_runs(snapshot_dir)):
        raise SnapshotError(f"Model {model} already has a final-split result in this snapshot. Final runs are not repeated.")
    with (Path(snapshot_dir) / RESULTS).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
