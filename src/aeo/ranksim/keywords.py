"""Target queries for SmartCrew, mapped to the sitemap snapshot.

A query is a page gap when the suggested URL is not in the snapshot.
The snapshot is a fetch of the live sitemap, not a guessed site map.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path

from aeo.crawl.sitemap import parse_sitemap
from aeo.crawl.urls import normalize_url

def repo_root() -> Path:
    override = os.environ.get("RANKSIM_ROOT")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "smartcrew" / "sitemap_urls.txt").exists():
            return parent
    return Path.cwd()


def sitemap_path() -> Path:
    return repo_root() / "smartcrew" / "sitemap_urls.txt"


def keywords_path() -> Path:
    return repo_root() / "smartcrew" / "keywords.csv"

SITEMAP_NOTE = "No matching URL in the 2026-10-29 sitemap."
STANCE_NOTE = (
    "No sitemap page on 2026-10-29. SmartCrew's current pages say they do not "
    "replace takeoff, estimating, machine control, telematics, or ELD."
)

COMPETITORS: list[tuple[str, str]] = [
    ("busybusy", "https://www.smartcrewhq.com/compare/busybusy-alternative/"),
    ("clockshark", "https://www.smartcrewhq.com/compare/clockshark-alternative/"),
    ("contractor foreman", "https://www.smartcrewhq.com/compare/contractor-foreman-alternative/"),
    ("contractor plus", "https://www.smartcrewhq.com/compare/contractor-plus-alternative/"),
    ("exaktime", "https://www.smartcrewhq.com/compare/exaktime-alternative/"),
    ("foreman", "https://www.smartcrewhq.com/compare/foreman-alternative/"),
    ("hcss heavyjob", "https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative/"),
    ("jobber", "https://www.smartcrewhq.com/compare/jobber-alternative/"),
    ("jobtread", "https://www.smartcrewhq.com/compare/jobtread-alternative/"),
    ("knowify", "https://www.smartcrewhq.com/compare/knowify-alternative/"),
    ("procore", "https://www.smartcrewhq.com/compare/procore-alternative/"),
    ("projul", "https://www.smartcrewhq.com/compare/projul-alternative/"),
    ("quickbooks time", "https://www.smartcrewhq.com/compare/quickbooks-time-alternative/"),
    ("quoteiq", "https://www.smartcrewhq.com/compare/quoteiq-alternative/"),
    ("raken", "https://www.smartcrewhq.com/compare/raken-alternative/"),
    ("workmax", "https://www.smartcrewhq.com/compare/workmax-alternative/"),
]


@dataclass(frozen=True)
class Keyword:
    query: str
    intent: str
    cluster: str
    suggested_target_page: str
    page_exists: bool
    notes: str

    def as_row(self) -> dict[str, str]:
        return {
            "query": self.query,
            "intent": self.intent,
            "cluster": self.cluster,
            "suggested_target_page": self.suggested_target_page,
            "page_exists": "yes" if self.page_exists else "no",
            "notes": self.notes,
        }


def load_sitemap_urls(path: Path | None = None) -> set[str]:
    text = (path or sitemap_path()).read_text()
    urls: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.add(normalize_url(line))
    return urls


def refresh_sitemap(text: str, path: Path | None = None) -> set[str]:
    kind, locs = parse_sitemap(text)
    if kind != "urlset":
        raise ValueError("Expected a sitemap urlset")
    urls = [normalize_url(url) for url in locs]
    target = path or sitemap_path()
    target.write_text("\n".join(urls) + "\n")
    return set(urls)


def _pages() -> dict[str, str]:
    base = "https://www.smartcrewhq.com"
    return {
        "dirtwork": f"{base}/dirtwork-software/",
        "excavation": f"{base}/industries/excavation/",
        "heavy_civil": f"{base}/industries/heavy-civil/",
        "grading": f"{base}/industries/grading/",
        "sitework": f"{base}/industries/sitework/",
        "paving": f"{base}/industries/paving/",
        "road": f"{base}/industries/road-construction/",
        "land": f"{base}/industries/land-clearing/",
        "septic": f"{base}/industries/septic/",
        "demo": f"{base}/industries/demolition/",
        "hdd": f"{base}/industries/directional-drilling/",
        "utils": f"{base}/industries/underground-utilities/",
        "blog_best": f"{base}/blog/best-excavation-dirtwork-software-2026/",
        "blog_20": f"{base}/blog/best-excavation-software-20-50-employees/",
        "blog_50": f"{base}/blog/best-excavation-software-50-100-employees/",
        "blog_small": f"{base}/blog/best-software-small-dirt-work-contractors/",
        "blog_job": f"{base}/blog/best-job-costing-software-sitework-contractors/",
        "blog_time": f"{base}/blog/best-time-tracking-apps-excavation-crews/",
        "blog_maint": f"{base}/blog/best-equipment-maintenance-software-small-contractors/",
        "blog_gc": f"{base}/blog/gc-software-vs-dirtwork-software/",
        "blog_qb": f"{base}/blog/excavation-software-quickbooks-online/",
        "blog_price": f"{base}/blog/construction-software-per-user-pricing/",
        "blog_haul": f"{base}/blog/track-dump-truck-loads-haul-tickets/",
        "blog_hours": f"{base}/blog/track-equipment-hours-by-job/",
        "time": f"{base}/features/construction-time-tracking/",
        "jobcost": f"{base}/features/construction-job-costing/",
        "daily": f"{base}/features/construction-daily-report-app/",
        "sched": f"{base}/features/crew-scheduling-software/",
        "haul": f"{base}/features/hauling-software/",
        "maint": f"{base}/features/heavy-equipment-maintenance-software/",
        "pretrip": f"{base}/features/pre-trip-inspection-app/",
        "pricing": f"{base}/pricing/faq/",
        "heavyjob": f"{base}/compare/hcss-heavyjob-alternative/",
    }


def build_keywords(sitemap_urls: set[str] | None = None) -> list[Keyword]:
    known = sitemap_urls if sitemap_urls is not None else load_sitemap_urls()
    pages = _pages()
    specs: list[tuple[str, str, str, str, str]] = []

    def add(query: str, intent: str, cluster: str, url: str, note: str = "") -> None:
        specs.append((query, intent, cluster, normalize_url(url), note))

    category = [
        ("dirtwork software", "dirtwork"),
        ("dirt work software", "dirtwork"),
        ("excavation software", "excavation"),
        ("excavation contractor software", "excavation"),
        ("heavy civil construction software", "heavy_civil"),
        ("heavy construction software", "heavy_civil"),
        ("grading contractor software", "grading"),
        ("sitework software", "sitework"),
        ("site work software", "sitework"),
        ("paving contractor software", "paving"),
        ("road construction software", "road"),
        ("land clearing software", "land"),
        ("septic contractor software", "septic"),
        ("demolition contractor software", "demo"),
        ("directional drilling software", "hdd"),
        ("underground utility contractor software", "utils"),
        ("utility contractor software", "utils"),
    ]
    for query, key in category:
        add(query, "commercial", "category", pages[key])

    best = [
        ("what is the best all-in-one dirtworks software", "dirtwork"),
        ("best all-in-one dirtworks software", "dirtwork"),
        ("best all-in-one heavy construction software", "heavy_civil"),
        ("best heavy civil construction software", "heavy_civil"),
        ("best dirtwork software", "dirtwork"),
        ("best excavation software", "blog_best"),
        ("best excavation software for small crews", "blog_small"),
        ("best excavation software for 20 to 50 employees", "blog_20"),
        ("best excavation software for 50 to 100 employees", "blog_50"),
        ("best software for small dirt work contractors", "blog_small"),
        ("best job costing software for sitework contractors", "blog_job"),
        ("best time tracking apps for excavation crews", "blog_time"),
        ("best equipment maintenance software for small contractors", "blog_maint"),
        ("best grading software", "grading"),
        ("best sitework contractor software", "sitework"),
        ("gc software vs dirtwork software", "blog_gc"),
    ]
    for query, key in best:
        intent = "comparison" if " vs " in query else "commercial"
        add(query, intent, "best", pages[key])

    for name, url in COMPETITORS:
        add(f"{name} alternative", "comparison", "alternative", url)
        add(f"{name} alternative for dirt work crews", "comparison", "alternative", url)
        add(f"smartcrew vs {name}", "comparison", "comparison", url)

    features = [
        ("construction time tracking app", "commercial", "time"),
        ("gps time clock for construction crews", "commercial", "time"),
        ("construction job costing software", "commercial", "jobcost"),
        ("job cost against the bid", "commercial", "jobcost"),
        ("construction daily report app", "commercial", "daily"),
        ("daily report software for excavation", "commercial", "daily"),
        ("crew scheduling software for contractors", "commercial", "sched"),
        ("haul ticket software", "commercial", "haul"),
        ("dump truck load tracking software", "commercial", "blog_haul"),
        ("track equipment hours by job", "informational", "blog_hours"),
        ("heavy equipment maintenance software", "commercial", "maint"),
        ("pre trip inspection app", "commercial", "pretrip"),
        ("dot inspection app for dump trucks", "commercial", "pretrip"),
        ("construction software per user pricing", "informational", "blog_price"),
        ("excavation software that works with quickbooks", "commercial", "blog_qb"),
        ("smartcrew pricing", "transactional", "pricing"),
        ("how much does smartcrew cost", "transactional", "pricing"),
        ("heavyjob alternative for small contractors", "comparison", "heavyjob"),
        ("hcss alternative", "comparison", "heavyjob"),
        ("hcss alternative for small crews", "comparison", "heavyjob"),
        ("best hcss alternative", "commercial", "heavyjob"),
        ("excavation software with haul tickets", "commercial", "haul"),
        ("all in one software for excavation contractors", "commercial", "dirtwork"),
        ("field software for heavy civil crews", "commercial", "heavy_civil"),
        ("software for 5 to 50 person dirt crews", "commercial", "blog_small"),
        ("equipment tracking software for excavation", "commercial", "maint"),
        ("construction daily logs for grading crews", "commercial", "daily"),
        ("time tracking for paving crews", "commercial", "time"),
        ("job costing for underground utility contractors", "commercial", "jobcost"),
        ("haul tickets for sitework", "commercial", "haul"),
        ("best haul ticket app for dump trucks", "commercial", "haul"),
        ("daily equipment inspection app", "commercial", "pretrip"),
    ]
    for query, intent, key in features:
        add(query, intent, "feature", pages[key])

    gaps = [
        ("hcss heavybid alternative", "comparison", "https://www.smartcrewhq.com/compare/hcss-heavybid-alternative/", SITEMAP_NOTE),
        ("heavybid alternative for contractors", "comparison", "https://www.smartcrewhq.com/compare/hcss-heavybid-alternative/", SITEMAP_NOTE),
        ("smartcrew vs hcss heavybid", "comparison", "https://www.smartcrewhq.com/compare/hcss-heavybid-alternative/", SITEMAP_NOTE),
        ("b2w alternative", "comparison", "https://www.smartcrewhq.com/compare/b2w-alternative/", SITEMAP_NOTE),
        ("b2w track alternative for heavy civil", "comparison", "https://www.smartcrewhq.com/compare/b2w-alternative/", SITEMAP_NOTE),
        ("foundation software alternative", "comparison", "https://www.smartcrewhq.com/compare/foundation-software-alternative/", SITEMAP_NOTE),
        ("viewpoint vista alternative for contractors", "comparison", "https://www.smartcrewhq.com/compare/viewpoint-alternative/", SITEMAP_NOTE),
        ("assignar alternative", "comparison", "https://www.smartcrewhq.com/compare/assignar-alternative/", SITEMAP_NOTE),
        ("rhumbix alternative", "comparison", "https://www.smartcrewhq.com/compare/rhumbix-alternative/", SITEMAP_NOTE),
        ("fieldwire alternative for civil contractors", "comparison", "https://www.smartcrewhq.com/compare/fieldwire-alternative/", SITEMAP_NOTE),
        ("trimble construction one alternative", "comparison", "https://www.smartcrewhq.com/compare/trimble-alternative/", STANCE_NOTE),
        ("agtek alternative for excavation", "comparison", "https://www.smartcrewhq.com/compare/agtek-alternative/", STANCE_NOTE),
        ("tenna alternative", "comparison", "https://www.smartcrewhq.com/compare/tenna-alternative/", SITEMAP_NOTE),
        ("certified payroll software for heavy civil", "commercial", "https://www.smartcrewhq.com/features/certified-payroll/", SITEMAP_NOTE),
        ("prevailing wage software for contractors", "commercial", "https://www.smartcrewhq.com/features/certified-payroll/", SITEMAP_NOTE),
        ("excavation estimating software", "commercial", "https://www.smartcrewhq.com/features/excavation-estimating/", STANCE_NOTE),
        ("dirtwork takeoff software", "commercial", "https://www.smartcrewhq.com/features/dirtwork-takeoff/", STANCE_NOTE),
        ("construction safety software for excavation crews", "commercial", "https://www.smartcrewhq.com/features/safety/", SITEMAP_NOTE),
        ("toolbox talk app for contractors", "commercial", "https://www.smartcrewhq.com/features/toolbox-talks/", SITEMAP_NOTE),
        ("equipment dispatch software for heavy civil", "commercial", "https://www.smartcrewhq.com/features/equipment-dispatch/", SITEMAP_NOTE),
        ("fleet telematics software for dump trucks", "commercial", "https://www.smartcrewhq.com/features/fleet-telematics/", STANCE_NOTE),
        ("eld for dump truck contractors", "commercial", "https://www.smartcrewhq.com/features/eld/", STANCE_NOTE),
        ("bid management software for sitework", "commercial", "https://www.smartcrewhq.com/features/bid-management/", STANCE_NOTE),
        ("change order software for excavation", "commercial", "https://www.smartcrewhq.com/features/change-orders/", SITEMAP_NOTE),
        ("smartcrew integrations", "commercial", "https://www.smartcrewhq.com/integrations/", SITEMAP_NOTE),
        ("smartcrew reviews", "commercial", "https://www.smartcrewhq.com/reviews/", SITEMAP_NOTE),
        ("smartcrew demo", "transactional", "https://www.smartcrewhq.com/demo/", SITEMAP_NOTE),
        ("hcss heavybid pricing", "transactional", "https://www.smartcrewhq.com/compare/hcss-heavybid-alternative/", SITEMAP_NOTE),
        ("best certified payroll software for civil contractors", "commercial", "https://www.smartcrewhq.com/features/certified-payroll/", SITEMAP_NOTE),
        ("best estimating software for excavation contractors", "commercial", "https://www.smartcrewhq.com/features/excavation-estimating/", STANCE_NOTE),
        ("smartcrew vs b2w", "comparison", "https://www.smartcrewhq.com/compare/b2w-alternative/", SITEMAP_NOTE),
        ("smartcrew vs foundation software", "comparison", "https://www.smartcrewhq.com/compare/foundation-software-alternative/", SITEMAP_NOTE),
        ("smartcrew vs viewpoint", "comparison", "https://www.smartcrewhq.com/compare/viewpoint-alternative/", SITEMAP_NOTE),
        ("excavation software with certified payroll", "commercial", "https://www.smartcrewhq.com/features/certified-payroll/", SITEMAP_NOTE),
        ("heavy civil estimating and job cost in one system", "commercial", "https://www.smartcrewhq.com/features/excavation-estimating/", STANCE_NOTE),
        ("machine control software for grading contractors", "commercial", "https://www.smartcrewhq.com/features/machine-control/", STANCE_NOTE),
        ("smartcrew api", "informational", "https://www.smartcrewhq.com/integrations/", SITEMAP_NOTE),
    ]
    for query, intent, url, note in gaps:
        add(query, intent, "gap", url, note)

    rows: list[Keyword] = []
    for query, intent, cluster, url, note in specs:
        exists = url in known
        rows.append(
            Keyword(
                query=query,
                intent=intent,
                cluster=cluster,
                suggested_target_page=url,
                page_exists=exists,
                notes=note if not exists else "",
            )
        )
    return rows


def page_gaps(rows: list[Keyword] | None = None) -> list[Keyword]:
    return [row for row in (rows if rows is not None else build_keywords()) if not row.page_exists]


def write_keywords_csv(path: Path | None = None, rows: list[Keyword] | None = None) -> Path:
    target = path or keywords_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = rows if rows is not None else build_keywords()
    with target.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["query", "intent", "cluster", "suggested_target_page", "page_exists", "notes"],
        )
        writer.writeheader()
        for row in body:
            writer.writerow(row.as_row())
    return target


def gap_summary(rows: list[Keyword] | None = None) -> dict[str, int]:
    body = rows if rows is not None else build_keywords()
    gaps = [row for row in body if not row.page_exists]
    return {
        "queries": len(body),
        "with_page": sum(1 for row in body if row.page_exists),
        "gaps": len(gaps),
    }


# Columns that keyword tools and Search Console put next to the query. In a plain list of
# queries a whole comma-separated cell is almost never one of these.
_EXPORT_COLUMNS = frozenset({
    "intent", "volume", "search volume", "avg. monthly searches", "kd", "keyword difficulty", "difficulty", "cpc",
    "competition", "competitive density", "clicks", "impressions", "ctr", "position", "traffic", "url",
})
# Other columns that Google Ads and Keyword Planner put next to the keyword. Below title rows, a
# header with the query column counts only when it also names one of these or a metric column.
_EXPORT_LABEL_COLUMNS = frozenset({
    "match type", "campaign", "ad group", "currency", "keyword status", "status", "impr.", "avg. cpc", "cost",
    "conversions", "max. cpc", "three month change", "yoy change", "competition (indexed value)",
})
_DELIMITERS = (",", "\t", ";")
# Keyword Planner and Google Ads exports put a title and a date range above the header row.
_HEADER_LINES = 10


def load_keyword_file(path: str | Path) -> list[Keyword]:
    """Queries to collect or track from a file: a CSV with a query column (optional ``intent``)
    or one query per line. The query column may be named query, keyword, queries or top queries,
    as keyword tool and Search Console exports name it, and the file may be separated by commas,
    tabs or semicolons. The header may sit below title rows within the first 10 lines, as in
    Keyword Planner and Google Ads exports; the rows above it are dropped. Below the first line,
    a row counts as a header only when it has two or more cells, every row under it has as many,
    and it names the query column next to an export column (volume, clicks, match type, campaign
    and the like) or has a column of only numbers. So a later line that is just "keyword", or a
    query with commas in a plain list, stays a query.
    A table whose header has none of those is refused rather than read as one query per line,
    since each row would become a paid search. It counts as a table when every line has the
    same number of cells and the header names a metric column (volume, clicks, CPC and the
    like) or a column holds only numbers below a text header, or when a header below title rows
    has a column of only numbers. A plain list whose queries contain commas is still read one
    query per line. Intent falls back to the shared rule (aeo.ranksim.intents)."""
    import csv as _csv

    from aeo.ranksim.groundtruth import QUERY_COLUMNS
    from aeo.ranksim.intents import KNOWN_INTENTS, intent_for, normalize_query

    def cells(line: str, delimiter: str) -> list[str]:
        return [cell.strip() for cell in next(_csv.reader([line], delimiter=delimiter))]

    text = Path(path).read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    rows: list[tuple[str, str | None]] = []
    found = None
    for start in range(min(_HEADER_LINES, len(lines))):
        for delimiter in _DELIMITERS:
            if start:
                header = _titled_table_header(lines, start, delimiter, cells) or []
            else:
                header = [cell.lower() for cell in cells(lines[0], delimiter)]
            query_column = next((name for name in QUERY_COLUMNS if name in header), None)
            if query_column:
                found = (delimiter, header, query_column)
                lines = lines[start:]  # title rows above the header are not queries
                break
        if found:
            break
    if found:
        delimiter, header, query_column = found
        position = header.index(query_column)
        intent_position = header.index("intent") if "intent" in header else None
        for line in lines[1:]:
            row = cells(line, delimiter)
            if len(header) == 1:
                # A one-column list keeps a query with an unquoted comma whole.
                query = row[0] if len(row) == 1 else line.strip()
            else:
                query = row[position] if position < len(row) else ""
            intent = (row[intent_position] if intent_position is not None and intent_position < len(row) else "").lower()
            rows.append((query, intent if intent in KNOWN_INTENTS else None))
    else:
        for delimiter in _DELIMITERS:
            header = _metric_table_header(lines, delimiter, cells)
            if header:
                raise ValueError(
                    f"{path} looks like a table with the columns {', '.join(header)}, but none is a query column. "
                    f"Name the query column one of: {', '.join(QUERY_COLUMNS)}. "
                    "If every line is one query, add a first line that says query."
                )
        rows = [(line.strip(), None) for line in lines]
    seen: set[str] = set()
    out: list[Keyword] = []
    for query, intent in rows:
        key = normalize_query(query)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(Keyword(query=key, intent=intent or intent_for(key, {}), cluster="file", suggested_target_page="", page_exists=False, notes=f"from {Path(path).name}"))
    return out


def _number(cell: str) -> bool:
    import math

    value = cell.strip().strip("$€£%<>~").replace(",", "").replace(" ", "")
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _numeric_column(header: list[str], body: list[list[str]]) -> bool:
    """Whether a column with a text name holds only numbers in the rows below it."""
    for column, name in enumerate(header):
        values = [row[column] for row in body if row[column]]
        if name and not _number(name) and values and all(_number(value) for value in values):
            return True
    return False


def _metric_table_header(lines: list[str], delimiter: str, cells) -> list[str] | None:
    """The header of a keyword table with no query column, or None when the lines read as queries."""
    table = [cells(line, delimiter) for line in lines[:21]]
    if table and len(table[0]) >= 2 and all(len(row) == len(table[0]) for row in table):
        header = [cell.lower() for cell in table[0]]
        if any(cell in _EXPORT_COLUMNS for cell in header) or _numeric_column(header, table[1:]):
            return header
    for start in range(1, min(_HEADER_LINES, len(lines))):
        header = _titled_table_header(lines, start, delimiter, cells)
        if header:
            return header
    return None


def _titled_table_header(lines: list[str], start: int, delimiter: str, cells) -> list[str] | None:
    """The header of an export table that starts below title rows, at ``lines[start]``, or None.

    It needs two or more filled cells and at least one row below it, and each of the next 20
    rows must have as many cells. It must also name the query column next to an export column
    (volume, clicks, match type, campaign and the like), or have a column of only numbers.
    A line further down a plain list of queries, even one with commas, rarely looks like that."""
    from aeo.ranksim.groundtruth import QUERY_COLUMNS

    header = [cell.lower() for cell in cells(lines[start], delimiter)]
    body = [cells(line, delimiter) for line in lines[start + 1 : start + 21]]
    if sum(1 for cell in header if cell) < 2 or not body or any(len(row) != len(header) for row in body):
        return None
    named = any(cell in QUERY_COLUMNS for cell in header) and any(
        cell in _EXPORT_COLUMNS or cell in _EXPORT_LABEL_COLUMNS for cell in header
    )
    return header if named or _numeric_column(header, body) else None

