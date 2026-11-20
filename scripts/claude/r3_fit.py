"""Round 3: fit the chosen scorer once on the training lists and save it for the freeze.

  python scripts/claude/r3_fit.py --spec evals/claude/r3/model_spec.json

The spec names the scorer kind (logit, gbm or blend), the feature tiers and the
training labels. The fit reads only training lists (train sites x train search
groups of evals/claude/r3/plans/universe.json) and their stored pages. It writes
evals/claude/r3/model/<name>/: spec.json, logit.json and/or gbm.txt, and
df_table.json (the document-frequency table BM25 uses, from the training pages).
Nothing here reads a held-out list.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import r3_dataset, r3_model as R, r3_plans  # noqa: E402

MODEL_ROOT = REPO / "evals" / "claude" / "r3" / "model"


def training_table(labels: list[str], with_semantic: bool = True):
    universe = r3_plans.load_universe()
    group_of = {query: index for index, group in enumerate(universe["groups"]) for query in group}
    table, df_table = r3_dataset.build_table(labels, with_semantic=with_semantic)
    site_fold = table["site"].map(lambda site: universe["site_fold"].get(site))
    query_fold = table["query"].map(lambda q: universe["group_fold"].get(str(group_of.get(q))))
    # A held-out site or search group must never reach the fit.
    if site_fold.isin(["test1", "test2"]).any() or (query_fold == "test").any():
        raise SystemExit("A training label holds rows of a held-out site or search group. Refusing to fit.")
    # Sites outside the universe (user-content platforms, sites plain HTTP cannot read) are left out, as in the design run.
    in_cell = (site_fold == "train") & (query_fold == "train")
    print(f"training rows kept: {int(in_cell.sum())}, left out (site not in the universe): {int((~in_cell).sum())}")
    table = table[in_cell].reset_index(drop=True)
    # The BM25 table must describe exactly the pages that are kept.
    kept_keys = set(table.loc[table["usable"], "key"])
    pages = r3_dataset.r3_pages.load_pages(kept_keys)
    df_table_kept = r3_dataset.make_df_table({key: page for key, page in pages.items() if key in kept_keys})
    if df_table_kept["n"] != df_table["n"]:
        # Rebuild features with the table of the kept pages only, so fit and scoring use one and the same table.
        table, _ = r3_dataset.build_table(labels, df_table=df_table_kept, with_semantic=with_semantic)
        site_fold = table["site"].map(lambda site: universe["site_fold"].get(site))
        query_fold = table["query"].map(lambda q: universe["group_fold"].get(str(group_of.get(q))))
        table = table[(site_fold == "train") & (query_fold == "train")].reset_index(drop=True)
    return table, df_table_kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--spec", required=True, action="append", help="Model spec JSON. Repeatable; specs must share train_labels.")
    args = parser.parse_args(argv)
    specs = [json.loads(Path(name).read_text(encoding="utf-8")) for name in args.spec]
    labels = specs[0]["train_labels"]
    if any(spec["train_labels"] != labels for spec in specs):
        raise SystemExit("All specs in one run must use the same train_labels.")
    table, df_table = training_table(labels)
    pairs = R.list_pairs_index(table)
    for spec in specs:
        features = R.feature_set(spec["tiers"], set(table.columns))
        missing = [name for tier in spec["tiers"] for name in R.TIERS[tier] if name not in table.columns]
        if missing:
            raise SystemExit(f"Features missing from the training table: {missing}")
        scorer = R.FrozenScorer.fit(table, spec["kind"], features)
        out = MODEL_ROOT / spec["name"]
        scorer.save(out)
        (out / "df_table.json").write_text(json.dumps(df_table, sort_keys=True) + "\n", encoding="utf-8")
        fit_info = {
            "train_labels": labels,
            "tiers": spec["tiers"],
            "lists_with_pairs": int(pairs["list_id"].nunique()),
            "pairs": int(len(pairs)),
            "sites": int(pairs["site"].nunique()),
            "searches": int(pairs["query"].nunique()),
            "usable_pages": int(table["usable"].sum()),
            "in_sample_pair_accuracy": float(R.pair_hits(pairs, scorer.score(table)).mean()),
            "note": "in-sample accuracy is a training score, not accuracy",
        }
        (out / "fit_info.json").write_text(json.dumps(fit_info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # A reloaded scorer must give the same scores as the fitted one.
        reloaded = R.FrozenScorer.load(out).score(table)
        drift = float((reloaded - scorer.score(table)).abs().max())
        if drift > 1e-6:
            raise SystemExit(f"Reloaded scorer differs from the fitted one by {drift}.")
        print(spec["name"], json.dumps(fit_info, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
