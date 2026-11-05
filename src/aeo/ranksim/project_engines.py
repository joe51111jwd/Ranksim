"""AI answer engines inside a project: REAL citations per pull, PREDICTED simulation per test.

A pull asks every key search of the project to each AI engine that has a key
(aeo.ranksim.engines.ask_engines) and saves the REAL answers in the pull folder
as engines.json. An engine without its key is recorded as 'not run: needs
<ENV VAR>' and nothing else; no answer, citation or share is ever made up.

Testing a page attaches, per search, the newest REAL answers of each engine
(from whichever pull last got them, with the time they were pulled), and, when
an LLM key is set, the PREDICTED simulation: our own LLM answering the search
from the stored Google top 10 plus your page. The simulation is labeled as our
model, never as the real engine.

Page text, crawled text and engine answers are data. They are fenced and
escaped before any LLM sees them (see engines.build_answer_prompt) and never
followed as instructions.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aeo.config import Settings
from aeo.ranksim.engines import (
    ENGINE_KEYS,
    ENGINE_SPECS,
    MAX_RUNS,
    NO_LLM_STATUS,
    REAL_LABEL,
    REAL_NOTE,
    SIM_LABEL,
    CitationSimulation,
    EngineSummary,
    RealCitationReport,
    _llm_ready,
    ask_engines,
    engine_api_key,
    engine_availability,
    not_run_status,
    real_report_from_dict,
    simulate_citations,
    source_page_from_doc,
    to_dict,
)
from aeo.ranksim.project import (
    NOT_RUN_GOOGLE,
    EngineRow,
    Project,
    ProjectContext,
    SearchBoard,
    SimSummary,
    _lookup,
    _read_json,
    _round,
    _stamp,
    _write_json,
    clean,
    list_pulls,
)
from aeo.ranksim.tester import plain_dashes

ENGINES_FILE = "engines.json"
BING_NOTE = "Bing Copilot: not covered. No public API returns its answer citations, so nothing is shown for it."
NO_STORED_STATUS = "not run: none of the REAL Google results for this search are stored"
NOT_PULLED_STATUS = "not pulled yet: the key is set now, press Pull now"
SIM_OFF_STATUS = "not run: switched off for this test"
SIM_WORKERS = 4
ENGINE_SEARCH_WORKERS = 4

EmitFn = Callable[[str, str, int, int], None]


@dataclass
class EnginesSnapshot:
    """The REAL AI engine answers of one pull, per search."""

    pull_id: str
    written_at: str
    our_domain: str
    brand: str
    n_runs: int
    engines: list[dict] = field(default_factory=list)  # availability rows
    status: dict[str, str] = field(default_factory=dict)  # engine -> plain status
    reports: dict[str, dict] = field(default_factory=dict)  # search -> RealCitationReport as dict
    fresh_calls: int = 0
    spent_usd: float | None = None
    unpriced_calls: int = 0
    seconds: float = 0.0
    note: str = ""
    label: str = REAL_LABEL
    real_note: str = REAL_NOTE

    def to_dict(self) -> dict:
        return clean(asdict(self))

    @classmethod
    def from_dict(cls, data: dict) -> "EnginesSnapshot":
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})

    def report(self, search: str) -> RealCitationReport | None:
        data = self.reports.get(search)
        return real_report_from_dict(data) if data else None


def run_engines_step(
    project: Project,
    settings: Settings,
    *,
    pull_id: str,
    engines: Sequence[str] | None = None,
    n_runs: int = 1,
    brand: str | None = None,
    cache_dir: str | Path | None = None,
    client=None,
    use_cache: bool = True,
    models: dict[str, str] | None = None,
    emit: EmitFn | None = None,
    now: datetime | None = None,
) -> EnginesSnapshot:
    """Ask every key search to every chosen engine. REAL answers, or 'not run: needs <ENV VAR>'."""
    if n_runs < 1 or n_runs > MAX_RUNS:
        raise ValueError(f"AI engine runs must be between 1 and {MAX_RUNS}")
    began = time.perf_counter()
    chosen = list(engines or ENGINE_KEYS)
    say = emit or (lambda *_args: None)
    searches = list(project.searches)
    availability = [row for row in engine_availability(settings) if row.engine in chosen]
    configured = [row for row in availability if row.configured]
    brand = brand or project.name
    cache = Path(cache_dir) if cache_dir else project.dir / "engines"
    if configured:
        names = ", ".join(row.name for row in configured)
        say("engines", f"Asking {names} each of {len(searches)} searches, {n_runs} run(s) each.", 0, len(searches))
    else:
        say("engines", "No AI engine key is set: " + "; ".join(f"{row.name} {row.status}" for row in availability) + ".", 0, 0)

    def one(search: str) -> tuple[str, RealCitationReport]:
        return search, ask_engines(
            search,
            settings=settings,
            our_domain=project.domain,
            brand=brand,
            engines=chosen,
            n_runs=n_runs,
            models=models,
            cache_dir=cache,
            client=client,
            now=now,
            use_cache=use_cache,
            max_workers=4,
        )

    reports: dict[str, RealCitationReport] = {}
    if configured and searches:
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, min(ENGINE_SEARCH_WORKERS, len(searches)))) as pool:
            futures = [pool.submit(one, search) for search in searches]
            for future in as_completed(futures):
                search, report = future.result()
                reports[search] = report
                done += 1
                say("engines", f"{search}: {_report_line(report)}", done, len(searches))
    else:
        # No key: ask_engines makes no network call and records 'not run' rows only.
        for search in searches:
            reports[search] = one(search)[1]
        say("engines", f"AI engines not run: no key set. {len(searches)} searches recorded as not run.", 0, 0)
    status = {row.engine: _engine_status(row.engine, row.status, [reports[s] for s in searches if s in reports]) for row in availability}
    fresh = sum(report.fresh_calls for report in reports.values())
    priced = [report.spent_usd for report in reports.values() if report.spent_usd is not None and report.fresh_calls]
    snapshot = EnginesSnapshot(
        pull_id=pull_id,
        written_at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        our_domain=project.domain,
        brand=brand,
        n_runs=n_runs,
        engines=[asdict(row) for row in availability],
        status=status,
        reports={search: to_dict(reports[search]) for search in searches if search in reports},
        fresh_calls=fresh,
        spent_usd=0.0 if not fresh else (sum(priced) if priced else None),
        unpriced_calls=sum(report.unpriced_calls for report in reports.values()),
        seconds=_round(time.perf_counter() - began) or 0.0,
    )
    snapshot.note = plain_dashes(
        "; ".join(f"{ENGINE_SPECS[key].name}: {value}" for key, value in status.items())
        + (f". Spent about {_usd(snapshot.spent_usd)} on {fresh} fresh calls." if fresh else ".")
    )
    return snapshot


def _engine_status(engine: str, availability_status: str, reports: Iterable[RealCitationReport]) -> str:
    if availability_status != "ready":
        return availability_status
    summaries = [summary for report in reports for summary in report.engines if summary.engine == engine]
    answered = [summary for summary in summaries if summary.ok_runs]
    if not answered:
        errors = [summary.status for summary in summaries if summary.status and summary.status != "ok"]
        return errors[0] if errors else "error: no answers"
    cited = sum(1 for summary in answered if summary.our_cited_runs)
    line = f"ok, answered {len(answered)} of {len(summaries)} searches, cited your site on {cited}"
    failed = len(summaries) - len(answered)
    if failed:
        line += f", {failed} failed"
    return line


def _report_line(report: RealCitationReport) -> str:
    parts = []
    for summary in report.engines:
        if summary.ok_runs:
            parts.append(f"{summary.name} cited you in {summary.our_cited_runs} of {summary.ok_runs}")
        else:
            parts.append(f"{summary.name} {summary.status}")
    return "; ".join(parts)


def save_engines(folder: Path, snapshot: EnginesSnapshot) -> None:
    _write_json(Path(folder) / ENGINES_FILE, snapshot.to_dict())


def load_engines(project: Project, pull_id: str) -> EnginesSnapshot | None:
    data = _read_json(project.dir / "pulls" / pull_id / ENGINES_FILE)
    return EnginesSnapshot.from_dict(data) if data else None


@dataclass
class LatestEngines:
    """Per search, the newest REAL answers of each engine across all pulls."""

    rows: dict[str, list[EngineRow]]
    reports: dict[str, RealCitationReport]  # newest report per search with at least one answer
    pulls: list[str]  # pulls the rows came from, newest first


def latest_engines(project: Project, *, settings: Settings | None = None, searches: Sequence[str] | None = None) -> LatestEngines:
    """Walk pulls newest first. An engine's newest answered pull wins; older answers keep their own pull time."""
    wanted = list(searches if searches is not None else project.searches)
    chosen: dict[tuple[str, str], tuple[EngineSummary, str]] = {}
    newer: dict[tuple[str, str], str] = {}
    reports: dict[str, RealCitationReport] = {}
    used: list[str] = []
    for info in reversed(list_pulls(project)):
        snapshot = load_engines(project, info.pull_id)
        if snapshot is None:
            continue
        for search in wanted:
            data = snapshot.reports.get(search)
            if not data:
                continue
            report = real_report_from_dict(data)
            if search not in reports and report.overall_ok_runs:
                reports[search] = report
            for summary in report.engines:
                key = (search, summary.engine)
                held = chosen.get(key)
                if held is not None and held[0].ok_runs:
                    continue
                if summary.ok_runs:
                    if held is not None:
                        newer[key] = held[0].status
                    chosen[key] = (summary, info.pull_id)
                    if info.pull_id not in used:
                        used.append(info.pull_id)
                elif held is None:
                    chosen[key] = (summary, info.pull_id)
    rows: dict[str, list[EngineRow]] = {}
    for search in wanted:
        found: list[EngineRow] = []
        for engine in ENGINE_KEYS:
            held = chosen.get((search, engine))
            if held is None:
                found.append(_missing_row(engine, settings))
                continue
            row = engine_row(*held)
            if row.status.startswith("not run") and settings is not None and engine_api_key(settings, engine) is not None:
                row.status = NOT_PULLED_STATUS
            if (search, engine) in newer:
                row.note = f"The newest pull got no answer ({newer[(search, engine)]}). These are the newest REAL answers."
            found.append(row)
        rows[search] = found
    return LatestEngines(rows=rows, reports=reports, pulls=used)


def _missing_row(engine: str, settings: Settings | None) -> EngineRow:
    spec = ENGINE_SPECS[engine]
    if settings is not None and engine_api_key(settings, engine) is not None:
        status = NOT_PULLED_STATUS
    else:
        status = not_run_status(engine)
    return EngineRow(engine=engine, name=spec.name, status=status)


def engine_row(summary: EngineSummary, pull_id: str) -> EngineRow:
    spec = ENGINE_SPECS[summary.engine]
    row = EngineRow(engine=summary.engine, name=spec.name, status=summary.status or "error: no runs", pull_id=pull_id)
    if not summary.ok_runs:
        return row
    row.label = REAL_LABEL
    row.pulled_at = summary.last_pulled_at or summary.first_pulled_at
    row.model = summary.model
    row.runs = summary.n_runs
    row.ok_runs = summary.ok_runs
    row.our_cited_runs = summary.our_cited_runs
    row.our_share = summary.our_citation_share
    row.our_mean_position = summary.our_mean_position
    row.brand_mentioned_runs = summary.brand_mentioned_runs
    row.top_domains = [item.domain for item in summary.domains[:6]]
    ours: list[str] = []
    for run in summary.runs:
        for url in run.our_urls:
            if url not in ours:
                ours.append(url)
    row.our_urls = ours
    answered = [run for run in summary.runs if run.ok]
    if answered:
        latest = max(answered, key=lambda run: (run.pulled_at, run.run_index))
        row.cited = [
            {"position": item.position, "url": item.url, "domain": item.domain, "title": plain_dashes(item.title or "")}
            for item in latest.citations[:10]
        ]
    return row


def sim_summary(sim: CitationSimulation) -> SimSummary:
    summary = SimSummary(status=sim.status, label=SIM_LABEL, provider=sim.provider, model=sim.model, runs=sim.n_runs)
    if sim.status != "ok":
        return summary
    summary.ok_runs = sim.ok_runs
    summary.your_cited_runs = sim.your_cited_runs
    summary.your_share = sim.your_share
    summary.your_mean_position = sim.your_mean_position
    summary.domain_share_without = sim.your_domain_share_without
    summary.domain_share_with = sim.your_domain_share_with
    summary.domain_delta = sim.your_domain_delta
    summary.sources = [
        {
            "url": row.url,
            "domain": row.domain,
            "title": plain_dashes(row.title or ""),
            "google_position": row.google_position,
            "is_yours": row.is_yours,
            "share_with": row.share_with,
            "share_without": row.share_without,
        }
        for row in sim.sources[:8]
    ]
    summary.agreement = [asdict(item) for item in sim.agreement]
    summary.flags = list(sim.flags)
    summary.notes = list(sim.notes)
    summary.cost_usd = sim.llm_cost_usd
    return summary


def attach_ai(
    ctx: ProjectContext,
    rows: list[SearchBoard],
    html: str,
    url: str,
    *,
    llm=None,
    sim_runs: int = 3,
    settings: Settings | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    sim_off: str = "",
) -> tuple[str, str, list[str]]:
    """Fill row.engines (REAL, newest per engine) and row.simulation (PREDICTED) for every search.

    With no usable LLM the simulation reads NO_LLM_STATUS, or sim_off when the
    caller switched it off on purpose.
    """
    latest = latest_engines(ctx.project, settings=settings, searches=[row.search for row in rows])
    for row in rows:
        row.engines = latest.rows.get(row.search, [])
    if not _llm_ready(llm):
        status = sim_off if (sim_off and llm is None) else NO_LLM_STATUS
        for row in rows:
            row.simulation = SimSummary(status=status, label=SIM_LABEL)
        return REAL_NOTE, SIM_LABEL, latest.pulls
    if sim_runs < 1 or sim_runs > MAX_RUNS:
        raise ValueError(f"simulation runs must be between 1 and {MAX_RUNS}")
    by_search = ctx.google.by_search()
    jobs: list[tuple[SearchBoard, list]] = []
    for row in rows:
        entry = by_search.get(row.search)
        if entry is None or not entry.results:
            row.simulation = SimSummary(status=row.error or NOT_RUN_GOOGLE, label=SIM_LABEL)
            continue
        pages = []
        for item in entry.results:
            doc = _lookup(ctx.pages, item.url)
            if doc is not None and (doc.title or doc.body or "").strip():
                pages.append(source_page_from_doc(doc, google_position=float(item.position)))
        if not pages:
            row.simulation = SimSummary(status=NO_STORED_STATUS, label=SIM_LABEL)
            continue
        jobs.append((row, pages))

    def one(job: tuple[SearchBoard, list]) -> tuple[SearchBoard, SimSummary]:
        row, pages = job
        try:
            sim = simulate_citations(
                row.search,
                pages,
                (url, html),
                llm,
                n_runs=sim_runs,
                our_domain=ctx.project.domain,
                real=latest.reports.get(row.search),
            )
            return row, sim_summary(sim)
        except Exception as exc:  # noqa: BLE001 - one search failing must not sink the board
            return row, SimSummary(status=f"error: {type(exc).__name__}", label=SIM_LABEL)

    done = 0
    if progress is not None:
        progress(f"AI answer simulation: {len(jobs)} searches, {sim_runs} runs each.", 0, len(jobs))
    with ThreadPoolExecutor(max_workers=max(1, min(SIM_WORKERS, len(jobs) or 1))) as pool:
        for future in as_completed([pool.submit(one, job) for job in jobs]):
            row, summary = future.result()
            row.simulation = summary
            done += 1
            if progress is not None:
                progress(f"{row.search}: {summary.status}", done, len(jobs))
    return REAL_NOTE, SIM_LABEL, latest.pulls


def ai_summary_text(rows: Sequence[SearchBoard]) -> str:
    """One plain line for the board: REAL engine citations across searches, then the PREDICTED simulation."""
    total = len(rows)
    parts: list[str] = []
    for engine in ENGINE_KEYS:
        cells = [item for row in rows for item in row.engines if item.engine == engine]
        if not cells:
            continue
        name = ENGINE_SPECS[engine].name
        answered = [item for item in cells if item.status == "ok"]
        if answered:
            cited = sum(1 for item in answered if item.our_cited_runs)
            newest = max(item.pulled_at for item in answered)
            parts.append(f"{name} REAL: cites your site on {cited} of {len(answered)} searches answered (newest {_stamp(newest)})")
        else:
            statuses = sorted({item.status for item in cells})
            parts.append(f"{name}: {statuses[0]}")
    sims = [row.simulation for row in rows if row.simulation is not None]
    ok = [sim for sim in sims if sim.status == "ok"]
    if ok:
        cited = sum(sim.your_cited_runs for sim in ok)
        runs = sum(sim.ok_runs for sim in ok)
        parts.append(f"AI answer simulation PREDICTED: your page cited in {cited} of {runs} runs over {len(ok)} of {total} searches")
    elif sims:
        parts.append(f"AI answer simulation: {sorted({sim.status for sim in sims})[0]}")
    if not parts:
        return ""
    return plain_dashes("; ".join(parts) + ". " + BING_NOTE)


def render_engines_text(project: Project, latest: LatestEngines) -> str:
    """`project engines NAME`: the newest REAL AI engine citations per search."""
    lines = [
        f"{project.name} ({project.site}): REAL AI engine citations for {len(project.searches)} key searches.",
        REAL_NOTE,
        BING_NOTE,
    ]
    if latest.pulls:
        lines.append("From pulls: " + ", ".join(latest.pulls) + ".")
    else:
        lines.append("No pull has REAL AI engine answers yet.")
    # An engine that has no key and no earlier answers says so once, not once per search.
    never: dict[str, str] = {}
    for engine_rows in zip(*[latest.rows.get(search, []) for search in project.searches]) if project.searches else []:
        statuses = {row.status for row in engine_rows}
        if len(statuses) == 1 and next(iter(statuses)).startswith("not run"):
            never[engine_rows[0].engine] = f"{engine_rows[0].name}: {next(iter(statuses))}"
    if never:
        lines.append("Not run on any search: " + "; ".join(never.values()) + ".")
    for index, search in enumerate(project.searches, start=1):
        lines.append("")
        lines.append(f'{index}/{len(project.searches)} "{search}"')
        shown = 0
        for row in latest.rows.get(search, []):
            if row.engine in never:
                continue
            shown += 1
            if row.status != "ok":
                lines.append(f"   {row.name}: {row.status}")
                continue
            ours = (
                f"cites your site in {row.our_cited_runs} of {row.ok_runs} answers"
                + (f" (first at citation #{row.our_mean_position:.0f} on average)" if row.our_mean_position is not None else "")
                if row.our_cited_runs
                else f"does not cite your site ({row.ok_runs} answers)"
            )
            lines.append(f"   {row.name} REAL, pulled {_stamp(row.pulled_at)}, model {row.model}, pull {row.pull_id}: {ours}.")
            for item in row.cited[:5]:
                lines.append(f"      #{item['position']} {item['domain']}  {item['url']}")
            if row.note:
                lines.append(f"      {row.note}")
        if not shown:
            lines.append("   No AI engine has a key, so nothing was asked.")
    return plain_dashes("\n".join(lines) + "\n")


def latest_engines_dict(latest: LatestEngines) -> dict:
    return clean({"label": REAL_LABEL, "note": REAL_NOTE, "pulls": latest.pulls, "searches": {key: [asdict(row) for row in rows] for key, rows in latest.rows.items()}})


def _usd(value: float | None) -> str:
    return "an unknown amount (no published rate)" if value is None else f"${value:.2f}"
