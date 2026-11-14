"""Per-project publishing state: ownership records, connections (credentials sealed), and publish history.

Layout under ``project.dir / "plugins" / "publish"``:

    state.json            {"ownership": {domain: record}, "connections": {id: connection}}
    history/<id>.json     one publish: who, when, what changed, the previous content, the platform result

Credentials are sealed with ``aeo.ranksim.secretbox`` (``seal(plaintext, *, purpose)`` / ``unseal(token, *, purpose)``).
The purpose binds a sealed value to its project and connection, so a token copied elsewhere does not open. Nothing in
this module ever returns a credential to a caller outside the publishing package: ``public_connection`` drops it.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
HISTORY_KEEP = 500


class StoreError(RuntimeError):
    pass


def _box():
    try:
        from aeo.ranksim import secretbox  # owned by the GSC workstream
    except ImportError as exc:  # pragma: no cover - exercised only before secretbox lands
        raise StoreError("Credential encryption is not set up on this server yet, so connections cannot be saved.") from exc
    return secretbox


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.RLock()
        return _LOCKS[key]


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class PublishStore:
    def __init__(self, folder: str | Path, *, scope: str) -> None:
        """folder: the plugin folder. scope: a stable name (the project slug) mixed into every sealing purpose."""
        self.folder = Path(folder)
        self.scope = scope
        self.lock = _lock_for(self.folder)

    @classmethod
    def for_project(cls, project) -> "PublishStore":
        return cls(Path(project.dir) / "plugins" / "publish", scope=project.slug)

    # -- state -------------------------------------------------------------

    @property
    def state_file(self) -> Path:
        return self.folder / "state.json"

    def load(self) -> dict:
        data = _read_json(self.state_file, {})
        data.setdefault("ownership", {})
        data.setdefault("connections", {})
        return data

    def save(self, data: dict) -> None:
        _write_json(self.state_file, data)

    # -- ownership ---------------------------------------------------------

    def ownership(self, domain: str) -> dict | None:
        return self.load()["ownership"].get(domain)

    def put_ownership(self, record: dict) -> None:
        with self.lock:
            data = self.load()
            data["ownership"][record["domain"]] = record
            self.save(data)

    # -- connections -------------------------------------------------------

    def _purpose(self, connection_id: str) -> str:
        return f"ranksim.publish:{self.scope}:{connection_id}"

    def add_connection(self, *, kind: str, domain: str, label: str, settings: dict, secret: dict, now: float | None = None) -> dict:
        connection_id = "c" + secrets.token_hex(6)
        sealed = _box().seal(json.dumps(secret, sort_keys=True), purpose=self._purpose(connection_id))
        connection = {
            "id": connection_id,
            "kind": kind,
            "domain": domain,
            "label": label,
            "settings": settings,
            "sealed": sealed,
            "created": time.time() if now is None else now,
        }
        with self.lock:
            data = self.load()
            data["connections"][connection_id] = connection
            self.save(data)
        return connection

    def connection(self, connection_id: str) -> dict | None:
        return self.load()["connections"].get(connection_id)

    def connections(self) -> list[dict]:
        return sorted(self.load()["connections"].values(), key=lambda c: c.get("created") or 0)

    def secret(self, connection: dict) -> dict:
        try:
            plain = _box().unseal(connection["sealed"], purpose=self._purpose(connection["id"]))
        except StoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - a key change or tampering; never echo the token
            raise StoreError("The saved credentials for this connection could not be opened. Connect it again.") from exc
        return json.loads(plain)

    def remove_connection(self, connection_id: str) -> bool:
        with self.lock:
            data = self.load()
            removed = data["connections"].pop(connection_id, None)
            self.save(data)
        return removed is not None

    # -- history -----------------------------------------------------------

    @property
    def history_dir(self) -> Path:
        return self.folder / "history"

    def new_history_id(self, now: float) -> str:
        return time.strftime("%Y%m%d%H%M%S", time.gmtime(now)) + "-" + secrets.token_hex(3)

    def put_history(self, entry: dict) -> None:
        with self.lock:
            _write_json(self.history_dir / f"{entry['id']}.json", entry)
            files = sorted(self.history_dir.glob("*.json"))
            for old in files[:-HISTORY_KEEP]:
                old.unlink(missing_ok=True)

    def history_entry(self, entry_id: str) -> dict | None:
        if not entry_id or not all(ch.isalnum() or ch == "-" for ch in entry_id):
            return None
        return _read_json(self.history_dir / f"{entry_id}.json", None)

    def history(self, limit: int = 50) -> list[dict]:
        if not self.history_dir.is_dir():
            return []
        files = sorted(self.history_dir.glob("*.json"), reverse=True)[:limit]
        return [entry for entry in (_read_json(path, None) for path in files) if entry]


def public_connection(connection: dict) -> dict:
    """Safe for the browser: no sealed credentials, no secret settings."""
    return {
        "id": connection["id"],
        "kind": connection["kind"],
        "domain": connection["domain"],
        "label": connection.get("label") or connection["kind"],
        "settings": {k: v for k, v in (connection.get("settings") or {}).items() if "secret" not in k and "token" not in k and "password" not in k},
        "created": connection.get("created"),
        "has_credentials": bool(connection.get("sealed")),
    }


def public_history(entry: dict) -> dict:
    """History for the browser: drops the stored previous content (it can be large) but keeps what changed."""
    view = {k: v for k, v in entry.items() if k not in {"previous", "before_html", "after_html"}}
    view["can_rollback"] = bool(entry.get("can_rollback")) and not entry.get("rolled_back_at")
    return view
