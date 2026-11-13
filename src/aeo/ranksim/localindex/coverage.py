"""Text coverage for labeled rows, rank bands, and the URLs still missing."""

from __future__ import annotations

from aeo.ranksim.localindex.records import Candidate, PageRecord, rank_band

TOP10_TARGET = 0.95


def coverage_report(candidates: list[Candidate], pages: dict[str, PageRecord]) -> dict:
    labeled = _labeled_rows(candidates)
    by_query: dict[str, list[tuple[str, int]]] = {}
    by_band: dict[str, list[tuple[str, int]]] = {}
    for query, url, position in labeled:
        by_query.setdefault(query, []).append((url, position))
        by_band.setdefault(rank_band(position), []).append((url, position))

    def pack(rows: list[tuple[str, int]]) -> dict:
        with_text = sum(1 for url, _position in rows if _full(pages.get(url)))
        total = len(rows)
        return {
            "rows": total,
            "with_text": with_text,
            "pct": (with_text / total) if total else 0.0,
        }

    query_rows = []
    for query in sorted(by_query):
        summary = pack(by_query[query])
        summary["query"] = query
        query_rows.append(summary)
    band_order = ["1-10", "11-20", "21-50", "51-100", "100+"]
    band_rows = []
    for band in band_order:
        if band not in by_band:
            continue
        summary = pack(by_band[band])
        summary["band"] = band
        band_rows.append(summary)

    labeled_summary = pack([(url, position) for _query, url, position in labeled])
    top10 = [(url, position) for _query, url, position in labeled if position <= 10]
    top10_summary = pack(top10)
    unique = sorted({item.url for item in candidates})
    unique_with = sum(1 for url in unique if _full(pages.get(url)))

    missing_urls: dict[str, dict] = {}
    for item in candidates:
        page = pages.get(item.url)
        if _full(page):
            continue
        slot = missing_urls.setdefault(
            item.url,
            {
                "url": item.url,
                "reason": (page.miss_reason if page else "") or "no_capture",
                "source": page.source if page else "",
                "queries": [],
            },
        )
        slot["queries"].append({"query": item.query, "position": item.position, "candidate_source": item.source})
        if page and page.miss_reason:
            slot["reason"] = page.miss_reason
    missing = []
    for url in sorted(missing_urls):
        slot = missing_urls[url]
        positions = [item["position"] for item in slot["queries"] if item["position"] is not None]
        slot["best_position"] = min(positions) if positions else None
        missing.append(slot)
    missing.sort(key=lambda item: (item["best_position"] is None, item["best_position"] or 0, item["url"]))

    return {
        "labeled_rows": labeled_summary["rows"],
        "labeled_with_text": labeled_summary["with_text"],
        "labeled_text_pct": labeled_summary["pct"],
        "top10_rows": top10_summary["rows"],
        "top10_with_text": top10_summary["with_text"],
        "top10_text_pct": top10_summary["pct"],
        "top10_target": TOP10_TARGET,
        "top10_meets_target": top10_summary["pct"] >= TOP10_TARGET and top10_summary["rows"] > 0,
        "unique_urls": len(unique),
        "unique_with_text": unique_with,
        "unique_text_pct": (unique_with / len(unique)) if unique else 0.0,
        "by_query": query_rows,
        "by_rank_band": band_rows,
        "missing": missing,
    }


def coverage_lines(report: dict) -> list[str]:
    lines = [
        (
            f"Top-10 text coverage: {report['top10_with_text']} of {report['top10_rows']} "
            f"({report['top10_text_pct']:.1%}). Target is {report['top10_target']:.0%}."
        ),
        (
            f"Labeled rows with text: {report['labeled_with_text']} of {report['labeled_rows']} "
            f"({report['labeled_text_pct']:.1%})."
        ),
        (
            f"Unique candidate URLs with text: {report['unique_with_text']} of {report['unique_urls']} "
            f"({report['unique_text_pct']:.1%})."
        ),
    ]
    for band in report["by_rank_band"]:
        lines.append(f"Rank band {band['band']}: {band['with_text']} of {band['rows']} ({band['pct']:.1%}).")
    for row in report["by_query"]:
        lines.append(f"Query {row['query']}: {row['with_text']} of {row['rows']} ({row['pct']:.1%}).")
    missing = report["missing"]
    shown = missing[:200]
    if len(missing) > len(shown):
        lines.append(f"Still missing: {len(missing)} URLs. The first 200 are listed. The full list is in coverage.json.")
    else:
        lines.append(f"Still missing: {len(missing)} URLs.")
    for item in shown:
        position = item["best_position"]
        where = "unlabeled" if position is None else f"best position {position}"
        lines.append(f"  {item['url']}  {where}  {item['reason']}")
    return lines


def _labeled_rows(candidates: list[Candidate]) -> list[tuple[str, str, int]]:
    best: dict[tuple[str, str], int] = {}
    for item in candidates:
        if item.position is None:
            continue
        key = (item.query, item.url)
        current = best.get(key)
        if current is None or item.position < current:
            best[key] = item.position
    return [(query, url, position) for (query, url), position in sorted(best.items(), key=lambda pair: (pair[0][0], pair[1], pair[0][1]))]


def _full(page: PageRecord | None) -> bool:
    return page is not None and page.status == "full" and bool(page.main_text.strip())
