"""Full-coverage forward ranker, frozen before the next snapshot.

Surviving URLs keep a persistence score: the negative of the position on the
immediately previous snapshot. URLs that were absent from that snapshot get a
linear score on the same scale. The line was fit on new URLs in the
2026-11-06 to 2026-11-09 transition only. 2026-11-11 positions are not
coefficients. The report that uses them is a backtest of an outcome that was
already opened in 55a695c.

Outcome positions are labels. They are not features. A page with no HTML gets
no BM25F push. Tranco and Majestic weights are zero because those tables are
not in the repo. This module does not fetch and does not refit at score time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from aeo.crawl.extract import extract_html
from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim.bm25f import bm25f_scores
from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.metrics import mean_metric, ndcg_at_k, spearman
from aeo.ranksim.offpage import load_majestic_million, load_tranco_popularity
from aeo.textutil import tokenize_for_search

GENESIS = "0" * 64
ENTRANT_SCORE = -1000.0
CHANCE_PAIRWISE = 0.5
MIN_NEW_VS_SURVIVING_PAIRS = 100
BM25F_Z_WEIGHT = 1.0
PREVIOUS_SNAPSHOT_DAY = "2026-11-11"
FIT_HISTORY_DAY = "2026-11-06"
FIT_LABEL_DAY = "2026-11-09"
BACKTEST_LABEL_DAY = "2026-11-11"
PREDICTIONS_WINDOW = "2026-11-09_to_2026-11-11"
PAIR_MODES = ("all", "surviving_surviving", "new_vs_surviving", "new_vs_new")
CONTENT_STOP = frozenset(
    {"a", "an", "the", "for", "with", "and", "in", "of", "to", "best", "software", "all", "one", "1"}
)
_SLD2 = {"co", "com", "org", "net", "gov", "ac", "edu"}

HISTORY = (
    {
        "day": "2026-11-06",
        "path": "evals/forward-2026-11-09/baseline_serp.csv",
        "sha256": "7ad4617418b65377a9ed72b68831e66a3b7ceee6a98c34adf05fb0230d0f8e3d",
        "role": "Fit features, and domain history for every later score.",
    },
    {
        "day": "2026-11-09",
        "path": "evals/forward-2026-11-09/forward_serp.csv",
        "sha256": "70f92176ab7cce1e12ef28728a9db6170680dc57d6d01418cf05eb18e5c13a71",
        "role": "Labels for the new-URL fit. Domain history afterward. Not a 2026-11-11 feature.",
    },
    {
        "day": "2026-11-11",
        "path": "evals/forward-2026-11-11/outcome_serp.csv",
        "sha256": "3bda2449a497fcb2ecaf0876a0eafe6c50de1a894f39c8b4733ec3d00d3b9fd0",
        "role": "Previous snapshot when the next file is scored. Not used to fit coefficients.",
    },
)
PREDICTIONS_REL = "evals/forward-2026-11-11/predictions.json"
PREDICTIONS_SHA256 = "dae9d00a413fca2b07978123e67fc24605338005ee6d5e3623b55ed1f4dc616c"
PARAMS_REL = "evals/preregistered/next-snapshot/params.json"
MANIFEST_REL = "evals/preregistered/next-snapshot/manifest.json"
PREREG_REL = "evals/preregistered/next-snapshot/preregistration.md"
BACKTEST_JSON_REL = "evals/preregistered/next-snapshot/backtest-nov9-nov11.json"
BACKTEST_MD_REL = "evals/preregistered/next-snapshot/backtest-nov9-nov11.md"
LEDGER_REL = "evals/preregistered/next-snapshot/track-record.jsonl"
PINNED_PATHS = (
    "src/aeo/ranksim/full_coverage.py",
    "src/aeo/ranksim/bm25f.py",
    "src/aeo/ranksim/metrics.py",
    "src/aeo/ranksim/snapshot_protocol.py",
    "src/aeo/crawl/extract.py",
    "src/aeo/crawl/urls.py",
    "src/aeo/textutil.py",
    "scripts/score_next_snapshot.py",
    PARAMS_REL,
    PREREG_REL,
)
DO_NOT_WRITE = PINNED_PATHS + (MANIFEST_REL, BACKTEST_JSON_REL, BACKTEST_MD_REL)

GATE_TEXT = (
    "Promotion gate for the next snapshot. This text was written before that snapshot existed.\n"
    "\n"
    "The combined predictor clears the gate on that snapshot only when all three are true:\n"
    "\n"
    "1. On all ordered pairs among the new snapshot's top 10, pairwise accuracy is strictly "
    "greater than persistence-ranked-last on the same pairs. Pairwise accuracy is "
    "correct / (correct + wrong + ties). A score tie is not correct. Pairs whose outcome "
    "positions are equal are not in the denominator.\n"
    "\n"
    "2. On new-versus-surviving pairs, pairwise accuracy is strictly greater than 0.5.\n"
    "\n"
    "3. The new-versus-surviving denominator is at least 100 pairs.\n"
    "\n"
    "Persistence-ranked-last scores a URL that was in the immediately previous snapshot at "
    "the negative of that position. A URL that was absent from that snapshot scores -1000, "
    "tied with every other absent URL and below every survivor.\n"
    "\n"
    "A pass does not change the product ranker, the fitted parameters, or the frozen files. "
    "The command records the gate result on the track-record ledger. One snapshot of these "
    "30 queries is not a general forecast of Google. This gate was not chosen by looking at "
    "the Nov 9 to Nov 11 backtest."
)


@dataclass(frozen=True)
class PositionRow:
    query: str
    url: str
    position: float
    url_key: str
    day: str


@dataclass(frozen=True)
class FittedParams:
    """Scoring weights. Only the first three coefficients were estimated, and only on Nov 6 to Nov 9."""

    intercept: float
    coef_domain_prior: float
    coef_url_overlap: float
    coef_content_missing: float = 0.0
    bm25f_z_weight: float = BM25F_Z_WEIGHT
    tranco_z_weight: float = 0.0
    majestic_z_weight: float = 0.0
    survivor_history_weight: float = 0.0

    def position_hat(
        self,
        *,
        domain_prior: float,
        url_overlap: float,
        content_missing: float,
        bm25f_z: float,
        tranco_z: float,
        majestic_z: float,
    ) -> float:
        return (
            self.intercept
            + self.coef_domain_prior * domain_prior
            + self.coef_url_overlap * url_overlap
            + self.coef_content_missing * content_missing
            - self.bm25f_z_weight * bm25f_z
            - self.tranco_z_weight * tranco_z
            - self.majestic_z_weight * majestic_z
        )


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def site_key(url_or_host: str) -> str:
    """Registrable host. www and one subdomain fold in. example.co.uk stays example.co.uk."""
    text = str(url_or_host or "").strip()
    host = domain_of(text) if ("/" in text or ":" in text) else text
    labels = [part for part in (host or "").lower().strip(".").split(".") if part]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SLD2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def url_key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url.strip()


def content_tokens(query: str) -> set[str]:
    text = query.lower().replace("-", " ").replace("dirtworks", "dirt work").replace("dirtwork", "dirt work")
    return {token for token in tokenize_for_search(text) if token not in CONTENT_STOP}


def url_text(url: str) -> str:
    return url.replace("https://", " ").replace("http://", " ").replace("/", " ").replace("-", " ").replace(".", " ")


def url_query_overlap(query: str, url: str) -> float:
    wanted = content_tokens(query)
    if not wanted:
        return 0.0
    have = {token for token in tokenize_for_search(url_text(url).lower()) if token not in CONTENT_STOP}
    return len(wanted & have) / len(wanted)


def population_z(values: list[float | None]) -> list[float]:
    """Z-score of the present numbers. A missing number adds no push."""
    present = [float(value) for value in values if value is not None]
    if len(present) < 2:
        return [0.0 for _ in values]
    mean = sum(present) / len(present)
    variance = sum((value - mean) ** 2 for value in present) / len(present)
    std = math.sqrt(variance)
    if std == 0.0:
        return [0.0 for _ in values]
    return [0.0 if value is None else (float(value) - mean) / std for value in values]


def relevance_gain(position: float) -> float:
    if position <= 0 or position >= 11:
        return 0.0
    return float(11.0 - position)


def load_position_csv(path: Path, day: str) -> list[PositionRow]:
    rows: list[PositionRow] = []
    for label in load_serp_file(path):
        query = " ".join(label.query.split())
        url = label.url.strip()
        rows.append(
            PositionRow(query=query, url=url, position=float(label.position), url_key=url_key(url), day=day)
        )
    if not rows:
        raise ValueError(f"{path} has no position rows")
    return rows


def _dedupe(rows: list[PositionRow]) -> list[PositionRow]:
    best: dict[tuple[str, str], PositionRow] = {}
    for row in rows:
        key = (row.query, row.url_key)
        current = best.get(key)
        if current is None or row.position < current.position:
            best[key] = row
    return list(best.values())


def _by_query(rows: list[PositionRow]) -> dict[str, dict[str, PositionRow]]:
    grouped: dict[str, dict[str, PositionRow]] = defaultdict(dict)
    for row in _dedupe(rows):
        grouped[row.query][row.url_key] = row
    return grouped


def domain_prior_table(rows: list[PositionRow]) -> tuple[dict[str, float], float, dict[str, int]]:
    """Mean relevance (11 - position) of each site. Each snapshot contributes its own rows.

    Duplicates inside one snapshot collapse to the best position. The same URL on
    two snapshots counts twice. Unseen sites get the mean of those rows.
    """
    by_day: dict[str, list[PositionRow]] = defaultdict(list)
    for row in rows:
        by_day[row.day].append(row)
    totals: dict[str, list[float]] = defaultdict(list)
    for day_rows in by_day.values():
        for row in _dedupe(day_rows):
            totals[site_key(row.url)].append(relevance_gain(row.position))
    if not totals:
        return {}, 0.0, {}
    prior = {host: sum(values) / len(values) for host, values in totals.items()}
    counts = {host: len(values) for host, values in totals.items()}
    flat = [value for values in totals.values() for value in values]
    return prior, sum(flat) / len(flat), counts


def _solve(augmented: list[list[float]]) -> list[float] | None:
    matrix = [row[:] for row in augmented]
    width = len(matrix)
    for col in range(width):
        pivot = max(range(col, width), key=lambda row: abs(matrix[row][col]))
        if abs(matrix[pivot][col]) < 1e-12:
            return None
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        scale = matrix[col][col]
        for col_index in range(col, width + 1):
            matrix[col][col_index] /= scale
        for row in range(width):
            if row == col:
                continue
            factor = matrix[row][col]
            if factor == 0.0:
                continue
            for col_index in range(col, width + 1):
                matrix[row][col_index] -= factor * matrix[col][col_index]
    return [matrix[row][width] for row in range(width)]


def fit_ols(features: list[list[float]], targets: list[float]) -> list[float] | None:
    if not features or len(features) < len(features[0]):
        return None
    width = len(features[0])
    xtx = [[0.0] * width for _ in range(width)]
    xty = [0.0] * width
    for row, target in zip(features, targets, strict=True):
        for left in range(width):
            xty[left] += row[left] * target
            for right in range(width):
                xtx[left][right] += row[left] * row[right]
    return _solve([xtx[row] + [xty[row]] for row in range(width)])


def _varies(values: list[float]) -> bool:
    return max(values) - min(values) > 1e-12


def fit_new_url_params(history: list[PositionRow], outcome: list[PositionRow]) -> tuple[FittedParams, dict]:
    """OLS of outcome position on domain prior and URL overlap. New URLs only.

    ``history`` is the previous snapshot. ``outcome`` positions are the labels.
    A survivor's outcome position is not a training row and not a feature.
    """
    previous = _by_query(history)
    prior, default, _counts = domain_prior_table(history)
    rows: list[dict] = []
    for label in _dedupe(outcome):
        if label.url_key in previous.get(label.query, {}):
            continue
        host = site_key(label.url)
        rows.append(
            {
                "position": float(label.position),
                "domain_prior": prior.get(host, default),
                "url_overlap": url_query_overlap(label.query, label.url),
            }
        )
    if len(rows) < 3:
        raise ValueError(f"Need at least 3 new URLs to fit the line. Found {len(rows)}.")
    names = ["domain_prior", "url_overlap"]
    used = [name for name in names if _varies([float(row[name]) for row in rows])]
    design = [[1.0] + [float(row[name]) for name in used] for row in rows]
    targets = [float(row["position"]) for row in rows]
    solved = fit_ols(design, targets)
    if solved is None:
        raise ValueError("The new-URL design matrix is singular. No coefficient was written.")
    coef = {"intercept": solved[0], "domain_prior": 0.0, "url_overlap": 0.0}
    for name, value in zip(used, solved[1:], strict=True):
        coef[name] = value
    params = FittedParams(
        intercept=coef["intercept"],
        coef_domain_prior=coef["domain_prior"],
        coef_url_overlap=coef["url_overlap"],
        coef_content_missing=0.0,
        bm25f_z_weight=BM25F_Z_WEIGHT,
        tranco_z_weight=0.0,
        majestic_z_weight=0.0,
        survivor_history_weight=0.0,
    )
    predictions = [
        params.position_hat(
            domain_prior=float(row["domain_prior"]),
            url_overlap=float(row["url_overlap"]),
            content_missing=0.0,
            bm25f_z=0.0,
            tranco_z=0.0,
            majestic_z=0.0,
        )
        for row in rows
    ]
    residual = sum((pred - target) ** 2 for pred, target in zip(predictions, targets, strict=True)) / len(rows)
    estimated = ["intercept"] + used
    not_estimated = {
        "content_missing": "No HTML is in git, so this column does not vary on the fit rows. Weight stays 0. Missing text adds no push and is not a penalty.",
        "bm25f_z_weight": (
            "Precommitted at 1. One within-query BM25F standard deviation moves the predicted "
            "position by one. Same scale as HISTORY_CONTENT_SCALE, fixed before the Nov 9 labels "
            "were scored. Not estimated here, because the fit rows have no page text in git."
        ),
        "tranco_z_weight": "No Tranco file is in the repo. Weight stays 0. A file passed at score time is recorded and does not move the rank.",
        "majestic_z_weight": "No Majestic Million file is in the repo. Weight stays 0. A file passed at score time is recorded and does not move the rank.",
        "survivor_history_weight": (
            "The fit window has one previous snapshot, so a blend of earlier positions is not "
            "identified. Survivor score is the negative of the latest position."
        ),
    }
    for name in names:
        if name not in used:
            not_estimated[name] = "The column did not vary on the fit rows. Coefficient stays 0."
    diagnostics = {
        "estimated": estimated,
        "not_estimated": not_estimated,
        "n_new_urls": len(rows),
        "n_queries": len({row.query for row in _dedupe(outcome) if row.url_key not in previous.get(row.query, {})}),
        "in_sample_rmse": math.sqrt(residual),
        "predicted_position_min": min(predictions),
        "predicted_position_max": max(predictions),
        "label": "IN-SAMPLE fit diagnostics on 2026-11-06 to 2026-11-09 new URLs. Not a forward test.",
    }
    return params, diagnostics


def params_document(params: FittedParams, diagnostics: dict) -> dict:
    return {
        "bm25f_z_weight": params.bm25f_z_weight,
        "coef_content_missing": params.coef_content_missing,
        "coef_domain_prior": params.coef_domain_prior,
        "coef_url_overlap": params.coef_url_overlap,
        "fit_window": "2026-11-06_to_2026-11-09",
        "formula": (
            "position_hat = intercept + coef_domain_prior * domain_prior + coef_url_overlap * url_overlap "
            "+ coef_content_missing * content_missing - bm25f_z_weight * bm25f_z "
            "- tranco_z_weight * tranco_z - majestic_z_weight * majestic_z. "
            "New-URL score = -position_hat. "
            "Survivor score = -((1 - survivor_history_weight) * last_position + survivor_history_weight * mean of earlier positions). "
            "With survivor_history_weight 0 this is -last_position."
        ),
        "in_sample": diagnostics,
        "inputs": {
            "files_read": [HISTORY[0]["path"], HISTORY[1]["path"]],
            "history_day": FIT_HISTORY_DAY,
            "history_sha256": HISTORY[0]["sha256"],
            "label_day": FIT_LABEL_DAY,
            "label_sha256": HISTORY[1]["sha256"],
            "not_read": HISTORY[2]["path"],
        },
        "intercept": params.intercept,
        "label": "FIT. Coefficients estimated on new URLs from 2026-11-06 to 2026-11-09 only. 2026-11-11 positions were not used.",
        "majestic_z_weight": params.majestic_z_weight,
        "survivor_history_weight": params.survivor_history_weight,
        "tranco_z_weight": params.tranco_z_weight,
    }


def load_params(path: Path) -> FittedParams:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = (
        "intercept",
        "coef_domain_prior",
        "coef_url_overlap",
        "coef_content_missing",
        "bm25f_z_weight",
        "tranco_z_weight",
        "majestic_z_weight",
        "survivor_history_weight",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"{path} is missing {missing}")
    values = {name: float(payload[name]) for name in required}
    if any(not math.isfinite(value) for value in values.values()):
        raise ValueError(f"{path} has a non-finite coefficient")
    return FittedParams(
        intercept=values["intercept"],
        coef_domain_prior=values["coef_domain_prior"],
        coef_url_overlap=values["coef_url_overlap"],
        coef_content_missing=values["coef_content_missing"],
        bm25f_z_weight=values["bm25f_z_weight"],
        tranco_z_weight=values["tranco_z_weight"],
        majestic_z_weight=values["majestic_z_weight"],
        survivor_history_weight=values["survivor_history_weight"],
    )


def _lookup(table: dict[str, float] | None, url: str) -> float | None:
    if not table:
        return None
    host = site_key(url)
    if host in table:
        return float(table[host])
    bare = domain_of(url)
    if bare in table:
        return float(table[bare])
    return None


def _html_fields(url: str, html: str) -> dict[str, str] | None:
    extracted = extract_html(html or "", url)
    headings = " ".join(str(item.get("text") or "") for item in extracted.headings)
    title = extracted.title or ""
    h1 = extracted.h1 or ""
    body = extracted.main_text or ""
    if not (title.strip() or h1.strip() or headings.strip() or body.strip()):
        return None
    return {
        "title": title,
        "h1": h1,
        "headings": headings,
        "url": url_text(url),
        "anchor": "",
        "body": body[:8000],
    }


def _attach_bm25(query: str, rows: list[dict]) -> None:
    ready: list[dict] = []
    for row in rows:
        html = row.pop("_html", None)
        fields = None if not html else _html_fields(row["url"], html)
        if fields is None:
            row["content_missing"] = 1
            row["bm25f"] = None
            row["html_sha256"] = ""
        else:
            row["content_missing"] = 0
            row["_fields"] = fields
            row["html_sha256"] = hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()
            ready.append(row)
    values = bm25f_scores(query, [row["_fields"] for row in ready]) if ready else []
    for row, value in zip(ready, values, strict=True):
        row["bm25f"] = float(value)
        row.pop("_fields", None)


def score_outcome(
    history_snapshots: list[tuple[str, list[PositionRow]]],
    outcome: list[PositionRow],
    params: FittedParams,
    *,
    html_by_url: dict[str, str] | None = None,
    tranco: dict[str, float] | None = None,
    majestic: dict[str, float] | None = None,
) -> dict[str, list[dict]]:
    """Score every outcome URL. Outcome positions are stored as labels and are not read as features."""
    if not history_snapshots:
        raise ValueError("Scoring needs the previous snapshot.")
    indexed = [(day, _by_query(rows)) for day, rows in history_snapshots]
    previous_day, previous = indexed[-1]
    prior, default, counts = domain_prior_table([row for _day, rows in history_snapshots for row in rows])
    html_by_url = html_by_url or {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for label in _dedupe(outcome):
        earlier = []
        for day, table in indexed[:-1]:
            found = table.get(label.query, {}).get(label.url_key)
            if found is not None:
                earlier.append({"day": day, "position": float(found.position)})
        survived = previous.get(label.query, {}).get(label.url_key)
        host = site_key(label.url)
        grouped[label.query].append(
            {
                "query": label.query,
                "url": label.url,
                "url_key": label.url_key,
                "position": float(label.position),
                "bucket": "surviving" if survived is not None else "new",
                "previous_day": previous_day,
                "last_position": None if survived is None else float(survived.position),
                "earlier_positions": earlier,
                "domain_prior": prior.get(host, default),
                "domain_seen": host in prior,
                "domain_rows": counts.get(host, 0),
                "domain_default": default,
                "url_overlap": url_query_overlap(label.query, label.url),
                "tranco_popularity": _lookup(tranco, label.url),
                "majestic_refsubnets": _lookup(majestic, label.url),
                "_html": html_by_url.get(label.url_key),
            }
        )
    queries: dict[str, list[dict]] = {}
    for query, rows in grouped.items():
        _attach_bm25(query, rows)
        bm25_z = population_z([row["bm25f"] for row in rows])
        tranco_z = population_z([row["tranco_popularity"] for row in rows])
        majestic_z = population_z([row["majestic_refsubnets"] for row in rows])
        present_bm25 = [row["bm25f"] for row in rows if row["bm25f"] is not None]
        missing_floor = -1.0 if not present_bm25 else min(present_bm25) - 1.0
        for row, z_bm25, z_tranco, z_majestic in zip(rows, bm25_z, tranco_z, majestic_z, strict=True):
            row["bm25f_z"] = z_bm25
            row["tranco_z"] = z_tranco
            row["majestic_z"] = z_majestic
            position_hat = params.position_hat(
                domain_prior=float(row["domain_prior"]),
                url_overlap=float(row["url_overlap"]),
                content_missing=float(row["content_missing"]),
                bm25f_z=z_bm25,
                tranco_z=z_tranco,
                majestic_z=z_majestic,
            )
            if row["bucket"] == "surviving":
                latest = float(row["last_position"])
                earlier_values = [float(item["position"]) for item in row["earlier_positions"]]
                weight = float(params.survivor_history_weight)
                if earlier_values and weight != 0.0:
                    blended = (1.0 - weight) * latest + weight * (sum(earlier_values) / len(earlier_values))
                else:
                    blended = latest
                full_score = -blended
                full_position = blended
                persistence = -latest
            else:
                full_score = -position_hat
                full_position = position_hat
                persistence = ENTRANT_SCORE
            row["predicted_position"] = full_position
            row["scores"] = {
                "full_coverage": full_score,
                "persistence_ranked_last": persistence,
                "bm25": missing_floor if row["bm25f"] is None else float(row["bm25f"]),
            }
            row.pop("domain_default", None)
        queries[query] = rows
    return queries


def _mode_keeps(left: str, right: str, mode: str) -> bool:
    if mode == "all":
        return True
    if mode == "surviving_surviving":
        return left == "surviving" and right == "surviving"
    if mode == "new_vs_new":
        return left == "new" and right == "new"
    if mode == "new_vs_surviving":
        return left != right
    raise ValueError(mode)


def _pair_block(queries: dict[str, list[dict]], score_name: str, mode: str) -> dict:
    correct = wrong = ties = 0
    used = 0
    for rows in queries.values():
        got_correct = got_wrong = got_ties = 0
        saw = False
        for left in range(len(rows)):
            for right in range(len(rows)):
                if rows[left]["position"] >= rows[right]["position"]:
                    continue
                if not _mode_keeps(rows[left]["bucket"], rows[right]["bucket"], mode):
                    continue
                saw = True
                left_score = rows[left]["scores"][score_name]
                right_score = rows[right]["scores"][score_name]
                if left_score > right_score:
                    got_correct += 1
                elif left_score < right_score:
                    got_wrong += 1
                else:
                    got_ties += 1
        if not saw:
            continue
        used += 1
        correct += got_correct
        wrong += got_wrong
        ties += got_ties
    total = correct + wrong + ties
    return {
        "queries": used,
        "pairs": total,
        "correct": correct,
        "wrong": wrong,
        "ties": ties,
        "pairwise": None if total == 0 else round(correct / total, 6),
    }


def rank_metrics(queries: dict[str, list[dict]], score_name: str) -> dict:
    ndcgs: list[float | None] = []
    rhos: list[float | None] = []
    for rows in queries.values():
        if len(rows) < 2:
            continue
        order = sorted(range(len(rows)), key=lambda index: (-rows[index]["scores"][score_name], rows[index]["url"]))
        gains = {str(index): relevance_gain(rows[index]["position"]) for index in range(len(rows))}
        ndcgs.append(ndcg_at_k([str(index) for index in order], gains, k=10))
        rhos.append(spearman([row["scores"][score_name] for row in rows], [row["position"] for row in rows]))
    ndcg = mean_metric(ndcgs)
    rho = mean_metric(rhos)
    metrics = {mode: _pair_block(queries, score_name, mode) for mode in PAIR_MODES}
    metrics["ndcg_at_10"] = None if ndcg is None else round(ndcg, 6)
    metrics["mean_spearman"] = None if rho is None else round(rho, 6)
    metrics["ndcg_queries"] = sum(1 for value in ndcgs if value is not None)
    metrics["spearman_queries"] = sum(1 for value in rhos if value is not None)
    return metrics


def evaluate_gate(combined: dict, persistence: dict) -> dict:
    def rate(block: dict) -> float | None:
        if not block["pairs"]:
            return None
        return block["correct"] / block["pairs"]

    combined_all = rate(combined["all"])
    persistence_all = rate(persistence["all"])
    new_rate = rate(combined["new_vs_surviving"])
    new_pairs = int(combined["new_vs_surviving"]["pairs"])
    checks = {
        "beats_persistence_ranked_last_on_all_pairs": (
            combined_all is not None and persistence_all is not None and combined_all > persistence_all
        ),
        "beats_chance_on_new_vs_surviving": new_rate is not None and new_rate > CHANCE_PAIRWISE,
        "new_vs_surviving_pairs_at_least_minimum": new_pairs >= MIN_NEW_VS_SURVIVING_PAIRS,
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "chance": CHANCE_PAIRWISE,
        "min_new_vs_surviving_pairs": MIN_NEW_VS_SURVIVING_PAIRS,
        "combined_all_pairwise": None if combined_all is None else round(combined_all, 6),
        "persistence_all_pairwise": None if persistence_all is None else round(persistence_all, 6),
        "new_vs_surviving_pairwise": None if new_rate is None else round(new_rate, 6),
        "new_vs_surviving_pairs": new_pairs,
        "ranker_change": "none",
    }


def _coverage(queries: dict[str, list[dict]]) -> dict:
    rows = [row for group in queries.values() for row in group]
    new_rows = [row for row in rows if row["bucket"] == "new"]
    return {
        "outcome_rows": len(rows),
        "queries": len(queries),
        "new_rows": len(new_rows),
        "surviving_rows": sum(1 for row in rows if row["bucket"] == "surviving"),
        "new_with_domain_seen": sum(1 for row in new_rows if row["domain_seen"]),
        "content_missing_rows": sum(1 for row in rows if row["content_missing"]),
        "html_rows": sum(1 for row in rows if not row["content_missing"]),
        "scored_rows": sum(1 for row in rows if all(math.isfinite(value) for value in row["scores"].values())),
    }


def load_html_dir(html_dir: Path) -> tuple[dict[str, str], dict]:
    manifest = html_dir / "html_manifest.csv"
    if not manifest.exists():
        raise ValueError(
            f"{html_dir} has no html_manifest.csv. Expected columns url and file, with files relative to that directory."
        )
    import csv

    found: dict[str, str] = {}
    missing_files = 0
    empty_files = 0
    with manifest.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{manifest} has no header")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        url_column = fields.get("url") or fields.get("link")
        file_column = fields.get("file") or fields.get("path")
        if not url_column or not file_column:
            raise ValueError(f"{manifest} needs url and file columns")
        for raw in reader:
            url = (raw.get(url_column) or "").strip()
            relative = (raw.get(file_column) or "").strip()
            if not url or not relative:
                continue
            path = (html_dir / relative).resolve()
            try:
                path.relative_to(html_dir.resolve())
            except ValueError as exc:
                raise ValueError(f"HTML path escapes the html dir: {relative}") from exc
            if not path.is_file():
                missing_files += 1
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if not text.strip():
                empty_files += 1
                continue
            found[url_key(url)] = text
    return found, {"html_files": len(found), "missing_files": missing_files, "empty_files": empty_files}


def assert_history(root: Path) -> None:
    for item in HISTORY:
        path = root / item["path"]
        if not path.is_file():
            raise SystemExit(f"Missing frozen history file {item['path']}")
        digest = file_sha256(path)
        if digest != item["sha256"]:
            raise SystemExit(f"{item['path']} sha256 {digest} does not match the frozen {item['sha256']}")


def assert_pinned(root: Path) -> None:
    manifest_path = root / MANIFEST_REL
    if not manifest_path.is_file():
        raise SystemExit(f"Missing {MANIFEST_REL}. Refusing to score without the freeze manifest.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    listed = manifest.get("sha256") or {}
    expected_keys = set(PINNED_PATHS)
    if set(listed) != expected_keys:
        raise SystemExit("Freeze manifest sha256 list does not match the scorer's pinned paths.")
    for rel, expected in listed.items():
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"Missing pinned file {rel}")
        digest = file_sha256(path)
        if digest != expected:
            raise SystemExit(f"{rel} sha256 {digest} does not match frozen manifest {expected}")


def _refuse_write(path: Path, root: Path) -> None:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return
    if relative in DO_NOT_WRITE:
        raise SystemExit(f"Refusing to write frozen file {relative}")


def load_frozen_prediction_scores(path: Path) -> dict[tuple[str, str], dict[str, float]]:
    if file_sha256(path) != PREDICTIONS_SHA256:
        raise ValueError(f"{path} does not match the frozen predictions sha256 {PREDICTIONS_SHA256}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    window = payload["windows"][PREDICTIONS_WINDOW]
    found: dict[tuple[str, str], dict[str, float]] = {}
    for row in window["rows"]:
        query = " ".join(str(row["query"]).split())
        key = str(row.get("url_key") or url_key(str(row["url"])))
        found[(query, key)] = {name: float(value) for name, value in row["scores"].items()}
    return found


def attach_frozen_baselines(queries: dict[str, list[dict]], frozen: dict[tuple[str, str], dict[str, float]]) -> dict:
    unmatched = 0
    disagreements = 0
    for rows in queries.values():
        for row in rows:
            found = frozen.get((row["query"], row["url_key"]))
            if found is None:
                row["scores"]["lambdamart_ranked_last"] = ENTRANT_SCORE
                row["scores"]["bm25_ranked_last"] = ENTRANT_SCORE
                if row["bucket"] == "surviving":
                    unmatched += 1
                continue
            row["scores"]["lambdamart_ranked_last"] = float(found["lambdamart"])
            row["scores"]["bm25_ranked_last"] = float(found["bm25"])
            if row["bucket"] == "surviving" and abs(float(found["persistence"]) - row["scores"]["persistence_ranked_last"]) > 1e-6:
                disagreements += 1
    return {"frozen_surviving_unmatched": unmatched, "persistence_score_disagreements": disagreements}


def _load_named_history(root: Path, days: set[str]) -> list[tuple[str, list[PositionRow]]]:
    assert_history(root)
    loaded = []
    for item in HISTORY:
        if item["day"] not in days:
            continue
        loaded.append((item["day"], load_position_csv(root / item["path"], item["day"])))
    return loaded


def build_backtest(root: Path | None = None) -> dict:
    """Nov 9 to Nov 11 backtest. Does not refit. Nov 11 positions are labels only."""
    root = root or repo_root()
    params = load_params(root / PARAMS_REL)
    history = _load_named_history(root, {FIT_HISTORY_DAY, FIT_LABEL_DAY})
    outcome = load_position_csv(root / HISTORY[2]["path"], BACKTEST_LABEL_DAY)
    queries = score_outcome(history, outcome, params)
    frozen = load_frozen_prediction_scores(root / PREDICTIONS_REL)
    baseline_notes = attach_frozen_baselines(queries, frozen)
    scorers = (
        "full_coverage",
        "persistence_ranked_last",
        "lambdamart_ranked_last",
        "bm25_ranked_last",
    )
    fit_history = _load_named_history(root, {FIT_HISTORY_DAY})
    fit_outcome = load_position_csv(root / HISTORY[1]["path"], FIT_LABEL_DAY)
    fit_queries = score_outcome(fit_history, fit_outcome, params)
    coverage = _coverage(queries)
    report = {
        "label": "BACKTEST",
        "not_a_forward_test": True,
        "reason": (
            "The 2026-11-11 outcome was opened in commit 55a695c, before this scorer existed. "
            "Coefficients were fit on 2026-11-06 to 2026-11-09 new URLs only and were not changed "
            "after these numbers were computed. This is not the promotion gate."
        ),
        "html": "No snapshot HTML is in git. content_missing is 1 on every backtest row. bm25f_z is 0. The precommitted BM25F weight was not exercised.",
        "offpage": "Tranco and Majestic Million files are not in the repo. Their frozen weights are 0.",
        "coverage": coverage,
        "baseline_match": baseline_notes,
        "scorers": {name: rank_metrics(queries, name) for name in scorers},
        "fit_sample_nov6_to_nov9": {
            "label": "IN-SAMPLE. These pairs are the fit window. They are not a test and not a gate.",
            "coverage": _coverage(fit_queries),
            "scorers": {
                name: rank_metrics(fit_queries, name) for name in ("full_coverage", "persistence_ranked_last")
            },
        },
        "gate_if_this_were_the_next_snapshot": {
            "label": "NOT A PROMOTION. Computed so the backtest can be read against the frozen rule. The rule was not edited after this number.",
            **evaluate_gate(
                rank_metrics(queries, "full_coverage"),
                rank_metrics(queries, "persistence_ranked_last"),
            ),
        },
    }
    return report


def _cell(block: dict) -> str:
    if block["pairs"] == 0:
        return "0 pairs"
    text = f"{block['correct']}/{block['pairs']} ({block['pairwise']:.6f})"
    if block["ties"]:
        text += f", {block['ties']} ties"
    return text


def render_backtest(report: dict) -> str:
    lines = [
        "# Backtest: Nov 9 to Nov 11 full coverage",
        "",
        "Label: BACKTEST. Not a forward test and not a promotion.",
        "",
        report["reason"],
        "",
        report["html"],
        "",
        report["offpage"],
        "",
        "LambdaMART and BM25 baselines are the scores frozen in `evals/forward-2026-11-11/predictions.json` for the Nov 9 to Nov 11 window. A Nov 11 URL that file did not score gets -1000, tied with the other unscored URLs and below every scored URL. This run did not refit LambdaMART. The booster and the training HTML are not in git.",
        "",
        "Pairwise counts ordered pairs whose outcome positions differ. A score tie is in the denominator and is not correct. NDCG@10 and mean Spearman are on the full outcome list for each query. Higher scores are better. NDCG gains are 11 minus position.",
        "",
        f"Outcome rows {report['coverage']['outcome_rows']}. Surviving {report['coverage']['surviving_rows']}. New {report['coverage']['new_rows']}. Scored {report['coverage']['scored_rows']}. Domain seen on {report['coverage']['new_with_domain_seen']} of the new rows. HTML rows {report['coverage']['html_rows']}.",
        "",
        "| Scorer | All pairs | Surviving-surviving | New vs surviving | New vs new | NDCG@10 | Mean Spearman |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    titles = {
        "full_coverage": "Combined (persistence + new-URL line)",
        "persistence_ranked_last": "Persistence, new URLs ranked last and tied",
        "lambdamart_ranked_last": "Frozen LambdaMART, new URLs ranked last",
        "bm25_ranked_last": "Frozen BM25, new URLs ranked last",
    }
    for name, title in titles.items():
        metrics = report["scorers"][name]
        lines.append(
            "| "
            + " | ".join(
                [
                    title,
                    _cell(metrics["all"]),
                    _cell(metrics["surviving_surviving"]),
                    _cell(metrics["new_vs_surviving"]),
                    _cell(metrics["new_vs_new"]),
                    "n/a" if metrics["ndcg_at_10"] is None else f"{metrics['ndcg_at_10']:.6f}",
                    "n/a" if metrics["mean_spearman"] is None else f"{metrics['mean_spearman']:.6f}",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            f"Frozen surviving URLs with no prediction-file match: {report['baseline_match']['frozen_surviving_unmatched']}. Persistence disagreements against that file: {report['baseline_match']['persistence_score_disagreements']}.",
            "",
            (
                "Surviving-surviving reproduces the published Nov 9 to Nov 11 overlap: "
                f"persistence {report['scorers']['persistence_ranked_last']['surviving_surviving']['correct']}/"
                f"{report['scorers']['persistence_ranked_last']['surviving_surviving']['pairs']}, "
                f"LambdaMART {report['scorers']['lambdamart_ranked_last']['surviving_surviving']['correct']}/"
                f"{report['scorers']['lambdamart_ranked_last']['surviving_surviving']['pairs']} "
                f"with {report['scorers']['lambdamart_ranked_last']['surviving_surviving']['ties']} ties, "
                f"BM25 {report['scorers']['bm25_ranked_last']['surviving_surviving']['correct']}/"
                f"{report['scorers']['bm25_ranked_last']['surviving_surviving']['pairs']}."
            ),
            "",
            "## Fit sample, Nov 6 to Nov 9",
            "",
            report["fit_sample_nov6_to_nov9"]["label"],
            "",
            "| Scorer | All pairs | Surviving-surviving | New vs surviving | New vs new | NDCG@10 | Mean Spearman |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    fit_titles = {
        "full_coverage": "Combined, in-sample",
        "persistence_ranked_last": "Persistence, new URLs ranked last, in-sample",
    }
    fit = report["fit_sample_nov6_to_nov9"]
    for name, title in fit_titles.items():
        metrics = fit["scorers"][name]
        lines.append(
            "| "
            + " | ".join(
                [
                    title,
                    _cell(metrics["all"]),
                    _cell(metrics["surviving_surviving"]),
                    _cell(metrics["new_vs_surviving"]),
                    _cell(metrics["new_vs_new"]),
                    "n/a" if metrics["ndcg_at_10"] is None else f"{metrics['ndcg_at_10']:.6f}",
                    "n/a" if metrics["mean_spearman"] is None else f"{metrics['mean_spearman']:.6f}",
                ]
            )
            + " |"
        )
    gate = report["gate_if_this_were_the_next_snapshot"]
    combined = report["scorers"]["full_coverage"]
    persistence = report["scorers"]["persistence_ranked_last"]
    all_gain = combined["all"]["correct"] - persistence["all"]["correct"]
    cross_gain = combined["new_vs_surviving"]["correct"] - persistence["new_vs_surviving"]["correct"]
    fresh_gain = combined["new_vs_new"]["correct"] - persistence["new_vs_new"]["correct"]
    fit_fresh = report["fit_sample_nov6_to_nov9"]["scorers"]["full_coverage"]["new_vs_new"]
    lines.extend(
        [
            "",
            (
                f"The all-pairs gap versus persistence-ranked-last is {all_gain} correct pairs "
                f"({cross_gain} of them new-versus-surviving, {fresh_gain} new-versus-new). "
                "Persistence-ranked-last ties every new-versus-new pair, so those pairs add nothing to its correct count. "
                f"On the fit window, new-versus-new was {fit_fresh['correct']}/{fit_fresh['pairs']} "
                f"({fit_fresh['pairwise']:.6f}), with {fit_fresh['ties']} ties in that denominator. "
                "Under the rule that a tie is not correct, that rate is not above a coin flip."
            ),
            "",
            "Predicted positions on the fit rows sit in a narrow band. See `predicted_position_min` and `predicted_position_max` in `params.json`. A new URL is not predicted at the top of the list.",
            "",
            "## Frozen gate applied to this backtest",
            "",
            gate["label"],
            "",
            f"Pass: {gate['pass']}. Checks: {json.dumps(gate['checks'], sort_keys=True)}.",
            "",
            "A pass or a fail here does not promote the ranker and does not edit the gate.",
            "",
        ]
    )
    return "\n".join(lines)


def _canonical(row: dict) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def append_track_record(path: Path, rows: list[dict]) -> str:
    previous = GENESIS
    if path.exists() and path.read_text(encoding="utf-8").strip():
        filled = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        previous = json.loads(filled[-1])["row_hash"]
    written: list[str] = []
    for row in rows:
        body = dict(row)
        body["prev_hash"] = previous
        digest = hashlib.sha256((previous + "\n" + _canonical(body)).encode("utf-8")).hexdigest()
        body["row_hash"] = digest
        previous = digest
        written.append(_canonical(body))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in written:
            handle.write(line + "\n")
    return previous


def verify_track_record(path: Path) -> int:
    previous = GENESIS
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row_hash = row.pop("row_hash")
        if row.get("prev_hash") != previous:
            raise ValueError("track-record prev_hash does not match the previous row")
        digest = hashlib.sha256((previous + "\n" + _canonical(row)).encode("utf-8")).hexdigest()
        if digest != row_hash:
            raise ValueError("track-record row_hash does not match the row")
        previous = row_hash
        count += 1
    return count


def _parse_day(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"--observed-on must be YYYY-MM-DD, got {value!r}") from exc


def _feature_payload(row: dict) -> dict:
    return {
        "bm25f": row["bm25f"],
        "bm25f_z": row["bm25f_z"],
        "content_missing": row["content_missing"],
        "domain_prior": row["domain_prior"],
        "domain_rows": row["domain_rows"],
        "domain_seen": row["domain_seen"],
        "earlier_positions": row["earlier_positions"],
        "html_sha256": row["html_sha256"],
        "last_position": row["last_position"],
        "majestic_refsubnets": row["majestic_refsubnets"],
        "majestic_z": row["majestic_z"],
        "tranco_popularity": row["tranco_popularity"],
        "tranco_z": row["tranco_z"],
        "url_overlap": row["url_overlap"],
    }


def score_command(
    *,
    snapshot_csv: Path,
    observed_on: str,
    html_dir: Path | None = None,
    ledger: Path | None = None,
    tranco_path: Path | None = None,
    majestic_path: Path | None = None,
    report_path: Path | None = None,
    root: Path | None = None,
) -> dict:
    """Score a new snapshot with the frozen line. Append PREDICTED and REAL rows. Do not refit."""
    root = root or repo_root()
    day = _parse_day(observed_on)
    if day <= date.fromisoformat(PREVIOUS_SNAPSHOT_DAY):
        raise SystemExit(
            f"--observed-on must be after {PREVIOUS_SNAPSHOT_DAY}. "
            "The Nov 11 file is history for this freeze, not a new outcome."
        )
    if not snapshot_csv.is_file():
        raise SystemExit(f"Missing snapshot CSV {snapshot_csv}")
    snapshot_hash = file_sha256(snapshot_csv)
    for item in HISTORY:
        if snapshot_hash == item["sha256"]:
            raise SystemExit(f"The snapshot CSV is the frozen {item['day']} history file. It is not a new outcome.")
    ledger_path = ledger or (root / LEDGER_REL)
    _refuse_write(ledger_path, root)
    if report_path is not None:
        _refuse_write(report_path, root)
    before = {rel: file_sha256(root / rel) for rel in PINNED_PATHS if (root / rel).is_file()}
    assert_pinned(root)
    assert_history(root)
    params = load_params(root / PARAMS_REL)
    html_info = {"html_files": 0, "missing_files": 0, "empty_files": 0, "attached": False}
    html_by_url: dict[str, str] = {}
    if html_dir is not None:
        if not html_dir.is_dir():
            raise SystemExit(f"--html-dir is not a directory: {html_dir}")
        html_by_url, html_info = load_html_dir(html_dir)
        html_info["attached"] = True
    tranco = None
    majestic = None
    if tranco_path is not None:
        if not tranco_path.is_file():
            raise SystemExit(f"Missing Tranco file {tranco_path}")
        tranco = load_tranco_popularity(tranco_path)
    if majestic_path is not None:
        if not majestic_path.is_file():
            raise SystemExit(f"Missing Majestic file {majestic_path}")
        majestic = load_majestic_million(majestic_path)
    history = _load_named_history(root, {item["day"] for item in HISTORY})
    outcome = load_position_csv(snapshot_csv, observed_on)
    queries = score_outcome(history, outcome, params, html_by_url=html_by_url, tranco=tranco, majestic=majestic)
    coverage = _coverage(queries)
    if coverage["scored_rows"] != coverage["outcome_rows"]:
        raise SystemExit("A row was left without a score. Refusing to write the ledger.")
    scorer_names = ("full_coverage", "persistence_ranked_last", "bm25")
    metrics = {name: rank_metrics(queries, name) for name in scorer_names}
    gate = evaluate_gate(metrics["full_coverage"], metrics["persistence_ranked_last"])
    manifest = json.loads((root / MANIFEST_REL).read_text(encoding="utf-8"))
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows: list[dict] = [
        {
            "label": "PREDICTED",
            "kind": "run",
            "confidence": "UNSURE",
            "created_at": created_at,
            "observed_on": observed_on,
            "snapshot_sha256": snapshot_hash,
            "params_sha256": file_sha256(root / PARAMS_REL),
            "scorer_sha256": file_sha256(root / "src/aeo/ranksim/full_coverage.py"),
            "code_commit": manifest.get("code_commit"),
            "html": html_info,
            "tranco_weight": params.tranco_z_weight,
            "majestic_weight": params.majestic_z_weight,
            "note": "PREDICTED. Outcome positions were not features. The line was not refit.",
        }
    ]
    for query in sorted(queries):
        for row in sorted(queries[query], key=lambda item: (item["position"], item["url"])):
            rows.append(
                {
                    "label": "PREDICTED",
                    "kind": "score",
                    "confidence": "UNSURE",
                    "created_at": created_at,
                    "observed_on": observed_on,
                    "snapshot_sha256": snapshot_hash,
                    "scorer": "full_coverage",
                    "query": row["query"],
                    "url": row["url"],
                    "bucket": row["bucket"],
                    "score": row["scores"]["full_coverage"],
                    "predicted_position": row["predicted_position"],
                    "persistence_ranked_last": row["scores"]["persistence_ranked_last"],
                    "bm25": row["scores"]["bm25"],
                    "features": _feature_payload(row),
                    "note": "PREDICTED. Not a Google rank.",
                }
            )
            rows.append(
                {
                    "label": "REAL",
                    "kind": "outcome",
                    "observed_on": observed_on,
                    "snapshot_sha256": snapshot_hash,
                    "query": row["query"],
                    "url": row["url"],
                    "position": row["position"],
                    "note": "REAL outcome position from the snapshot CSV. It was not a feature.",
                }
            )
    rows.append(
        {
            "label": "SCORE",
            "kind": "metrics",
            "observed_on": observed_on,
            "snapshot_sha256": snapshot_hash,
            "coverage": coverage,
            "metrics": metrics,
            "gate": gate,
            "lambdamart": "not scored. The frozen booster and the training HTML are not in git, and this command does not refit.",
            "bm25_note": (
                "bm25 is BM25F on the supplied HTML. A URL with no HTML shares a score below every URL that has BM25F. "
                "If no HTML was attached, every bm25 score is tied."
                if html_info.get("attached")
                else "No HTML directory was passed. bm25 scores are tied. full_coverage gets no BM25F push."
            ),
        }
    )
    append_track_record(ledger_path, rows)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"metrics": metrics, "gate": gate, "coverage": coverage}, indent=2) + "\n", encoding="utf-8")
    after = {rel: file_sha256(root / rel) for rel in before}
    if after != before:
        raise SystemExit("Frozen files changed during scoring.")
    return {"coverage": coverage, "metrics": metrics, "gate": gate, "ledger": str(ledger_path)}


def format_summary(result: dict) -> str:
    gate = result["gate"]
    combined = result["metrics"]["full_coverage"]
    persistence = result["metrics"]["persistence_ranked_last"]
    lines = [
        f"Label: PREDICTED. Gate pass: {gate['pass']}. ranker_change: none.",
        f"All pairs: combined {_cell(combined['all'])} vs persistence-ranked-last {_cell(persistence['all'])}.",
        f"New vs surviving: {_cell(combined['new_vs_surviving'])} (minimum {MIN_NEW_VS_SURVIVING_PAIRS}, chance {CHANCE_PAIRWISE}).",
        f"Surviving-surviving: {_cell(combined['surviving_surviving'])}. New vs new: {_cell(combined['new_vs_new'])}.",
        f"NDCG@10 {combined['ndcg_at_10']}. Mean Spearman {combined['mean_spearman']}.",
        f"Rows {result['coverage']['scored_rows']}/{result['coverage']['outcome_rows']}. Ledger {result['ledger']}.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the next snapshot with the frozen full-coverage line. Does not refit.")
    parser.add_argument("--snapshot-csv", required=True, help="New snapshot CSV with query, url (or link), and position.")
    parser.add_argument("--observed-on", required=True, help="YYYY-MM-DD of that snapshot. Must be after 2026-11-11.")
    parser.add_argument("--html-dir", default="", help="Directory containing html_manifest.csv. Omit when there is no HTML.")
    parser.add_argument("--ledger", default="", help=f"Append-only JSONL. Default {LEDGER_REL}.")
    parser.add_argument("--tranco", default="", help="Optional Tranco CSV or zip. Frozen weight is 0, so it is recorded and does not change scores.")
    parser.add_argument("--majestic", default="", help="Optional Majestic Million CSV. Frozen weight is 0, so it is recorded and does not change scores.")
    parser.add_argument("--report", default="", help="Optional JSON summary. Refuses a path inside the frozen directory.")
    args = parser.parse_args(argv)
    result = score_command(
        snapshot_csv=Path(args.snapshot_csv),
        observed_on=args.observed_on,
        html_dir=Path(args.html_dir) if args.html_dir else None,
        ledger=Path(args.ledger) if args.ledger else None,
        tranco_path=Path(args.tranco) if args.tranco else None,
        majestic_path=Path(args.majestic) if args.majestic else None,
        report_path=Path(args.report) if args.report else None,
    )
    print(format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
