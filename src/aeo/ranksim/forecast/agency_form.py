"""The agency form: the business's footprint and its launch plan (data sources 8 and 9).

An agency fills in one short form per new site. It feeds the trust model (part 5)
and the placement model (part 6), and it names the brand so the scoreboard can
flag brand searches.

What is REAL here: the answers, as the agency gave them. Nothing is checked
against the web. What is PREDICTED here: nothing.

Rules:

- An answer that is not known stays None. It never becomes 0. An empty text box ("")
  is read as not known.
- A wrong type or an impossible value is an error that names the field.
- Every answer carries a date. Measured numbers (brand search volume, referring
  domains, reviews) carry their own date. Every other answer is dated `filled_on`.
  `to_features` refuses (asof.LeakError) any answer dated after its `as_of`.
- A measured number may not be dated after the day the form was filled in.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..site_history import site_key
from .asof import AsOfGuard, Dated, as_date
from .config import EXCLUDED_SITES

FORM_VERSION = 1
MAX_RATING = 5.0
MIN_RATING = 1.0
MAX_YEARS_TRADING = 300.0

# Feature names returned by `to_features`, in order. Each is a float or None.
FEATURE_NAMES: tuple[str, ...] = (
    "form_review_count",  # number of reviews on the named platform
    "form_review_rating",  # average rating, 1 to 5
    "form_brand_search_volume",  # monthly searches for the brand name
    "form_referring_domains",  # sites linking to the business
    "form_has_old_site",  # 1 when the business had an older site, 0 when not
    "form_old_site_age_days",  # days from the older site's first-seen date to as_of
    "form_years_trading",  # years the business has traded
    "form_social_profile_count",  # number of social profiles listed
    "form_business_listing",  # 1 when it has a business listing (for example Google Business Profile), 0 when not
    "form_links_planned",  # links planned around launch
    "form_press_planned",  # 1 when press is planned, 0 when not
    "form_monthly_ad_budget_usd",  # planned monthly ad budget in USD
    "form_pages_at_launch",  # pages live on launch day
    "form_days_to_planned_launch",  # planned launch date minus as_of, in days (negative after launch)
)


class FormError(ValueError):
    """An agency form answer has the wrong type or an impossible value. The message names the field."""


@dataclass(frozen=True)
class Footprint:
    """What the business already has before the new site launches."""

    review_count: int | None = None
    review_rating: float | None = None
    review_platform: str | None = None
    reviews_date: date | None = None
    brand_search_volume: int | None = None
    brand_search_volume_source: str | None = None
    brand_search_volume_date: date | None = None
    referring_domains: int | None = None
    referring_domains_source: str | None = None
    referring_domains_date: date | None = None
    has_old_site: bool | None = None
    old_domain: str | None = None
    old_domain_first_seen: date | None = None
    years_trading: float | None = None
    social_profiles: tuple[str, ...] | None = None
    business_listing: bool | None = None


@dataclass(frozen=True)
class LaunchPlan:
    """What the agency plans to do around launch."""

    planned_launch_date: date | None = None
    links_planned_count: int | None = None
    links_planned_kind: str | None = None
    press_planned: bool | None = None
    monthly_ad_budget_usd: float | None = None
    pages_at_launch: int | None = None


@dataclass(frozen=True)
class AgencyForm:
    """One filled-in form for one new site."""

    site: str
    niche: str | None
    brand_names: tuple[str, ...]
    footprint: Footprint
    launch_plan: LaunchPlan
    filled_by: str
    filled_on: date

    def to_payload(self) -> dict[str, Any]:
        def encode(value: Any) -> Any:
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, tuple):
                return list(value)
            if isinstance(value, dict):
                return {key: encode(item) for key, item in value.items()}
            return value

        payload = asdict(self)
        return {
            "form_version": FORM_VERSION,
            "answers": encode(payload),
        }


# Plain questions for the blank form, one per field.
QUESTIONS: dict[str, str] = {
    "site": "What is the new site's domain? For example example.com.",
    "niche": "What kind of business is it? For example plumber or dentist.",
    "brand_names": "What names do people use for the business? List each one.",
    "filled_by": "Who filled in this form?",
    "filled_on": "On what date was this form filled in? Use YYYY-MM-DD.",
    "footprint.review_count": "How many reviews does the business have? Leave blank if you do not know.",
    "footprint.review_rating": "What is the average review rating, from 1 to 5?",
    "footprint.review_platform": "Where are those reviews? For example Google or Yelp.",
    "footprint.reviews_date": "On what date did you count the reviews? Use YYYY-MM-DD.",
    "footprint.brand_search_volume": "How many people search the business name each month?",
    "footprint.brand_search_volume_source": "Which tool gave that number?",
    "footprint.brand_search_volume_date": "On what date did you read that number? Use YYYY-MM-DD.",
    "footprint.referring_domains": "How many other sites link to the business?",
    "footprint.referring_domains_source": "Which tool gave that number?",
    "footprint.referring_domains_date": "On what date did you read that number? Use YYYY-MM-DD.",
    "footprint.has_old_site": "Did the business have a website before this one? true or false.",
    "footprint.old_domain": "If yes, what was its domain?",
    "footprint.old_domain_first_seen": "When was that old site first online? Use YYYY-MM-DD.",
    "footprint.years_trading": "How many years has the business traded?",
    "footprint.social_profiles": "List the business's social profile links. Use an empty list if it has none.",
    "footprint.business_listing": "Does it have a business listing such as Google Business Profile? true or false.",
    "launch_plan.planned_launch_date": "On what date will the new site go live? Use YYYY-MM-DD.",
    "launch_plan.links_planned_count": "How many links do you plan to build around launch?",
    "launch_plan.links_planned_kind": "What kind of links? For example directories, local press or partners.",
    "launch_plan.press_planned": "Is press planned around launch? true or false.",
    "launch_plan.monthly_ad_budget_usd": "What is the monthly ad budget in US dollars?",
    "launch_plan.pages_at_launch": "How many pages will the site have on launch day?",
}

_TOP_FIELDS = ("site", "niche", "brand_names", "filled_by", "filled_on", "footprint", "launch_plan")


# --------------------------------------------------------------------------- field readers


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(name: str, value: Any) -> str | None:
    if _blank(value):
        return None
    if not isinstance(value, str):
        raise FormError(f"{name} must be text, not {type(value).__name__} {value!r}")
    return value.strip()


def _whole(name: str, value: Any, *, minimum: int = 0) -> int | None:
    if _blank(value):
        return None
    if isinstance(value, bool):
        raise FormError(f"{name} must be a whole number, not true or false")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise FormError(f"{name} must be a whole number, not {type(value).__name__} {value!r}")
    if value < minimum:
        raise FormError(f"{name} must be {minimum} or more, not {value}")
    return value


def _number(name: str, value: Any, *, minimum: float, maximum: float | None = None) -> float | None:
    if _blank(value):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FormError(f"{name} must be a number, not {type(value).__name__} {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise FormError(f"{name} must be a finite number, not {value!r}")
    if number < minimum or (maximum is not None and number > maximum):
        upper = f" to {maximum:g}" if maximum is not None else " or more"
        raise FormError(f"{name} must be {minimum:g}{upper}, not {value!r}")
    return number


def _flag(name: str, value: Any) -> bool | None:
    if _blank(value):
        return None
    if not isinstance(value, bool):
        raise FormError(f"{name} must be true or false, not {value!r}")
    return value


def _day(name: str, value: Any) -> date | None:
    if _blank(value):
        return None
    if isinstance(value, datetime):
        raise FormError(f"{name} must be a calendar date, not a date and time ({value!r})")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise FormError(f"{name} must be a date like 2026-11-30, not {value!r}")
    text = value.strip()
    try:
        if len(text) != 10:
            raise ValueError
        return date.fromisoformat(text)
    except ValueError:
        raise FormError(f"{name} must be a date like 2026-11-30, not {value!r}") from None


def _text_list(name: str, value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise FormError(f"{name} must be a list of text, not {type(value).__name__} {value!r}")
    items: list[str] = []
    for index, item in enumerate(value):
        text = _text(f"{name}[{index}]", item)
        if text is not None:
            items.append(text)
    return tuple(items)


def _section(name: str, value: Any, allowed: tuple[str, ...]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise FormError(f"{name} must be a group of answers, not {type(value).__name__}")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise FormError(f"{name} has unknown fields {unknown}")
    return dict(value)


def _not_after(name: str, day: date | None, limit: date, limit_name: str) -> None:
    if day is not None and day > limit:
        raise FormError(f"{name} {day.isoformat()} is after {limit_name} {limit.isoformat()}, which is impossible")


# --------------------------------------------------------------------------- load


def load_form(source: str | Path | Mapping[str, Any]) -> AgencyForm:
    """Read and strictly check a filled-in form from a JSON file or a dict.

    Accepts the template layout ({"form_version", "questions", "answers"}) or the
    answers alone. Unknown fields are errors, so a typo cannot hide an answer.
    """
    if isinstance(source, Mapping):
        payload: Any = dict(source)
    else:
        try:
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise FormError(f"{source} is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise FormError("the form must be a JSON object")
    if "answers" in payload:
        version = payload.get("form_version", FORM_VERSION)
        if version != FORM_VERSION:
            raise FormError(f"form_version {version!r} is not {FORM_VERSION}")
        extra = sorted(set(payload) - {"answers", "questions", "form_version"})
        if extra:
            raise FormError(f"the form has unknown top-level fields {extra}")
        payload = payload["answers"]
        if not isinstance(payload, dict):
            raise FormError("answers must be a JSON object")
    unknown = sorted(set(payload) - set(_TOP_FIELDS))
    if unknown:
        raise FormError(f"the form has unknown fields {unknown}")

    site_text = _text("site", payload.get("site"))
    if site_text is None:
        raise FormError("site is required")
    site = site_key(site_text)
    if not site or "." not in site:
        raise FormError(f"site {site_text!r} is not a domain")
    if site in EXCLUDED_SITES:
        raise FormError(f"site {site} is excluded and its data is never used")
    filled_by = _text("filled_by", payload.get("filled_by"))
    if filled_by is None:
        raise FormError("filled_by is required")
    filled_on = _day("filled_on", payload.get("filled_on"))
    if filled_on is None:
        raise FormError("filled_on is required")
    brand_names = _text_list("brand_names", payload.get("brand_names")) or ()

    foot_names = tuple(item.name for item in fields(Footprint))
    plan_names = tuple(item.name for item in fields(LaunchPlan))
    raw_foot = _section("footprint", payload.get("footprint"), foot_names)
    raw_plan = _section("launch_plan", payload.get("launch_plan"), plan_names)

    def f(key: str) -> Any:
        return raw_foot.get(key)

    footprint = Footprint(
        review_count=_whole("footprint.review_count", f("review_count")),
        review_rating=_number("footprint.review_rating", f("review_rating"), minimum=MIN_RATING, maximum=MAX_RATING),
        review_platform=_text("footprint.review_platform", f("review_platform")),
        reviews_date=_day("footprint.reviews_date", f("reviews_date")),
        brand_search_volume=_whole("footprint.brand_search_volume", f("brand_search_volume")),
        brand_search_volume_source=_text("footprint.brand_search_volume_source", f("brand_search_volume_source")),
        brand_search_volume_date=_day("footprint.brand_search_volume_date", f("brand_search_volume_date")),
        referring_domains=_whole("footprint.referring_domains", f("referring_domains")),
        referring_domains_source=_text("footprint.referring_domains_source", f("referring_domains_source")),
        referring_domains_date=_day("footprint.referring_domains_date", f("referring_domains_date")),
        has_old_site=_flag("footprint.has_old_site", f("has_old_site")),
        old_domain=_text("footprint.old_domain", f("old_domain")),
        old_domain_first_seen=_day("footprint.old_domain_first_seen", f("old_domain_first_seen")),
        years_trading=_number("footprint.years_trading", f("years_trading"), minimum=0.0, maximum=MAX_YEARS_TRADING),
        social_profiles=_text_list("footprint.social_profiles", f("social_profiles")),
        business_listing=_flag("footprint.business_listing", f("business_listing")),
    )

    def p(key: str) -> Any:
        return raw_plan.get(key)

    plan = LaunchPlan(
        planned_launch_date=_day("launch_plan.planned_launch_date", p("planned_launch_date")),
        links_planned_count=_whole("launch_plan.links_planned_count", p("links_planned_count")),
        links_planned_kind=_text("launch_plan.links_planned_kind", p("links_planned_kind")),
        press_planned=_flag("launch_plan.press_planned", p("press_planned")),
        monthly_ad_budget_usd=_number("launch_plan.monthly_ad_budget_usd", p("monthly_ad_budget_usd"), minimum=0.0),
        pages_at_launch=_whole("launch_plan.pages_at_launch", p("pages_at_launch"), minimum=1),
    )
    _check_consistency(footprint, filled_on)
    return AgencyForm(
        site=site,
        niche=_text("niche", payload.get("niche")),
        brand_names=brand_names,
        footprint=footprint,
        launch_plan=plan,
        filled_by=filled_by,
        filled_on=filled_on,
    )


def _check_consistency(foot: Footprint, filled_on: date) -> None:
    if foot.review_count == 0 and foot.review_rating is not None:
        raise FormError("footprint.review_rating is given but footprint.review_count is 0, which is impossible")
    if foot.review_count is not None and foot.review_platform is None:
        raise FormError("footprint.review_platform is needed when footprint.review_count is given")
    for value_name, value, source_name, source, date_name, day in (
        ("brand_search_volume", foot.brand_search_volume, "brand_search_volume_source",
         foot.brand_search_volume_source, "brand_search_volume_date", foot.brand_search_volume_date),
        ("referring_domains", foot.referring_domains, "referring_domains_source",
         foot.referring_domains_source, "referring_domains_date", foot.referring_domains_date),
    ):
        if value is not None and source is None:
            raise FormError(f"footprint.{source_name} is needed when footprint.{value_name} is given")
        if value is not None and day is None:
            raise FormError(f"footprint.{date_name} is needed when footprint.{value_name} is given")
    if foot.has_old_site is False and (foot.old_domain is not None or foot.old_domain_first_seen is not None):
        raise FormError("footprint.old_domain is given but footprint.has_old_site is false")
    if foot.old_domain is not None and "." not in foot.old_domain:
        raise FormError(f"footprint.old_domain {foot.old_domain!r} is not a domain")
    for name, day in (
        ("footprint.reviews_date", foot.reviews_date),
        ("footprint.brand_search_volume_date", foot.brand_search_volume_date),
        ("footprint.referring_domains_date", foot.referring_domains_date),
        ("footprint.old_domain_first_seen", foot.old_domain_first_seen),
    ):
        _not_after(name, day, filled_on, "filled_on")


# --------------------------------------------------------------------------- features


def _bool_float(value: bool | None) -> float | None:
    return None if value is None else (1.0 if value else 0.0)


def _num(value: float | None) -> float | None:
    return None if value is None else float(value)


def to_features(form: AgencyForm, as_of: date | str) -> dict[str, float | None]:
    """The form as model features, as of a date. Names are FEATURE_NAMES. Unknown stays None.

    Every answer passes through an AsOfGuard. A measured number uses its own date.
    Every other answer uses `filled_on`. Any answer dated after `as_of` raises
    asof.LeakError, even when its value is None, because the form itself was not
    knowable then.
    """
    day = as_date(as_of)
    guard = AsOfGuard(as_of=day)
    foot = form.footprint
    plan = form.launch_plan
    filled = form.filled_on

    def take(name: str, value: Any, snapshot: date | None = None) -> Any:
        return guard.take(Dated(value=value, source=f"agency_form.{name}", snapshot_date=snapshot or filled))

    review_count = take("review_count", foot.review_count, foot.reviews_date)
    review_rating = take("review_rating", foot.review_rating, foot.reviews_date)
    brand = take("brand_search_volume", foot.brand_search_volume, foot.brand_search_volume_date)
    refdomains = take("referring_domains", foot.referring_domains, foot.referring_domains_date)
    has_old = take("has_old_site", foot.has_old_site)
    first_seen = take("old_domain_first_seen", foot.old_domain_first_seen, foot.old_domain_first_seen)
    years = take("years_trading", foot.years_trading)
    social = take("social_profiles", foot.social_profiles)
    listing = take("business_listing", foot.business_listing)
    links = take("links_planned_count", plan.links_planned_count)
    press = take("press_planned", plan.press_planned)
    budget = take("monthly_ad_budget_usd", plan.monthly_ad_budget_usd)
    pages = take("pages_at_launch", plan.pages_at_launch)
    planned = take("planned_launch_date", plan.planned_launch_date)

    if has_old is None and (foot.old_domain is not None or first_seen is not None):
        has_old = True
    features: dict[str, float | None] = {
        "form_review_count": _num(review_count),
        "form_review_rating": _num(review_rating),
        "form_brand_search_volume": _num(brand),
        "form_referring_domains": _num(refdomains),
        "form_has_old_site": _bool_float(has_old),
        "form_old_site_age_days": None if first_seen is None else float((day - first_seen).days),
        "form_years_trading": _num(years),
        "form_social_profile_count": None if social is None else float(len(social)),
        "form_business_listing": _bool_float(listing),
        "form_links_planned": _num(links),
        "form_press_planned": _bool_float(press),
        "form_monthly_ad_budget_usd": _num(budget),
        "form_pages_at_launch": _num(pages),
        "form_days_to_planned_launch": None if planned is None else float((planned - day).days),
    }
    return {name: features[name] for name in FEATURE_NAMES}


# --------------------------------------------------------------------------- template


def blank_template() -> dict[str, Any]:
    """The blank form as a dict: every answer None, every field with its question."""
    answers: dict[str, Any] = {
        "site": None,
        "niche": None,
        "brand_names": [],
        "filled_by": None,
        "filled_on": None,
        "footprint": {item.name: None for item in fields(Footprint)},
        "launch_plan": {item.name: None for item in fields(LaunchPlan)},
    }
    return {"form_version": FORM_VERSION, "questions": dict(QUESTIONS), "answers": answers}


def write_template(path: str | Path) -> Path:
    """Write the blank form as JSON a person can fill in. Refuses to overwrite a file."""
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"{target} already exists. The template does not overwrite a form.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(blank_template(), indent=2) + "\n", encoding="utf-8")
    return target


# --------------------------------------------------------------------------- brand searches


def _tokens(text: str) -> list[str]:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in str(text).casefold())
    return cleaned.split()


def brand_terms(form: AgencyForm) -> tuple[str, ...]:
    """The brand terms for the scoreboard's brand-search flag, sorted.

    Each brand name as lower-case words, plus the site's domain name without its
    ending (example.co.uk gives "example"). Terms shorter than 3 letters are dropped
    because they match too many searches.
    """
    terms: set[str] = set()
    for name in form.brand_names:
        words = _tokens(name)
        if words:
            terms.add(" ".join(words))
    label = form.site.split(".")[0]
    words = _tokens(label)
    if words:
        terms.add(" ".join(words))
    return tuple(sorted(term for term in terms if len(term.replace(" ", "")) >= 3))


def is_brand_search(search: str, form: AgencyForm) -> bool:
    """True when the search names the brand.

    A brand term matches when its words appear in a row in the search, or when its
    letters without spaces equal a run of whole words in the search joined together
    ("acme plumbing" matches the term "acmeplumbing" and the reverse). A term never
    matches part of a word, so "art" does not match "start".
    """
    words = _tokens(search)
    if not words:
        return False
    for term in brand_terms(form):
        term_words = term.split()
        size = len(term_words)
        for start in range(len(words) - size + 1):
            if words[start : start + size] == term_words:
                return True
        joined_term = "".join(term_words)
        for start in range(len(words)):
            run = ""
            for end in range(start, len(words)):
                run += words[end]
                if run == joined_term:
                    return True
                if len(run) >= len(joined_term):
                    break
    return False
