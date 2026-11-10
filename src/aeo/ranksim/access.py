"""Password access for a hosted Ranksim. One owner password, a signed session cookie, nothing stored server side.

A hosted Ranksim spends Google API credits and fetches pages from the web, so it must not be open to anyone with the
link. Set RANKSIM_PASSWORD to turn this on. The cookie is HttpOnly, Secure on https, SameSite=Lax, and signed with a
key derived from RANKSIM_SECRET (or the password itself when no secret is set). Changing the password signs everyone out.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time

COOKIE = "rs_session"
MAX_AGE = 30 * 24 * 3600
FAILURE_DELAY_SECONDS = 0.6
MAX_FAILURES_PER_MINUTE = 10


class Access:
    def __init__(self, password: str | None, secret: str | None = None) -> None:
        self.password = (password or "").strip()
        self.enabled = bool(self.password)
        seed = f"ranksim-session|{secret or ''}|{self.password}"
        self._key = hashlib.sha256(seed.encode("utf-8")).digest()
        self._failures: list[float] = []
        self._lock = threading.Lock()

    def check_password(self, given: str) -> bool:
        if not self.enabled:
            return True
        ok = hmac.compare_digest(
            hashlib.sha256((given or "").encode("utf-8")).digest(), hashlib.sha256(self.password.encode("utf-8")).digest()
        )
        if not ok:
            with self._lock:
                now = time.monotonic()
                self._failures = [stamp for stamp in self._failures if now - stamp < 60] + [now]
            time.sleep(FAILURE_DELAY_SECONDS)
        return ok

    def locked_out(self) -> bool:
        with self._lock:
            now = time.monotonic()
            return sum(1 for stamp in self._failures if now - stamp < 60) >= MAX_FAILURES_PER_MINUTE

    def issue(self, now: float | None = None) -> str:
        expires = int((now or time.time()) + MAX_AGE)
        return f"{expires}.{self._sign(str(expires))}"

    def valid(self, value: str | None, now: float | None = None) -> bool:
        if not self.enabled:
            return True
        if not value or "." not in value:
            return False
        expires, _dot, signature = value.partition(".")
        if not expires.isdigit() or int(expires) < (now or time.time()):
            return False
        return hmac.compare_digest(signature, self._sign(expires))

    def _sign(self, text: str) -> str:
        return hmac.new(self._key, text.encode("utf-8"), hashlib.sha256).hexdigest()
