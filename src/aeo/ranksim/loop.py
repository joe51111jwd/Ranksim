"""Collect a focused corpus, calibrate, test one page, and record daily ranks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim import OUR_DOMAIN
from aeo.ranksim.features import PageDoc
from aeo.ranksim.keywords import Keyword
from aeo.ranksim.score import HAND_WEIGHTS, best_our_position, fit_weights, score_pages
from aeo.ranksim.serp import SerpProvider
from aeo.ranksim.store import (
    RsPage,
    latest_weights,
    page_by_url,
    queries_with_serp,
    replace_serp,
    save_keywords,
    save_rank_checks,
    save_test_run,
    save_weights,
    serp_for_query,
    upsert_page,
)
from aeo.ranksim.store import RsRankCheck


@dataclass
class FetchResult:
    url: str
    html: str
    status: str
    error: str | None = None
    fetch_ms: float | None = None


@dataclass
class CollectResult:
    queries: int
    serp_hits: int
    pages_stored: int
    provider: str
    synthetic: bool
    is_google: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class QueryDelta:
    query: str
    intent: str
    before_predicted_position: int | None
    after_predicted_position: int | None
    delta: int | None
    competitor_gaps: list[str]
    note: str
    feature_moves: list[str] = field(default_factory=list)


def is_our_url(url: str) -> bool:
    host = domain_of(url)
    return host == OUR_DOMAIN or host.endswith("." + OUR_DOMAIN)


def page_from_html(url: str, html: str, *, fetch_ms: float | None = None, is_ours: bool | None = None) -> PageDoc:
    from aeo.crawl.extract import extract_html  # lazy: it imports bs4, which server start-up does not need

    extracted = extract_html(html or "", url)
    headings = " ".join(str(item.get("text") or "") for item in extracted.headings)
    schema_types = _schema_types(extracted.schema_objects)
    return PageDoc(
        url=url,
        title=extracted.title,
        h1=extracted.h1,
        headings=headings,
        body=extracted.main_text,
        word_count=extracted.word_count,
        table_count=(html or "").lower().count("<table"),
        has_faq="FAQPage" in schema_types or "faq" in headings.lower(),
        link_count=len(extracted.links),
        fetch_ms=fetch_ms,
        updated=extracted.updated or extracted.published,
        authority=None,
        is_ours=is_our_url(url) if is_ours is None else is_ours,
    )


def page_from_row(row: RsPage) -> PageDoc:
    return PageDoc(
        url=row.url,
        title=row.title or "",
        h1=row.h1 or "",
        headings=row.headings_text or "",
        body=row.body or "",
        word_count=row.word_count or 0,
        table_count=row.table_count or 0,
        has_faq=bool(row.has_faq),
        link_count=row.link_count or 0,
        fetch_ms=row.fetch_ms,
        updated=row.updated_label,
        authority=row.authority,
        is_ours=is_our_url(row.url),
    )


def store_fetched_page(session: Session, fetched: FetchResult) -> None:
    if fetched.status != "fetched" or not fetched.html:
        upsert_page(
            session,
            url=fetched.url,
            domain=domain_of(fetched.url),
            status=fetched.status,
            error=fetched.error,
            raw_html=fetched.html or "",
            fetch_ms=fetched.fetch_ms,
            authority_source="unavailable",
        )
        return
    doc = page_from_html(fetched.url, fetched.html, fetch_ms=fetched.fetch_ms)
    upsert_page(
        session,
        url=doc.url,
        domain=domain_of(doc.url),
        status="fetched",
        error=None,
        raw_html=fetched.html,
        title=doc.title,
        h1=doc.h1,
        headings_text=doc.headings,
        body=doc.body,
        word_count=doc.word_count,
        table_count=doc.table_count,
        has_faq=doc.has_faq,
        link_count=doc.link_count,
        fetch_ms=doc.fetch_ms,
        updated_label=doc.updated,
        authority=None,
        authority_source="unavailable",
    )


def collect_corpus(
    session: Session,
    keywords: list[Keyword],
    provider: SerpProvider,
    fetcher,
    *,
    depth: int = 10,
    max_pages: int = 1000,
) -> CollectResult:
    if provider.synthetic:
        warnings = [
            "Mock SERP is synthetic. These are not Google results and must not be reported as Google ranks."
        ]
    elif not provider.is_google:
        warnings = [
            f"{provider.name} is not Google. Positions from this collect are {provider.name} order, not Google order."
        ]
    else:
        warnings = []
    save_keywords(session, keywords)
    stored: set[str] = set()
    hit_count = 0
    queries_done = 0
    for keyword in keywords:
        if len(stored) >= max_pages:
            warnings.append(f"Stopped at {max_pages} stored pages. Later queries were not fetched.")
            break
        hits = provider.search(keyword.query, depth)
        replace_serp(session, keyword.query, hits)
        hit_count += len(hits)
        queries_done += 1
        for hit in hits:
            try:
                url = normalize_url(hit.url)
            except ValueError:
                continue
            if url in stored:
                continue
            if len(stored) >= max_pages:
                warnings.append(f"Page cap {max_pages} reached while storing results for '{keyword.query}'.")
                break
            stored.add(url)
            try:
                fetched = fetcher(url)
            except Exception as exc:  # noqa: BLE001
                fetched = FetchResult(url=url, html="", status="error", error=str(exc))
            store_fetched_page(session, fetched)
    return CollectResult(
        queries=queries_done,
        serp_hits=hit_count,
        pages_stored=len(stored),
        provider=provider.name,
        synthetic=provider.synthetic,
        is_google=provider.is_google,
        warnings=warnings,
    )


def attach_authority(session: Session, client) -> int:
    """Fill the authority feature from a backlink client. Failures stay unset."""
    updated = 0
    cache: dict[str, float | None] = {}
    pages = session.query(RsPage).all()
    for page in pages:
        domain = page.domain or domain_of(page.url)
        if not domain:
            continue
        if domain not in cache:
            try:
                cache[domain] = client.score(domain)
            except Exception:
                cache[domain] = None
        if cache[domain] is None:
            page.authority = None
            page.authority_source = "unavailable"
            continue
        page.authority = cache[domain]
        page.authority_source = getattr(client, "source", "backlinks")
        updated += 1
    session.commit()
    return updated


def calibrate(session: Session, keywords: list[Keyword], today: date | None = None):
    intents = {row.query: row.intent for row in keywords}
    groups = []
    providers: set[str] = set()
    synthetic = False
    is_google = True
    authority_used = False
    for query in queries_with_serp(session):
        hits = serp_for_query(session, query)
        pages: list[PageDoc] = []
        positions: list[int] = []
        for hit in hits:
            providers.add(hit.provider)
            synthetic = synthetic or hit.synthetic
            is_google = is_google and hit.is_google
            row = page_by_url(session, normalize_url(hit.url)) if _can_normalize(hit.url) else page_by_url(session, hit.url)
            if row is None or row.status != "fetched" or not row.body and not row.title:
                continue
            doc = page_from_row(row)
            if doc.authority is not None:
                authority_used = True
            pages.append(doc)
            positions.append(hit.position)
        if len(pages) >= 2:
            groups.append((query, intents.get(query, "commercial"), pages, positions))
    report = fit_weights(groups, today=today, authority_used=authority_used)
    provider_name = ",".join(sorted(providers)) if providers else "none"
    if not groups:
        label = "not_calibrated"
    elif synthetic:
        label = "mock_calibration_not_google"
    elif not is_google:
        label = "calibrated_on_non_google_serp"
    else:
        label = "calibrated_on_google_serp"
    save_weights(
        session,
        weights_json=report.weights,
        spearman=report.spearman,
        ndcg_at_10=report.ndcg_at_10,
        n_queries=report.n_queries,
        n_pairs=report.n_pairs,
        provider=provider_name,
        synthetic=synthetic or report.n_queries == 0,
        is_google=bool(is_google and groups and not synthetic),
        authority_used=authority_used,
        label=label,
    )
    return report, label, provider_name


def run_test(
    session: Session,
    keywords: list[Keyword],
    new_page: PageDoc,
    *,
    replaces: str | None = None,
    today: date | None = None,
    source_label: str = "",
) -> list[QueryDelta]:
    stored = latest_weights(session)
    weights = stored.weights_json if stored else HAND_WEIGHTS
    weight_label = stored.label if stored else "hand_weights_not_calibrated"
    synthetic = bool(stored.synthetic) if stored else False
    is_google = bool(stored.is_google) if stored else False
    target_url = normalize_url(replaces) if replaces else new_page.url
    ours = PageDoc(
        url=target_url,
        title=new_page.title,
        h1=new_page.h1,
        headings=new_page.headings,
        body=new_page.body,
        word_count=new_page.word_count,
        table_count=new_page.table_count,
        has_faq=new_page.has_faq,
        link_count=new_page.link_count,
        fetch_ms=new_page.fetch_ms,
        updated=new_page.updated,
        authority=new_page.authority,
        is_ours=True,
    )
    deltas: list[QueryDelta] = []
    intents = {row.query: row.intent for row in keywords}
    for keyword in keywords:
        hits = serp_for_query(session, keyword.query)
        if not hits:
            deltas.append(
                QueryDelta(
                    query=keyword.query,
                    intent=keyword.intent,
                    before_predicted_position=None,
                    after_predicted_position=None,
                    delta=None,
                    competitor_gaps=[],
                    note="No stored SERP for this query. No position was invented.",
                )
            )
            continue
        before_pages = _candidates(session, hits, replacement=None)
        after_pages = _candidates(session, hits, replacement=ours)
        intent = intents.get(keyword.query, keyword.intent)
        before_scored = score_pages(keyword.query, intent, before_pages, weights, today)
        after_scored = score_pages(keyword.query, intent, after_pages, weights, today)
        before_pos = best_our_position(before_scored)
        after_pos = best_our_position(after_scored)
        delta = None
        if before_pos is not None and after_pos is not None:
            delta = before_pos - after_pos
        gaps = competitor_gaps(keyword.query, ours, before_pages)
        before_ours = next((page for page in before_pages if page.url == ours.url), None)
        moves: list[str] = []
        if before_ours is not None:
            from aeo.ranksim.attribution import feature_moves

            peers = [page for page in before_pages if page.url != ours.url][:8]
            moves = feature_moves(keyword.query, intent, before_ours, ours, peers, today=today, weights=weights)
        note = "Predicted position on our index. Not a live Google rank."
        if before_pos is None:
            note = "SmartCrew was not in the stored results. Before position is blank. " + note
        deltas.append(
            QueryDelta(
                query=keyword.query,
                intent=intent,
                before_predicted_position=before_pos,
                after_predicted_position=after_pos,
                delta=delta,
                competitor_gaps=gaps,
                note=note,
                feature_moves=moves,
            )
        )
    save_test_run(
        session,
        source_label=source_label or ours.url,
        weight_label=weight_label,
        synthetic_corpus=synthetic,
        is_google=is_google,
        summary_json={
            "queries": len(deltas),
            "improved": sum(1 for row in deltas if row.delta is not None and row.delta > 0),
            "regressed": sum(1 for row in deltas if row.delta is not None and row.delta < 0),
            "rows": [
                {
                    "query": row.query,
                    "before_predicted_position": row.before_predicted_position,
                    "after_predicted_position": row.after_predicted_position,
                    "delta": row.delta,
                    "competitor_gaps": row.competitor_gaps,
                    "feature_moves": row.feature_moves,
                    "note": row.note,
                }
                for row in deltas
            ],
        },
    )
    return deltas


def track_ranks(
    session: Session,
    keywords: list[Keyword],
    provider: SerpProvider,
    *,
    depth: int = 10,
    checked_on: str | None = None,
) -> list[RsRankCheck]:
    day = checked_on or date.today().isoformat()
    rows: list[RsRankCheck] = []
    for keyword in keywords:
        hits = provider.search(keyword.query, depth)
        ours = [hit for hit in hits if is_our_url(hit.url)]
        best = min(ours, key=lambda hit: hit.position) if ours else None
        rows.append(
            RsRankCheck(
                query=keyword.query,
                checked_on=day,
                our_position=best.position if best else None,
                our_url=best.url if best else None,
                provider=provider.name,
                synthetic=provider.synthetic,
                is_google=provider.is_google,
                top_json=[
                    {
                        "position": hit.position,
                        "url": hit.url,
                        "title": hit.title,
                        "provider": hit.provider,
                        "synthetic": hit.synthetic,
                        "is_google": hit.is_google,
                    }
                    for hit in hits
                ],
            )
        )
    save_rank_checks(session, rows)
    return rows


def competitor_gaps(query: str, ours: PageDoc, competitors: list[PageDoc]) -> list[str]:
    rivals = [page for page in competitors if not page.is_ours][:5]
    if not rivals:
        return ["No competitor pages are stored for this query."]
    gaps: list[str] = []
    if any(page.table_count for page in rivals) and not ours.table_count:
        gaps.append("Stored competitors use a table. This page has no table.")
    if any(page.has_faq for page in rivals) and not ours.has_faq:
        gaps.append("Stored competitors have an FAQ. This page has no FAQ.")
    rival_words = [page.word_count for page in rivals if page.word_count]
    if rival_words and ours.word_count < int(sum(rival_words) / len(rival_words) * 0.6):
        gaps.append(
            f"This page has {ours.word_count} words. Stored competitors average {int(sum(rival_words) / len(rival_words))}."
        )
    from aeo.textutil import salient_terms

    their_terms: list[str] = []
    for page in rivals:
        for term in salient_terms(f"{page.title} {page.h1}"):
            if term not in their_terms:
                their_terms.append(term)
    ours_terms = set(salient_terms(f"{ours.title} {ours.h1} {ours.headings} {ours.body[:3000]}"))
    missing = [term for term in their_terms if term not in ours_terms and term not in salient_terms(query)][:8]
    if missing:
        gaps.append("Heading terms on stored competitors that this page lacks: " + ", ".join(missing) + ".")
    return gaps


def _candidates(session: Session, hits: list, replacement: PageDoc | None) -> list[PageDoc]:
    pages: list[PageDoc] = []
    replaced = False
    for hit in hits:
        url = hit.url
        try:
            url = normalize_url(hit.url)
        except ValueError:
            pass
        if replacement is not None and url == replacement.url:
            pages.append(replacement)
            replaced = True
            continue
        row = page_by_url(session, url)
        if row is None or row.status != "fetched":
            pages.append(
                PageDoc(url=url, title=getattr(hit, "title", "") or "", body=getattr(hit, "snippet", "") or "", is_ours=is_our_url(url))
            )
            continue
        pages.append(page_from_row(row))
    if replacement is not None and not replaced:
        pages.append(replacement)
    return pages


def _schema_types(objects: list) -> set[str]:
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            kind = node.get("@type")
            if isinstance(kind, str):
                found.add(kind)
            elif isinstance(kind, list):
                found.update(str(item) for item in kind)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(objects)
    return found


def _can_normalize(url: str) -> bool:
    try:
        normalize_url(url)
    except ValueError:
        return False
    return True
