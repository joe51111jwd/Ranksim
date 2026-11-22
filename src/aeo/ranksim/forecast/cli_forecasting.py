"""The forecasting commands: python -m aeo.ranksim forecast train|backtest|upload|update|due.

What is REAL in the output: graded outcomes and the evidence files of the frozen backtests.
What is PREDICTED: every forecast point, range and label. What is SYNTHETIC: a bundle trained
on the synthetic world and anything it forecasts. Each line with a number says which one it is.

Every command here is free. None spends, and none scores a held-out row: `backtest run` only
freezes the next design version and hands over to backtest.run_panel, which refuses until that
design is committed and pushed. upload and update are dry runs unless --record is passed.

The logic lives in forecasting.py. This module parses arguments and prints. cli.py imports it
when it builds the parser; it imports cli.py, never the other way round at import time.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from . import forecasting
from .cli import EXIT_CHECK_FAILED, EXIT_OK, PROG, Context, _iso_date, _today
from .config import STAGE_DAYS
from .types import SYNTHETIC

UPDATE_STAGES: tuple[str, ...] = tuple(stage for stage in STAGE_DAYS if stage != "upload")


# ---------------------------------------------------------------------------
# train


def train_lines(result: forecasting.TrainResult) -> list[str]:
    if result.refusal:
        return [f"Nothing was trained: {result.refusal}"]
    bundle = result.bundle
    assert bundle is not None
    manifest = bundle.manifest
    label = result.label
    counts = result.counts
    lines = [
        f"Wrote bundle {bundle.name} to {bundle.folder} ({label} training data, version {bundle.version}).",
        f"Trained on {manifest['training_source']} data: {counts.get('upload_rows')} placement rows from "
        f"{counts.get('sites')} sites and {counts.get('searches')} searches ({label}). Training cutoff "
        f"{manifest['training_cutoff']}: a forecast dated earlier is refused.",
        f"Training cells: {manifest['training_cells']}. Features used: {len(manifest['features'])}. "
        f"Features with no value in the training rows, left out: {len(manifest['features_dropped_no_value'])}.",
        f"Updater in use: {(counts.get('updater') or {}).get('in_use') or 'none'}.",
        f"Split: {manifest['split']}.",
    ]
    if bundle.conditional:
        lines.append("Every upload forecast with this bundle passes the appearance gate, because its placement model only "
                     "knows sites that appeared on a search.")
    marker2 = result.marker2
    if marker2 and marker2.get("verdict"):
        from .markers import render_marker_text

        lines.append(render_marker_text(marker2["verdict"]))
        if marker2.get("report_file"):
            lines.append(f"Marker 2 read from {marker2['report_file']}. Nothing was rescored.")
    elif marker2:
        lines.append(f"Marker 2 was not measured: {marker2.get('note')}.")
    else:
        lines.append("No scored trust report exists, so marker 2 is not printed.")
    lines.extend(result.notes)
    return lines


def cmd_train(ctx: Context, args: argparse.Namespace) -> int:
    result = forecasting.train(ctx.paths, data=args.data, name=args.name, frame_path=args.frame,
                               log=lambda message: ctx.err.write(message + "\n"))
    ctx.emit(result.to_dict(), train_lines(result))
    return EXIT_CHECK_FAILED if result.refusal else EXIT_OK


# ---------------------------------------------------------------------------
# backtest


def cmd_backtest_list(ctx: Context, args: argparse.Namespace) -> int:
    items = forecasting.list_backtests(args.root)
    ctx.emit({"backtests": items, "scored_now": False}, forecasting.backtest_lines(items))
    return EXIT_OK


def cmd_backtest_run(ctx: Context, args: argparse.Namespace) -> int:
    from . import backtest

    reason = str(args.reason or "").strip()
    if not reason:
        return ctx.fail("backtest run needs --reason: why the frozen designs are looked at again. It is recorded.")
    ctx.err.write(forecasting.RERUN_WARNING + "\n")
    version, frozen = forecasting.backtest_rerun_version(args.root)
    if not frozen:
        shas = backtest.prereg_panel(root=args.root, version=version, reason=reason)
        lines = [forecasting.RERUN_WARNING,
                 f"Froze design version {version} beside the old files, with the reason recorded. Nothing was scored.",
                 f"Commit and push the prereg-v{version}.json files, then run {PROG} backtest run --reason with the same "
                 "reason to score them once."]
        ctx.emit({"warning": forecasting.RERUN_WARNING, "design_version": version, "frozen": shas, "scored": False}, lines)
        return EXIT_CHECK_FAILED
    result = backtest.run_panel(root=args.root, version=version, reason=reason,
                                log=lambda message: ctx.err.write(message + "\n"))
    lines = [forecasting.RERUN_WARNING, f"Scored design version {version} once. Reports were written beside the old ones."]
    lines.extend(forecasting.backtest_lines(forecasting.list_backtests(args.root))[1:])
    ctx.emit({"warning": forecasting.RERUN_WARNING, "design_version": version, "scored": True,
              "result": json.loads(json.dumps(result, default=str))}, lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# upload and update


def _bundle(ctx: Context, name: str | None) -> forecasting.ModelBundle:
    chosen = name or forecasting.newest_bundle(ctx.paths)
    if chosen is None:
        raise forecasting.ForecastingRefusal(f"No model bundle is trained yet under {ctx.paths.models}. Train one with: "
                                             f"{PROG} train --data panel")
    return forecasting.load_bundle(ctx.paths, chosen)


def _sites_for(ctx: Context, args: argparse.Namespace, stage: str) -> list[str]:
    if args.site:
        return [args.site]
    if not args.all_due:
        raise ValueError("Give --site SITE, or --all-due for every site whose stage is due.")
    calendar = forecasting.due(ctx.scoreboard(), _today(ctx, args.date))
    return sorted({item["site"] for item in calendar["due"] if item["stage"] == stage})


def _forecast(ctx: Context, args: argparse.Namespace, stage: str) -> int:
    bundle = _bundle(ctx, args.bundle)
    board = ctx.scoreboard()
    sites = _sites_for(ctx, args, stage)
    runs = []
    lines: list[str] = []
    if not sites:
        lines.append(f"No site has a {stage} forecast due.")
    for site in sites:
        run = forecasting.upload_or_update(board, ctx.paths, bundle=bundle, site=site, stage=stage, day=args.date,
                                           now=ctx.clock.now(), record=bool(args.record))
        runs.append(run)
        lines.extend(forecasting.run_lines(run, record=bool(args.record)))
    payload = {"stage": stage, "record": bool(args.record), "bundle": bundle.version,
               "training_label": bundle.training_label, "runs": [run.to_dict() for run in runs]}
    ctx.emit(payload, lines)
    trouble = any(run.blocked or run.missed or run.skipped or run.refused for run in runs)
    return EXIT_CHECK_FAILED if trouble else EXIT_OK


def cmd_upload(ctx: Context, args: argparse.Namespace) -> int:
    return _forecast(ctx, args, "upload")


def cmd_update(ctx: Context, args: argparse.Namespace) -> int:
    return _forecast(ctx, args, args.stage)


# ---------------------------------------------------------------------------
# due


def cmd_due(ctx: Context, args: argparse.Namespace) -> int:
    result = forecasting.due(ctx.scoreboard(), _today(ctx, args.date), window_days=int(args.window))
    ctx.emit(result, forecasting.due_lines(result))
    return EXIT_CHECK_FAILED if result["missed"] else EXIT_OK


# ---------------------------------------------------------------------------
# marker 2 (called by cli.cmd_marker)


def marker2(ctx: Context, root: str | Path | None = None) -> int:
    from .markers import render_marker_text

    path = forecasting.newest_trust_report(root)
    if path is None:
        return ctx.fail("No scored trust report exists under evals/forecast/trust, so marker 2 cannot be judged yet.",
                        EXIT_CHECK_FAILED)
    result = forecasting.marker2_from_trust_report(path)
    verdict = result["verdict"]
    lines = [f"Marker 2 read from {path} ({result['label']}). Nothing was rescored."]
    lines.extend(render_marker_text(verdict).splitlines())
    lines.append("The free panel version is a stand-in for the plan's 20,000 new sites, so it neither passes nor fails "
                 "marker 2.")
    ctx.emit(result, lines)
    if verdict.get("label") == SYNTHETIC:
        return EXIT_CHECK_FAILED
    return EXIT_OK if verdict["passed"] is True else EXIT_CHECK_FAILED


# ---------------------------------------------------------------------------
# the parsers


def _register_train(sub: Any, common: argparse.ArgumentParser) -> None:
    train = sub.add_parser("train", parents=[common], help="fit trust, placement and updater on one source and write a "
                                                            "bundle (free, never scores held-out rows)")
    train.add_argument("--data", choices=forecasting.TRAINING_SOURCES, required=True,
                       help="panel (REAL, training side only), synthetic (SYNTHETIC) or paid (REAL, the paid pulls)")
    train.add_argument("--name", default=None, help="the bundle folder under data/forecast/models (default <data>-<time>)")
    train.add_argument("--frame", default=None, help="the panel frame parquet (default: the newest under the cache)")
    train.set_defaults(handler=cmd_train)


def _register_backtest(sub: Any, common: argparse.ArgumentParser) -> None:
    bt = sub.add_parser("backtest", parents=[common], help="list the frozen backtest designs and their evidence files "
                                                           "(scores nothing)")
    bt.add_argument("--root", default=None, help="the backtest evidence folder (default evals/forecast/backtest)")
    bt.set_defaults(handler=cmd_backtest_list)
    inner = bt.add_subparsers(dest="backtest_command", required=False)
    run = inner.add_parser("run", parents=[common], help="look at the same held-out rows again under the next design "
                                                         "version (reported as another look)")
    run.add_argument("--reason", required=True, help="why the frozen designs are looked at again. It is recorded.")
    run.add_argument("--root", default=None, help="the backtest evidence folder (default evals/forecast/backtest)")
    run.set_defaults(handler=cmd_backtest_run)


def _add_forecast_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--site", default=None, help="a registered seed or pilot site")
    parser.add_argument("--all-due", action="store_true", help="every site whose stage is due in the next 3 days")
    parser.add_argument("--date", type=_iso_date, default=None,
                        help="the forecast date (default: the stage day, or today when that is earlier)")
    parser.add_argument("--bundle", default=None, help="the model bundle (default: the newest)")
    parser.add_argument("--record", action="store_true", help="freeze the forecasts on the scoreboard. Without it this is "
                                                              "a dry run.")


def _register_upload(sub: Any, common: argparse.ArgumentParser) -> None:
    upload = sub.add_parser("upload", parents=[common], help="the upload forecast of a site (dry run unless --record)")
    _add_forecast_args(upload)
    upload.set_defaults(handler=cmd_upload)
    update = sub.add_parser("update", parents=[common], help="the stage forecast of a site at day 7, 14, 30 or 60 "
                                                             "(dry run unless --record)")
    _add_forecast_args(update)
    update.add_argument("--stage", choices=UPDATE_STAGES, required=True)
    update.set_defaults(handler=cmd_update)


def _register_due(sub: Any, common: argparse.ArgumentParser) -> None:
    due = sub.add_parser("due", parents=[common], help="stage forecasts due soon and stages missed (free)")
    due.add_argument("--date", type=_iso_date, default=None, help="today, as a UTC date (default: now)")
    due.add_argument("--window", type=int, default=3, help="days ahead to look (default 3)")
    due.set_defaults(handler=cmd_due)


REGISTRARS: tuple[Callable[[Any, argparse.ArgumentParser], None], ...] = (
    _register_train,
    _register_backtest,
    _register_upload,
    _register_due,
)

__all__ = ["REGISTRARS", "marker2", "train_lines"]
