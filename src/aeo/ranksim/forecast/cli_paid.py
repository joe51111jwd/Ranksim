"""The paid cohort commands: python -m aeo.ranksim forecast paid ... and forecast cohort-backtest ...

paid cells        write the REAL training-set folder from a cohort, its launch pages and a universe (free)
paid synthetic    write a SYNTHETIC training-set folder from the simulated world (free)
paid join         join the train split with its past result pages and print what was kept and dropped (free)
paid train        train a bundle on the train split (free; forecast train --data paid does the same on the cache)
cohort-backtest freeze   freeze the cohort backtest design (free, scores nothing)
cohort-backtest score    score the test split once under the frozen design (free)

What is REAL: past result pages and the positions on them, the cohort evidence and its launch pages.
The launch date is an ESTIMATE with its uncertainty. What is PREDICTED: every forecast and content
score. What is SYNTHETIC: every table from `paid synthetic`, every bundle trained on it and every
number scored on it. Every line printed for SYNTHETIC tables says SYNTHETIC.

Nothing here spends money or calls a paid service. The three paid pulls are `pull past-pages`,
`pull traffic` and `pull positions` (cli_data.py), each a dry run until --execute.

Commands plug into cli.py through REGISTRARS. This module imports cli.py, never the other way round
at import time.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from . import paidset
from . import trainingset as ts
from .cli import EXIT_CHECK_FAILED, EXIT_OK, PROG, Context
from .types import REAL, SYNTHETIC


def _scorer(args: argparse.Namespace) -> Any:
    """The content scorer asked for. None (score only) means the backend the bundle was trained with."""
    from .content import ContentScorer

    backend = getattr(args, "content_backend", "auto")
    return None if backend is None else ContentScorer(backend)


def _set_dir(ctx: Context, args: argparse.Namespace) -> Path:
    return Path(args.set) if getattr(args, "set", None) else paidset.default_set_dir(ctx.paths)


def _tag(label: str) -> str:
    return f"({label})"


# ----------------------------------------------------------------------------------------------
# paid cells and paid synthetic
# ----------------------------------------------------------------------------------------------


def cmd_paid_cells(ctx: Context, args: argparse.Namespace) -> int:
    import pandas as pd

    from .sources.newsites import load_pages
    from .sources.universe import load_universe

    out = Path(args.out) if args.out else paidset.default_set_dir(ctx.paths)
    if (out / ts.CELLS_FILE).exists():
        return ctx.fail(f"{out} already holds a cell table. A training set is never replaced: pass a new --out.")
    cohort = pd.read_parquet(args.cohort)
    pages = load_pages(Path(args.pages))
    universe = [row for path in args.universe for row in load_universe(path)]
    if not universe:
        return ctx.fail("The universe tables hold no search.")
    try:
        result = ts.write_cells_set(out, cohort, pages, universe, which=args.which,
                                    sources={"cohort": str(args.cohort), "pages": str(args.pages),
                                             "universe": ";".join(str(p) for p in args.universe)})
    except ValueError as error:
        return ctx.fail(str(error))
    meta = result["meta"]
    lines = [f"Wrote the REAL training-set folder {out}: {result['cells']} cells (site, target search) {_tag(REAL)}.",
             "Target searches were picked by the frozen label-blind rule from launch-era page text. No ranking was read.",
             "Split: " + ", ".join(f"{k} {v}" for k, v in sorted(meta["split_counts"].items())) + f" {_tag(REAL)}.",
             f"Sites on both sides: {meta['split']['sites_on_both_sides']}. Search groups on both sides: "
             f"{meta['split']['groups_on_both_sides']}.",
             f"Sites without launch-era text: {meta['counts']['sites_without_text']}. Sites without a launch date: "
             f"{meta['counts']['sites_without_launch_date']}.",
             f"Searches listed in more than one niche: {meta['counts'].get('searches_in_more_than_one_niche', 0)}. Each is "
             "one search: a site gets at most one cell for it.",
             "Launch dates are ESTIMATES with their uncertainty in days.",
             f"Next: {PROG} paid join --set {out}"]
    ctx.emit({**result, "label": REAL}, lines)
    return EXIT_OK


def cmd_paid_synthetic(ctx: Context, args: argparse.Namespace) -> int:
    from .synth import World

    out = Path(args.out) if args.out else paidset.default_set_dir(ctx.paths, synthetic=True)
    if (out / ts.CELLS_FILE).exists():
        return ctx.fail(f"{out} already holds a cell table. Pass a new --out.")
    world = World(args.seed, niches=args.niches, searches_per_niche=args.searches_per_niche, new_sites=args.new_sites)
    made = ts.synthetic_training_set(world, out_dir=out)
    counts = made.cells["split"].value_counts().to_dict()
    lines = [f"SYNTHETIC. Wrote the training-set folder {out} from the simulated world, seed {args.seed}.",
             f"SYNTHETIC. Cells: {len(made.cells)}. Split: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ".",
             f"SYNTHETIC. Past result pages: {made.panel_rows} rows in {made.paths['panel']}.",
             "SYNTHETIC. Nothing built from this folder is ever accuracy.",
             f"SYNTHETIC. Next: {PROG} paid join --set {out}"]
    ctx.emit({"label": SYNTHETIC, "folder": str(out), "cells": int(len(made.cells)), "split_counts": counts,
              "paths": made.paths}, lines)
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# paid join and paid train
# ----------------------------------------------------------------------------------------------


def cmd_paid_join(ctx: Context, args: argparse.Namespace) -> int:
    join = paidset.join_upload_cells(_set_dir(ctx, args), splits=("train",), panel_dir=args.panel_dir, paths=ctx.paths,
                                     content_scorer=_scorer(args), want_readings=True,
                                     log=lambda message: ctx.err.write(message + "\n"))
    lines = join.lines()
    lines.append(f"Only the train split was joined. The test split is read once, by {PROG} cohort-backtest score "
                 f"{_tag(join.label)}.")
    ctx.emit(join.to_dict(), lines)
    return EXIT_OK


def cmd_paid_train(ctx: Context, args: argparse.Namespace) -> int:
    from .cli_forecasting import train_lines

    result = paidset.train_paid(ctx.paths, args.name, folder=_set_dir(ctx, args), panel_dir=args.panel_dir,
                                content_scorer=_scorer(args), log=lambda message: ctx.err.write(message + "\n"))
    lines = train_lines(result)
    if result.label == SYNTHETIC:
        lines = [line if SYNTHETIC in line else f"{line} (SYNTHETIC)" for line in lines]
    ctx.emit(result.to_dict(), lines)
    return EXIT_CHECK_FAILED if result.refusal else EXIT_OK


# ----------------------------------------------------------------------------------------------
# cohort-backtest freeze and score
# ----------------------------------------------------------------------------------------------


def cmd_cohort_freeze(ctx: Context, args: argparse.Namespace) -> int:
    frozen = paidset.freeze_design(ctx.paths, folder=_set_dir(ctx, args), bundle_name=args.bundle, root=args.root,
                                   name=args.name, version=args.design_version, reason=args.reason, panel_dir=args.panel_dir)
    label = frozen["label"]
    payload = frozen["payload"]
    lines = [f"Froze the cohort backtest design {args.name} version {frozen['version']} in {frozen['prereg_file']} "
             f"{_tag(label)}. Nothing was scored.",
             f"Test rows: {payload['test_rows']['count']} cells on {payload['test_rows']['sites']} sites. Bundle "
             f"{payload['bundle']['name']} (sha256 {payload['bundle']['sha256'][:12]}) {_tag(label)}.",
             f"Band prior fitted on {payload['band_prior']['training_cells']} training cells: {payload['band_prior']['points']} "
             f"{_tag(label)}.",
             f"sha256 {frozen['sha256']}. The forecast source hash is stored beside it."]
    looks = frozen.get("earlier_looks") or []
    if looks:
        where = ", ".join(f"{look['design']} version {look['design_version']}" for look in looks)
        lines.append(f"These held-out test rows were already scored ({where}). A score of this design is a SECOND LOOK: it "
                     "needs design version 2 or later with its reason, and its numbers are PROVISIONAL with no marker "
                     f"verdict {_tag(label)}.")
    if label == REAL:
        lines.append(f"Commit and push {frozen['prereg_file']}, keep the source tree clean, then run: {PROG} cohort-backtest "
                     f"score --bundle {args.bundle}" + (f" --design-version {args.design_version}" if args.design_version > 1 else ""))
    else:
        lines.append(f"SYNTHETIC. Score it with: {PROG} cohort-backtest score --bundle {args.bundle} --root {args.root} "
                     "--skip-git-check")
    ctx.emit(frozen, lines)
    return EXIT_OK


def cmd_cohort_score(ctx: Context, args: argparse.Namespace) -> int:
    result = paidset.score_design(ctx.paths, folder=_set_dir(ctx, args), bundle_name=args.bundle, root=args.root,
                                  name=args.name, version=args.design_version, reason=args.reason,
                                  panel_dir=args.panel_dir, content_scorer=_scorer(args), check_git=not args.skip_git_check,
                                  log=lambda message: ctx.err.write(message + "\n"))
    ctx.emit({"label": result["label"], "report_file": result["report_file"], "manifest_file": result["manifest_file"],
              "marker": result["marker"], "report": result["report"]}, paidset.score_lines(result))
    return EXIT_OK if result["marker"].get("passed") is True else EXIT_CHECK_FAILED


# ----------------------------------------------------------------------------------------------
# The parser
# ----------------------------------------------------------------------------------------------


def _add_set(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--set", default=None, help="the training-set folder (default cache/trainingset/cells)")
    parser.add_argument("--panel-dir", default=None,
                        help="the past result pages (default the folder's own panel/, else cache/panel_labs)")


def _add_content(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--content-backend", choices=("auto", "cross_encoder", "lexical"), default="auto",
                        help="the content match scorer (default auto, the live path's choice)")


def _register_paid(sub: Any, common: argparse.ArgumentParser) -> None:
    paid = sub.add_parser("paid", help="the paid cohort path: cells, join and training (free)")
    inner = paid.add_subparsers(dest="paid_command", required=True)
    cells = inner.add_parser("cells", parents=[common], help="write the REAL training-set folder (free)")
    cells.add_argument("--cohort", required=True, help="a cohort table written by cohort build")
    cells.add_argument("--pages", required=True, help="the pages_<name>.jsonl written by cohort build")
    cells.add_argument("--universe", action="append", required=True, help="a universe table (repeatable)")
    cells.add_argument("--which", choices=("kept", "all"), default="kept")
    cells.add_argument("--out", default=None, help="default cache/trainingset/cells")
    cells.set_defaults(handler=cmd_paid_cells)
    syn = inner.add_parser("synthetic", parents=[common], help="write a SYNTHETIC training-set folder (free)")
    syn.add_argument("--seed", type=int, default=3)
    syn.add_argument("--new-sites", type=int, default=60)
    syn.add_argument("--niches", type=int, default=4)
    syn.add_argument("--searches-per-niche", type=int, default=90,
                     help="enough searches that the split rule leaves training cells (no search group on both sides)")
    syn.add_argument("--out", default=None, help="default cache/trainingset/synthetic")
    syn.set_defaults(handler=cmd_paid_synthetic)
    join = inner.add_parser("join", parents=[common], help="join the train split into upload cells and count drops (free)")
    _add_set(join)
    _add_content(join)
    join.set_defaults(handler=cmd_paid_join)
    train = inner.add_parser("train", parents=[common], help="train a bundle on the train split (free)")
    _add_set(train)
    _add_content(train)
    train.add_argument("--name", required=True, help="the bundle name")
    train.set_defaults(handler=cmd_paid_train)


def _register_cohort_backtest(sub: Any, common: argparse.ArgumentParser) -> None:
    group = sub.add_parser("cohort-backtest", help="the frozen backtest on past new sites, marker 3 (free)")
    inner = group.add_subparsers(dest="cohort_backtest_command", required=True)
    for command, handler, text in (("freeze", cmd_cohort_freeze, "freeze the design (scores nothing)"),
                                   ("score", cmd_cohort_score, "score the test split once")):
        parser = inner.add_parser(command, parents=[common], help=text)
        _add_set(parser)
        parser.add_argument("--bundle", required=True, help="the bundle trained by paid train on this folder")
        parser.add_argument("--root", default=None, help="the design folder root (default evals/forecast/backtest)")
        parser.add_argument("--name", default=paidset.DESIGN_NAME, help="the design name")
        parser.add_argument("--design-version", type=int, default=1)
        parser.add_argument("--reason", default=None, help="why a new design version (needed for version 2 and later)")
        if command == "score":
            parser.add_argument("--content-backend", choices=("cross_encoder", "lexical"), default=None,
                                help="the content match scorer (default the backend the bundle was trained with)")
            parser.add_argument("--skip-git-check", action="store_true",
                                help="SYNTHETIC tables only: skip the commit and push checks. Refused for REAL tables.")
        parser.set_defaults(handler=handler)


REGISTRARS: tuple[Callable[[Any, argparse.ArgumentParser], None], ...] = (
    _register_paid,
    _register_cohort_backtest,
)
