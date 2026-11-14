"""Heuristic competitive score, 0 to 100. This is not a prediction of Google.

Missing factors are dropped and the remaining weights are renormalized.
An AI Overview the SERP response does not mention is unknown, not absent.
"""

from __future__ import annotations

import csv
import json
import math
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from aeo.crawl.urls import domain_of
from aeo.ranksim.kwresearch.models import VolumeRecord, now_iso
from aeo.ranksim.site_history import site_key

WEIGHTS = {
    "serp_composition": 0.30,
    "domain_authority": 0.25,
    "exact_match": 0.15,
    "ai_overview": 0.10,
    "cpc": 0.10,
    "ads_competition": 0.10,
}
COMPOSITION_POINTS = {"brand_homepage": 100.0, "review_ugc": 70.0, "niche": 25.0}
BRAND_RANK_MAX = 1000
UNKNOWN_AUTHORITY = 15.0
CPC_CAP_USD = 50.0
AI_KEYS = ("aiOverview", "ai_overview", "aiOverviews")
CONFIG_PATH = Path(__file__).resolve().parent / "serp_composition.json"

EXPLANATIONS = {
    "serp_composition": "How much of the live top 10 is big-brand homepages, review or UGC sites, and other pages.",
    "domain_authority": "Strength of the ranking domains on the Majestic Million and Tranco lists. A domain on neither list counts as low authority and is flagged.",
    "exact_match": "How many top results put the full keyword in the title or the URL slug.",
    "ai_overview": "Whether this SERP response includes an AI Overview, and how many domains it cites.",
    "cpc": "Cost per click from the volume source. The sub-score follows a log curve that reaches 100 at $50.",
    "ads_competition": "Google Ads competition from the volume source.",
}


@dataclass
class Factor:
    name: str
    status: str
    explanation: str
    raw: str
    subscore: float | None
    weight: float
    weight_used: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DifficultyResult:
    keyword: str
    score: int | None
    coverage: float
    kind: str
    label: str
    factors: list[Factor]
    fetched_at: str
    serp_day: str
    serp_cached: bool
    reason: str
    credits: int = 0
    navigational: bool = False
    navigational_domain: str = ""
    navigational_note: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["factors"] = [factor.to_dict() for factor in self.factors]
        data["kind"] = "heuristic"
        return data


def load_composition(path: str | Path | None = None) -> dict[str, set[str]]:
    file_path = Path(path) if path else CONFIG_PATH
    data = json.loads(file_path.read_text(encoding="utf-8"))
    return {
        "review_ugc": {item.strip().lower() for item in data.get("review_ugc") or [] if str(item).strip()},
        "brand_homepages": {item.strip().lower() for item in data.get("brand_homepages") or [] if str(item).strip()},
    }


def load_tranco_ranks(path: str | Path) -> dict[str, int]:
    file_path = Path(path)
    if file_path.suffix == ".zip":
        with zipfile.ZipFile(file_path) as archive:
            name = archive.namelist()[0]
            lines = archive.read(name).decode("utf-8", "replace").splitlines()
    else:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    found: dict[str, int] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.lower().startswith("rank,"):
            continue
        rank_text, domain = line.split(",", 1)
        try:
            rank = int(rank_text)
        except ValueError:
            continue
        if rank > 0:
            found[domain.strip().lower()] = rank
    return found


def load_majestic_ranks(path: str | Path) -> dict[str, int]:
    found: dict[str, int] = {}
    with Path(path).open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        domain_key = fields.get("domain")
        rank_key = fields.get("globalrank") or fields.get("rank")
        if not domain_key or not rank_key:
            raise ValueError("Majestic Million CSV needs Domain and GlobalRank columns")
        for row in reader:
            domain = (row.get(domain_key) or "").strip().lower()
            raw = (row.get(rank_key) or "").strip()
            if not domain or not raw:
                continue
            try:
                rank = int(float(raw))
            except ValueError:
                continue
            if rank > 0:
                found[domain] = rank
    return found


def score_difficulty(
    keyword: str,
    *,
    organic: list[dict] | None,
    payload: dict | None,
    volume: VolumeRecord | None,
    tranco: dict[str, int] | None,
    majestic: dict[str, int] | None,
    composition: dict[str, set[str]] | None = None,
    serp_day: str = "",
    serp_cached: bool = False,
    credits: int = 0,
    tranco_date: str = "",
    majestic_date: str = "",
    tranco_note: str = "",
    majestic_note: str = "",
) -> DifficultyResult:
    lists = composition if composition is not None else load_composition()
    nav_domain = navigational_domain(keyword, organic)
    factors = [
        _composition(organic, tranco, majestic, lists, nav_domain),
        _authority(organic, tranco, majestic, tranco_date, majestic_date, tranco_note, majestic_note),
        _exact_match(keyword, organic),
        _ai_overview(payload),
        _cpc(volume),
        _ads(volume),
    ]
    present = [factor for factor in factors if factor.status == "present" and factor.subscore is not None]
    weight_sum = sum(factor.weight for factor in present)
    total = sum(WEIGHTS.values())
    for factor in factors:
        factor.weight_used = (factor.weight / weight_sum) if factor in present and weight_sum else 0.0
    if not present or weight_sum <= 0:
        score = None
        reason = "no scored factors"
    else:
        raw = sum((factor.subscore or 0.0) * factor.weight for factor in present) / weight_sum
        score = max(0, min(100, int(math.floor(raw + 0.5))))
        reason = ""
    coverage = weight_sum / total if total else 0.0
    return DifficultyResult(
        keyword=keyword,
        score=score,
        coverage=round(coverage, 4),
        kind="heuristic",
        label="Heuristic score, not a Google prediction.",
        factors=factors,
        fetched_at=now_iso(),
        serp_day=serp_day,
        serp_cached=serp_cached,
        reason=reason,
        credits=credits,
        navigational=bool(nav_domain),
        navigational_domain=nav_domain,
        navigational_note=_navigational_note(keyword, nav_domain),
    )


def _factor(name: str, status: str, raw: str, subscore: float | None) -> Factor:
    return Factor(name, status, EXPLANATIONS[name], raw, subscore, WEIGHTS[name], 0.0)


def navigational_domain(keyword: str, organic: list[dict] | None) -> str:
    """Registrable domain at position 1 whose brand name is the keyword."""
    top = _top_result(organic)
    if top is None:
        return ""
    site = _site(str(top.get("url") or ""))
    brand = site.split(".", 1)[0] if site else ""
    token = re.sub(r"[^a-z0-9]", "", keyword.lower())
    if len(token) < 2 or token != brand:
        return ""
    return site


def _navigational_note(keyword: str, domain: str) -> str:
    if not domain:
        return ""
    return (
        f"Navigational. This score mostly reflects the brand owning its own SERP. "
        f"{domain} ranks #1 for {keyword}."
    )


def _top_result(organic: list[dict] | None) -> dict | None:
    if not organic:
        return None
    if any("position" in hit for hit in organic):
        ordered = sorted(organic, key=lambda hit: int(hit.get("position") or 10**9))
        top = ordered[0]
        if int(top.get("position") or 0) != 1:
            return None
        return top
    return organic[0]


def _composition(
    organic: list[dict] | None,
    tranco: dict[str, int] | None,
    majestic: dict[str, int] | None,
    lists: dict[str, set[str]],
    navigational_site: str = "",
) -> Factor:
    if organic is None:
        return _factor("serp_composition", "missing", "no live SERP", None)
    counts = {"brand_homepage": 0, "review_ugc": 0, "niche": 0}
    for hit in organic:
        counts[classify_result(str(hit.get("url") or ""), tranco, majestic, lists, navigational_site)] += 1
    total = sum(counts.values())
    if total == 0:
        return _factor("serp_composition", "missing", "SERP had no organic results", None)
    sub = (
        counts["brand_homepage"] * COMPOSITION_POINTS["brand_homepage"]
        + counts["review_ugc"] * COMPOSITION_POINTS["review_ugc"]
        + counts["niche"] * COMPOSITION_POINTS["niche"]
    ) / total
    raw = (
        f"brand_homepages={counts['brand_homepage']}, review_ugc={counts['review_ugc']}, "
        f"niche={counts['niche']} of {total}"
    )
    if navigational_site:
        raw += f". Navigational homepage: {navigational_site}"
    return _factor("serp_composition", "present", raw, sub)


def classify_result(
    url: str,
    tranco: dict[str, int] | None,
    majestic: dict[str, int] | None,
    lists: dict[str, set[str]],
    navigational_site: str = "",
) -> str:
    site = _site(url)
    if site and site in lists.get("review_ugc", set()):
        return "review_ugc"
    if navigational_site and site == navigational_site and _is_apex_homepage(url, site):
        return "brand_homepage"
    if _is_homepage(url) and site and _is_big_brand(site, tranco, majestic, lists):
        return "brand_homepage"
    return "niche"


def _is_big_brand(
    site: str,
    tranco: dict[str, int] | None,
    majestic: dict[str, int] | None,
    lists: dict[str, set[str]],
) -> bool:
    if site in lists.get("brand_homepages", set()):
        return True
    ranks = []
    if tranco is not None and site in tranco:
        ranks.append(tranco[site])
    if majestic is not None and site in majestic:
        ranks.append(majestic[site])
    return bool(ranks) and min(ranks) <= BRAND_RANK_MAX


def _authority(
    organic: list[dict] | None,
    tranco: dict[str, int] | None,
    majestic: dict[str, int] | None,
    tranco_date: str = "",
    majestic_date: str = "",
    tranco_note: str = "",
    majestic_note: str = "",
) -> Factor:
    if organic is None:
        return _factor("domain_authority", "missing", "no live SERP", None)
    if tranco is None and majestic is None:
        return _factor("domain_authority", "missing", "Tranco and Majestic lists are not loaded", None)
    if not organic:
        return _factor("domain_authority", "missing", "SERP had no organic results", None)
    scores: list[float] = []
    unknown: list[str] = []
    parts: list[str] = []
    for hit in organic:
        site = _site(str(hit.get("url") or "")) or "(no domain)"
        ranks: list[str] = []
        numbers: list[int] = []
        if tranco is not None and site in tranco:
            numbers.append(tranco[site])
            ranks.append(f"Tranco {tranco[site]}")
        if majestic is not None and site in majestic:
            numbers.append(majestic[site])
            ranks.append(f"Majestic {majestic[site]}")
        if numbers:
            scores.append(_rank_score(min(numbers)))
            parts.append(f"{site} ({', '.join(ranks)})")
        else:
            scores.append(UNKNOWN_AUTHORITY)
            unknown.append(site)
            parts.append(f"{site} (not listed, low authority)")
    sub = sum(scores) / len(scores)
    raw = "; ".join(parts)
    if unknown:
        raw += ". Flagged unknown: " + ", ".join(dict.fromkeys(unknown))
    header = _list_dates(tranco, majestic, tranco_date, majestic_date, tranco_note, majestic_note)
    if header:
        raw = header + ". " + raw
    return _factor("domain_authority", "present", raw, sub)


def _list_dates(
    tranco: dict[str, int] | None,
    majestic: dict[str, int] | None,
    tranco_date: str,
    majestic_date: str,
    tranco_note: str,
    majestic_note: str,
) -> str:
    parts = []
    if tranco is not None:
        parts.append(_list_label("Tranco", tranco_date, tranco_note))
    if majestic is not None:
        parts.append(_list_label("Majestic", majestic_date, majestic_note))
    return ". ".join(parts)


def _list_label(name: str, list_date: str, note: str) -> str:
    text = f"{name} list date {list_date}" if list_date else f"{name} list date unknown"
    if note:
        text += f" ({note})"
    return text


def _rank_score(rank: int) -> float:
    if rank <= 100:
        return 98.0
    if rank <= 1_000:
        return 88.0
    if rank <= 10_000:
        return 72.0
    if rank <= 100_000:
        return 52.0
    if rank <= 1_000_000:
        return 32.0
    return UNKNOWN_AUTHORITY


def _exact_match(keyword: str, organic: list[dict] | None) -> Factor:
    if organic is None:
        return _factor("exact_match", "missing", "no live SERP", None)
    phrase = _phrase(keyword)
    if not phrase or not organic:
        return _factor("exact_match", "missing", "no keyword or no organic results", None)
    matched = 0
    for hit in organic:
        title = _phrase(str(hit.get("title") or ""))
        slug = _phrase(_url_slug(str(hit.get("url") or "")))
        if f" {phrase} " in f" {title} " or f" {phrase} " in f" {slug} ":
            matched += 1
    sub = 100.0 * matched / len(organic)
    return _factor("exact_match", "present", f"{matched} of {len(organic)}", sub)


def _ai_overview(payload: dict | None) -> Factor:
    if payload is None or not any(key in payload for key in AI_KEYS):
        return _factor("ai_overview", "unknown", "SERP response did not include an AI Overview field", None)
    found = None
    for key in AI_KEYS:
        if key in payload:
            found = payload.get(key)
            break
    if found in (None, "", [], {}, False):
        return _factor("ai_overview", "present", "AI Overview field present and empty", 15.0)
    domains = _cited_domains(found)
    if not domains:
        return _factor("ai_overview", "present", "AI Overview present, no cited domains", 55.0)
    sub = min(100.0, 55.0 + 8.0 * len(domains))
    return _factor("ai_overview", "present", "cited: " + ", ".join(domains), sub)


def _cited_domains(found: object) -> list[str]:
    sources: list = []
    if isinstance(found, dict):
        for key in ("sources", "references", "citations", "links"):
            if isinstance(found.get(key), list):
                sources = found[key]
                break
    elif isinstance(found, list):
        sources = found
    domains: list[str] = []
    for item in sources:
        link = ""
        if isinstance(item, str):
            link = item
        elif isinstance(item, dict):
            link = str(item.get("link") or item.get("url") or item.get("source") or "")
        if link.startswith("http"):
            site = _site(link)
            if site and site not in domains:
                domains.append(site)
    return domains


def _cpc(volume: VolumeRecord | None) -> Factor:
    if volume is None or volume.cpc_usd is None:
        return _factor("cpc", "missing", "volume source did not include CPC", None)
    cpc = volume.cpc_usd
    sub = min(100.0, 100.0 * math.log1p(cpc) / math.log1p(CPC_CAP_USD))
    return _factor("cpc", "present", f"${cpc:.2f} from {volume.provider or volume.volume_source}", sub)


def _ads(volume: VolumeRecord | None) -> Factor:
    if volume is None:
        return _factor("ads_competition", "missing", "no volume record", None)
    if volume.competition_index is not None:
        index = volume.competition_index
        if 0 <= index <= 100:
            label = volume.competition or str(index)
            return _factor("ads_competition", "present", f"{label} (index {index})", float(index))
    text = (volume.competition or "").strip()
    if not text:
        return _factor("ads_competition", "missing", "volume source did not include Ads competition", None)
    lower = text.lower()
    named = {"high": 90.0, "medium": 55.0, "low": 25.0}
    if lower in named:
        return _factor("ads_competition", "present", text, named[lower])
    try:
        number = float(text)
    except ValueError:
        return _factor("ads_competition", "missing", f"unrecognized competition value {text}", None)
    if 0 <= number <= 1:
        return _factor("ads_competition", "present", text, number * 100.0)
    if 1 < number <= 100:
        return _factor("ads_competition", "present", text, number)
    return _factor("ads_competition", "missing", f"competition {text} is outside 0 to 100", None)


def _phrase(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _url_slug(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc} {parts.path}"


def _is_apex_homepage(url: str, site: str) -> bool:
    if not site or not _is_homepage(url):
        return False
    try:
        return domain_of(url) == site
    except ValueError:
        return False


def _is_homepage(url: str) -> bool:
    path = urlsplit(url).path or "/"
    if path in {"", "/"}:
        return True
    return re.fullmatch(r"/[a-z]{2}(?:-[a-z]{2})?/?", path.lower()) is not None


def _site(url: str) -> str:
    if not url:
        return ""
    try:
        return site_key(url if "://" in url else "https://" + url)
    except (ValueError, IndexError):
        return ""
