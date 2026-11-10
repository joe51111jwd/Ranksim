"""Site and link graph. Only pages we fetch, and only when robots.txt allows.

Outbound links are followed to a set depth. This is not a map of the open web,
and in-crawl inlinks are not web-wide backlinks.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from aeo.errors import ProviderConfigError
from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim.graph import pagerank


@dataclass
class GraphNode:
    url: str
    depth: int
    title: str = ""
    status: str = "seen"
    inlinks: int = 0
    outlinks: int = 0
    orphan: bool = False
    pagerank: float = 0.0
    anchors: str = ""
    html: str = ""


@dataclass
class LinkGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fetches: int = 0
    blocked: int = 0

    def to_json(self) -> dict:
        return {
            "nodes": [
                {
                    "url": node.url,
                    "depth": node.depth,
                    "title": node.title,
                    "status": node.status,
                    "inlinks": node.inlinks,
                    "outlinks": node.outlinks,
                    "orphan": node.orphan,
                    "pagerank": node.pagerank,
                    "anchors": node.anchors,
                }
                for node in self.nodes.values()
            ],
            "edges": [{"source": src, "target": dst, "anchor": anchor} for src, dst, anchor in self.edges],
            "notes": self.notes,
            "fetches": self.fetches,
            "blocked": self.blocked,
        }

    @classmethod
    def from_json(cls, payload: dict) -> LinkGraph:
        graph = cls(notes=list(payload.get("notes") or []), fetches=int(payload.get("fetches") or 0), blocked=int(payload.get("blocked") or 0))
        for raw in payload.get("nodes") or []:
            graph.nodes[raw["url"]] = GraphNode(
                url=raw["url"],
                depth=int(raw.get("depth") or 0),
                title=str(raw.get("title") or ""),
                status=str(raw.get("status") or "seen"),
                inlinks=int(raw.get("inlinks") or 0),
                outlinks=int(raw.get("outlinks") or 0),
                orphan=bool(raw.get("orphan")),
                pagerank=float(raw.get("pagerank") or 0.0),
                anchors=str(raw.get("anchors") or ""),
            )
        for raw in payload.get("edges") or []:
            graph.edges.append((raw["source"], raw["target"], str(raw.get("anchor") or "")))
        return graph


def crawl_link_graph(
    seeds: list[str],
    *,
    fetch,
    known_html: dict[str, str] | None = None,
    our_domain: str = "smartcrewhq.com",
    our_internal_depth: int = 2,
    competitor_internal_depth: int = 1,
    outbound_depth: int = 1,
    max_fetches: int = 60,
    max_nodes: int = 140,
    max_per_host: int = 4,
    our_host_cap: int = 40,
) -> LinkGraph:
    """BFS from seeds. ``fetch(url)`` returns (html, status) with status fetched, blocked, or error."""
    known = {}
    for url, html in (known_html or {}).items():
        try:
            known[normalize_url(url)] = html
        except ValueError:
            continue
    graph = LinkGraph()
    seen: set[str] = set()
    host_counts: dict[str, int] = defaultdict(int)
    # url, depth, outbound_hops, allow_internal
    queue: deque[tuple[str, int, int, bool]] = deque()
    for seed in seeds:
        try:
            url = normalize_url(seed)
        except ValueError:
            continue
        queue.append((url, 0, 0, True))
    while queue and len(graph.nodes) < max_nodes:
        url, depth, outbound_hops, allow_internal = queue.popleft()
        if url in seen:
            continue
        host = domain_of(url) or ""
        cap = our_host_cap if host == our_domain else max_per_host
        if host_counts[host] >= cap:
            continue
        seen.add(url)
        host_counts[host] += 1
        if url in known:
            html, status = known[url], "fetched"
        else:
            if graph.fetches >= max_fetches:
                graph.notes.append(f"Stopped at {max_fetches} fetches. Later links were recorded only when already known.")
                break
            html, status = fetch(url)
            graph.fetches += 1
            if status == "blocked":
                graph.blocked += 1
        title = _title(html) if html else ""
        graph.nodes[url] = GraphNode(url=url, depth=depth, title=title, status=status, html=html or "")
        if status != "fetched" or not html:
            continue
        internal_limit = our_internal_depth if host == our_domain else competitor_internal_depth
        for target, anchor in extract_anchors(html, url):
            graph.edges.append((url, target, anchor))
            if target in seen:
                continue
            same = domain_of(target) == host and host != ""
            if same and allow_internal and depth + 1 <= internal_limit:
                queue.append((target, depth + 1, outbound_hops, True))
            elif not same and outbound_hops + 1 <= outbound_depth:
                queue.append((target, depth + 1, outbound_hops + 1, False))
    _finish(graph)
    if not graph.nodes:
        graph.notes.append("The link crawl stored no pages.")
    return graph


def extract_anchors(html: str, base: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html or "", "lxml")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(base, href).split("#")[0]
        if not absolute.startswith("http"):
            continue
        try:
            target = normalize_url(absolute)
        except ValueError:
            continue
        if target in seen or target == base:
            continue
        seen.add(target)
        text = " ".join(anchor.get_text(" ", strip=True).split())[:180]
        found.append((target, text))
    return found


def attach_graph(pages: dict, graph: LinkGraph) -> int:
    """Copy crawl metrics onto page docs. Pages the crawl never saw stay missing."""
    updated = 0
    incoming: dict[str, list[str]] = defaultdict(list)
    for _src, dst, anchor in graph.edges:
        if anchor:
            incoming[dst].append(anchor)
    for url, node in graph.nodes.items():
        doc = pages.get(url)
        if doc is None:
            try:
                doc = pages.get(normalize_url(url))
            except ValueError:
                doc = None
        if doc is None:
            continue
        # Graph facts are annotations for link advice. They do not overwrite model inputs:
        # the graph's PageRank is computed on a different node set (most edges point outside
        # it), and its anchors include sitewide navigation. Corpus PageRank and anchors stay.
        doc.orphan = node.orphan
        doc.link_depth = node.depth
        doc.crawl_inlinks = node.inlinks
        doc.graph_pagerank = node.pagerank
        updated += 1
    return updated


def _finish(graph: LinkGraph) -> None:
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, int] = defaultdict(int)
    simple_edges: list[tuple[str, str]] = []
    for src, dst, anchor in graph.edges:
        if src not in graph.nodes or dst not in graph.nodes:
            # Keep the edge, but PageRank only uses nodes we stored.
            if dst not in graph.nodes:
                continue
        simple_edges.append((src, dst))
        outgoing[src] += 1
        if anchor:
            incoming[dst].append(anchor)
    ranks = pagerank([(src, dst) for src, dst in simple_edges if src in graph.nodes and dst in graph.nodes])
    for url, node in graph.nodes.items():
        node.inlinks = sum(1 for src, dst, _anchor in graph.edges if dst == url and src in graph.nodes)
        node.outlinks = outgoing.get(url, 0)
        path = urlsplit(url).path or "/"
        node.orphan = node.inlinks == 0 and path not in {"", "/"}
        node.pagerank = float(ranks.get(url, 0.0))
        node.anchors = " ".join(incoming.get(url, []))


def _title(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    if soup.title and soup.title.string:
        return " ".join(soup.title.string.split())[:180]
    return ""


def collect_graph(settings, labels, *, saved_html: dict[str, str], our_domain: str = "smartcrewhq.com", **limits) -> LinkGraph:
    """Crawl from the client homepage and each non-client URL in the labeled file."""
    from aeo.crawl.fetch import Crawler

    from aeo.ranksim.keywords import load_sitemap_urls

    seeds = [f"https://www.{our_domain}/"]
    seen = set()
    for label in labels:
        if domain_of(label.url) == our_domain:
            continue
        if label.url in seen:
            continue
        seen.add(label.url)
        seeds.append(label.url)
    for url in load_sitemap_urls():
        if url not in seen:
            seeds.append(url)
    crawler = Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=max(settings.crawl_delay_seconds, 0.4),
        allow_private=settings.crawl_allow_private,
        timeout=min(settings.request_timeout_seconds, 20.0),
    )

    def fetch(url: str) -> tuple[str, str]:
        try:
            fetched = crawler.fetch(url)
        except ProviderConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            return "", f"error {exc}"
        if fetched.blocked:
            return "", "blocked"
        if fetched.error or not fetched.html:
            return "", "error"
        return fetched.html, "fetched"

    try:
        return crawl_link_graph(seeds, fetch=fetch, known_html=saved_html, our_domain=our_domain, **limits)
    finally:
        crawler.close()


def save_graph(graph: LinkGraph, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(graph.to_json(), handle)


def load_graph(path: str) -> LinkGraph:
    with open(path, encoding="utf-8") as handle:
        return LinkGraph.from_json(json.load(handle))
