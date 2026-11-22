"""Part 1, the clean reader: pulls, the poisoned-page filter and the clean reading.

What is REAL here: every `Pull` is one provider response as it came back, and a
`CleanReading` is the middle position per site over the pulls that pass the
filter. Nothing here is PREDICTED. Nothing here invents a position: a site that
is not in a pull's top 50 counts as NT50 (51) for that pull, a failed pull is a
failed pull and never an empty result, and a value that is not known stays None.

The rules follow docs/plan/IMPLEMENTATION_SPEC.md section 3. Two versions of
the filter exist. Version 2 is the default. Version 1 is kept, unchanged, so a
report can show both side by side (`version=1`).

Structural drops (both versions, applied in this order, first reason wins):

1. `not_ok`: the provider said the task failed.
2. `partial`: the pull is marked partial and has fewer organic results than
   `partial_min_organic(depth)`: ceil(PARTIAL_MIN_ORGANIC * depth / TOP_DEPTH),
   at least 1. That is 30 at depth 50 and 6 at depth 10.
3. `spell_corrected`: Google answered a corrected spelling.

Version 1, rule 4, `low_overlap`: a pull is dropped when fewer than
POISON_MIN_OVERLAP of the distinct sites in its top POISON_TOP_N appear in the
top 50 of at least one other usable pull. Leave-one-out, all at once, only with
at least 2 other usable pulls. Its known limit: pulls altered the same way
vouch for each other, and a clean pull in the minority is the one dropped.

OUR READING, version 2 (READER_VERSION "reader-v2"). The evidence that made it:
the committed Serper pulls of 19 Nov 2026 (three pulls of 70 searches, top 10,
about 35 to 40 minutes apart) were taken while Google served altered pages to
scrapers. Rule 4 of version 1 dropped 17 of 210 pulls and left 15 of 70
readings degraded. Altered pulls are correlated inside one session: in at least
4 searches two altered pulls agree with each other and outvote the one clean
pull. In "assignar alternative" pulls a and c are slashdot.org in 7 slots, then
softwr.com, itqlick.com and gitnux.org, while pull b is g2.com,
softwareadvice.com, cbinsights.com, capterra.com, assignar.com and so on. In
"hcss pricing" pull a is hcss.com in 5 slots with reddit, trustradius and
softwarefinder, and pulls b and c are slashdot.org and itqlick.com in 7 or more
slots. Across the 210 pulls the top site holds 1 slot in 97 pulls, 2 in 77, 3
in 17, 4 in 4, 5 in 7, 6 in 3, 7 in 3 and 9 in 2. The sites holding 4 or more
slots are hcss.com (7 pulls, all searches that name hcss, so legitimate),
itqlick.com (5), slashdot.org (3), gitnux.org (2), indeed.com and google.com
(1 each). Published figures: about 8% of pages altered since 13 Sep 2026, near
30% on bad days, 78% of spells recover within 3 days, a retry returns a normal
page about 3 times in 4. So majority alone cannot pick the clean page.

Version 2, after the structural drops:

A. Clusters. Two usable pulls link when each has at least LINK_MIN_SHARE of
   its top CLUSTER_TOP_N slots (capped at the requested depth) held by sites
   that are in the other pull's top 50. Slots are counted, not distinct sites,
   so a page where one site fills 7 slots weighs those 7 slots. Two pulls with
   no slots link to each other and to nothing else. Clusters are the connected
   groups of linked pulls.
B. One cluster: every pull is kept. A stable page where one site really holds
   many slots is the truth, not poison. One exception: when an earlier clean
   reading is supplied, the cluster overlaps it by less than HISTORY_MIN_SHARE,
   and the cluster is flagged by signal (b) or (c) below, every pull is dropped
   (`altered_vs_history`) and the reading is degraded.
C. More than one cluster: the cluster to keep is chosen by signals that do not
   depend on which cluster is larger. Each signal can only narrow the
   candidates. It narrows them when some candidates pass and some fail. When
   one candidate is left, it is kept. The signals, in order:
   0. provider repair: clusters that hold a pull the provider repaired
      (`Pull.repaired`) beat clusters that hold none;
   a. history: when an earlier non-degraded clean reading of the same search is
      supplied, the clusters whose pulls on average have at least
      HISTORY_MIN_SHARE of their top slots held by sites in that reading's top
      50, and are within HISTORY_MARGIN of the best such cluster. This is the
      data provider's own definition of an altered page: fewer than half of the
      usual sites appear;
   b. off-brand concentration: a pull is flagged when one site whose domain
      shares no token of BRAND_TOKEN_MIN_LETTERS letters or more with the
      search holds CONCENTRATION_MIN_SLOTS or more of the top
      CONCENTRATION_TOP_N slots. Altered pages are runs of one unrelated site.
      A cluster is flagged when at least CLUSTER_FLAG_MIN_SHARE of its pulls
      are flagged. Unflagged clusters win;
   c. round ubiquity: a pull is flagged when UBIQUITY_MIN_SLOTS or more of its
      top CONCENTRATION_TOP_N slots are held by round suspects that are
      unrelated to the search. A round suspect is a site that holds
      SUSPECT_MIN_SLOTS or more slots in at least SUSPECT_MIN_SEARCHES OTHER
      searches of the same pull round that it is unrelated to
      (`round_suspects`). Cluster flags work as in (b);
   d. only then cluster size: the largest candidate wins.
   A tie that no signal breaks drops every clustered pull (`cluster_tie`) and
   the reading is degraded, with the reason. It never silently picks one.
   Pulls of the clusters not chosen are dropped as `other_cluster`, with the
   signal that decided.
D. A reading still needs MIN_KEPT_PULLS kept pulls. When the clean pulls are a
   minority and too few, the reading is degraded, not wrong. Repaired pulls in
   the kept cluster are kept and counted like any other.

Calibration on clean-era data (the DataForSEO history samples, 117 locales,
53,091 dated result pages from 1 May 2025 to 30 April 2026, before the
altered-page era; `noise.calibrate_on_dataforseo_history` reruns it):

- Splits. Of 2,671 re-pulls of one search less than a day apart (median gap
  4.4 hours), 5 (0.19%) would land in different clusters at LINK_MIN_SHARE 0.5
  on the top 10 and 1 (0.04%) on the top 20 against the top 50. At 0.6 it is
  0.56% and 0.19%. So 0.5, the provider's own line, almost never splits a
  clean page.
- Signal (b) alone flags 13.8%, 6.1% and 3.1% of clean English pages at 3, 4
  and 5 slots (17.7%, 8.5% and 4.7% over all locales): YouTube, Pinterest,
  government and similar sites really do hold runs. CONCENTRATION_MIN_SLOTS is
  4 because on 19 Nov an injected directory held 4 slots in "agtek
  alternative" (gitnux.org), and at 5 that search keeps the altered pair. The
  cost is small because (b) only ever chooses between clusters, and clean
  re-pulls split about 2 times in 1,000.
- Signal (c) alone, with one locale and one month standing in for a round
  (a month holds more searches than a round, so this overstates it), flags
  5.8%, 3.1% and 1.6% of clean English pages at 3, 4 and 5 suspect slots
  (9.2%, 5.5% and 3.3% over all locales). UBIQUITY_MIN_SLOTS is 4: at 3,
  reddit.com (a real, everywhere site that holds 3 slots in 2 searches of a
  round) makes version 2 drop the legitimate pair in one 19 Nov search.

Known limits, stated and tested: pulls altered the same way with no run of one
site, no round suspects and no history win on size (19 Nov "tenna
alternative"). A single cluster that is altered throughout is kept when no
history is supplied. Two clean pages that Google really serves in two forms
can split, and the smaller is dropped (19 Nov "trimble viewpoint alternative").

The middle position: per site, its best organic rank in each kept pull, or 51
when absent. The clean position is the median exactly as
`aeo.ranksim.consensus.median_position` computes it (even count: the mean of
the two middle values rounded half up). A median above 50 is NT50 and the site
is not listed. A reading with fewer than MIN_KEPT_PULLS kept pulls is degraded.

Round mode: the pulls of one reading may be hours apart. `fetch_round` makes
one round, `round_suspects` reads the suspects of a round, and
`assemble_readings` builds one reading per request from the pulls whose
completed times fall inside one window of `window_hours`. `read_clean` uses
them inside.

Provenance on every reading: `reader_version` (READER_VERSION, or the name
with "-filter-off" for the ablation) and, for a degraded reading,
`degraded_reason`, one line that starts with one of DEGRADED_REASONS. The
label is set here: a reading holding any pull of a synthetic provider
(`types.SYNTHETIC_PROVIDERS`) is SYNTHETIC, every other reading is REAL. A
history reading must carry the same label as the pulls it judges.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from aeo.crawl.urls import domain_of
from aeo.ranksim.consensus import median_position
from aeo.ranksim.site_history import site_key

from .asof import parse_utc, utc_iso
from .chain import canonical_json, sha256_text
from .config import (
    DEFAULT_PULLS,
    EXCLUDED_SITES,
    MIN_KEPT_PULLS,
    MIN_PULL_GAP_SECONDS,
    NT50,
    PARTIAL_MIN_ORGANIC,
    POISON_MIN_OVERLAP,
    POISON_TOP_N,
    TOP_DEPTH,
)
from .types import REAL, SYNTHETIC, SYNTHETIC_PROVIDERS, CleanReading, OrganicHit, Pull, PullSpec, SerpSource, SitePosition

READER_VERSION_V1 = "reader-v1"
READER_VERSION_V2 = "reader-v2"
READER_VERSIONS: dict[int, str] = {1: READER_VERSION_V1, 2: READER_VERSION_V2}
DEFAULT_RULE_VERSION = 2
READER_VERSION = READER_VERSIONS[DEFAULT_RULE_VERSION]
ID_HEX_CHARS = 24

# Version 2 thresholds. Each one is explained in the module docstring.
CLUSTER_TOP_N = POISON_TOP_N  # top slots compared when linking two pulls (capped at the requested depth)
LINK_MIN_SHARE = 0.5  # each pull needs this share of its top slots held by sites in the other's top 50
CONCENTRATION_TOP_N = 10  # slots read for signals (b) and (c)
CONCENTRATION_MIN_SLOTS = 4  # one off-brand site holding this many of the top 10 slots flags the pull
BRAND_TOKEN_MIN_LETTERS = 3  # shortest token that ties a domain to a search
BRAND_STOP_TOKENS: frozenset[str] = frozenset(
    {"the", "and", "for", "with", "how", "what", "are", "you", "your", "can", "from", "www", "com", "org", "net"}
)
SUSPECT_MIN_SLOTS = 3  # a site needs this many slots in one search to count that search for signal (c)
SUSPECT_MIN_SEARCHES = 2  # ... in at least this many OTHER unrelated searches of the round
UBIQUITY_MIN_SLOTS = 4  # top-10 slots held by unrelated round suspects that flag a pull
CLUSTER_FLAG_MIN_SHARE = 0.5  # a cluster is flagged when at least this share of its pulls is flagged
HISTORY_MIN_SHARE = 0.5  # the provider's own line: fewer than half of the usual sites means altered
HISTORY_MARGIN = 0.10  # clusters this close to the best history overlap stay candidates
DEFAULT_WINDOW_HOURS = 12.0  # pulls of one reading in round mode; below marker 1's 20 hour minimum gap

# Drop reasons, in the order the filter tests them.
DROP_NOT_OK = "not_ok"
DROP_PARTIAL = "partial"
DROP_SPELL = "spell_corrected"
DROP_LOW_OVERLAP = "low_overlap"  # version 1 only
DROP_OTHER_CLUSTER = "other_cluster"  # version 2
DROP_CLUSTER_TIE = "cluster_tie"  # version 2
DROP_ALTERED_VS_HISTORY = "altered_vs_history"  # version 2
DROP_NO_RESULT = "no_result"  # used only when a whole fetch round failed for a spec
DROP_REASONS = (
    DROP_NOT_OK, DROP_PARTIAL, DROP_SPELL, DROP_LOW_OVERLAP, DROP_OTHER_CLUSTER, DROP_CLUSTER_TIE,
    DROP_ALTERED_VS_HISTORY,
)

# Why a reading is degraded (`CleanReading.degraded_reason` starts with one of these).
DEGRADED_CLUSTER_TIE = DROP_CLUSTER_TIE
DEGRADED_ALTERED_VS_HISTORY = DROP_ALTERED_VS_HISTORY
DEGRADED_TOO_FEW_KEPT = "too_few_kept_pulls"
DEGRADED_NO_PULLS = "no_pulls"
DEGRADED_INVALID_PULLS = "invalid_pulls"
FILTER_OFF_SUFFIX = "-filter-off"
DEGRADED_SHALLOW = "shallow_depth"
DEGRADED_REASONS = (
    DEGRADED_CLUSTER_TIE, DEGRADED_ALTERED_VS_HISTORY, DEGRADED_TOO_FEW_KEPT, DEGRADED_NO_PULLS, DEGRADED_INVALID_PULLS,
    DEGRADED_SHALLOW,
)

# Providers whose pulls are never REAL: the simulated world and the DataForSEO sandbox, whose answers
# are dummy data ("All the results you receive within Sandbox contain dummy data", provider map s7).
# A name that starts with one of these and a dash (e.g. "dataforseo-sandbox-live") counts too.
SANDBOX_PROVIDERS: frozenset[str] = frozenset({"dataforseo-sandbox"})
NOT_REAL_PROVIDERS: frozenset[str] = frozenset(SYNTHETIC_PROVIDERS) | SANDBOX_PROVIDERS

SIGNAL_REPAIRED = "provider repair"
SIGNAL_HISTORY = "history"
SIGNAL_CONCENTRATION = "off-brand concentration"
SIGNAL_UBIQUITY = "round ubiquity"
SIGNAL_SIZE = "cluster size"

# The committed Serper pulls (evals/claude/serp/<day>/<pull>/chunk-NN.json.gz).
# They sent {"gl": "us", "hl": "en", "num": 10} and no location and no device.
# 2840 is the Google geo target id of the United States, a country, not a city.
SERPER_PROVIDER = "serper"
SERPER_COUNTRY_LOCATION_CODE = 2840
SERPER_DEVICE_UNSPECIFIED = "unspecified"
SERPER_CHUNK_ENDPOINT = "https://google.serper.dev/search gl=us num=10 (no pinned city, no device, top 10 only)"


# ---------------------------------------------------------------------------
# Ids


def _spec_payload(spec: PullSpec) -> dict[str, object]:
    return {
        "search": spec.search,
        "location_code": int(spec.location_code),
        "language_code": spec.language_code,
        "device": spec.device,
        "depth": int(spec.depth),
    }


def new_pull_id(provider: str, spec: PullSpec, requested_at: str, *, sequence: int | str = 0) -> str:
    """A deterministic pull id: a sha256 prefix of provider, the request and the requested UTC time.

    `sequence` separates two pulls of the same request made in the same second.
    """
    payload = {"provider": provider, "spec": _spec_payload(spec), "requested_at": requested_at, "sequence": str(sequence)}
    return "pull-" + sha256_text(canonical_json(payload))[:ID_HEX_CHARS]


def _version_name(version: int) -> str:
    try:
        return READER_VERSIONS[int(version)]
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"there is no reader version {version!r}. Known versions: {sorted(READER_VERSIONS)}") from None


def reader_version_name(version: int = DEFAULT_RULE_VERSION, *, overlap_rule: bool = True) -> str:
    """The name stored on a reading: "reader-v2", or "reader-v2-filter-off" for the ablation with the filter off."""
    name = _version_name(version)
    return name if overlap_rule else name + FILTER_OFF_SUFFIX


def provider_is_real(provider: str | None) -> bool:
    """False for a provider whose answers are not observations of Google: the simulated world
    (types.SYNTHETIC_PROVIDERS) and the DataForSEO sandbox, which answers with dummy data."""
    name = str(provider or "")
    if name in NOT_REAL_PROVIDERS:
        return False
    return not any(name.startswith(prefix + "-") for prefix in NOT_REAL_PROVIDERS)


def reading_label(pulls: Iterable[Pull]) -> str:
    """SYNTHETIC when any pull comes from a provider that is not real (`provider_is_real`), else REAL.

    A reading that holds even one simulated or sandbox pull is not an observation
    of Google, so it is never labeled REAL.
    """
    return SYNTHETIC if any(not provider_is_real(pull.provider) for pull in pulls) else REAL


def new_reading_id(
    spec: PullSpec,
    pull_ids: Sequence[str],
    *,
    version: int = DEFAULT_RULE_VERSION,
    overlap_rule: bool = True,
    history_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """A deterministic reading id from the request, the set of pull ids (order does not matter) and the reader.

    Everything that can change what the reader makes of the same pulls is in the id, so two
    different readings never share one: the filter switched off (the ablation), the history
    reading it was judged against, and `extra` (the round suspect slots that the filter saw, or
    for a reading with no pulls its time and reasons). The payload of a plain reading (filter on,
    no history, nothing extra) is the one ids were always made from, so those ids do not change.
    """
    payload: dict[str, Any] = {"spec": _spec_payload(spec), "pull_ids": sorted(pull_ids), "reader": _version_name(version)}
    if not overlap_rule:
        payload["reader"] = reader_version_name(version, overlap_rule=False)
    if history_id is not None:
        payload["history"] = str(history_id)
    if extra:
        payload["extra"] = dict(extra)
    return "reading-" + sha256_text(canonical_json(payload))[:ID_HEX_CHARS]


# ---------------------------------------------------------------------------
# Replay source


class ReplaySource:
    """A SerpSource over pulls already on disk or in memory. For tests and dry runs. It never spends.

    Pulls are served in the order given, per exact `PullSpec`. When a spec has
    no pull left, `fetch` returns a failed pull for it (ok False), never an
    empty result and never an exception.
    """

    def __init__(self, pulls: Iterable[Pull], *, name: str = "replay", clock: Callable[[], datetime] | None = None) -> None:
        self.name = name
        self._queues: dict[PullSpec, list[Pull]] = defaultdict(list)
        for pull in pulls:
            self._queues[pull.spec].append(pull)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.served = 0
        self.misses = 0

    def remaining(self, spec: PullSpec) -> int:
        return len(self._queues.get(spec, ()))

    def fetch(self, specs: Sequence[PullSpec]) -> list[Pull]:
        out: list[Pull] = []
        for spec in specs:
            queue = self._queues.get(spec)
            if queue:
                out.append(queue.pop(0))
                self.served += 1
                continue
            stamp = utc_iso(self._clock())
            self.misses += 1
            out.append(
                Pull(
                    pull_id=new_pull_id(self.name, spec, stamp, sequence=f"miss-{self.misses}"),
                    provider=self.name,
                    endpoint="replay",
                    spec=spec,
                    requested_at=stamp,
                    completed_at=stamp,
                    ok=False,
                    status_message="replay has no more pulls for this request",
                )
            )
        return out

    def estimate_cost(self, count: int, depth: int = TOP_DEPTH) -> float:
        return 0.0


# ---------------------------------------------------------------------------
# Committed Serper chunks


def _serper_item_pull(item: dict, *, path: Path, index: int, label: str, did_you_mean_is_correction: bool) -> Pull:
    query = str(item.get("query") or item.get("q") or "")
    if not query:
        raise ValueError(f"{path} item {index} has no query")
    if not item.get("start"):
        raise ValueError(f"{path} item {index} has no start time, so the pull cannot be dated")
    requested_at = utc_iso(parse_utc(str(item["start"])))
    completed_at = utc_iso(parse_utc(str(item.get("end") or item["start"])))
    raw_text = item.get("raw")
    raw_sha = sha256_text(raw_text) if isinstance(raw_text, str) else None
    status = item.get("status")
    status_code = int(status) if status is not None else None

    payload: dict | None = None
    problem: str | None = None
    if status_code != 200:
        problem = f"HTTP status {status_code}"
    elif not isinstance(raw_text, str):
        problem = "no raw response stored"
    else:
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as error:
            problem = f"raw response is not JSON: {error.msg}"
        if payload is not None and not isinstance(payload, dict):
            problem, payload = "raw response is not a JSON object", None
        if payload is not None and not isinstance(payload.get("organic"), list):
            problem, payload = "raw response has no organic list", None

    params = (payload or {}).get("searchParameters") or {}
    depth = int(params.get("num") or 10)
    spec = PullSpec(
        search=query,
        location_code=SERPER_COUNTRY_LOCATION_CODE,
        language_code=str(params.get("hl") or "en"),
        device=SERPER_DEVICE_UNSPECIFIED,
        depth=depth,
    )
    common = dict(
        pull_id=new_pull_id(SERPER_PROVIDER, spec, requested_at, sequence=label),
        provider=SERPER_PROVIDER,
        endpoint=SERPER_CHUNK_ENDPOINT,
        spec=spec,
        requested_at=requested_at,
        completed_at=completed_at,
        status_code=status_code,
        raw_path=f"{path}#items[{index}]",
        raw_sha256=raw_sha,
    )
    if payload is None:
        return Pull(ok=False, status_message=problem, **common)

    hits: list[OrganicHit] = []
    organic = payload["organic"]
    for entry in organic:
        if not isinstance(entry, dict) or not entry.get("link"):
            continue
        try:
            rank = int(entry["position"])
        except (KeyError, TypeError, ValueError):
            continue  # an organic row without a usable position is not given one
        link = str(entry["link"])
        site = site_key(link)
        if rank < 1 or rank > depth or not site or site in EXCLUDED_SITES:
            continue  # the provider's rank is kept, so a dropped row leaves a gap and moves nobody
        hits.append(OrganicHit(rank=rank, url=link, domain=domain_of(link), site=site, title=entry.get("title")))
    info = payload.get("searchInformation") or {}
    suggestion = info.get("didYouMean") if isinstance(info, dict) else None
    return Pull(
        ok=True,
        status_message=f"didYouMean: {suggestion}" if suggestion else None,
        partial=len(organic) < depth,
        spell_corrected=bool(suggestion) and did_you_mean_is_correction,
        hits=tuple(sorted(hits, key=lambda hit: (hit.rank, hit.url))),
        **common,
    )


def load_serper_chunk_pulls(directory: str | Path, *, did_you_mean_is_correction: bool = False) -> list[Pull]:
    """Pulls from committed Serper chunks: `<directory>/chunk-NN.json.gz`, one Pull per stored item.

    REAL, but weaker than the plan's reader: top 10 only, `gl=us`, no pinned
    city and no device. That is said on every Pull (`endpoint`, the country
    `location_code` 2840 and the device "unspecified"). Hits for EXCLUDED_SITES
    are dropped and the other ranks are left as Google gave them. A stored item
    with a non-200 status or an unreadable body is a failed Pull. `partial` is
    True when fewer organic results came back than were asked for.

    Serper's `didYouMean` is a suggestion. Whether Google answered the corrected
    query instead is not known from the stored response, so by default it is
    kept in `status_message` and does not mark the pull spell corrected.
    """
    folder = Path(directory)
    files = sorted(folder.glob("chunk-*.json.gz"))
    if not files:
        raise FileNotFoundError(f"no chunk-*.json.gz files in {folder}")
    pulls: list[Pull] = []
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            document = json.load(handle)
        label = str(document.get("label") or folder.name)
        for index, item in enumerate(document.get("items") or []):
            pulls.append(
                _serper_item_pull(item, path=path, index=index, label=label, did_you_mean_is_correction=did_you_mean_is_correction)
            )
    return pulls


# ---------------------------------------------------------------------------
# The poisoned-page filter: shared pieces


def partial_min_organic(depth: int) -> int:
    """Fewest organic results a partial pull may have and still be kept, scaled to the requested depth.

    ceil(PARTIAL_MIN_ORGANIC * depth / TOP_DEPTH), at least 1: 30 at depth 50, 6 at depth 10.
    """
    if depth < 1:
        raise ValueError(f"depth {depth} is below 1")
    return max(1, math.ceil(PARTIAL_MIN_ORGANIC * int(depth) / TOP_DEPTH))


def pull_site_positions(pull: Pull) -> dict[str, int]:
    """Best organic rank per site, inside the requested depth and the top 50, excluded sites removed."""
    limit = min(int(pull.spec.depth), TOP_DEPTH)
    return {site: rank for site, rank in pull.site_positions().items() if rank <= limit and site not in EXCLUDED_SITES}


def _top_sites(pull: Pull, top_n: int) -> set[str]:
    limit = min(top_n, int(pull.spec.depth), TOP_DEPTH)
    return {site for site, rank in pull_site_positions(pull).items() if rank <= limit}


def _check_one_request(pulls: Sequence[Pull]) -> PullSpec:
    if not pulls:
        raise ValueError("a reading needs at least one pull")
    first = pulls[0].spec
    for pull in pulls[1:]:
        if pull.spec != first:
            raise ValueError(
                f"pulls of one reading must share one request. {pull.pull_id} asked for {pull.spec}, not {first}"
            )
    ids = [pull.pull_id for pull in pulls]
    if len(set(ids)) != len(ids):
        raise ValueError("the same pull id appears twice in one reading")
    return first


def _structural_drops(pulls: Sequence[Pull], spec: PullSpec) -> tuple[list[Pull], dict[str, str]]:
    threshold = partial_min_organic(spec.depth)
    dropped: dict[str, str] = {}
    usable: list[Pull] = []
    for pull in pulls:
        if not pull.ok:
            detail = " ".join(str(part) for part in (pull.status_code, pull.status_message) if part is not None)
            dropped[pull.pull_id] = f"{DROP_NOT_OK}: {detail}" if detail else DROP_NOT_OK
        elif pull.partial and len(pull.hits) < threshold:
            dropped[pull.pull_id] = f"{DROP_PARTIAL}: {len(pull.hits)} organic results, fewer than {threshold}"
        elif pull.spell_corrected:
            dropped[pull.pull_id] = DROP_SPELL
        else:
            usable.append(pull)
    return usable, dropped


def _low_overlap_v1(usable: Sequence[Pull], dropped: dict[str, str]) -> None:
    """Version 1, rule 4, exactly as it was frozen. Writes into `dropped`."""
    if len(usable) < 3:
        return
    top50 = {pull.pull_id: set(pull_site_positions(pull)) for pull in usable}
    for pull in usable:
        mine = _top_sites(pull, POISON_TOP_N)
        seen_elsewhere: set[str] = set()
        for other in usable:
            if other.pull_id != pull.pull_id:
                seen_elsewhere |= top50[other.pull_id]
        if not mine:
            if seen_elsewhere:
                dropped[pull.pull_id] = f"{DROP_LOW_OVERLAP}: no sites in its top {POISON_TOP_N}, the other pulls have some"
            continue
        share = len(mine & seen_elsewhere) / len(mine)
        if share < POISON_MIN_OVERLAP:
            dropped[pull.pull_id] = (
                f"{DROP_LOW_OVERLAP}: {len(mine & seen_elsewhere)} of its {len(mine)} top sites "
                f"appear in another pull's top {TOP_DEPTH}, below {POISON_MIN_OVERLAP:g}"
            )


# ---------------------------------------------------------------------------
# Version 2 signals


_TOKEN = re.compile(r"[^\W_]+")


def search_tokens(search: str) -> frozenset[str]:
    """Lower-case word tokens of a search with BRAND_TOKEN_MIN_LETTERS letters or more, stop words removed."""
    return frozenset(
        token for token in _TOKEN.findall(str(search).lower())
        if len(token) >= BRAND_TOKEN_MIN_LETTERS and token not in BRAND_STOP_TOKENS
    )


def site_name(site: str) -> str:
    """The name part of a registrable domain: `hcss` for hcss.com, `getapp` for getapp.com.au."""
    return str(site).lower().split(".")[0]


def site_shares_token(site: str, search: str) -> bool:
    """True when the site's domain and the search share a token of BRAND_TOKEN_MIN_LETTERS letters or more.

    A search token inside the domain name counts (`hcss` in hcss.com for "hcss
    pricing"), and so does a domain name token inside the search written without
    spaces. Such a site is on-brand for the search. Any other site is off-brand.
    """
    name = site_name(site)
    if not name:
        return False
    tokens = search_tokens(search)
    if any(token in name for token in tokens):
        return True
    joined = "".join(_TOKEN.findall(str(search).lower()))
    return any(
        len(part) >= BRAND_TOKEN_MIN_LETTERS and part not in BRAND_STOP_TOKENS and part in joined
        for part in _TOKEN.findall(name)
    )


def slot_sites(pull: Pull, top_n: int = CONCENTRATION_TOP_N) -> list[str]:
    """The site of every organic slot ranked 1 to top_n (capped at the requested depth), best first.

    A site that holds three slots appears three times. Excluded sites are left
    out, so their slots count for nobody.
    """
    limit = min(int(top_n), int(pull.spec.depth), TOP_DEPTH)
    hits = sorted((hit for hit in pull.hits if 1 <= hit.rank <= limit), key=lambda hit: (hit.rank, hit.url))
    return [hit.site for hit in hits if hit.site and hit.site not in EXCLUDED_SITES]


def off_brand_concentration(pull: Pull, search: str | None = None) -> tuple[str | None, int]:
    """(site, slots): the off-brand site holding the most of the top CONCENTRATION_TOP_N slots.

    Ties go to the alphabetically first site. (None, 0) when every slot is on-brand or empty.
    """
    text = pull.spec.search if search is None else search
    counts = Counter(site for site in slot_sites(pull, CONCENTRATION_TOP_N) if not site_shares_token(site, text))
    if not counts:
        return None, 0
    site, slots = min(counts.items(), key=lambda item: (-item[1], item[0]))
    return site, int(slots)


def concentration_flag(pull: Pull) -> bool:
    """Signal (b) for one pull: one off-brand site holds CONCENTRATION_MIN_SLOTS or more of the top slots."""
    return off_brand_concentration(pull)[1] >= CONCENTRATION_MIN_SLOTS


@dataclass(frozen=True)
class RoundSuspects:
    """Sites that hold many slots in unrelated searches of one pull round. REAL counts of REAL pulls.

    `searches_by_site[site]` holds the searches in which the site held
    SUSPECT_MIN_SLOTS or more of the top CONCENTRATION_TOP_N slots while sharing
    no token with the search. A site is a suspect for a search when it did so
    in at least SUSPECT_MIN_SEARCHES OTHER searches.

    `pull_ids` lists every pull of the round it was read from (`round_suspects`
    fills it), so a pull is judged against the suspects of its own round.
    """

    searches_by_site: Mapping[str, frozenset[str]] = field(default_factory=dict)
    rounds: int = 1
    pull_ids: frozenset[str] = frozenset()

    def is_suspect(self, site: str, search: str) -> bool:
        others = self.searches_by_site.get(site, frozenset()) - {search}
        return len(others) >= SUSPECT_MIN_SEARCHES

    def sites(self) -> frozenset[str]:
        """Sites that are a suspect for at least one search (held many slots in SUSPECT_MIN_SEARCHES + 1 searches or more)."""
        return frozenset(site for site, found in self.searches_by_site.items() if len(found) >= SUSPECT_MIN_SEARCHES)

    def merged(self, other: "RoundSuspects") -> "RoundSuspects":
        """The union of two rounds: a site's searches are joined.

        Not used by the reader: signal (c) judges each pull against its own round only (spec s3:
        "unrelated searches of the same round"). Kept for reports.
        """
        joined: dict[str, set[str]] = {site: set(found) for site, found in self.searches_by_site.items()}
        for site, found in other.searches_by_site.items():
            joined.setdefault(site, set()).update(found)
        return RoundSuspects(
            searches_by_site={site: frozenset(found) for site, found in sorted(joined.items())},
            rounds=self.rounds + other.rounds,
            pull_ids=frozenset(self.pull_ids) | frozenset(other.pull_ids),
        )


def round_suspects(pulls_of_a_round: Iterable[Pull]) -> RoundSuspects:
    """Signal (c), computed once per round over every search of the round.

    Only usable pulls count (ok, not spell corrected, not a short partial). A
    site counts a search when it holds SUSPECT_MIN_SLOTS or more of that
    search's top CONCENTRATION_TOP_N slots and shares no token with it.
    """
    found: dict[str, set[str]] = defaultdict(set)
    ids: set[str] = set()
    for pull in pulls_of_a_round:
        ids.add(pull.pull_id)
        if not pull.ok or pull.spell_corrected or (pull.partial and len(pull.hits) < partial_min_organic(pull.spec.depth)):
            continue
        counts = Counter(slot_sites(pull, CONCENTRATION_TOP_N))
        for site, slots in counts.items():
            if slots >= SUSPECT_MIN_SLOTS and not site_shares_token(site, pull.spec.search):
                found[site].add(pull.spec.search)
    return RoundSuspects(
        searches_by_site={site: frozenset(searches) for site, searches in sorted(found.items())},
        pull_ids=frozenset(ids),
    )


def _round_list(suspects: "RoundSuspects | Iterable[RoundSuspects] | None") -> RoundSuspects | tuple[RoundSuspects, ...] | None:
    """One RoundSuspects as given, or several as a tuple (one per round). Checks the types."""
    if suspects is None or isinstance(suspects, RoundSuspects):
        return suspects
    items = tuple(suspects)
    for item in items:
        if not isinstance(item, RoundSuspects):
            raise TypeError(f"suspects must be RoundSuspects, not {type(item).__name__}")
    if not items:
        return None
    return items[0] if len(items) == 1 else items


def suspects_for_pull(pull: Pull, suspects: "RoundSuspects | Iterable[RoundSuspects] | None") -> RoundSuspects | None:
    """The round suspects a pull is judged against (signal c): those of its own round only.

    One RoundSuspects is taken as the pull's round. With several (one per round), the one whose
    `pull_ids` holds the pull is used, and a pull in none of them gets no suspects (its round is
    not known, so signal c cannot fire for it). Several rounds that carry no pull ids at all
    (built by hand) cannot be told apart and are joined, as before.
    """
    rounds = _round_list(suspects)
    if rounds is None or isinstance(rounds, RoundSuspects):
        return rounds
    for item in rounds:
        if pull.pull_id in item.pull_ids:
            return item
    if not any(item.pull_ids for item in rounds):
        joined = rounds[0]
        for item in rounds[1:]:
            joined = joined.merged(item)
        return joined
    return None


def ubiquity_slots(pull: Pull, suspects: "RoundSuspects | Iterable[RoundSuspects] | None") -> int:
    """Top CONCENTRATION_TOP_N slots held by suspects of the pull's own round that share no token with the search."""
    suspects = suspects_for_pull(pull, suspects)
    if suspects is None:
        return 0
    search = pull.spec.search
    return sum(
        1 for site in slot_sites(pull, CONCENTRATION_TOP_N)
        if not site_shares_token(site, search) and suspects.is_suspect(site, search)
    )


def ubiquity_flag(pull: Pull, suspects: "RoundSuspects | Iterable[RoundSuspects] | None") -> bool:
    """Signal (c) for one pull: UBIQUITY_MIN_SLOTS or more top slots held by unrelated round suspects."""
    return ubiquity_slots(pull, suspects) >= UBIQUITY_MIN_SLOTS


def _cluster_slots(pull: Pull, reach: int | None = None) -> list[str]:
    limit = CLUSTER_TOP_N if reach is None else min(CLUSTER_TOP_N, int(reach))
    return slot_sites(pull, limit)


def _reach(pull: Pull) -> int:
    """The deepest organic rank the pull reports inside its depth and the top 50. 0 when it has none."""
    limit = min(int(pull.spec.depth), TOP_DEPTH)
    return max((hit.rank for hit in pull.hits if 1 <= hit.rank <= limit), default=0)


def slot_share(pull: Pull, other_sites: set[str] | frozenset[str], *, reach: int | None = None) -> float | None:
    """Share of the pull's top CLUSTER_TOP_N slots held by sites in `other_sites`.

    With `reach`, only slots ranked at or above it count (the other page did not
    report anything deeper). None when no slot counts.
    """
    slots = _cluster_slots(pull, reach)
    if not slots:
        return None
    return sum(1 for site in slots if site in other_sites) / len(slots)


def link_shares(first: Pull, second: Pull) -> tuple[float | None, float | None]:
    """The two shares `pulls_link` compares: each pull's top slots held by sites in the other's top 50.

    Each pull's slots are counted only down to the deepest rank the other pull
    reports, so a short page that is the head of a longer one is not punished
    for what the other page never showed. None for a pull with no slots.
    """
    share_a = slot_share(first, set(pull_site_positions(second)), reach=_reach(second))
    share_b = slot_share(second, set(pull_site_positions(first)), reach=_reach(first))
    return share_a, share_b


def pulls_link(first: Pull, second: Pull, *, min_share: float = LINK_MIN_SHARE) -> bool:
    """Two pulls show the same page: each has `min_share` (LINK_MIN_SHARE) of its top slots in the other's top 50.

    See `link_shares`. Two pulls with no slots link. A pull with no slots never
    links to one with slots.
    """
    if not _cluster_slots(first) or not _cluster_slots(second):
        return not _cluster_slots(first) and not _cluster_slots(second)
    share_a, share_b = link_shares(first, second)
    return min(share_a or 0.0, share_b or 0.0) >= min_share


def cluster_pulls(pulls: Sequence[Pull]) -> list[list[Pull]]:
    """Connected groups of linked pulls. Each group and the list of groups are in completed-time order."""
    ordered = sorted(pulls, key=_order_key)
    parent = list(range(len(ordered)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            if pulls_link(ordered[i], ordered[j]):
                a, b = root(i), root(j)
                if a != b:
                    parent[max(a, b)] = min(a, b)
    groups: dict[int, list[Pull]] = {}
    for i, pull in enumerate(ordered):
        groups.setdefault(root(i), []).append(pull)
    return [groups[key] for key in sorted(groups)]


def history_share(pull: Pull, history: CleanReading) -> float | None:
    """Share of the pull's top slots held by sites with a clean position in the earlier reading. None with no slots."""
    return slot_share(pull, frozenset(history.ranked_sites()))


def _check_history(history: CleanReading | None, spec: PullSpec, pulls: Sequence[Pull]) -> CleanReading | None:
    if history is None:
        return None
    if history.search != spec.search or int(history.location_code) != int(spec.location_code) or history.device != spec.device:
        raise ValueError(
            f"history must be a reading of the same search, location and device. Got {history.search!r} "
            f"{history.location_code} {history.device}, for {spec.search!r} {spec.location_code} {spec.device}"
        )
    if history.degraded:
        raise ValueError("history must be a non-degraded clean reading. This one is degraded.")
    if history.label != reading_label(pulls):
        raise ValueError(
            f"history is {history.label} but the pulls make a {reading_label(pulls)} reading. "
            "Real and synthetic readings never judge each other."
        )
    earliest = min(parse_utc(pull.completed_at) for pull in pulls)
    if parse_utc(history.taken_at) >= earliest:
        raise ValueError(
            f"history must be taken before the pulls it judges. It was taken at {history.taken_at}, "
            f"the first pull completed at {utc_iso(earliest)}"
        )
    return history


@dataclass(frozen=True)
class ClusterVerdict:
    """What version 2 decided for one reading. For reports and tests. REAL inputs, a rule's output."""

    clusters: tuple[tuple[str, ...], ...]  # pull ids per cluster, in completed-time order
    kept_cluster: int | None  # index into `clusters`, None when nothing was kept by a cluster choice
    decided_by: str | None  # the signal that decided, None with one cluster
    tie: bool
    notes: tuple[str, ...] = ()


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _flag_share(flags: Sequence[bool]) -> float:
    return sum(1 for flag in flags if flag) / len(flags) if flags else 0.0


def judge_clusters(
    usable: Sequence[Pull],
    *,
    history: CleanReading | None = None,
    suspects: "RoundSuspects | Iterable[RoundSuspects] | None" = None,
) -> tuple[ClusterVerdict, dict[str, str]]:
    """Version 2 on the usable pulls of one reading. Returns the verdict and {pull_id: drop reason}.

    `suspects` is one RoundSuspects or one per round; each pull is judged against its own round's.
    """
    suspects = _round_list(suspects)
    clusters = cluster_pulls(usable)
    ids = tuple(tuple(pull.pull_id for pull in group) for group in clusters)
    dropped: dict[str, str] = {}
    if not clusters:
        return ClusterVerdict(ids, None, None, False), dropped

    concentrated = [[concentration_flag(pull) for pull in group] for group in clusters]
    ubiquitous = [[ubiquity_flag(pull, suspects) for pull in group] for group in clusters]
    shares: list[float | None] = []
    for group in clusters:
        if history is None:
            shares.append(None)
            continue
        values = [value for value in (history_share(pull, history) for pull in group) if value is not None]
        shares.append(_mean(values) if values else 0.0)

    if len(clusters) == 1:
        group = clusters[0]
        flagged = (_flag_share(concentrated[0]) >= CLUSTER_FLAG_MIN_SHARE
                   or _flag_share(ubiquitous[0]) >= CLUSTER_FLAG_MIN_SHARE)
        share = shares[0]
        if share is not None and share < HISTORY_MIN_SHARE and flagged and not any(pull.repaired for pull in group):
            reason = (
                f"{DROP_ALTERED_VS_HISTORY}: the only cluster shares {share:.2f} of its top slots with the last clean "
                f"reading, below {HISTORY_MIN_SHARE:g}, and looks altered"
            )
            for pull in group:
                dropped[pull.pull_id] = reason
            return ClusterVerdict(ids, None, SIGNAL_HISTORY, False, (reason,)), dropped
        return ClusterVerdict(ids, 0, None, False), dropped

    candidates = list(range(len(clusters)))
    notes: list[str] = []
    decided: str | None = None

    def narrow(name: str, passing: list[int], detail: str) -> None:
        nonlocal candidates, decided
        if decided is not None or len(candidates) <= 1:
            return
        keep = [index for index in candidates if index in passing]
        if 0 < len(keep) < len(candidates):
            candidates = keep
            notes.append(f"{name}: {detail}")
            if len(candidates) == 1:
                decided = name

    narrow(SIGNAL_REPAIRED, [i for i, group in enumerate(clusters) if any(pull.repaired for pull in group)],
           "only some clusters hold a pull the provider repaired")
    if history is not None and decided is None:
        best = max(shares[i] or 0.0 for i in candidates)
        passing = [i for i in candidates
                   if (shares[i] or 0.0) >= HISTORY_MIN_SHARE and (shares[i] or 0.0) >= best - HISTORY_MARGIN]
        detail = ", ".join(f"cluster {i + 1} shares {shares[i] or 0.0:.2f}" for i in candidates)
        narrow(SIGNAL_HISTORY, passing, f"{detail} of its top slots with the last clean reading")
    if decided is None:
        passing = [i for i in candidates if _flag_share(concentrated[i]) < CLUSTER_FLAG_MIN_SHARE]
        detail = "; ".join(
            f"cluster {i + 1}: " + ", ".join(
                f"{site or 'no site'} holds {slots}" for site, slots in (off_brand_concentration(p) for p in clusters[i])
            )
            for i in candidates
        )
        narrow(SIGNAL_CONCENTRATION, passing, f"{detail} of the top {CONCENTRATION_TOP_N} slots")
    if decided is None and suspects is not None:
        passing = [i for i in candidates if _flag_share(ubiquitous[i]) < CLUSTER_FLAG_MIN_SHARE]
        detail = "; ".join(
            f"cluster {i + 1}: " + ", ".join(str(ubiquity_slots(p, suspects)) for p in clusters[i]) + " suspect slots"
            for i in candidates
        )
        narrow(SIGNAL_UBIQUITY, passing, detail)
    if decided is None and len(candidates) > 1:
        largest = max(len(clusters[i]) for i in candidates)
        biggest = [i for i in candidates if len(clusters[i]) == largest]
        if len(biggest) == 1:
            candidates = biggest
            decided = SIGNAL_SIZE
            notes.append(f"{SIGNAL_SIZE}: cluster {biggest[0] + 1} has {largest} pulls, more than any other candidate")

    if len(candidates) != 1:
        sizes = ", ".join(str(len(clusters[i])) for i in candidates)
        reason = (
            f"{DROP_CLUSTER_TIE}: {len(clusters)} clusters and no signal tells clusters {', '.join(str(i + 1) for i in candidates)} "
            f"apart (sizes {sizes})"
        )
        for group in clusters:
            for pull in group:
                dropped[pull.pull_id] = reason
        return ClusterVerdict(ids, None, None, True, tuple(notes) + (reason,)), dropped

    chosen = candidates[0]
    for index, group in enumerate(clusters):
        if index == chosen:
            continue
        reason = (
            f"{DROP_OTHER_CLUSTER}: cluster {index + 1} of {len(clusters)} ({len(group)} pulls) was not kept. "
            f"Cluster {chosen + 1} was chosen by {decided}"
        )
        for pull in group:
            dropped[pull.pull_id] = reason
    return ClusterVerdict(ids, chosen, decided, False, tuple(notes)), dropped


def filter_pulls(
    pulls: Sequence[Pull],
    *,
    overlap_rule: bool = True,
    version: int = DEFAULT_RULE_VERSION,
    history: CleanReading | None = None,
    suspects: "RoundSuspects | Iterable[RoundSuspects] | None" = None,
) -> tuple[list[Pull], list[tuple[str, str]]]:
    """Apply the poisoned-page filter to the pulls of one reading.

    Returns (kept pulls, dropped) where dropped is a list of (pull_id, reason
    text). The reason text starts with one of DROP_REASONS. Order of the input
    is kept. `overlap_rule=False` switches off everything after the structural
    drops (the ablation). `version=1` is the frozen version 1 rule, which
    ignores `history` and `suspects`.
    """
    kept, dropped_list, _ = _filter(pulls, overlap_rule=overlap_rule, version=version, history=history, suspects=suspects)
    return kept, dropped_list


def _filter(
    pulls: Sequence[Pull],
    *,
    overlap_rule: bool,
    version: int,
    history: CleanReading | None,
    suspects: "RoundSuspects | Iterable[RoundSuspects] | None",
) -> tuple[list[Pull], list[tuple[str, str]], ClusterVerdict | None]:
    spec = _check_one_request(pulls)
    _version_name(version)
    usable, dropped = _structural_drops(pulls, spec)
    verdict: ClusterVerdict | None = None
    if overlap_rule and int(version) == 1:
        _low_overlap_v1(usable, dropped)
    elif overlap_rule:
        checked = _check_history(history, spec, pulls)
        verdict, more = judge_clusters(usable, history=checked, suspects=suspects)
        dropped.update(more)
    kept = [pull for pull in pulls if pull.pull_id not in dropped]
    reasons = [(pull.pull_id, dropped[pull.pull_id]) for pull in pulls if pull.pull_id in dropped]
    return kept, reasons, verdict


# ---------------------------------------------------------------------------
# The clean reading


def _url_list(pull: Pull) -> tuple[str, ...]:
    return tuple(hit.url for hit in sorted(pull.hits, key=lambda hit: (hit.rank, hit.url)))


def pulls_look_dependent(first: Pull, second: Pull) -> bool:
    """True when two pulls look like one fetch served twice.

    Same raw sha256, or the same ordered URL list with the same provider result
    time. A missing provider time is not a match: Serper gives none, and two
    honest pulls can return the same list.
    """
    if first.raw_sha256 is not None and first.raw_sha256 == second.raw_sha256:
        return True
    if first.provider_datetime is None or second.provider_datetime is None:
        return False
    return first.provider_datetime == second.provider_datetime and _url_list(first) == _url_list(second)


def _order_key(pull: Pull) -> tuple[str, str]:
    return (utc_iso(parse_utc(pull.completed_at)), pull.pull_id)


def build_clean_reading(
    pulls: Sequence[Pull],
    *,
    reading_id: str | None = None,
    overlap_rule: bool = True,
    version: int = DEFAULT_RULE_VERSION,
    history: CleanReading | None = None,
    suspects: "RoundSuspects | Iterable[RoundSuspects] | None" = None,
    min_depth: int = TOP_DEPTH,
) -> CleanReading:
    """The clean reading of one request from its pulls. A pure function of its inputs.

    Raises ValueError for no pulls, pulls of different requests, an unknown
    version, or a `history` reading that is of another request, degraded, or
    not earlier than the pulls. The result lists only sites whose clean
    position is 1 to 50, best first.

    A reading whose request asked for fewer than `min_depth` results (TOP_DEPTH,
    50, by default) is degraded (`shallow_depth`): a site it did not see may be
    at rank 11, so it cannot say NT50 (spec s2 and s3: readings are at depth 50).
    A report that knowingly reads shallower pulls (the top-10 dry run on the
    committed Serper pulls) passes its own `min_depth`.
    """
    return clean_reading_with_verdict(
        pulls, reading_id=reading_id, overlap_rule=overlap_rule, version=version, history=history, suspects=suspects,
        min_depth=min_depth,
    )[0]


def clean_reading_with_verdict(
    pulls: Sequence[Pull],
    *,
    reading_id: str | None = None,
    overlap_rule: bool = True,
    version: int = DEFAULT_RULE_VERSION,
    history: CleanReading | None = None,
    suspects: "RoundSuspects | Iterable[RoundSuspects] | None" = None,
    min_depth: int = TOP_DEPTH,
) -> tuple[CleanReading, ClusterVerdict | None]:
    """`build_clean_reading` plus the version 2 cluster verdict (None for version 1 or with the rule off)."""
    spec = _check_one_request(pulls)
    if int(min_depth) < 1:
        raise ValueError(f"min_depth {min_depth} is below 1")
    kept, dropped, verdict = _filter(pulls, overlap_rule=overlap_rule, version=version, history=history, suspects=suspects)
    kept = sorted(kept, key=_order_key)
    order = {pull.pull_id: _order_key(pull) for pull in pulls}
    dropped = sorted(dropped, key=lambda entry: order[entry[0]])  # the reading does not depend on input order
    per_pull = [pull_site_positions(pull) for pull in kept]
    sites = sorted(set().union(*per_pull)) if per_pull else []

    positions: list[SitePosition] = []
    for site in sites:
        values = tuple(found.get(site, NT50) for found in per_pull)
        middle = median_position(list(values))
        if middle > TOP_DEPTH:
            continue
        best_url = None
        for pull, found in zip(kept, per_pull):
            if found.get(site) == middle:
                best_url = min(
                    (hit for hit in pull.hits if hit.site == site and hit.rank == middle),
                    key=lambda hit: hit.url,
                ).url
                break
        positions.append(SitePosition(site=site, position=middle, pull_positions=values, best_url=best_url))
    positions.sort(key=lambda entry: (entry.position, entry.site))

    dependent = any(
        pulls_look_dependent(kept[i], kept[j]) for i in range(len(kept)) for j in range(i + 1, len(kept))
    )
    # The reading is dated by the latest pull the filter judged, kept or dropped by the cluster rule:
    # in version 2 the dropped clusters decide which cluster is kept (and whether the reading is
    # degraded), so a reading must not be dated before them (no leakage into a forecast made in
    # between). A pull dropped on its own (failed, short partial, spell corrected) decides nothing
    # about the others and does not move the date. With no usable pull, every pull dates it.
    judged = _structural_drops(pulls, spec)[0] or list(pulls)
    stamps = [parse_utc(pull.completed_at) for pull in judged]
    shallow = int(spec.depth) < int(min_depth)
    degraded = len(kept) < MIN_KEPT_PULLS or shallow
    if shallow:
        why = (
            f"{DEGRADED_SHALLOW}: the pulls asked for the top {spec.depth}, fewer than {min_depth}, so a site "
            f"they did not show is unknown, not NT50"
        )
    elif degraded:
        why = _degraded_reason(len(pulls), len(kept), dropped, verdict)
    else:
        why = None
    checked_history = history if (overlap_rule and int(version) != 1) else None
    reading = CleanReading(
        reading_id=reading_id or new_reading_id(
            spec, [pull.pull_id for pull in pulls], version=version, overlap_rule=overlap_rule,
            history_id=checked_history.reading_id if checked_history is not None else None,
            extra=_suspect_extra(pulls, suspects, overlap_rule=overlap_rule, version=version),
        ),
        search=spec.search,
        location_code=int(spec.location_code),
        device=spec.device,
        taken_at=utc_iso(max(stamps)),
        pulls_total=len(pulls),
        pulls_kept=len(kept),
        dropped=tuple(dropped),
        degraded=degraded,
        pulls_not_independent=dependent,
        positions=tuple(positions),
        pull_ids=tuple(pull.pull_id for pull in kept),
        label=reading_label(pulls),
        reader_version=reader_version_name(version, overlap_rule=overlap_rule),
        degraded_reason=why,
    )
    return reading, verdict


def _suspect_extra(
    pulls: Sequence[Pull], suspects: "RoundSuspects | Iterable[RoundSuspects] | None", *, overlap_rule: bool, version: int
) -> dict[str, Any] | None:
    """The round suspect slots the filter saw, per pull, for the reading id. None when there are none.

    Only pulls with suspect slots are listed, so a reading whose suspects touched no pull keeps
    the id it would have without suspects.
    """
    if not overlap_rule or int(version) == 1 or suspects is None:
        return None
    rounds = _round_list(suspects)
    slots = {pull.pull_id: ubiquity_slots(pull, rounds) for pull in pulls if pull.ok}
    seen = {pull_id: count for pull_id, count in sorted(slots.items()) if count}
    return {"suspect_slots": seen} if seen else None


def _degraded_reason(
    total: int, kept: int, dropped: Sequence[tuple[str, str]], verdict: ClusterVerdict | None
) -> str:
    """One line on why a reading with too few kept pulls is degraded. Starts with one of DEGRADED_REASONS."""
    if verdict is not None and verdict.tie:
        return next((note for note in verdict.notes if note.startswith(DROP_CLUSTER_TIE)), DROP_CLUSTER_TIE)
    if verdict is not None and verdict.kept_cluster is None and verdict.decided_by == SIGNAL_HISTORY:
        return next((note for note in verdict.notes if note.startswith(DROP_ALTERED_VS_HISTORY)), DROP_ALTERED_VS_HISTORY)
    counts = Counter(str(reason).split(":")[0] for _pull_id, reason in dropped)
    detail = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    text = f"{DEGRADED_TOO_FEW_KEPT}: {kept} of {total} pulls kept, {MIN_KEPT_PULLS} needed"
    return f"{text}. Dropped: {detail}" if detail else text


def drop_rate(reading: CleanReading) -> float | None:
    """Share of a reading's pulls the filter dropped. None when the reading had no pulls."""
    if reading.pulls_total <= 0:
        return None
    return (reading.pulls_total - reading.pulls_kept) / reading.pulls_total


def reader_settings(*, version: int = DEFAULT_RULE_VERSION, overlap_rule: bool = True) -> dict[str, Any]:
    """The reader version and every filter threshold, as plain JSON, for reports."""
    name = _version_name(version)
    settings: dict[str, Any] = {
        "reader_version": name,
        "filter_on": bool(overlap_rule),
        "min_kept_pulls": MIN_KEPT_PULLS,
        "partial_min_organic_at_depth_50": PARTIAL_MIN_ORGANIC,
    }
    if int(version) == 1:
        settings.update({"rule": "low_overlap leave-one-out", "poison_top_n": POISON_TOP_N,
                         "poison_min_overlap": POISON_MIN_OVERLAP})
    else:
        settings.update({
            "rule": "clusters chosen by provider repair, history, off-brand concentration, round ubiquity, then size",
            "cluster_top_n": CLUSTER_TOP_N,
            "link_min_share": LINK_MIN_SHARE,
            "concentration_top_n": CONCENTRATION_TOP_N,
            "concentration_min_slots": CONCENTRATION_MIN_SLOTS,
            "brand_token_min_letters": BRAND_TOKEN_MIN_LETTERS,
            "suspect_min_slots": SUSPECT_MIN_SLOTS,
            "suspect_min_searches": SUSPECT_MIN_SEARCHES,
            "ubiquity_min_slots": UBIQUITY_MIN_SLOTS,
            "cluster_flag_min_share": CLUSTER_FLAG_MIN_SHARE,
            "history_min_share": HISTORY_MIN_SHARE,
            "history_margin": HISTORY_MARGIN,
            "default_window_hours": DEFAULT_WINDOW_HOURS,
        })
    return settings


# ---------------------------------------------------------------------------
# Round mode and reading many searches


@dataclass(frozen=True)
class ReadResult:
    """What one `read_clean` call produced. `failed_rounds` holds (round index, error text)."""

    readings: tuple[CleanReading, ...]
    pulls: tuple[Pull, ...]
    failed_rounds: tuple[tuple[int, str], ...] = ()


class RoundFailed(RuntimeError):
    """A whole fetch round failed: the source raised or broke its contract. Holds the round index and when it started.

    `pulls` holds one Pull per spec when the source had already paid for part of the round before
    it stopped (a later task_post refused by the budget guard or the provider): the paid tasks,
    collected or with their task ids, and failed pulls for what was never sent. Empty otherwise.
    The caller must keep them: they were paid for.
    """

    def __init__(self, round_index: int, message: str, started_at: str, pulls: Sequence[Pull] = ()) -> None:
        super().__init__(message)
        self.round_index = int(round_index)
        self.message = message
        self.started_at = started_at
        self.pulls: tuple[Pull, ...] = tuple(pulls)


def _empty_reading(
    spec: PullSpec,
    reasons: Sequence[tuple[str, str]],
    taken_at: str,
    *,
    version: int = DEFAULT_RULE_VERSION,
    label: str = REAL,
    overlap_rule: bool = True,
    why: str | None = None,
) -> CleanReading:
    text = why or f"{DEGRADED_NO_PULLS}: no pull of this search came back"
    # No pull ids to tell two failures apart, so the time and the reasons go into the id: two failed
    # readings of one search on two days are two readings, never one id with two contents.
    extra = {"taken_at": taken_at, "why": text, "reasons": [list(item) for item in reasons]}
    return CleanReading(
        reading_id=new_reading_id(spec, [], version=version, overlap_rule=overlap_rule, extra=extra),
        search=spec.search,
        location_code=int(spec.location_code),
        device=spec.device,
        taken_at=taken_at,
        pulls_total=0,
        pulls_kept=0,
        dropped=tuple(reasons),
        degraded=True,
        pulls_not_independent=False,
        positions=(),
        pull_ids=(),
        label=label,
        reader_version=reader_version_name(version, overlap_rule=overlap_rule),
        degraded_reason=text,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch_round(
    source: SerpSource,
    specs: Sequence[PullSpec],
    *,
    round_index: int = 0,
    now: Callable[[], datetime] = _utc_now,
) -> list[Pull]:
    """One round: one `source.fetch(specs)`, checked. Returns one pull per spec, in order.

    Raises RoundFailed when the source raises, returns the wrong number of
    pulls, or returns pulls for other requests or out of order. A single failed
    task is a failed Pull inside the round, not a failed round. When the source
    raised after paying for part of the round and put one Pull per spec on the
    error (`error.pulls`), the RoundFailed carries them in `pulls`. This
    function does not check a budget and does not sleep.
    """
    if not specs:
        raise ValueError("no specs to read")
    started = utc_iso(now())
    try:
        got = list(source.fetch(list(specs)))
    except Exception as error:  # a whole round failed: the caller records it and goes on
        salvaged = _salvaged_pulls(error, specs)
        raise RoundFailed(round_index, f"{type(error).__name__}: {error}", started, salvaged) from error
    if len(got) != len(specs):
        raise RoundFailed(round_index, f"source returned {len(got)} pulls for {len(specs)} specs", started)
    wrong = [i for i, (spec, pull) in enumerate(zip(specs, got)) if pull.spec != spec]
    if wrong:
        raise RoundFailed(round_index, f"source returned pulls out of order or for other requests at positions {wrong}", started)
    return got


def _salvaged_pulls(error: BaseException, specs: Sequence[PullSpec]) -> tuple[Pull, ...]:
    """The paid pulls a source attached to its error, when they keep the contract (one per spec, in order)."""
    found = getattr(error, "pulls", None)
    if not found:
        return ()
    pulls = tuple(found)
    if len(pulls) != len(specs) or not all(isinstance(pull, Pull) for pull in pulls):
        return ()
    if any(pull.spec != spec for spec, pull in zip(specs, pulls)):
        return ()
    return pulls


def _place_key(spec: PullSpec) -> tuple[str, int, str]:
    return (spec.search, int(spec.location_code), spec.device)


def _latest_history(
    history: Iterable[CleanReading] | None, spec: PullSpec, before: datetime
) -> CleanReading | None:
    best: CleanReading | None = None
    for reading in history or ():
        if (reading.search, int(reading.location_code), reading.device) != _place_key(spec) or reading.degraded:
            continue
        taken = parse_utc(reading.taken_at)
        if taken >= before:
            continue
        if best is None or (taken, reading.reading_id) > (parse_utc(best.taken_at), best.reading_id):
            best = reading
    return best


def assemble_readings(
    pulls: Iterable[Pull],
    *,
    history: Iterable[CleanReading] | None = None,
    suspects: "RoundSuspects | Iterable[RoundSuspects] | None" = None,
    window_hours: float | None = DEFAULT_WINDOW_HOURS,
    overlap_rule: bool = True,
    version: int = DEFAULT_RULE_VERSION,
    min_depth: int = TOP_DEPTH,
) -> list[CleanReading]:
    """One clean reading per (search, location, device) per window of pulls.

    Pulls are grouped by request and sorted by completed time. A window opens
    at the first pull of a group and holds every pull completed within
    `window_hours` of it. The next pull after that opens the next window. None
    means one window per request. `history` may hold any earlier readings: for
    each window the latest non-degraded reading of the same request taken
    before the window's first pull is used. `suspects` is one RoundSuspects or
    several (one per round); each pull is judged against the suspects of its own
    round (`suspects_for_pull`), never a join of rounds. Two requests that share a
    search, location and device but differ in language or depth raise
    ValueError, because a reading needs one request. The result is sorted by
    search, location, device and time.
    """
    if window_hours is not None and not window_hours > 0:
        raise ValueError("window_hours must be above 0, or None for one window per request")
    rounds = _round_list(suspects)
    past = list(history or ())
    groups: dict[tuple[str, int, str], list[Pull]] = defaultdict(list)
    specs: dict[tuple[str, int, str], PullSpec] = {}
    for pull in pulls:
        key = _place_key(pull.spec)
        if key in specs and specs[key] != pull.spec:
            raise ValueError(
                f"two requests for {key}: {specs[key]} and {pull.spec}. A reading needs one request."
            )
        specs[key] = pull.spec
        groups[key].append(pull)

    readings: list[CleanReading] = []
    span = timedelta(hours=window_hours) if window_hours is not None else None
    for key in sorted(groups):
        ordered = sorted(groups[key], key=_order_key)
        windows: list[list[Pull]] = []
        opened: datetime | None = None
        for pull in ordered:
            moment = parse_utc(pull.completed_at)
            if opened is None or (span is not None and moment - opened > span):
                windows.append([])
                opened = moment
            windows[-1].append(pull)
        for window in windows:
            first = parse_utc(window[0].completed_at)
            earlier = _latest_history(past, specs[key], first) if overlap_rule and int(version) != 1 else None
            readings.append(build_clean_reading(
                window, overlap_rule=overlap_rule, version=version, history=earlier, suspects=rounds,
                min_depth=min_depth,
            ))
    readings.sort(key=lambda reading: (reading.search, reading.location_code, reading.device, reading.taken_at))
    return readings


def read_clean(
    source: SerpSource,
    specs: Sequence[PullSpec],
    *,
    pulls: int = DEFAULT_PULLS,
    gap_seconds: float = MIN_PULL_GAP_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = _utc_now,
    overlap_rule: bool = True,
    version: int = DEFAULT_RULE_VERSION,
    history: Iterable[CleanReading] | None = None,
    min_depth: int = TOP_DEPTH,
) -> ReadResult:
    """Read every spec `pulls` times and build one clean reading per spec.

    One `fetch_round` per round. Round k+1 starts at least `gap_seconds` after
    round k started (the wait goes through `sleep`, the time through `now`). A
    round that fails is recorded in `failed_rounds`, and the readings are built
    from the rounds that worked. A round that failed after the source had paid
    for part of it is recorded in `failed_rounds` too, and the pulls the source
    handed back (`RoundFailed.pulls`: the paid tasks, and failed pulls for what
    was not sent) are kept like the pulls of any round. The suspects of signal
    (c) are read per round, and each pull is judged against its own round's
    suspects. All pulls of a spec make one reading, however
    far apart they are. A spec with no pull at all, or whose pulls cannot form
    a reading (for example one pull id served twice), gets an empty degraded
    reading with the reason, and its pulls stay in `pulls`. This function does
    not check a budget: the caller reserves `cost_of_reading(...)` first.
    """
    if pulls < 1:
        raise ValueError("pulls must be at least 1")
    if gap_seconds < 0:
        raise ValueError("gap_seconds cannot be negative")
    if not specs:
        raise ValueError("no specs to read")
    if len(set(specs)) != len(specs):
        raise ValueError("the same spec appears twice. Each spec is one reading.")
    _version_name(version)

    collected: dict[PullSpec, list[Pull]] = {spec: [] for spec in specs}
    every: list[Pull] = []
    failed: list[tuple[int, str]] = []
    rounds: list[RoundSuspects] = []
    salvaged: set[int] = set()
    round_start: datetime | None = None
    for index in range(pulls):
        if round_start is not None:
            elapsed = (now() - round_start).total_seconds()
            if elapsed < gap_seconds:
                sleep(gap_seconds - elapsed)
        round_start = now()
        try:
            got = fetch_round(source, specs, round_index=index, now=now)
        except RoundFailed as error:
            failed.append((index, error.message))
            if not error.pulls:
                continue
            got = list(error.pulls)  # paid for before the round stopped: kept, never thrown away
            salvaged.add(index)
        rounds.append(round_suspects(got))
        for spec, pull in zip(specs, got):
            collected[spec].append(pull)
            every.append(pull)

    per_round = tuple(rounds) or None
    past = list(history or ())
    # A round that handed pulls back gave every spec a pull (failed ones say why), so it is not a missing result.
    round_reasons = [(f"round-{index}", f"{DROP_NO_RESULT}: {text}") for index, text in failed if index not in salvaged]
    label = REAL if provider_is_real(getattr(source, "name", None)) else SYNTHETIC
    readings: list[CleanReading] = []
    for spec in specs:
        mine = collected[spec]
        if not mine:
            why = f"{DEGRADED_NO_PULLS}: every round failed" + (f" ({failed[0][1]})" if failed else "")
            readings.append(_empty_reading(spec, round_reasons, utc_iso(now()), version=version, label=label,
                                           overlap_rule=overlap_rule, why=why))
            continue
        try:
            _check_one_request(mine)
            (reading,) = assemble_readings(
                mine, history=past, suspects=per_round, window_hours=None, overlap_rule=overlap_rule, version=version,
                min_depth=min_depth,
            )
        except ValueError as error:  # the source broke the contract for this spec; keep the other readings
            reasons = round_reasons + [("reading", f"{DEGRADED_INVALID_PULLS}: {error}")]
            readings.append(_empty_reading(spec, reasons, utc_iso(now()), version=version,
                                           label=reading_label(mine) if label == REAL else label,
                                           overlap_rule=overlap_rule, why=f"{DEGRADED_INVALID_PULLS}: {error}"))
            continue
        if round_reasons:
            reading = replace(reading, dropped=reading.dropped + tuple(round_reasons))
        readings.append(reading)
    return ReadResult(readings=tuple(readings), pulls=tuple(every), failed_rounds=tuple(failed))


# ---------------------------------------------------------------------------
# Cost preview


@dataclass(frozen=True)
class CostPreview:
    """What a reading would cost before anything is sent. USD, from the source's own price table."""

    provider: str
    searches: int
    pulls: int
    depth: int
    usd_per_round: float
    usd_total: float
    notes: tuple[str, ...] = field(default_factory=tuple)


def cost_of_reading(source: SerpSource, searches: int | Sequence[object], pulls: int = DEFAULT_PULLS, depth: int = TOP_DEPTH) -> CostPreview:
    """The cost of reading `searches` searches `pulls` times at `depth`: one fetch per round."""
    count = searches if isinstance(searches, int) else len(searches)
    if count < 0 or pulls < 1 or depth < 1:
        raise ValueError("searches must be 0 or more, pulls and depth at least 1")
    per_round = float(source.estimate_cost(count, depth)) if count else 0.0
    if per_round < 0 or math.isnan(per_round):
        raise ValueError(f"{source.name} gave a cost of {per_round}, which is not a price")
    return CostPreview(
        provider=source.name,
        searches=count,
        pulls=pulls,
        depth=depth,
        usd_per_round=per_round,
        usd_total=per_round * pulls,
        notes=(f"{pulls} rounds of {count} searches at depth {depth}",),
    )


def gap_between(first: CleanReading, second: CleanReading) -> timedelta:
    """Absolute time between two readings' `taken_at`."""
    return abs(parse_utc(second.taken_at) - parse_utc(first.taken_at))
