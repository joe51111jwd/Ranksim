"""Temporal pairwise experiments. Selection uses only the earlier windows.

PREDICTED scores. Outcome positions are labels. Every outcome row stays in the denominator.

Selection rule, fixed before the later windows are consulted for the winner:
- Nov 9 number: 5-fold query-group cross-validation inside the Nov 6 to Nov 9 transition.
  There is no earlier label day, so this is not a temporal score. It is labeled as such.
- Nov 11 number: fit on the Nov 6 to Nov 9 transition only, score Nov 9 to Nov 11.
- The winner maximizes the worse of those two combined pairwise accuracies, then their mean,
  then new-vs-new, then new-vs-surviving, then the simpler family.
- Nov 13 and Nov 16 are scored after that choice. They do not pick the family.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pickle
import sys
import traceback
from collections import defaultdict
from pathlib import Path

from aeo.ranksim.full_coverage import load_params, load_position_csv, rank_metrics, score_outcome
from aeo.ranksim.pair_order import (
    COMPLEXITY,
    EXPERIMENT_NAMES,
    BlendModel,
    BoostingModel,
    FrozenStyleModel,
    LinearModel,
    TwoHeadModel,
    bootstrap_ci,
    build_transition,
    dump_model,
    evaluate_scores,
    feature_list,
    fit_experiment,
    load_cc_ranks,
    load_html_dir,
    load_serp_snapshot,
    load_offpage,
    pages_from_html,
    selection_sort_key,
    write_offpage_cache,
)

ROOT = Path(__file__).resolve().parents[1]
DAYS = ("2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16")
TRANSITIONS = {
    "2026-11-09": ("2026-11-06",),
    "2026-11-11": ("2026-11-06", "2026-11-09"),
    "2026-11-13": ("2026-11-06", "2026-11-09", "2026-11-11"),
    "2026-11-16": ("2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13"),
}
PARAMS = ROOT / "evals/preregistered/next-snapshot/params.json"
OUT_DIR = ROOT / "evals/experiments-2026-11-16"
FREEZE_DIR = ROOT / "evals/preregistered/forward-pair-2026-11-16"
CACHE = Path("/tmp/ranksim-page-cache-v1.pkl")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rate(block: dict) -> float | None:
    if not block or not block["pairs"]:
        return None
    return block["correct"] / block["pairs"]


def _fmt(block: dict) -> str:
    if not block or not block["pairs"]:
        return "n/a"
    rate = block["correct"] / block["pairs"]
    return f"{block['correct']}/{block['pairs']} ({rate:.6f})"


def _ci(text_low, text_high) -> str:
    if text_low is None:
        return "n/a"
    return f"[{text_low:.3f}, {text_high:.3f}]"


def _find_snapshot(root: Path, day: str) -> Path:
    matches = [path for path in root.iterdir() if path.is_dir() and day in path.name]
    if len(matches) != 1:
        raise SystemExit(f"Expected one snapshot directory for {day} under {root}, found {matches}")
    return matches[0]


def _load_bundle(directory: Path, day: str):
    from aeo.ranksim.pair_order import load_serp_snapshot

    snap = load_serp_snapshot(directory, day)
    html_map, info = load_html_dir(directory)
    pages = pages_from_html(html_map)
    return snap, pages, html_map, info


def _note(model: object) -> str:
    if isinstance(model, LinearModel):
        return f"used {len(model.features)} features, lam {model.lam:g}, kind {model.kind}"
    if isinstance(model, FrozenStyleModel):
        weights = ", ".join(f"{name}={value:.4f}" for name, value in model.weights.items())
        return f"intercept {model.intercept:.4f}; {weights}"
    if isinstance(model, BlendModel):
        return f"alpha {model.alpha:g}; {_note(model.base)}"
    if isinstance(model, TwoHeadModel):
        return f"new-url bias {model.bias:.4f}"
    if isinstance(model, BoostingModel):
        return f"lambdamart trees, monotone {model.monotone}, {len(model.features)} features"
    return model.__class__.__name__


def _row_record(name: str, train: str, eval_day: str, metrics: dict, persistence: dict, notes: str) -> dict:
    combined = metrics["blocks"]["all"]
    low, high = bootstrap_ci(metrics["per_query"]["all"])
    record = {
        "name": name,
        "features": feature_list(name) if name in COMPLEXITY else name,
        "train_windows": train,
        "eval_window": eval_day,
        "combined_correct": combined["correct"],
        "combined_pairs": combined["pairs"],
        "combined_ties": combined["ties"],
        "combined_pairwise": None if combined["pairs"] == 0 else combined["correct"] / combined["pairs"],
        "ci95_low": low,
        "ci95_high": high,
        "queries": combined["queries"],
        "ndcg_at_10": metrics["ndcg_at_10"],
        "spearman": metrics["spearman"],
        "notes": notes,
        "scored_rows": metrics["rows"],
    }
    for key, column in (
        ("new_vs_new", "new_new"),
        ("new_vs_surviving", "new_surviving"),
        ("surviving_surviving", "surviving_surviving"),
    ):
        block = metrics["blocks"][key]
        record[f"{column}_correct"] = block["correct"]
        record[f"{column}_pairs"] = block["pairs"]
        record[f"{column}_ties"] = block["ties"]
        record[f"{column}_pairwise"] = None if block["pairs"] == 0 else block["correct"] / block["pairs"]
    pers = persistence["blocks"]["all"]
    record["persistence_correct"] = pers["correct"]
    record["persistence_pairs"] = pers["pairs"]
    record["persistence_ties"] = pers["ties"]
    record["persistence_pairwise"] = None if pers["pairs"] == 0 else pers["correct"] / pers["pairs"]
    return record


def _cross_validate(name: str, rows: list) -> dict:
    queries = sorted({row.query for row in rows})
    folds = [set(queries[index::5]) for index in range(5)]
    scores: list[float | None] = [None] * len(rows)
    for fold in folds:
        train = [row for row in rows if row.query not in fold]
        test_indexes = [index for index, row in enumerate(rows) if row.query in fold]
        model = fit_experiment(name, train)
        predicted = model.predict([rows[index] for index in test_indexes])
        for index, score in zip(test_indexes, predicted, strict=True):
            scores[index] = score
    if any(score is None for score in scores):
        raise ValueError("query-group CV left a row unscored")
    return evaluate_scores(rows, [float(score) for score in scores])


def _published(history, outcome, html_map) -> dict:
    hist = [(snap.day, load_position_csv(snap.serp_path, snap.day)) for snap in history]
    labels = load_position_csv(outcome.serp_path, outcome.day)
    scored = score_outcome(hist, labels, load_params(PARAMS), html_by_url=html_map)
    metrics = rank_metrics(scored, "full_coverage")
    persistence = rank_metrics(scored, "persistence_ranked_last")
    return {"metrics": metrics, "persistence": persistence, "rows": sum(len(group) for group in scored.values())}


def _published_record(eval_day: str, train: str, payload: dict) -> dict:
    """Adapt the frozen scorer's rounded blocks into the experiment table."""
    def block(source: dict) -> dict:
        return {
            "correct": source["correct"],
            "wrong": source["wrong"],
            "ties": source["ties"],
            "pairs": source["pairs"],
            "queries": source["queries"],
        }

    metrics = {
        "blocks": {mode: block(payload["metrics"][mode]) for mode in ("all", "new_vs_new", "new_vs_surviving", "surviving_surviving")},
        "per_query": {"all": []},
        "ndcg_at_10": payload["metrics"]["ndcg_at_10"],
        "spearman": payload["metrics"]["mean_spearman"],
        "rows": payload["rows"],
    }
    persistence = {"blocks": {"all": block(payload["persistence"]["all"])}}
    record = _row_record(
        "published_frozen_coefficients",
        train,
        eval_day,
        metrics,
        persistence,
        "Published full_coverage.py coefficients from params.json. Not refit. CI omitted because that scorer does not return per-query pair counts.",
    )
    record["features"] = "published domain prior, url overlap, bm25 z weight 1; survivors keep last position"
    record["ci95_low"] = None
    record["ci95_high"] = None
    return record


def _agreement(left, right) -> dict:
    left_map = {}
    for row in left.rows:
        key = (row.query, row.url_key)
        if key not in left_map or row.position < left_map[key].position:
            left_map[key] = row
    right_map = {}
    for row in right.rows:
        key = (row.query, row.url_key)
        if key not in right_map or row.position < right_map[key].position:
            right_map[key] = row
    correct = wrong = 0
    queries = set()
    shared_rows = 0
    for query in {row.query for row in left.rows}:
        urls = [url for (q, url) in left_map if q == query and (q, url) in right_map]
        shared_rows += len(urls)
        for i, left_url in enumerate(urls):
            for right_url in urls[i + 1 :]:
                left_order = left_map[(query, left_url)].position - left_map[(query, right_url)].position
                right_order = right_map[(query, left_url)].position - right_map[(query, right_url)].position
                if left_order == 0 or right_order == 0:
                    continue
                queries.add(query)
                if (left_order < 0) == (right_order < 0):
                    correct += 1
                else:
                    wrong += 1
    total = correct + wrong
    return {"correct": correct, "pairs": total, "pairwise": None if total == 0 else correct / total, "queries": len(queries), "shared_rows": shared_rows}


def _flip_counts(maps: list[dict], start: str, end: str) -> dict:
    by_query: dict[str, list[str]] = defaultdict(list)
    for query, url in maps[0]:
        if all((query, url) in mapping for mapping in maps):
            by_query[query].append(url)
    stable = stayed = returned = later = 0
    for query, urls in by_query.items():
        for i, left in enumerate(urls):
            for right in urls[i + 1 :]:
                signs = []
                for mapping in maps:
                    diff = mapping[(query, left)] - mapping[(query, right)]
                    if diff == 0:
                        signs = []
                        break
                    signs.append(1 if diff < 0 else -1)
                if len(signs) != 3:
                    continue
                if signs[0] == signs[1] == signs[2]:
                    stable += 1
                elif signs[0] != signs[1] and signs[1] == signs[2]:
                    stayed += 1
                elif signs[0] != signs[1] and signs[0] == signs[2]:
                    returned += 1
                else:
                    later += 1
    flipped = stayed + returned
    return {
        "window": f"{start} to {end}",
        "stable": stable,
        "flipped_and_stayed": stayed,
        "flipped_and_returned": returned,
        "flipped_later_only": later,
        "return_rate_among_flips": None if flipped == 0 else returned / flipped,
    }


def _filter_cc(gz_path: Path, domains: set[str], out_path: Path) -> int:
    kept = 0
    with gzip.open(gz_path, "rt", encoding="utf-8", errors="replace", newline="") as handle, out_path.open("w", encoding="utf-8") as out:
        header = handle.readline().rstrip("\n")
        names = [part.strip().lstrip("#") for part in header.split("\t")]
        index = {name: position for position, name in enumerate(names)}
        host_name = "host_rev" if "host_rev" in index else "domain" if "domain" in index else None
        if host_name is None or "pr_val" not in index:
            raise ValueError(f"Unexpected Common Crawl header: {header}")
        host_i = index[host_name]
        pr_i = index["pr_val"]
        harm_i = index.get("harmonicc_val")
        out.write("domain\tpr_val\tharmonicc_val\n")
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= max(host_i, pr_i):
                continue
            raw = parts[host_i].strip().lower()
            domain = ".".join(reversed([piece for piece in raw.split(".") if piece])) if host_name == "host_rev" else raw
            if domain not in domains and raw not in domains:
                continue
            harm = parts[harm_i] if harm_i is not None and harm_i < len(parts) else ""
            out.write(f"{domain}\t{parts[pr_i]}\t{harm}\n")
            kept += 1
    return kept


def _markdown(records: list[dict], preamble: str, ceiling: str, winner: dict | None) -> str:
    lines = [preamble, "", "## Results", ""]
    lines.append("Every pairwise cell is correct/pairs. Ties sit in the denominator and are not correct. Label: PREDICTED.")
    lines.append("")
    header = "| Experiment | Train | Eval | Combined | 95% CI | New vs new | New vs surviving | Surviving vs surviving | NDCG@10 | Spearman | Persistence |"
    sep = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    lines.extend([header, sep])
    for record in records:
        if record.get("error"):
            continue
        lines.append(
            "| {name} | {train} | {eval_window} | {combined} | {ci} | {nn} | {ns} | {ss} | {ndcg} | {rho} | {pers} |".format(
                name=record["name"],
                train=record["train_windows"],
                eval_window=record["eval_window"],
                combined=_fmt({"correct": record["combined_correct"], "pairs": record["combined_pairs"]}),
                ci=_ci(record["ci95_low"], record["ci95_high"]),
                nn=_fmt({"correct": record["new_new_correct"], "pairs": record["new_new_pairs"]}),
                ns=_fmt({"correct": record["new_surviving_correct"], "pairs": record["new_surviving_pairs"]}),
                ss=_fmt({"correct": record["surviving_surviving_correct"], "pairs": record["surviving_surviving_pairs"]}),
                ndcg="n/a" if record["ndcg_at_10"] is None else f"{record['ndcg_at_10']:.4f}",
                rho="n/a" if record["spearman"] is None else f"{record['spearman']:.4f}",
                pers=_fmt({"correct": record["persistence_correct"], "pairs": record["persistence_pairs"]}),
            )
        )
    errors = [record for record in records if record.get("error")]
    if errors:
        lines.extend(["", "## Failed runs", ""])
        for record in errors:
            lines.append(f"- {record['name']} eval {record['eval_window']}: {record['notes']}")
    lines.extend(["", "## Selection", ""])
    if winner:
        lines.append(
            f"Winner by the early-window rule: `{winner['name']}` "
            f"with worse-window combined {winner['worse']:.6f}. "
            "Nov 13 and Nov 16 did not choose it."
        )
    else:
        lines.append("No winner.")
    lines.extend(["", "## Label movement, not a model", "", ceiling, ""])
    return "\n".join(lines)


def _csv(records: list[dict], path: Path) -> None:
    import csv

    fields = [
        "name",
        "features",
        "train_windows",
        "eval_window",
        "combined_correct",
        "combined_pairs",
        "combined_ties",
        "combined_pairwise",
        "ci95_low",
        "ci95_high",
        "new_new_correct",
        "new_new_pairs",
        "new_new_ties",
        "new_new_pairwise",
        "new_surviving_correct",
        "new_surviving_pairs",
        "new_surviving_ties",
        "new_surviving_pairwise",
        "surviving_surviving_correct",
        "surviving_surviving_pairs",
        "surviving_surviving_ties",
        "surviving_surviving_pairwise",
        "ndcg_at_10",
        "spearman",
        "persistence_correct",
        "persistence_pairs",
        "persistence_ties",
        "persistence_pairwise",
        "queries",
        "scored_rows",
        "notes",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)


def main() -> int:
    snap_root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ranksim-snaps")
    off_root = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/ranksim-offpage")
    print("Loading snapshots", flush=True)
    snaps = {}
    pages = {}
    html_maps = {}
    coverage_lines = []
    if CACHE.is_file():
        cached = pickle.loads(CACHE.read_bytes())
        pages, html_maps = cached["pages"], cached["html"]
        for day in DAYS:
            snaps[day] = load_serp_snapshot(_find_snapshot(snap_root, day), day)
        print("Loaded page cache and re-read SERP rows", flush=True)
    else:
        for day in DAYS:
            directory = _find_snapshot(snap_root, day)
            snap, page_map, html_map, info = _load_bundle(directory, day)
            snaps[day] = snap
            pages[day] = page_map
            html_maps[day] = html_map
            matched = sum(1 for row in snap.rows if row.url_key in page_map)
            if matched < 40:
                raise SystemExit(f"HTML matched only {matched} rows on {day}. Refusing to fit text features against the wrong keys.")
            coverage_lines.append(
                f"- {day}: {len({row.query for row in snap.rows})} queries, {len(snap.rows)} rows, HTML matched {matched}, manifest files {info['html_files']}, missing files {info['missing_files']}, sha256 { _sha256(snap.serp_path) }"
            )
            print(coverage_lines[-1], flush=True)
        CACHE.write_bytes(pickle.dumps({"snaps": snaps, "pages": pages, "html": html_maps}, protocol=4))
    if not coverage_lines:
        for day, snap in snaps.items():
            matched = sum(1 for row in snap.rows if row.url_key in pages[day])
            coverage_lines.append(f"- {day}: {len(snap.rows)} rows, HTML matched {matched}, sha256 {_sha256(snap.serp_path)}")
    print("Loading off-page lists", flush=True)
    cc_gz = off_root / "cc-ranks.txt.gz"
    cc_tsv = off_root / "cc-domains.tsv"
    cc_log = off_root / "cc-curl.log"
    import time

    waited = 0
    while waited < 900:
        size = cc_gz.stat().st_size if cc_gz.is_file() else 0
        done = False
        if cc_log.is_file():
            tail = cc_log.read_text(encoding="utf-8", errors="replace")[-80:]
            done = "EXIT:" in tail
        if size > 2_000_000_000 or done:
            break
        print(f"Waiting for Common Crawl download, bytes {size}", flush=True)
        time.sleep(15)
        waited += 15
    domains = set()
    for snap in snaps.values():
        for row in snap.rows:
            domains.add(row.site)
    already_filtered = cc_tsv.is_file() and sum(1 for _line in cc_tsv.open(encoding="utf-8")) > 10
    if already_filtered:
        print("Using the Common Crawl domain filter already on disk", flush=True)
    elif cc_gz.is_file() and cc_gz.stat().st_size > 2_000_000_000:
        print("Filtering Common Crawl ranks", flush=True)
        kept = _filter_cc(cc_gz, domains, cc_tsv)
        print(f"Common Crawl domains kept {kept}", flush=True)
        if kept < 10:
            raise SystemExit("Common Crawl filter kept too few domains. The host column was not reversed into registrable domains.")
    offpage = load_offpage(
        off_root / "top-1m.csv.zip" if (off_root / "top-1m.csv.zip").is_file() else None,
        off_root / "majestic_million.csv" if (off_root / "majestic_million.csv").is_file() else None,
        cc_tsv if cc_tsv.is_file() and cc_tsv.stat().st_size > 200 else None,
    )
    print(
        f"Tranco {len(offpage.tranco)} Majestic {len(offpage.majestic)} CC pr {len(offpage.cc_pagerank)}",
        flush=True,
    )
    print("Building transitions", flush=True)
    built = {}
    for label, hist_days in TRANSITIONS.items():
        history = [snaps[day] for day in hist_days]
        built[label] = build_transition(history, snaps[label], pages[label], offpage)
        rows = built[label]
        new = sum(1 for row in rows if row.bucket == "new")
        missing = sum(1 for row in rows if row.features.get("content_present") == 0)
        print(f"  {label}: rows {len(rows)} new {new} content missing {missing}", flush=True)
    records: list[dict] = []
    persistence_metrics = {}
    for label, rows in built.items():
        scores = fit_experiment("persistence_ranked_last", rows).predict(rows)
        persistence_metrics[label] = evaluate_scores(rows, scores)
    print("Published frozen coefficients", flush=True)
    for label, hist_days in TRANSITIONS.items():
        if label == "2026-11-09":
            continue
        try:
            payload = _published([snaps[day] for day in hist_days], snaps[label], html_maps[label])
            train = "published coefficients fit on 2026-11-06_to_2026-11-09, history " + "+".join(hist_days)
            records.append(_published_record(f"{hist_days[-1]} -> {label}", train, payload))
            print("  published", label, _fmt({"correct": records[-1]["combined_correct"], "pairs": records[-1]["combined_pairs"]}), flush=True)
        except Exception as exc:
            records.append({"name": "published_frozen_coefficients", "eval_window": label, "train_windows": "published", "notes": str(exc), "error": True, "features": ""})
            print("  published failed", label, exc, flush=True)
    fitted_models = {}
    print("Fitting experiments", flush=True)
    for name in EXPERIMENT_NAMES:
        for label, hist_days in TRANSITIONS.items():
            train_days = [day for day in TRANSITIONS if day < label]
            train_rows = [row for day in train_days for row in built[day]]
            if not train_rows and name not in {"persistence_ranked_last", "precommitted_blend"}:
                continue
            eval_label = f"{hist_days[-1]} -> {label}"
            train_label = "none (no learned weights)" if name in {"persistence_ranked_last", "precommitted_blend"} else " + ".join(f"{TRANSITIONS[day][-1]}->{day}" for day in train_days)
            if name in {"persistence_ranked_last", "precommitted_blend"}:
                train_label = "no fit; features from history before " + label
            try:
                if not train_rows and name not in {"persistence_ranked_last", "precommitted_blend"}:
                    raise ValueError("no earlier labels")
                model = fit_experiment(name, train_rows or built[label])
                scores = model.predict(built[label])
                metrics = evaluate_scores(built[label], scores)
                record = _row_record(name, train_label, eval_label, metrics, persistence_metrics[label], _note(model))
                records.append(record)
                if label == "2026-11-16" and train_days == ["2026-11-09", "2026-11-11", "2026-11-13"]:
                    fitted_models[("heldout16", name)] = model
                if label == "2026-11-13" and train_days == ["2026-11-09", "2026-11-11"]:
                    fitted_models[("heldout13", name)] = model
                print(f"  {name} {eval_label} {_fmt({'correct': record['combined_correct'], 'pairs': record['combined_pairs']})} nn {_fmt({'correct': record['new_new_correct'], 'pairs': record['new_new_pairs']})}", flush=True)
            except Exception:
                err = traceback.format_exc(limit=3)
                records.append({"name": name, "features": feature_list(name), "train_windows": train_label, "eval_window": eval_label, "notes": err, "error": True})
                print(f"  FAILED {name} {eval_label}", flush=True)
                print(err, flush=True)
        print(f"  query CV {name}", flush=True)
        try:
            cv9 = _cross_validate(name, built["2026-11-09"])
            records.append(
                _row_record(
                    name,
                    "query-group CV inside 2026-11-09; features from 2026-11-06 only. Not a temporal score.",
                    "2026-11-06 -> 2026-11-09 query-group CV",
                    cv9,
                    persistence_metrics["2026-11-09"],
                    "secondary selection window",
                )
            )
            print(f"    nov9 cv {_fmt({'correct': records[-1]['combined_correct'], 'pairs': records[-1]['combined_pairs']})}", flush=True)
        except Exception:
            err = traceback.format_exc(limit=3)
            records.append({"name": name, "features": feature_list(name), "train_windows": "query-group CV", "eval_window": "2026-11-09 query-group CV", "notes": err, "error": True})
            print("    nov9 cv failed", flush=True)
        try:
            train_rows = [row for day in ("2026-11-09", "2026-11-11", "2026-11-13") for row in built[day]]
            # Query CV on Nov 16 uses other queries' earlier transitions only.
            queries = sorted({row.query for row in built["2026-11-16"]})
            folds = [set(queries[index::5]) for index in range(5)]
            scores: list[float | None] = [None] * len(built["2026-11-16"])
            for fold in folds:
                train = [row for row in train_rows if row.query not in fold]
                test_indexes = [index for index, row in enumerate(built["2026-11-16"]) if row.query in fold]
                model = fit_experiment(name, train)
                predicted = model.predict([built["2026-11-16"][index] for index in test_indexes])
                for index, score in zip(test_indexes, predicted, strict=True):
                    scores[index] = score
            cv16 = evaluate_scores(built["2026-11-16"], [float(score) for score in scores])
            records.append(
                _row_record(
                    name,
                    "query-group CV; train transitions through 2026-11-13 on the other queries",
                    "2026-11-13 -> 2026-11-16 query-group CV",
                    cv16,
                    persistence_metrics["2026-11-16"],
                    "secondary check, not used for selection",
                )
            )
            print(f"    nov16 cv {_fmt({'correct': records[-1]['combined_correct'], 'pairs': records[-1]['combined_pairs']})}", flush=True)
        except Exception:
            err = traceback.format_exc(limit=3)
            records.append({"name": name, "features": feature_list(name), "train_windows": "query-group CV", "eval_window": "2026-11-16 query-group CV", "notes": err, "error": True})
            print("    nov16 cv failed", flush=True)
    # Refit the full-through-Nov-16 models after selection. Selection itself uses the records above.
    nov9 = {}
    nov11 = {}
    for record in records:
        if record.get("error"):
            continue
        if record["eval_window"] == "2026-11-06 -> 2026-11-09 query-group CV":
            nov9[record["name"]] = record
        if record["eval_window"] == "2026-11-09 -> 2026-11-11" and record["name"] in COMPLEXITY:
            nov11[record["name"]] = record
    winner = None
    best_key = None
    for name, later in nov11.items():
        earlier = nov9.get(name)
        if earlier is None or earlier.get("combined_pairwise") is None or later.get("combined_pairwise") is None:
            continue
        key = selection_sort_key(
            float(earlier["combined_pairwise"]),
            float(later["combined_pairwise"]),
            float(later.get("new_new_pairwise") or 0.0),
            float(later.get("new_surviving_pairwise") or 0.0),
            COMPLEXITY[name],
        )
        if best_key is None or key > best_key:
            best_key = key
            winner = {
                "name": name,
                "worse": key[0],
                "mean": key[1],
                "nov9": earlier,
                "nov11": later,
            }
    print("Winner", winner["name"] if winner else None, flush=True)
    next_model = None
    if winner:
        all_train = [row for day in ("2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16") for row in built[day]]
        next_model = fit_experiment(winner["name"], all_train)
        heldout16 = fitted_models.get(("heldout16", winner["name"]))
        heldout13 = fitted_models.get(("heldout13", winner["name"]))
        FREEZE_DIR.mkdir(parents=True, exist_ok=True)
        if heldout13:
            dump_model(heldout13, FREEZE_DIR / "model-heldout-nov13.json", extra={"fit_through": "2026-11-11", "label": "PREDICTED", "family": winner["name"]})
        if heldout16:
            dump_model(heldout16, FREEZE_DIR / "model-heldout-nov16.json", extra={"fit_through": "2026-11-13", "label": "PREDICTED", "family": winner["name"]})
        dump_model(
            next_model,
            FREEZE_DIR / "model-next.json",
            extra={
                "fit_through": "2026-11-16",
                "label": "PREDICTED",
                "family": winner["name"],
                "selection_rule": "min of Nov9 query-group CV and Nov11 forward pairwise, then mean, new-vs-new, new-vs-surviving, simpler family",
            },
        )
        domains = sorted({row.site for snap in snaps.values() for row in snap.rows})
        write_offpage_cache(FREEZE_DIR / "offpage.tsv", offpage, domains)
    ceiling_bits = []
    ordered = [snaps[day] for day in DAYS]
    for index in range(len(ordered) - 1):
        left, right = ordered[index], ordered[index + 1]
        stats = _agreement(left, right)
        ceiling_bits.append(
            f"- URLs present on both {left.day} and {right.day}: order agrees on {_fmt({'correct': stats['correct'], 'pairs': stats['pairs']})} "
            f"across {stats['queries']} queries, {stats['shared_rows']} shared rows. This is how stable Google's own order was, not a model."
        )
    maps = []
    for day in DAYS:
        found = {}
        for row in snaps[day].rows:
            key = (row.query, row.url_key)
            if key not in found or row.position < found[key]:
                found[key] = row.position
        maps.append(found)
    for index in range(len(DAYS) - 2):
        window_maps = maps[index : index + 3]
        # Reuse _flip_counts by aligning keys. The helper expects three maps.
        bit = _flip_counts(window_maps, DAYS[index], DAYS[index + 2])
        rate = bit["return_rate_among_flips"]
        ceiling_bits.append(
            f"- {bit['window']}: pairs in the top 10 all three days: stable {bit['stable']}, "
            f"flipped and stayed {bit['flipped_and_stayed']}, flipped and returned {bit['flipped_and_returned']}, "
            f"later flip {bit['flipped_later_only']}. "
            f"Among pairs that flipped on the first step, the share that flipped back is {'n/a' if rate is None else f'{rate:.3f}'}."
        )
    cc_note = f"Common Crawl domains in the lookup: {len(offpage.cc_pagerank)}."
    if len(offpage.cc_pagerank) == 0:
        cc_note += " The rank file was not ready, so Common Crawl columns were constant and dropped."
    preamble = "\n".join(
        [
            "# Pairwise order experiments, 2026-11-16",
            "",
            "Label: PREDICTED on every model number. Label: REAL only for the Serper positions used as labels.",
            "",
            f"Families in the selection set: {len(EXPERIMENT_NAMES)}. Plus the published frozen-coefficient baseline, which is not in the selection set. "
            "The best later-window number across this many tries is optimistic. The winner below was chosen without those later windows. "
            "Three families (persistence locked to yesterday's order, and the SERP snippet/date/niche-vocabulary ridge) were added after the first selection-window pass showed new-vs-new still near two thirds. Their Nov 13 and Nov 16 numbers were not used to design them, but they do add to the multiple-comparison count.",
            "",
            "Protocol. Fit only on earlier transitions. Primary held-out tests are Nov 11 to Nov 13 and Nov 13 to Nov 16. "
            "Selection uses Nov 6 to Nov 9 as 5-fold query-group cross-validation (there is no earlier label day) and Nov 9 to Nov 11 as a true forward test. "
            "The winner is the family with the higher worse-of-those-two combined pairwise accuracy. "
            "A page with no HTML is scored. Nothing is dropped from a denominator.",
            "",
            cc_note,
            f"Tranco domains loaded: {len(offpage.tranco)}. Majestic domains loaded: {len(offpage.majestic)}. Open PageRank was not called; no key is set.",
            "",
            "## Coverage",
            "",
            *coverage_lines,
        ]
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _csv(records, OUT_DIR / "results.csv")
    (OUT_DIR / "log.md").write_text(_markdown(records, preamble, "\n".join(ceiling_bits), winner), encoding="utf-8")
    (OUT_DIR / "selection.json").write_text(json.dumps({"winner": None if not winner else {"name": winner["name"], "worse": winner["worse"], "mean": winner["mean"]}}, indent=2) + "\n", encoding="utf-8")
    print("Wrote", OUT_DIR / "log.md", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
