"""One source of query intent for training, checks, what-if and the space view.

Order: the label file's ``intent`` column, then ``smartcrew/keywords.csv``, then one rule
(a query with "alternative" or " vs " is a comparison), then the default "commercial". Every
query records where its intent came from, so a defaulted intent is reported, not hidden.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

DEFAULT_INTENT = "commercial"
KNOWN_INTENTS = frozenset({"commercial", "comparison", "informational", "transactional", "navigational"})


def normalize_query(query: str) -> str:
    return " ".join((query or "").split()).casefold()


def rule_intent(query: str) -> str | None:
    """The one rule, used only when no file names an intent."""
    text = normalize_query(query)
    if "alternative" in text or " vs " in text:
        return "comparison"
    return None


def intent_for(query: str, intents: Mapping[str, str] | None = None) -> str:
    """Intent for one query: an explicit entry, else the rule, else the default."""
    if intents:
        found = intents.get(query) or intents.get(normalize_query(query))
        if found:
            return found
    return rule_intent(query) or DEFAULT_INTENT


@dataclass
class IntentMap:
    intents: dict[str, str] = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)  # label_file, keywords, rule or default

    def get(self, query: str, default: str | None = None) -> str:
        return intent_for(query, self.intents) if default is None else (self.intents.get(normalize_query(query)) or default)

    def unknown(self, queries: Iterable[str] | None = None) -> list[str]:
        wanted = {normalize_query(query) for query in queries} if queries is not None else None
        return sorted(query for query, origin in self.origin.items() if origin == "default" and (wanted is None or query in wanted))

    def note(self, queries: Iterable[str] | None = None) -> str:
        names = sorted({normalize_query(query) for query in queries}) if queries is not None else sorted(self.origin)
        missing = self.unknown(names)
        if not missing:
            return ""
        return (
            f"{len(missing)} of {len(names)} labeled queries have no intent in the label file or keywords.csv and match "
            f"no rule; they are scored as '{DEFAULT_INTENT}' (the default): " + ", ".join(missing[:5]) + ("." if len(missing) <= 5 else ", ...")
        )


def query_intents(labels=None, keywords=None) -> IntentMap:
    """Intents for every labeled query and every keywords.csv query."""
    if keywords is None:
        from aeo.ranksim.keywords import build_keywords

        keywords = build_keywords()
    result = IntentMap()
    for row in keywords:
        query = normalize_query(row.query)
        if row.intent and query not in result.intents:
            result.intents[query] = row.intent
            result.origin[query] = "keywords"
    for label in labels or []:
        query = normalize_query(label.query)
        explicit = getattr(label, "intent", None)
        if explicit and result.origin.get(query) != "label_file":
            result.intents[query] = explicit
            result.origin[query] = "label_file"
        elif query not in result.intents:
            ruled = rule_intent(query)
            result.intents[query] = ruled or DEFAULT_INTENT
            result.origin[query] = "rule" if ruled else "default"
    return result
