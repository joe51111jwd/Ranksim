"""Design evaluation for T1 placement. Design days only (<= 2026-11-17). Output label: PREDICTED.

Reproduces every number in evals/claude/t1-placement/notes.md:

  python scripts/claude/t1_design_eval.py            # writes design_eval.json and design_eval.md
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import metrics as m  # noqa: E402
from scripts.claude import rsdata  # noqa: E402
from scripts.claude import t1_common as c  # noqa: E402
from scripts.claude import t1_placement as t1  # noqa: E402

OUT_DIR = ROOT / "evals" / "claude" / "t1-placement"
CLASSES = ("new", "reentrant")
SURVIVOR = {"anchors": [{"feature": "last_cons", "max": 10, "offset": 0}], "default": 7}
BASELINES = ("b_dp", "b_c7", "b_c11")
METRICS = {"band3": m.band3_hit, "err3": m.err3_hit}


def anchor(feature: str, offset: int = 0, maximum: int = 10) -> dict:
    return {"feature": feature, "max": maximum, "offset": offset}


def spec(new: dict, reentrant: dict, clip=(2, 9)) -> dict:
    return {"clip": list(clip), "new": new, "reentrant": reentrant, "survivor": SURVIVOR}


# ---------------------------------------------------------------- fixed variants
# Every variant tried is listed here and reported. None is fitted on outcome rows.
REENT_DEFAULT = {"anchors": [anchor("deep_key", 0, 30), anchor("last_top_pos")], "default": 8}


def fixed_variants() -> dict[str, dict]:
    v: dict[str, dict] = {}
    for k in range(5, 10):
        v[f"const{k}"] = spec({"anchors": [], "default": k}, {"anchors": [], "default": k}, clip=(1, 11))
    v["dp_clip_else7"] = spec({"anchors": [anchor("dp")], "default": 7}, {"anchors": [anchor("dp")], "default": 7})
    v["dp_clip_else8"] = spec({"anchors": [anchor("dp")], "default": 8}, {"anchors": [anchor("dp")], "default": 8})
    v["dp_clip_else9"] = spec({"anchors": [anchor("dp")], "default": 9}, {"anchors": [anchor("dp")], "default": 9})
    v["dp+1_else8"] = spec({"anchors": [anchor("dp", 1)], "default": 8}, {"anchors": [anchor("dp", 1)], "default": 8})
    for d in (7, 8, 9):
        v[f"dp_xqk_else{d}"] = spec({"anchors": [anchor("dp"), anchor("xq_key_min")], "default": d}, REENT_DEFAULT)
        v[f"dp_xqk+1_else{d}"] = spec({"anchors": [anchor("dp"), anchor("xq_key_min", 1)], "default": d}, REENT_DEFAULT)
        v[f"xqk_dp_else{d}"] = spec({"anchors": [anchor("xq_key_min"), anchor("dp")], "default": d}, REENT_DEFAULT)
        v[f"med_dp_xqk_xqs_else{d}"] = spec(
            {"anchors": [anchor("dp"), anchor("xq_key_min"), anchor("xq_site_min")], "combine": "median", "default": d}, REENT_DEFAULT
        )
        v[f"med_dp_xqk_else{d}"] = spec({"anchors": [anchor("dp"), anchor("xq_key_min")], "combine": "median", "default": d}, REENT_DEFAULT)
        v[f"dp_xqk_else{d}_ugc-2"] = spec(
            {"anchors": [anchor("dp"), anchor("xq_key_min")], "default": d, "ugc_shift": -2}, REENT_DEFAULT
        )
        v[f"dp_deepsite_xqk_else{d}"] = spec(
            {"anchors": [anchor("dp"), anchor("deep_site", 0, 30), anchor("xq_key_min")], "default": d}, REENT_DEFAULT
        )
    # reentrant-only variants (new part fixed to dp_xqk_else8)
    new8 = {"anchors": [anchor("dp"), anchor("xq_key_min")], "default": 8}
    v["re:last_top"] = spec(new8, {"anchors": [anchor("last_top_pos")], "default": 8})
    v["re:last_top+1"] = spec(new8, {"anchors": [anchor("last_top_pos", 1)], "default": 8})
    v["re:deep_last"] = spec(new8, REENT_DEFAULT)
    v["re:deep_last_dp"] = spec(new8, {"anchors": [anchor("deep_key", 0, 30), anchor("last_top_pos"), anchor("dp")], "default": 8})
    v["re:med_deep_last_xqk"] = spec(
        new8, {"anchors": [anchor("deep_key", 0, 30), anchor("last_top_pos"), anchor("xq_key_min")], "combine": "median", "default": 8}
    )
    # err3-efficient range: on rows with truth 1-10, 7 covers a superset of 8-10's err3 window and 4 of 1-3's.
    v["clip47:dp_xqk|deep_last"] = {
        "new": {"anchors": [anchor("dp"), anchor("xq_key_min")], "default": 7, "clip": [4, 7]},
        "reentrant": {"anchors": [anchor("deep_key", 0, 30), anchor("last_top_pos")], "default": 7, "clip": [4, 7]},
        "survivor": SURVIVOR,
    }
    v["clip47:dp|deep_last"] = {
        "new": {"anchors": [anchor("dp")], "default": 7, "clip": [4, 7]},
        "reentrant": {"anchors": [anchor("deep_key", 0, 30), anchor("last_top_pos")], "default": 7, "clip": [4, 7]},
        "survivor": SURVIVOR,
    }
    v["top4:dp<=3|deep_last"] = {
        "new": {"anchors": [{"feature": "dp", "max": 3, "value": 4}], "default": 7, "clip": [2, 9]},
        "reentrant": REENT_DEFAULT | {"clip": [2, 9]},
        "survivor": SURVIVOR,
    }
    v["top:dp<=3,xqk<=3->4|deep_last"] = {
        "new": {"anchors": [{"feature": "dp", "max": 3, "value": 4}, {"feature": "xq_key_min", "max": 3, "value": 4}], "default": 7, "clip": [2, 9]},
        "reentrant": REENT_DEFAULT | {"clip": [2, 9]},
        "survivor": SURVIVOR,
    }
    # reentrant clip windows on the deep -> last-top anchor (new part = clip47:dp, err3-safe)
    for lo, hi in ((2, 9), (2, 8), (3, 8), (2, 7), (3, 7), (4, 7)):
        v[f"reclip{lo}{hi}:deep_last"] = {
            "new": {"anchors": [anchor("dp")], "default": 7, "clip": [4, 7]},
            "reentrant": {"anchors": [anchor("deep_key", 0, 30), anchor("last_top_pos")], "default": 7, "clip": [lo, hi]},
            "survivor": SURVIVOR,
        }
    # new: dp -> deep_site fallback (only differs where the site is in the deep list but not yesterday's top 10)
    v["band:dp_deepsite_else9|deep_last"] = {
        "new": {"anchors": [anchor("dp"), anchor("deep_site", 0, 30)], "default": 9, "clip": [2, 9]},
        "reentrant": REENT_DEFAULT | {"clip": [2, 9]},
        "survivor": SURVIVOR,
    }
    v["clip27:dp_xqk|clip27:deep_last"] = {
        "new": {"anchors": [anchor("dp"), anchor("xq_key_min")], "default": 7, "clip": [2, 7]},
        "reentrant": {"anchors": [anchor("deep_key", 0, 30), anchor("last_top_pos")], "default": 7, "clip": [2, 7]},
        "survivor": SURVIVOR,
    }
    return v


# ---------------------------------------------------------------- forward-chained selection grid
def grid() -> list[dict]:
    """Rule specs the forward-chained selector chooses from (new and reentrant chosen separately)."""
    clips = ([2, 9], [4, 7], [2, 7])
    new_anchor_opts = (
        lambda f: {"feature": f, "max": 3, "value": 3},
        lambda f: {"feature": f, "max": 3, "value": 4},
        lambda f: anchor(f, 0),
        lambda f: anchor(f, 1),
    )
    news = []
    for order in ([], ["dp"], ["xq_key_min"], ["dp", "xq_key_min"], ["xq_key_min", "dp"]):
        for opts in itertools.product(new_anchor_opts, repeat=len(order)):
            for clip in clips:
                for d in (7, 8, 9):
                    for ugc in (0, -2):
                        news.append({"anchors": [o(f) for f, o in zip(order, opts, strict=True)], "default": d, "ugc_shift": ugc, "clip": clip})
    reents = []
    for order in (["last_top_pos"], ["deep_key", "last_top_pos"], ["last_top_pos", "dp"], ["deep_key", "last_top_pos", "dp"],
                  ["deep_key", "last_top_pos", "xq_key_min"]):
        for offs in itertools.product((0, 1), repeat=len(order)):
            for clip in clips:
                for d in (7, 8, 9):
                    reents.append(
                        {"anchors": [anchor(f, o, 30 if f == "deep_key" else 10) for f, o in zip(order, offs, strict=True)], "default": d, "clip": clip}
                    )
    return news, reents


def add_baselines(table: pd.DataFrame) -> pd.DataFrame:
    table = table.copy()
    table["b_dp"] = table["domain_prior"].astype(int)
    table["b_c7"] = 7
    table["b_c11"] = 11
    table["r_c9"] = 9  # reference only (best band3 constant on every prefix of design days), never a "best baseline"
    return table


def predict_table(table: pd.DataFrame, params: dict) -> list[int]:
    """Row-wise rule on precomputed features (identical to t1.predict_rows, which recomputes them)."""
    cols = [col for col in table.columns]
    return [t1.rule_predict(rec, params) for rec in table[cols].to_dict("records")]


def hits(table: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    out = pd.DataFrame(index=table.index)
    for name, fn in METRICS.items():
        out[name] = [int(fn(p, t)) for p, t in zip(table[pred_col], table["truth"], strict=True)]
    return out


def score(table: pd.DataFrame, pred_col: str) -> dict:
    """Rate and paired difference vs the best of the three baselines, per metric."""
    res = {"n": int(len(table)), "queries": int(table["query"].nunique())}
    if table.empty:
        return res
    frame = table[["query"]].copy()
    for name, fn in METRICS.items():
        frame[f"{name}_model"] = [int(fn(p, t)) for p, t in zip(table[pred_col], table["truth"], strict=True)]
        best, best_hits = None, -1
        for base in BASELINES:
            frame[f"{name}_{base}"] = [int(fn(p, t)) for p, t in zip(table[base], table["truth"], strict=True)]
            total = int(frame[f"{name}_{base}"].sum())
            if total > best_hits:
                best, best_hits = base, total
        res[name] = {
            "model": m.bootstrap_rate(frame, f"{name}_model"),
            "baselines": {base: m.bootstrap_rate(frame, f"{name}_{base}") for base in BASELINES},
            "best_baseline": best,
            "diff_vs_best": m.bootstrap_difference(frame, f"{name}_model", f"{name}_{best}"),
            "diff_vs": {base: m.bootstrap_difference(frame, f"{name}_model", f"{name}_{base}") for base in BASELINES},
        }
        if "r_c9" in table:
            frame[f"{name}_r_c9"] = [int(fn(p, t)) for p, t in zip(table["r_c9"], table["truth"], strict=True)]
            res[name]["ref_c9"] = m.bootstrap_rate(frame, f"{name}_r_c9")
            res[name]["diff_vs_c9"] = m.bootstrap_difference(frame, f"{name}_model", f"{name}_r_c9")
    return res


def eval_sets(design: pd.DataFrame, emu: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Named evaluation slices. Keys: '<day>/<group>' plus the a70 emulation."""
    sets = {}
    for (day, grp), g in design.groupby(["day", "grp"]):
        sets[f"{day}/{grp}"] = g
    sets["emu-a70/o30-2026-11-17"] = emu
    sets["pool:o30-consensus-days(11-16,11-17)"] = design[(design["grp"] == "o30") & design["day"].isin(["2026-11-16", "2026-11-17"])]
    sets["pool:all-design-slices"] = design
    return sets


def objective(frame: pd.DataFrame, pred: list[int]) -> tuple[int, int]:
    """Training objective: hits on both metrics minus the best baseline per metric (min of the two gains, then sum)."""
    gains = []
    for name, fn in METRICS.items():
        model = sum(int(fn(p, t)) for p, t in zip(pred, frame["truth"], strict=True))
        best = max(sum(int(fn(b, t)) for b, t in zip(frame[base], frame["truth"], strict=True)) for base in BASELINES)
        gains.append(model - best)
    return (min(gains), sum(gains))


def select(train: pd.DataFrame, klass: str, candidates: list[dict]) -> dict:
    """Pick the class spec with the best (min gain, total gain) on training rows. Ties go to the earlier (simpler) candidate."""
    rows = train[train["klass"] == klass]
    records = rows.to_dict("records")
    best, best_val = candidates[0], None
    for cand in candidates:
        params = {"clip": [2, 9], klass: cand}
        pred = [t1.rule_predict(rec, params) for rec in records]
        val = objective(rows, pred) if len(rows) else (0, 0)
        if best_val is None or val > best_val:
            best, best_val = cand, val
    return {"spec": best, "train_objective": list(best_val) if best_val else None, "train_rows": int(len(rows))}


def best_constant(train: pd.DataFrame, klass: str) -> int:
    """Constant 1-11 with the most band3 + err3 hits on training rows (ties to the smaller error vs 7)."""
    rows = train[train["klass"] == klass]
    best, best_val = 7, -1
    for k in sorted(range(1, 12), key=lambda x: abs(x - 7)):
        val = sum(int(m.band3_hit(k, t)) + int(m.err3_hit(k, t)) for t in rows["truth"])
        if val > best_val:
            best, best_val = k, val
    return best


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    design = add_baselines(c.design_table())
    emu = add_baselines(c.emulation_table())
    assert design["day"].max() <= rsdata.DESIGN_MAX_DATE
    sets = eval_sets(design, emu)
    report: dict = {"label": "PREDICTED", "design_max_date": rsdata.DESIGN_MAX_DATE, "fixed": {}, "forward": {}, "const_fc": {}}

    # 1. fixed variants on every slice and class
    variants = fixed_variants()
    for vname, params in variants.items():
        for sname, frame in sets.items():
            frame = frame.copy()
            frame["pred"] = predict_table(frame, params)
            for klass in CLASSES:
                sub = frame[frame["klass"] == klass]
                if len(sub):
                    report["fixed"].setdefault(vname, {}).setdefault(sname, {})[klass] = score(sub, "pred")

    # 2. forward-chained selection and best-constant reference
    news, reents = grid()
    fc_days = ["2026-11-11", "2026-11-13", "2026-11-16", "2026-11-17"]
    for day in fc_days:
        train = design[design["day"] < day]
        chosen = {"new": select(train, "new", news), "reentrant": select(train, "reentrant", reents)}
        params = {"clip": [2, 9], "new": chosen["new"]["spec"], "reentrant": chosen["reentrant"]["spec"], "survivor": SURVIVOR}
        consts = {klass: best_constant(train, klass) for klass in CLASSES}
        cparams = spec({"anchors": [], "default": consts["new"]}, {"anchors": [], "default": consts["reentrant"]}, clip=(1, 11))
        targets = {name: frame for name, frame in sets.items() if name.startswith(day)}
        if day == "2026-11-17":
            targets["emu-a70/o30-2026-11-17"] = sets["emu-a70/o30-2026-11-17"]
        for sname, frame in targets.items():
            frame = frame.copy()
            frame["pred"] = predict_table(frame, params)
            frame["cpred"] = predict_table(frame, cparams)
            for klass in CLASSES:
                sub = frame[frame["klass"] == klass]
                if len(sub):
                    report["forward"].setdefault(sname, {})[klass] = {"chosen": chosen[klass], "score": score(sub, "pred")}
                    report["const_fc"].setdefault(sname, {})[klass] = {"constant": consts[klass], "score": score(sub, "cpred")}
    # 3. forward-chained choice of the reentrant clip window (anchors fixed: deep list, then last top-10 position)
    windows = [[2, 9], [2, 8], [3, 8], [2, 7], [3, 7], [4, 7]]
    wcands = [{"anchors": [anchor("deep_key", 0, 30), anchor("last_top_pos")], "default": 7, "clip": w} for w in windows]
    report["fc_window"] = {}
    for day in fc_days[1:]:
        train = design[design["day"] < day]
        chosen = select(train, "reentrant", wcands)
        params = {"new": {"anchors": [], "default": 7}, "reentrant": chosen["spec"], "survivor": SURVIVOR}
        targets = {name: frame for name, frame in sets.items() if name.startswith(day)}
        if day == "2026-11-17":
            targets["emu-a70/o30-2026-11-17"] = sets["emu-a70/o30-2026-11-17"]
        for sname, frame in targets.items():
            sub = frame[frame["klass"] == "reentrant"].copy()
            if len(sub):
                sub["pred"] = predict_table(sub, params)
                report["fc_window"][sname] = {"chosen": chosen, "score": score(sub, "pred")}
    # 4. the frozen spec, run end to end through t1.predict_rows (features recomputed from history only)
    report["frozen"] = frozen_check(design, emu, sets)
    with open(OUT_DIR / "design_eval.json", "w") as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
    write_markdown(report)


def frozen_check(design: pd.DataFrame, emu: pd.DataFrame, sets: dict) -> dict:
    params = t1.load_params()
    sha = hashlib.sha256(t1.PARAMS_PATH.read_bytes()).hexdigest()
    rows = c.design_rows()
    cases = [(day, rows, design[design["day"] == day]) for day in c.DESIGN_DAYS]
    cases.append(("emu-a70/o30-2026-11-17", c.emulation_rows(), emu))
    checks = {"params_sha256": sha, "equal_to_table_path": True, "row_independent": True, "seal_assert_fires": False}
    preds: dict[str, pd.Series] = {}
    for name, source, table in cases:
        day = table["day"].iloc[0] if name.startswith("emu") is False else "2026-11-17"
        history = source[source["day"] < day]
        cols = ["query", "key", "url", "site", "day"]
        targets = table[cols].copy()
        targets["day"] = day
        full = t1.predict_rows(history, targets)
        # same rule through the precomputed feature table
        checks["equal_to_table_path"] &= full == predict_table(table, params)
        # each row alone, in reverse order: a prediction must not depend on other target rows
        hist = t1.History(history)
        single = [t1.predict_rows(history, targets.iloc[[i]], hist=hist)[0] for i in range(len(targets) - 1, -1, -1)][::-1]
        checks["row_independent"] &= full == single
        preds[name] = pd.Series(full, index=table.index)
    try:
        t1.predict_rows(rows[rows["day"] <= "2026-11-16"], design[design["day"] == "2026-11-16"][["query", "key", "url", "site", "day"]])
    except AssertionError:
        checks["seal_assert_fires"] = True
    # Nov 16: the deep list was pulled that day, so no Nov 16 target may carry a deep feature
    nov16 = design[design["day"] == "2026-11-16"]
    checks["nov16_no_deep_features"] = bool(((nov16["deep_key"] == 99) & (nov16["deep_site"] == 99)).all())
    # emulation history: only the Nov 16 q100 pull and the Nov 16 deep list
    emu_hist = c.emulation_rows()
    emu_hist = emu_hist[emu_hist["day"] < "2026-11-17"]
    checks["emu_history_sources"] = sorted(emu_hist["source"].unique().tolist())
    checks["emu_history_days"] = sorted(emu_hist["day"].unique().tolist())
    checks["design_rows_max_day"] = str(rows["day"].max())
    out = {"checks": checks, "scores": {}}
    for sname, frame in sets.items():
        frame = frame.copy()
        if sname.startswith("emu"):
            frame["pred"] = preds["emu-a70/o30-2026-11-17"].reindex(frame.index)
        else:
            frame["pred"] = pd.concat([preds[d] for d in c.DESIGN_DAYS]).reindex(frame.index)
        for klass in CLASSES:
            sub = frame[frame["klass"] == klass]
            if len(sub):
                out["scores"].setdefault(sname, {})[klass] = score(sub, "pred")
    return out


def _cell(res: dict, metric: str) -> str:
    r = res[metric]
    best = r["best_baseline"]
    return f"{m.fmt_rate(r['model'])} | {best[2:]} {m.fmt_rate(r['baselines'][best])} | {m.fmt_diff(r['diff_vs_best'])}"


def write_markdown(report: dict) -> None:
    lines = ["# T1 design evaluation tables (PREDICTED)", "", "Generated by scripts/claude/t1_design_eval.py. Design days <= 2026-11-17 only.", ""]
    lines += ["## Fixed variants: pooled summary", "", "Hits summed over slices, model minus best baseline per slice summed (rows).", ""]
    lines += ["| variant | class | n | band3 model | band3 best-base | err3 model | err3 best-base | slices with both diff lo > 0 |", "|---|---|---|---|---|---|---|---|"]
    for vname, by_set in report["fixed"].items():
        for klass in CLASSES:
            tot = {"n": 0, "b": 0, "bb": 0, "e": 0, "eb": 0, "pass": 0, "sl": 0}
            for sname, res in by_set.items():
                if klass not in res or sname.startswith(("emu", "pool:")):
                    continue
                r = res[klass]
                tot["n"] += r["n"]
                tot["b"] += r["band3"]["model"]["hits"]
                tot["bb"] += r["band3"]["baselines"][r["band3"]["best_baseline"]]["hits"]
                tot["e"] += r["err3"]["model"]["hits"]
                tot["eb"] += r["err3"]["baselines"][r["err3"]["best_baseline"]]["hits"]
                tot["sl"] += 1
                tot["pass"] += int(r["band3"]["diff_vs_best"]["lo"] > 0 and r["err3"]["diff_vs_best"]["lo"] > 0)
            if tot["n"]:
                lines.append(
                    f"| {vname} | {klass} | {tot['n']} | {tot['b']} | {tot['bb']} | {tot['e']} | {tot['eb']} | {tot['pass']}/{tot['sl']} |"
                )
    fz = report.get("frozen")
    if fz:
        lines += ["## Frozen spec (params.json) run end to end through t1_placement.predict_rows", ""]
        lines += [f"Checks: `{json.dumps(fz['checks'])}`", ""]
        lines += ["| slice | class | n | metric | model | dp | c7 | c11 | model - dp | model - c7 | model - c11 | best baseline | ref c9 | model - c9 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for sname, by_class in fz["scores"].items():
            for klass, r in by_class.items():
                for metric in METRICS:
                    x = r[metric]
                    b = x["baselines"]
                    dv = x["diff_vs"]
                    lines.append(
                        f"| {sname} | {klass} | {r['n']} | {metric} | {m.fmt_rate(x['model'])} | {m.fmt_rate(b['b_dp'])} | {m.fmt_rate(b['b_c7'])} | "
                        f"{m.fmt_rate(b['b_c11'])} | {m.fmt_diff(dv['b_dp'])} | {m.fmt_diff(dv['b_c7'])} | {m.fmt_diff(dv['b_c11'])} | {x['best_baseline'][2:]} | {m.fmt_rate(x['ref_c9'])} | {m.fmt_diff(x['diff_vs_c9'])} |"
                    )
        lines.append("")
    lines += ["", "## Forward-chained selection (fit on earlier design days only)", ""]
    lines += ["| slice | class | n | band3 model | band3 best baseline | band3 diff | err3 model | err3 best baseline | err3 diff | chosen spec |", "|---|---|---|---|---|---|---|---|---|---|"]
    for sname, by_class in report["forward"].items():
        for klass, item in by_class.items():
            r = item["score"]
            lines.append(f"| {sname} | {klass} | {r['n']} | {_cell(r, 'band3')} | {_cell(r, 'err3')} | `{json.dumps(item['chosen']['spec'])}` |")
    lines += ["", "## Forward-chained reentrant clip window (anchors deep list -> last top-10 position, window chosen on earlier days)", ""]
    lines += ["| slice | n | band3 model | band3 best baseline | band3 diff | err3 model | err3 best baseline | err3 diff | chosen clip | train objective (min gain, sum gain) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for sname, item in report.get("fc_window", {}).items():
        r = item["score"]
        lines.append(f"| {sname} | {r['n']} | {_cell(r, 'band3')} | {_cell(r, 'err3')} | {item['chosen']['spec']['clip']} | {item['chosen']['train_objective']} |")
    lines += ["", "## Best constant fitted on earlier design days (reference)", ""]
    lines += ["| slice | class | constant | band3 model | band3 best baseline | band3 diff | err3 model | err3 best baseline | err3 diff |", "|---|---|---|---|---|---|---|---|---|"]
    for sname, by_class in report["const_fc"].items():
        for klass, item in by_class.items():
            r = item["score"]
            lines.append(f"| {sname} | {klass} | {item['constant']} | {_cell(r, 'band3')} | {_cell(r, 'err3')} |")
    lines += ["", "## Fixed variants: every slice", "", "diff = model minus the best of {domain prior, 7, 11} on that slice and metric, query bootstrap 95% CI.", ""]
    lines += ["| variant | slice | class | n | band3 model | band3 best baseline | band3 diff | err3 model | err3 best baseline | err3 diff |", "|---|---|---|---|---|---|---|---|---|---|"]
    for vname, by_set in report["fixed"].items():
        for sname, by_class in by_set.items():
            for klass, r in by_class.items():
                lines.append(f"| {vname} | {sname} | {klass} | {r['n']} | {_cell(r, 'band3')} | {_cell(r, 'err3')} |")
    (OUT_DIR / "design_eval.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
