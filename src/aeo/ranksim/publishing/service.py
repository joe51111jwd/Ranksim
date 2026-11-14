"""The publish flow: ownership, connections, plan, publish, live check, history and rollback.

``Publisher`` holds no global state. The app builds one per request with:

- ``store``: a PublishStore for the project
- ``fetch(url) -> (html, error)``: the robots-respecting, private-address-refusing page fetcher
- ``http_factory() -> httpx.Client``: for platform APIs and DNS-over-HTTPS (tests pass a MockTransport client)
"""

from __future__ import annotations

import html as htmllib
import re
import time
import unicodedata
from typing import Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from . import ownership as own
from .connectors import CONNECTORS, Connector, ConnectorError, Prepared
from .connectors.base import same_page
from .htmlparts import parts, short
from .store import PublishStore, StoreError, public_connection, public_history

MAX_HTML = 2_000_000


class PublishError(RuntimeError):
    """A message safe to show the owner. ``status`` is the HTTP status the route should use."""

    def __init__(self, message: str, status: int = 400, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


def default_http() -> httpx.Client:
    return httpx.Client(follow_redirects=False, timeout=20.0, headers={"User-Agent": "Ranksim-Publish/1.0"})


def cache_busted(url: str, now: float) -> str:
    parts_ = urlsplit(url)
    extra = urlencode({"ranksim_check": int(now * 1000)})
    query = f"{parts_.query}&{extra}" if parts_.query else extra
    return urlunsplit((parts_.scheme, parts_.netloc, parts_.path, query, ""))


class Publisher:
    def __init__(
        self,
        store: PublishStore,
        *,
        fetch: Callable[[str], tuple[str, str]],
        http_factory: Callable[[], httpx.Client] = default_http,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        verify_waits: tuple[float, ...] = (0.0, 3.0, 6.0),
        allow_private: bool = False,
        who: str = "owner",
        checkpoint: Callable[[], None] | None = None,
        proof_fetch: Callable[[str], tuple] | None = None,
    ) -> None:
        self.store = store
        self.fetch = fetch
        # Ownership proofs need the final URL after redirects; the app passes a fetcher that returns it.
        self.proof_fetch = proof_fetch or fetch
        # Saves the data folder to cloud storage mid-request (the plugin passes plugins.save); a no-op locally.
        self.checkpoint = checkpoint or (lambda: None)
        self.http_factory = http_factory
        self.now = now
        self.sleep = sleep
        self.verify_waits = verify_waits
        self.allow_private = allow_private
        self.who = who

    # -- ownership ---------------------------------------------------------

    def start_ownership(self, site: str) -> dict:
        try:
            domain = own.site_key(site)
        except own.OwnershipError as exc:
            raise PublishError(str(exc)) from exc
        with self.store.lock:
            record = self.store.ownership(domain)
            if record is None:
                record = own.new_record(domain, self.now())
                self.store.put_ownership(record)
        return own.public(record)

    def new_token(self, site: str) -> dict:
        try:
            domain = own.site_key(site)
        except own.OwnershipError as exc:
            raise PublishError(str(exc)) from exc
        with self.store.lock:
            record = own.new_record(domain, self.now())
            self.store.put_ownership(record)
        return own.public(record)

    def verify_ownership(self, site: str, methods: list[str] | None = None) -> dict:
        try:
            domain = own.site_key(site)
        except own.OwnershipError as exc:
            raise PublishError(str(exc)) from exc
        record = self.store.ownership(domain)
        if record is None:
            raise PublishError("Start ownership for this site first, so it has a token.", 404)
        with self.http_factory() as client:
            own.run_checks(record, fetch=self.proof_fetch, dns_client=client, methods=methods, now=self.now())
        with self.store.lock:
            latest = self.store.ownership(domain)
            if latest is not None and latest.get("token") == record["token"]:
                self.store.put_ownership(record)
        return own.public(record)

    def ownership_for(self, url: str) -> dict | None:
        """The record that would cover this URL (exact host first, then a parent domain proven by DNS)."""
        try:
            domain = own.site_key(url)
        except own.OwnershipError:
            return None
        records = self.store.load()["ownership"]
        exact = records.get(domain)
        if exact and exact.get("verified"):
            return exact
        labels = domain.split(".")
        for i in range(1, len(labels) - 1):
            parent = records.get(".".join(labels[i:]))
            if parent and parent.get("verified") and parent.get("scope") == "domain":
                return parent
        return exact

    def require_ownership(self, url: str) -> dict:
        record = self.ownership_for(url)
        if record is None:
            raise PublishError("Prove you own this site before publishing to it.", 403, code="not_owned")
        if record.get("verified") and own.is_stale(record, self.now()):
            self.verify_ownership(record["domain"], recheck_order(record))
            record = self.store.ownership(record["domain"]) or record
        if not own.covers(record, url):
            raise PublishError("Ownership of this site is not proven, or the proof is gone. Check it again.", 403, code="not_owned")
        return record

    def recheck_stale(self) -> list[str]:
        """Re-check every verified record that is due. Used after pulls, so proofs are checked periodically."""
        done = []
        for domain, record in list(self.store.load()["ownership"].items()):
            if record.get("verified") and own.is_stale(record, self.now()):
                self.verify_ownership(domain, recheck_order(record))
                done.append(domain)
        return done

    # -- connections -------------------------------------------------------

    def add_connection(self, *, kind: str, site: str, label: str, settings: dict, secret: dict, test: bool = True) -> dict:
        cls = CONNECTORS.get(kind)
        if cls is None:
            raise PublishError("Pick a platform to connect.")
        from .store import encryption_ready

        if not encryption_ready():  # before any network call: credentials would not be kept anyway
            raise PublishError("Credential encryption is not set up on this server yet, so connections cannot be saved.", 503)
        try:
            domain = own.site_key(site)
            clean_settings = cls.clean_settings(settings)
            clean_secret = cls.clean_secret(secret)
        except (own.OwnershipError, ConnectorError) as exc:
            raise PublishError(str(exc)) from exc
        account = ""
        if test:
            with self.http_factory() as client:
                try:
                    account = cls(clean_settings, clean_secret, client, allow_private=self.allow_private).check()
                except ConnectorError as exc:
                    raise PublishError(f"The connection did not work. {exc}") from exc
        try:
            connection = self.store.add_connection(
                kind=kind,
                domain=domain,
                label=(label or account or cls.label)[:120],
                settings=clean_settings,
                secret=clean_secret,
                now=self.now(),
            )
        except StoreError as exc:
            raise PublishError(str(exc), 503) from exc
        return {**public_connection(connection), "account": account}

    def check_connection(self, *, kind: str, settings: dict, secret: dict, url: str = "") -> dict:
        """The guided flow's live check: test what was typed, step by step, without saving anything."""
        cls = CONNECTORS.get(kind)
        if cls is None:
            raise PublishError("Pick a platform to connect.")
        try:
            clean_settings = cls.clean_settings(settings)
            clean_secret = cls.clean_secret(secret)
        except ConnectorError as exc:
            raise PublishError(str(exc)) from exc
        with self.http_factory() as client:
            connector = cls(clean_settings, clean_secret, client, allow_private=self.allow_private)
            steps = guarded(lambda: connector.inspect(url), 502)
        # The page step informs; a connection that works can be saved even when this page is not on the platform.
        return {"ok": all(s["ok"] for s in steps), "can_save": all(s["ok"] for s in steps if s["key"] != "page"), "steps": steps}

    def remove_connection(self, connection_id: str) -> bool:
        return self.store.remove_connection(connection_id)

    def _connection(self, connection_id: str) -> dict:
        connection = self.store.connection(connection_id or "")
        if connection is None:
            raise PublishError("That connection is gone. Connect the site again.", 404)
        return connection

    def _connector(self, connection: dict, client: httpx.Client) -> Connector:
        try:
            secret = self.store.secret(connection)
        except StoreError as exc:
            raise PublishError(str(exc), 503) from exc
        return CONNECTORS[connection["kind"]](connection.get("settings") or {}, secret, client, allow_private=self.allow_private)

    def connections_for(self, url: str) -> list[dict]:
        try:
            domain = own.site_key(url)
        except own.OwnershipError:
            return []
        return [
            public_connection(c)
            for c in self.store.connections()
            if c["domain"] == domain or domain.endswith("." + c["domain"])
        ]

    # -- plan and publish --------------------------------------------------

    def _live_html(self, url: str) -> str:
        html, error = self.fetch(url)
        if error or not html:
            raise PublishError(f"The live page could not be read ({error or 'empty'}), so the change cannot be compared.", 502)
        return html

    def _check_inputs(self, url: str, html: str) -> None:
        if not url or urlsplit(url).scheme not in {"http", "https"}:
            raise PublishError("Open a page address to publish.")
        if not html or len(html.encode("utf-8", errors="ignore")) > MAX_HTML:
            raise PublishError("The edited page is empty or over 2 MB.")

    def status(self, url: str) -> dict:
        """What the Publish sheet needs before any change is planned: ownership and connections."""
        try:
            domain = own.site_key(url)
        except own.OwnershipError as exc:
            raise PublishError(str(exc)) from exc
        record = self.ownership_for(url)
        return {
            "url": url,
            "domain": domain,
            "owned": bool(record and own.covers(record, url)),
            "ownership": own.public(record) if record else None,
            "connections": self.connections_for(url),
        }

    def plan(self, url: str, html: str, connection_id: str | None = None) -> dict:
        self._check_inputs(url, html)
        state = self.status(url)
        before_html = self._live_html(url)
        before, after = parts(before_html), parts(html)
        from .htmlparts import changes as field_changes

        from .htmlparts import display

        state["changes"] = [display(c) for c in field_changes(before, after)]
        state["plan"] = None
        if connection_id:
            connection = self._connection(connection_id)
            with self.http_factory() as client:
                prepared = guarded(lambda: self._connector(connection, client).prepare(url, before_html, html, slug=self.store.scope), 422)
            state["plan"] = prepared.public()
            state["changes"] = [{k: row.get(k) for k in ("field", "label", "before", "after")} for row in state["plan"]["rows"]]
        return state

    def publish(
        self,
        url: str,
        html: str,
        connection_id: str,
        *,
        confirm: list[str] | None = None,
        options: dict | None = None,
        fixes: list | None = None,
    ) -> dict:
        self._check_inputs(url, html)
        self._fixes = clean_fixes(fixes)
        record = self.require_ownership(url)
        connection = self._connection(connection_id)
        if not (own.site_key(url) == connection["domain"] or own.site_key(url).endswith("." + connection["domain"])):
            raise PublishError("That connection is for another site.", 403)
        options = {k: bool(v) for k, v in (options or {}).items() if k in {"publish_site"}}
        before_html = self._live_html(url)
        with self.http_factory() as client:
            connector = self._connector(connection, client)
            prepared: Prepared = guarded(lambda: connector.prepare(url, before_html, html, slug=self.store.scope), 422)
            if not prepared.applied_fields():
                raise PublishError(f"None of these changes can be published through {connector.label}.", 422, plan=prepared.public())
            needed = {w["code"] for w in prepared.warnings if w.get("confirm")}
            missing = needed - set(confirm or [])
            if missing:
                raise PublishError("Confirm the warnings before publishing.", 409, plan=prepared.public(), confirm=sorted(missing))
            started = self.now()
            entry = self._entry_for(prepared, connector, connection, record, url, html, options, started)
            # Saved before the platform call: if it fails half way, the previous values are still on record.
            self.store.put_history(entry)
            self.checkpoint()
            try:
                result = guarded(lambda: connector.apply(prepared, options=options), 502, prefix="Publishing failed. ")
            except PublishError as exc:
                entry.update(status="failed", note=str(exc), verify={"status": "failed", "at": self.now(), "fields": {}, "detail": str(exc)})
                self.store.put_history(entry)
                exc.extra["history_id"] = entry["id"]
                raise
        entry["remote"] = {**prepared.target, **result}
        entry["status"] = result.get("status", "live")
        entry["note"] = result.get("note", "")
        self.store.put_history(entry)
        if entry["status"] == "live":
            entry["verify"] = self._verify(entry, waits=self.verify_waits)
        else:
            entry["verify"] = {"status": "not_live_yet", "at": self.now(), "fields": {}, "detail": entry["note"] or "Not live until it is merged or published."}
        self.store.put_history(entry)
        return public_history(entry)

    def _entry_for(self, prepared, connector, connection, record, url, html, options, started) -> dict:
        return {
            "id": self.store.new_history_id(started),
            "at": started,
            "who": self.who,
            "url": url,
            "domain": connection["domain"],
            "ownership": {"method": record.get("method"), "verified_at": record.get("verified_at")},
            "connection": {"id": connection["id"], "kind": connection["kind"], "label": connection.get("label")},
            "rows": prepared.public()["rows"],
            "fields": prepared.applied_fields(),
            "warnings": prepared.warnings,
            "options": options,
            "target": prepared.public()["target"],
            "remote": dict(prepared.target),
            "status": "applying",
            "note": "",
            "payload": prepared.payload,
            "previous": prepared.previous,
            "expected": {field: parts(html, connector.regions()).value(field) for field in prepared.applied_fields()},
            "regions": connector.regions(),
            "can_rollback": prepared.can_rollback,
            "fixes": getattr(self, "_fixes", []),
            "verify": None,
            "rolled_back_at": None,
        }

    # -- live check --------------------------------------------------------

    def _verify(self, entry: dict, *, waits: tuple[float, ...]) -> dict:
        expected: dict = entry.get("expected") or {}
        regions = entry.get("regions") or []
        outcome = {"status": "unknown", "fields": {}, "attempts": 0, "detail": ""}
        for wait in waits or (0.0,):
            if wait:
                self.sleep(wait)
            outcome["attempts"] += 1
            html, error = self.fetch(cache_busted(entry["url"], self.now()))
            if error or not html:
                outcome.update(status="unknown", detail=f"The live page could not be read ({error or 'empty'}).")
                continue
            live = parts(html, regions)
            fields = {field: comparable(live.value(field)) == comparable(value) for field, value in expected.items()}
            outcome["fields"] = fields
            if fields and all(fields.values()):
                outcome.update(status="confirmed", detail="The live page shows the change.")
                break
            outcome.update(
                status="partial" if any(fields.values()) else "pending",
                detail="The live page does not show every change yet. Caches can take a few minutes. Check again later.",
            )
        outcome["at"] = self.now()
        return outcome

    def verify_live(self, entry_id: str) -> dict:
        """Check again. A merged pull request, or a saved draft the live page now shows, turns the entry live. A
        pending revert pull request that was merged completes its rollback. Only this step's fields are written, onto
        a fresh copy of the entry, so a rollback that ran meanwhile is never undone."""
        entry = self._entry(entry_id)
        kind = entry["connection"]["kind"]
        updates: dict = {}
        if entry.get("rollback_pending") and kind == "github":
            connector_call = self._github_merged(entry, entry["rollback"])
            if connector_call:
                updates.update(rollback_pending=False, rolled_back_at=self.now())
                updates["rollback"] = {**entry["rollback"], "status": "live", "note": "The revert pull request was merged."}
        elif entry.get("status") == "pull_request" and kind == "github":
            if not self._github_merged(entry, entry["remote"]):
                updates["verify"] = {"status": "not_live_yet", "at": self.now(), "fields": {}, "detail": "The pull request is not merged yet."}
                return self._merge_entry(entry_id, updates)
            updates["status"] = "live"
        if "verify" not in updates:
            updates["verify"] = self._verify({**entry, **updates}, waits=(0.0,))
            if entry.get("status") == "saved" and updates["verify"]["status"] == "confirmed":
                updates["status"] = "live"  # a Webflow draft someone published from Webflow
        return self._merge_entry(entry_id, updates)

    def _github_merged(self, entry: dict, remote: dict) -> bool:
        connection = self._rollback_connection(entry, None)
        with self.http_factory() as client:
            connector = self._connector(connection, client)
            return bool(guarded(lambda: connector.merged(remote), 502))

    def _merge_entry(self, entry_id: str, updates: dict) -> dict:
        with self.store.lock:
            fresh = self._entry(entry_id)
            if fresh.get("rolled_back_at") and "rolled_back_at" not in updates:
                updates = {k: v for k, v in updates.items() if k != "status"}
            fresh.update(updates)
            self.store.put_history(fresh)
        return public_history(fresh)

    # -- history and rollback ----------------------------------------------

    def _rollback_connection(self, entry: dict, connection_id: str | None) -> dict:
        """The connection used to publish, or one the owner picks: same platform and site (after a reconnect)."""
        original = self.store.connection(entry["connection"]["id"])
        if original is not None and not connection_id:
            return original
        chosen = self._connection(connection_id or entry["connection"]["id"])
        if chosen["kind"] != entry["connection"]["kind"] or chosen["domain"] != entry["domain"]:
            raise PublishError("Pick a connection to the same platform and site to roll this back.", 409)
        return chosen

    def _entry(self, entry_id: str) -> dict:
        entry = self.store.history_entry(entry_id)
        if entry is None:
            raise PublishError("No such publish in the history.", 404)
        return entry

    def history(self, url: str | None = None, limit: int = 50) -> list[dict]:
        entries = self.store.history(limit=limit if not url else 500)
        if url:
            entries = [e for e in entries if e.get("url") == url][:limit]
        return [public_history(e) for e in entries]

    def rollback(self, entry_id: str, connection_id: str | None = None) -> dict:
        self.require_ownership(self._entry(entry_id)["url"])
        with self.store.lock:  # claim the entry so two rollbacks of it cannot both run
            entry = self._entry(entry_id)
            if not entry.get("can_rollback"):
                raise PublishError("This publish cannot be rolled back.", 409)
            if entry.get("rolled_back_at") or entry.get("rolling_back") or entry.get("rollback_pending"):
                raise PublishError("This publish was already rolled back, or its rollback is waiting for a merge.", 409)
            if entry.get("status") == "applying":
                raise PublishError("This publish is still being sent. Try again in a moment.", 409)
            newer = [
                e for e in self.store.history(limit=500)
                if same_page(e.get("url") or "", entry["url"]) and e["id"] > entry["id"] and not e.get("rolled_back_at") and e.get("status") not in {"failed", "closed"}
            ]
            if newer:
                raise PublishError("A newer publish of this page is live. Roll that one back first.", 409, newer=newer[0]["id"])
            entry["rolling_back"] = True
            self.store.put_history(entry)
        self.checkpoint()  # on a cloud host, other instances see the claim before the platform call
        try:
            connection = self._rollback_connection(entry, connection_id)
            with self.http_factory() as client:
                connector = self._connector(connection, client)
                result = guarded(lambda: connector.rollback(entry), 502, prefix="Rollback failed. ")
        except PublishError:
            self._merge_entry(entry_id, {"rolling_back": False})
            raise
        now = self.now()
        updates = {"rolling_back": False, "rollback": {"who": self.who, "at": now, **result}}
        if result.get("status") == "pull_request":
            updates["rollback_pending"] = True  # a revert pull request: rolled back once it is merged (Check again)
        else:
            updates["rolled_back_at"] = now
        return self._merge_entry(entry_id, updates)


FIX_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")


def clean_fixes(fixes) -> list[dict]:
    """Issues the editor applied a suggestion for ({id, title} from the audit), kept on the history entry so a live
    publish can mark them done. Anything that is not that shape is dropped."""
    found, seen = [], set()
    for fix in fixes or []:
        if not isinstance(fix, dict):
            continue
        fix_id = str(fix.get("id") or "").strip()
        if not FIX_ID.match(fix_id) or fix_id in seen:
            continue
        seen.add(fix_id)
        found.append({"id": fix_id, "title": " ".join(str(fix.get("title") or "").split())[:160]})
        if len(found) >= 50:
            break
    return found


def guarded(call, status: int, *, prefix: str = ""):
    """Run a connector call. Any failure, including an unexpected platform answer, becomes a PublishError."""
    try:
        return call()
    except PublishError:
        raise
    except ConnectorError as exc:
        raise PublishError(f"{prefix}{exc}", status) from exc
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
        raise PublishError(f"{prefix}The platform sent an answer Ranksim did not expect ({type(exc).__name__}).", 502) from exc


_ASCII = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00a0": " "})


def comparable(text: str) -> str:
    """Text as a live check compares it: entities decoded, Unicode normalized, curly quotes and long dashes plain."""
    text = unicodedata.normalize("NFKC", htmllib.unescape(text or "")).translate(_ASCII)
    return " ".join(text.split())


def recheck_order(record: dict) -> list[str]:
    """The method that last worked first, then the others, so one lost proof never hides another valid one."""
    first = [record["method"]] if record.get("method") in own.METHODS else []
    return first + [m for m in own.METHODS if m not in first]


def catalog() -> list[dict]:
    """Every platform, what it can change, and what it cannot. Safe for the browser."""
    return [cls.describe() for cls in CONNECTORS.values()]
