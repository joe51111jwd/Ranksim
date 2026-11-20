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
    in_cell = table["site"].map(lambda site: universe["site_fold"].get(site) == "train") & table["query"].map(
        lambda q: universe["group_fold"].get(str(group_of.get(q))) == "train"
    )
    if not in_cell.all():
        raise SystemExit(f"{int((~in_cell).sum())} training rows are outside the train cell. Refusing to fit.")
    return table.reset_index(drop=True), df_table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--spec", required=True)
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    table, df_table = training_table(spec["train_labels"])
    features = R.feature_set(spec["tiers"], set(table.columns))
    missing = [name for tier in spec["tiers"] for name in R.TIERS[tier] if name not in table.columns]
    if missing:
        raise SystemExit(f"Features missing from the training table: {missing}")
    scorer = R.FrozenScorer.fit(table, spec["kind"], features)
    out = MODEL_ROOT / spec["name"]
    scorer.save(out)
    (out / "df_table.json").write_text(json.dumps(df_table, sort_keys=True) + "\n", encoding="utf-8")
    pairs = R.list_pairs_index(table)
    fit_info = {
        "train_labels": spec["train_labels"],
        "lists_with_pairs": int(pairs["list_id"].nunique()),
        "pairs": int(len(pairs)),
        "sites": int(pairs["site"].nunique()),
        "searches": int(pairs["query"].nunique()),
        "usable_pages": int(table["usable"].sum()),
        "in_sample_pair_accuracy": float(R.pair_hits(pairs, scorer.score(table)).mean()),
        "note": "in-sample accuracy is a training score, not accuracy",
    }
    (out / "fit_info.json").write_text(json.dumps(fit_info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(fit_info, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
