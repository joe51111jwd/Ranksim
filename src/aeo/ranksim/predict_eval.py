"""Train the draft predictor on the Nov 6 snapshot and score it once on Nov 9 new entries.

Two commands, run in order, with a commit in between:

    python -m aeo.ranksim.predict_eval train --html-dir <private html> --out evals/predict-2026-11-14
    python -m aeo.ranksim.predict_eval score --html-dir <private html> --out evals/predict-2026-11-14

``train`` reads only the Oct 29 and Nov 6 position lists and page HTML. It picks the feature set
and penalty by query-family-out pairwise on Nov 6, fits the new-page handicap on the Oct 29 to
Nov 6 entries, and writes ``training.json`` and ``model.json``. It never opens the Nov 9 file.

``score`` loads that frozen model, checks the training file hash, and scores the Nov 9 new
entries once. It refuses to run if ``report.json`` already exists.

Caveat that applies to every number: the snapshots' own HTML is private and not in this
environment. Page HTML was fetched again on the date in the fetch manifest, days after both
snapshots. A page can have changed since.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aeo.ranksim.predict import (
    ALL_FEATURES,
    CONTENT_FEATURES,
    SITE_FEATURES,
    Group,
    PairwiseModel,
    expected_position,
    fit_entrant_logit,
    fit_pairwise,
    page_signals,
    site_signals,
)

REPO = Path(__file__).resolve().parents[3]
OCT29 = REPO / "smartcrew" / "serp.csv"
NOV06 = REPO / "evals" / "forward-2026-11-09" / "baseline_serp.csv"
NOV09 = REPO / "evals" / "forward-2026-11-09" / "forward_serp.csv"
CANDIDATE_SETS = {"content": CONTENT_FEATURES, "site": SITE_FEATURES, "content+site": ALL_FEATURES}
CANDIDATE_L2 = (0.3, 1.0, 3.0, 10.0, 30.0)
BOTTOM = 1000.0


def key(url: str) -> str:
    from aeo.ranksim.project import loose_key

    return loose_key(url)


def domain(url: str) -> str:
    return key(url).split("/", 1)[0]


def load_positions(path: Path) -> dict[str, dict[str, tuple[str, float]]]:
    """search -> url key -> (url, best position)."""
    out: dict[str, dict[str, tuple[str, float]]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            url = row.get("link") or row.get("url") or ""
            search = (row.get("query") or "").strip()
            position = float(row["position"])
            if not url or not search:
                continue
            k = key(url)
            if k not in out[search] or position < out[search][k][1]:
                out[search][k] = (url, position)
    return dict(out)


@dataclass
class Pages:
    docs: dict  # url key -> PageDoc
    html: dict  # url key -> html
    manifest: list[dict]


def load_pages(html_dir: Path) -> Pages:
    from aeo.ranksim.loop import page_from_html

    manifest = json.loads((html_dir / "fetch_manifest.json").read_text(encoding="utf-8"))
    docs = {}
    html = {}
    for entry in manifest:
        if not entry.get("file"):
            continue
        text = (html_dir / entry["file"]).read_text(encoding="utf-8")
        k = key(entry["url"])
        html[k] = text
        docs[k] = page_from_html(entry["url"], text, is_ours=False)
    return Pages(docs, html, manifest)


def _blank_doc(url: str):
    from aeo.ranksim.features import PageDoc

    return PageDoc(url=url)


def site_rows(snapshot: dict[str, dict[str, tuple[str, float]]]) -> list[tuple[str, str, str, float]]:
    return [(search, domain(url), k, pos) for search, rows in snapshot.items() for k, (url, pos) in rows.items()]


def signals_for(search: str, k: str, url: str, pages: Pages, peers: list, rows) -> dict[str, float]:
    doc = pages.docs.get(k)
    if doc is None:
        signals = {name: float("nan") for name in CONTENT_FEATURES}
    else:
        signals = page_signals(search, doc, pages.html.get(k), peers)
    signals.update(site_signals(domain(url), k, search, rows))
    return signals


def training_groups(snapshot, pages: Pages, *, html_only: bool = True) -> list[Group]:
    rows = site_rows(snapshot)
    groups = []
    for search in sorted(snapshot):
        items = sorted(snapshot[search].items(), key=lambda item: item[1][1])
        peers = [pages.docs[k] for k, _ in items if k in pages.docs]
        sig, pos, urls = [], [], []
        for k, (url, position) in items:
            if html_only and k not in pages.docs:
                continue
            sig.append(signals_for(search, k, url, pages, peers, rows))
            pos.append(position)
            urls.append(k)
        if len(sig) >= 2:
            groups.append(Group(search, sig, pos, urls))
    return groups


def pair_counts(positions: list[float], scores: list[float]) -> tuple[int, int, int]:
    good = bad = tied = 0
    for i in range(len(positions)):
        for j in range(len(positions)):
            if positions[i] >= positions[j]:
                continue
            if scores[i] > scores[j]:
                good += 1
            elif scores[i] < scores[j]:
                bad += 1
            else:
                tied += 1
    return good, bad, tied


def family_cv(groups: list[Group], features, l2: float, families: dict[str, str]):
    """Out-of-fold scores per group, and per-fold weight signs."""
    folds = sorted(set(families[group.search] for group in groups))
    oof: dict[str, list[float]] = {}
    signs: list[dict[str, int]] = []
    models: dict[str, PairwiseModel] = {}
    for fold in folds:
        train = [group for group in groups if families[group.search] != fold]
        test = [group for group in groups if families[group.search] == fold]
        if not train or not test:
            continue
        model = fit_pairwise(train, features, l2=l2)
        models[fold] = model
        signs.append({name: (1 if w > 0 else -1 if w < 0 else 0) for name, w in zip(model.features, model.weights)})
        for group in test:
            oof[group.search] = [model.score(signals) for signals in group.signals]
    return oof, signs, models


def cv_pairwise(groups: list[Group], oof: dict[str, list[float]]) -> dict:
    good = bad = tied = 0
    per = {}
    for group in groups:
        if group.search not in oof:
            continue
        g, b, t = pair_counts(group.positions, oof[group.search])
        good, bad, tied = good + g, bad + b, tied + t
        per[group.search] = {"pairs": g + b + t, "correct": g, "pairwise": round(g / (g + b + t), 4) if g + b + t else None}
    total = good + bad + tied
    return {"pairs": total, "correct": good, "ties": tied, "pairwise": round(good / total, 4) if total else None, "per_search": per}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fetch_summary(pages: Pages) -> dict:
    stamps = sorted(entry["fetched_at"] for entry in pages.manifest if entry.get("fetched_at"))
    return {
        "urls": len(pages.manifest),
        "with_html": sum(1 for entry in pages.manifest if entry.get("file")),
        "fetched_from": stamps[0] if stamps else None,
        "fetched_to": stamps[-1] if stamps else None,
        "caveat": (
            "The snapshots' own stored HTML is private and was not available. Every page was fetched again "
            "on the dates above, 5 to 8 days after the Nov 6 and Nov 9 snapshots. A page may have changed since "
            "it was ranked. HTML is not in git."
        ),
    }


def train(html_dir: Path, out: Path) -> dict:
    from aeo.ranksim.snapshot_protocol import assign_families

    pages = load_pages(html_dir)
    nov06 = load_positions(NOV06)
    groups = training_groups(nov06, pages)
    families = assign_families(sorted(nov06))
    selection = []
    best = None
    for set_name, features in CANDIDATE_SETS.items():
        for l2 in CANDIDATE_L2:
            oof, _signs, _ = family_cv(groups, features, l2, families)
            result = cv_pairwise(groups, oof)
            row = {"features": set_name, "l2": l2, "pairs": result["pairs"], "pairwise": result["pairwise"]}
            selection.append(row)
            # Ties keep the earlier (smaller feature set, smaller penalty) candidate.
            if best is None or (result["pairwise"] or 0) > (best["pairwise"] or 0) + 1e-9:
                best = row
    features = CANDIDATE_SETS[best["features"]]
    oof, signs, fold_models = family_cv(groups, features, best["l2"], families)
    chosen_cv = cv_pairwise(groups, oof)
    stable = {name: (1 if all(s[name] == 1 for s in signs) else -1 if all(s[name] == -1 for s in signs) else 0) for name in features}
    model = fit_pairwise(groups, features, l2=best["l2"], trained_on="2026-11-06 Serper top 10, 30 searches, HTML fetched later")

    # New-page handicap and persistence slope from the earlier Oct 29 -> Nov 6 move (training period only).
    oct29 = load_positions(OCT29)
    rows = site_rows(nov06)
    entrant_pairs, offsets = [], []
    slope_pairs = []
    entrant_positions = []
    for search, now in nov06.items():
        before = oct29.get(search)
        if not before:
            continue
        fold = families[search]
        scorer = fold_models.get(fold, model)
        peers = [pages.docs[k] for k in now if k in pages.docs]
        sig = {k: signals_for(search, k, url, pages, peers, rows) for k, (url, _) in now.items()}
        for k, (_url, pos) in now.items():
            if k not in before:
                entrant_positions.append(pos)
                for r, (_u, rpos) in now.items():
                    if r in before:
                        entrant_pairs.append((sig[k], sig[r], pos < rpos))
                        offsets.append(scorer.score(sig[k]) - scorer.score(sig[r]))
        returning = [(k, before[k][1], pos) for k, (_u, pos) in now.items() if k in before]
        for a in returning:
            for b in returning:
                if a[1] < b[1]:
                    slope_pairs.append((b[1] - a[1], a[2] < b[2]))
    model.entrant_logit = fit_entrant_logit(model, entrant_pairs, offsets)
    slope = _fit_slope(slope_pairs)
    model.notes = [
        "PREDICTED only. Trained on the 2026-11-06 top 10 with HTML fetched on 2026-11-14.",
        "entrant_logit is fit on pages that newly entered a top 10 between 2026-10-29 and 2026-11-06.",
    ]
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / "model.json"
    model.save(model_path)
    training = {
        "protocol": "Features, penalty, new-page handicap and persistence slope are chosen on training data only. "
        "2026-11-09 positions are not read by this step.",
        "inputs": {
            "nov06_positions": {"file": str(NOV06.relative_to(REPO)), "sha256": _sha(NOV06)},
            "oct29_positions": {"file": str(OCT29.relative_to(REPO)), "sha256": _sha(OCT29), "use": "new-page handicap and persistence slope only"},
            "html": _fetch_summary(pages),
        },
        "coverage": {
            "nov06_rows": sum(len(rows_) for rows_ in nov06.values()),
            "nov06_rows_with_html": sum(len(group.signals) for group in groups),
            "searches": len(groups),
            "families": len(set(families.values())),
        },
        "selection": {
            "method": "query-family-out pairwise on 2026-11-06 (same snapshot, held-out family). Not accuracy.",
            "candidates": selection,
            "chosen": best,
            "chosen_cv": {k: v for k, v in chosen_cv.items() if k != "per_search"},
            "per_search_backtest": chosen_cv["per_search"],
            "sign_stable_across_folds": stable,
        },
        "entrant_prior": {
            "pairs": len(entrant_pairs),
            "entrant_above_rate": round(sum(1 for _, _, up in entrant_pairs if up) / len(entrant_pairs), 4) if entrant_pairs else None,
            "entrant_logit": round(model.entrant_logit, 4),
            "entrants": len(entrant_positions),
            "median_entrant_position": _median(entrant_positions),
            "note": "The Oct 29 list is an older export with heavy churn. It is used only for this handicap and the slope.",
        },
        "persistence_slope": {"pairs": len(slope_pairs), "slope": round(slope, 4)},
        "model_sha256": _sha(model_path),
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "training.json").write_text(json.dumps(training, indent=2) + "\n", encoding="utf-8")
    _write_fetch_manifest(pages, out)
    return training


def _write_fetch_manifest(pages: Pages, out: Path) -> None:
    rows = [
        {k: entry.get(k) for k in ("url", "fetched_at", "status", "final_url", "content_type", "sha256", "error")}
        for entry in pages.manifest
    ]
    (out / "fetch_manifest.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return float(ordered[mid]) if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _fit_slope(pairs: list[tuple[float, bool]]) -> float:
    """P(earlier-higher page is still higher) = sigmoid(slope * position gap)."""
    if not pairs:
        return 1.0
    slope = 0.5
    for _ in range(100):
        grad = hess = 0.0
        for gap, kept in pairs:
            p = 1.0 / (1.0 + math.exp(-slope * gap))
            grad += (p - (1.0 if kept else 0.0)) * gap
            hess += p * (1.0 - p) * gap * gap
        step = grad / (hess + 1e-9)
        slope -= step
        if abs(step) < 1e-9:
            break
    return slope


# -- scoring once on Nov 9 -----------------------------------------------------------------


def score(html_dir: Path, out: Path) -> dict:
    report_path = out / "report.json"
    if report_path.exists():
        raise SystemExit(f"{report_path} exists. The forward set is scored once.")
    training_path = out / "training.json"
    training = json.loads(training_path.read_text(encoding="utf-8"))
    model_path = out / "model.json"
    if _sha(model_path) != training["model_sha256"]:
        raise SystemExit("model.json does not match training.json. Nothing was scored.")
    model = PairwiseModel.load(model_path)
    no_prior = PairwiseModel.from_dict(dict(model.to_dict(), entrant_logit=0.0))
    pages = load_pages(html_dir)
    nov06 = load_positions(NOV06)
    nov09 = load_positions(NOV09)
    rows = site_rows(nov06)
    seen_anywhere = {k for search_rows in nov06.values() for k in search_rows}
    scored = []
    for search in sorted(nov09):
        before = nov06.get(search, {})
        peers = [pages.docs[k] for k in before if k in pages.docs]
        others = [signals_for(search, k, url, pages, peers, rows) for k, (url, _) in before.items()]
        for k, (url, pos9) in sorted(nov09[search].items(), key=lambda item: item[1][1]):
            row = {"search": search, "url": url, "position": pos9}
            if k in before:
                row.update(entrant=False, pos06=before[k][1], scorable=True)
            else:
                row.update(entrant=True, pos06=None, never_seen=k not in seen_anywhere, scorable=k in pages.docs)
                if row["scorable"]:
                    sig = signals_for(search, k, url, pages, peers, rows)
                    row["predicted"] = round(expected_position(model, sig, others, is_new=True), 4)
                    row["predicted_no_prior"] = round(expected_position(no_prior, sig, others, is_new=True), 4)
            scored.append(row)
    by_search = defaultdict(list)
    for row in scored:
        by_search[row["search"]].append(row)

    def value(row, scorer):
        if not row["entrant"]:
            return -row["pos06"]
        if scorer == "bottom" or not row["scorable"]:
            return -BOTTOM
        return -row[scorer]

    scorers = {"hybrid_with_prior": "predicted", "hybrid_no_prior": "predicted_no_prior", "persistence_bottom": "bottom"}
    slices = {}
    for name, field_name in scorers.items():
        slices[name] = {
            mode: _slice(by_search, lambda r, f=field_name: value(r, f), mode)
            for mode in ("all", "returning_only", "entrant_vs_returning", "entrant_vs_entrant", "never_seen_vs_returning", "never_seen_vs_never_seen")
        }
    entrants = [row for row in scored if row["entrant"]]
    scorable = [row for row in entrants if row["scorable"]]
    mae = {}
    if scorable:
        mae["hybrid_with_prior"] = round(sum(abs(r["predicted"] - r["position"]) for r in scorable) / len(scorable), 3)
        mae["hybrid_no_prior"] = round(sum(abs(r["predicted_no_prior"] - r["position"]) for r in scorable) / len(scorable), 3)
        median_prior = training["entrant_prior"]["median_entrant_position"] or 10.0
        mae["constant_training_median"] = round(sum(abs(median_prior - r["position"]) for r in scorable) / len(scorable), 3)
        mae["constant_training_median_value"] = median_prior
    report = {
        "label": "PREDICTED positions scored once against 2026-11-09. Not a general forecast.",
        "training_sha256": _sha(training_path),
        "model_sha256": training["model_sha256"],
        "forward_positions": {"file": str(NOV09.relative_to(REPO)), "sha256": _sha(NOV09)},
        "html": training["inputs"]["html"],
        "coverage": {
            "forward_rows": len(scored),
            "returning_rows": len(scored) - len(entrants),
            "entrant_rows": len(entrants),
            "entrant_rows_scorable": len(scorable),
            "entrant_rows_without_html": len(entrants) - len(scorable),
            "never_seen_entrants": sum(1 for r in entrants if r.get("never_seen")),
            "never_seen_scorable": sum(1 for r in scorable if r.get("never_seen")),
            "searches": len(by_search),
            "note": "Entrants without fetched HTML are not scored by the model; in every slice they sit at the bottom, tied, for all scorers, and pairs touching them are dropped from the entrant slices.",
        },
        "rules": {
            "pairs": "Pairs of pages on the 2026-11-09 list for one search whose positions differ. A tied score counts as not correct.",
            "returning": "A page that was in that search's 2026-11-06 top 10 is anchored at its 2026-11-06 position (persistence).",
            "entrant": "A page new to that search's top 10 is placed at its PREDICTED position: 1 plus the expected number of the 2026-11-06 pages that beat it.",
            "persistence_bottom": "Reference: every entrant below every returning page, entrants tied. Same as persistence in evals/forward-2026-11-09/report.json.",
            "never_seen": "An entrant URL that was in no 2026-11-06 search at all, the closest case to a brand new draft.",
        },
        "scorers": slices,
        "entrant_position_mae": mae,
        "rows": scored,
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _in_slice(left: dict, right: dict, mode: str) -> bool:
    if mode == "all":
        return True
    if (left["entrant"] and not left["scorable"]) or (right["entrant"] and not right["scorable"]):
        return False
    a, b = left["entrant"], right["entrant"]
    if mode == "returning_only":
        return not a and not b
    if mode == "entrant_vs_returning":
        return a != b
    if mode == "entrant_vs_entrant":
        return a and b
    na, nb = bool(left.get("never_seen")), bool(right.get("never_seen"))
    if mode == "never_seen_vs_returning":
        return (na and not b) or (nb and not a)
    if mode == "never_seen_vs_never_seen":
        return na and nb
    raise ValueError(mode)


def _slice(by_search, value, mode: str) -> dict:
    good = bad = tied = 0
    used = 0
    for rows in by_search.values():
        g = b = t = 0
        for left in rows:
            for right in rows:
                if left["position"] >= right["position"] or not _in_slice(left, right, mode):
                    continue
                lv, rv = value(left), value(right)
                if lv > rv:
                    g += 1
                elif lv < rv:
                    b += 1
                else:
                    t += 1
        if g + b + t:
            used += 1
        good, bad, tied = good + g, bad + b, tied + t
    total = good + bad + tied
    return {"searches": used, "pairs": total, "correct": good, "wrong": bad, "ties": tied, "pairwise": round(good / total, 4) if total else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.predict_eval")
    parser.add_argument("step", choices=("train", "score"))
    parser.add_argument("--html-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.step == "train":
        result = train(args.html_dir, args.out)
        print(json.dumps({k: result[k] for k in ("coverage", "entrant_prior", "persistence_slope")}, indent=2))
        print(json.dumps(result["selection"]["chosen"]), json.dumps(result["selection"]["chosen_cv"]))
    else:
        result = score(args.html_dir, args.out)
        print(json.dumps(result["coverage"], indent=2))
        for name, slices in result["scorers"].items():
            print(name, {mode: (s["correct"], s["pairs"], s["pairwise"]) for mode, s in slices.items()})
        print(json.dumps(result["entrant_position_mae"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
