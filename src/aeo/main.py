"""ASGI entrypoint. ``uvicorn aeo.main:app`` uses the settings in the environment."""

from __future__ import annotations

from aeo.api import create_app

app = create_app()
