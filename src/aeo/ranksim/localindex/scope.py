"""Index scope for one ranksim project: its queries, its pulls, and its competitors."""

from __future__ import annotations

import json
from pathlib import Path

from aeo.crawl.urls import domain_of
from aeo.ranksim.localindex.records import Candidate, canon_url
from aeo.ranksim.project import Project, list_pulls
from aeo.ranksim.workspace import WORKSPACE_FILE


def project_index_dir(project: Project) -> Path:
    """Each project keeps its own index, next to its pulls, outside git."""
    return project.dir / "index"


def project_scope(project: Project) -> tuple[list[str], list[Candidate]]:
    """Queries the project tracks, plus every URL from its pulls and competitor homepages.

    A pull is a past snapshot. Competitor homepages are unlabeled candidates so the
    fetch ladder can read those sites and follow relevant internal links.
    """
    queries = [str(item).strip() for item in project.searches if str(item).strip()]
    allowed = set(queries)
    found: list[Candidate] = []
    seen: set[tuple] = set()

    def add(item: Candidate) -> None:
        if allowed and item.query not in allowed:
            return
        key = (item.url, item.query, item.source, item.snapshot_id)
        if key in seen:
            return
        seen.add(key)
        found.append(item)

    for info in list_pulls(project):
        path = project.dir / "pulls" / info.pull_id / "google.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for search in data.get("searches") or []:
            if not isinstance(search, dict):
                continue
            query = " ".join(str(search.get("search") or "").split())
            if not query:
                continue
            for result in search.get("results") or []:
                if not isinstance(result, dict):
                    continue
                url = canon_url(str(result.get("url") or ""))
                try:
                    position = int(result.get("position") or 0)
                except (TypeError, ValueError):
                    position = 0
                if url is None or position <= 0:
                    continue
                add(Candidate(url=url, query=query, position=position, source="snapshot", snapshot_id=info.pull_id))

    homes = []
    site = domain_of(f"https://{project.site}/") if project.site else ""
    if site:
        homes.append((f"https://{site}/", "project_site"))
    for name in _competitors(project):
        host = domain_of(name if "://" in name else f"https://{name}/")
        if host and host != site:
            homes.append((f"https://{host}/", "competitor"))
    for url, source in homes:
        canonical = canon_url(url)
        if canonical is None:
            continue
        for query in queries:
            add(Candidate(url=canonical, query=query, position=None, source=source, snapshot_id=""))
    return queries, found


def _competitors(project: Project) -> list[str]:
    path = project.dir / WORKSPACE_FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [str(item).strip() for item in data.get("competitors") or [] if str(item).strip()]
