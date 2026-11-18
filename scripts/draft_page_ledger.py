"""Freeze PREDICTED positions for unpublished SmartCrew pages. Does not touch that repo.

    python scripts/draft_page_ledger.py \
        --serp /path/to/serp_serper_live.csv \
        --extract-dir /path/to/draft-text \
        --history /path/to/2026-11-13 \
        --html-dir /path/to/html-snapshot \
        --out evals/draft-ledger/smartcrew-pr41.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from aeo.ranksim.consensus import median_position
from aeo.ranksim.draft_ledger import (
    CORE_UPDATE_NOTE,
    CORE_UPDATE_THROUGH,
    missing_queries,
    prediction_sha256,
    score_dates,
    text_sha256,
)
from aeo.ranksim.pair_order import (
    PageView,
    Snap,
    SnapRow,
    build_transition,
    load_model,
    load_offpage_cache,
    load_pages,
    load_serp_snapshot,
)
from aeo.ranksim.position_metrics import clip_range, ranks_from_scores
from aeo.ranksim.site_history import site_key, url_key

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.score_consensus import _read_serp  # noqa: E402
HEAD = "cbb50dab37cd1d7e6ae0d6c61ef3c1111436263a"
HOST = "https://www.smartcrewhq.com"
MODEL = ROOT / "evals/preregistered/forward-pair-2026-11-16/model-heldout-nov16.json"
CACHE = ROOT / "evals/preregistered/forward-pair-2026-11-16/offpage.tsv"

# Target searches from context/search-map.md at HEAD, for the pages in PR 41.
# The owner URL in that map is the page. Shopping variants named on the same row are included.
PAGES = [
    {
        "slug": "compare/workyard-alternative",
        "kind": "new",
        "queries": ["workyard alternative", "workyard", "workyard pricing", "workyard reviews", "workyard competitors", "workyard vs smartcrew"],
    },
    {
        "slug": "compare/agtek-alternative",
        "kind": "new",
        "queries": ["agtek alternative", "agtek", "agtek pricing"],
    },
    {
        "slug": "compare/trimble-viewpoint-alternative",
        "kind": "new",
        "queries": ["trimble viewpoint alternative", "trimble viewpoint", "trimble viewpoint pricing"],
    },
    {
        "slug": "compare/assignar-alternative",
        "kind": "new",
        "queries": ["assignar alternative", "assignar", "assignar pricing", "assignar competitors"],
    },
    {
        "slug": "compare/busybusy-alternative",
        "kind": "rewrite",
        "queries": ["busybusy alternative", "busybusy", "busybusy pricing", "busybusy reviews", "busybusy competitors"],
    },
    {
        "slug": "compare/hcss-alternative",
        "kind": "rewrite",
        "queries": ["hcss alternative", "hcss", "hcss pricing", "hcss reviews"],
    },
    {
        "slug": "compare/hcss-heavyjob-alternative",
        "kind": "rewrite",
        "queries": ["heavy job", "hcss heavyjob alternative", "heavyjob alternative", "hcss heavyjob pricing", "hcss heavyjob reviews"],
    },
    {
        "slug": "compare/hcss-heavybid-alternative",
        "kind": "rewrite",
        "queries": ["construction estimating software", "heavybid", "hcss heavybid alternative", "heavybid alternative", "hcss heavybid pricing", "hcss heavybid reviews"],
    },
    {
        "slug": "compare/raken-alternative",
        "kind": "rewrite",
        "queries": ["raken alternative", "raken", "raken pricing", "raken reviews", "raken competitors"],
    },
    {
        "slug": "compare/b2w-alternative",
        "kind": "rewrite",
        "queries": ["b2w alternative", "b2w", "b2w pricing", "b2w reviews"],
    },
    {
        "slug": "compare/quoteiq-alternative",
        "kind": "rewrite",
        "queries": ["quoteiq alternative", "quoteiq", "quoteiq pricing", "quoteiq reviews"],
    },
    {
        "slug": "features/construction-time-tracking",
        "kind": "rewrite",
        "queries": [
            "construction time tracking",
            "construction time tracking software",
            "time clock app for construction",
            "time tracking app for contractors",
            "construction time tracking app",
        ],
    },
    {
        "slug": "features/crew-scheduling-software",
        "kind": "rewrite",
        "queries": ["construction scheduling software", "crew scheduling software"],
    },
    {
        "slug": "features/heavy-equipment-maintenance-software",
        "kind": "rewrite",
        "queries": [
            "equipment maintenance software",
            "construction equipment maintenance software",
            "heavy equipment maintenance software",
            "heavy equipment fleet management software",
        ],
    },
    {
        "slug": "earthwork-software",
        "kind": "rewrite",
        "queries": ["earthwork software", "construction takeoff software"],
    },
    {
        "slug": "all-in-one-dirt-work-software",
        "kind": "rewrite",
        "queries": [
            "construction management software",
            "project management software for construction",
            "construction management app",
            "all in one dirt work software",
            "best all in one dirt work software",
            "best all in one heavy civil software",
            "best construction software for dirtwork",
        ],
    },
    {
        "slug": "compare/clockshark-alternative",
        "kind": "rewrite",
        # The search map does not give ClockShark a volume row. These are the page keyword
        # and the same shopping variants the other compare pages own.
        "queries": ["clockshark alternative", "clockshark", "clockshark pricing", "clockshark reviews", "clockshark competitors"],
    },
]


def _all_ranks(path: Path | None) -> dict[str, dict[str, int]]:
    if path is None:
        return {}
    found: dict[str, dict[str, int]] = {}
    table = _read_serp(path)
    for query, rows in table.items():
        found[query] = {key: position for key, (_url, position) in rows.items()}
    return found


def _page_url(slug: str) -> str:
    return f"{HOST}/{slug.strip('/')}/"


def _view(payload: dict) -> PageView:
    body = payload.get("body") or ""
    return PageView(
        title=payload.get("title") or "",
        h1=payload.get("h1") or "",
        headings=payload.get("headings") or "",
        body=body,
        word_count=int(payload.get("word_count") or len(body.split())),
        present=True,
    )


def _before(url: str, query: str, pulls: list[dict], deep: dict) -> dict:
    key = url_key(url)
    deep_rank = deep.get(query, {}).get(key)
    pulls_with_query = [pull for pull in pulls if query in pull]
    per_pull = []
    for pull in pulls_with_query:
        found = pull[query].get(key)
        per_pull.append(found[1] if found else None)
    shown = [position for position in per_pull if position is not None]
    if len(pulls_with_query) >= 2 and len(pulls_with_query) == len(pulls):
        ranks = []
        for position in per_pull:
            if position is not None:
                ranks.append(position)
            elif deep_rank is not None and deep_rank >= 11:
                ranks.append(deep_rank)
            else:
                ranks.append(11)
        return {
            "before_position": median_position(ranks) if shown or (deep_rank is not None and deep_rank >= 11) else None,
            "before_shown": shown,
            "before_pulls": len(pulls_with_query),
            "before_deep": deep_rank,
            "before_source": "three_pull_median" if shown or (deep_rank is not None and deep_rank >= 11) else "absent_from_top10",
        }
    if shown:
        return {
            "before_position": shown[0],
            "before_shown": shown,
            "before_pulls": len(pulls_with_query),
            "before_deep": deep_rank,
            "before_source": "q100_top10",
        }
    if deep_rank is not None:
        return {
            "before_position": deep_rank,
            "before_shown": [],
            "before_pulls": len(pulls_with_query),
            "before_deep": deep_rank,
            "before_source": "deep_rank",
        }
    return {
        "before_position": None,
        "before_shown": [],
        "before_pulls": len(pulls_with_query),
        "before_deep": None,
        "before_source": "absent",
    }


def _predict(query: str, url: str, view: PageView, serp: dict, history, pages, model, offpage, seen_pairs: set[tuple[str, str]]) -> dict:
    candidates = list(serp.get(query, {}).items())
    draft_key = url_key(url)
    injected = draft_key not in {key for key, _item in candidates}
    if injected:
        candidates.append((draft_key, (url, len(candidates) + 1)))
    outcome_rows = []
    for key, (link, _position) in candidates:
        outcome_rows.append(
            SnapRow(query=query, url=link, position=1.0, title="", url_key=key, site=site_key(link))
        )
    outcome = Snap(day="2099-01-01", rows=outcome_rows)
    page_map = dict(pages)
    page_map[draft_key] = view
    built = build_transition(history, outcome, page_map, offpage)
    scores = model.predict(built)
    ranks = ranks_from_scores(built, scores)
    match = [index for index, row in enumerate(built) if row.url_key == draft_key]
    if len(match) != 1:
        raise SystemExit(f"Draft row missing for {query} {url}")
    position = ranks[match[0]]
    survived = (query, draft_key) in seen_pairs
    if position > 10:
        low = high = None
    else:
        low_offset, high_offset = (-1, 1) if survived else (-2, 0)
        low, high, _width = clip_range(position, low_offset, high_offset)
    return {
        "predicted_position": position,
        "range_low": low,
        "range_high": high,
        "candidate_count": len(candidates),
        "injected": injected,
        "history": "surviving" if survived else "new",
        "range_rule": "surviving -1..+1" if survived else "new -2..0",
    }


def _before_cell(entry: dict) -> str:
    if entry["kind"] == "new" and entry.get("before_position") is None:
        return "new"
    shown = entry.get("before_shown") or []
    deep = entry.get("before_deep")
    position = entry.get("before_position")
    if shown:
        text = ", ".join(str(item) for item in shown)
        pulls = entry.get("before_pulls") or len(shown)
        if pulls > len(shown):
            text = f"{text} in {len(shown)} of {pulls}"
        if position is not None and position not in shown:
            text = f"{text}; median {position}"
    elif position is not None:
        text = str(position)
    else:
        text = "not in the pulled top 10s"
    if deep is not None and deep not in shown:
        text = f"{text}; deep {deep}"
    return text


def _candidate_tables(specs: list[str]) -> list[tuple[str, dict]]:
    """Each spec is serp.csv|ISO-time. Later times win when a query is in more than one file."""
    ranked = []
    for spec in specs:
        path_text, _, stamp = spec.partition("|")
        path = Path(path_text)
        if not stamp:
            raise SystemExit(f"--candidate needs path|ISO-time, got {spec}")
        ranked.append((stamp, path, _read_serp(path)))
    ranked.sort()
    return [(stamp, table) for stamp, _path, table in ranked]


def _refreeze_head(args: argparse.Namespace) -> int:
    """Append rows for a new source head. Existing ledger lines are not rewritten."""
    if not args.out.is_file():
        raise SystemExit(f"No ledger to append to: {args.out}")
    if not args.candidate:
        raise SystemExit("--refreeze-head needs at least one --candidate")
    original = args.out.read_text(encoding="utf-8")
    history = [load_serp_snapshot(path) for path in args.history]
    pages = load_pages(args.html_dir) if args.html_dir is not None else {}
    for spec in args.candidate:
        sibling = Path(spec.partition("|")[0]).parent
        if (sibling / "html_manifest.csv").is_file():
            try:
                pages.update(load_pages(sibling))
            except ValueError:
                continue
    offpage = load_offpage_cache(args.offpage_cache)
    model = load_model(args.model)
    model_sha = hashlib.sha256(args.model.read_bytes()).hexdigest()
    if model_sha != "c4619100d0f9db5d4dbeb563e416284631f76589cd1b4f428a60e8fb88de30f3":
        raise SystemExit(f"Refusing to freeze against a different model hash: {model_sha}")
    seen_pairs = {(row.query, row.url_key) for snap in history for row in snap.rows}
    stamped = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    publish = datetime.fromisoformat(args.publish)
    dates = score_dates(publish.date())
    tables = _candidate_tables(args.candidate)
    deep = _all_ranks(args.deep)
    added = []
    for page in PAGES:
        extract_path = args.extract_dir / f"{page['slug']}.json"
        if not extract_path.is_file():
            raise SystemExit(f"Missing extract {extract_path}")
        payload = json.loads(extract_path.read_text(encoding="utf-8"))
        view = _view(payload)
        digest = text_sha256(view.title, view.h1, view.headings, view.body)
        url = _page_url(page["slug"])
        for query in page["queries"]:
            chosen = None
            for stamp, table in tables:
                if query in table:
                    chosen = (stamp, table)
            base = {
                "label": "PREDICTED",
                "timestamp": stamped,
                "source_repo": "joe51111jwd/Smart-Crew-Website",
                "source_head": args.refreeze_head,
                "source_pr": 41,
                "url": url,
                "kind": page["kind"],
                "query": query,
                "text_sha256": digest,
                "model_sha256": model_sha,
                "model_path": "evals/preregistered/forward-pair-2026-11-16/model-heldout-nov16.json",
                "core_update_through": CORE_UPDATE_THROUGH,
                "core_update_note": CORE_UPDATE_NOTE,
                "publish_date": args.publish,
                "publish_note": "About 10 minutes after the 9:57 PM ET merge on Nov 17. Not indexed yet.",
                "score_at": dates,
            }
            if chosen is None:
                added.append({**base, "status": "no_baseline", "predicted_position": None, "range_low": None, "range_high": None, "before_position": None})
                continue
            stamp, table = chosen
            predicted = _predict(query, url, view, table, history, pages, model, offpage, seen_pairs)
            before = _before(url, query, [table], deep)
            if page["kind"] == "new" and before["before_position"] is None:
                before = {"before_position": None, "before_shown": [], "before_pulls": 1, "before_deep": None, "before_source": "new_url"}
            bound = prediction_sha256(
                source_head=args.refreeze_head,
                model_sha256=model_sha,
                url=url,
                query=query,
                position=predicted["predicted_position"],
                low=predicted["range_low"] if predicted["range_low"] is not None else 0,
                high=predicted["range_high"] if predicted["range_high"] is not None else 0,
                text_sha256=digest,
            )
            added.append(
                {
                    **base,
                    "status": "frozen",
                    "prediction_sha256": bound,
                    "candidate_set": f"latest available pull at {stamp}",
                    **predicted,
                    **before,
                }
            )
    with args.out.open("a", encoding="utf-8") as handle:
        for entry in added:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if args.out.read_text(encoding="utf-8")[: len(original)] != original:
        raise SystemExit("Refreeze rewrote earlier ledger bytes")
    summary = args.out.with_suffix(".md")
    lines = [
        "",
        f"## Freeze at merge, head `{args.refreeze_head}`",
        "",
        "Label: PREDICTED. The cbb50da rows above are unchanged.",
        f"Frozen at {stamped}. Publish time about {args.publish}. Not indexed yet.",
        f"Score dates: publish+7 {dates['plus_7']}, publish+14 {dates['plus_14']}, publish+28 {dates['plus_28']}.",
        CORE_UPDATE_NOTE,
        "Candidate set for each search is the latest pull that contains it. Nov 17 pulls were not on disk, so these are Nov 16 pulls.",
        "ClockShark is included because that compare page was rewritten at this head. The search map does not give it a volume row; the targets are its keyword and the same shopping variants as the other compare pages.",
        "",
        "| Page | Kind | Query | Before | PREDICTED | Range | Candidate |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for entry in added:
        if entry["status"] != "frozen":
            continue
        before = _before_cell(entry)
        span = "behind top 10" if entry["range_low"] is None else f"{entry['range_low']}-{entry['range_high']}"
        lines.append(
            f"| `{entry['url'].replace(HOST, '')}` | {entry['kind']} | {entry['query']} | {before} | {entry['predicted_position']} | {span} | {entry['candidate_set'].replace('latest available pull at ', '')} |"
        )
    missing = [entry for entry in added if entry["status"] != "frozen"]
    if missing:
        lines.extend(["", "No pull contains these searches, so no position was frozen:", ""])
        for entry in missing:
            lines.append(f"- `{entry['query']}` → {entry['url'].replace(HOST, '')}")
    with summary.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"Appended {len(added)} rows at {stamped} for {args.refreeze_head}")
    return 0


def _append_baseline(args: argparse.Namespace) -> int:
    """Score queries that were waiting on a baseline. Earlier frozen rows stay as they were."""
    if not args.out.is_file():
        raise SystemExit(f"No ledger to append to: {args.out}")
    entries = [json.loads(line) for line in args.out.read_text(encoding="utf-8").splitlines() if line.strip()]
    pending = [entry for entry in entries if entry.get("status") == "no_baseline"]
    serp = _read_serp(args.append_baseline)
    history = [load_serp_snapshot(path) for path in args.history]
    pages = load_pages(args.html_dir)
    offpage = load_offpage_cache(args.offpage_cache)
    model = load_model(args.model)
    model_sha = hashlib.sha256(args.model.read_bytes()).hexdigest()
    if model_sha != "c4619100d0f9db5d4dbeb563e416284631f76589cd1b4f428a60e8fb88de30f3":
        raise SystemExit(f"Refusing to freeze against a different model hash: {model_sha}")
    seen_pairs = {(row.query, row.url_key) for snap in history for row in snap.rows}
    stamped = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    by_query = {entry["query"]: entry for entry in pending}
    added = []
    for page in PAGES:
        extract_path = args.extract_dir / f"{page['slug']}.json"
        payload = json.loads(extract_path.read_text(encoding="utf-8"))
        view = _view(payload)
        digest = text_sha256(view.title, view.h1, view.headings, view.body)
        url = _page_url(page["slug"])
        for query in page["queries"]:
            if query not in by_query or query not in serp:
                continue
            predicted = _predict(query, url, view, serp, history, pages, model, offpage, seen_pairs)
            before = _before(url, query, [serp], {})
            if page["kind"] == "new" and before["before_position"] is None:
                before = {
                    "before_position": None,
                    "before_shown": [],
                    "before_pulls": 1,
                    "before_deep": None,
                    "before_source": "new_url",
                }
            bound = prediction_sha256(
                source_head=HEAD,
                model_sha256=model_sha,
                url=url,
                query=query,
                position=predicted["predicted_position"],
                low=predicted["range_low"] if predicted["range_low"] is not None else 0,
                high=predicted["range_high"] if predicted["range_high"] is not None else 0,
                text_sha256=digest,
            )
            entry = {
                "label": "PREDICTED",
                "status": "frozen",
                "timestamp": stamped,
                "source_repo": "joe51111jwd/Smart-Crew-Website",
                "source_head": HEAD,
                "source_pr": 41,
                "url": url,
                "kind": page["kind"],
                "query": query,
                "text_sha256": digest,
                "model_sha256": model_sha,
                "model_path": "evals/preregistered/forward-pair-2026-11-16/model-heldout-nov16.json",
                "prediction_sha256": bound,
                "candidate_set": "2026-11-16 20:16 ET PR41 baseline top 10, draft injected when absent",
                **predicted,
                **before,
                "core_update_through": CORE_UPDATE_THROUGH,
                "core_update_note": CORE_UPDATE_NOTE,
                "score_at": "publish+7, publish+14, publish+28",
                "publish_date": None,
            }
            added.append(entry)
    if len(added) != len(pending):
        missing = sorted(set(entry["query"] for entry in pending) - set(entry["query"] for entry in added))
        raise SystemExit(f"Baseline did not cover every open query: {missing}")
    added_by_query = {entry["query"]: entry for entry in added}
    merged = [added_by_query.get(entry["query"], entry) if entry.get("status") == "no_baseline" else entry for entry in entries]
    # A no_baseline row is replaced only when the query matches. Keep frozen rows byte-for-byte via the original dict.
    with args.out.open("w", encoding="utf-8") as handle:
        for entry in merged:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    summary = args.out.with_suffix(".md")
    text = summary.read_text(encoding="utf-8")
    marker = "## Target searches with no baseline in the 100-query set"
    if marker not in text:
        raise SystemExit(f"{summary} has no section to update")
    head, _tail = text.split(marker, 1)
    lines = [
        marker,
        "",
        "Scored from the Nov 16 20:16 ET baseline pull. The table is below.",
        "",
        "## Baseline pull, 2026-11-16 20:16 ET",
        "",
        f"Label: PREDICTED. Frozen at {stamped}. Same model and SmartCrew head as the rows above.",
        "One pull, 35 searches. The candidate set is that pull's top 10, with the draft text injected.",
        "None of the 16 target URLs were in these top 10s. A different SmartCrew URL, the pricing blog, was at position 6 for `workyard vs smartcrew`.",
        "",
        "| Page | Kind | Query | Before | PREDICTED | Range |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for entry in added:
        before = _before_cell(entry)
        if entry["range_low"] is None:
            span = "behind top 10"
        else:
            span = f"{entry['range_low']}-{entry['range_high']}"
        lines.append(
            f"| `{entry['url'].replace(HOST, '')}` | {entry['kind']} | {entry['query']} | {before} | {entry['predicted_position']} | {span} |"
        )
    summary.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"Froze {len(added)} baseline rows at {stamped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PREDICTED draft-page ledger. Does not refit.")
    parser.add_argument("--serp", type=Path, help="Top-10 file for the original 100-query ledger.")
    parser.add_argument(
        "--append-baseline",
        type=Path,
        help="A later top-10 file for queries that were left as no_baseline. Appends those rows and leaves earlier freezes unchanged.",
    )
    parser.add_argument("--pull", action="append", type=Path, default=[])
    parser.add_argument("--deep", type=Path)
    parser.add_argument("--extract-dir", type=Path, required=True)
    parser.add_argument("--history", action="append", type=Path, required=True)
    parser.add_argument("--html-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=MODEL)
    parser.add_argument("--offpage-cache", type=Path, default=CACHE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--refreeze-head", default="", help="Append a new freeze for this source head. Earlier rows stay byte-for-byte.")
    parser.add_argument("--publish", default="2026-11-18T03:07:00+00:00")
    parser.add_argument("--candidate", action="append", default=[], help="serp.csv|ISO-time. The latest time that contains a query is its candidate set.")
    args = parser.parse_args(argv)
    if args.refreeze_head:
        return _refreeze_head(args)
    if args.append_baseline:
        return _append_baseline(args)
    if args.serp is None:
        raise SystemExit("--serp is required unless --append-baseline is set")
    serp = _read_serp(args.serp)
    pulls = [serp] + [_read_serp(path) for path in args.pull]
    deep = _all_ranks(args.deep)
    tracked = sorted(serp)
    history = [load_serp_snapshot(path) for path in args.history]
    pages = load_pages(args.html_dir)
    offpage = load_offpage_cache(args.offpage_cache)
    model = load_model(args.model)
    model_sha = hashlib.sha256(args.model.read_bytes()).hexdigest()
    seen_pairs = {(row.query, row.url_key) for snap in history for row in snap.rows}
    stamped = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    entries = []
    missing_rows = []
    for page in PAGES:
        extract_path = args.extract_dir / f"{page['slug']}.json"
        if not extract_path.is_file():
            raise SystemExit(f"Missing extract {extract_path}")
        payload = json.loads(extract_path.read_text(encoding="utf-8"))
        view = _view(payload)
        digest = text_sha256(view.title, view.h1, view.headings, view.body)
        url = _page_url(page["slug"])
        absent = missing_queries(page["queries"], tracked)
        for query in absent:
            missing_rows.append({"url": url, "kind": page["kind"], "query": query})
        for query in page["queries"]:
            if query in absent:
                entries.append(
                    {
                        "label": "PREDICTED",
                        "status": "no_baseline",
                        "timestamp": stamped,
                        "source_repo": "joe51111jwd/Smart-Crew-Website",
                        "source_head": HEAD,
                        "source_pr": 41,
                        "url": url,
                        "kind": page["kind"],
                        "query": query,
                        "text_sha256": digest,
                        "model_sha256": model_sha,
                        "predicted_position": None,
                        "range_low": None,
                        "range_high": None,
                        "before_position": None,
                        "core_update_through": CORE_UPDATE_THROUGH,
                        "core_update_note": CORE_UPDATE_NOTE,
                        "score_at": "publish+7, publish+14, publish+28",
                        "publish_date": None,
                    }
                )
                continue
            predicted = _predict(query, url, view, serp, history, pages, model, offpage, seen_pairs)
            before = _before(url, query, pulls, deep) if page["kind"] == "rewrite" else {
                "before_position": None,
                "before_shown": [],
                "before_deep": None,
                "before_source": "new_url",
            }
            bound = prediction_sha256(
                source_head=HEAD,
                model_sha256=model_sha,
                url=url,
                query=query,
                position=predicted["predicted_position"],
                low=predicted["range_low"] if predicted["range_low"] is not None else 0,
                high=predicted["range_high"] if predicted["range_high"] is not None else 0,
                text_sha256=digest,
            )
            entries.append(
                {
                    "label": "PREDICTED",
                    "status": "frozen",
                    "timestamp": stamped,
                    "source_repo": "joe51111jwd/Smart-Crew-Website",
                    "source_head": HEAD,
                    "source_pr": 41,
                    "url": url,
                    "kind": page["kind"],
                    "query": query,
                    "text_sha256": digest,
                    "model_sha256": model_sha,
                    "model_path": "evals/preregistered/forward-pair-2026-11-16/model-heldout-nov16.json",
                    "prediction_sha256": bound,
                    "candidate_set": "2026-11-16 16:26 ET q100 top 10, draft injected when absent",
                    **predicted,
                    **before,
                    "core_update_through": CORE_UPDATE_THROUGH,
                    "core_update_note": CORE_UPDATE_NOTE,
                    "score_at": "publish+7, publish+14, publish+28",
                    "publish_date": None,
                }
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    summary = args.out.with_suffix(".md")
    lines = [
        "# SmartCrew PR 41 draft ledger",
        "",
        "Label: PREDICTED. PR 41 is not merged and has no publish date.",
        f"Source head: `{HEAD}`.",
        f"Frozen at {stamped}.",
        f"Model: `model-heldout-nov16.json` `{model_sha}` (fit on single pulls through Nov 13, not on the Nov 16 consensus).",
        "",
        CORE_UPDATE_NOTE,
        "",
        "The candidate set for a frozen prediction is the Nov 16 16:26 ET top 10. The draft is added when that URL is not already there, and its text replaces the live HTML when it is. A rank above 10 means the model places the draft behind that top 10, so no 1-10 range is frozen.",
        "",
        "Rewrites record the current live position. New URLs have no before position.",
        "",
        "## Frozen predictions",
        "",
        "| Page | Kind | Query | Before | PREDICTED | Range |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for entry in entries:
        if entry["status"] != "frozen":
            continue
        before = _before_cell(entry)
        if entry["range_low"] is None:
            span = "behind top 10"
        else:
            span = f"{entry['range_low']}-{entry['range_high']}"
        lines.append(
            f"| `{entry['url'].replace(HOST, '')}` | {entry['kind']} | {entry['query']} | {before} | {entry['predicted_position']} | {span} |"
        )
    lines.extend(["", "## Target searches with no baseline in the 100-query set", ""])
    if not missing_rows:
        lines.append("None.")
    else:
        lines.append("These need a pull before merge. No position was frozen for them.")
        lines.append("")
        for row in missing_rows:
            lines.append(f"- `{row['query']}` → {row['url'].replace(HOST, '')} ({row['kind']})")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} ledger rows to {args.out}")
    print(f"Missing baselines: {len(missing_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
