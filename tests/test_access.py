"""Login limits shared through an object store, session revocation, and the cookie formats. No network, no real keys."""

from __future__ import annotations

import time

import pytest

from aeo.ranksim import access as access_mod
from aeo.ranksim.access import FAIL_PREFIX, MAX_AGE, REVOKED_PREFIX, Access
from aeo.ranksim.objects import DirStore, MemoryStore, StoreError

PASSWORD = "correct horse battery staple"
SECRET = "test-secret"
START = 1_800_000_000.0


class Clock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class BrokenStore:
    """Every call fails, like a Blob store that cannot be reached."""

    def put(self, *args, **kwargs):
        raise StoreError("down")

    def get(self, name):
        raise StoreError("down")

    def head(self, name):
        raise StoreError("down")

    def list(self, prefix):
        raise StoreError("down")

    def delete(self, names):
        raise StoreError("down")


class CountingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.lists: list[str] = []

    def list(self, prefix):
        self.lists.append(prefix)
        return super().list(prefix)


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(access_mod, "FAILURE_DELAY_SECONDS", 0)


def _pair(store=None, clock=None):
    store = store if store is not None else MemoryStore()
    clock = clock or Clock()
    return Access(PASSWORD, SECRET, store=store, clock=clock), Access(PASSWORD, SECRET, store=store, clock=clock), store, clock


# -- rate limits ------------------------------------------------------------------------------


def test_failures_on_one_instance_lock_out_another():
    a, b, store, clock = _pair()
    for _ in range(access_mod.MAX_FAILURES_PER_MINUTE - 1):
        assert not a.check_password("wrong")
        clock.advance(1)
    assert not b.locked_out()
    assert not a.check_password("wrong")
    assert b.locked_out() and a.locked_out()  # b never saw a failure itself: the count came from the store
    assert len(store.list(FAIL_PREFIX)) == access_mod.MAX_FAILURES_PER_MINUTE
    assert 0 < b.retry_after() <= 60
    clock.advance(61)
    assert not b.locked_out()
    assert b.check_password(PASSWORD)


def test_per_client_limit_only_locks_that_client():
    a, b, _store, clock = _pair()
    for _ in range(access_mod.MAX_CLIENT_FAILURES_PER_MINUTE):
        a.check_password("wrong", client="203.0.113.7")
        clock.advance(1)
    assert b.locked_out(client="203.0.113.7")
    assert not b.locked_out(client="198.51.100.2")
    assert not b.locked_out()  # the global limit is higher
    clock.advance(60)
    assert not b.locked_out(client="203.0.113.7")


def test_backoff_grows_for_a_client_that_keeps_failing():
    a, _b, _store, clock = _pair()
    ip = "203.0.113.9"

    def burst(count):
        for _ in range(count):
            assert not a.locked_out(client=ip)
            a.check_password("wrong", client=ip)
            clock.advance(1)

    burst(5)
    clock.advance(61)
    burst(5)  # 10 in the hour: the per-minute rule only
    clock.advance(61)
    burst(1)  # the 11th: back-off starts at 60 s
    assert 55 <= a.retry_after(client=ip) <= 60
    clock.advance(61)
    burst(1)  # the 12th: doubled
    assert 115 <= a.retry_after(client=ip) <= 120
    clock.advance(121)
    burst(1)
    assert 235 <= a.retry_after(client=ip) <= 240
    clock.advance(access_mod.BACKOFF_WINDOW_SECONDS + 1)
    assert not a.locked_out(client=ip)


def test_existing_call_sites_still_work():
    access = Access(PASSWORD, SECRET)  # no store, as studio's create_app builds it
    assert not access.locked_out()
    assert not access.check_password("nope")
    assert access.check_password(PASSWORD)
    disabled = Access(None)
    assert disabled.check_password("anything") and not disabled.locked_out() and disabled.valid(None)


def test_failure_delay_is_kept(monkeypatch):
    monkeypatch.setattr(access_mod, "FAILURE_DELAY_SECONDS", 0.6)
    slept: list[float] = []
    monkeypatch.setattr(access_mod.time, "sleep", slept.append)
    access = Access(PASSWORD, SECRET, store=MemoryStore())
    access.check_password("wrong")
    access.check_password(PASSWORD)
    assert slept == [0.6]


def test_store_failure_falls_back_to_memory_limits():
    access = Access(PASSWORD, SECRET, store=BrokenStore(), clock=Clock())
    assert access.check_password(PASSWORD)
    for _ in range(access_mod.MAX_FAILURES_PER_MINUTE):
        assert not access.locked_out()
        assert not access.check_password("wrong")
    assert access.locked_out()  # the in-memory counter still applies
    value = access.issue()
    assert access.valid(value)
    assert access.revoke_all() is False  # the marker could not be shared...
    assert not access.valid(value)  # ...but this instance applies it


def test_unshared_local_failures_still_count_when_writes_fail():
    class NoWrites(MemoryStore):
        def put(self, *args, **kwargs):
            raise StoreError("read only")

    clock = Clock()
    store = NoWrites()
    access = Access(PASSWORD, SECRET, store=store, clock=clock)
    for _ in range(access_mod.MAX_FAILURES_PER_MINUTE):
        access.check_password("wrong")
    assert store.items == {} and access.locked_out()


def test_old_failures_are_pruned():
    a, _b, store, clock = _pair()
    a.check_password("wrong", client="203.0.113.1")
    old = store.list(FAIL_PREFIX)
    clock.advance(access_mod.BACKOFF_WINDOW_SECONDS + 5)
    a.check_password("wrong", client="203.0.113.1")
    names = store.list(FAIL_PREFIX)
    assert len(names) == 1 and names[0] not in old


@pytest.mark.parametrize("kind", ["memory", "dir"])
def test_no_password_or_raw_ip_is_ever_stored(tmp_path, kind):
    store = MemoryStore() if kind == "memory" else DirStore(tmp_path / "objects")
    a = Access(PASSWORD, SECRET, store=store, clock=Clock())
    guesses = ["correct horse battery stapl", "hunter2-guess", PASSWORD + " ", PASSWORD.upper()]
    for guess in guesses:
        a.check_password(guess, client="203.0.113.250")
    a.check_password(PASSWORD, client="203.0.113.250")
    a.revoke_all()
    names = store.list("")
    assert any(name.startswith(FAIL_PREFIX) for name in names) and any(name.startswith(REVOKED_PREFIX) for name in names)
    blob = b"\n".join(name.encode() + b"\0" + store.get(name) for name in names)
    if kind == "dir":
        blob += b"\n".join(path.read_bytes() + str(path).encode() for path in (tmp_path / "objects").rglob("*") if path.is_file())
    lowered = blob.lower()
    for secret in [PASSWORD, *guesses, "hunter2", "203.0.113.250", SECRET]:
        assert secret.strip().lower().encode() not in lowered


# -- cookies and revocation ------------------------------------------------------------------


def test_issue_and_valid_round_trip():
    access = Access(PASSWORD, SECRET, store=MemoryStore())
    value = access.issue(now=START)
    assert value.startswith("2.") and value.count(".") == 3
    assert access.valid(value, now=START + 1)
    assert access.valid(value, now=START + MAX_AGE - 1)
    assert not access.valid(value, now=START + MAX_AGE + 1)
    _version, issued, expires, signature = value.split(".")
    assert not access.valid(f"2.{int(issued) + 1000}.{expires}.{signature}", now=START + 1)  # a later issue time
    assert not access.valid(f"2.{issued}.{int(expires) + 1}.{signature}", now=START + 1)
    assert not access.valid(f"3.{issued}.{expires}.{signature}", now=START + 1)
    for junk in (None, "", "garbage", "2.x.y.z", "2.1.2", "1.2.3.4.5", f"{expires}.{signature}"):
        assert not access.valid(junk, now=START + 1)


def test_password_or_secret_change_signs_everyone_out():
    value = Access(PASSWORD, SECRET).issue(now=START)
    assert Access(PASSWORD, SECRET).valid(value, now=START + 1)
    assert not Access("a new password", SECRET).valid(value, now=START + 1)
    assert not Access(PASSWORD, "a new secret").valid(value, now=START + 1)
    old = Access(PASSWORD, SECRET)
    v1 = f"{int(START) + MAX_AGE}.{old._sign(str(int(START) + MAX_AGE))}"
    assert old.valid(v1, now=START + 1) and not Access("a new password", SECRET).valid(v1, now=START + 1)


def test_revoke_all_reaches_another_instance_after_the_cache_interval():
    a, b, store, clock = _pair()
    before = a.issue()
    assert b.valid(before)  # b lists the markers now and caches the answer
    clock.advance(1)
    assert a.revoke_all() is True
    assert not a.valid(before)  # at once on the instance that revoked
    clock.advance(1)
    assert b.valid(before)  # still cached on b
    clock.advance(access_mod.REVOKE_CACHE_SECONDS)
    assert not b.valid(before)
    clock.advance(0.01)
    after = b.issue()
    assert a.valid(after) and b.valid(after)  # signing in again works everywhere
    assert len(store.list(REVOKED_PREFIX)) == 1


def test_a_cookie_issued_in_the_same_moment_as_a_revocation_is_refused_then_login_works():
    access = Access(PASSWORD, SECRET, store=MemoryStore(), clock=Clock())
    same = access.issue()
    access.revoke_all()
    assert not access.valid(same)
    access._clock.advance(0.002)
    assert access.valid(access.issue())


def test_valid_lists_markers_at_most_once_per_interval():
    store = CountingStore()
    clock = Clock()
    access = Access(PASSWORD, SECRET, store=store, clock=clock)
    value = access.issue()
    for _ in range(200):
        assert access.valid(value)
        clock.advance(0.04)  # 8 seconds in all
    assert store.lists.count(REVOKED_PREFIX) == 1
    clock.advance(access_mod.REVOKE_CACHE_SECONDS)
    access.valid(value)
    assert store.lists.count(REVOKED_PREFIX) == 2
    assert access.valid("garbage") is False and store.lists.count(REVOKED_PREFIX) == 2  # junk never reaches the store


def test_old_format_cookies_survive_the_upgrade_until_a_revocation():
    a, b, _store, clock = _pair()
    expires = int(clock()) + 3600
    v1 = f"{expires}.{a._sign(str(expires))}"
    assert a.valid(v1) and b.valid(v1)  # the upgrade alone signs nobody out
    assert not a.valid(f"{expires + 1}.{a._sign(str(expires))}")
    assert not a.valid(v1, now=expires + 1)
    clock.advance(5)
    a.revoke_all()
    assert not a.valid(v1)
    clock.advance(access_mod.REVOKE_CACHE_SECONDS + 1)
    assert not b.valid(v1)  # any revocation signs v1 cookies out (no issue time to compare)


def test_revoke_without_a_store_applies_to_this_instance():
    clock = Clock()
    access = Access(PASSWORD, SECRET, clock=clock)
    value = access.issue()
    assert access.revoke_all() is True
    assert not access.valid(value)
    clock.advance(1)
    assert access.valid(access.issue())


def test_real_clock_defaults_work():
    access = Access(PASSWORD, SECRET, store=MemoryStore())
    value = access.issue()
    assert access.valid(value)
    time.sleep(0.002)
    access.revoke_all()
    assert not access.valid(value)
