"""Publishing connectors, one per platform."""

from __future__ import annotations

from .base import Connector, ConnectorError, Prepared
from .github import GitHub
from .shopify import Shopify
from .webflow import Webflow
from .webhook import Webhook
from .wordpress import WordPress

CONNECTORS: dict[str, type[Connector]] = {c.kind: c for c in (WordPress, Webflow, Shopify, GitHub, Webhook)}

__all__ = ["CONNECTORS", "Connector", "ConnectorError", "Prepared"]
