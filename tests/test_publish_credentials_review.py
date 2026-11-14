"""Review fix from the gsc session: a publish credential is sealed together with a digest of the settings that decide
where it is sent, so editing the stored settings cannot redirect it."""

from __future__ import annotations

import json

import pytest

from aeo.ranksim import secretbox
from aeo.ranksim.publishing.store import PublishStore, StoreError


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(secretbox.SECRET_ENV, "publish-review-secret-0123456789-abcdef")
    return PublishStore(tmp_path / "publish", scope="site")


def test_secret_round_trips(store):
    made = store.add_connection(kind="wordpress", domain="example.com", label="WP", settings={"base_url": "https://example.com", "username": "me"}, secret={"app_password": "abcd efgh"})
    assert store.secret(store.connection(made["id"])) == {"app_password": "abcd efgh"}
    assert "abcd efgh" not in store.state_file.read_text()


def test_edited_settings_lock_the_secret(store):
    made = store.add_connection(kind="wordpress", domain="example.com", label="WP", settings={"base_url": "https://example.com", "username": "me"}, secret={"app_password": "abcd efgh"})
    data = json.loads(store.state_file.read_text())
    data["connections"][made["id"]]["settings"]["base_url"] = "https://attacker.example"
    store.state_file.write_text(json.dumps(data))
    with pytest.raises(StoreError, match="changed outside Ranksim"):
        store.secret(store.connection(made["id"]))
    data["connections"][made["id"]]["settings"]["base_url"] = "https://example.com"
    data["connections"][made["id"]]["domain"] = "attacker.example"
    store.state_file.write_text(json.dumps(data))
    with pytest.raises(StoreError):
        store.secret(store.connection(made["id"]))


def test_a_secret_sealed_before_the_binding_still_opens(store):
    made = store.add_connection(kind="webhook", domain="example.com", label="Hook", settings={"url": "https://example.com/hook"}, secret={"signing_secret": "x" * 20})
    data = json.loads(store.state_file.read_text())
    legacy = secretbox.seal(json.dumps({"signing_secret": "y" * 20}), purpose=store._purpose(made["id"]))
    data["connections"][made["id"]]["sealed"] = legacy
    store.state_file.write_text(json.dumps(data))
    assert store.secret(store.connection(made["id"])) == {"signing_secret": "y" * 20}
