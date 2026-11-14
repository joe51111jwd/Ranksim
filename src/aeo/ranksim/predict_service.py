"""Test one draft on every search it targets, for a loaded project.

Uses the recipe that was frozen in ``evals/predict-2026-11-14/training.json``: content plus site
features, L2 0.3, the new-page handicap and the persistence slope learned on SmartCrew's
Oct 29 to Nov 6 move. The pairwise weights are refit on each project's own observed results.

Per search the result says PREDICTED position now and with the edit, the move, and a
confidence label with that search's backtest pairwise and pair count. A backtest is a
held-out-query score on the same snapshot. It is never called accuracy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from aeo.ranksim.predict import (
    ALL_FEATURES,
    ONE_FAMILY_SHORT,
    ONE_FAMILY_TEXT,
    UNSURE,
    Group,
    PairwiseModel,
    anchored_position,
    confidence,
    expected_position,
    fit_pairwise,
    guidance,
    page_signals,
    raw_counts,
    site_signals,
    term_match,
)

# Frozen in evals/predict-2026-11-14/training.json (chosen on training data only).
RECIPE_L2 = 0.3
ENTRANT_LOGIT = -0.8377
PERSISTENCE_SLOPE = 0.3985
GUIDE_ABOVE = 3
MAX_SEARCHES = 40

FORWARD_NOTE = (
    "One forward check, on SmartCrew searches only (2026-11-06 to 2026-11-09, page HTML fetched 2026-11-14, "
    "83 of 97 new pages scorable): new pages that entered a top 10 were put in the right order among themselves "
    "in 92 of 151 pairs (0.61). Against pages already ranking it was not clearly better than placing every new "
    "page last, and its position number was no better than a constant guess. Weights here are refit on this "
    "project, so that check does not measure this project."
)


class PredictError(ValueError):
    pass


@dataclass
class Item:
    url: str
    key: str
    position: float
    doc: object | None
    html: str | None = None  # only while building; dropped once content signals are worked out
    content: dict | None = None  # page signals for this search (no site signals), None when not stored
    raw: dict = field(default_factory=dict)
    has_html: bool = False


@dataclass
class ProjectPredictor:
    model: PairwiseModel
    backtest: dict[str, dict]
    stable: dict[str, int]
    items: dict[str, list[Item]]  # search -> observed results
    site_rows: list[tuple[str, str, str, float]]
    groups: int = 0
    notes: list[str] = field(default_factory=list)
    families: int = 0


def _key(url: str) -> str:
    from aeo.ranksim.project import loose_key

    return loose_key(url)


def _domain(url: str) -> str:
    return _key(url).split("/", 1)[0]


def _html_for(ctx, url: str) -> str | None:
    database = getattr(ctx, "database", None)
    if not database:
        return None
    try:
        from aeo.ranksim.tester import stored_html

        found = stored_html(database, url)
    except Exception:  # noqa: BLE001 - no stored copy is the same as missing
        return None
    return found[0] if found else None


def observed_items(ctx) -> dict[str, list[Item]]:
    """Observed top 10 per search, with each stored page's signals worked out once."""
    from aeo.ranksim.project import TOP_DEPTH, _lookup

    out: dict[str, list[Item]] = {}
    html_cache: dict[str, str | None] = {}
    for search, entry in ctx.google.by_search().items():
        seen = set()
        rows = []
        for result in sorted(entry.results, key=lambda r: r.position):
            k = _key(result.url)
            if k in seen or not 1 <= result.position <= TOP_DEPTH:
                continue
            seen.add(k)
            doc = _lookup(ctx.pages, result.url)
            if doc is not None and not (doc.title or doc.body or "").strip():
                doc = None
            html = None
            if doc is not None:
                if k not in html_cache:
                    html_cache[k] = _html_for(ctx, result.url)
                html = html_cache[k]
            rows.append(Item(result.url, k, float(result.position), doc, html))
        peers = [item.doc for item in rows if item.doc is not None]
        for item in rows:
            if item.doc is not None:
                item.content = page_signals(search, item.doc, item.html, peers)
                item.raw = raw_counts(item.doc, item.html)
                item.has_html = bool(item.html)
            item.html = None
        out[search] = rows
    return out


def _signals(search: str, url: str, doc, html, peers, rows) -> dict[str, float]:
    if doc is None:
        signals = {name: float("nan") for name in ALL_FEATURES}
    else:
        signals = page_signals(search, doc, html, peers)
    signals.update(site_signals(_domain(url), _key(url), search, rows))
    return signals


def _item_signals(search: str, item: Item, rows) -> dict[str, float]:
    signals = dict(item.content) if item.content is not None else {name: float("nan") for name in ALL_FEATURES}
    signals.update(site_signals(_domain(item.url), item.key, search, rows))
    return signals


def _groups(items: dict[str, list[Item]], rows) -> list[Group]:
    groups: list[Group] = []
    for search, found in sorted(items.items()):
        stored = [item for item in found if item.content is not None]
        if len(stored) < 2:
            continue
        groups.append(
            Group(
                search,
                [_item_signals(search, item, rows) for item in stored],
                [item.position for item in stored],
                [item.key for item in stored],
            )
        )
    return groups


def build_predictor(ctx) -> ProjectPredictor:
    """Fit the pairwise model on this project's observed results, plus a family-out backtest."""
    from aeo.ranksim.snapshot_protocol import assign_families

    items = observed_items(ctx)
    rows = [(search, _domain(item.url), item.key, item.position) for search, found in items.items() for item in found]
    groups = _groups(items, rows)
    if not groups:
        raise PredictError("No search has two or more stored pages yet, so there is nothing to learn from. Run a pull first.")
    model = fit_pairwise(groups, ALL_FEATURES, l2=RECIPE_L2, trained_on="this project's observed results")
    model.entrant_logit = ENTRANT_LOGIT
    backtest: dict[str, dict] = {}
    stable = {name: 0 for name in ALL_FEATURES}
    families = assign_families([group.search for group in groups])
    folds = sorted(set(families.values()))
    if len(folds) >= 2:
        signs = []
        for fold in folds:
            # Hide the held-out family's positions from the site features too, so a sibling
            # search in the same snapshot cannot leak its order into the backtest.
            held = {search for search, family in families.items() if family == fold}
            fold_groups = _groups(items, [row for row in rows if row[0] not in held])
            train = [group for group in fold_groups if families[group.search] != fold]
            test = [group for group in fold_groups if families[group.search] == fold]
            fold_model = fit_pairwise(train, ALL_FEATURES, l2=RECIPE_L2)
            signs.append(fold_model.weights)
            for group in test:
                scores = [fold_model.score(signals) for signals in group.signals]
                good = total = 0
                for i in range(len(scores)):
                    for j in range(len(scores)):
                        if group.positions[i] < group.positions[j]:
                            total += 1
                            good += 1 if scores[i] > scores[j] else 0
                backtest[group.search] = {"pairwise": good / total if total else None, "pairs": total}
        for index, name in enumerate(ALL_FEATURES):
            if all(weights[index] > 0 for weights in signs):
                stable[name] = 1
            elif all(weights[index] < 0 for weights in signs):
                stable[name] = -1
    notes: list[str] = []
    return ProjectPredictor(model, backtest, stable, items, rows, len(groups), notes, len(folds))


_CACHE: dict[int, tuple] = {}
CACHE_VERSION = 3  # bump when features, signals or the recipe change


def _nan_to_none(signals: dict | None) -> dict | None:
    if signals is None:
        return None
    return {k: (None if v is None or v != v else float(v)) for k, v in signals.items()}


def _none_to_nan(signals: dict | None) -> dict | None:
    if signals is None:
        return None
    return {k: (float("nan") if v is None else float(v)) for k, v in signals.items()}


def _cache_path(ctx):
    """<project>/plugins/predict/predictor-<pull>-<hash>.json, or None when the project has no folder."""
    import hashlib

    project = getattr(ctx, "project", None)
    pull = getattr(ctx, "pull_id", None)
    if project is None or not getattr(project, "dir", None) or not pull:
        return None
    from aeo.ranksim import plugins

    sig = hashlib.sha256(json.dumps([CACHE_VERSION, sorted(project.searches), len(ctx.pages or {})]).encode()).hexdigest()[:12]
    return plugins.data_dir(project, "predict") / f"predictor-{pull}-{sig}.json"


def _dump(predictor: ProjectPredictor) -> dict:
    return {
        "version": CACHE_VERSION,
        "model": predictor.model.to_dict(),
        "backtest": predictor.backtest,
        "stable": predictor.stable,
        "groups": predictor.groups,
        "notes": predictor.notes,
        "families": predictor.families,
        "items": {
            search: [[item.url, item.key, item.position, _nan_to_none(item.content), item.raw, item.has_html] for item in found]
            for search, found in predictor.items.items()
        },
    }


def _load(ctx, data: dict) -> ProjectPredictor:
    from aeo.ranksim.project import _lookup

    items: dict[str, list[Item]] = {}
    for search, rows in data["items"].items():
        found = []
        for url, key, position, content, raw, has_html in rows:
            doc = _lookup(ctx.pages, url) if content is not None else None
            found.append(Item(url, key, float(position), doc, None, _none_to_nan(content), raw or {}, bool(has_html)))
        items[search] = found
    site_rows = [(search, _domain(item.url), item.key, item.position) for search, found in items.items() for item in found]
    return ProjectPredictor(
        PairwiseModel.from_dict(data["model"]),
        data["backtest"],
        data["stable"],
        items,
        site_rows,
        int(data["groups"]),
        list(data.get("notes") or []),
        int(data.get("families") or 0),
    )


def predictor_for(ctx) -> ProjectPredictor:
    """Memory cache per loaded project, then a file per pull under the project's plugin folder
    (cloud storage mirrors it, so other instances reuse it), then a fresh build."""
    stamp = (getattr(ctx, "pull_id", None), len(getattr(ctx, "pages", {}) or {}))
    cached = _CACHE.get(id(ctx))
    if cached is not None and cached[0] is ctx and cached[2] == stamp:
        return cached[1]
    path = None
    built = None
    try:
        path = _cache_path(ctx)
    except Exception:  # noqa: BLE001 - no folder means no disk cache
        path = None
    if path is not None and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") == CACHE_VERSION:
                built = _load(ctx, data)
        except Exception:  # noqa: BLE001 - a broken cache file is rebuilt
            built = None
    if built is None:
        built = build_predictor(ctx)
        if path is not None:
            try:
                for old in path.parent.glob("predictor-*.json"):
                    if old != path:
                        old.unlink(missing_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(_dump(built)), encoding="utf-8")
                tmp.replace(path)
            except OSError:
                pass
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[id(ctx)] = (ctx, built, stamp)
    return built


def target_searches(ctx, url: str, draft_doc, requested: list[str] | None) -> list[str]:
    """Searches to test. Explicit ones win; otherwise every search the URL ranks for plus every
    search whose words the draft's title or H1 mostly matches; otherwise all searches."""
    searches = list(ctx.project.searches)
    if requested:
        wanted = [search for search in requested if search in searches]
        if not wanted:
            raise PredictError("None of those searches are in this project. Nothing was run.")
        return wanted[:MAX_SEARCHES]
    key = _key(url)
    ranked = []
    for search, entry in ctx.google.by_search().items():
        if any(_key(result.url) == key and 1 <= result.position <= 10 for result in entry.results) and search in searches:
            ranked.append(search)
    head = f"{draft_doc.title} {draft_doc.h1}"
    matched = [search for search in searches if search not in ranked and term_match(search, head) >= 0.6]
    picked = ranked + matched
    return (picked or searches)[:MAX_SEARCHES]


def _uses_search_words(signals: dict[str, float]) -> bool:
    """Off-topic guard for new pages: some search word must appear in the title, H1, subheadings or body."""
    names = ("title_match", "h1_match", "heading_match", "body_match")
    return any((signals.get(name) or 0.0) > 0.0 for name in names if signals.get(name) == signals.get(name))


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 1)


def _slot(value: float | None) -> int | None:
    return None if value is None else max(1, int(round(value)))


def test_searches(
    ctx,
    draft_html: str,
    url: str,
    *,
    searches: list[str] | None = None,
    original_html: str | None = None,
    first: str | None = None,
) -> dict:
    """PREDICTED position now and with the edit for every target search, plus guidance."""
    from aeo.ranksim.loop import page_from_html

    if not (draft_html or "").strip():
        raise PredictError("Write or paste a draft first. Nothing was run.")
    predictor = predictor_for(ctx)
    model = predictor.model
    draft_doc = page_from_html(url, draft_html, is_ours=True)
    original_doc = page_from_html(url, original_html, is_ours=True) if (original_html or "").strip() else None
    key = _key(url)
    own_domain = _domain(url)  # your other pages are not the pages to beat
    out_rows = []
    by_search = ctx.google.by_search()
    picked = target_searches(ctx, url, draft_doc, searches)
    if first and first in ctx.project.searches:
        picked = [first] + [search for search in picked if search != first]
    for search in picked[:MAX_SEARCHES]:
        found = predictor.items.get(search, [])
        mine = next((item for item in found if item.key == key), None)
        others = [item for item in found if item.key != key]
        peers = [item.doc for item in others if item.doc is not None]
        other_signals = [(_item_signals(search, item, predictor.site_rows), item) for item in others]
        after = _signals(search, url, draft_doc, draft_html, peers, predictor.site_rows)
        raw_after = raw_counts(draft_doc, draft_html)
        backtest = predictor.backtest.get(search) or {}
        if predictor.families < 2:
            conf = dict(confidence(None, 0), short=ONE_FAMILY_SHORT, text=ONE_FAMILY_TEXT, one_family=True)
        else:
            conf = confidence(backtest.get("pairwise"), backtest.get("pairs")) if predictor.groups and backtest else confidence(None, 0)
        entry = by_search.get(search)
        seen = entry.pulled_label if entry is not None and hasattr(entry, "pulled_label") else ""
        row = {
            "search": search,
            "label": "PREDICTED",
            "candidates": len(others) + 1,
            "stored_candidates": len(peers),
            "confidence": conf,
            "google_seen": seen,
        }
        if mine is None and not _uses_search_words(after):
            row.update(
                method="not placed",
                method_text="The draft does not use this search's words, so it gets no PREDICTED position here.",
                observed_position=None,
                now=None,
                with_edit=None,
                slot_now=None,
                slot_with_edit=None,
                move=None,
                move_places=None,
                above=[],
                guidance=[],
            )
            row["confidence"] = dict(conf, label=UNSURE, short="Not placed: the page does not use this search's words.")
            out_rows.append(row)
            continue
        if mine is not None:
            if original_doc is not None:
                base_doc, base_has_html = original_doc, True
                before = _signals(search, url, original_doc, original_html, peers, predictor.site_rows)
            else:
                base_doc, base_has_html = mine.doc, mine.has_html
                before = _item_signals(search, mine, predictor.site_rows)
            # Compare like with like: when the current page has no stored HTML, the draft's
            # HTML-only signals (subheadings, structured data) are left out of the move too.
            after_cmp = after if base_has_html else _signals(search, url, draft_doc, None, peers, predictor.site_rows)
            moved = anchored_position(
                model,
                mine.position,
                before,
                after_cmp,
                [(signals, item.position) for signals, item in other_signals],
                persistence_slope=PERSISTENCE_SLOPE,
            )
            row.update(
                method="anchored",
                method_text="Ranks today. Starts from its real position and moves by the edit.",
                observed_position=mine.position,
                now=_round(moved["now"]),
                with_edit=_round(moved["predicted"]),
                slot_now=_slot(moved["now"]),
                slot_with_edit=_slot(moved["predicted"]),
            )
            if base_doc is None:
                row["confidence"] = dict(conf, label=UNSURE, short="Unsure: no stored copy of the current page.", text="No stored copy of the current page, so the move is not measured.")
                row["with_edit"] = row["now"]
                row["slot_with_edit"] = row["slot_now"]
        else:
            placed_after = expected_position(model, after, [signals for signals, _ in other_signals], is_new=True)
            placed_before = None
            if original_doc is not None:
                before = _signals(search, url, original_doc, original_html, peers, predictor.site_rows)
                placed_before = expected_position(model, before, [signals for signals, _ in other_signals], is_new=True)
            row.update(
                method="new page",
                method_text="Not in this search's results today. Placed by the new-page model.",
                observed_position=None,
                now=_round(placed_before),
                with_edit=_round(placed_after),
                slot_now=_slot(placed_before),
                slot_with_edit=_slot(placed_after),
            )
        if row["now"] is not None and row["with_edit"] is not None:
            row["move"] = round(row["now"] - row["with_edit"], 1)  # positive: PREDICTED higher
        else:
            row["move"] = None
        row["move_places"] = _places(row["move"])
        target = row["with_edit"] or 1.0
        above = sorted(
            [(signals, item) for signals, item in other_signals if item.position < target and item.doc is not None and _domain(item.url) != own_domain],
            key=lambda pair: -pair[1].position,
        )[:GUIDE_ABOVE]
        row["above"] = [{"url": item.url, "position": item.position} for _signals_, item in above]
        row["guidance"] = guidance(
            model,
            after,
            [signals for signals, _ in above],
            stable_signs=predictor.stable,
            raw_draft=raw_after,
            raw_above=[item.raw for _signals_, item in above],
        )
        out_rows.append(row)
    notes = list(predictor.notes)
    if out_rows and all(row.get("method") == "not placed" for row in out_rows):
        notes.append("Not placed on any search yet. Use the words of a search in the title and text to get a PREDICTED position.")
    if predictor.families < 2:
        notes = [ONE_FAMILY_TEXT]
    if not any(row[1] == own_domain for row in predictor.site_rows):
        notes.append(
            f"{own_domain} is not in the top 10 for any of these searches yet. Its pages are placed as new pages from a "
            "site with no rankings here, so treat every position as a rough guide."
        )
    return {
        "label": "PREDICTED",
        "url": url,
        "summary": _summary(out_rows),
        "searches": out_rows,
        "guidance_note": "Guidance, not a guarantee. These are the gaps to the pages PREDICTED just above you that this model ties to ranking higher.",
        "forward_check": FORWARD_NOTE,
        "backtest_note": "Backtest: each search scored by a model trained on the other search families in this project, same snapshot. Not accuracy.",
        "model": {"features": model.features, "pairs": model.pairs, "searches": predictor.groups},
        "notes": notes,
    }


def _places(move: float | None) -> int | None:
    """Whole places moved, rounded half away from zero. The table and the summary both use this."""
    if move is None:
        return None
    places = int(abs(move) + 0.5)
    return places if move > 0 else -places


def _summary(rows: list[dict]) -> dict:
    """Counts for the one-line header, from the same whole-place moves the table shows."""
    out = {"searches": len(rows), "up": 0, "down": 0, "same": 0, "not_placed": 0, "unsure": 0, "new": 0}
    for row in rows:
        if row["confidence"]["label"] == UNSURE:
            out["unsure"] += 1
        if row.get("with_edit") is None:
            out["not_placed"] += 1
        elif row.get("move") is None:
            out["new"] += 1
        elif row["move_places"] > 0:
            out["up"] += 1
        elif row["move_places"] < 0:
            out["down"] += 1
        else:
            out["same"] += 1
    return out
