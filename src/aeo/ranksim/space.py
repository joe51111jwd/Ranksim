"""How the client stacks up against imported competitors.

Real shares use the stored Google file only. Predicted shares use scored pages
only. A domain with no stored HTML does not get a predicted rank.
"""

from __future__ import annotations

import csv
import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from aeo.errors import ProviderConfigError
from aeo.crawl.urls import domain_of
from aeo.ranksim.features import raw_features
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.lambdamart import RankExample, predict_scores
from aeo.ranksim.whatif import _lookup_page


@dataclass
class PlayerLine:
    domain: str
    real_position: float | None
    real_url: str | None
    predicted_rank: int | None
    predicted_of: int | None
    predicted_url: str | None


@dataclass
class QueryBoard:
    query: str
    players: list[PlayerLine]
    scored_pages: int


@dataclass
class DomainShare:
    domain: str
    tracked_queries: int
    real_top3: int
    real_top10: int  # labeled positions 1 to 10 only
    predicted_queries: int
    predicted_top3: int  # kept for existing displays: rank 3 or better among however many pages were scored
    predicted_top10: int
    # Top k where it means something: only queries where more than k pages were scored (a rank of 3
    # among 3 pages is not "top 3"). Use these with their own denominators.
    predicted_top3_qualified: int = 0
    predicted_top3_queries: int = 0
    predicted_top10_qualified: int = 0
    predicted_top10_queries: int = 0
    kind: str = ""  # business (listed by the user), serp (taken from the labeled results), both, or "" (client)


@dataclass
class HeadToHead:
    """The client's record against one competitor: wins are queries where the client ranks above it."""

    domain: str
    real_queries: int
    real_wins: int
    real_losses: int
    real_ties: int
    predicted_queries: int
    predicted_wins: int
    predicted_losses: int
    predicted_ties: int
    kind: str = ""


@dataclass
class FixPage:
    url: str
    query: str
    predicted_rank: int
    predicted_of: int


@dataclass
class SpaceReport:
    our_domain: str
    boards: list[QueryBoard]
    shares: list[DomainShare]
    headtohead: list[HeadToHead]
    fixes: list[FixPage]
    content_gap: str
    link_gap: str
    authority_gap: str
    notes: list[str] = field(default_factory=list)


def domains_from_labels(labels: list[SerpLabel], our_domain: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for label in labels:
        host = domain_of(label.url)
        if not host or host == our_domain or host in seen:
            continue
        seen.add(host)
        found.append(host)
    return found


def parse_competitor_text(text: str, rejected: list[tuple[int, str, str]] | None = None) -> list[str]:
    """Domains from pasted text or a CSV (first column), one per line; duplicates collapse.

    Lines are read with csv.reader, so a quoted first field with commas is kept whole, and a header
    row (domain, url, host, site) is skipped. Lines that are not a public site are left out and, when
    ``rejected`` is given, appended to it as (line number, text, reason) so callers can show them.
    """
    domains: list[str] = []
    seen: set[str] = set()
    for number, cells in enumerate(csv.reader(text.splitlines()), start=1):
        first = (cells[0] if cells else "").strip()
        if not first or first.startswith("#"):
            continue
        if number == 1 and first.lower() in {"domain", "url", "host", "site", "website"}:
            continue
        host, reason = competitor_host(first)
        if not host:
            if rejected is not None:
                rejected.append((number, first, reason))
            continue
        if host in seen:
            continue
        seen.add(host)
        domains.append(host)
    return domains


_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_NOT_SITES = ("localhost", "local", "internal", "localdomain", "home.arpa")


def competitor_host(value: str) -> tuple[str, str]:
    """(host, "") for a public site name or URL, else ("", reason). www. is dropped; an
    international name is stored in its IDNA (punycode) form."""
    text = value.strip()
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme not in {"http", "https"}:
            return "", f"unsupported scheme {parts.scheme}"
        netloc = parts.netloc
    else:
        netloc = text.split("/", 1)[0]
    if "@" in netloc:
        return "", "credentials are not part of a site name"
    host = netloc
    if host.startswith("["):
        return "", "an IP address is not a site"
    if ":" in host:
        return "", "a port is not part of a site name"
    host = host.strip().strip(".").lower()
    if not host:
        return "", "no host"
    try:
        ipaddress.ip_address(host)
        return "", "an IP address is not a site"
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return "", "not a valid international domain name"
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) < 2:
        return "", "a site name needs a dot (example: acme.com)"
    if any(not _LABEL.match(label) for label in labels) or len(host) > 253:
        return "", "not a valid host name"
    if labels[-1].isdigit():
        return "", "an IP address is not a site"
    if labels[-1] in _NOT_SITES or ".".join(labels[-2:]) in _NOT_SITES:
        return "", "a local network name is not a public site"
    return host, ""


def read_competitors(path: str | Path) -> list[str]:
    file_path = Path(path)
    if not file_path.is_file():
        return []
    return parse_competitor_text(file_path.read_text(encoding="utf-8"))


def read_competitor_kinds(path: str | Path) -> dict[str, str]:
    """domain -> kind from a competitors file with a ``kind`` column (business, serp or both)."""
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    with file_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "kind" not in [name.strip().lower() for name in reader.fieldnames]:
            return {}
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        kinds = {}
        for raw in reader:
            host, _reason = competitor_host(raw.get(fields.get("domain", "domain")) or "")
            if host:
                kinds[host] = (raw.get(fields["kind"]) or "").strip().lower()
        return kinds


def write_competitors(path: str | Path, domains: list[str], source: str, kinds: dict[str, str] | None = None) -> None:
    """``kinds``: business (listed by the user), serp (taken from labeled results) or both."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["domain", "source", "kind"])
        writer.writeheader()
        for domain in domains:
            writer.writerow({"domain": domain, "source": source, "kind": (kinds or {}).get(domain, "")})


def build_space(
    labels: list[SerpLabel],
    pages: dict,
    models: dict,
    competitors: list[str],
    *,
    our_domain: str = "smartcrewhq.com",
    today: date | None = None,
    intents: dict[str, str] | None = None,
    kinds: dict[str, str] | None = None,
) -> SpaceReport:
    """``intents`` (query to intent) should be ``intents.query_intents(labels).intents``, the same
    source training used; without it each query gets the shared rule or the default."""
    from aeo.ranksim.intents import intent_for

    today = today or date.today()
    players = [our_domain] + [domain for domain in competitors if domain != our_domain]
    queries = list(dict.fromkeys(row.query for row in labels))
    boards: list[QueryBoard] = []
    real_best: dict[tuple[str, str], tuple[float, str]] = {}
    predicted_best: dict[tuple[str, str], tuple[int, int, str]] = {}
    our_losses: list[tuple[str, str, object, object]] = []
    for query in queries:
        query_labels = [row for row in labels if row.query == query]
        for label in query_labels:
            host = domain_of(label.url)
            key = (query, host)
            current = real_best.get(key)
            if current is None or label.position < current[0]:
                real_best[key] = (label.position, label.url)
        docs = []
        for label in query_labels:
            page = _lookup_page(pages, label.url)
            if page is not None:
                docs.append(page)
        scored = 0
        ranks: dict[int, int] = {}
        if len(docs) >= 2 and models.get(query) is not None:
            raw = raw_features(query, intent_for(query, intents), docs, today)
            examples = [RankExample(query, doc.url, 0.0, row) for doc, row in zip(docs, raw)]
            scores = predict_scores(models[query], examples)
            order = sorted(range(len(docs)), key=lambda index: (-scores[id(examples[index])], index))
            ranks = {index: place for place, index in enumerate(order, start=1)}
            scored = len(docs)
            by_domain: dict[str, tuple[int, str]] = {}
            for index, doc in enumerate(docs):
                host = domain_of(doc.url)
                current = by_domain.get(host)
                if current is None or ranks[index] < current[0]:
                    by_domain[host] = (ranks[index], doc.url)
            for host, (rank, url) in by_domain.items():
                predicted_best[(query, host)] = (rank, scored, url)
        lines = []
        for domain in players:
            real = real_best.get((query, domain))
            predicted = predicted_best.get((query, domain))
            if real is None and predicted is None:
                continue
            lines.append(
                PlayerLine(
                    domain=domain,
                    real_position=real[0] if real else None,
                    real_url=real[1] if real else None,
                    predicted_rank=predicted[0] if predicted else None,
                    predicted_of=predicted[1] if predicted else None,
                    predicted_url=predicted[2] if predicted else None,
                )
            )
        lines.sort(key=lambda row: (row.real_position is None, row.real_position or 999, row.predicted_rank or 999))
        boards.append(QueryBoard(query=query, players=lines, scored_pages=scored))
        ours_p = predicted_best.get((query, our_domain))
        for domain in players:
            if domain == our_domain:
                continue
            theirs = predicted_best.get((query, domain))
            if ours_p and theirs and ours_p[0] > theirs[0]:
                our_doc = _lookup_page(pages, ours_p[2])
                their_doc = _lookup_page(pages, theirs[2])
                if our_doc is not None and their_doc is not None:
                    our_losses.append((query, domain, our_doc, their_doc))

    kinds = kinds or {}
    shares = []
    for domain in players:
        real_top3 = sum(1 for query in queries if (pos := real_best.get((query, domain))) and pos[0] <= 3)
        real_top10 = sum(1 for query in queries if (pos := real_best.get((query, domain))) and pos[0] <= 10)
        pred_queries = [query for query in queries if (query, domain) in predicted_best]
        over3 = [query for query in pred_queries if predicted_best[(query, domain)][1] > 3]
        over10 = [query for query in pred_queries if predicted_best[(query, domain)][1] > 10]
        shares.append(
            DomainShare(
                domain=domain,
                tracked_queries=len(queries),
                real_top3=real_top3,
                real_top10=real_top10,
                predicted_queries=len(pred_queries),
                predicted_top3=sum(1 for query in pred_queries if predicted_best[(query, domain)][0] <= 3),
                predicted_top10=sum(1 for query in pred_queries if predicted_best[(query, domain)][0] <= 10),
                predicted_top3_qualified=sum(1 for query in over3 if predicted_best[(query, domain)][0] <= 3),
                predicted_top3_queries=len(over3),
                predicted_top10_qualified=sum(1 for query in over10 if predicted_best[(query, domain)][0] <= 10),
                predicted_top10_queries=len(over10),
                kind="" if domain == our_domain else kinds.get(domain, ""),
            )
        )
    head = []
    for domain in players:
        if domain == our_domain:
            continue
        real_w = real_l = real_t = real_n = 0
        pred_w = pred_l = pred_t = pred_n = 0
        for query in queries:
            ours_r = real_best.get((query, our_domain))
            theirs_r = real_best.get((query, domain))
            if ours_r and theirs_r:
                real_n += 1
                if ours_r[0] < theirs_r[0]:
                    real_w += 1
                elif ours_r[0] > theirs_r[0]:
                    real_l += 1
                else:
                    real_t += 1
            ours_p = predicted_best.get((query, our_domain))
            theirs_p = predicted_best.get((query, domain))
            if ours_p and theirs_p:
                pred_n += 1
                if ours_p[0] < theirs_p[0]:
                    pred_w += 1
                elif ours_p[0] > theirs_p[0]:
                    pred_l += 1
                else:
                    pred_t += 1
        head.append(
            HeadToHead(
                domain=domain,
                real_queries=real_n,
                real_wins=real_w,
                real_losses=real_l,
                real_ties=real_t,
                predicted_queries=pred_n,
                predicted_wins=pred_w,
                predicted_losses=pred_l,
                predicted_ties=pred_t,
                kind=kinds.get(domain, ""),
            )
        )
    head.sort(key=lambda row: (-row.real_queries, row.domain))
    fixes = _fix_pages(predicted_best, our_domain)
    content_gap, link_gap, authority_gap = _gaps(our_losses, pages)
    notes = [
        "REAL counts use the stored Google file only.",
        "PREDICTED ranks are among stored pages for that query, not a live Google slot.",
        "A blank predicted rank means that domain had no stored HTML on that query.",
    ]
    if not any(getattr(doc, "authority", None) not in (None, 0) for doc in pages.values()):
        notes.append("Authority was not measured. No backlink key is set, so there is no authority gap number.")
    return SpaceReport(
        our_domain=our_domain,
        boards=boards,
        shares=shares,
        headtohead=head,
        fixes=fixes,
        content_gap=content_gap,
        link_gap=link_gap,
        authority_gap=authority_gap,
        notes=notes,
    )


def render_space_text(report: SpaceReport) -> str:
    lines = [
        "Space view. REAL rows are the stored Google file. PREDICTED rows are model ranks on stored pages.",
        f"Client domain: {report.our_domain}",
        "",
        "Share of tracked queries",
        "domain\tkind\treal_top3\treal_top10\tpredicted_top3\tpredicted_top10\tpredicted_queries",
        "PREDICTED top k counts only queries where more than k pages were scored; REAL top 10 is labeled position 1 to 10.",
    ]
    for row in report.shares:
        pred3 = "n/a" if row.predicted_top3_queries == 0 else f"{row.predicted_top3_qualified} of {row.predicted_top3_queries}"
        pred10 = "n/a" if row.predicted_top10_queries == 0 else f"{row.predicted_top10_qualified} of {row.predicted_top10_queries}"
        lines.append(
            f"{row.domain}\t{row.kind or ('client' if row.domain == report.our_domain else 'unknown')}\t"
            f"REAL {row.real_top3} of {row.tracked_queries}\tREAL {row.real_top10} of {row.tracked_queries}\t"
            f"PREDICTED {pred3}\tPREDICTED {pred10}\t{row.predicted_queries}"
        )
    lines.append("")
    lines.append(f"Head to head: {report.our_domain}'s record against each competitor (a win is a query where {report.our_domain} ranks above it)")
    lines.append("competitor\tkind\treal_record\tpredicted_record")
    for row in report.headtohead:
        if row.real_queries == 0 and row.predicted_queries == 0:
            continue
        real = (
            "n/a" if row.real_queries == 0
            else f"REAL {report.our_domain} won {row.real_wins}, lost {row.real_losses}, tied {row.real_ties} of {row.real_queries} queries"
        )
        pred = (
            "n/a"
            if row.predicted_queries == 0
            else f"PREDICTED {report.our_domain} won {row.predicted_wins}, lost {row.predicted_losses}, tied {row.predicted_ties} of {row.predicted_queries} queries"
        )
        lines.append(f"{row.domain}\t{row.kind or 'unknown'}\t{real}\t{pred}")
    lines.append("")
    lines.append("Per query")
    for board in report.boards:
        lines.append(f"Query: {board.query} ({board.scored_pages} scored pages)")
        for player in board.players:
            real = "REAL n/a" if player.real_position is None else f"REAL {player.real_position:g} {player.real_url}"
            if player.predicted_rank is None:
                pred = "PREDICTED n/a"
            else:
                pred = f"PREDICTED {player.predicted_rank} of {player.predicted_of} {player.predicted_url}"
            lines.append(f"  {player.domain}\t{real}\t{pred}")
    lines.append("")
    lines.append("Gaps")
    lines.append(report.content_gap)
    lines.append(report.link_gap)
    lines.append(report.authority_gap)
    lines.append("Pages to fix first (worst PREDICTED rank on a stored query):")
    if not report.fixes:
        lines.append("  No client page had a predicted rank.")
    for fix in report.fixes:
        lines.append(f"  PREDICTED {fix.predicted_rank} of {fix.predicted_of} on {fix.query} {fix.url}")
    lines.append("")
    for note in report.notes:
        lines.append(note)
    return "\n".join(lines) + "\n"


def _fix_pages(predicted_best: dict, our_domain: str) -> list[FixPage]:
    rows = []
    for (query, domain), (rank, total, url) in predicted_best.items():
        if domain != our_domain:
            continue
        rows.append(FixPage(url=url, query=query, predicted_rank=rank, predicted_of=total))
    rows.sort(key=lambda row: (-row.predicted_rank, row.query))
    return rows[:5]


def _gaps(losses: list, pages: dict) -> tuple[str, str, str]:
    if not losses:
        content = "Content gap: no predicted loss had both pages stored, so no content gap was computed."
        link = "Link gap: no predicted loss had both pages stored, so no in-crawl link gap was computed."
    else:
        word_gap = None
        link_gap = None
        for query, domain, ours, theirs in losses:
            if ours.word_count and theirs.word_count and theirs.word_count > ours.word_count:
                delta = theirs.word_count - ours.word_count
                if word_gap is None or delta > word_gap[0]:
                    word_gap = (delta, query, domain, ours.url, ours.word_count, theirs.word_count)
            if ours.crawl_inlinks is not None and theirs.crawl_inlinks is not None and theirs.crawl_inlinks > ours.crawl_inlinks:
                delta = theirs.crawl_inlinks - ours.crawl_inlinks
                if link_gap is None or delta > link_gap[0]:
                    link_gap = (delta, query, domain, ours.url, ours.crawl_inlinks, theirs.crawl_inlinks)
        if word_gap is None:
            content = "Content gap: stored pages on predicted losses did not show a longer competitor page."
        else:
            _delta, query, domain, url, ours_words, their_words = word_gap
            content = (
                f"Content gap: on {query}, {domain} has {their_words} words versus {ours_words} on {url}."
            )
        if link_gap is None:
            link = "Link gap: in-crawl inlinks were missing on one side, or the competitor did not have more of them."
        else:
            _delta, query, domain, url, ours_links, their_links = link_gap
            link = (
                f"Link gap: on {query}, {domain} has {their_links} in-crawl inlinks versus {ours_links} on {url}. "
                "These are links inside this crawl, not web-wide backlinks."
            )
    authority_seen = any(getattr(doc, "authority", None) not in (None, 0, 0.0) for doc in pages.values())
    if not authority_seen:
        authority = "Authority gap: not measured. No backlink source is configured."
    else:
        authority = "Authority gap: backlink scores exist on some pages, but this report does not invent a gap beyond those stored scores."
    return content, link, authority


def crawl_competitor_homepages(settings, domains: list[str], database: str, *, max_fetches: int = 20) -> tuple[int, int, list[str]]:
    """Fetch a homepage only when we do not already have HTML for that host.

    Returns (fetched, skipped_already_stored, errors). Robots blocks are errors, not invented pages.
    """
    from aeo.crawl.fetch import Crawler
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import RsPage, open_session

    session = open_session(database)
    have = set()
    try:
        for row in session.query(RsPage).all():
            if row.status == "fetched" and (row.raw_html or "").strip():
                have.add(domain_of(row.url))
    finally:
        session.close()
    crawler = Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=max(settings.crawl_delay_seconds, 0.4),
        allow_private=settings.crawl_allow_private,
        timeout=min(settings.request_timeout_seconds, 15.0),
    )
    fetched = 0
    skipped = 0
    errors: list[str] = []
    try:
        for domain in domains:
            if domain in have:
                skipped += 1
                continue
            if fetched >= max_fetches:
                errors.append(f"Stopped at {max_fetches} fetches. Remaining domains were not crawled.")
                break
            url = f"https://{domain}/"
            try:
                result = crawler.fetch(url)
            except ProviderConfigError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{domain} {exc}")
                continue
            fetched += 1
            if result.blocked or result.error or not result.html:
                errors.append(f"{domain} {result.error or 'blocked'}")
                continue
            session = open_session(database)
            try:
                store_fetched_page(session, FetchResult(url=url, html=result.html, status="fetched"))
            finally:
                session.close()
            have.add(domain)
    finally:
        crawler.close()
    return fetched, skipped, errors
