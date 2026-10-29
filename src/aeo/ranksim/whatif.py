"""Before/after scores for a bundle of saved HTML.

Ranks are LambdaMART scores on the crawled labeled set. They are predicted.
They are not live Google ranks. A URL that was not in the labeled file is
inserted only in the after set, and the report says so.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date

from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim.attribution import LABELS
from aeo.ranksim.features import FEATURE_NAMES, PageDoc, raw_features
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.lambdamart import (
    RankExample,
    _is_smartcrew,
    _lookup_page,
    build_examples,
    cross_validate,
    fit_lambdamart,
    navboost_value,
    predict_scores,
)
from aeo.ranksim.loop import page_from_html

FOCUS_QUERIES = (
    "all in one heavy civil software",
    "all in one dirt work software",
    "hcss alternative",
)


@dataclass
class Override:
    url: str
    doc: PageDoc
    in_corpus: bool


@dataclass
class PageMove:
    url: str
    role: str
    before_rank: int | None
    after_rank: int | None
    swap_rank: int | None
    title_before: str
    title_after: str
    h1_after: str
    words_before: int | None
    words_after: int
    tables_before: int | None
    tables_after: int
    feature_lines: list[str] = field(default_factory=list)


@dataclass
class TopGap:
    position: float
    url: str
    crawled: bool
    title: str
    rival_rank: int | None
    our_rank: int | None
    is_ours: bool = False
    same_as_best: bool = False
    feature_lines: list[str] = field(default_factory=list)


@dataclass
class QueryWhatIf:
    query: str
    before_n: int
    swap_n: int
    after_n: int
    real_position: float | None
    real_url: str | None
    real_crawled: bool
    before_rank: int | None
    before_url: str | None
    swap_rank: int | None
    after_rank: int | None
    after_url: str | None
    after_words: int | None
    content_rank: int | None
    content_url: str | None
    pages: list[PageMove]
    gaps: list[TopGap]
    order: list[tuple[int, str, bool]]


@dataclass
class WhatIfReport:
    accuracy_note: str
    pairwise_accuracy: float | None
    ndcg_at_10: float | None
    spearman: float | None
    top3_hit_rate: float | None
    n_queries: int
    n_pairs: int
    n_labeled_urls: int
    uncovered_urls: int
    queries: list[QueryWhatIf]
    bundle_urls: list[str]


def overrides_from_html(html_by_url: dict[str, str], pages: dict[str, PageDoc]) -> list[Override]:
    """Build page docs from supplied HTML. Missing corpus pages are not invented elsewhere."""
    donor = _domain_donor(pages, "smartcrewhq.com")
    overrides: list[Override] = []
    for url, html in html_by_url.items():
        key = normalize_url(url)
        existing = _lookup_page(pages, url)
        doc = page_from_html(url, html, is_ours=True)
        if existing is not None:
            _copy_graph(existing, doc)
            overrides.append(Override(url=key, doc=doc, in_corpus=True))
            continue
        doc.pagerank = 0.0
        doc.authority = 0.0
        doc.anchors = ""
        doc.navboost_prior = 0.0
        if donor is not None:
            doc.domain_rank = donor.domain_rank
            doc.homepage_authority = donor.homepage_authority
        overrides.append(Override(url=key, doc=doc, in_corpus=False))
    return overrides


def score_whatif(
    labels: list[SerpLabel],
    pages: dict[str, PageDoc],
    overrides: list[Override],
    intents: dict[str, str] | None = None,
    *,
    today: date | None = None,
) -> WhatIfReport:
    """Score every labeled query before the bundle and after it.

    Each query is scored by a model trained on the other queries only.
    """
    reports = score_many(labels, pages, {"bundle": overrides}, intents, today=today)
    return reports["bundle"]


def score_many(
    labels: list[SerpLabel],
    pages: dict[str, PageDoc],
    variants: dict[str, list[Override]],
    intents: dict[str, str] | None = None,
    *,
    today: date | None = None,
) -> dict[str, WhatIfReport]:
    """Score several HTML bundles with one leave-one-query-out fit per query."""
    today = today or date.today()
    intents = intents or {}
    examples, uncovered, withheld = build_examples(labels, pages, intents, today=today)
    accuracy = cross_validate(examples, real_google=True, folds_mode="loo")
    accuracy.uncovered_urls = uncovered
    accuracy.navboost_withheld = withheld
    by_query: dict[str, list[SerpLabel]] = {}
    for label in labels:
        by_query.setdefault(label.query, []).append(label)
    usable = {query: rows for query, rows in _examples_by_query(examples).items() if len(rows) >= 2}
    models = {}
    for query in by_query:
        train = [row for name, rows in usable.items() if name != query for row in rows]
        models[query] = fit_lambdamart(train) if train else None
    note = accuracy.note
    if withheld:
        note += " Navboost prior was withheld because it copied the label position."
    reports = {}
    for name, overrides in variants.items():
        override_index = {row.url: row for row in overrides}
        queries = [
            _score_query(
                query,
                by_query[query],
                pages,
                override_index,
                intents,
                models[query],
                today,
                withhold=withheld,
            )
            for query in by_query
        ]
        reports[name] = WhatIfReport(
            accuracy_note=note,
            pairwise_accuracy=accuracy.pairwise_accuracy,
            ndcg_at_10=accuracy.ndcg_at_10,
            spearman=accuracy.spearman,
            top3_hit_rate=accuracy.top3_hit_rate,
            n_queries=accuracy.n_queries,
            n_pairs=accuracy.n_pairs,
            n_labeled_urls=len(examples),
            uncovered_urls=uncovered,
            queries=queries,
            bundle_urls=[row.doc.url for row in overrides],
        )
    return reports


def render_whatif(
    report: WhatIfReport,
    statuses: dict[str, tuple[str, str | None]] | None = None,
) -> str:
    """Plain-text report. Every rank line is labeled predicted."""
    lines: list[str] = []
    lines.append("Predicted positions. Not live Google ranks.")
    lines.append("Model: LambdaMART, leave-one-query-out. The query being scored was not in that model.")
    lines.append("Cross-validated accuracy on the labeled file, using the pages stored before this HTML change:")
    lines.append(f"  Pairwise accuracy: {_num(report.pairwise_accuracy)}")
    lines.append(f"  NDCG at 10: {_num(report.ndcg_at_10)}")
    lines.append(f"  Spearman: {_num(report.spearman)}")
    lines.append(f"  Top-3 hit rate: {_num(report.top3_hit_rate)}")
    lines.append(
        f"  Labeled URLs with HTML: {report.n_labeled_urls}. Uncovered URLs: {report.uncovered_urls}. "
        f"Queries: {report.n_queries}. Pairs: {report.n_pairs}."
    )
    lines.append(
        "Confidence: use the cross-validated numbers above. Pairwise accuracy is the one to trust first. "
        "Spearman and the top-3 hit rate move around on a 16-query file. A predicted slot is a score inside "
        "the crawled set."
    )
    lines.append(
        "Before set: crawled URLs from the labeled file. After set: those URLs, with bundle HTML on a matching URL, "
        "plus bundle URLs that were not in that query. A new URL gets crawl PageRank 0. Domain PageRank is copied "
        "from other pages on the same host. No competitor HTML was invented."
    )
    lines.append("")
    lines.append(
        "query\treal_google\tbefore_predicted\tafter_predicted\tafter_url\tafter_words\t"
        "content_page_predicted\tcontent_page_url\tbefore_n\tafter_n"
    )
    lines.append(
        "content_page_predicted is the best SmartCrew URL whose extracted body has at least 150 words. "
        "A page under 150 words is gated behind those."
    )
    for row in report.queries:
        lines.append(
            "\t".join(
                [
                    row.query,
                    _cell(row.real_position),
                    _rank_cell(row.before_rank, row.before_n),
                    _rank_cell(row.after_rank, row.after_n),
                    row.after_url or "",
                    _cell(row.after_words),
                    _rank_cell(row.content_rank, row.after_n),
                    row.content_url or "",
                    str(row.before_n),
                    str(row.after_n),
                ]
            )
        )
    focus = [row for row in report.queries if row.query in FOCUS_QUERIES]
    rest = [row for row in report.queries if row.query not in FOCUS_QUERIES]
    lines.append("")
    lines.append("Detail. Focus queries are first. Ranks are predicted.")
    for row in focus + rest:
        lines.extend(_render_query(row, statuses or {}))
    text = "\n".join(lines) + "\n"
    return text.replace("\u2014", " - ").replace("\u2013", "-")


def _score_query(
    query: str,
    labels: list[SerpLabel],
    pages: dict[str, PageDoc],
    overrides: dict[str, Override],
    intents: dict[str, str],
    model,
    today: date,
    *,
    withhold: bool,
) -> QueryWhatIf:
    intent = _intent(query, intents)
    labeled: list[tuple[SerpLabel, PageDoc]] = []
    for label in labels:
        page = _lookup_page(pages, label.url)
        if page is not None:
            labeled.append((label, page))
    labeled_keys = {normalize_url(page.url) for _label, page in labeled}
    before_docs = [_clone(page, _prior(label, withhold)) for label, page in labeled]
    swap_docs: list[PageDoc] = []
    for label, page in labeled:
        key = normalize_url(page.url)
        override = overrides.get(key)
        if override is None:
            swap_docs.append(_clone(page, _prior(label, withhold)))
            continue
        doc = _clone(override.doc, _prior(label, withhold))
        doc.url = page.url
        swap_docs.append(doc)
    extras: list[tuple[Override, PageDoc]] = []
    for key, override in overrides.items():
        if key in labeled_keys:
            continue
        extras.append((override, _clone(override.doc, 0.0)))
    after_docs = swap_docs + [doc for _override, doc in extras]
    before_raw = raw_features(query, intent, before_docs, today) if before_docs else []
    swap_raw = raw_features(query, intent, swap_docs, today) if swap_docs else []
    after_raw = raw_features(query, intent, after_docs, today) if after_docs else []
    before_scores = _scores(model, query, before_raw)
    swap_scores = _scores(model, query, swap_raw)
    after_scores = _scores(model, query, after_raw)
    before_rank = _ranks(before_scores)
    swap_rank = _ranks(swap_scores)
    after_rank = _ranks(after_scores)
    gain = _gains(model)
    smart_labels = [label for label in labels if _is_smartcrew(domain_of(label.url))]
    real_label = min(smart_labels, key=lambda label: (label.position, label.url)) if smart_labels else None
    pages_out: list[PageMove] = []
    for index, (label, page) in enumerate(labeled):
        if not _is_smartcrew(domain_of(page.url)):
            continue
        key = normalize_url(page.url)
        override = overrides.get(key)
        after_doc = swap_docs[index]
        role = "labeled, HTML replaced" if override else "labeled, HTML unchanged"
        feature_lines: list[str] = []
        if override and model is not None:
            feature_lines = _content_deltas(
                model,
                before_raw[index],
                swap_raw[index],
            )
        pages_out.append(
            PageMove(
                url=page.url,
                role=role,
                before_rank=before_rank[index],
                after_rank=after_rank[index],
                swap_rank=swap_rank[index],
                title_before=page.title,
                title_after=after_doc.title,
                h1_after=after_doc.h1,
                words_before=page.word_count,
                words_after=after_doc.word_count,
                tables_before=page.table_count,
                tables_after=after_doc.table_count,
                feature_lines=feature_lines,
            )
        )
    for offset, (override, doc) in enumerate(extras):
        if not _is_smartcrew(domain_of(doc.url)):
            continue
        index = len(swap_docs) + offset
        role = "inserted from the crawl, PR HTML" if override.in_corpus else "new URL, not in the crawl"
        feature_lines = _new_page_lines(model, after_raw[index]) if model is not None else []
        if not override.in_corpus:
            feature_lines.insert(0, "Crawl PageRank is 0. This URL has no inlinks in the stored crawl.")
        pages_out.append(
            PageMove(
                url=doc.url,
                role=role,
                before_rank=None,
                after_rank=after_rank[index],
                swap_rank=None,
                title_before="",
                title_after=doc.title,
                h1_after=doc.h1,
                words_before=None,
                words_after=doc.word_count,
                tables_before=None,
                tables_after=doc.table_count,
                feature_lines=feature_lines,
            )
        )
    our_before = _best(pages_out, "before_rank")
    our_after = _best(pages_out, "after_rank")
    our_swap = _best([page for page in pages_out if page.swap_rank is not None], "swap_rank")
    content_pages = [page for page in pages_out if page.words_after >= 150]
    our_content = _best(content_pages, "after_rank")
    our_after_features = None
    if our_after is not None:
        our_after_features = _features_for_url(after_docs, after_raw, our_after.url)
    gaps: list[TopGap] = []
    for label in sorted(labels, key=lambda item: item.position):
        if label.position > 3:
            continue
        rival = _lookup_page(pages, label.url)
        if rival is None:
            gaps.append(
                TopGap(
                    label.position,
                    label.url,
                    False,
                    "",
                    None,
                    our_after.after_rank if our_after else None,
                )
            )
            continue
        rival_index = _index_of(after_docs, rival.url)
        feature_lines = []
        same_as_best = bool(
            our_after is not None and _index_of([PageDoc(url=our_after.url)], rival.url) is not None
        )
        if our_after_features is not None and rival_index is not None and model is not None and not same_as_best:
            feature_lines = _gap_lines(our_after_features, after_raw[rival_index], gain)
        title = after_docs[rival_index].title if rival_index is not None else rival.title
        gaps.append(
            TopGap(
                position=label.position,
                url=rival.url,
                crawled=True,
                title=title,
                rival_rank=after_rank[rival_index] if rival_index is not None else None,
                our_rank=our_after.after_rank if our_after else None,
                is_ours=_is_smartcrew(domain_of(rival.url)),
                same_as_best=same_as_best,
                feature_lines=feature_lines,
            )
        )
    order = [(after_rank[index], doc.url, _is_smartcrew(domain_of(doc.url))) for index, doc in enumerate(after_docs)]
    order.sort(key=lambda item: item[0])
    return QueryWhatIf(
        query=query,
        before_n=len(before_docs),
        swap_n=len(swap_docs),
        after_n=len(after_docs),
        real_position=real_label.position if real_label else None,
        real_url=real_label.url if real_label else None,
        real_crawled=bool(real_label and _lookup_page(pages, real_label.url)),
        before_rank=our_before.before_rank if our_before else None,
        before_url=our_before.url if our_before else None,
        swap_rank=our_swap.swap_rank if our_swap else None,
        after_rank=our_after.after_rank if our_after else None,
        after_url=our_after.url if our_after else None,
        after_words=our_after.words_after if our_after else None,
        content_rank=our_content.after_rank if our_content else None,
        content_url=our_content.url if our_content else None,
        pages=pages_out,
        gaps=gaps,
        order=order,
    )


def _render_query(row: QueryWhatIf, statuses: dict[str, tuple[str, str | None]]) -> list[str]:
    lines = ["", f"Query: {row.query}", "Predicted. Not a live Google rank."]
    if row.real_position is None:
        lines.append("Real Google position: not in the labeled file.")
    else:
        crawled = "that URL was crawled" if row.real_crawled else "that URL was not crawled, so it has no before score"
        lines.append(f"Real Google position: {row.real_position:g} at {row.real_url} ({crawled}).")
    lines.append(
        f"Best predicted SmartCrew page before: {_rank_cell(row.before_rank, row.before_n)} {row.before_url or '(none in the crawled labeled set)'}."
    )
    if row.swap_rank is not None and row.swap_n != row.after_n:
        lines.append(
            f"Best predicted SmartCrew page after HTML replacement only: {_rank_cell(row.swap_rank, row.swap_n)}."
        )
    lines.append(
        f"Best predicted SmartCrew page after the bundle: {_rank_cell(row.after_rank, row.after_n)} {row.after_url or '(none)'}."
    )
    if row.after_words is not None and row.after_words < 150:
        lines.append(
            f"That page has {row.after_words} extracted words. The built file is a short crawl shell, not a full article. "
            "With pairwise accuracy at the cross-validated number above, this slot is not evidence the shell would outrank the crawled pages."
        )
    if row.content_url and row.content_url != row.after_url:
        lines.append(
            f"Best predicted SmartCrew page with at least 150 extracted words: {_rank_cell(row.content_rank, row.after_n)} {row.content_url}."
        )
    if row.before_rank is None and row.after_rank is not None:
        lines.append(
            "The after rank inserts SmartCrew into a set where the labeled file has no crawled SmartCrew URL. It is not a move from a Google position."
        )
    lines.append("SmartCrew pages in this query:")
    for page in sorted(row.pages, key=lambda item: (item.after_rank or 999, item.url)):
        words = f"{page.words_after} words" if page.words_before is None else f"words {page.words_before} -> {page.words_after}"
        tables = f"{page.tables_after} tables" if page.tables_before is None else f"tables {page.tables_before} -> {page.tables_after}"
        lines.append(
            f"  {_plain(page.url, 180)} | {page.role} | before {_rank_cell(page.before_rank, row.before_n)} | "
            f"after {_rank_cell(page.after_rank, row.after_n)} | {words} | {tables}"
        )
        if page.title_before and page.title_before != page.title_after:
            lines.append(f"    title {_plain(page.title_before)} -> {_plain(page.title_after)}")
        elif page.title_after:
            lines.append(f"    title {_plain(page.title_after)}")
        if page.h1_after and page.h1_after != page.title_after:
            lines.append(f"    h1 {_plain(page.h1_after)}")
        if page.words_after < 150:
            lines.append("    thin-content gate: ranked behind every page with at least 150 words")
        for feature in page.feature_lines:
            lines.append(f"    feature {feature}")
    lines.append("Real Google top 3, and the remaining gap versus the best predicted SmartCrew page:")
    if not row.gaps:
        lines.append("  This query has no labeled position 1 to 3.")
    for gap in row.gaps:
        if not gap.crawled:
            lines.append(
                f"  Google #{gap.position:g} {gap.url} | {_status_phrase(gap.url, statuses)}. No page text was invented."
            )
            continue
        if gap.same_as_best:
            lines.append(
                f"  Google #{gap.position:g} is the best predicted SmartCrew page, at {_rank_cell(gap.rival_rank, row.after_n)}. "
                f"{_plain(gap.title)} | {gap.url}"
            )
            continue
        if gap.is_ours:
            lines.append(
                f"  Google #{gap.position:g} is another SmartCrew URL, predicted {_rank_cell(gap.rival_rank, row.after_n)}. "
                f"{_plain(gap.title)} | {gap.url}"
            )
            if gap.feature_lines:
                lines.append("    Features where this SmartCrew URL is higher than the best predicted SmartCrew page:")
                for feature in gap.feature_lines:
                    lines.append(f"    {feature}")
            continue
        ahead = ""
        if gap.rival_rank is not None and gap.our_rank is not None:
            if gap.our_rank < gap.rival_rank:
                ahead = "Model places SmartCrew ahead of this crawled page."
            elif gap.our_rank > gap.rival_rank:
                ahead = "Model places this crawled page ahead of SmartCrew."
            else:
                ahead = "Model ties SmartCrew with this crawled page."
        lines.append(
            f"  Google #{gap.position:g} predicted rank {_rank_cell(gap.rival_rank, row.after_n)} | {_plain(gap.title)} | {gap.url}"
        )
        if ahead:
            lines.append(f"    {ahead}")
        if gap.feature_lines:
            lines.append("    Features where this page is still higher than the best predicted SmartCrew page:")
            for feature in gap.feature_lines:
                lines.append(f"    {feature}")
        else:
            lines.append("    No scored feature is higher on this page than on the best predicted SmartCrew page.")
    if row.query in FOCUS_QUERIES:
        lines.append("Predicted order after the bundle (crawled labeled URLs plus bundle URLs):")
        for rank, url, ours in row.order:
            mark = " SmartCrew" if ours else ""
            lines.append(f"  {rank}. {url}{mark}")
    return lines


def _content_deltas(model, before: dict[str, float], after: dict[str, float], limit: int = 5) -> list[str]:
    before_c = _contrib(model, before)
    after_c = _contrib(model, after)
    rows = []
    for name in FEATURE_NAMES:
        raw_before = before[name]
        raw_after = after[name]
        raw_delta = raw_after - raw_before
        contrib_delta = after_c[name] - before_c[name]
        raw_missing = raw_delta != raw_delta
        contrib_missing = contrib_delta != contrib_delta
        if raw_missing and contrib_missing:
            continue
        if not raw_missing and abs(raw_delta) < 1e-6 and not contrib_missing and abs(contrib_delta) < 1e-4:
            continue
        rows.append(
            (
                0.0 if contrib_missing else abs(contrib_delta),
                0.0 if raw_missing else abs(raw_delta),
                name,
                raw_before,
                raw_after,
                before_c[name],
                after_c[name],
            )
        )
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    lines = []
    for _contrib_abs, _raw_abs, name, raw_before, raw_after, contrib_before, contrib_after in rows[:limit]:
        label = LABELS.get(name, name)
        lines.append(
            f"{label} {_fmt_feature(raw_before)} -> {_fmt_feature(raw_after)} "
            f"(contribution {_fmt_feature(contrib_before)} -> {_fmt_feature(contrib_after)})"
        )
    return lines or ["No scored feature changed on the same candidate set."]


def _new_page_lines(model, features: dict[str, float], limit: int = 5) -> list[str]:
    contrib = _contrib(model, features)
    names = sorted(FEATURE_NAMES, key=lambda name: abs(contrib[name]), reverse=True)
    lines = []
    for name in names[:limit]:
        if abs(contrib[name]) < 1e-4:
            continue
        label = LABELS.get(name, name)
        lines.append(f"{label} {_fmt_feature(features[name])} (contribution {_fmt_feature(contrib[name])})")
    return lines or ["No feature contribution stood out."]


def _gap_lines(ours: dict[str, float], rival: dict[str, float], gain: dict[str, float], limit: int = 4) -> list[str]:
    rows = []
    for name in FEATURE_NAMES:
        if rival[name] != rival[name] or ours[name] != ours[name]:
            continue
        delta = rival[name] - ours[name]
        if delta <= 0.02:
            continue
        rows.append((gain.get(name, 0.0) * delta, delta, name, rival[name], ours[name]))
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    lines = []
    for _weighted, _delta, name, rival_value, our_value in rows[:limit]:
        label = LABELS.get(name, name)
        lines.append(
            f"{label} Google page {_fmt_feature(rival_value)}, SmartCrew {_fmt_feature(our_value)}"
        )
    return lines


def _contrib(model, features: dict[str, float]) -> dict[str, float]:
    import numpy as np

    matrix = np.asarray([[features.get(name, 0.0) for name in FEATURE_NAMES]], dtype=float)
    row = model.predict(matrix, pred_contrib=True)[0]
    return {name: float(row[index]) for index, name in enumerate(FEATURE_NAMES)}


def _scores(model, query: str, rows: list[dict[str, float]]) -> list[float]:
    if model is None or not rows:
        return [0.0 for _ in rows]
    examples = [RankExample(query=query, url=str(index), position=0.0, features=row) for index, row in enumerate(rows)]
    scored = predict_scores(model, examples)
    return [scored[id(example)] for example in examples]


def _ranks(scores: list[float]) -> list[int]:
    order = sorted(range(len(scores)), key=lambda index: (-scores[index], index))
    ranks = [0] * len(scores)
    for place, index in enumerate(order, start=1):
        ranks[index] = place
    return ranks


def _gains(model) -> dict[str, float]:
    if model is None:
        return {}
    names = list(model.feature_name())
    gains = model.feature_importance(importance_type="gain")
    return {name: float(gain) for name, gain in zip(names, gains)}


def _features_for_url(docs: list[PageDoc], rows: list[dict[str, float]], url: str) -> dict[str, float] | None:
    index = _index_of(docs, url)
    if index is None:
        return None
    return rows[index]


def _index_of(docs: list[PageDoc], url: str) -> int | None:
    try:
        key = normalize_url(url)
    except ValueError:
        key = url
    for index, doc in enumerate(docs):
        try:
            if normalize_url(doc.url) == key:
                return index
        except ValueError:
            if doc.url == url:
                return index
    return None


def _best(pages: list[PageMove], field_name: str) -> PageMove | None:
    ranked = [page for page in pages if getattr(page, field_name) is not None]
    if not ranked:
        return None
    return min(ranked, key=lambda page: (getattr(page, field_name), page.url))


def _examples_by_query(examples: list[RankExample]) -> dict[str, list[RankExample]]:
    grouped: dict[str, list[RankExample]] = {}
    for example in examples:
        grouped.setdefault(example.query, []).append(example)
    return grouped


def _prior(label: SerpLabel, withhold: bool) -> float:
    if withhold or label.prior_position is None:
        return 0.0
    return navboost_value(label.prior_position)


def _clone(doc: PageDoc, prior: float) -> PageDoc:
    cloned = copy.copy(doc)
    cloned.navboost_prior = prior
    return cloned


def _copy_graph(source: PageDoc, target: PageDoc) -> None:
    target.pagerank = source.pagerank
    target.domain_rank = source.domain_rank
    target.homepage_authority = source.homepage_authority
    target.anchors = source.anchors
    target.authority = source.authority
    target.ad_hits = source.ad_hits
    target.fetch_ms = source.fetch_ms


def _domain_donor(pages: dict[str, PageDoc], domain: str) -> PageDoc | None:
    for url, doc in pages.items():
        if url != doc.url:
            continue
        if domain_of(doc.url) == domain and (doc.domain_rank or doc.homepage_authority):
            return doc
    for doc in pages.values():
        if domain_of(doc.url) == domain:
            return doc
    return None


def _intent(query: str, intents: dict[str, str]) -> str:
    if query in intents:
        return intents[query]
    if "alternative" in query or " vs " in query:
        return "comparison"
    return "commercial"


def _status_phrase(url: str, statuses: dict[str, tuple[str, str | None]]) -> str:
    row = statuses.get(url)
    if row is None:
        try:
            row = statuses.get(normalize_url(url))
        except ValueError:
            row = None
    if row is None:
        return "not in the crawled set"
    status, error = row
    if status == "blocked":
        return "blocked by robots.txt"
    if status == "error":
        detail = " ".join((error or "fetch error").split())[:140]
        return f"fetch error ({detail})"
    if status == "fetched":
        return "fetched, but not in the scored page index"
    return f"status {status}"


def _plain(text: str, limit: int = 110) -> str:
    cleaned = text.replace("\u2014", " - ").replace("\u2013", "-").replace("\u00a0", " ")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _fmt_feature(value: float) -> str:
    if value != value:
        return "missing"
    return f"{value:.3f}"


def _num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _cell(value: float | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _rank_cell(rank: int | None, total: int) -> str:
    if rank is None:
        return "n/a"
    return f"{rank} of {total}"


# Queries each PR URL is written to rank for, among the labeled 16.
PAGE_TARGETS: dict[str, tuple[str, ...]] = {
    "https://www.smartcrewhq.com": (
        "all in one dirtwork software",
        "all in one dirt work software",
        "all in one excavation software",
        "all in one heavy civil software",
        "all-in-one heavy civil construction software",
        "best all in one heavy civil software",
        "best all-in-one dirtworks software",
        "best heavy civil construction software",
        "best heavy construction software",
        "heavy civil field management software",
        "excavation contractor software",
    ),
    "https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative": (
        "hcss alternative",
        "hcss heavyjob alternative",
        "hcss heavybid alternative",
        "b2w alternative",
    ),
    "https://www.smartcrewhq.com/all-in-one-heavy-civil-software": (
        "all in one heavy civil software",
        "all-in-one heavy civil construction software",
        "best all in one heavy civil software",
        "best heavy civil construction software",
        "best heavy construction software",
        "heavy civil field management software",
    ),
    "https://www.smartcrewhq.com/all-in-one-dirt-work-software": (
        "all in one dirt work software",
        "all in one dirtwork software",
        "best all-in-one dirtworks software",
        "dirt work contractor software",
        "all in one excavation software",
    ),
    "https://www.smartcrewhq.com/blog/best-excavation-software-20-50-employees": (
        "all in one dirt work software",
        "all in one dirtwork software",
        "best all-in-one dirtworks software",
        "all in one excavation software",
        "excavation contractor software",
        "best heavy construction software",
    ),
    "https://www.smartcrewhq.com/blog/best-software-small-dirt-work-contractors": (
        "dirt work contractor software",
        "best all-in-one dirtworks software",
        "all in one dirt work software",
    ),
}


def worse_than_live(live_rank: int | None, new_rank: int | None) -> bool:
    """A higher rank number is worse. A page that was not on the live set cannot regress."""
    if live_rank is None:
        return False
    if new_rank is None:
        return True
    return new_rank > live_rank


def render_three_way(live: WhatIfReport, v1: WhatIfReport, v2: WhatIfReport) -> str:
    """Predicted live vs PR v1 vs PR v2. A v2 rank worse than live is a ship blocker."""
    lines = [
        "Predicted positions. Not live Google ranks.",
        "Model: LambdaMART, leave-one-query-out. The query being scored was not in that model.",
        "Cross-validated accuracy on the labeled file, using the stored pages:",
        f"  Pairwise accuracy: {_num(live.pairwise_accuracy)}",
        f"  NDCG at 10: {_num(live.ndcg_at_10)}",
        f"  Spearman: {_num(live.spearman)}",
        f"  Top-3 hit rate: {_num(live.top3_hit_rate)}",
        (
            f"  Labeled URLs with HTML: {live.n_labeled_urls}. Uncovered URLs: {live.uncovered_urls}. "
            f"Queries: {live.n_queries}. Pairs: {live.n_pairs}."
        ),
        "Live is the stored crawl. v1 and v2 replace those URLs with the bundle and add URLs the query did not already have.",
        "A BLOCK line means v2's predicted rank number is worse than live. That blocks shipping.",
        "",
        "query\tlive\tv1\tv2\tship",
    ]
    live_by = {row.query: row for row in live.queries}
    v1_by = {row.query: row for row in v1.queries}
    v2_by = {row.query: row for row in v2.queries}
    blockers: list[str] = []
    for query in live_by:
        left = live_by[query]
        mid = v1_by[query]
        right = v2_by[query]
        blocked = worse_than_live(left.after_rank, right.after_rank)
        if blocked:
            blockers.append(
                f"BLOCK query {query}: live {_rank_cell(left.after_rank, left.after_n)} "
                f"{_short(left.after_url)}, v2 {_rank_cell(right.after_rank, right.after_n)} {_short(right.after_url)}"
            )
        lines.append(
            "\t".join(
                [
                    query,
                    f"{_rank_cell(left.after_rank, left.after_n)} {_short(left.after_url)}".strip(),
                    f"{_rank_cell(mid.after_rank, mid.after_n)} {_short(mid.after_url)}".strip(),
                    f"{_rank_cell(right.after_rank, right.after_n)} {_short(right.after_url)}".strip(),
                    "BLOCK" if blocked else "",
                ]
            )
        )
    lines.append("")
    lines.append("Per SmartCrew URL. A row is included when the query is a target, the URL is on the live set, or the URL is the best predicted SmartCrew page.")
    lines.append("page\tquery\trole\tlive\tv1\tv2\tship")
    for url in PAGE_TARGETS:
        targets = set(PAGE_TARGETS[url])
        for query in live_by:
            left = _page_rank(live_by[query], url)
            mid = _page_rank(v1_by[query], url)
            right = _page_rank(v2_by[query], url)
            best = url in {
                _key(live_by[query].after_url),
                _key(v1_by[query].after_url),
                _key(v2_by[query].after_url),
            }
            if query not in targets and left[0] is None and not best:
                continue
            role = "target" if query in targets else "ranks"
            blocked = worse_than_live(left[0], right[0])
            if blocked:
                blockers.append(
                    f"BLOCK page {_short(url)} on {query}: live {_rank_cell(*left)}, v2 {_rank_cell(*right)}"
                )
            lines.append(
                "\t".join(
                    [
                        _short(url),
                        query,
                        role,
                        _rank_cell(*left),
                        _rank_cell(*mid),
                        _rank_cell(*right),
                        "BLOCK" if blocked else "",
                    ]
                )
            )
    lines.append("")
    if blockers:
        lines.append(f"Ship blockers: {len(blockers)}. v2 predicts worse than live.")
        lines.extend(blockers)
    else:
        lines.append("Ship blockers: 0. No SmartCrew page or query has a v2 predicted rank worse than live.")
    lines.append("These ranks are predicted slots in the crawled set.")
    text = "\n".join(lines) + "\n"
    return text.replace("\u2014", " - ").replace("\u2013", "-")


def _page_rank(row: QueryWhatIf, url: str) -> tuple[int | None, int]:
    key = _key(url)
    for page in row.pages:
        if _key(page.url) == key:
            return page.after_rank, row.after_n
    return None, row.after_n


def _key(url: str | None) -> str:
    if not url:
        return ""
    try:
        return normalize_url(url)
    except ValueError:
        return url


def _short(url: str | None) -> str:
    if not url:
        return ""
    try:
        key = normalize_url(url)
    except ValueError:
        return url
    marker = "://"
    if marker not in key:
        return key
    return "/" + key.split(marker, 1)[1].split("/", 1)[1] if key.count("/") >= 3 else "/"
