"""Reader noise measured on REAL repeated Google pulls: the raw single-pull baseline marker 1 must beat.

What is REAL: every position here was recorded by someone else from Google.
Two sources:

1. The DataForSEO result-page panel (`panel.py`): pages of the same keyword
   that DataForSEO happened to read 0.5 to 3 days apart. These are COUNTRY
   level DataForSEO pulls, single pulls, not a pinned city and not a clean
   reading. Label: "single-pull day-apart agreement, country-level DataForSEO pulls".
2. The Wellesley Dobbs SERPs (Harvard Dataverse doi:10.7910/DVN/YFAH9X, CC0):
   the same query from the same US city, read in the morning and the evening of
   one day, and on one evening and the next. 2022, about 8 organic results per
   page, news-heavy queries about one topic (the Dobbs ruling).

What is PREDICTED: nothing. No model is involved. Reading A is treated as a
"forecast" of reading B only to show what grading rule v1 would say about pure
reading noise.

Definitions (docs/plan/IMPLEMENTATION_SPEC.md section 3, marker 1, OUR READING):
the unit is a (search, site) where the site is in the top 10 of either reading.
They agree when the two positions differ by 2 or less, a site missing from a
reading counting as 51. Also given: the same over every site in the top 50 of
either reading, per band of reading A, the entry and exit rates of the top 10,
and agreement under grading rule v1 with reading A taken as the forecast of
reading B. Intervals resample whole searches (keywords, or Dobbs queries).

The agreement arithmetic is implemented here on plain {site: position} dicts so
this module does not depend on the clean reader being written at the same time.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

from ...site_history import site_key
from ..asof import utc_iso
from ..config import EXCLUDED_SITES, NT50, TOP_DEPTH, forecast_paths
from ..grading import RULE_V1, band_of
from ..stats import cluster_bootstrap_ratio, wilson

AGREE_WITHIN = 2
TOP_UNIT = 10
REPEAT_MIN_GAP_DAYS = 0.5
REPEAT_MAX_GAP_DAYS = 3.0
UNIT_BANDS = ("1-10", "11-20", "21-50", "NT50")

DFS_LABEL = "single-pull day-apart agreement, country-level DataForSEO pulls"
DOBBS_LABEL = "single-pull agreement, Wellesley Dobbs SERPs, same query and same US city"
DOBBS_LIMITS = (
    "Collected in June and July 2022, before Google's 2025 and 2026 changes.",
    "About 8 organic results per page, so it says nothing about positions 11 to 50.",
    "A site past the last shown result counts as 51, which can turn a move from 8 to 9 into a disagreement.",
    "News-heavy queries about one topic, read in the weeks after the Dobbs ruling, when results moved fast.",
    "The files carry no time of day, only AM or PM in the file name.",
    "City-level desktop pulls by a research team, not DataForSEO and not our clean reader.",
)

DOBBS_DOI = "doi:10.7910/DVN/YFAH9X"
DOBBS_ACCESS_URL = "https://dataverse.harvard.edu/api/access/datafile/{file_id}"
DOBBS_DATASET_URL = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/YFAH9X"
# (Dataverse file id, file name, size in bytes) read from the dataset's file list on 2026-11-22.
DOBBS_FILES: tuple[tuple[int, str, int], ...] = (
    (6897245, "06-26-22-AM-organic-links.tab", 75357470),
    (6897261, "06-26-22-PM-organic-links.tab", 81025411),
    (6897253, "06-27-22-AM-organic-links.tab", 71478577),
    (6897258, "06-27-22-PM-organic-links.tab", 76118234),
    (6897246, "06-28-22-AM-organic-links.tab", 83317584),
    (6897250, "06-28-22-PM-organic-links.tab", 76292796),
    (6897255, "06-29-22-AM-organic-links.tab", 64110559),
    (6897257, "06-29-22-PM-organic-links.tab", 83477695),
    (6897243, "06-30-22-AM-organic-links.tab", 70842418),
    (6897249, "06-30-22-PM-organic-links.tab", 83068516),
    (6897242, "07-01-22-PM-organic-links.tab", 83363118),
)
DOBBS_COLUMNS = ("state", "city", "query", "domain", "link", "position")
HTTP_TIMEOUT_SECONDS = 120.0
DOWNLOAD_CHUNK = 1 << 20


def default_dobbs_dir() -> Path:
    return forecast_paths().root / "public" / "wellesley_dobbs"


# ---------------------------------------------------------------------------
# agreement arithmetic on {site: position} dicts


@dataclass(frozen=True)
class RepeatPair:
    """Two readings of one search. Positions are 1..50; a site not in the dict is absent (51)."""

    cluster: str
    a: Mapping[str, int]
    b: Mapping[str, int]
    group: str = ""
    gap_days: float | None = None


@dataclass(frozen=True)
class Unit:
    cluster: str
    site: str
    position_a: int
    position_b: int


def _pos(positions: Mapping[str, int], site: str) -> int:
    value = positions.get(site)
    if value is None:
        return NT50
    number = int(value)
    return number if 1 <= number <= TOP_DEPTH else NT50


def pair_units(pair: RepeatPair, top: int) -> list[Unit]:
    """Every site in the top `top` of either reading, with both positions (absent = 51)."""
    sites = {s for s, p in pair.a.items() if _pos(pair.a, s) <= top} | {s for s, p in pair.b.items() if _pos(pair.b, s) <= top}
    return [Unit(pair.cluster, site, _pos(pair.a, site), _pos(pair.b, site)) for site in sorted(sites)]


def agrees(unit: Unit) -> bool:
    return abs(unit.position_a - unit.position_b) <= AGREE_WITHIN


def _share(flags: Sequence[float], clusters: Sequence[str]) -> dict[str, Any]:
    total = float(len(flags))
    got = float(sum(flags))
    low, high = cluster_bootstrap_ratio(flags, [1.0] * len(flags), clusters)
    w_low, w_high = wilson(got, total)
    return {
        "units": int(total),
        "agree": int(got),
        "share": got / total if total else None,
        "interval_bootstrap_over_searches": [low, high],
        "wilson_if_independent": [w_low, w_high],
    }


def agreement_report(pairs: Sequence[RepeatPair], label: str) -> dict[str, Any]:
    """Marker-1 style agreement of single raw pulls, with a bootstrap over searches."""
    top10 = [unit for pair in pairs for unit in pair_units(pair, TOP_UNIT)]
    top50 = [unit for pair in pairs for unit in pair_units(pair, TOP_DEPTH)]
    out: dict[str, Any] = {
        "label": label,
        "pairs": len(pairs),
        "searches": len({pair.cluster for pair in pairs}),
        "identical_pairs": sum(1 for pair in pairs if dict(pair.a) == dict(pair.b)),
    }
    if not pairs:
        out["note"] = "no pairs"
        return out
    clusters10 = [u.cluster for u in top10]
    clusters50 = [u.cluster for u in top50]
    out["headline_top10_within_2"] = _share([1.0 if agrees(u) else 0.0 for u in top10], clusters10)
    out["top50_within_2"] = _share([1.0 if agrees(u) else 0.0 for u in top50], clusters50)
    out["top10_exact_same_position"] = _share([1.0 if u.position_a == u.position_b else 0.0 for u in top10], clusters10)
    out["top10_units_present_in_both"] = _share(
        [1.0 if u.position_a <= TOP_DEPTH and u.position_b <= TOP_DEPTH else 0.0 for u in top10], clusters10
    )
    both = [u for u in top10 if u.position_a <= TOP_DEPTH and u.position_b <= TOP_DEPTH]
    out["top10_within_2_when_present_in_both"] = _share([1.0 if agrees(u) else 0.0 for u in both], [u.cluster for u in both])
    by_band: dict[str, Any] = {}
    for band in UNIT_BANDS:
        index = [i for i, u in enumerate(top50) if band_of(u.position_a) == band]
        flags = [1.0 if agrees(top50[i]) else 0.0 for i in index]
        block = _share(flags, [top50[i].cluster for i in index])
        block["band_of"] = "reading A (NT50 = absent from A, in the top 50 of B)"
        by_band[band] = block
    out["top50_by_band_of_a"] = by_band
    entered = [u for u in top10 if u.position_b <= TOP_UNIT]
    left = [u for u in top10 if u.position_a <= TOP_UNIT]
    out["top10_entry_rate"] = {
        "definition": "share of reading B's top 10 sites that were not in reading A's top 10",
        "count": len(entered),
        "rate": (sum(1 for u in entered if u.position_a > TOP_UNIT) / len(entered)) if entered else None,
    }
    out["top10_exit_rate"] = {
        "definition": "share of reading A's top 10 sites that are not in reading B's top 10",
        "count": len(left),
        "rate": (sum(1 for u in left if u.position_b > TOP_UNIT) / len(left)) if left else None,
    }
    out["rule_v1_a_as_forecast_of_b_top10_units"] = _share(
        [1.0 if RULE_V1.is_hit(u.position_a, u.position_b) else 0.0 for u in top10], clusters10
    )
    out["rule_v1_a_as_forecast_of_b_top50_units"] = _share(
        [1.0 if RULE_V1.is_hit(u.position_a, u.position_b) else 0.0 for u in top50], clusters50
    )
    gaps = [pair.gap_days for pair in pairs if pair.gap_days is not None]
    if gaps:
        ordered = sorted(gaps)
        out["gap_days"] = {"min": ordered[0], "median": ordered[len(ordered) // 2], "max": ordered[-1]}
    return out


# ---------------------------------------------------------------------------
# DataForSEO panel: pages of one keyword 0.5 to 3 days apart


def dfs_repeat_pairs(
    panel: Any,
    *,
    min_gap_days: float = REPEAT_MIN_GAP_DAYS,
    max_gap_days: float = REPEAT_MAX_GAP_DAYS,
) -> tuple[list[RepeatPair], dict[str, int]]:
    """Every ordered pair of pages of one keyword whose gap is in the window, both pages reaching rank 50.

    Returns the pairs and counts of what was left out. A pair where a page
    stops before rank 50 is dropped, because a missing site there is not known
    to be outside the top 50.
    """
    from .panel import transitions

    moves = transitions(panel, min_gap_days, max_gap_days)
    counts = {"pairs_in_window": 0, "pairs_dropped_short_page": 0, "pairs_kept": 0}
    if moves.empty:
        return [], counts
    keys = ["keyword_id", "time_a", "time_b"]
    pair_depth = moves.groupby(keys, sort=True)["depth_ok"].all()
    counts["pairs_in_window"] = int(len(pair_depth))
    counts["pairs_dropped_short_page"] = int((~pair_depth).sum())
    kept = moves.merge(pair_depth[pair_depth].reset_index()[keys], on=keys)
    pairs: list[RepeatPair] = []
    for (kid, time_a, time_b), chunk in kept.groupby(keys, sort=True):
        a = {s: int(p) for s, p in zip(chunk["site"], chunk["position_a"]) if int(p) <= TOP_DEPTH}
        b = {s: int(p) for s, p in zip(chunk["site"], chunk["position_b"]) if int(p) <= TOP_DEPTH}
        locale = str(chunk["locale"].iloc[0]) if "locale" in chunk else ""
        gap = float(chunk["gap_days"].iloc[0])
        pairs.append(RepeatPair(cluster=str(kid), a=a, b=b, group=locale, gap_days=gap))
    counts["pairs_kept"] = len(pairs)
    return pairs, counts


def dfs_noise(panel: Any) -> dict[str, Any]:
    """Single-pull agreement on DataForSEO pages 0.5 to 3 days apart: all locales and English locales."""
    pairs, counts = dfs_repeat_pairs(panel)
    english = [pair for pair in pairs if pair.group.endswith("_en")]
    per_locale: dict[str, int] = {}
    for pair in pairs:
        per_locale[pair.group] = per_locale.get(pair.group, 0) + 1
    return {
        "label": DFS_LABEL,
        "window_days": [REPEAT_MIN_GAP_DAYS, REPEAT_MAX_GAP_DAYS],
        "pair_counts": counts,
        "pairs_by_locale": dict(sorted(per_locale.items())),
        "all_locales": agreement_report(pairs, DFS_LABEL + ", all locales"),
        "english_locales": agreement_report(english, DFS_LABEL + ", English locales"),
        "caveats": [
            "Country-level pages (one location code per country), not a pinned city.",
            "Single pulls as DataForSEO stored them, not 5-pull clean readings.",
            "Why DataForSEO read these keywords twice within days is not documented.",
        ],
    }


# ---------------------------------------------------------------------------
# Wellesley Dobbs


@dataclass
class DobbsRun:
    """One Dobbs collection run: pages keyed by (query, state, city), each {site: best position}."""

    name: str
    day: date
    half: str
    pages: dict[tuple[str, str, str], dict[str, int]] = field(default_factory=dict)
    rows: int = 0
    bad_rows: int = 0


def parse_dobbs_name(name: str) -> tuple[date, str]:
    """(date, 'AM' or 'PM') from a name like 06-26-22-PM-organic-links.tab."""
    parts = name.split("-")
    if len(parts) < 4 or parts[3] not in ("AM", "PM"):
        raise ValueError(f"not a Dobbs organic file name: {name!r}")
    return date(2000 + int(parts[2]), int(parts[0]), int(parts[1])), parts[3]


def read_dobbs_run(path: str | Path) -> DobbsRun:
    """Stream one organic-links file. Columns: state, city, query, domain, link, position (1 to 10)."""
    source = Path(path)
    day, half = parse_dobbs_name(source.name)
    run = DobbsRun(name=source.name, day=day, half=half)
    with open(source, newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.reader(handle, delimiter="\t", quotechar='"')
        header = next(reader, None)
        if header is None or tuple(h.strip().lower() for h in header[:6]) != DOBBS_COLUMNS:
            raise ValueError(f"{source.name}: unexpected header {header!r}")
        for row in reader:
            if len(row) < 6:
                run.bad_rows += 1
                continue
            state, city, query, domain, link, position = row[:6]
            try:
                rank = int(float(position))
            except ValueError:
                run.bad_rows += 1
                continue
            site = site_key(domain or link)
            if not site or rank < 1 or site in EXCLUDED_SITES:
                run.bad_rows += 1
                continue
            run.rows += 1
            page = run.pages.setdefault((query, state, city), {})
            if site not in page or rank < page[site]:
                page[site] = rank
    return run


def dobbs_pairs(run_a: DobbsRun, run_b: DobbsRun) -> list[RepeatPair]:
    """Same query and same city in both runs. Run A is the earlier one. Clusters are queries."""
    pairs = []
    for key in sorted(set(run_a.pages) & set(run_b.pages)):
        pairs.append(RepeatPair(cluster=key[0], a=run_a.pages[key], b=run_b.pages[key], group=f"{run_a.name}|{run_b.name}"))
    return pairs


def dobbs_noise(dobbs_dir: str | Path) -> dict[str, Any]:
    """Morning versus evening of the same day, and evening versus the next evening, on the Dobbs files present."""
    folder = Path(dobbs_dir)
    present = {}
    for path in sorted(folder.glob("*-organic-links.tab")):
        try:
            present[parse_dobbs_name(path.name)] = path
        except ValueError:
            continue
    days = sorted({day for day, _ in present})
    am_pm: list[RepeatPair] = []
    pm_pm: list[RepeatPair] = []
    runs_used: dict[str, dict[str, int]] = {}
    plan: list[tuple[str, tuple[date, str], tuple[date, str]]] = []
    for day in days:
        if (day, "AM") in present and (day, "PM") in present:
            plan.append(("am_pm", (day, "AM"), (day, "PM")))
        nxt = day + timedelta(days=1)
        if (day, "PM") in present and (nxt, "PM") in present:
            plan.append(("pm_pm", (day, "PM"), (nxt, "PM")))
    cache: dict[tuple[date, str], DobbsRun] = {}
    pair_sets: dict[str, list[dict[str, Any]]] = {"am_pm": [], "pm_pm": []}
    for step, (kind, key_a, key_b) in enumerate(plan):
        for key in (key_a, key_b):
            if key not in cache:
                cache[key] = read_dobbs_run(present[key])
                run = cache[key]
                runs_used[run.name] = {"pages": len(run.pages), "rows": run.rows, "bad_rows": run.bad_rows}
        made = dobbs_pairs(cache[key_a], cache[key_b])
        (am_pm if kind == "am_pm" else pm_pm).extend(made)
        pair_sets[kind].append({"a": cache[key_a].name, "b": cache[key_b].name, "pairs": len(made)})
        needed = {k for _, x, y in plan[step + 1 :] for k in (x, y)}
        for key in list(cache):
            if key not in needed:
                del cache[key]
    depth = [max(pair.a.values()) for pair in am_pm + pm_pm if pair.a]
    return {
        "label": DOBBS_LABEL,
        "source": {"doi": DOBBS_DOI, "licence": "CC0 1.0", "files_used": runs_used},
        "pair_sets": pair_sets,
        "page_depth_reading_a": {
            "pages": len(depth),
            "median_deepest_position": sorted(depth)[len(depth) // 2] if depth else None,
            "share_reaching_10": (sum(1 for d in depth if d >= 10) / len(depth)) if depth else None,
        },
        "morning_vs_evening_same_day": agreement_report(am_pm, DOBBS_LABEL + ", morning versus evening of the same day"),
        "evening_vs_next_evening": agreement_report(pm_pm, DOBBS_LABEL + ", evening versus the next evening"),
        "limits": list(DOBBS_LIMITS),
    }


def download_dobbs(
    dobbs_dir: str | Path | None = None,
    *,
    client: httpx.Client | None = None,
    files: Sequence[tuple[int, str, int]] = DOBBS_FILES,
) -> dict[str, Any]:
    """Fetch the Dobbs organic files the noise set uses (about 0.85 GB), resumably, and write SOURCES.json.

    A file already present at its listed size is not fetched again. A partial
    file is resumed with a Range request. Each file's sha256 is recorded.
    """
    folder = Path(dobbs_dir) if dobbs_dir else default_dobbs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    owned = client is None
    http = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
    entries = []
    try:
        for file_id, name, size in files:
            target = folder / name
            url = DOBBS_ACCESS_URL.format(file_id=file_id)
            fetched = False
            if not target.is_file() or target.stat().st_size != size:
                partial = folder / (name + ".part")
                offset = partial.stat().st_size if partial.is_file() else 0
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                with http.stream("GET", url, headers=headers) as response:
                    if response.status_code not in (200, 206):
                        raise RuntimeError(f"{name}: HTTP {response.status_code}")
                    mode = "ab" if response.status_code == 206 and offset else "wb"
                    with open(partial, mode) as handle:
                        for block in response.iter_bytes(DOWNLOAD_CHUNK):
                            handle.write(block)
                if partial.stat().st_size != size:
                    raise RuntimeError(f"{name}: got {partial.stat().st_size} bytes, the file list says {size}")
                os.replace(partial, target)
                fetched = True
            digest = hashlib.sha256()
            with open(target, "rb") as handle:
                for block in iter(lambda: handle.read(DOWNLOAD_CHUNK), b""):
                    digest.update(block)
            mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
            entries.append(
                {
                    "path": str(target),
                    "url": url,
                    "fetched_at": utc_iso(mtime),
                    "fetched_now": fetched,
                    "bytes": target.stat().st_size,
                    "sha256": digest.hexdigest(),
                }
            )
    finally:
        if owned:
            http.close()
    payload = {
        "written_at": utc_iso(datetime.now(timezone.utc)),
        "dataset": DOBBS_DOI,
        "dataset_metadata_url": DOBBS_DATASET_URL,
        "licence": "CC0 1.0",
        "note": "Wellesley CRED lab Google SERPs, June and July 2022. Organic-links files only. The embedded tweets file is not fetched.",
        "files": entries,
    }
    (folder / "SOURCES.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"dir": str(folder), "files": len(entries), "bytes": sum(e["bytes"] for e in entries)}


# ---------------------------------------------------------------------------
# both


def run_noise(*, panel_dir: str | Path | None = None, dobbs_dir: str | Path | None = None) -> dict[str, Any]:
    """The noise numbers on the real data: the DataForSEO panel history pages, and Dobbs when its files are present."""
    from .dfs_samples import DATASET_HISTORY
    from .panel import load_panel

    panel = load_panel(
        ["dataset", "locale", "keyword_id", "snapshot_time", "rank", "site", "poison_window"],
        datasets=[DATASET_HISTORY],
        panel_dir=panel_dir,
    )
    out: dict[str, Any] = {
        "what_this_is": "Raw single-pull agreement on REAL repeated pulls. It is the baseline marker 1's clean reader must beat. It is not a clean-reader result.",
        "dataforseo": dfs_noise(panel),
    }
    if dobbs_dir is not None and any(Path(dobbs_dir).glob("*-organic-links.tab")):
        out["wellesley_dobbs"] = dobbs_noise(dobbs_dir)
    else:
        out["wellesley_dobbs"] = {"note": "no Dobbs organic files present. Run the dobbs-download command first."}
    return out


__all__ = [
    "DFS_LABEL",
    "DOBBS_FILES",
    "DOBBS_LABEL",
    "DobbsRun",
    "RepeatPair",
    "Unit",
    "agreement_report",
    "agrees",
    "dfs_noise",
    "dfs_repeat_pairs",
    "dobbs_noise",
    "dobbs_pairs",
    "download_dobbs",
    "pair_units",
    "parse_dobbs_name",
    "read_dobbs_run",
    "run_noise",
]
