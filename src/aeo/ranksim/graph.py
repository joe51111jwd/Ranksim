"""PageRank on the links we actually crawled. This is not web-wide PageRank."""

from __future__ import annotations

from collections import defaultdict

from aeo.crawl.urls import domain_of


def pagerank(
    edges: list[tuple[str, str]],
    *,
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    """Standard PageRank. Nodes with no out-links pass mass evenly."""
    nodes: set[str] = set()
    outgoing: dict[str, set[str]] = defaultdict(set)
    for source, target in edges:
        if not source or not target or source == target:
            continue
        nodes.add(source)
        nodes.add(target)
        outgoing[source].add(target)
    if not nodes:
        return {}
    count = len(nodes)
    rank = {node: 1.0 / count for node in nodes}
    teleport = (1.0 - damping) / count
    for _ in range(iterations):
        updated = {node: teleport for node in nodes}
        dangling = sum(rank[node] for node in nodes if not outgoing.get(node))
        share_dangling = damping * dangling / count
        for node in nodes:
            updated[node] += share_dangling
        for source, targets in outgoing.items():
            portion = damping * rank[source] / len(targets)
            for target in targets:
                updated[target] += portion
        rank = updated
    return rank


def domain_pagerank(edges: list[tuple[str, str]], **kwargs) -> dict[str, float]:
    domain_edges = []
    for source, target in edges:
        left = domain_of(source)
        right = domain_of(target)
        if left and right:
            domain_edges.append((left, right))
    return pagerank(domain_edges, **kwargs)
