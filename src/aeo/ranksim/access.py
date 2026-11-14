"""Password access for a hosted Ranksim. One owner password, a signed session cookie, nothing secret stored server side.

A hosted Ranksim spends Google API credits and fetches pages from the web, so it must not be open to anyone with the
link. Set RANKSIM_PASSWORD to turn this on. The cookie is HttpOnly, Secure on https, SameSite=Lax, and signed with a
key derived from RANKSIM_SECRET (or the password itself when no secret is set). Changing the password (or the secret)
changes the key, so every existing cookie stops validating: that signs everyone out.

Serverless hosting runs many short-lived instances, so two things that must be shared live in an optional object store
(see objects.py) instead of process memory:

- Failed logins. Each failure is one new immutable object, ranksim/v2/auth/fail/<stamp>-<random>-<client>.json, where
  <client> is a keyed hash of the client (the IP the caller passes), never the raw IP and never the password. Recent
  failures are counted by listing the prefix (the time and client come from the name, so no object is read). Old ones
  are deleted now and then. If the store fails, this instance's in-memory counter still applies, so login keeps working
  and stays limited.
- "Sign out everywhere". revoke_all() writes ranksim/v2/auth/revoked/<stamp>-<random>.json. A cookie issued before
  the newest marker is refused. valid() caches the newest marker and lists the store at most once per
  REVOKE_CACHE_SECONDS, so a revocation reaches other instances within that interval.

Limits (per minute, all instances together): MAX_FAILURES_PER_MINUTE for everyone, MAX_CLIENT_FAILURES_PER_MINUTE for one
client. On top, a progressive back-off: past BACKOFF_CLIENT_AFTER failures from one client (or BACKOFF_GLOBAL_AFTER from
everyone) within an hour, each further failure locks out for BACKOFF_BASE_SECONDS, doubling up to BACKOFF_MAX_SECONDS.
Calls without a client (studio does not pass one yet) only meet the global rules.

Cookie formats:
- v2 (issued now): "2.<issued ms>.<expires s>.<sig>", sig over "2.<issued ms>.<expires s>".
- v1 (issued before this change): "<expires s>.<sig>". It has no issue time, so it is treated as issued at time 0: it
  keeps validating until it expires while no revocation marker exists, and any revocation signs it out. The upgrade
  alone signs nobody out.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

COOKIE = "rs_session"
MAX_AGE = 30 * 24 * 3600
FAILURE_DELAY_SECONDS = 0.6
MAX_FAILURES_PER_MINUTE = 10
MAX_CLIENT_FAILURES_PER_MINUTE = 5
BACKOFF_WINDOW_SECONDS = 3600
BACKOFF_CLIENT_AFTER = 10
BACKOFF_GLOBAL_AFTER = 50
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
REVOKE_CACHE_SECONDS = 10.0
LOCK_PUBLISH_SECONDS = 300.0
PRUNE_EVERY_SECONDS = 60.0

FAIL_PREFIX = "ranksim/v2/auth/fail/"
REVOKED_PREFIX = "ranksim/v2/auth/revoked/"
_NS = 1_000_000_000


def _stamped_name(prefix: str, now: float, tag: str = "") -> str:
    """The new_name layout from objects.py (<20-digit ns>-<random>...), stamped with this Access's clock so stamp_of
    reads back the same time the limits use (tests inject a clock)."""
    tail = f"-{tag}" if tag else ""
    return f"{prefix}{int(now * _NS):020d}-{secrets.token_hex(3)}{tail}.json"


def _client_of(name: str) -> str:
    stem = name.rsplit("/", 1)[-1].removesuffix(".json")
    parts = stem.split("-")
    return parts[2] if len(parts) >= 3 and parts[2] != "0" else ""


class Access:
    def __init__(self, password: str | None, secret: str | None = None, *, store=None, clock=None, shared=None) -> None:
        self.password = (password or "").strip()
        self.enabled = bool(self.password)
        seed = f"ranksim-session|{secret or ''}|{self.password}"
        self._key = hashlib.sha256(seed.encode("utf-8")).digest()
        # A separate key for client hashes, so a stored hash cannot be matched against guessed IPs without the secret.
        self._client_key = hashlib.sha256(f"ranksim-client|{secret or ''}|{self.password}".encode("utf-8")).digest()
        # store: an object store for failure records and revocation markers (local runs, tests). shared: the cloud
        # state (cloud.CloudState) on a hosted app, where Blob operations are budgeted: failures are then counted in
        # memory, and only a global lockout and a revocation go to storage, inside the manifest every instance reads
        # anyway. So a flood of wrong passwords costs at most one storage write per LOCK_PUBLISH_SECONDS.
        self.store = store if shared is None else None
        self.shared = shared
        self._published_lock = 0.0
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._failures: list[tuple[float, str, str | None]] = []  # (time, client hash, stored name or None)
        self._revoked_ms = 0  # newest revocation marker known to this instance, in ms
        self._revoked_checked: float | None = None
        self._last_prune: float | None = None

    # -- passwords and rate limits ----------------------------------------------------------------

    def check_password(self, given: str, *, client: str | None = None) -> bool:
        if not self.enabled:
            return True
        ok = hmac.compare_digest(
            hashlib.sha256((given or "").encode("utf-8")).digest(), hashlib.sha256(self.password.encode("utf-8")).digest()
        )
        if not ok:
            self._record_failure(self._client_hash(client))
            time.sleep(FAILURE_DELAY_SECONDS)
        return ok

    def locked_out(self, *, client: str | None = None) -> bool:
        """True while logins must be refused: too many recent failures overall, or from this client."""
        return self.retry_after(client=client) > 0

    def retry_after(self, *, client: str | None = None) -> int:
        """Whole seconds until a login may be tried again; 0 when it may be tried now. Suits a Retry-After header."""
        if not self.enabled:
            return 0
        now = self._clock()
        left = _lockout_left(self._recent_failures(now), self._client_hash(client), now)
        if self.shared is not None:
            shared_until = float((getattr(self.shared, "auth", {}) or {}).get("lock_until") or 0)
            left = max(left, shared_until - now)
        return int(left) + (1 if left % 1 else 0)

    def _client_hash(self, client: str | None) -> str:
        if not client:
            return ""
        return hmac.new(self._client_key, client.encode("utf-8"), hashlib.sha256).hexdigest()[:24]

    def _record_failure(self, client_hash: str) -> None:
        now = self._clock()
        name = None
        if self.store is not None:
            candidate = _stamped_name(FAIL_PREFIX, now, client_hash or "0")
            try:
                self.store.put(candidate, b"{}", content_type="application/json")
                name = candidate
            except Exception:  # noqa: BLE001 - fail safe: the in-memory record below still counts
                name = None
        with self._lock:
            self._failures = [item for item in self._failures if now - item[0] < BACKOFF_WINDOW_SECONDS]
            self._failures.append((now, client_hash, name))
        if self.shared is not None:
            self._publish_lockout(now)
        if name is not None:
            self._maybe_prune(now)

    def _recent_failures(self, now: float) -> list[tuple[float, str]]:
        """Failures in the back-off window: everything in the shared store, plus this instance's own failures the store
        does not show (a refused write, or a listing that lags). With the store down, this instance's alone."""
        with self._lock:
            local = [item for item in self._failures if now - item[0] < BACKOFF_WINDOW_SECONDS]
        if self.store is None:
            return [(stamp, owner) for stamp, owner, _name in local]
        try:
            names = self.store.list(FAIL_PREFIX)
        except Exception:  # noqa: BLE001 - fail safe: keep the in-memory limit
            return [(stamp, owner) for stamp, owner, _name in local]
        from aeo.ranksim.objects import stamp_of

        listed = set(names)
        found = []
        for name in names:
            stamp = stamp_of(name) / _NS
            if now - stamp < BACKOFF_WINDOW_SECONDS:  # a stamp a little ahead (another instance's clock) counts
                found.append((stamp, _client_of(name)))
        found.extend((stamp, owner) for stamp, owner, name in local if name is None or name not in listed)
        return found

    def _maybe_prune(self, now: float) -> None:
        """Delete failure records older than the back-off window, at most once a minute per instance."""
        if self._last_prune is not None and now - self._last_prune < PRUNE_EVERY_SECONDS:
            return
        self._last_prune = now
        from aeo.ranksim.objects import stamp_of

        try:
            old = [name for name in self.store.list(FAIL_PREFIX) if now - stamp_of(name) / _NS >= BACKOFF_WINDOW_SECONDS]
            if old:
                self.store.delete(old[:500])
        except Exception:  # noqa: BLE001 - pruning is best effort
            return

    def _publish_lockout(self, now: float) -> None:
        """When this instance locks everyone out, tell the other instances through the manifest (rate limited)."""
        left = _lockout_left(self._recent_failures(now), "", now)
        if left <= 0 or now - self._published_lock < LOCK_PUBLISH_SECONDS:
            return
        self._published_lock = now
        try:
            self.shared.update_auth(lock_until=now + left)
        except Exception:  # noqa: BLE001 - fail safe: this instance's own limit still applies
            pass

    # -- session cookies --------------------------------------------------------------------------

    def issue(self, now: float | None = None) -> str:
        current = now if now is not None else self._clock()
        body = f"2.{int(current * 1000)}.{int(current + MAX_AGE)}"
        return f"{body}.{self._sign(body)}"

    def valid(self, value: str | None, now: float | None = None) -> bool:
        if not self.enabled:
            return True
        if not value or "." not in value:
            return False
        current = now if now is not None else self._clock()
        parts = value.split(".")
        if len(parts) == 4 and parts[0] == "2":
            _version, issued, expires, signature = parts
            if not (issued.isdigit() and expires.isdigit()) or int(expires) < current:
                return False
            if not hmac.compare_digest(signature, self._sign(f"2.{issued}.{expires}")):
                return False
            return int(issued) >= self._revoked_since(current)
        if len(parts) == 2:  # v1, issued before revocation existed: treated as issued at time 0
            expires, signature = parts
            if not expires.isdigit() or int(expires) < current:
                return False
            if not hmac.compare_digest(signature, self._sign(expires)):
                return False
            return self._revoked_since(current) == 0
        return False

    def revoke_all(self) -> bool:
        """Sign out every session: cookies issued before now stop validating, here at once and on other instances
        within REVOKE_CACHE_SECONDS. Needs a store to reach other instances. Returns False when the store refused the
        marker (this instance still applies it). Changing RANKSIM_PASSWORD or RANKSIM_SECRET also signs everyone out."""
        now = self._clock()
        name = _stamped_name(REVOKED_PREFIX, now)
        with self._lock:
            self._revoked_ms = max(self._revoked_ms, _marker_ms(name))
        if self.shared is not None:
            try:
                self.shared.update_auth(revoked_ms=self._revoked_ms)
            except Exception:  # noqa: BLE001
                return False
            return True
        if self.store is None:
            return True
        try:
            self.store.put(name, b"{}", content_type="application/json")
        except Exception:  # noqa: BLE001
            return False
        return True

    def _revoked_since(self, now: float) -> int:
        """Newest revocation time in ms (0 when none). Lists the store at most once per REVOKE_CACHE_SECONDS; with a
        shared cloud state it reads the manifest that instance already holds (no storage call)."""
        if self.shared is not None:
            shared_ms = int((getattr(self.shared, "auth", {}) or {}).get("revoked_ms") or 0)
            return max(self._revoked_ms, shared_ms)
        if self.store is None:
            return self._revoked_ms
        with self._lock:
            if self._revoked_checked is not None and 0 <= now - self._revoked_checked < REVOKE_CACHE_SECONDS:
                return self._revoked_ms
            self._revoked_checked = now  # set before listing so concurrent requests do not all list
        try:
            names = self.store.list(REVOKED_PREFIX)
        except Exception:  # noqa: BLE001 - keep the last known marker and try again after the interval
            return self._revoked_ms
        newest = max((_marker_ms(name) for name in names), default=0)
        with self._lock:
            self._revoked_ms = max(self._revoked_ms, newest)
            return self._revoked_ms

    def _sign(self, text: str) -> str:
        return hmac.new(self._key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _marker_ms(name: str) -> int:
    """The millisecond after a marker's time: a cookie issued in the same millisecond as the revocation is refused,
    and one issued a millisecond later is accepted."""
    from aeo.ranksim.objects import stamp_of

    stamp = stamp_of(name)
    return stamp // 1_000_000 + 1 if stamp else 0


def _lockout_left(failures: list[tuple[float, str]], client_hash: str, now: float) -> float:
    """Seconds of lockout left under the per-minute limits and the progressive back-off, for everyone and this client."""
    scopes = [(sorted(stamp for stamp, _owner in failures), MAX_FAILURES_PER_MINUTE, BACKOFF_GLOBAL_AFTER)]
    if client_hash:
        scopes.append((sorted(stamp for stamp, owner in failures if owner == client_hash), MAX_CLIENT_FAILURES_PER_MINUTE, BACKOFF_CLIENT_AFTER))
    left = 0.0
    for stamps, per_minute, backoff_after in scopes:
        minute = [stamp for stamp in stamps if now - stamp < 60]
        if len(minute) >= per_minute:
            left = max(left, minute[-per_minute] + 60 - now)
        if len(stamps) > backoff_after:
            wait = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2 ** min(len(stamps) - backoff_after - 1, 16))
            left = max(left, stamps[-1] + wait - now)
    return max(0.0, left)
