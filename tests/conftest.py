from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from aeo.config import Settings

LIVE_ENV = "RANKSIM_LIVE_TESTS"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
# A proxy on loopback would carry a test's request to the internet past the socket guard,
# so normal tests run without proxy settings and must connect directly (and are refused).
_PROXY_VARS = {"http_proxy", "https_proxy", "all_proxy", "ws_proxy", "wss_proxy"}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "database_url": f"sqlite:///{tmp_path / 'aeo.db'}",
        "search_provider": "mock",
        "llm_provider": "mock",
        "embedding_provider": "mock",
        "process_jobs_inline": True,
        "crawl_delay_seconds": 0,
        "crawl_allow_private": False,
        "embedding_dim": 64,
        "brave_api_key": None,
        "openai_api_key": None,
    }
    values.update(overrides)
    return Settings(**values)


def pytest_collection_modifyitems(config, items):
    if os.environ.get(LIVE_ENV) == "1":
        return
    skip = pytest.mark.skip(reason=f"live provider test; set {LIVE_ENV}=1 and real keys to run it")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


# Read directly from os.environ by the code, outside Settings.
_EXTRA_ENV = {"ranksim_root"}


@pytest.fixture(autouse=True)
def _isolate_settings_environment(request, monkeypatch):
    """Normal tests never see a developer's provider keys, settings env vars, or .env file.

    Settings is a pydantic BaseSettings that reads every field from the environment
    (case-insensitive) and from ./.env. Without this, a machine with OPENROUTER_API_KEY or
    SERPER_API_KEY exported gets different test results from CI.
    """
    if request.node.get_closest_marker("live"):
        return  # live tests run with the developer's real keys on purpose
    fields = {name.lower() for name in Settings.model_fields} | _EXTRA_ENV
    for name in list(os.environ):
        if name.lower() in fields:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    # The default ranksim database is ./data/ranksim.db in the checkout. A command run without
    # --database (and any registry it writes) must use a per-test file instead.
    tmp_path = request.getfixturevalue("tmp_path")
    monkeypatch.setenv("RANKSIM_DATABASE_URL", f"sqlite:///{tmp_path / 'ranksim-default.db'}")
    # A --final run appends to the git-tracked smartcrew/final_runs.jsonl; tests use a per-test file.
    monkeypatch.setenv("RANKSIM_FINAL_RUNS", str(tmp_path / "final_runs.jsonl"))


@pytest.fixture(autouse=True)
def _block_external_network(request, monkeypatch):
    """Fail loudly if a normal test opens a non-loopback socket. Live tests are exempt."""
    if request.node.get_closest_marker("live"):
        return
    for name in list(os.environ):
        if name.lower() in _PROXY_VARS:
            monkeypatch.delenv(name, raising=False)
    # On macOS and Windows, urllib (and so httpx) also reads the system proxy settings, which can
    # point at a loopback proxy. Report no proxies at all for normal tests.
    import urllib.request

    import httpx._utils

    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(httpx._utils, "getproxies", lambda: {}, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _allowed(sock: socket.socket, address) -> bool:
        if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
            return True
        host = address[0] if isinstance(address, tuple) and address else None
        return host in _LOOPBACK_HOSTS

    def guarded_connect(sock, address):
        if not _allowed(sock, address):
            raise RuntimeError(
                f"Test tried to open a network connection to {address!r}. "
                "Mock the transport, or mark the test with @pytest.mark.live."
            )
        return real_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if not _allowed(sock, address):
            raise RuntimeError(f"Test tried to open a network connection to {address!r}.")
        return real_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
