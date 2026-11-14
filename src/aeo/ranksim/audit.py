"""The audit: specific issues on the site's own pages, each with a plain comment that names the page, the numbers
and what to change.

Every check is a small function registered in CHECKS (in order) with the @check decorator. A check takes the
AuditContext and returns a list of issue dicts (shape in docs/BUILD_PLAN.md 4.4, built with _issue). audit() runs
the built-in CHECKS, then the checks plugins return from their audit_checks hook with the same context, then
sorts: high before medium before low, searches before page details, then by title.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import median
from urllib.parse import urlsplit

from aeo.ranksim.report import _tokens
from aeo.ranksim.site import SitePage

TITLE_LIMIT = 60
META_LIMIT = 160
THIN_WORDS = 300
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
RANKING_KINDS = {"not-ranking", "no-page", "behind"}

log = logging.getLogger(__name__)


@dataclass
class AuditContext:
    """What every check reads. Checks must not change it."""

    project: object | None
    pages: list[SitePage]  # the site's own pages, readable or not
    rankings: list[dict]  # build_report's rankings rows: q, position, url, checked, top
    docs: dict[str, SitePage]  # stored copies of the top 10 pages, by URL
    database: str | None = None
    meta: object | None = None  # the project's workspace meta, when the report passes it
    readable: list[SitePage] = field(init=False)
    own_by_url: dict[str, SitePage] = field(init=False)

    def __post_init__(self) -> None:
        self.readable = [page for page in self.pages if page.status == "fetched"]
        self.own_by_url = {page.url: page for page in self.readable}


Check = Callable[[AuditContext], list[dict]]
CHECKS: list[Check] = []


def check(fn: Check) -> Check:
    """Register a built-in check. Checks run in the order they are registered."""
    CHECKS.append(fn)
    return fn


def _issue(severity: str, kind: str, title: str, comment: str, *, page: str | None = None, search: str | None = None, fix: str = "") -> dict:
    return {"id": _issue_id(kind, page, search), "severity": severity, "kind": kind, "title": title, "comment": comment, "page": page, "search": search, "fix": fix}


def _issue_id(kind: str, page: str | None, search: str | None, selector: str | None = None) -> str:
    """kind-page-search[-selector] in lowercase letters, digits and dashes. Over 120 characters it is cut to 111 and
    ends in a short hash of the whole, so two long ids that share a start still differ."""
    parts = [kind, page or "", search or ""] + ([selector] if selector else [])
    full = re.sub(r"[^a-z0-9]+", "-", "-".join(parts).lower()).strip("-")
    if len(full) <= 120:
        return full
    return f"{full[:111].rstrip('-')}-{hashlib.sha1(full.encode()).hexdigest()[:8]}"


def _unique_ids(issues: list[dict]) -> None:
    """Give every issue a different id, in list order: a repeated id gets -2, -3 and so on (never an id in use)."""
    taken = {issue["id"] for issue in issues}
    seen: set[str] = set()
    for issue in issues:
        base = issue["id"]
        if base not in seen:
            seen.add(base)
            continue
        number = 2
        while f"{base}-{number}" in taken or f"{base}-{number}" in seen:
            number += 1
        issue["id"] = f"{base}-{number}"
        seen.add(issue["id"])


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "..."


# -- the site's own pages -------------------------------------------------------------------------


@check
def unreadable(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.pages:
        if page.status != "fetched":
            reason = page.error or page.status
            issues.append(
                _issue("high", "unreadable", f"We couldn't read {page.path}", f"It answered with {reason}. If Google gets the same, this page can't rank.", page=page.url, fix="Check the page loads for visitors and isn't blocked by robots.txt.")
            )
    return issues


@check
def title_length(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.readable:
        if not page.title:
            issues.append(_issue("high", "title-missing", f"{page.path} has no title", "Google shows the title as the blue link. Without one it makes one up.", page=page.url, fix="Add a title that starts with the search this page should win."))
        elif len(page.title) > TITLE_LIMIT:
            issues.append(
                _issue("medium", "title-long", f"Title is {len(page.title)} characters", f"Google shows about {TITLE_LIMIT}, so \"{_short(page.title, TITLE_LIMIT)}\" is roughly what people see on {page.path}.", page=page.url, fix="Move the main search to the front and cut the rest.")
            )
        elif len(page.title) < 25:
            issues.append(_issue("low", "title-short", f"Title is only {len(page.title)} characters", f"\"{page.title}\" leaves room to name what {page.path} is about.", page=page.url, fix="Add the main search this page should rank for."))
    return issues


@check
def meta_description(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.readable:
        if not page.meta:
            issues.append(_issue("medium", "meta-missing", f"{page.path} has no meta description", "Google will pull a line from the page for the snippet, which is often not the line you want.", page=page.url, fix="Write one or two sentences, about 150 characters, that answer the search."))
        elif len(page.meta) > META_LIMIT:
            issues.append(_issue("low", "meta-long", f"Meta description is {len(page.meta)} characters", f"Google cuts it near {META_LIMIT}, so the end of the sentence on {page.path} won't show.", page=page.url, fix="Shorten it and put the point first."))
    return issues


@check
def main_heading(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.readable:
        if not page.h1:
            issues.append(_issue("high", "h1-missing", f"{page.path} has no H1", "The main heading tells Google and readers what the page is about.", page=page.url, fix="Add one H1 that matches the title's main search."))
        elif page.h1_count > 1:
            issues.append(_issue("low", "h1-many", f"{page.h1_count} H1 headings on {page.path}", "More than one main heading makes the page's topic less clear.", page=page.url, fix="Keep one H1 and turn the others into H2s."))
    return issues


@check
def thin_content(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.readable:
        if page.words < THIN_WORDS:
            issues.append(
                _issue("medium", "thin", f"{page.path} has {page.words} words", f"Pages under {THIN_WORDS} words rarely win competitive searches, and the model ranks pages under 150 words behind every fuller page.", page=page.url, fix="Add the details a buyer needs: prices, steps, comparisons, questions.")
            )
    return issues


@check
def duplicate_titles(ctx: AuditContext) -> list[dict]:
    titles: dict[str, list[str]] = defaultdict(list)
    for page in ctx.readable:
        if page.title:
            titles[page.title.strip().lower()].append(page.url)
    issues = []
    for title, urls in titles.items():
        if len(urls) > 1:
            paths = ", ".join(urlsplit(url).path or "/" for url in urls[:3])
            issues.append(_issue("medium", "title-duplicate", f"{len(urls)} pages share one title", f"{paths} all use \"{_short(title, 50)}\". Google may show only one of them.", page=urls[0], fix="Give each page its own title for its own search."))
    return issues


# -- the searches ---------------------------------------------------------------------------------


def _leaders(row: dict) -> tuple[list[dict], str]:
    leaders = [item for item in row["top"] if not item["you"]][:3]
    return leaders, ", ".join(item["domain"] for item in leaders)


@check
def not_on_page_one(ctx: AuditContext) -> list[dict]:
    issues = []
    for row in ctx.rankings:
        if not row["checked"] or row["position"] is not None:
            continue
        search = row["q"]
        _top3, names = _leaders(row)
        page = ctx.own_by_url.get(row["url"]) if row["url"] else None
        if page is not None:
            issues.append(
                _issue("high", "not-ranking", f"Not in the top 10 for \"{search}\"", f"{page.path} is about this search but isn't on the first page. {names} are the top 3.", page=page.url, search=search, fix="Compare your page with theirs in the editor and test changes.")
            )
        else:
            issues.append(
                _issue("high", "no-page", f"No page for \"{search}\"", f"Your site uses this phrase but no page targets it, and it isn't in the top 10. {names} hold the top 3.", search=search, fix="Write a page for it and test it in the editor before you publish.")
            )
    return issues


@check
def behind_the_top3(ctx: AuditContext) -> list[dict]:
    issues = []
    for row in ctx.rankings:
        if not row["checked"] or row["position"] is None:
            continue
        search = row["q"]
        leaders, names = _leaders(row)
        mine = ctx.own_by_url.get(row["url"])
        if row["position"] <= 3 or mine is None:
            continue
        rivals = [ctx.docs[item["url"]] for item in leaders if item["url"] in ctx.docs]
        notes = []
        if rivals:
            mid = median(doc.words for doc in rivals)
            if mid and mine.words < 0.65 * mid:
                notes.append(f"the top 3 average {int(mid):,} words and yours has {mine.words:,}")
            tables = sum(1 for doc in rivals if doc.tables)
            if tables >= 2 and not mine.tables:
                notes.append(f"{tables} of them have a table and yours doesn't")
            faqs = sum(1 for doc in rivals if doc.has_faq)
            if faqs >= 2 and not mine.has_faq:
                notes.append(f"{faqs} of them answer common questions in an FAQ")
        want = _tokens(search)
        missing = sorted(want - _tokens(mine.title))
        if missing:
            notes.append(f"your title is missing \"{' '.join(missing)}\"")
        detail = "; ".join(notes) if notes else f"{names} are ahead of you"
        issues.append(
            _issue("medium", "behind", f"#{row['position']} for \"{search}\"", f"{mine.path} is on page one, but {detail}.", page=mine.url, search=search, fix="Open it in the editor, make the change, and test it.")
        )
    return issues


# -- the audit ------------------------------------------------------------------------------------


def audit(project, pages: list[SitePage], rankings: list[dict], docs: dict[str, SitePage], *, database: str | None = None, meta=None) -> list[dict]:
    """Specific issues, each with a plain comment that names the page, the numbers and what to change."""
    ctx = AuditContext(project, pages, rankings, docs, database=database, meta=meta)
    issues: list[dict] = []
    for run in CHECKS:
        issues.extend(run(ctx))
    issues.extend(_plugin_issues(ctx))
    issues.sort(key=lambda issue: (SEVERITY_ORDER[issue["severity"]], issue["kind"] not in RANKING_KINDS, issue["title"]))
    _unique_ids(issues)
    return issues


def _plugin_issues(ctx: AuditContext) -> list[dict]:
    """Issues from the checks plugins return from audit_checks. A check that raises or returns a bad issue is
    logged and skipped; it never breaks the audit."""
    from aeo.ranksim import plugins

    issues: list[dict] = []
    for run in plugins.collect("audit_checks"):
        label = f"audit check {getattr(run, '__module__', '?')}.{getattr(run, '__qualname__', repr(run))}"
        if not callable(run):
            log.warning("ranksim plugins: %s is not callable; skipped.", label)
            continue
        ok, found = plugins.guarded(label, run, ctx)
        if not ok:
            continue
        for item in found or []:
            issue = _plugin_issue(item)
            if issue is None:
                log.warning("ranksim plugins: %s returned an issue without a severity (high, medium, low), kind and title; skipped.", label)
                continue
            problem = plugins.json_error(issue)
            if problem is not None:
                log.warning("ranksim plugins: %s returned an issue that is not JSON (%s); skipped.", label, problem)
                continue
            issues.append(issue)
    return issues


def _plugin_issue(item) -> dict | None:
    if not isinstance(item, dict) or item.get("severity") not in SEVERITY_ORDER:
        return None
    if not isinstance(item.get("kind"), str) or not isinstance(item.get("title"), str):
        return None
    issue = dict(item)
    for key, default in (("comment", ""), ("fix", ""), ("page", None), ("search", None)):
        issue.setdefault(key, default)
    if not issue.get("id") or not isinstance(issue["id"], str):
        element = issue.get("element")
        selector = element.get("selector") if isinstance(element, dict) else None
        issue["id"] = _issue_id(issue["kind"], issue["page"], issue["search"], selector if isinstance(selector, str) else None)
    return issue
