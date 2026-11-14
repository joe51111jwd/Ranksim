"""PREDICTED impact per audit issue (predict workstream).

POST /api/v1/projects/{slug}/issues/impact  {"ids": ["issue-id", ...], "issues": [issue, ...]?}

"issues" is optional: the issue objects the caller already has from the report. Sending them skips the report
build (the slow part of a first answer). Ids without a sent issue are looked up in the server's report.

For each issue: apply its suggestion to the stored page HTML on the server (the issue's ``action`` says how,
see audit.py), then run the predictor on the page's target searches and return the PREDICTED move with a
confidence label. A change that does not touch anything the ranking model reads gets ``no_estimate``, said
plainly. Results are cached per pull under plugins/predict-impact. Everything here is PREDICTED, never a guarantee.
"""

from __future__ import annotations

import hashlib
import json
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

FEATURE = "predict-impact"
MAX_IDS = 50
CACHE_VERSION = 2
APPLY_ACTIONS = {"text", "attr", "retag", "append", "prepend", "after"}
NOTE = "PREDICTED, not a guarantee. Positions come from the same model as Test changes, and most calls are UNSURE."


class ImpactError(ValueError):
    pass


def apply_suggestion(html: str, issue: dict) -> str | None:
    """The page HTML with the issue's suggestion applied, or None when it cannot be applied here."""
    from bs4 import BeautifulSoup  # imported on first use: plugins load at startup, and startup stays light

    action = issue.get("action")
    suggestion = issue.get("suggestion")
    selector = ((issue.get("element") or {}).get("selector") or "").strip()
    if action not in APPLY_ACTIONS or not suggestion or not selector:
        return None
    soup = BeautifulSoup(html, "lxml")
    try:
        target = soup.select_one(selector)
    except Exception:  # noqa: BLE001 - a selector soupsieve cannot read is the same as no match
        target = None
    if target is None:
        return None
    if action == "text":
        target.clear()
        target.append(str(suggestion))
    elif action == "attr":
        attr = (issue.get("element") or {}).get("attr")
        if not attr:
            return None
        target[attr] = str(suggestion)
    elif action == "retag":
        name = str(suggestion).strip().lower()
        if not name.isalnum():
            return None
        target.name = name
    else:
        fragment = BeautifulSoup(str(suggestion), "lxml")
        holder = fragment.body or fragment.head or fragment
        nodes = list(holder.contents) if holder is not None else []
        if not nodes:
            return None
        if action == "append":
            for node in nodes:
                target.append(node)
        elif action == "prepend":
            for node in reversed(nodes):
                target.insert(0, node)
        else:  # after
            anchor = target
            for node in nodes:
                anchor.insert_after(node)
                anchor = node
    return str(soup)


# Issues that carry no ready change. A plain reason, said once, for each kind the audit raises without one.
NO_CHANGE_REASONS = {
    # No "where it stands today" line: the model can only place a page among today's top 10, so for a page Google
    # does not show it would name a top 10 slot that the real results contradict.
    "not-ranking": "There is no single change to try. Open the page in the editor, close the gaps and use Test changes.",
    "behind": "There is no single change to try. Open the page in the editor and use Test changes.",
    "thin-vs-top": "The model cannot judge new writing it has not seen. Write the section, then use Test changes on it.",
    "few-links": "No estimate. In this site's results, pages with more links did not reliably rank higher, so the model "
    "would not credit this change. Links still help people and Google find your pages.",
    "script-built": "No estimate. The model reads the text the crawler stored, so it cannot tell script-drawn text from "
    "server-drawn text. Google can.",
}
EMPTY_JSONLD = '<script type="application/ld+json">{"@context": "https://schema.org"}</script>'


def _stable_signs(ctx) -> dict:
    from aeo.ranksim import predict_service

    try:
        return predict_service.predictor_for(ctx).stable
    except Exception:  # noqa: BLE001 - no predictor means no stand-in change
        return {}


def hypothetical(issue: dict, html: str, stable: dict | None = None):
    """A well-founded stand-in change for an issue that has no ready suggestion.

    Returns (issue with a suggestion, assumption text), a plain reason string, or None when there is none.
    """
    from aeo.ranksim.predict import has_schema

    kind = issue.get("kind") or ""
    selector = ((issue.get("element") or {}).get("selector") or "").strip()
    search = (issue.get("search") or "").strip()
    if kind == "h1-search" and selector and search:
        text = search[:1].upper() + search[1:]
        return dict(issue, action="text", suggestion=text), f'Assumes the H1 is changed to name the search, for example "{text}".'
    if kind == "schema-gap":
        if has_schema(html):
            return "No estimate. The model only reads whether a page has structured data, and this page already has some."
        return dict(issue, action="append", suggestion=EMPTY_JSONLD, element={"selector": "head"}), "Assumes structured data is added to the page."
    if kind == "few-links" and selector and (stable or {}).get("links") == 1:
        links = "".join(f'<p><a href="/">Related page {n}</a></p>' for n in (1, 2, 3))
        return dict(issue, action="append", suggestion=links), "Assumes three links to related pages are added to the body text."
    return None


def _unchanged_for(searches: list[str], url: str, before_html: str, after_html: str) -> bool:
    """True when every content signal the model reads is the same before and after, on every search."""
    from aeo.ranksim.loop import page_from_html
    from aeo.ranksim.predict import page_signals

    before = page_from_html(url, before_html, is_ours=True)
    after = page_from_html(url, after_html, is_ours=True)
    return all(_same(page_signals(q, before, before_html, []), page_signals(q, after, after_html, [])) for q in searches)


def _same(left: dict, right: dict) -> bool:
    for key in set(left) | set(right):
        a, b = left.get(key), right.get(key)
        if a != a and b != b:  # both NaN
            continue
        if a is None or b is None or abs(float(a) - float(b)) > 1e-9:
            return False
    return True


def _compact_row(row: dict) -> dict:
    conf = row.get("confidence") or {}
    return {
        "search": row["search"],
        "label": "PREDICTED",
        "method": row.get("method"),
        "now": row.get("now"),
        "with_change": row.get("with_edit"),
        "move": row.get("move"),
        "move_places": row.get("move_places"),
        "google_position": row.get("observed_position"),
        "google_seen": row.get("google_seen", ""),
        "confidence": {
            key: conf.get(key)
            for key in ("label", "short", "text", "backtest_pairwise", "backtest_pairs", "near_chance")
        },
    }


def _headline(rows: list[dict]) -> str:
    moved = [row for row in rows if row.get("move_places")]
    if not rows:
        return "No estimate: none of this page's searches could be scored."
    if not moved:
        return f"PREDICTED: no change in position on {len(rows)} search{'es' if len(rows) != 1 else ''}."
    best = max(moved, key=lambda row: row["move_places"])
    worst = min(moved, key=lambda row: row["move_places"])
    pick = best if best["move_places"] > 0 else worst
    places = abs(pick["move_places"])
    way = "up" if pick["move_places"] > 0 else "down"
    label = (pick.get("confidence") or {}).get("label") or "UNSURE"
    return f"PREDICTED: {way} {places} place{'s' if places != 1 else ''} on {pick['search']} ({label})."


def estimate(ctx, issue: dict, html: str | None, searches: list[str]) -> dict:
    """One issue's PREDICTED impact. ``html`` is the stored page; ``searches`` limit the targets when given."""
    from aeo.ranksim import predict_service

    base = {"id": issue.get("id"), "label": "PREDICTED", "note": NOTE}
    page = issue.get("page")
    if not page:
        return dict(base, status="no_estimate", reason="This issue is about the whole site, not one page.")
    kind = issue.get("kind") or ""
    if not html:
        return dict(base, status="no_estimate", reason="No stored copy of this page, so the change cannot be tried.")
    assumption = ""
    if issue.get("action") == "copy" or not issue.get("suggestion"):
        tried = hypothetical(issue, html, _stable_signs(ctx))
        if tried is None:
            reason = NO_CHANGE_REASONS.get(kind, "There is no single change to apply to the page for this issue.")
            return dict(base, status="no_estimate", reason=reason)
        if isinstance(tried, str):
            return dict(base, status="no_estimate", reason=tried)
        issue, assumption = tried
    changed = apply_suggestion(html, issue)
    if changed is None:
        return dict(base, status="no_estimate", reason="The suggestion could not be applied to the stored page.")
    probes = list(dict.fromkeys([q for q in [issue.get("search"), *searches, *ctx.project.searches] if q]))[:40]
    if probes and _unchanged_for(probes, page, html, changed):
        return dict(
            base,
            status="no_estimate",
            reason="This change leaves every signal the ranking model reads the same on this site's searches (for "
            "example a shorter title that keeps the same search words). It can still matter for people and for Google.",
        )
    try:
        result = predict_service.test_searches(
            ctx,
            changed,
            page,
            searches=searches or None,
            original_html=html,
            first=issue.get("search") if issue.get("search") in ctx.project.searches else None,
        )
    except predict_service.PredictError as exc:
        return dict(base, status="no_estimate", reason=str(exc))
    rows = [_compact_row(row) for row in result["searches"] if row.get("with_edit") is not None]
    if not rows:
        status = "no_estimate"
    elif all(not row.get("move_places") for row in rows):
        status = "no_change"  # moves under half a place on every search
    else:
        status = "estimate"
    return dict(
        base,
        status=status,
        headline=_headline(rows),
        searches=rows,
        summary=result.get("summary"),
        reason="" if rows else "None of this page's searches could be scored.",
        assumption=assumption,
        backtest_note=result.get("backtest_note", ""),
    )


def _issue_hash(issue: dict) -> str:
    keep = {key: issue.get(key) for key in ("id", "kind", "page", "search", "suggestion", "action", "element")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _cache_file(project, pull: str | None, searches) -> object:
    """One file per pull and search list. A new pull or a changed search list starts a fresh file."""
    from aeo.ranksim import plugins

    if not pull:
        return None
    sig = hashlib.sha256(json.dumps(sorted(searches or [])).encode()).hexdigest()[:10]
    return plugins.data_dir(project, FEATURE) / f"impact-v{CACHE_VERSION}-{pull}-{sig}.json"


ISSUE_KEYS = ("id", "kind", "page", "search", "suggestion", "action", "element")


def _clean_issue(item: dict) -> dict:
    """Only the fields an estimate reads, as plain strings, from an issue the caller sent."""
    out: dict = {}
    for key in ISSUE_KEYS:
        value = item.get(key)
        if key == "element" and isinstance(value, dict):
            out[key] = {k: str(v)[:2000] for k, v in value.items() if k in ("selector", "attr", "tag", "text") and isinstance(v, str)}
        elif isinstance(value, str):
            out[key] = value[:20000]
    return out


def impacts(ui, slug: str, ids: list[str], given: list[dict] | None = None) -> dict:
    from aeo.ranksim.api.editor import page_html
    from aeo.ranksim.api.projects import project_report

    project = ui.project(slug)
    try:
        ctx = ui.context(slug)
    except Exception as exc:  # noqa: BLE001
        raise ImpactError("The prediction engine is not ready for this site yet. Refresh it in Settings first.") from exc
    pull = getattr(ctx, "pull_id", None)
    path = _cache_file(project, pull, project.searches)
    cache: dict = {}
    if path is not None and path.exists():
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    latest = cache.get("_by_id", {})
    if all(issue_id in latest and latest[issue_id] in cache for issue_id in ids):
        # Every answer is cached for this pull and these searches: no need to rebuild the report.
        return {"label": "PREDICTED", "note": NOTE, "pull": pull, "impacts": [cache[latest[issue_id]] for issue_id in ids]}
    # Issues the caller already holds (the sheet has the report) skip the report build. They are trusted only for
    # this answer: the id-only fast path above is filled from the server's own report, never from a caller's copy.
    sent = {item["id"]: item for item in (given or []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    by_id: dict | None = None
    out = []
    dirty = False
    for issue_id in ids:
        trusted = issue_id not in sent
        if trusted:
            if by_id is None:
                report = project_report(ui, slug)
                by_id = {issue.get("id"): issue for issue in report.get("issues") or []}
            issue = by_id.get(issue_id)
        else:
            issue = _clean_issue(sent[issue_id])
        if issue is None:
            out.append({"id": issue_id, "label": "PREDICTED", "status": "unknown", "reason": "No issue with this id in the current report."})
            continue
        key = f"{issue_id}:{_issue_hash(issue)}"
        if trusted and latest.get(issue_id) != key:
            latest[issue_id] = key
            dirty = True
        if key in cache:
            out.append(cache[key])
            continue
        found = page_html(ui, slug, issue["page"]) if issue.get("page") else None
        try:
            result = estimate(ctx, issue, (found or {}).get("html"), [])
        except Exception as exc:  # noqa: BLE001 - one issue never breaks the others
            log.warning("predict-impact: %s failed: %s", issue_id, type(exc).__name__)
            result = {"id": issue_id, "label": "PREDICTED", "status": "no_estimate", "reason": "The estimate could not be worked out for this issue."}
        else:
            cache[key] = result
            dirty = True
        out.append(result)
    if dirty and path is not None and not ui.public_demo:
        try:
            for old in path.parent.glob("impact-*.json"):
                if old != path:
                    old.unlink(missing_ok=True)
            tmp = path.with_suffix(".tmp")
            cache["_by_id"] = latest
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass
    return {"label": "PREDICTED", "note": NOTE, "pull": pull, "impacts": out}


def register(app, ui) -> None:
    from aeo.ranksim.api.common import _check_header_csrf, _error, _json_body

    @app.post("/api/v1/projects/{slug}/issues/impact")
    async def api_issue_impact(slug: str, request: Request) -> JSONResponse:
        _check_header_csrf(ui, request)
        body = await _json_body(request, 1024 * 1024)
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids:
            return _error(400, "Send a list of issue ids.")
        ids = [str(item)[:200] for item in ids if isinstance(item, str)][:MAX_IDS]
        if not ids:
            return _error(400, "Send a list of issue ids.")
        given = body.get("issues") if isinstance(body.get("issues"), list) else None
        try:
            return JSONResponse(await run_in_threadpool(impacts, ui, slug, ids, (given or [])[:MAX_IDS]))
        except LookupError:
            return _error(404, "No such project.")
        except ImpactError as exc:
            return _error(409, str(exc))
