"""Frozen benchmark snapshots: the labels, every stored page, and a fixed dev/final split.

A benchmark run on a live database changes whenever pages are recrawled or unlabeled pages are
added (in-corpus PageRank and anchors use every stored page). A snapshot pins all of it:

- ``labels.csv``: a byte copy of the label file, with its sha256 in the manifest.
- ``pages.jsonl.gz``: every stored page with its HTML, source, fetch time and HTML sha256.
- ``manifest.json``: hashes, counts, ``as_of``, the intent of each labeled query, and the split.
- ``graph.json``: optional copy of a link-graph file, with its sha256 in the manifest.
- ``results.jsonl``: append-only log of final-split runs (see ``record_final``).

A final split is identified by ``final_key``: its final query set and the parsed labels of those
queries (query, URL match key, position). The label file's bytes (line endings, column order, dev
rows), the cluster threshold and the folder do not enter it, so a re-frozen copy of the same split
has the same key. Each final run is recorded under that key in three places: the snapshot's
``results.jsonl``, the main database (``rs_final_runs``) and ``smartcrew/final_runs.jsonl``, which
is kept in git so a fresh checkout or database also knows it. A run recorded only in an older
snapshot's ``results.jsonl`` (E12) is in that file too, and opening any snapshot copies its
``results.jsonl`` runs into the database. ``--final`` refuses a model configuration found in any
of them, so a split is scored at most once per model configuration, also after re-freezing it.
Rows written under the first key (label file bytes, cluster threshold, final queries) are still
looked up and never changed. While the snapshot folder such a row names still exists, the row is
also copied under that folder's current key before a final split is checked, so it blocks a copy
re-frozen with other line endings or another cluster threshold. When that path no longer resolves
(the folder was moved or deleted, or the path was relative to another working directory), the old
row only blocks a re-freeze of byte-identical labels with the same cluster threshold. A line of a
snapshot's ``results.jsonl`` that cannot be read stops ``benchmark --snapshot`` (dev runs too) with
the file and the line number. A registry line that cannot be read stops ``benchmark-freeze`` and
``--final`` before they write anything, with the file and the line number.

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
import os
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
        if not must_leave_dev and len(final) + len(queries) <= target:
            final.update(queries)
        else:
            dev.update(queries)
    if not final:
        # Every cluster is larger than the target: hold out the smallest one (first in hash order).
        smallest = min(ranked, key=len)
        dev.difference_update(smallest)
        final.update(smallest)
    return dev, final


def final_key(manifest: dict, labels) -> str:
    """Identity of a final split: the sorted final queries and the parsed label rows of those
    queries as (query, URL match key, position). ``labels`` is the snapshot's label file as
    ``load_serp_file`` returns it.

    Bytes that do not change what is held out leave the key alone: CRLF or LF line endings, column
    order, a duplicate row the loader drops, dev-split rows, the cluster threshold and the folder.
    Dev rows are left out on purpose: training on more dev labels does not make a second look at
    the same held-out labels a new result."""
    final = set(manifest["split"]["final_queries"])
    from aeo.ranksim.urlkey import match_key

    rows = sorted({(row.query, match_key(row.url), repr(float(row.position))) for row in labels if row.query in final})
    body = "final-split-v2\n" + "\n".join(sorted(final)) + "\n--\n" + "\n".join("\t".join(row) for row in rows)
    return hashlib.sha256(body.encode()).hexdigest()


def legacy_final_key(manifest: dict) -> str:
    """The key the first rs_final_runs rows were stored under (label file bytes, cluster threshold,
    final queries). Only looked up, so runs registered with it stay protected. While the snapshot
    folder a row names exists, the row is also copied under that folder's ``final_key``."""
    split = manifest["split"]
    body = f"{manifest['labels_sha256']}|{split['cluster_threshold']}|" + "\n".join(sorted(split["final_queries"]))
    return hashlib.sha256(body.encode()).hexdigest()


def snapshot_final_key(snapshot_dir: str | Path, manifest: dict) -> str:
    """``final_key`` of a frozen snapshot, from its own label copy."""
    from aeo.ranksim.groundtruth import load_serp_file

    return final_key(manifest, load_serp_file(Path(snapshot_dir) / LABELS))


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
    intent_origin: dict[str, str] | None = None,
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
    from aeo.ranksim.groundtruth import ImportReport

    import_report = ImportReport()
    labels = load_serp_file(out / LABELS, report=import_report)
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
        "intent_origin": {query: (intent_origin or {}).get(query, "unknown") for query in sorted({row.query for row in labels})},
        "split": {
            "seed": seed,
            "final_share": final_share,
            "cluster_threshold": cluster_threshold,
            "clusters": len(set(clusters.values())),
            "dev_queries": sorted(dev),
            "final_queries": sorted(final),
        },
        "notice": "Contains third-party page HTML. Keep out of git; do not publish.",
        "import_report": [line.strip() for line in import_report.lines()],
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
    """Every final run in a snapshot's results.jsonl: one JSON object per line, never rewritten.

    A line that is not UTF-8 JSON for an object with a ``model_id`` raises SnapshotError naming the
    file and that line's number, so no final run is checked against a partial list."""
    path = Path(snapshot_dir) / RESULTS
    if not path.is_file():
        return []
    rows: list[dict] = []
    for number, raw in enumerate(path.read_bytes().split(b"\n"), start=1):
        try:
            line = raw.decode("utf-8-sig" if number == 1 else "utf-8")
        except UnicodeDecodeError:
            raise SnapshotError(_results_problem(path, number, "it is not UTF-8 text")) from None
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SnapshotError(_results_problem(path, number, f"it is not valid JSON ({exc.msg}: column {exc.colno})")) from None
        if not isinstance(row, dict) or not isinstance(row.get("model_id"), str) or not row["model_id"]:
            raise SnapshotError(_results_problem(path, number, "it needs a model_id"))
        rows.append(row)
    return rows


def _results_problem(path: Path, number: int, what: str) -> str:
    return (
        f"Line {number} of {path} cannot be read: {what}. Fix that line by hand. "
        'Each line is one JSON object with a model_id, for example {"model_id": "...", "created_at": "..."}. '
        "Until then benchmark --snapshot stops here for this snapshot, because it cannot check its earlier final runs."
    )


def record_final(snapshot_dir: str | Path, entry: dict) -> None:
    """Append one final-split result. A model configuration is scored on the final split once."""
    model = entry.get("model_id")
    if any(row.get("model_id") == model for row in final_runs(snapshot_dir)):
        raise SnapshotError(f"Model {model} already has a final-split result in this snapshot. Final runs are not repeated.")
    with (Path(snapshot_dir) / RESULTS).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def final_registry_path() -> Path:
    """``smartcrew/final_runs.jsonl`` under the repo root, kept in git. ``RANKSIM_FINAL_RUNS`` overrides it."""
    override = os.environ.get("RANKSIM_FINAL_RUNS")
    if override:
        return Path(override)
    from aeo.ranksim.keywords import repo_root

    return repo_root() / "smartcrew" / "final_runs.jsonl"


def registered_final_runs(path: str | Path | None = None) -> list[dict]:
    """Every final run in the git-tracked registry: one JSON object per line, never rewritten.

    A line that is not a JSON object with a ``final_key`` and a ``model_id`` raises SnapshotError
    naming the file and that line's number, so no final run is checked against a partial list."""
    target = Path(path) if path is not None else final_registry_path()
    if not target.is_file():
        return []
    rows: list[dict] = []
    for number, raw in enumerate(target.read_bytes().split(b"\n"), start=1):
        try:
            line = raw.decode("utf-8-sig" if number == 1 else "utf-8")
        except UnicodeDecodeError:
            raise SnapshotError(_registry_problem(target, number, "it is not UTF-8 text")) from None
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SnapshotError(_registry_problem(target, number, f"it is not valid JSON ({exc.msg} at column {exc.colno})")) from None
        if not isinstance(row, dict) or not all(isinstance(row.get(name), str) and row[name] for name in ("final_key", "model_id")):
            raise SnapshotError(_registry_problem(target, number, "it needs a final_key and a model_id"))
        rows.append(row)
    return rows


def _registry_problem(target: Path, number: int, what: str) -> str:
    origin = " (set by RANKSIM_FINAL_RUNS)" if os.environ.get("RANKSIM_FINAL_RUNS") else ""
    return (
        f"Line {number} of the final-run registry {target}{origin} cannot be read: {what}. "
        'Fix that line by hand. Each line is one JSON object, for example {"final_key": "...", "model_id": "..."}. '
        "Until then benchmark-freeze and benchmark --final stop here, because they cannot check earlier final runs."
    )


def register_final(entry: dict, path: str | Path | None = None) -> Path:
    """Append one final run (identity and provenance only, no scores) to the git-tracked registry."""
    target = Path(path) if path is not None else final_registry_path()
    keep = ("final_key", "model_id", "created_at", "as_of", "labels_sha256", "corpus_sha256", "git_commit", "snapshot_dir", "source")
    row = {name: entry.get(name) for name in keep if entry.get(name) is not None}
    target.parent.mkdir(parents=True, exist_ok=True)
    # A line added by hand may lack its newline. Start a new line so the two runs do not merge into one.
    prefix = ""
    if target.is_file() and target.stat().st_size:
        with target.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            prefix = "" if handle.read(1) == b"\n" else "\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(prefix + json.dumps(row, sort_keys=True) + "\n")
    return target
