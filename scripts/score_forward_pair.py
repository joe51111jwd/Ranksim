"""Score a later top 10 from earlier snapshots. Does not refit. Label: PREDICTED.

    python scripts/score_forward_pair.py \
        --history /path/ranksim-live-snapshot-2026-11-13 \
        --snapshot /path/ranksim-live-snapshot-NEXT \
        --offpage-cache evals/preregistered/forward-pair-2026-11-16/offpage.tsv

History may be repeated. The last history directory is the previous top 10.
Pass --tranco, --majestic, and --cc to look up domains that are not in the cache.
A page with no HTML is still scored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from aeo.ranksim.pair_order import (
    build_transition,
    evaluate_scores,
    fit_experiment,
    load_model,
    load_offpage,
    load_offpage_cache,
    load_pages,
    load_serp_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "evals/preregistered/forward-pair-2026-11-16/model-next.json"
DEFAULT_CACHE = ROOT / "evals/preregistered/forward-pair-2026-11-16/offpage.tsv"


def _pair(block: dict) -> str:
    pairs = int(block["pairs"])
    if pairs == 0:
        return "n/a"
    return f"{block['correct']}/{pairs} ({block['correct'] / pairs:.6f}), {block['wrong']} wrong, {block['ties']} ties"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PREDICTED pairwise order for a later Serper snapshot.")
    parser.add_argument("--history", action="append", required=True, type=Path, help="Earlier snapshot directory. Repeat in any order.")
    parser.add_argument("--snapshot", required=True, type=Path, help="Outcome snapshot directory. Positions are labels.")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--offpage-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--tranco", type=Path)
    parser.add_argument("--majestic", type=Path)
    parser.add_argument("--cc", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    if not args.model.is_file():
        raise SystemExit(f"Missing frozen model {args.model}")
    manifest = args.model.parent / "manifest.json"
    if manifest.is_file() and args.model.name in {"model-next.json", "model-heldout-nov13.json", "model-heldout-nov16.json"}:
        listed = json.loads(manifest.read_text(encoding="utf-8")).get("sha256") or {}
        expected = listed.get(args.model.name)
        digest = hashlib.sha256(args.model.read_bytes()).hexdigest()
        if expected and digest != expected:
            raise SystemExit(f"{args.model.name} sha256 {digest} does not match the frozen manifest")
    history = sorted((load_serp_snapshot(path) for path in args.history), key=lambda snap: snap.day)
    outcome = load_serp_snapshot(args.snapshot)
    if any(snap.day >= outcome.day for snap in history):
        raise SystemExit("Every --history day must be earlier than --snapshot")
    pages = load_pages(outcome.directory or args.snapshot)
    if args.offpage_cache.is_file() and not (args.tranco or args.majestic or args.cc):
        offpage = load_offpage_cache(args.offpage_cache)
    else:
        offpage = load_offpage(args.tranco, args.majestic, args.cc)
        if args.offpage_cache.is_file():
            cached = load_offpage_cache(args.offpage_cache)
            for name in ("tranco", "majestic", "cc_pagerank", "cc_harmonic"):
                merged = dict(getattr(cached, name))
                merged.update(getattr(offpage, name))
                setattr(offpage, name, merged)
    rows = build_transition(history, outcome, pages, offpage)
    model = load_model(args.model)
    scores = model.predict(rows)
    # The persistence line is the baseline inside the same candidate set. It is not a second fit.
    persistence_scores = fit_experiment("persistence_ranked_last", rows).predict(rows)
    predicted = evaluate_scores(rows, scores)
    persistence = evaluate_scores(rows, persistence_scores)
    missing = sum(1 for row in rows if row.features.get("content_present", 0.0) == 0.0)
    print("Label: PREDICTED. Outcome positions were not features.")
    print(f"Model: {args.model}")
    print(f"History: {', '.join(snap.day for snap in history)}")
    print(f"Outcome: {outcome.day}. Rows {len(rows)}. HTML missing {missing}.")
    print(f"All pairs: {_pair(predicted['blocks']['all'])}")
    print(f"New vs new: {_pair(predicted['blocks']['new_vs_new'])}")
    print(f"New vs surviving: {_pair(predicted['blocks']['new_vs_surviving'])}")
    print(f"Surviving vs surviving: {_pair(predicted['blocks']['surviving_surviving'])}")
    ndcg = predicted["ndcg_at_10"]
    rho = predicted["spearman"]
    print(f"NDCG@10: {'n/a' if ndcg is None else f'{ndcg:.6f}'}. Spearman: {'n/a' if rho is None else f'{rho:.6f}'}.")
    print(f"Persistence-ranked-last: {_pair(persistence['blocks']['all'])}")
    if args.json_out:
        payload = {
            "label": "PREDICTED",
            "outcome_day": outcome.day,
            "history_days": [snap.day for snap in history],
            "rows": len(rows),
            "html_missing": missing,
            "predicted": predicted["blocks"],
            "ndcg_at_10": predicted["ndcg_at_10"],
            "spearman": predicted["spearman"],
            "persistence_ranked_last": persistence["blocks"],
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
