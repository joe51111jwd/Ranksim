"""The search universe for one niche: candidate searches with volume, competition and difficulty.

What is REAL here: every search and every metric is copied from a public file.
Two sources are read:

- DataForSEO's free keyword samples (`google/keywords/<CC>_<lang>.json.gz`,
  1,000 searches per locale): search volume, competition, CPC, keyword
  difficulty (0 to 100) and intent, dated by the file's `time_update`.
- The public Kaggle keyword dataset `caiquedourado/google-keywords-dataset`
  (CC BY 4.0): one parquet shard per download, columns palavra (the search),
  mercado (market), fonte (source), volume, concorrencia (Google Ads
  competition 0 to 1), cpc, adulto. Only the shards named in KAGGLE_SHARDS are
  downloaded. The shards are sorted by market and then alphabetically: the US
  part starts in row group 4 of shard 0010 ("#1 chat" to "internet games",
  read from the shard's footer on 2026-11-22) and ends in shard 0011
  ("internet games for 2 players" to the end, 961,526 rows). One shard alone
  covers only part of the alphabet, which would bias the universe. So the
  build checks which first letters the country's rows cover and refuses a
  universe with a gap unless allow_partial_coverage=True. Its metrics are an
  undated snapshot.

Nothing here is PREDICTED. The difficulty proxy is a rule on the REAL metrics:
keyword difficulty when DataForSEO gives it, else Ads competition. The two
scales mean different things, so thirds are cut inside each proxy source and
never across them.

Leakage note. Both sources describe searches as of 2026 (the Kaggle shard has
no date at all). They are used to pick which searches to study, never as
launch-month features of a backtest forecast.

The niches are the owner's decision. Nothing here picks them: build_universe
takes seed terms. The example in __main__ is labelled as an example.

Rules are code with a hash. Brand and navigational searches are dropped by
`exclusion_reason` and the lists in UniverseRule. The rule hash covers the
lists and the source text of the function, so any change shows.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

import httpx
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ..asof import utc_iso
from ..chain import canonical_json, sha256_text
from ..config import forecast_paths
from . import dfs_samples as dfs

KAGGLE_DATASET = "caiquedourado/google-keywords-dataset"
KAGGLE_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download/{dataset}/{file}"
# Anonymous Kaggle downloads work only with a short user agent (a full browser string gets a captcha page).
KAGGLE_USER_AGENT = "Mozilla/5.0"
KAGGLE_SHARDS: dict[str, tuple[str, ...]] = {"US": ("palavras_chave-0010.parquet", "palavras_chave-0011.parquet")}
# First letters a full-coverage universe must see among the country's rows (a to z).
REQUIRED_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyz")
KAGGLE_MAX_BYTES = 300_000_000
COUNTRY_LOCALE: dict[str, str] = {"US": "US_en", "GB": "GB_en", "CA": "CA_en", "AU": "AU_en", "ES": "ES_es", "AR": "AR_es", "BR": "BR_pt"}

DEFAULT_SEED = 20261122
STRATA = ("easy", "medium", "hard")
SOURCE_DFS = "dfs_keywords"
SOURCE_KAGGLE = "kaggle_google_keywords"
PROXY_KD = "kd"
PROXY_COMPETITION = "competition"


def default_public(source: str) -> Path:
    return forecast_paths().root / "public" / source


def default_out() -> Path:
    return forecast_paths().cache / "universe"


# ----------------------------------------------------------------------------------------------
# The exclusion rule.
# ----------------------------------------------------------------------------------------------

# Very large brands and platforms whose names make a search navigational for a local or niche site.
# OUR LIST. A caller adds niche brands with `brand_terms`.
BRAND_TOKENS: tuple[str, ...] = (
    "amazon", "walmart", "home depot", "homedepot", "lowes", "lowe's", "costco", "ikea", "wayfair",
    "ebay", "etsy", "craigslist", "yelp", "angi", "angie's list", "angies list", "homeadvisor", "thumbtack",
    "google", "youtube", "facebook", "instagram", "tiktok", "reddit", "pinterest", "twitter", "linkedin",
    "microsoft", "samsung", "netflix", "spotify", "uber", "lyft", "doordash", "airbnb", "zillow",
    "redfin", "realtor.com", "taskrabbit", "groupon", "bbb", "best buy", "bestbuy", "menards", "ace hardware",
    "harbor freight", "sears", "kohler", "moen", "delta faucet", "rheem", "ao smith", "bradford white",
    "roto rooter", "roto-rooter", "mr rooter", "benjamin franklin plumbing", "rooter man",
)
NAVIGATIONAL_PHRASES: tuple[str, ...] = (
    "login", "log in", "sign in", "signin", "sign up", "my account", "account", "customer service",
    "phone number", "contact number", "official site", "official website", "website", ".com", "www",
)
DOMAIN_PATTERN = r"(https?://|www\.|\b[a-z0-9-]+\.(com|net|org|co|io|us|info|biz)\b)"


@dataclass(frozen=True)
class UniverseRule:
    """The frozen rule that drops searches. Its hash covers these values and the source of exclusion_reason."""

    version: str = "universe-rule-v1"
    brand_tokens: tuple[str, ...] = BRAND_TOKENS
    navigational_phrases: tuple[str, ...] = NAVIGATIONAL_PHRASES
    domain_pattern: str = DOMAIN_PATTERN
    min_words: int = 1
    max_words: int = 8
    drop_adult: bool = True
    min_volume: int | None = None

    def rule_hash(self, extra_brands: Sequence[str] = ()) -> str:
        payload = {**asdict(self), "extra_brands": sorted(normalise(b) for b in extra_brands),
                   "code": inspect.getsource(exclusion_reason)}
        return sha256_text(canonical_json(payload))


DEFAULT_RULE = UniverseRule()


def normalise(text: str) -> str:
    """Lower case, NFKC, single spaces, no surrounding punctuation. The dedup key of a search."""
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    value = " ".join(value.split())
    return value.strip(" .,;:!?\"'()[]{}")


def _phrase(phrase: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")


def exclusion_reason(keyword: str, *, intent: str | None, adult: bool | None, volume: int | None,
                     rule: UniverseRule, extra_brands: Sequence[str] = ()) -> str | None:
    """Why a search is dropped, or None to keep it. The checks run in this order and the first hit wins.

    adult flag, then a navigational main intent from DataForSEO, then a URL or
    domain in the text, then a navigational phrase, then a brand token (built in
    or given), then the word count limits, then the volume floor (a missing
    volume is kept: missing is not zero).
    """
    text = normalise(keyword)
    if rule.drop_adult and adult:
        return "adult"
    if (intent or "").lower() == "navigational":
        return "navigational_intent"
    if re.search(rule.domain_pattern, text):
        return "domain_or_url"
    for phrase in rule.navigational_phrases:
        if _phrase(phrase).search(text):
            return "navigational_phrase"
    for brand in (*rule.brand_tokens, *extra_brands):
        if _phrase(normalise(brand)).search(text):
            return "brand"
    words = len(text.split())
    if words < rule.min_words or words > rule.max_words:
        return "word_count"
    if rule.min_volume is not None and volume is not None and volume < rule.min_volume:
        return "low_volume"
    return None


# ----------------------------------------------------------------------------------------------
# Download.
# ----------------------------------------------------------------------------------------------


def download_kaggle_shard(file_name: str, *, http: httpx.Client | None = None, root: Path | None = None,
                          dataset: str = KAGGLE_DATASET) -> Path:
    """One file of a public Kaggle dataset, fetched anonymously. Refuses the whole-dataset bundle. Resumable (skips a finished file)."""
    if not file_name.strip() or ".." in file_name:
        raise ValueError("name one file of the dataset. An empty name would fetch the whole dataset bundle.")
    folder = Path(root) if root else default_public("kaggle_google_keywords")
    target = folder / file_name
    if target.is_file():
        return target
    own = http is None
    client = http or httpx.Client(timeout=120.0, headers={"User-Agent": KAGGLE_USER_AGENT})
    try:
        url = KAGGLE_DOWNLOAD.format(dataset=dataset, file=quote(file_name, safe=""))
        first = client.get(url, headers={"User-Agent": KAGGLE_USER_AGENT}, follow_redirects=False)
        location = first.headers.get("location", "")
        if first.status_code not in (301, 302, 303, 307) or not location:
            raise ConnectionError(f"Kaggle did not redirect {file_name} to a download (HTTP {first.status_code})")
        if "bundle/archive.zip" in location:
            raise ConnectionError(f"Kaggle offered the whole dataset bundle for {file_name}. Refused.")
        folder.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        size = 0
        with client.stream("GET", location, headers={"User-Agent": KAGGLE_USER_AGENT}) as response:
            if response.status_code != 200:
                raise ConnectionError(f"the signed download of {file_name} answered HTTP {response.status_code}")
            with part.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    size += len(chunk)
                    if size > KAGGLE_MAX_BYTES:
                        raise ConnectionError(f"{file_name} is larger than {KAGGLE_MAX_BYTES} bytes. Stopped.")
                    digest.update(chunk)
                    handle.write(chunk)
        os.replace(part, target)
        sources = folder / "SOURCES.json"
        payload = json.loads(sources.read_text(encoding="utf-8")) if sources.is_file() else {"files": {}}
        payload.setdefault("files", {})[file_name] = {
            "url": url, "dataset": dataset, "licence": "CC BY 4.0", "fetched_at": utc_iso(datetime.now(timezone.utc)),
            "bytes": size, "sha256": digest.hexdigest(),
        }
        sources.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return target
    finally:
        if own:
            client.close()


# ----------------------------------------------------------------------------------------------
# Building the universe.
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchRow:
    """One candidate search. REAL metrics as published. `difficulty_proxy` and `stratum` follow the stated rule."""

    keyword: str
    keyword_id: str
    locale: str
    country: str
    niche: str
    seed_term: str
    search_volume: int | None
    competition: float | None
    cpc: float | None
    kd: float | None
    intent: str | None
    sources: str
    difficulty_proxy: float | None
    difficulty_proxy_source: str | None
    stratum: str | None


@dataclass
class Universe:
    niche: str
    country: str
    locale: str
    seed_terms: tuple[str, ...]
    n: int
    seed: int
    rows: list[SearchRow]
    candidates: list[SearchRow]
    counts: dict[str, Any]
    rule_hash: str
    sha256: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)

    def frame(self):
        import pandas as pd

        frame = pd.DataFrame.from_records([asdict(row) for row in self.rows], columns=list(SearchRow.__dataclass_fields__))
        frame["search_volume"] = frame["search_volume"].astype("Int64")
        return frame


def seed_pattern(seed_terms: Sequence[str]) -> str:
    """A case-blind RE2 pattern: any seed term as whole words, with an optional plural s or es."""
    terms = sorted({normalise(term) for term in seed_terms if normalise(term)}, key=lambda t: (-len(t), t))
    if not terms:
        raise ValueError("give at least one seed term")
    escaped = [re.escape(term).replace("\\ ", r"\s+") for term in terms]
    return r"(?i)\b(?:" + "|".join(escaped) + r")(?:s|es)?\b"


def _matched_seed(text: str, seed_terms: Sequence[str]) -> str:
    for term in sorted({normalise(t) for t in seed_terms}, key=lambda t: (-len(t), t)):
        if re.search(r"(?<![a-z0-9])" + re.escape(term).replace("\\ ", r"\s+") + r"(?:s|es)?(?![a-z0-9])", text):
            return term
    return ""


def _read_kaggle(paths: Sequence[Path], country: str, pattern: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Matching rows of the country, one row group at a time (memory stays small), and which first letters were seen."""
    rows: list[dict[str, Any]] = []
    stats: dict[str, Any] = {"kaggle_rows_read": 0, "kaggle_rows_in_country": 0, "kaggle_rows_matched": 0}
    letters: set[str] = set()
    columns = ["palavra", "mercado", "volume", "concorrencia", "cpc", "adulto"]
    for path in paths:
        handle = pq.ParquetFile(path)
        for group in range(handle.num_row_groups):
            table = handle.read_row_group(group, columns=columns)
            stats["kaggle_rows_read"] += table.num_rows
            table = table.filter(pc.equal(table["mercado"], country))
            stats["kaggle_rows_in_country"] += table.num_rows
            if table.num_rows:
                firsts = pc.utf8_lower(pc.utf8_slice_codeunits(table["palavra"], 0, 1))
                letters.update(str(x) for x in pc.unique(firsts).to_pylist() if x)
            table = table.filter(pc.fill_null(pc.match_substring_regex(table["palavra"], pattern), False))
            stats["kaggle_rows_matched"] += table.num_rows
            rows.extend(table.to_pylist())
    stats["kaggle_missing_letters"] = "".join(sorted(REQUIRED_LETTERS - letters)) if paths else ""
    return rows, stats


def _read_dfs(paths: Sequence[Path], locale: str, pattern: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    regex = re.compile(pattern.replace("(?i)", ""), re.IGNORECASE)
    stats = {"dfs_rows_read": 0, "dfs_rows_matched": 0}
    for path in paths:
        for row in dfs.iter_keyword_rows(path, SOURCE_DFS, locale=locale):
            stats["dfs_rows_read"] += 1
            if regex.search(row["keyword"] or ""):
                stats["dfs_rows_matched"] += 1
                rows.append(row)
    return rows, stats


def _terciles(rows: list[dict[str, Any]]) -> None:
    """Cut thirds inside each proxy source by rank, ties broken by keyword_id. Deterministic."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["difficulty_proxy"] is not None:
            groups.setdefault(row["difficulty_proxy_source"], []).append(row)
    for members in groups.values():
        members.sort(key=lambda r: (r["difficulty_proxy"], r["keyword_id"]))
        total = len(members)
        for index, row in enumerate(members):
            row["stratum"] = STRATA[min(2, index * 3 // total)]


def build_universe(
    seed_terms: Sequence[str],
    *,
    country: str,
    n: int,
    niche: str,
    kaggle_files: Sequence[str | Path] = (),
    dfs_keyword_files: Sequence[str | Path] = (),
    seed: int = DEFAULT_SEED,
    brand_terms: Sequence[str] = (),
    rule: UniverseRule = DEFAULT_RULE,
    allow_partial_coverage: bool = False,
) -> Universe:
    """Searches of one niche, deduplicated, brand and navigational searches dropped, `n` picked in equal difficulty thirds.

    Deterministic: the same files, seed terms, seed and rule give the same rows
    and the same sha256. Picking inside a third uses sha256(seed | keyword_id)
    order, not a random generator. A third with too few searches is reported as
    a shortfall and is not filled from the others.
    """
    country = country.upper()
    if country not in COUNTRY_LOCALE:
        raise ValueError(f"no locale is set for country {country}. Add it to COUNTRY_LOCALE.")
    if n < 3:
        raise ValueError("n must be at least 3 so each third gets a search")
    if not kaggle_files and not dfs_keyword_files:
        raise ValueError("give at least one source file")
    locale = COUNTRY_LOCALE[country]
    pattern = seed_pattern(seed_terms)
    kaggle_rows, kaggle_stats = _read_kaggle([Path(p) for p in kaggle_files], country, pattern)
    if kaggle_stats["kaggle_missing_letters"] and not allow_partial_coverage:
        raise ValueError(
            f"The keyword shards cover only part of the alphabet for {country}: no searches start with "
            f"{kaggle_stats['kaggle_missing_letters']!r}. Add the missing shard (see KAGGLE_SHARDS) or pass allow_partial_coverage=True."
        )
    dfs_rows, dfs_stats = _read_dfs([Path(p) for p in dfs_keyword_files], locale, pattern)

    merged: dict[str, dict[str, Any]] = {}
    for row in dfs_rows:
        key = normalise(row["keyword"])
        if not key:
            continue
        current = merged.get(key)
        candidate = {
            "keyword": key, "search_volume": row["search_volume"], "competition": row["competition"], "cpc": row["cpc"],
            "kd": row["kd"], "intent": row["intent"], "adult": None, "sources": {SOURCE_DFS},
        }
        if current is None:
            merged[key] = candidate
        else:
            for name in ("search_volume", "competition", "cpc", "kd", "intent"):
                if current[name] is None:
                    current[name] = candidate[name]
    for row in kaggle_rows:
        key = normalise(row["palavra"])
        if not key:
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = {
                "keyword": key, "search_volume": row["volume"], "competition": row["concorrencia"], "cpc": row["cpc"],
                "kd": None, "intent": None, "adult": row["adulto"], "sources": {SOURCE_KAGGLE},
            }
        else:
            current["sources"].add(SOURCE_KAGGLE)
            current["adult"] = bool(current["adult"]) or bool(row["adulto"])
            for name, value in (("search_volume", row["volume"]), ("competition", row["concorrencia"]), ("cpc", row["cpc"])):
                if current[name] is None:
                    current[name] = value

    dropped: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for key in sorted(merged):
        item = merged[key]
        reason = exclusion_reason(key, intent=item["intent"], adult=item["adult"], volume=item["search_volume"],
                                  rule=rule, extra_brands=brand_terms)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        if item["kd"] is not None:
            proxy, source = float(item["kd"]), PROXY_KD
        elif item["competition"] is not None:
            proxy, source = float(item["competition"]), PROXY_COMPETITION
        else:
            proxy, source = None, None
        kept.append({
            **item, "keyword_id": dfs.keyword_id(locale, key), "difficulty_proxy": proxy,
            "difficulty_proxy_source": source, "stratum": None, "seed_term": _matched_seed(key, seed_terms),
        })
    _terciles(kept)

    def as_row(item: Mapping[str, Any]) -> SearchRow:
        return SearchRow(
            keyword=item["keyword"], keyword_id=item["keyword_id"], locale=locale, country=country, niche=niche,
            seed_term=item["seed_term"], search_volume=item["search_volume"], competition=item["competition"],
            cpc=item["cpc"], kd=item["kd"], intent=item["intent"], sources="+".join(sorted(item["sources"])),
            difficulty_proxy=item["difficulty_proxy"], difficulty_proxy_source=item["difficulty_proxy_source"],
            stratum=item["stratum"],
        )

    candidates = [as_row(item) for item in kept]
    quotas = {stratum: n // 3 + (1 if index < n % 3 else 0) for index, stratum in enumerate(STRATA)}
    chosen: list[SearchRow] = []
    available: dict[str, int] = {}
    shortfall: dict[str, int] = {}
    for stratum in STRATA:
        pool = [row for row in candidates if row.stratum == stratum]
        available[stratum] = len(pool)
        pool.sort(key=lambda row: hashlib.sha256(f"{seed}|{row.keyword_id}".encode()).hexdigest())
        take = pool[: quotas[stratum]]
        if len(take) < quotas[stratum]:
            shortfall[stratum] = quotas[stratum] - len(take)
        chosen.extend(take)
    chosen.sort(key=lambda row: (STRATA.index(row.stratum or "easy"), row.keyword))
    rule_hash = rule.rule_hash(brand_terms)
    counts = {
        **kaggle_stats, **dfs_stats,
        "merged_unique": len(merged),
        "dropped": dict(sorted(dropped.items())),
        "kept": len(kept),
        "without_difficulty_proxy": sum(1 for row in candidates if row.stratum is None),
        "proxy_sources": {name: sum(1 for row in candidates if row.difficulty_proxy_source == name) for name in (PROXY_KD, PROXY_COMPETITION)},
        "available_by_stratum": available,
        "quota_by_stratum": quotas,
        "selected_by_stratum": {stratum: sum(1 for row in chosen if row.stratum == stratum) for stratum in STRATA},
        "shortfall_by_stratum": shortfall,
        "selected": len(chosen),
    }
    universe = Universe(
        niche=niche, country=country, locale=locale, seed_terms=tuple(seed_terms), n=n, seed=seed, rows=chosen,
        candidates=candidates, counts=counts, rule_hash=rule_hash,
        sources=[{"path": str(p), "kind": SOURCE_KAGGLE} for p in kaggle_files] + [{"path": str(p), "kind": SOURCE_DFS} for p in dfs_keyword_files],
    )
    universe.sha256 = universe_sha256(universe)
    return universe


def universe_sha256(universe: Universe) -> str:
    """sha256 of the chosen rows, the rule hash, the seed terms, the seed and n."""
    payload = {
        "rows": [asdict(row) for row in universe.rows], "rule_hash": universe.rule_hash, "seed_terms": sorted(universe.seed_terms),
        "seed": universe.seed, "n": universe.n, "country": universe.country, "niche": universe.niche,
    }
    return sha256_text(canonical_json(payload))


def write_universe(universe: Universe, out_dir: Path | None = None) -> dict[str, str]:
    """universe_<niche>_<country>.parquet plus a JSON with counts, rule hash and sha256."""
    folder = Path(out_dir) if out_dir else default_out()
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", universe.niche.lower()).strip("-")
    base = folder / f"universe_{slug}_{universe.country}"
    frame = universe.frame()
    tmp = base.with_suffix(".parquet.tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, base.with_suffix(".parquet"))
    meta = {
        "niche": universe.niche, "country": universe.country, "locale": universe.locale, "seed_terms": list(universe.seed_terms),
        "n": universe.n, "seed": universe.seed, "rule_hash": universe.rule_hash, "sha256": universe.sha256,
        "counts": universe.counts, "sources": universe.sources, "written_at": utc_iso(datetime.now(timezone.utc)),
    }
    base.with_suffix(".json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return {"table": str(base.with_suffix(".parquet")), "meta": str(base.with_suffix(".json"))}


def load_universe(path: str | Path) -> list[SearchRow]:
    """Rows of a written universe parquet. Missing values come back as None."""
    table = pq.read_table(path)
    out = []
    for row in table.to_pylist():
        out.append(SearchRow(**{name: row.get(name) for name in SearchRow.__dataclass_fields__}))
    return out


# An example niche to show the function working. It is NOT one of the owner's three niches.
EXAMPLE_NICHE = "plumbing (example only, not an owner choice)"
EXAMPLE_SEEDS = ("plumber", "plumbing", "drain cleaning", "clogged drain", "water heater", "sewer line", "leaky faucet",
                 "toilet repair", "pipe repair", "burst pipe", "garbage disposal", "sump pump")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="universe", description="Build a search universe from seed terms.")
    parser.add_argument("--niche", default=EXAMPLE_NICHE)
    parser.add_argument("--seed-term", action="append", default=None)
    parser.add_argument("--country", default="US")
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args(argv)
    country = args.country.upper()
    kaggle: list[Path] = []
    if not args.no_download:
        for name in KAGGLE_SHARDS.get(country, ()):
            kaggle.append(download_kaggle_shard(name))
    dfs_file = default_public("dataforseo_samples") / "google" / "keywords" / f"{COUNTRY_LOCALE[country]}.json.gz"
    universe = build_universe(args.seed_term or EXAMPLE_SEEDS, country=country, n=args.n, niche=args.niche,
                              kaggle_files=kaggle, dfs_keyword_files=[dfs_file] if dfs_file.is_file() else [])
    paths = write_universe(universe)
    print(json.dumps({"paths": paths, "sha256": universe.sha256, "rule_hash": universe.rule_hash, "counts": universe.counts}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
