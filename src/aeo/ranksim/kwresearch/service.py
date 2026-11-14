"""Keyword volume chain, difficulty, import, and report."""

from __future__ import annotations

import csv
import io
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aeo.ranksim.kwresearch.difficulty import (
    DifficultyResult,
    load_composition,
    load_majestic_ranks,
    load_tranco_ranks,
    score_difficulty,
)
from aeo.ranksim.kwresearch.http import USER_AGENT, PoliteHttp, scrub
from aeo.ranksim.kwresearch.models import (
    Attempt,
    VolumeRecord,
    blank_record,
    norm_keyword,
    now_iso,
    parse_float,
    parse_int,
    provider_id,
)
from aeo.ranksim.kwresearch.providers import (
    PaidConfig,
    SemrushTable,
    fetch_dataforseo,
    fetch_google_ads,
    fetch_seodata,
    guess_semrush_domains,
    load_domain_file,
    overview_url,
    parse_semrush_overview,
    semrush_record,
)
from aeo.ranksim.kwresearch.store import KeywordStore
from aeo.ranksim.localindex.credits import CreditSpend, search_credits
from aeo.ranksim.serp import SerperProvider, parse_serper

SEMRUSH_FETCH_CAP = 40
DOMAIN_FILE = Path(__file__).resolve().parent / "semrush_overview_domains.txt"
Log = Callable[[str], None]


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def resolve_cache(project: str | None, cache: str | None, projects_root: str | Path | None = None) -> Path:
    if cache:
        return Path(cache)
    from aeo.ranksim.project import projects_root as root_fn
    from aeo.ranksim.project import slugify

    base = root_fn(projects_root)
    if project:
        return base / slugify(project) / "keyword_research.sqlite"
    env = os.environ.get("RANKSIM_KEYWORD_CACHE", "").strip()
    if env:
        return Path(env)
    return base / "_keywords" / "keyword_research.sqlite"


def read_keyword_list(keywords: str | None = None, path: str | None = None) -> list[str]:
    if keywords and path:
        raise ValueError("Pass keywords or a file, not both.")
    if keywords:
        items = [" ".join(part.split()) for part in keywords.split(",")]
        return _unique([item for item in items if item])
    if not path:
        raise ValueError("Pass --keywords or --file.")
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"No keyword file at {file_path}.")
    raw = file_path.read_text(encoding="utf-8")
    if file_path.suffix.lower() == ".csv" or raw.lstrip().lower().startswith("keyword,"):
        reader = csv.DictReader(io.StringIO(raw))
        fields = {name.strip().lower(): name for name in (reader.fieldnames or [])}
        column = fields.get("keyword")
        if column:
            return _unique([" ".join((row.get(column) or "").split()) for row in reader if (row.get(column) or "").strip()])
    lines = []
    for line in raw.splitlines():
        text = " ".join(line.split())
        if text and not text.startswith("#"):
            lines.append(text)
    return _unique(lines)


def import_volumes(csv_path: str | Path, cache: str | Path) -> tuple[int, int]:
    """Load a research CSV into the cache. Source, URL, and data date stay as written."""
    file_path = Path(csv_path)
    with file_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "keyword" not in {name.strip().lower() for name in reader.fieldnames}:
            raise ValueError("The CSV needs a keyword column.")
        rows = list(reader)
    store = KeywordStore(cache)
    stored = 0
    blanks = 0
    try:
        for row in rows:
            record = _record_from_csv(row)
            if record is None:
                continue
            store.put_volume(record)
            stored += 1
            if not record.usable():
                blanks += 1
    finally:
        store.close()
    return stored, blanks


def keyword_volume(
    keywords: list[str],
    cache: str | Path,
    *,
    refresh: bool = False,
    client: httpx.Client | None = None,
    delay: float = 1.0,
    retries: int = 3,
    paid: PaidConfig | None = None,
    semrush_domains: list[str] | None = None,
    log: Log | None = None,
) -> list[VolumeRecord]:
    words = _unique(keywords)
    paid = paid or PaidConfig.from_env()
    store = KeywordStore(cache)
    owns = client is None
    http_client = client
    results: dict[str, VolumeRecord] = {}
    pending: list[str] = []
    try:
        for keyword in words:
            cached = store.get_volume(keyword)
            if cached is not None and not refresh and store.volume_is_fresh(cached):
                cached.keyword = keyword
                results[keyword] = cached
            else:
                pending.append(keyword)
        if not pending:
            return [results[keyword] for keyword in words]
        if http_client is None:
            http_client = httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
            owns = True
        http = PoliteHttp(http_client, delay=delay, retries=retries)
        still: list[str] = []
        previous = {keyword: store.get_volume(keyword) for keyword in pending}
        for keyword in pending:
            record, attempt = fetch_seodata(http, keyword)
            if record is not None and record.usable():
                _keep_semrush_columns(record, previous.get(keyword))
                record.attempts = [attempt]
                store.put_volume(record)
                results[keyword] = record
            else:
                still.append(keyword)
                results[keyword] = blank_record(keyword, [attempt])
        still = _apply_semrush(still, results, store, http, semrush_domains or [], refresh=refresh, log=log)
        if still and paid.dataforseo_ready():
            filled = fetch_dataforseo(http, still, paid)
            still = _take(still, results, store, filled)
        elif still:
            for keyword in still:
                _add_attempt(results[keyword], Attempt("dataforseo", "skip", "DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD are not set"))
        if still and paid.google_ads_ready():
            filled = fetch_google_ads(http, still, paid)
            still = _take(still, results, store, filled)
        elif still:
            for keyword in still:
                _add_attempt(results[keyword], Attempt("google_ads", "skip", "Google Ads Keyword Planner keys are not all set"))
        for keyword in still:
            kept = previous.get(keyword)
            if kept is not None and kept.usable():
                note = results[keyword].blank_reason
                if note and "Refresh kept this stored volume" not in kept.notes:
                    kept.notes = (kept.notes + f" Refresh kept this stored volume. Live sources: {note}.").strip()
                results[keyword] = kept
                store.put_volume(kept)
            else:
                record = results[keyword]
                record.blank_reason = "; ".join(
                    f"{item.provider}: {item.detail}" for item in record.attempts if item.outcome != "hit"
                ) or "no source returned a monthly volume"
                record.fetched_at = now_iso()
                record.sticky = False
                store.put_volume(record)
        return [results[keyword] for keyword in words]
    finally:
        store.close()
        if owns and http_client is not None:
            http_client.close()


def keyword_difficulty(
    keywords: list[str],
    cache: str | Path,
    *,
    refresh: bool = False,
    api_key: str | None = None,
    credit_cap: int = 50,
    client: httpx.Client | None = None,
    tranco_path: str | None = None,
    majestic_path: str | None = None,
    today: str | None = None,
    log: Log | None = None,
) -> list[DifficultyResult]:
    words = _unique(keywords)
    day = today or utc_today()
    say = log or (lambda _line: None)
    store = KeywordStore(cache)
    per = search_credits(10)
    tranco = _maybe_ranks(load_tranco_ranks, tranco_path or os.environ.get("RANKSIM_TRANCO_PATH"))
    majestic = _maybe_ranks(load_majestic_ranks, majestic_path or os.environ.get("RANKSIM_MAJESTIC_PATH"))
    composition = load_composition()
    key = (api_key or "").strip() or None
    uncached = [keyword for keyword in words if refresh or store.get_serp(keyword, day) is None]
    say(
        "Serper credit estimate before spending: "
        f"{len(uncached) * per} credits ({len(uncached)} uncached keywords, {per} credit each, num=10, gl=us, hl=en). "
        f"Cached for {day}: {len(words) - len(uncached)}. Cap {credit_cap}."
    )
    if not key:
        say("SERPER_API_KEY is not set. No Serper calls. The key is not printed.")
    credits = CreditSpend(credit_cap)
    owns = client is None and bool(key)
    http_client = client
    provider: SerperProvider | None = None
    if key:
        if http_client is None:
            http_client = httpx.Client(timeout=30.0)
        provider = SerperProvider(key, client=http_client)
    results: list[DifficultyResult] = []
    try:
        for keyword in words:
            payload = None if refresh else store.get_serp(keyword, day)
            cached = payload is not None
            spent = 0
            error = ""
            if payload is None:
                if not key or provider is None:
                    error = "SERPER_API_KEY is not set"
                elif not credits.can_spend(per):
                    error = f"Serper credit cap reached ({credits.spent} of {credits.cap})"
                else:
                    try:
                        payload = provider.raw_search(keyword, 10)
                    except Exception as exc:  # noqa: BLE001 - the score stays blank rather than a guessed SERP
                        payload = None
                        error = scrub(f"Serper request failed: {type(exc).__name__}", [key])
                    else:
                        charged = payload.get("credits")
                        spent = int(charged) if isinstance(charged, (int, float)) and not isinstance(charged, bool) else per
                        credits.add_search(spent)
                        store.put_serp(keyword, day, payload, now_iso())
                        cached = False
            organic = _organic(payload) if payload is not None else None
            volume = store.get_volume(keyword)
            result = score_difficulty(
                keyword,
                organic=organic,
                payload=payload,
                volume=volume,
                tranco=tranco,
                majestic=majestic,
                composition=composition,
                serp_day=day if payload is not None else "",
                serp_cached=cached,
                credits=spent,
            )
            if error and payload is None:
                if result.score is None:
                    result.reason = error
                else:
                    result.reason = f"{error}. Scored with the factors that do not need a SERP."
            store.put_difficulty(keyword, day, result.to_dict())
            results.append(result)
        say(f"Serper credits spent this run: {credits.spent}. Cap {credits.cap}.")
        return results
    finally:
        store.close()
        if owns and http_client is not None:
            http_client.close()


def keyword_report(
    keywords: list[str],
    cache: str | Path,
    *,
    refresh: bool = False,
    fetch_serp: bool = False,
    client: httpx.Client | None = None,
    delay: float = 1.0,
    paid: PaidConfig | None = None,
    semrush_domains: list[str] | None = None,
    api_key: str | None = None,
    credit_cap: int = 50,
    tranco_path: str | None = None,
    majestic_path: str | None = None,
    today: str | None = None,
    log: Log | None = None,
) -> list[dict]:
    volumes = keyword_volume(
        keywords,
        cache,
        refresh=refresh,
        client=client,
        delay=delay,
        paid=paid,
        semrush_domains=semrush_domains,
        log=log,
    )
    difficulty_by_keyword: dict[str, dict] = {}
    if fetch_serp:
        for row in keyword_difficulty(
            keywords,
            cache,
            refresh=refresh,
            api_key=api_key,
            credit_cap=credit_cap,
            client=client,
            tranco_path=tranco_path,
            majestic_path=majestic_path,
            today=today,
            log=log,
        ):
            difficulty_by_keyword[norm_keyword(row.keyword)] = row.to_dict()
    else:
        store = KeywordStore(cache)
        try:
            for keyword in _unique(keywords):
                saved = store.latest_difficulty(keyword)
                if saved:
                    difficulty_by_keyword[norm_keyword(keyword)] = saved
        finally:
            store.close()
    rows = []
    for volume in volumes:
        difficulty = difficulty_by_keyword.get(norm_keyword(volume.keyword))
        rows.append(_report_row(volume, difficulty))
    return rows


def format_volume_text(rows: list[VolumeRecord]) -> str:
    header = ("keyword", "us monthly volume", "provider", "data date", "cpc", "competition", "blank reason")
    body = []
    for row in rows:
        body.append((
            row.keyword,
            row.volume_display or "",
            row.provider,
            row.data_date,
            "" if row.cpc_usd is None else f"{row.cpc_usd:.2f}",
            row.competition,
            row.blank_reason,
        ))
    return _text_table(header, body)


def format_difficulty_text(rows: list[DifficultyResult]) -> str:
    lines = ["Heuristic score, not a Google prediction."]
    for row in rows:
        score = "" if row.score is None else str(row.score)
        lines.append(f"{row.keyword}\t{score}\tcoverage {row.coverage:.2f}")
        for factor in row.factors:
            used = f"{factor.weight_used:.2f}" if factor.status == "present" else "0"
            sub = "" if factor.subscore is None else f"{factor.subscore:.1f}"
            lines.append(f"  {factor.name}\t{factor.status}\traw {factor.raw}\tsub {sub}\tweight {factor.weight:.2f}\tused {used}")
        if row.reason:
            lines.append(f"  note\t{row.reason}")
    return "\n".join(lines)


def format_report_text(rows: list[dict]) -> str:
    header = ("keyword", "us monthly volume", "provider", "data date", "difficulty", "coverage", "blank reason")
    body = []
    for row in rows:
        volume = row.get("volume_display") or ""
        difficulty = "" if row.get("difficulty") is None else str(row.get("difficulty"))
        coverage = "" if row.get("coverage") is None else f"{row['coverage']:.2f}"
        reason = row.get("blank_reason") or row.get("difficulty_reason") or ""
        body.append((row.get("keyword") or "", volume, row.get("provider") or "", row.get("data_date") or "", difficulty, coverage, reason))
    text = _text_table(header, body)
    return "Difficulty, when shown, is a heuristic score, not a Google prediction.\n" + text


def write_csv(rows: list[dict], path: str | Path, fields: list[str]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in fields})


VOLUME_FIELDS = [
    "keyword", "us_monthly_volume", "volume_display", "provider", "volume_source", "source_url",
    "data_date", "fetched_at", "cpc_usd", "competition", "competition_index", "semrush_us_volume",
    "semrush_url", "semrush_data_date", "notes", "blank_reason", "list_sources",
]
REPORT_FIELDS = VOLUME_FIELDS + ["difficulty", "coverage", "heuristic", "difficulty_reason"]


def _report_row(volume: VolumeRecord, difficulty: dict | None) -> dict:
    row = volume.to_dict()
    row.pop("attempts", None)
    row.pop("sticky", None)
    if difficulty is None:
        row["difficulty"] = None
        row["coverage"] = None
        row["heuristic"] = ""
        row["difficulty_reason"] = "difficulty not run"
        return row
    row["difficulty"] = difficulty.get("score")
    row["coverage"] = difficulty.get("coverage")
    row["heuristic"] = difficulty.get("label") or "Heuristic score, not a Google prediction."
    row["difficulty_reason"] = difficulty.get("reason") or ""
    row["factors"] = difficulty.get("factors") or []
    return row


def _apply_semrush(pending, results, store, http, extra_domains, *, refresh: bool, log: Log | None) -> list[str]:
    if not pending:
        return []
    domains = []
    for domain in list(extra_domains) + load_domain_file(str(DOMAIN_FILE)):
        cleaned = domain.strip().lower()
        if cleaned and cleaned not in domains:
            domains.append(cleaned)
    tables: dict[str, SemrushTable] = {}
    fetches = 0
    blocked = False

    def load_domain(domain: str, allow_fetch: bool) -> SemrushTable | None:
        nonlocal fetches, blocked
        if domain in tables:
            return tables[domain]
        cached = store.get_semrush_page(domain)
        if cached and not refresh and store.semrush_page_fresh(cached):
            table = SemrushTable.from_dict(cached)
            tables[domain] = table
            return table
        if not allow_fetch or blocked:
            return None
        if fetches >= SEMRUSH_FETCH_CAP:
            return None
        fetches += 1
        url = overview_url(domain)
        try:
            response = http.request("GET", url)
            table = parse_semrush_overview(response.text, domain=domain, url=str(response.url), status_code=response.status_code)
        except httpx.HTTPError:
            table = SemrushTable(domain, url, "error", "", "", now_iso(), {}, "request failed")
        store.put_semrush_page(domain, table.to_dict())
        tables[domain] = table
        if table.status == "captcha":
            blocked = True
            if log:
                log("Semrush returned a captcha or login wall. Further Semrush fetches are skipped.")
        return table

    for domain in domains:
        load_domain(domain, True)
    still = []
    for keyword in pending:
        record = None
        saw_captcha = blocked
        for domain in guess_semrush_domains(keyword):
            table = load_domain(domain, True)
            if table is None:
                continue
            if table.status == "captcha":
                saw_captcha = True
                break
            record = semrush_record(keyword, table)
            if record is not None:
                break
        if record is None and not saw_captcha:
            for table in tables.values():
                record = semrush_record(keyword, table)
                if record is not None:
                    break
        if record is not None and record.usable():
            record.attempts = list(results[keyword].attempts) + [Attempt("semrush", "hit", record.source_url)]
            store.put_volume(record)
            results[keyword] = record
            continue
        if saw_captcha:
            detail = "captcha or login wall, not bypassed"
        elif fetches >= SEMRUSH_FETCH_CAP and not any(semrush_record(keyword, table) for table in tables.values()):
            detail = "fetch cap reached before a public US table listed this keyword"
        elif not domains and not guess_semrush_domains(keyword) and not tables:
            detail = "no competitor overview to check for this keyword"
        else:
            detail = "keyword not in a fetched public US organic keyword table"
        _add_attempt(results[keyword], Attempt("semrush", "miss", detail))
        still.append(keyword)
    return still


def _take(pending, results, store, filled) -> list[str]:
    still = []
    for keyword in pending:
        record, attempt = filled.get(keyword, (None, Attempt("provider", "miss", "no result")))
        if record is not None and record.usable():
            record.attempts = list(results[keyword].attempts) + [attempt]
            store.put_volume(record)
            results[keyword] = record
        else:
            _add_attempt(results[keyword], attempt)
            still.append(keyword)
    return still


def _add_attempt(record: VolumeRecord, attempt: Attempt) -> None:
    record.attempts.append(attempt)
    record.blank_reason = "; ".join(f"{item.provider}: {item.detail}" for item in record.attempts if item.outcome != "hit")


def _keep_semrush_columns(record: VolumeRecord, previous: VolumeRecord | None) -> None:
    if previous is None or record.provider == "semrush":
        return
    if previous.semrush_us_volume is not None and record.semrush_us_volume is None:
        record.semrush_us_volume = previous.semrush_us_volume
        record.semrush_url = previous.semrush_url
        record.semrush_data_date = previous.semrush_data_date
    if previous.list_sources and not record.list_sources:
        record.list_sources = previous.list_sources


def _record_from_csv(row: dict) -> VolumeRecord | None:
    fields = {str(key).strip().lower(): value for key, value in row.items() if key}
    keyword = " ".join(str(fields.get("keyword") or "").split())
    if not keyword:
        return None
    volume = parse_int(fields.get("us_monthly_volume"))
    source = str(fields.get("volume_source") or "").strip()
    source_url = str(fields.get("source_url") or "").strip()
    data_date = str(fields.get("data_date") or "").strip()
    display = str(fields.get("volume_display") or "").strip()
    if volume is not None and (not display or parse_int(display) == volume):
        display = f"{volume:,}"
    notes = str(fields.get("notes") or "").strip()
    semrush_volume = parse_int(fields.get("semrush_us_volume"))
    record = VolumeRecord(
        keyword=keyword,
        us_monthly_volume=volume,
        volume_display=display or ("" if volume is None else f"{volume:,}"),
        provider=provider_id(source) if volume is not None else "",
        volume_source=source,
        source_url=source_url,
        data_date=data_date,
        fetched_at=now_iso(),
        cpc_usd=parse_float(fields.get("cpc_usd")),
        competition=str(fields.get("competition") or "").strip(),
        semrush_us_volume=semrush_volume,
        semrush_url=str(fields.get("semrush_url") or "").strip(),
        semrush_data_date=data_date if semrush_volume is not None and "semrush" in source.lower() else "",
        notes=notes,
        list_sources=str(fields.get("list_sources") or "").strip(),
        sticky=True,
        attempts=[Attempt("import", "hit" if volume is not None else "miss", "imported CSV")],
    )
    if volume is not None and not record.usable():
        record.us_monthly_volume = None
        record.volume_display = ""
        record.provider = ""
        record.blank_reason = "imported row has a volume but is missing source, source URL, or data date, so the number is not shown"
    elif volume is None:
        record.blank_reason = notes or "imported without a volume"
    return record


def _organic(payload: dict) -> list[dict]:
    hits = parse_serper(payload, 10)
    return [{"position": hit.position, "url": hit.url, "title": hit.title, "snippet": hit.snippet} for hit in hits]


def _maybe_ranks(loader, path: str | None):
    text = (path or "").strip()
    if not text:
        return None
    file_path = Path(text)
    if not file_path.is_file():
        return None
    return loader(file_path)


def _unique(keywords: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for keyword in keywords:
        text = " ".join(str(keyword or "").split())
        key = norm_keyword(text)
        if key and key not in seen:
            seen[key] = text
    return list(seen.values())


def _text_table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    widths = [len(item) for item in header]
    rendered = [header, *rows]
    for row in rendered:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], min(len(cell), 80))
    lines = []
    for row in rendered:
        parts = []
        for index, cell in enumerate(row):
            shown = cell if len(cell) <= 80 else cell[:77] + "..."
            parts.append(shown.ljust(widths[index]))
        lines.append("  ".join(parts).rstrip())
    return "\n".join(lines)


def dumps(rows: list) -> str:
    payload = [row.to_dict() if hasattr(row, "to_dict") else row for row in rows]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
