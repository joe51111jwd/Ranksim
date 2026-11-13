"""Serper credit estimates for an index build.

A search that asks for up to 10 results is 1 credit. A single request that
asks for 11 to 100 results is 2 credits. If that response is short, each
extra page of 10 is another 1-credit call. A scrape is 2 credits on a
typical page and up to 10 on a hard page. The cap reserves 10 before a
scrape call, then records the credits field Serper returns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SEARCH_CREDIT_UP_TO_10 = 1
SEARCH_CREDIT_DEEP = 2
SCRAPE_TYPICAL = 2
SCRAPE_WORST = 10
DEFAULT_CREDIT_CAP = 300
MAX_SEARCH_NUM = 100


def search_credits(num: int) -> int:
    """Credits for one Serper search request that asks for ``num`` results."""
    if num <= 0:
        return 0
    if num <= 10:
        return SEARCH_CREDIT_UP_TO_10
    return SEARCH_CREDIT_DEEP


def paged_search_credits(num: int) -> int:
    """Credits if each page of 10 is its own 1-credit call."""
    if num <= 0:
        return 0
    return math.ceil(num / 10) * SEARCH_CREDIT_UP_TO_10


@dataclass(frozen=True)
class CreditEstimate:
    queries: int
    num: int
    missing_top10: int
    missing_labeled: int
    missing_urls: int
    credit_cap: int
    offline: bool
    key_set: bool

    @property
    def search_per_query(self) -> int:
        if self.offline or not self.key_set:
            return 0
        return search_credits(self.num)

    @property
    def search_total(self) -> int:
        return self.queries * self.search_per_query

    @property
    def search_if_paged(self) -> int:
        if self.offline or not self.key_set:
            return 0
        return self.queries * paged_search_credits(self.num)

    @property
    def scrape_typical(self) -> int:
        if self.offline or not self.key_set:
            return 0
        return self.missing_top10 * SCRAPE_TYPICAL

    @property
    def scrape_worst(self) -> int:
        if self.offline or not self.key_set:
            return 0
        return self.missing_top10 * SCRAPE_WORST

    @property
    def will_spend_at_most(self) -> int:
        if self.offline:
            return 0
        return min(self.credit_cap, self.search_if_paged + self.scrape_worst)

    def lines(self) -> list[str]:
        if self.offline or not self.key_set:
            if self.offline:
                head = [
                    "Serper credit estimate before spending",
                    "Offline run. Serper calls: 0. Credits that will be spent: 0.",
                    "A live build of this candidate list, with a key, would estimate:",
                ]
            else:
                head = [
                    "Serper credit estimate before spending",
                    "SERPER_API_KEY is not set. Serper search and Serper scrape are skipped.",
                    "HTTP, the browser, Common Crawl, and Wayback still run.",
                    "Credits that will be spent: 0.",
                    "A build with SERPER_API_KEY set would estimate:",
                ]
            live = CreditEstimate(
                queries=self.queries,
                num=self.num,
                missing_top10=self.missing_top10,
                missing_labeled=self.missing_labeled,
                missing_urls=self.missing_urls,
                credit_cap=self.credit_cap,
                offline=False,
                key_set=True,
            )
            return head + ["  " + line for line in live._body()]
        return [
            "Serper credit estimate before spending",
            *self._body(),
            "SERPER_API_KEY is set. The key is not printed.",
        ]

    def _body(self) -> list[str]:
        return [
            f"queries: {self.queries}",
            f"search: {self.queries} requests with num={self.num}, {self.search_per_query} credits each, {self.search_total} credits",
            f"search if each page of 10 is a separate call: {self.search_if_paged} credits",
            f"top-10 URLs with no text yet: {self.missing_top10}",
            f"labeled URLs with no text yet: {self.missing_labeled}",
            f"candidate URLs with no text yet: {self.missing_urls}",
            f"scrape of the missing top-10 URLs at 2 credits: {self.scrape_typical}",
            f"scrape of the missing top-10 URLs at 10 credits: {self.scrape_worst}",
            f"extra result pages keep {self.scrape_typical} credits aside for those top-10 scrapes",
            f"credit cap: {self.credit_cap}",
            f"this build stops at {self.will_spend_at_most} credits",
        ]


class CreditSpend:
    """Hard ceiling. A call that might cost more than the credits left is not sent."""

    def __init__(self, cap: int) -> None:
        self.cap = max(0, int(cap))
        self.search = 0
        self.scrape = 0

    @property
    def spent(self) -> int:
        return self.search + self.scrape

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.spent)

    def can_spend(self, worst: int) -> bool:
        return self.spent + max(0, int(worst)) <= self.cap

    def add_search(self, amount: int) -> None:
        self.search += max(0, int(amount))

    def add_scrape(self, amount: int) -> None:
        self.scrape += max(0, int(amount))
