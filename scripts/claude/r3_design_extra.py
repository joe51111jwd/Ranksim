"""Round 3 design, two extra studies on training lists only (numbers the theory cites).

  python scripts/claude/r3_design_extra.py

A. Other ways to read "topic match": two cross-encoders and one embedding model, each on
   six page texts (title alone, H1 alone, URL words, title plus H1 plus meta, headings,
   intro), nothing fitted, plus three unfitted mixes. Shows the plateau of the signal.
B. Label noise against the scorer: the training lists that were pulled twice
   (validity-a, then noise-a 40 minutes later), scored with out-of-fold scores of the
   blend, under each way of turning two pulls into a truth label.

Design only, not a test. Output: evals/claude/r3/design/extra.json and extra.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import r3_data, r3_features as F, r3_fit, r3_model as R, r3_pages, r3_plans, r3_score  # noqa: E402

OUT = REPO / "evals" / "claude" / "r3" / "design"
LABELS = ["validity-a", "train-a"]


def slug_text(url: str) -> str:
    segs = [seg for seg in urlsplit(url).path.split("/") if seg]
    return re.sub(r"[-_+.]+", " ", " ".join(segs[-2:])) if segs else "home"


TEXTS = {
    "title alone": lambda page: (page.get("title") or "")[:300],
    "H1 alone": lambda page: (page.get("h1") or page.get("title") or "")[:300],
    "URL words": lambda page: slug_text(page["fetch_url"]),
    "title, H1 and meta": F.page_text_for_title,
    "headings": lambda page: " . ".join((page.get("headings") or [])[:25])[:900] or (page.get("title") or ""),
    "intro (first 120 words)": lambda page: " ".join((page.get("main_text") or "").split()[:120]) or (page.get("title") or ""),
}


def study_a(table: pd.DataFrame, pairs: pd.DataFrame) -> dict:
    from scripts.claude import r3_semantic as S

    usable = table[table["usable"]]
    pages = r3_pages.load_pages(set(usable["key"]))
    readings: dict[str, pd.Series] = {}
    for model in ("minilm-l6", "minilm-l12"):
        for name, text_of in TEXTS.items():
            parts = []
            for query, group in usable.groupby("query", sort=True):
                parts.append(pd.Series(S.cross_scores(query, [text_of(pages[key]) for key in group["key"]], model=model), index=group.index))
            readings[f"cross-encoder {model}: {name}"] = pd.concat(parts).astype(float)
    queries = sorted(set(usable["query"]))
    query_vec = dict(zip(queries, S.embed(queries, is_query=True), strict=True))
    for name, text_of in TEXTS.items():
        texts = [text_of(pages[key]) for key in usable["key"]]
        unique = sorted(set(texts))
        vec = dict(zip(unique, S.embed(unique), strict=True))
        readings[f"embedding bge-small: {name}"] = pd.Series([float(np.dot(query_vec[q], vec[t])) for q, t in zip(usable["query"], texts, strict=True)], index=usable.index)

    def z(name: str) -> pd.Series:
        return R.zscore_in_list(table, readings[name])

    mixes = {
        "mix: cross-encoder l6 + embedding, title H1 meta": z("cross-encoder minilm-l6: title, H1 and meta") + z("embedding bge-small: title, H1 and meta"),
        "mix: both cross-encoders + embedding on title H1 meta, plus URL words": z("cross-encoder minilm-l6: title, H1 and meta") + z("embedding bge-small: title, H1 and meta") + z("cross-encoder minilm-l12: title, H1 and meta") + z("cross-encoder minilm-l6: URL words") + z("embedding bge-small: URL words"),
        "mix: title, URL words, intro, headings": z("cross-encoder minilm-l12: title, H1 and meta") + z("embedding bge-small: title, H1 and meta") + z("cross-encoder minilm-l12: URL words") + z("cross-encoder minilm-l12: intro (first 120 words)") + z("embedding bge-small: headings"),
    }
    out = {}
    for name, scores in {**readings, **mixes}.items():
        entry = R.accuracy(pairs, R.pair_hits(pairs, scores))
        out[name] = {"rate": entry["rate"], "ci_site": entry["ci_site"], "n": entry["n"]}
    return out


def study_b(table: pd.DataFrame, pairs: pd.DataFrame, group_of: dict) -> dict:
    features = R.feature_set(["lexical", "semantic", "url", "shape", "query"], set(table.columns))
    site_cv = R.cross_validate(table, "blend", features)
    strict_cv = R.cross_validate(table, "blend", features, also_hold_out_queries=True, group_of=group_of)
    second = r3_data.load_label("noise-a")
    twice = set(second["q"]) | set(r3_data.load_requests("noise-a")["q"])
    mask = table.loc[pairs["hi"], "q"].isin(twice).to_numpy()
    part = pairs[mask].reset_index(drop=True)
    status = r3_score.pair_status(part, table, second)
    consensus = np.isin(status, ["consensus_both", "consensus_lower_missing"])
    disagree = np.isin(status, ["disagree_flipped", "disagree_higher_missing"])
    out = {
        "lists": int(part["list_id"].nunique()),
        "pairs": int(len(part)),
        "status": {name: int((status == name).sum()) for name in r3_score.STATUSES},
        "scorers": {},
    }
    for name, scores in (("blend, site held out", site_cv), ("blend, site and search group held out", strict_cv), ("bm25f", table["bm25f"].astype(float))):
        hits = R.pair_hits(part, scores)
        out["scorers"][name] = {
            "single pull, all pairs": float(hits.mean()),
            "consensus (missing page ranks below)": float(hits[consensus].mean()),
            "half credit on all pairs": float(np.where(disagree, 0.5, hits).mean()),
            "consensus, both pages in the second pull": float(hits[status == "consensus_both"].mean()),
            "flipped in the second pull": float(hits[status == "disagree_flipped"].mean()),
            "lower page missing from the second pull": float(hits[status == "consensus_lower_missing"].mean()),
            "higher page missing from the second pull": float(hits[status == "disagree_higher_missing"].mean()) if (status == "disagree_higher_missing").any() else None,
            "both missing": float(hits[status == "both_missing"].mean()) if (status == "both_missing").any() else None,
        }
    return out


def main() -> int:
    table, _df = r3_fit.training_table(LABELS)
    universe = r3_plans.load_universe()
    group_of = {query: index for index, group in enumerate(universe["groups"]) for query in group}
    pairs = R.list_pairs_index(table)
    res = {"A_topic_match_readings": study_a(table, pairs), "B_twice_pulled_lists": study_b(table, pairs, group_of)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "extra.json").write_text(json.dumps(res, indent=2, sort_keys=True, default=float) + "\n", encoding="utf-8")
    a, b = res["A_topic_match_readings"], res["B_twice_pulled_lists"]
    single = [value["rate"] for name, value in a.items() if not name.startswith("mix")]
    lines = [
        "# Round 3 design, extra studies (scripts/claude/r3_design_extra.py)",
        "",
        "Training lists only. PREDICTED scorer outputs against REAL site-restricted list order. Design, not a test.",
        "",
        "## A. Other readings of topic match, nothing fitted",
        "",
        f"{len(single)} single readings range from {min(single):.3f} to {max(single):.3f}. Three unfitted mixes: " + ", ".join(f"{value['rate']:.3f}" for name, value in a.items() if name.startswith("mix")) + ".",
        "",
        "| Reading | Pair accuracy [95% by site] |",
        "| --- | --- |",
    ]
    for name, value in sorted(a.items(), key=lambda item: -item[1]["rate"]):
        lines.append(f"| {name} | {value['rate']:.3f} [{value['ci_site'][0]:.3f}, {value['ci_site'][1]:.3f}] |")
    lines += [
        "",
        "## B. Training lists pulled twice, 40 minutes apart",
        "",
        f"{b['lists']} lists, {b['pairs']} pairs of the first pull. Status under the second pull: {json.dumps(b['status'])}.",
        "",
        "| Truth definition | " + " | ".join(b["scorers"]) + " |",
        "| --- | " + " | ".join("---" for _ in b["scorers"]) + " |",
    ]
    for key in next(iter(b["scorers"].values())):
        cells = [("n/a" if b["scorers"][name][key] is None else f"{b['scorers'][name][key]:.3f}") for name in b["scorers"]]
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    (OUT / "extra.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
