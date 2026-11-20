"""SmartCrew PR 45: PREDICTED direction per (target search, edited page) from before vs after HTML.

Run through ``python scripts/claude/r3_pr45.py predict``. Reads only:
  - the before-HTML saved by the owner at 6:58 PM ET on 2026-11-19,
  - the after-HTML saved by scripts/claude/r3_pr45.py at 8:09 PM ET the same day,
  - the frozen round 3 scorer (evals/claude/r3/model/page-blend), whose manifest must verify.
No search result is read. Nothing dated after the merge except the page HTML itself.

For each target search, the 15 before copies and the 15 after copies are scored
as one list of 30 pages. The scorer's two parts (logistic regression, LambdaMART)
are each standardised inside that list and averaged, as in the held-out test.
  delta = score(after copy) minus score(before copy), in list standard deviations.
PREDICTED direction, fixed here before any outcome exists:
  up        delta >= +0.10 and both parts moved up
  down      delta <= -0.10 and both parts moved down
  no call   anything else
"Up" means: for this search the edited page fits better than the page it replaced.
It is a statement about content fit. Round 3 theory section R3.7 says why that is
an upper bound on what the rank will do. A row is "in play" when the before copy is
one of the site's three best-scoring pages for the search; other rows are pages
that should not rank for that search either way.

Output: evals/claude/r3/pr45/ledger.json and ledger.md, with a sha256 of the ledger.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import r3_dataset, r3_model as R  # noqa: E402
from scripts.claude.freeze import verify_manifest  # noqa: E402
from scripts.claude.r3_pr45 import AFTER_DIR, BASE, BEFORE_DIR, OUT, SEARCHES, before_manifest  # noqa: E402
from scripts.claude.t2_changes import extract_page  # noqa: E402

PREREG = REPO / "evals" / "preregistered" / "claude-r3-samesite"
MODEL_DIR = REPO / "evals" / "claude" / "r3" / "model" / "page-blend"
CALL_DELTA = 0.10
IN_PLAY_TOP = 3


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    verify_manifest(PREREG)
    if (OUT / "ledger.json").exists():
        raise SystemExit("The PR 45 ledger exists. It is not overwritten.")
    scorer = R.FrozenScorer.load(MODEL_DIR)
    df_table = json.loads((MODEL_DIR / "df_table.json").read_text(encoding="utf-8"))
    items = []
    page_info = {}
    for path, name in before_manifest():
        url = BASE + path
        before = extract_page((BEFORE_DIR / name).read_text(encoding="utf-8", errors="replace"), url)
        after = extract_page((AFTER_DIR / name).read_text(encoding="utf-8", errors="replace"), url)
        items.append((f"{path}|before", url, before))
        items.append((f"{path}|after", url, after))
        page_info[path] = {
            "before_sha256": sha256_file(BEFORE_DIR / name),
            "after_sha256": sha256_file(AFTER_DIR / name),
            "words_before": int(before["word_count"]),
            "words_after": int(after["word_count"]),
            "title_changed": before["title"] != after["title"],
            "h1_changed": before["h1"] != after["h1"],
            "main_text_changed": before["main_text"] != after["main_text"],
        }
    rows = []
    for search in SEARCHES:
        table = r3_dataset.table_for_pages(search, items, df_table, list_id=search)
        parts = scorer.score_parts(table)
        z = {name: R.zscore_in_list(table, values) for name, values in parts.items()}
        blend = sum(z.values()) / len(z)
        by_key = {key: index for index, key in zip(table.index, table["key"], strict=True)}
        before_scores = {path: float(blend.loc[by_key[f"{path}|before"]]) for path, _name in before_manifest()}
        order = sorted(before_scores, key=before_scores.get, reverse=True)
        for path, _name in before_manifest():
            b, a = by_key[f"{path}|before"], by_key[f"{path}|after"]
            delta = float(blend.loc[a] - blend.loc[b])
            part_delta = {name: float(values.loc[a] - values.loc[b]) for name, values in z.items()}
            if delta >= CALL_DELTA and all(value > 0 for value in part_delta.values()):
                call = "up"
            elif delta <= -CALL_DELTA and all(value < 0 for value in part_delta.values()):
                call = "down"
            else:
                call = "no call"
            rows.append(
                {
                    "search": search,
                    "page": path,
                    "label": "PREDICTED",
                    "score_before": float(blend.loc[b]),
                    "score_after": float(blend.loc[a]),
                    "delta": delta,
                    "delta_parts": part_delta,
                    "direction": call,
                    "site_rank_before": order.index(path) + 1,
                    "in_play": order.index(path) < IN_PLAY_TOP,
                }
            )
    ledger = {
        "label": "PREDICTED",
        "edit": "SmartCrew PR 45, live by 20:09 ET on 2026-11-19",
        "scorer": "evals/claude/r3/model/page-blend",
        "scorer_spec_sha256": sha256_file(MODEL_DIR / "spec.json"),
        "rule": {"call_delta": CALL_DELTA, "both_parts_must_agree": True, "in_play_top": IN_PLAY_TOP},
        "searches": SEARCHES,
        "pages": page_info,
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    (OUT / "ledger.json").write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (OUT / "ledger.sha256").write_text(f"{digest}  ledger.json\n", encoding="utf-8")
    frame = pd.DataFrame(rows)
    lines = [
        "# SmartCrew PR 45: PREDICTED content-fit direction per (search, page)",
        "",
        "Frozen round 3 scorer on before vs after HTML. No search result was read. A later confirmation, not part of the round 3 test.",
        f"Ledger sha256 {digest}.",
        "",
        f"Calls: {frame['direction'].value_counts().to_dict()}. In play (before copy among the site's three best for the search): {int(frame['in_play'].sum())} rows, calls {frame[frame['in_play']]['direction'].value_counts().to_dict()}.",
        "",
        "| Search | Page | Site rank before | Delta | Direction (PREDICTED) |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in frame[frame["in_play"]].sort_values(["search", "site_rank_before"]).itertuples(index=False):
        lines.append(f"| {row.search} | {row.page} | {row.site_rank_before} | {row.delta:+.3f} | {row.direction} |")
    (OUT / "ledger.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:8]))
    print(f"rows={len(rows)} in_play={int(frame['in_play'].sum())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
