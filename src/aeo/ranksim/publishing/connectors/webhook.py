"""A signed webhook: POST the edited page to your own endpoint, which decides how to publish it.

Request: ``POST <url>`` with JSON
``{"event": "ranksim.publish" | "ranksim.rollback" | "ranksim.ping", "id", "url", "html", "changes", "sent_at"}``.

Headers: ``X-Ranksim-Event``, ``X-Ranksim-Delivery`` and ``X-Ranksim-Signature: t=<unix seconds>,v1=<hex>``, where
``v1 = HMAC-SHA256(secret, "<t>." + raw request body)``. Check the signature and reject old timestamps (5 minutes).
Any 2xx reply counts as accepted. Redirects are not followed, and the address must be public.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from ..htmlparts import parts
from .base import Connector, ConnectorError, Prepared


def signature(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("ascii") + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_signature(secret: str, header: str, body: bytes, *, now: float | None = None, tolerance: int = 300) -> bool:
    """Reference check for receivers (and tests)."""
    try:
        fields = dict(part.split("=", 1) for part in header.split(","))
        timestamp = int(fields["t"])
    except (ValueError, KeyError):
        return False
    now = time.time() if now is None else now
    if abs(now - timestamp) > tolerance:
        return False
    return hmac.compare_digest(signature(secret, timestamp, body), header)


class Webhook(Connector):
    kind = "webhook"
    label = "Webhook"
    setting_fields = ("url", "content_selector")
    secret_fields = ("signing_secret",)
    summary = "Sends the whole edited page to your endpoint, signed with your secret. Your endpoint publishes it."
    limits = ("Your endpoint decides what goes live. Rollback sends the previous page the same way.",)

    @classmethod
    def clean_secret(cls, secret: dict) -> dict:
        found = super().clean_secret(secret)
        if len(found["signing_secret"]) < 16:
            raise ConnectorError("Use a signing secret of at least 16 characters.")
        return found

    def send(self, event: str, payload: dict) -> dict:
        target = self.safe_url(str(self.settings.get("url", "")))
        if not target.startswith("https://") and not self.allow_private:
            raise ConnectorError("The webhook address must use https.")
        delivery = secrets.token_hex(8)
        now = int(time.time())
        body = json.dumps({"event": event, "id": delivery, "sent_at": now, **payload}, sort_keys=True).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "X-Ranksim-Event": event,
            "X-Ranksim-Delivery": delivery,
            "X-Ranksim-Signature": signature(self.secret["signing_secret"], now, body),
        }
        response = self.request("POST", target, content=body, headers=headers, expect=None)
        if not 200 <= response.status_code < 300:
            raise ConnectorError(f"The webhook answered {response.status_code}.")
        return {"delivery": delivery, "status_code": response.status_code}

    def check(self) -> str:
        self.send("ranksim.ping", {"url": "", "html": "", "changes": []})
        return str(self.settings.get("url", ""))

    def prepare(self, url: str, before_html: str, after_html: str, *, slug: str = "") -> Prepared:
        before, after = parts(before_html, self.regions()), parts(after_html, self.regions())
        rows = self.rows_for(before, after)
        for row in rows:
            row.update(apply=True, note="Sent to your endpoint.")
        return Prepared(
            url=url,
            target={"url": str(self.settings.get("url", ""))},
            rows=rows,
            warnings=self.embed_warning(before, after, rows),
            payload={"html": after_html, "changes": [{k: row[k] for k in ("field", "before", "after")} for row in rows]},
            previous={"html": before_html},
        )

    def apply(self, prepared: Prepared, *, options: dict | None = None) -> dict:
        sent = self.send("ranksim.publish", {"url": prepared.url, **prepared.payload})
        return {"status": "live", **sent}

    def rollback(self, entry: dict) -> dict:
        sent = self.send("ranksim.rollback", {"url": entry["url"], "html": entry["previous"]["html"], "changes": [], "rollback_of": entry["id"]})
        return {"status": "live", **sent}
