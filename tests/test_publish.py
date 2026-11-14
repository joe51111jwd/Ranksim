"""Ownership proof and publishing. Every platform, resolver and page fetch is mocked; nothing leaves the process."""

from __future__ import annotations

import base64
from html import escape
import hashlib
import json
import re
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from aeo.ranksim.publishing import ownership as own
from aeo.ranksim.publishing.connectors import CONNECTORS
from aeo.ranksim.publishing.connectors.base import ConnectorError
from aeo.ranksim.publishing.connectors.webhook import signature, verify_signature
from aeo.ranksim.publishing.htmlparts import changes, parts
from aeo.ranksim.publishing.service import PublishError, Publisher, cache_busted, catalog
from aeo.ranksim.publishing.store import PublishStore, StoreError, public_connection

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeBox(types.ModuleType):
    """Stands in for aeo.ranksim.secretbox (seal/unseal bound to a purpose) until the real one lands."""

    def __init__(self) -> None:
        super().__init__("aeo.ranksim.secretbox")
        self.key = b"test-key"

    def _pad(self, purpose: str, n: int) -> bytes:
        out = b""
        counter = 0
        while len(out) < n:
            out += hashlib.sha256(self.key + purpose.encode() + counter.to_bytes(4, "big")).digest()
            counter += 1
        return out[:n]

    def seal(self, plaintext: str, *, purpose: str) -> str:
        raw = plaintext.encode()
        mac = hashlib.sha256(self.key + purpose.encode() + raw).hexdigest()[:16]
        body = bytes(a ^ b for a, b in zip(raw, self._pad(purpose, len(raw))))
        return "fake1." + mac + "." + base64.urlsafe_b64encode(body).decode()

    def unseal(self, token: str, *, purpose: str) -> str:
        _v, mac, body = token.split(".", 2)
        enc = base64.urlsafe_b64decode(body)
        raw = bytes(a ^ b for a, b in zip(enc, self._pad(purpose, len(enc))))
        if hashlib.sha256(self.key + purpose.encode() + raw).hexdigest()[:16] != mac:
            raise ValueError("bad token")
        return raw.decode()


@pytest.fixture(autouse=True)
def box(monkeypatch):
    fake = FakeBox()
    import aeo.ranksim as pkg

    monkeypatch.setitem(sys.modules, "aeo.ranksim.secretbox", fake)
    monkeypatch.setattr(pkg, "secretbox", fake, raising=False)
    return fake


class Router:
    """httpx MockTransport handler: register (method, regex) -> handler(request, match)."""

    def __init__(self) -> None:
        self.routes: list = []
        self.calls: list[httpx.Request] = []

    def on(self, method: str, pattern: str, handler) -> None:
        self.routes.append((method, re.compile(pattern), handler))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        for method, pattern, handler in self.routes:
            if method == request.method and (m := pattern.search(url)):
                result = handler(request, m)
                if isinstance(result, httpx.Response):
                    return result
                return httpx.Response(200, json=result)
        return httpx.Response(404, json={"message": f"no mock for {request.method} {url}"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def bodies(self, method: str, pattern: str) -> list:
        return [json.loads(r.content or b"null") for r in self.calls if r.method == method and re.search(pattern, str(r.url))]


class Web:
    """The live web as the fetcher sees it: url -> html. Query strings (cache busters) are ignored."""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        self.fetched: list[str] = []

    @staticmethod
    def key(url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}{p.path}"

    def __call__(self, url: str) -> tuple[str, str]:
        self.fetched.append(url)
        k = self.key(url)
        if k in self.errors:
            return "", self.errors[k]
        if k in self.pages:
            return self.pages[k], ""
        return "", "HTTP 404"


def page(title="Old title | Acme", desc="Old description.", h1="Old heading", body="<p>Old body text.</p>", region="entry-content", extra=""):
    return (
        f"<!doctype html><html><head><title>{title}</title><meta name=\"description\" content=\"{escape(desc, quote=True)}\">{extra}</head>"
        f"<body><header>Menu</header><h1>{h1}</h1><div class=\"{region}\">{body}</div><footer>Foot</footer></body></html>"
    )


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_publisher(tmp_path: Path, web: Web, router: Router, clock: Clock | None = None) -> Publisher:
    store = PublishStore(tmp_path / "publish", scope="acme")
    return Publisher(
        store,
        fetch=web,
        http_factory=router.client,
        now=clock or Clock(),
        sleep=lambda s: None,
        verify_waits=(0.0, 1.0),
        allow_private=True,
    )


def dns_answer(*values, status=0):
    return {"Status": status, "Answer": [{"name": "acme.com.", "type": 16, "data": v} for v in values]}


def owned(pub: Publisher, web: Web, domain="acme.com") -> dict:
    record = pub.start_ownership(domain)
    web.pages[f"https://{domain}/"] = page(extra=f'<meta name="ranksim-verification" content="{record["token"]}">')
    result = pub.verify_ownership(domain)
    assert result["verified"] and result["method"] == "meta"
    return result


# ---------------------------------------------------------------------------
# Page parts
# ---------------------------------------------------------------------------


def test_parts_and_changes_find_each_field():
    before = parts(page(), [".entry-content"])
    after = parts(page(title="New title | Acme", h1="New heading", body="<p>New body text.</p>"), [".entry-content"])
    found = {c["field"]: c for c in changes(before, after)}
    assert set(found) == {"title", "h1", "body"}
    assert found["title"]["after"] == "New title | Acme"
    assert found["body"]["after"] == "New body text."
    assert after.region_html == "<p>New body text.</p>"


def test_body_change_without_region_uses_body_text():
    before = parts(page(region="x"), [".entry-content"])
    after = parts(page(region="x", body="<p>Other</p>"), [".entry-content"])
    assert [c["field"] for c in changes(before, after)] == ["body"]


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_site_key_normalizes_and_rejects_junk():
    assert own.site_key("https://WWW.Acme.com/blog/x") == "acme.com"
    assert own.site_key("acme.com") == "acme.com"
    for bad in ("", "not a site", "http://", "localhost", "acme.com;rm"):
        with pytest.raises(own.OwnershipError):
            own.site_key(bad)


def test_tokens_are_random_per_site(tmp_path):
    pub = make_publisher(tmp_path, Web(), Router())
    a, b = pub.start_ownership("acme.com"), pub.start_ownership("other.com")
    assert a["token"] != b["token"] and len(a["token"]) > 20
    assert pub.start_ownership("www.acme.com")["token"] == a["token"]  # stable until reset
    assert pub.new_token("acme.com")["token"] != a["token"]
    assert set(a["instructions"]) == {"meta", "file", "dns"}
    assert a["token"] in a["instructions"]["meta"]["value"]


def test_meta_tag_proves_ownership(tmp_path):
    web = Web()
    pub = make_publisher(tmp_path, web, Router())
    record = owned(pub, web)
    assert record["scope"] == "host" and record["verified_at"]
    assert web.fetched[0] == "https://acme.com/"


def test_meta_tag_with_other_token_fails(tmp_path):
    web = Web()
    pub = make_publisher(tmp_path, web, Router())
    pub.start_ownership("acme.com")
    web.pages["https://acme.com/"] = page(extra='<meta name="ranksim-verification" content="someone-else">')
    result = pub.verify_ownership("acme.com", ["meta"])
    assert not result["verified"]
    assert "different token" in result["last_check"]["detail"]


def test_file_proves_ownership_and_404_is_definitive(tmp_path):
    web = Web()
    pub = make_publisher(tmp_path, web, Router())
    token = pub.start_ownership("acme.com")["token"]
    result = pub.verify_ownership("acme.com", ["file"])
    assert not result["verified"] and result["last_check"]["attempts"][0]["definitive"]
    web.pages["https://acme.com/.well-known/ranksim-verification.txt"] = f"\n{token}\n"
    result = pub.verify_ownership("acme.com", ["file"])
    assert result["verified"] and result["method"] == "file"


def test_dns_txt_proves_ownership_over_doh(tmp_path):
    router = Router()
    pub = make_publisher(tmp_path, Web(), router)
    token = pub.start_ownership("acme.com")["token"]
    half = len(token) // 2
    # a long TXT value split into two quoted strings, next to an unrelated record
    router.on("GET", r"cloudflare-dns\.com", lambda r, m: dns_answer('"v=spf1 -all"', f'"ranksim-verification={token[:half]}" "{token[half:]}"'))
    result = pub.verify_ownership("acme.com", ["dns"])
    assert result["verified"] and result["scope"] == "domain"
    query = parse_qs(urlsplit(str(router.calls[0].url)).query)
    assert query == {"name": ["acme.com"], "type": ["TXT"]}
    assert router.calls[0].headers["accept"] == "application/dns-json"


def test_dns_falls_back_to_second_resolver_and_handles_nxdomain(tmp_path):
    router = Router()
    pub = make_publisher(tmp_path, Web(), router)
    pub.start_ownership("acme.com")
    router.on("GET", r"cloudflare-dns\.com", lambda r, m: httpx.Response(503))
    router.on("GET", r"dns\.google", lambda r, m: {"Status": 3})
    result = pub.verify_ownership("acme.com", ["dns"])
    assert not result["verified"]
    attempt = result["last_check"]["attempts"][0]
    assert attempt["definitive"] and "no DNS records" in attempt["detail"]
    assert any("dns.google" in str(c.url) for c in router.calls)


def test_resolver_outage_keeps_recent_proof_but_not_forever(tmp_path):
    router, web, clock = Router(), Web(), Clock()
    pub = make_publisher(tmp_path, web, router, clock)
    token = pub.start_ownership("acme.com")["token"]
    router.on("GET", r"cloudflare-dns\.com", lambda r, m: dns_answer(f'"ranksim-verification={token}"'))
    assert pub.verify_ownership("acme.com", ["dns"])["verified"]
    router.routes.clear()  # every resolver now fails: the check cannot run
    clock.t += 2 * 24 * 3600
    result = pub.verify_ownership("acme.com", ["dns"])
    assert result["verified"] and result["last_check"]["kept_by_grace"]
    clock.t += 8 * 24 * 3600
    assert not pub.verify_ownership("acme.com", ["dns"])["verified"]


def test_proof_removed_revokes_on_recheck_before_publish(tmp_path):
    web, clock = Web(), Clock()
    pub = make_publisher(tmp_path, web, Router(), clock)
    owned(pub, web)
    web.pages["https://acme.com/"] = page()  # tag removed
    clock.t += own.STALE_AFTER + 1
    with pytest.raises(PublishError) as err:
        pub.require_ownership("https://acme.com/about")
    assert err.value.status == 403
    assert not pub.store.ownership("acme.com")["verified"]


def test_recheck_stale_runs_periodically(tmp_path):
    web, clock = Web(), Clock()
    pub = make_publisher(tmp_path, web, Router(), clock)
    owned(pub, web)
    assert pub.recheck_stale() == []
    clock.t += own.STALE_AFTER + 5
    assert pub.recheck_stale() == ["acme.com"]
    assert pub.store.ownership("acme.com")["checked_at"] == clock.t


def test_dns_proof_covers_subdomains_but_meta_does_not(tmp_path):
    record = {"domain": "acme.com", "verified": True, "scope": "host"}
    assert own.covers(record, "https://www.acme.com/x")
    assert not own.covers(record, "https://blog.acme.com/x")
    assert own.covers({**record, "scope": "domain"}, "https://blog.acme.com/x")
    assert not own.covers({**record, "scope": "domain"}, "https://evilacme.com/")


def test_fetch_errors_are_not_definitive(tmp_path):
    web = Web()
    web.errors["https://acme.com/"] = "blocked by robots.txt"
    web.errors["https://www.acme.com/"] = "refused: private address"
    pub = make_publisher(tmp_path, web, Router())
    pub.start_ownership("acme.com")
    result = pub.verify_ownership("acme.com", ["meta"])
    assert not result["verified"]
    assert result["last_check"]["attempts"][0]["definitive"] is False


# ---------------------------------------------------------------------------
# Credentials at rest
# ---------------------------------------------------------------------------


def test_credentials_are_sealed_and_bound_to_their_connection(tmp_path, box):
    store = PublishStore(tmp_path / "p", scope="acme")
    c = store.add_connection(kind="webhook", domain="acme.com", label="Hook", settings={"url": "https://h.example/x"}, secret={"signing_secret": "s3cret-value-1234567"})
    on_disk = (tmp_path / "p" / "state.json").read_text()
    assert "s3cret-value-1234567" not in on_disk
    assert store.secret(c) == {"signing_secret": "s3cret-value-1234567"}
    other = PublishStore(tmp_path / "p", scope="different-project")
    with pytest.raises(StoreError):
        other.secret(c)  # purpose mismatch: a sealed value copied to another project does not open
    view = public_connection(c)
    assert "sealed" not in view and "s3cret" not in json.dumps(view)


def test_seal_round_trip_uses_secretbox_interface(box):
    token = box.seal("hello", purpose="a")
    assert box.unseal(token, purpose="a") == "hello"
    with pytest.raises(ValueError):
        box.unseal(token, purpose="b")


# ---------------------------------------------------------------------------
# Ownership is required
# ---------------------------------------------------------------------------


def add_webhook(pub: Publisher, router: Router, site="acme.com", url="https://hooks.acme.test/rs"):
    router.on("POST", re.escape(url), lambda r, m: httpx.Response(202))
    return pub.add_connection(kind="webhook", site=site, label="", settings={"url": url}, secret={"signing_secret": "k" * 24})


def test_publish_refused_without_ownership(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    conn = add_webhook(pub, router)
    web.pages["https://acme.com/about"] = page()
    with pytest.raises(PublishError) as err:
        pub.publish("https://acme.com/about", page(h1="New"), conn["id"])
    assert err.value.status == 403 and err.value.extra["code"] == "not_owned"
    pub.start_ownership("acme.com")  # a token alone is not proof
    with pytest.raises(PublishError):
        pub.publish("https://acme.com/about", page(h1="New"), conn["id"])


def test_connection_for_another_site_is_refused(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = add_webhook(pub, router, site="other.com")
    web.pages["https://acme.com/about"] = page()
    with pytest.raises(PublishError) as err:
        pub.publish("https://acme.com/about", page(h1="New"), conn["id"])
    assert err.value.status == 403


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


def test_webhook_publish_signs_verifies_live_and_rolls_back(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    url = "https://acme.com/about"
    old, new = page(), page(h1="New heading", desc="New description.")
    web.pages[url] = old
    received = []

    def hook(request, m):
        received.append(request)
        data = json.loads(request.content)
        if data["event"] != "ranksim.ping":
            web.pages[url] = data["html"]
        return httpx.Response(200)

    router.on("POST", r"hooks\.acme\.test", hook)
    conn = pub.add_connection(kind="webhook", site="acme.com", label="", settings={"url": "https://hooks.acme.test/rs"}, secret={"signing_secret": "k" * 24})
    entry = pub.publish(url, new, conn["id"])
    assert entry["status"] == "live" and entry["verify"]["status"] == "confirmed"
    assert set(entry["fields"]) == {"h1", "description"}
    request = received[-1]
    assert verify_signature("k" * 24, request.headers["X-Ranksim-Signature"], request.content, now=json.loads(request.content)["sent_at"])
    assert not verify_signature("wrong-secret-xxxxxxxx", request.headers["X-Ranksim-Signature"], request.content)
    body = json.loads(request.content)
    assert body["event"] == "ranksim.publish" and body["html"] == new
    rolled = pub.rollback(entry["id"])
    assert rolled["rolled_back_at"] and not rolled["can_rollback"]
    assert json.loads(received[-1].content)["event"] == "ranksim.rollback"
    assert web.pages[url] == old
    with pytest.raises(PublishError):
        pub.rollback(entry["id"])


def test_webhook_refuses_private_targets_and_short_secrets(tmp_path):
    with pytest.raises(ConnectorError):
        CONNECTORS["webhook"].clean_secret({"signing_secret": "short"})
    router = Router()
    hook = CONNECTORS["webhook"]({"url": "https://10.0.0.5/x"}, {"signing_secret": "k" * 20}, router.client(), allow_private=False)
    with pytest.raises(ConnectorError, match="cannot be used"):
        hook.check()
    assert router.calls == []


def test_signature_format():
    sig = signature("secret", 100, b"{}")
    assert sig.startswith("t=100,v1=") and len(sig.split("v1=")[1]) == 64
    assert not verify_signature("secret", sig, b"{}", now=100 + 3600)


def test_live_check_reports_pending_then_confirms(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    url = "https://acme.com/about"
    web.pages[url] = page()
    conn = add_webhook(pub, router)  # accepts but does not change the live page (a cache, say)
    entry = pub.publish(url, page(h1="Cached heading"), conn["id"])
    assert entry["verify"]["status"] == "pending" and entry["verify"]["attempts"] == 2
    assert all("ranksim_check=" in u for u in web.fetched[-2:])
    web.pages[url] = page(h1="Cached heading")
    assert pub.verify_live(entry["id"])["verify"]["status"] == "confirmed"


def test_cache_busted_keeps_query():
    assert cache_busted("https://a.com/x?y=1", 1.0) == "https://a.com/x?y=1&ranksim_check=1000"


# ---------------------------------------------------------------------------
# WordPress
# ---------------------------------------------------------------------------

WP = "https://acme.com/wp-json"


def wp_router(web: Web, url: str, *, namespaces=("wp/v2",), meta=None, raw_content="<p>Old body text.</p>"):
    router = Router()
    item = {
        "id": 42,
        "link": url + "/",
        "title": {"raw": "Old heading", "rendered": "Old heading"},
        "content": {"raw": raw_content, "rendered": "<p>Old body text.</p>\n"},
        "excerpt": {"raw": "Old excerpt", "rendered": "<p>Old excerpt</p>"},
        "meta": meta or {},
    }
    router.on("GET", re.escape(WP) + r"/wp/v2/users/me", lambda r, m: {"name": "James"})
    router.on("GET", re.escape(WP) + r"/$", lambda r, m: {"namespaces": list(namespaces)})
    router.on("GET", re.escape(WP) + r"/wp/v2/pages\?", lambda r, m: [])
    router.on("GET", re.escape(WP) + r"/wp/v2/posts\?", lambda r, m: [item] if "slug=about" in str(r.url) else [])

    def update(request, m):
        data = json.loads(request.content)
        for key in ("title", "content", "excerpt"):
            if key in data:
                item[key] = {"raw": data[key], "rendered": data[key]}
        item["meta"].update(data.get("meta") or {})
        live = parts(web.pages[url])
        web.pages[url] = page(
            title=live.title if "title" not in data else f"{data['title']} | Acme",
            desc=live.description,
            h1=item["title"]["raw"],
            body=item["content"]["raw"],
        )
        return item

    def rankmath(request, m):
        data = json.loads(request.content)["meta"]
        live = parts(web.pages[url], [".entry-content"])
        web.pages[url] = page(
            title=data.get("rank_math_title", live.title),
            desc=data.get("rank_math_description", live.description),
            h1=live.h1,
            body=live.region_html,
        )
        return {"slug": True}

    router.on("POST", re.escape(WP) + r"/wp/v2/posts/42", update)
    router.on("POST", re.escape(WP) + r"/rankmath/v1/updateMeta", rankmath)
    return router, item


def wp_connect(pub):
    return pub.add_connection(kind="wordpress", site="acme.com", label="", settings={"base_url": "https://acme.com", "username": "james"}, secret={"app_password": "abcd efgh ijkl"})


def test_wordpress_rank_math_full_publish_and_rollback(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    web.pages[url] = page()
    router, item = wp_router(web, url, namespaces=("wp/v2", "rankmath/v1"))
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = wp_connect(pub)
    assert conn["account"].startswith("James") and "app_password" not in json.dumps(conn)
    auth = router.calls[0].headers["authorization"]
    assert auth == "Basic " + base64.b64encode(b"james:abcd efgh ijkl").decode()

    new = page(title="New SEO title", desc="New description.", h1="New heading", body="<p>New body text.</p>")
    plan = pub.plan(url, new, conn["id"])
    rows = {r["field"]: r for r in plan["plan"]["rows"]}
    assert all(r["apply"] for r in rows.values()) and set(rows) == {"title", "description", "h1", "body"}
    assert plan["plan"]["target"]["plugin"] == "rankmath"

    entry = pub.publish(url, new, conn["id"])
    assert entry["verify"]["status"] == "confirmed", entry["verify"]
    post = router.bodies("POST", r"/wp/v2/posts/42")[-1]
    assert post == {"title": "New heading", "content": "<p>New body text.</p>"}
    rm = router.bodies("POST", r"updateMeta")[-1]
    assert rm == {"objectType": "post", "objectID": 42, "meta": {"rank_math_title": "New SEO title", "rank_math_description": "New description."}}

    history = pub.history()
    assert history[0]["id"] == entry["id"] and history[0]["who"] == "owner"
    assert "previous" not in history[0] and history[0]["connection"]["kind"] == "wordpress"
    stored = pub.store.history_entry(entry["id"])
    assert stored["previous"]["content"] == "<p>Old body text.</p>" and stored["previous"]["title"] == "Old heading"

    pub.rollback(entry["id"])
    restore = router.bodies("POST", r"/wp/v2/posts/42")[-1]
    assert restore == {"title": "Old heading", "content": "<p>Old body text.</p>"}
    # Rank Math did not expose stored values, so rollback clears the fields and Rank Math's template applies again
    assert router.bodies("POST", r"updateMeta")[-1]["meta"] == {"rank_math_title": "", "rank_math_description": ""}


def test_wordpress_without_seo_plugin_uses_title_suffix_and_excerpt(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    web.pages[url] = page(h1="Old heading", title="Old heading | Acme")
    router, item = wp_router(web, url)
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = wp_connect(pub)
    plan = pub.plan(url, page(h1="Old heading", title="Better heading | Acme", desc="Fresh."), conn["id"])["plan"]
    rows = {r["field"]: r for r in plan["rows"]}
    assert rows["title"]["apply"] and "theme uses" in rows["title"]["note"]
    assert rows["description"]["apply"] and "excerpt" in rows["description"]["note"]
    assert plan["warnings"][0]["code"] == "title_changes_heading"  # same WordPress title feeds the h1
    with pytest.raises(PublishError) as err:
        pub.publish(url, page(h1="Old heading", title="Better heading | Acme", desc="Fresh."), conn["id"])
    assert err.value.extra["confirm"] == ["title_changes_heading"]
    entry = pub.publish(url, page(h1="Old heading", title="Better heading | Acme", desc="Fresh."), conn["id"], confirm=["title_changes_heading"])
    assert router.bodies("POST", r"/wp/v2/posts/42")[-1] == {"title": "Better heading", "excerpt": "Fresh."}
    # the theme in this mock does not print the excerpt as the description: said plainly, not hidden
    assert entry["verify"]["status"] == "partial"
    assert entry["verify"]["fields"] == {"title": True, "description": False}


def test_wordpress_yoast_meta_only_when_exposed(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    web.pages[url] = page()
    router, _ = wp_router(web, url, namespaces=("wp/v2", "yoast/v1"))
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = wp_connect(pub)
    rows = {r["field"]: r for r in pub.plan(url, page(title="T2", desc="D2"), conn["id"])["plan"]["rows"]}
    assert not rows["title"]["apply"] and "Yoast does not let" in rows["title"]["note"]
    assert not rows["description"]["apply"]
    with pytest.raises(PublishError) as err:
        pub.publish(url, page(title="T2", desc="D2"), conn["id"])
    assert err.value.status == 422

    router2, _ = wp_router(web, url, namespaces=("wp/v2", "yoast/v1"), meta={"_yoast_wpseo_title": "", "_yoast_wpseo_metadesc": ""})
    pub2 = make_publisher(tmp_path / "two", web, router2)
    owned(pub2, web)
    conn2 = wp_connect(pub2)
    pub2.publish(url, page(title="T2", desc="D2"), conn2["id"])
    assert router2.bodies("POST", r"/wp/v2/posts/42")[-1] == {"meta": {"_yoast_wpseo_title": "T2", "_yoast_wpseo_metadesc": "D2"}}


def test_wordpress_blocks_need_confirmation(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    web.pages[url] = page()
    router, _ = wp_router(web, url, raw_content="<!-- wp:paragraph --><p>Old body text.</p><!-- /wp:paragraph -->")
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = wp_connect(pub)
    new = page(body="<p>New body text.</p>")
    with pytest.raises(PublishError) as err:
        pub.publish(url, new, conn["id"])
    assert err.value.status == 409 and err.value.extra["confirm"] == ["blocks"]
    assert not router.bodies("POST", r"/wp/v2/posts/42")
    entry = pub.publish(url, new, conn["id"], confirm=["blocks"])
    assert entry["fields"] == ["body"]


def test_wordpress_dropped_scripts_need_confirmation(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    old_body = "<p>Old body text.</p><script>track()</script>"
    web.pages[url] = page(body=old_body)
    router, item = wp_router(web, url, raw_content=old_body)
    item["content"]["rendered"] = old_body
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = wp_connect(pub)
    with pytest.raises(PublishError) as err:
        pub.publish(url, page(body="<p>New body text.</p>"), conn["id"])
    assert "embeds_removed" in err.value.extra["confirm"]


def test_wordpress_unknown_page_and_bad_credentials(tmp_path):
    web = Web()
    web.pages["https://acme.com/nope"] = page()
    router, _ = wp_router(web, "https://acme.com/about")
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = wp_connect(pub)
    with pytest.raises(PublishError) as err:
        pub.plan("https://acme.com/nope", page(h1="x"), conn["id"])
    assert "No WordPress post or page" in str(err.value)
    bad = Router()
    bad.on("GET", r"users/me", lambda r, m: httpx.Response(401, json={"code": "rest_not_logged_in", "message": "You are not currently logged in."}))
    pub_bad = make_publisher(tmp_path / "b", web, bad)
    with pytest.raises(PublishError) as err:
        wp_connect(pub_bad)
    assert "401" in str(err.value) and "abcd" not in str(err.value)
    assert pub_bad.store.connections() == []


def test_wordpress_refuses_private_site_address_and_plain_http(tmp_path):
    router = Router()
    wp = CONNECTORS["wordpress"]({"base_url": "https://10.0.0.5", "username": "a"}, {"app_password": "x"}, router.client(), allow_private=False)
    with pytest.raises(ConnectorError, match="cannot be used"):
        wp.check()
    plain = CONNECTORS["wordpress"]({"base_url": "http://acme.com", "username": "a"}, {"app_password": "x"}, router.client(), allow_private=False)
    with pytest.raises(ConnectorError, match="must use https"):
        plain.check()
    assert router.calls == []  # the password never left


# ---------------------------------------------------------------------------
# Webflow
# ---------------------------------------------------------------------------

WF = "https://api.webflow.com/v2"


def wf_router(web: Web):
    router = Router()
    pages = [
        {"id": "p1", "slug": "", "publishedPath": "/", "seo": {"title": "Home", "description": ""}},
        {"id": "p2", "slug": "about", "publishedPath": "/about", "seo": {"title": "Old title | Acme", "description": "Old description."}},
        {"id": "tpl", "slug": "detail_post", "collectionId": "col1"},
    ]
    item = {"id": "i9", "fieldData": {"name": "Old heading", "slug": "hello", "post-body": "<p>Old body text.</p>", "meta-description": "Old description."}}
    router.on("GET", re.escape(WF) + r"/sites/site1$", lambda r, m: {"displayName": "Acme"})
    router.on("GET", re.escape(WF) + r"/sites/site1/pages", lambda r, m: {"pages": pages, "pagination": {"total": 3}})
    router.on("GET", re.escape(WF) + r"/sites/site1/collections", lambda r, m: {"collections": [{"id": "col1", "slug": "blog"}]})
    router.on("GET", re.escape(WF) + r"/collections/col1/items", lambda r, m: {"items": [item], "pagination": {"total": 1}})
    router.on(
        "GET",
        re.escape(WF) + r"/collections/col1$",
        lambda r, m: {"fields": [{"slug": "name", "type": "PlainText"}, {"slug": "post-body", "type": "RichText"}, {"slug": "meta-description", "type": "PlainText"}]},
    )
    router.on("PUT", re.escape(WF) + r"/pages/p2", lambda r, m: {"id": "p2"})
    router.on("GET", re.escape(WF) + r"/sites/site1/custom_domains", lambda r, m: {"customDomains": [{"id": "d1", "url": "acme.com"}]})

    def site_publish(request, m):
        seo = router.bodies("PUT", r"/pages/p2")[-1]["seo"]
        web.pages["https://acme.com/about"] = page(title=seo.get("title", "Old title | Acme"), desc=seo.get("description", "Old description."))
        return httpx.Response(202, json={})

    def item_patch(request, m):
        item["fieldData"].update(json.loads(request.content)["fieldData"])
        return item

    def item_publish(request, m):
        fd = item["fieldData"]
        web.pages["https://acme.com/blog/hello"] = page(h1=fd["name"], desc=fd["meta-description"], body=fd["post-body"], region="w-richtext")
        return httpx.Response(202, json={"publishedItemIds": ["i9"]})

    router.on("POST", re.escape(WF) + r"/sites/site1/publish", site_publish)
    router.on("PATCH", re.escape(WF) + r"/collections/col1/items/i9", item_patch)
    router.on("POST", re.escape(WF) + r"/collections/col1/items/publish", item_publish)
    return router, item


def test_webflow_static_page_metadata_and_honest_limits(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    web.pages[url] = page()
    router, _ = wf_router(web)
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = pub.add_connection(kind="webflow", site="acme.com", label="", settings={"site_id": "site1"}, secret={"api_token": "wf-token"})
    assert router.calls[0].headers["authorization"] == "Bearer wf-token"
    new = page(title="New title", desc="New description.", h1="New heading")
    rows = {r["field"]: r for r in pub.plan(url, new, conn["id"])["plan"]["rows"]}
    assert rows["title"]["apply"] and rows["description"]["apply"]
    assert not rows["h1"]["apply"] and "Designer" in rows["h1"]["note"]

    saved = pub.publish(url, new, conn["id"])  # without publishing the site: saved, not live
    assert saved["status"] == "saved" and saved["verify"]["status"] == "not_live_yet"
    assert not router.bodies("POST", r"/sites/site1/publish")

    live = pub.publish(url, new, conn["id"], options={"publish_site": True})
    assert live["status"] == "live" and live["verify"]["status"] == "confirmed"
    assert router.bodies("POST", r"/sites/site1/publish")[-1] == {"publishToWebflowSubdomain": True, "customDomains": ["d1"]}
    pub.rollback(live["id"])
    assert router.bodies("PUT", r"/pages/p2")[-1] == {"seo": {"title": "Old title | Acme", "description": "Old description."}}


def test_webflow_cms_item_update_publish_and_rollback(tmp_path):
    web = Web()
    url = "https://acme.com/blog/hello"
    web.pages[url] = page(region="w-richtext")
    router, item = wf_router(web)
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = pub.add_connection(kind="webflow", site="acme.com", label="", settings={"site_id": "site1"}, secret={"api_token": "wf-token"})
    new = page(region="w-richtext", h1="New heading", desc="New description.", body="<p>New body text.</p>", title="New title")
    entry = pub.publish(url, new, conn["id"])
    assert router.bodies("PATCH", r"/items/i9")[-1] == {"fieldData": {"name": "New heading", "meta-description": "New description.", "post-body": "<p>New body text.</p>"}}
    assert router.bodies("POST", r"/items/publish")[-1] == {"itemIds": ["i9"]}
    assert "title" not in entry["fields"]  # no SEO title field in this collection, said plainly
    assert entry["verify"]["status"] == "confirmed"
    pub.rollback(entry["id"])
    assert item["fieldData"]["name"] == "Old heading" and item["fieldData"]["post-body"] == "<p>Old body text.</p>"


# ---------------------------------------------------------------------------
# Shopify
# ---------------------------------------------------------------------------


def shop_router(web: Web):
    router = Router()
    state = {
        "page": {"id": "gid://shopify/Page/1", "title": "Old heading", "handle": "about", "body": "<p>Old body text.</p>", "seoTitle": None, "seoDescription": {"value": "Old description."}},
        "article": {"id": "gid://shopify/Article/7", "title": "Old heading", "handle": "post", "body": "<p>Old body text.</p>", "summary": "", "seoTitle": {"value": "Old title | Acme"}, "seoDescription": None},
    }

    def gql(request, m):
        assert request.headers["X-Shopify-Access-Token"] == "shpat_test"
        data = json.loads(request.content)
        q, v = data["query"], data["variables"]
        if "shop { name }" in q:
            return {"data": {"shop": {"name": "Acme Store"}}}
        if "pages(first" in q:
            return {"data": {"pages": {"nodes": [state["page"]] if v["q"] == "handle:about" else []}}}
        if "articles(first" in q:
            assert "blogs" not in q  # Blog.articles takes no query argument
            other_blog = {**state["article"], "id": "gid://shopify/Article/99", "blog": {"handle": "other"}}
            arts = [other_blog, {**state["article"], "blog": {"handle": "news"}}] if v["a"] == "handle:post" else []
            return {"data": {"articles": {"nodes": arts}}}
        if "pageUpdate" in q or "articleUpdate" in q:
            kind = "page" if "pageUpdate" in q else "article"
            fields = v["page" if kind == "page" else "article"]
            node = state[kind]
            for key in ("title", "body"):
                if key in fields:
                    node[key] = fields[key]
            for mf in fields.get("metafields") or []:
                node["seoTitle" if mf["key"] == "title_tag" else "seoDescription"] = {"value": mf["value"]}
            return {"data": {f"{kind}Update": {kind: {"id": node["id"]}, "userErrors": []}}}
        if "metafieldsDelete" in q:
            for mf in v["m"]:
                node = state["page" if "Page" in mf["ownerId"] else "article"]
                node["seoTitle" if mf["key"] == "title_tag" else "seoDescription"] = None
            return {"data": {"metafieldsDelete": {"deletedMetafields": [], "userErrors": []}}}
        return {"errors": [{"message": "unknown query"}]}

    router.on("POST", r"acme-store\.myshopify\.com/admin/api/[\d-]+/graphql\.json", gql)
    return router, state


def test_shopify_page_publish_and_rollback_removes_new_seo_title(tmp_path):
    web = Web()
    url = "https://acme.com/pages/about"
    web.pages[url] = page(region="rte")
    router, state = shop_router(web)
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = pub.add_connection(kind="shopify", site="acme.com", label="", settings={"shop": "acme-store.myshopify.com"}, secret={"access_token": "shpat_test"})
    assert conn["label"] == "Acme Store"
    new = page(region="rte", title="New title", desc="New description.", h1="New heading", body="<p>New body text.</p>")
    entry = pub.publish(url, new, conn["id"])
    assert set(entry["fields"]) == {"title", "description", "h1", "body"}
    assert state["page"]["title"] == "New heading" and state["page"]["body"] == "<p>New body text.</p>"
    assert state["page"]["seoTitle"] == {"value": "New title"}
    pub.rollback(entry["id"])
    assert state["page"]["title"] == "Old heading" and state["page"]["body"] == "<p>Old body text.</p>"
    assert state["page"]["seoTitle"] is None  # did not exist before, removed again
    assert state["page"]["seoDescription"] == {"value": "Old description."}


def test_shopify_article_and_unsupported_paths(tmp_path):
    web = Web()
    url = "https://acme.com/blogs/news/post"
    web.pages[url] = page(region="rte")
    router, state = shop_router(web)
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = pub.add_connection(kind="shopify", site="acme.com", label="", settings={"shop": "acme-store.myshopify.com"}, secret={"access_token": "shpat_test"})
    entry = pub.publish(url, page(region="rte", h1="Article heading"), conn["id"])
    assert state["article"]["title"] == "Article heading"
    assert entry["remote"]["id"] == "gid://shopify/Article/7"  # the article in blog "news", not the one in "other"
    web.pages["https://acme.com/products/shoe"] = page()
    with pytest.raises(PublishError) as err:
        pub.plan("https://acme.com/products/shoe", page(h1="x"), conn["id"])
    assert "Only Shopify pages" in str(err.value)
    with pytest.raises(PublishError):
        pub.add_connection(kind="shopify", site="acme.com", label="", settings={"shop": "evil.com"}, secret={"access_token": "x"})


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

GH = "https://api.github.com/repos/acme/site"


def gh_router(files: dict[str, str]):
    router = Router()
    state = {"merged": False, "state": "open", "refs": {}}

    def contents(request, m):
        path = m.group(1)
        if path not in files:
            return httpx.Response(404, json={"message": "Not Found"})
        return {"type": "file", "path": path, "sha": "sha-" + path, "content": base64.b64encode(files[path].encode()).decode()}

    router.on("GET", re.escape(GH) + r"$", lambda r, m: {"full_name": "acme/site", "permissions": {"push": True}})
    router.on("GET", re.escape(GH) + r"/contents/([^?]+)", contents)
    router.on("GET", re.escape(GH) + r"/git/ref/heads/main", lambda r, m: {"object": {"sha": "base-sha"}})
    router.on("POST", re.escape(GH) + r"/git/refs", lambda r, m: httpx.Response(201, json={"ref": json.loads(r.content)["ref"]}))
    router.on("PUT", re.escape(GH) + r"/contents/(.+)", lambda r, m: httpx.Response(201, json={"content": {"sha": "new"}}))
    router.on("POST", re.escape(GH) + r"/pulls$", lambda r, m: httpx.Response(201, json={"number": 7, "html_url": "https://github.com/acme/site/pull/7"}))
    router.on("GET", re.escape(GH) + r"/pulls/7", lambda r, m: {"merged": state["merged"], "state": state["state"]})
    router.on("PATCH", re.escape(GH) + r"/pulls/7", lambda r, m: {"state": "closed"})
    router.on("DELETE", re.escape(GH) + r"/git/refs/heads/(.+)", lambda r, m: httpx.Response(204))
    return router, state


def gh_connect(pub, **settings):
    return pub.add_connection(kind="github", site="acme.com", label="", settings={"repo": "acme/site", **settings}, secret={"token": "ghp_test"})


def test_github_whole_file_pull_request_verify_after_merge_and_rollback(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    old = page()
    web.pages[url] = old
    router, state = gh_router({"public/about/index.html": old})
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = gh_connect(pub, root="public")
    new = page(h1="New heading", body="<p>New body text.</p>")
    entry = pub.publish(url, new, conn["id"])
    assert entry["status"] == "pull_request" and entry["verify"]["status"] == "not_live_yet"
    assert entry["remote"]["pr_url"] == "https://github.com/acme/site/pull/7"
    branch = router.bodies("POST", r"/git/refs")[-1]
    assert branch["sha"] == "base-sha" and branch["ref"].startswith("refs/heads/ranksim/about-")
    put = router.bodies("PUT", r"/contents/public/about/index.html")[-1]
    assert base64.b64decode(put["content"]).decode() == new and put["sha"] == "sha-public/about/index.html"
    pr = router.bodies("POST", r"/pulls$")[-1]
    assert pr["base"] == "main" and pr["head"] == branch["ref"].removeprefix("refs/heads/")
    assert router.calls[0].headers["authorization"] == "Bearer ghp_test"

    assert pub.verify_live(entry["id"])["verify"]["status"] == "not_live_yet"
    state["merged"] = True
    web.pages[url] = new
    assert pub.verify_live(entry["id"])["verify"]["status"] == "confirmed"

    rolled = pub.rollback(entry["id"])  # merged: a revert pull request with the previous file
    assert rolled["rollback"]["status"] == "pull_request"
    put = router.bodies("PUT", r"/contents/public/about/index.html")[-1]
    assert base64.b64decode(put["content"]).decode() == old


def test_github_rollback_closes_open_pull_request(tmp_path):
    web = Web()
    url = "https://acme.com/"
    web.pages[url] = page()
    router, state = gh_router({"index.html": page()})
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    entry = pub.publish(url, page(h1="New"), gh_connect(pub)["id"])
    rolled = pub.rollback(entry["id"])
    assert rolled["rollback"]["status"] == "closed"
    assert router.bodies("PATCH", r"/pulls/7")[-1] == {"state": "closed"}
    assert any(r.method == "DELETE" for r in router.calls)


def test_github_template_file_is_patched_field_by_field(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    web.pages[url] = page()
    template = (
        '---\nlayout: base\n---\n<html><head><title>Old title | Acme</title>\n<meta name="description" content="Old description.">'
        "</head><body><h1>Old heading</h1>{{ content }}</body></html>"
    )
    router, _ = gh_router({"about.html": template})
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = gh_connect(pub)
    new = page(title="New & better", desc='Say "hi".', h1="New heading", body="<p>Changed</p>")
    plan = pub.plan(url, new, conn["id"])["plan"]
    rows = {r["field"]: r for r in plan["rows"]}
    assert rows["title"]["apply"] and rows["description"]["apply"] and rows["h1"]["apply"]
    assert not rows["body"]["apply"] and "template" in rows["body"]["note"]
    pub.publish(url, new, conn["id"])
    text = base64.b64decode(router.bodies("PUT", r"/contents/about.html")[-1]["content"]).decode()
    assert "<title>New &amp; better</title>" in text and 'content="Say &quot;hi&quot;."' in text
    assert "<h1>New heading</h1>" in text and "{{ content }}" in text and text.startswith("---\nlayout: base")


def test_github_path_mapping_and_unsafe_paths():
    gh = CONNECTORS["github"]({"repo": "acme/site", "paths": "/pricing=src/pages/pricing.html"}, {"token": "t"}, Router().client())
    assert gh.candidates("https://acme.com/pricing") == ["src/pages/pricing.html"]
    assert gh.candidates("https://acme.com/") == ["index.html"]
    assert gh.candidates("https://acme.com/a/b") == ["a/b.html", "a/b/index.html"]
    bad = CONNECTORS["github"]({"repo": "acme/site", "paths": "/x=../../etc/passwd"}, {"token": "t"}, Router().client())
    with pytest.raises(ConnectorError):
        bad.candidates("https://acme.com/x")


# ---------------------------------------------------------------------------
# Catalog and the web routes
# ---------------------------------------------------------------------------


def test_catalog_is_honest_and_has_no_dashes():
    kinds = {p["kind"]: p for p in catalog()}
    assert set(kinds) == {"wordpress", "webflow", "shopify", "github", "webhook"}
    assert any("Designer" in limit for limit in kinds["webflow"]["limits"])
    text = json.dumps(catalog(), ensure_ascii=False)
    assert "—" not in text and "–" not in text


class FakeProject:
    def __init__(self, root: Path) -> None:
        self.slug = "acme"
        self.site = "acme.com"
        self.dir = root / "acme"


class FakeUI:
    def __init__(self, root: Path, web: Web, router: Router) -> None:
        self.public_demo = False
        self.csrf = "csrf-token"
        self.preview_fetch = web
        self.settings = types.SimpleNamespace(crawl_allow_private=True)
        self.publish_overrides = {"http_factory": router.client, "sleep": lambda s: None, "verify_waits": (0.0,), "proof_fetch": web}
        self._project = FakeProject(root)

    def project(self, slug):
        if slug != "acme":
            raise LookupError(slug)
        return self._project


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from aeo.ranksim.plugins import publish as plugin

    web, router = Web(), Router()
    ui = FakeUI(tmp_path, web, router)
    app = FastAPI()
    plugin.register(app, ui)
    return TestClient(app), web, router, ui


H = {"x-ranksim-csrf": "csrf-token"}


def test_routes_full_flow_and_no_secret_leaves(app_env):
    client, web, router, ui = app_env
    url = "https://acme.com/about"
    web.pages[url] = page()
    hook_url = "https://hooks.acme.test/rs"

    def hook(request, m):
        data = json.loads(request.content)
        if data["event"] != "ranksim.ping":
            web.pages[url] = data["html"]
        return httpx.Response(200)

    router.on("POST", re.escape(hook_url), hook)
    assert client.get("/api/v1/publish/platforms").status_code == 200

    status = client.get("/api/v1/projects/acme/publish/status", params={"url": url}).json()
    assert status["owned"] is False and status["connections"] == []

    # writes need the CSRF header
    assert client.post("/api/v1/projects/acme/publish/ownership", json={"site": "acme.com"}).status_code == 403
    record = client.post("/api/v1/projects/acme/publish/ownership", json={"site": "acme.com"}, headers=H).json()
    web.pages["https://acme.com/"] = page(extra=f'<meta name="ranksim-verification" content="{record["token"]}">')
    assert client.post("/api/v1/projects/acme/publish/ownership/verify", json={"site": "acme.com"}, headers=H).json()["verified"]

    secret = "very-secret-signing-key-123"
    made = client.post(
        "/api/v1/projects/acme/publish/connections",
        json={"kind": "webhook", "site": "acme.com", "settings": {"url": hook_url}, "secret": {"signing_secret": secret}},
        headers=H,
    )
    assert made.status_code == 200, made.text
    conn_id = made.json()["id"]

    new = page(h1="Route heading")
    plan = client.post("/api/v1/projects/acme/publish/plan", json={"url": url, "html": new, "connection_id": conn_id}, headers=H).json()
    assert plan["changes"][0]["field"] == "h1" and plan["plan"]["rows"][0]["apply"]
    assert client.post("/api/v1/projects/acme/publish", json={"url": url, "html": new, "connection_id": conn_id}).status_code == 403
    done = client.post("/api/v1/projects/acme/publish", json={"url": url, "html": new, "connection_id": conn_id}, headers=H)
    assert done.status_code == 200 and done.json()["verify"]["status"] == "confirmed"
    entry_id = done.json()["id"]

    history = client.get("/api/v1/projects/acme/publish/history").json()["history"]
    assert history[0]["id"] == entry_id and history[0]["can_rollback"]
    assert client.post(f"/api/v1/projects/acme/publish/history/{entry_id}/verify", headers=H).json()["verify"]["status"] == "confirmed"
    assert client.post(f"/api/v1/projects/acme/publish/history/{entry_id}/rollback").status_code == 403
    assert client.post(f"/api/v1/projects/acme/publish/history/{entry_id}/rollback", headers=H).status_code == 200

    every = [
        client.get("/api/v1/projects/acme/publish/status", params={"url": url}).text,
        client.get("/api/v1/projects/acme/publish/connections").text,
        client.get("/api/v1/projects/acme/publish/history").text,
        made.text,
        done.text,
    ]
    assert not any(secret in body for body in every)
    assert not any("sealed" in body for body in every)

    assert client.delete(f"/api/v1/projects/acme/publish/connections/{conn_id}").status_code == 403
    assert client.delete(f"/api/v1/projects/acme/publish/connections/{conn_id}", headers=H).json() == {"ok": True}
    assert client.get("/api/v1/projects/nope/publish/history").status_code == 404


def test_routes_refuse_publish_when_not_owned_and_in_public_demo(app_env):
    client, web, router, ui = app_env
    url = "https://acme.com/about"
    web.pages[url] = page()
    router.on("POST", r"hooks", lambda r, m: httpx.Response(200))
    conn = client.post(
        "/api/v1/projects/acme/publish/connections",
        json={"kind": "webhook", "site": "acme.com", "settings": {"url": "https://hooks.acme.test/x"}, "secret": {"signing_secret": "k" * 20}},
        headers=H,
    ).json()
    refused = client.post("/api/v1/projects/acme/publish", json={"url": url, "html": page(h1="x"), "connection_id": conn["id"]}, headers=H)
    assert refused.status_code == 403 and refused.json()["code"] == "not_owned"
    ui.public_demo = True
    assert client.get("/api/v1/projects/acme/publish/history").status_code == 403


def test_after_pull_rechecks_and_never_raises(tmp_path):
    from aeo.ranksim.plugins import publish as plugin

    web, router = Web(), Router()
    ui = FakeUI(tmp_path, web, router)
    project = ui.project("acme")
    pub = plugin.publisher_for(ui, project)
    owned(pub, web)
    record = pub.store.ownership("acme.com")
    record["checked_at"] -= own.STALE_AFTER + 10
    pub.store.put_ownership(record)
    web.pages["https://acme.com/"] = page()  # proof removed
    plugin.after_pull(project=project, report={}, ui=ui)
    assert not pub.store.ownership("acme.com")["verified"]
    plugin.after_pull(project=object(), report={}, ui=ui)  # broken input is logged, not raised


# ---------------------------------------------------------------------------
# Hardening found in review
# ---------------------------------------------------------------------------


def test_pinning_dials_the_checked_address(monkeypatch):
    from aeo.ranksim.publishing.connectors import base

    answers = iter([[(2, 1, 6, "", ("93.184.216.34", 443))]])
    monkeypatch.setattr(base.socket, "getaddrinfo", lambda *a, **k: next(answers))
    router = Router()
    router.on("POST", r"93\.184\.216\.34", lambda r, m: httpx.Response(200))
    hook = CONNECTORS["webhook"]({"url": "https://hooks.acme.test/x"}, {"signing_secret": "k" * 20}, router.client(), allow_private=False)
    monkeypatch.setattr(base, "assert_safe_url", lambda url, **k: url)  # the first check passed
    hook.check()
    request = router.calls[0]
    assert request.url.host == "93.184.216.34" and request.headers["host"] == "hooks.acme.test"
    assert request.extensions["sni_hostname"] == "hooks.acme.test"


def test_pinning_refuses_a_name_that_now_points_inside(monkeypatch):
    from aeo.ranksim.publishing.connectors import base

    monkeypatch.setattr(base, "assert_safe_url", lambda url, **k: url)
    monkeypatch.setattr(base.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 443))])
    router = Router()
    hook = CONNECTORS["webhook"]({"url": "https://rebind.acme.test/x"}, {"signing_secret": "k" * 20}, router.client(), allow_private=False)
    with pytest.raises(ConnectorError, match="private address"):
        hook.check()
    assert router.calls == []


def test_proof_after_redirect_to_another_host_does_not_count(tmp_path):
    pub = make_publisher(tmp_path, Web(), Router())
    token = pub.start_ownership("acme.com")["token"]
    elsewhere = page(extra=f'<meta name="ranksim-verification" content="{token}">')

    def fetch(url):
        return elsewhere, "", "https://free-pages.example/acme"

    pub.proof_fetch = fetch
    result = pub.verify_ownership("acme.com")
    assert not result["verified"]
    assert "another site" in result["last_check"]["detail"]
    pub.proof_fetch = lambda url: (elsewhere, "", "https://www.acme.com/")  # apex to www is fine
    assert pub.verify_ownership("acme.com", ["meta"])["verified"]


def test_meta_tag_in_body_does_not_count(tmp_path):
    web = Web()
    pub = make_publisher(tmp_path, web, Router())
    token = pub.start_ownership("acme.com")["token"]
    web.pages["https://acme.com/"] = page(body=f'<p>comment</p><meta name="ranksim-verification" content="{token}">')
    assert not pub.verify_ownership("acme.com", ["meta"])["verified"]


def test_stale_recheck_tries_other_methods_before_revoking(tmp_path):
    web, clock, router = Web(), Clock(), Router()
    pub = make_publisher(tmp_path, web, router, clock)
    token = owned(pub, web)["token"]
    web.pages["https://acme.com/.well-known/ranksim-verification.txt"] = token
    web.pages["https://acme.com/"] = page()  # meta removed, file still there
    clock.t += own.STALE_AFTER + 1
    record = pub.require_ownership("https://acme.com/x")
    assert record["verified"] and record["method"] == "file"


def test_verified_parent_domain_wins_over_unverified_subdomain_record(tmp_path):
    router = Router()
    pub = make_publisher(tmp_path, Web(), router)
    token = pub.start_ownership("acme.com")["token"]
    router.on("GET", r"cloudflare-dns", lambda r, m: dns_answer(f'"ranksim-verification={token}"'))
    pub.verify_ownership("acme.com", ["dns"])
    pub.start_ownership("blog.acme.com")  # started, never verified
    assert pub.ownership_for("https://blog.acme.com/post")["domain"] == "acme.com"
    assert pub.require_ownership("https://blog.acme.com/post")["verified"]


def test_rollback_needs_ownership_still_proven(tmp_path):
    web, router, clock = Web(), Router(), Clock()
    pub = make_publisher(tmp_path, web, router, clock)
    owned(pub, web)
    url = "https://acme.com/about"
    web.pages[url] = page()
    conn = add_webhook(pub, router)
    entry = pub.publish(url, page(h1="New"), conn["id"])
    web.pages["https://acme.com/"] = page()  # proof removed
    clock.t += own.STALE_AFTER + 1
    hooks = lambda: [c for c in router.calls if "hooks" in str(c.url)]  # noqa: E731
    sent = len(hooks())
    with pytest.raises(PublishError) as err:
        pub.rollback(entry["id"])
    assert err.value.status == 403 and len(hooks()) == sent


def test_rollback_order_and_reconnected_connection(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    url = "https://acme.com/about"
    web.pages[url] = page()
    conn = add_webhook(pub, router)
    first = pub.publish(url, page(h1="One"), conn["id"])
    second = pub.publish(url, page(h1="Two"), conn["id"])
    assert first["id"] < second["id"]
    with pytest.raises(PublishError) as err:
        pub.rollback(first["id"])
    assert err.value.status == 409 and err.value.extra["newer"] == second["id"]
    pub.remove_connection(conn["id"])
    with pytest.raises(PublishError):
        pub.rollback(second["id"])
    again = add_webhook(pub, router)
    assert pub.rollback(second["id"], again["id"])["rolled_back_at"]
    assert pub.rollback(first["id"], again["id"])["rolled_back_at"]


def test_failed_apply_is_recorded_with_previous_values(tmp_path):
    web, router = Web(), Router()
    url = "https://acme.com/about"
    web.pages[url] = page()
    wp, _ = wp_router(web, url, namespaces=("wp/v2", "rankmath/v1"))
    wp.routes = [r for r in wp.routes if "updateMeta" not in r[1].pattern]
    wp.on("POST", r"updateMeta", lambda r, m: httpx.Response(500, json={"message": "boom"}))
    pub = make_publisher(tmp_path, web, wp)
    owned(pub, web)
    conn = wp_connect(pub)
    with pytest.raises(PublishError) as err:
        pub.publish(url, page(h1="New heading", title="New SEO"), conn["id"])
    assert err.value.status == 502 and "boom" in str(err.value)
    stored = pub.store.history_entry(err.value.extra["history_id"])
    assert stored["status"] == "failed" and stored["previous"]["title"] == "Old heading"  # the post title did change
    assert pub.history()[0]["can_rollback"]


def test_unexpected_platform_answer_is_a_clean_error(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    web.pages[url] = page()
    router, _ = wp_router(web, url)
    router.routes.insert(0, ("GET", re.compile(r"/wp/v2/posts\?"), lambda r, m: [5, "junk"]))
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = wp_connect(pub)
    with pytest.raises(PublishError):
        pub.plan(url, page(h1="x", body="<p>new</p>"), conn["id"])


def test_public_history_never_carries_page_payload(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    url = "https://acme.com/about"
    web.pages[url] = page()
    entry = pub.publish(url, page(h1="New"), add_webhook(pub, router)["id"])
    for view in (entry, pub.history()[0]):
        assert not {"payload", "previous", "expected"} & set(view)


def test_webhook_whole_page_warns_when_head_scripts_are_lost(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    url = "https://acme.com/about"
    web.pages[url] = page(extra="<script src=/a.js></script>")
    conn = add_webhook(pub, router)
    with pytest.raises(PublishError) as err:
        pub.publish(url, page(h1="New"), conn["id"])
    assert err.value.extra["confirm"] == ["embeds_removed"]


def test_live_check_ignores_entity_and_quote_differences():
    from aeo.ranksim.publishing.service import comparable

    assert comparable("Tom\u2019s  \u201cbest\u201d") == comparable("Tom's \"best\"") == comparable("Tom&#39;s &quot;best&quot;")


def test_title_suffix_needs_a_word_boundary():
    from aeo.ranksim.publishing.connectors.base import strip_suffix_title

    assert strip_suffix_title("Old | Acme", "Old", "New | Acme") == "New"
    assert strip_suffix_title("Olden times", "Old", "New times") is None


def test_routes_answer_400_for_a_bad_site(app_env):
    client, web, router, ui = app_env
    assert client.post("/api/v1/projects/acme/publish/ownership/reset", json={"site": "not a site"}, headers=H).status_code == 400
    assert client.get("/api/v1/projects/acme/publish/status", params={"url": "https://localhost/x"}).status_code == 400


# ---------------------------------------------------------------------------
# Encryption not available
# ---------------------------------------------------------------------------


def test_missing_secretbox_answers_503_and_saves_nothing(tmp_path, monkeypatch):
    from aeo.ranksim.publishing.store import encryption_ready

    monkeypatch.setitem(sys.modules, "aeo.ranksim.secretbox", None)  # import fails
    import aeo.ranksim as pkg

    monkeypatch.delattr(pkg, "secretbox", raising=False)
    assert encryption_ready() is False
    router = Router()
    router.on("POST", r"hooks", lambda r, m: httpx.Response(200))
    pub = make_publisher(tmp_path, Web(), router)
    with pytest.raises(PublishError) as err:
        pub.add_connection(kind="webhook", site="acme.com", label="", settings={"url": "https://hooks.acme.test/x"}, secret={"signing_secret": "k" * 20})
    assert err.value.status == 503 and "encryption" in str(err.value)
    assert pub.store.connections() == []


def test_secretbox_without_key_answers_503(tmp_path, box):
    from aeo.ranksim.publishing.store import encryption_ready

    box.configured = lambda: False

    def refuse(plaintext, *, purpose):
        raise RuntimeError("RANKSIM_SECRET is not set")

    box.seal = refuse
    assert encryption_ready() is False
    router = Router()
    router.on("POST", r"hooks", lambda r, m: httpx.Response(200))
    pub = make_publisher(tmp_path, Web(), router)
    with pytest.raises(PublishError) as err:
        pub.add_connection(kind="webhook", site="acme.com", label="", settings={"url": "https://hooks.acme.test/x"}, secret={"signing_secret": "k" * 20})
    assert err.value.status == 503 and "k" * 20 not in str(err.value)


def test_status_route_reports_encryption_state(app_env, box):
    client, web, router, ui = app_env
    box.configured = lambda: True
    assert client.get("/api/v1/projects/acme/publish/status", params={"url": "https://acme.com/"}).json()["encryption_ready"] is True
    box.configured = lambda: False
    assert client.get("/api/v1/projects/acme/publish/status", params={"url": "https://acme.com/"}).json()["encryption_ready"] is False


def test_store_lives_in_the_plugin_data_dir(tmp_path):
    store = PublishStore.for_project(FakeProject(tmp_path))
    assert store.folder == tmp_path / "acme" / "plugins" / "publish" and store.folder.is_dir()


def test_publish_and_rollback_reach_the_client_change_log(app_env, monkeypatch):
    from aeo.ranksim.plugins import share

    logged = []
    def fake_log(project, kind, summary, **kw):  # keyed like the real one: a repeat adds nothing
        if all(row[2] != kw["key"] for row in logged):
            logged.append((kind, summary, kw["key"], kw.get("public", True)))

    monkeypatch.setattr(share, "log_change", fake_log)
    client, web, router, ui = app_env
    url = "https://acme.com/about"
    web.pages[url] = page()
    router.on("POST", r"hooks", lambda r, m: httpx.Response(200))
    record = client.post("/api/v1/projects/acme/publish/ownership", json={"site": "acme.com"}, headers=H).json()
    web.pages["https://acme.com/"] = page(extra=f'<meta name="ranksim-verification" content="{record["token"]}">')
    client.post("/api/v1/projects/acme/publish/ownership/verify", json={"site": "acme.com"}, headers=H)
    conn = client.post("/api/v1/projects/acme/publish/connections", json={"kind": "webhook", "site": "acme.com", "settings": {"url": "https://hooks.acme.test/x"}, "secret": {"signing_secret": "k" * 20}}, headers=H).json()
    entry = client.post("/api/v1/projects/acme/publish", json={"url": url, "html": page(h1="New"), "connection_id": conn["id"]}, headers=H).json()
    client.post(f"/api/v1/projects/acme/publish/history/{entry['id']}/rollback", headers=H)
    assert logged[0] == ("publish", "Published changes to /about: main heading.", f"publish:{entry['id']}", True)
    assert logged[1] == ("publish", "Rolled back the published change to /about.", f"publish-rollback:{entry['id']}", True)
    assert len(logged) == 2


def test_real_secretbox_round_trip_through_the_store(tmp_path, monkeypatch):
    import importlib.util

    import aeo.ranksim as pkg

    path = Path(pkg.__file__).parent / "secretbox.py"
    spec = importlib.util.spec_from_file_location("aeo.ranksim.secretbox", path)
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    monkeypatch.setitem(sys.modules, "aeo.ranksim.secretbox", real)
    monkeypatch.setattr(pkg, "secretbox", real, raising=False)
    monkeypatch.setenv("RANKSIM_SECRET", "test-only-secret-0123456789abcdefghijkl")
    store = PublishStore(tmp_path / "p", scope="acme")
    c = store.add_connection(kind="webhook", domain="acme.com", label="", settings={}, secret={"signing_secret": "real-secret-value-123"})
    assert c["sealed"].startswith("v1.") and "real-secret-value-123" not in (tmp_path / "p" / "state.json").read_text()
    assert store.secret(c) == {"signing_secret": "real-secret-value-123"}
    with pytest.raises(StoreError):
        PublishStore(tmp_path / "p", scope="other").secret(c)
    monkeypatch.setenv("RANKSIM_SECRET", "a-different-key-0123456789abcdefghijklmn")
    with pytest.raises(StoreError):
        store.secret(c)  # a changed server key: reconnect, never a crash or a leak


def test_history_index_files_stay_small(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    url = "https://acme.com/about"
    web.pages[url] = page(body="<p>" + "word " * 5000 + "</p>")
    entry = pub.publish(url, page(h1="New", body="<p>" + "word " * 5000 + "</p>"), add_webhook(pub, router)["id"])
    light = json.loads((pub.store.history_dir / f"{entry['id']}.json").read_text())
    assert "payload" not in light and "previous" not in light
    assert pub.store.history_entry(entry["id"])["previous"]["html"].count("word") == 5000
    assert [e["id"] for e in pub.store.history()] == [entry["id"]]


def test_no_network_call_when_encryption_is_not_ready(tmp_path, box):
    box.configured = lambda: False
    router = Router()
    pub = make_publisher(tmp_path, Web(), router)
    with pytest.raises(PublishError) as err:
        pub.add_connection(kind="webhook", site="acme.com", label="", settings={"url": "https://hooks.acme.test/x"}, secret={"signing_secret": "k" * 20})
    assert err.value.status == 503 and router.calls == []


def test_after_pull_saves_when_a_proof_was_rechecked(tmp_path, monkeypatch):
    from aeo.ranksim import plugins
    from aeo.ranksim.plugins import publish as plugin

    saves = []
    monkeypatch.setattr(plugins, "save", lambda ui: saves.append(ui) or True)
    web, router = Web(), Router()
    ui = FakeUI(tmp_path, web, router)
    project = ui.project("acme")
    pub = plugin.publisher_for(ui, project)
    owned(pub, web)
    plugin.after_pull(project=project, report={}, ui=ui)
    assert saves == []  # nothing was due
    record = pub.store.ownership("acme.com")
    record["checked_at"] -= own.STALE_AFTER + 10
    pub.store.put_ownership(record)
    plugin.after_pull(project=project, report={}, ui=ui)
    assert saves == [ui]


# ---------------------------------------------------------------------------
# Guided WordPress connect
# ---------------------------------------------------------------------------


def wp_inspect(router, url="https://acme.com/about", **secret):
    wp = CONNECTORS["wordpress"]({"base_url": "https://acme.com", "username": "james"}, {"app_password": "abcd efgh ijkl", **secret}, router.client(), allow_private=True)
    return {s["key"]: s for s in wp.inspect(url)}


def test_guided_wordpress_check_reports_each_step(tmp_path):
    web = Web()
    router, _ = wp_router(web, "https://acme.com/about", namespaces=("wp/v2", "rankmath/v1"))
    router.routes.insert(0, ("GET", re.compile(r"/wp/v2/users/me"), lambda r, m: {"name": "James", "capabilities": {"edit_pages": True, "edit_posts": True}}))
    steps = wp_inspect(router)
    assert list(steps) == ["site", "login", "rights", "seo", "page"]
    assert all(s["ok"] for s in steps.values())
    assert "Rank Math" in steps["seo"]["detail"] and "Signed in as James" in steps["login"]["detail"]
    assert steps["page"]["detail"] == 'Found the post "Old heading".'


def test_guided_wordpress_check_explains_failures():
    router = Router()
    assert wp_inspect(router)["site"]["ok"] is False  # no WordPress API answered
    router.on("GET", r"/wp-json/$", lambda r, m: {"name": "Acme", "namespaces": ["wp/v2"]})
    router.on("GET", r"users/me", lambda r, m: httpx.Response(401, json={"message": "nope"}))
    steps = wp_inspect(router)
    assert steps["site"]["ok"] and not steps["login"]["ok"] and "not your normal password" in steps["login"]["detail"]
    assert "abcd" not in json.dumps(steps)
    router.routes = [r for r in router.routes if "users" not in r[1].pattern]
    router.on("GET", r"users/me", lambda r, m: {"name": "Sam", "capabilities": {"edit_posts": True}})
    router.on("GET", r"/wp/v2/(pages|posts)\?", lambda r, m: [])
    steps = wp_inspect(router)
    assert not steps["rights"]["ok"] and "cannot edit pages" in steps["rights"]["detail"]
    assert "No Yoast or Rank Math" in steps["seo"]["detail"]
    assert not steps["page"]["ok"]


def test_check_route_saves_nothing_and_needs_csrf(app_env):
    client, web, router, ui = app_env
    router.on("GET", r"/wp-json/$", lambda r, m: {"namespaces": ["wp/v2", "yoast/v1"]})
    router.on("GET", r"users/me", lambda r, m: {"name": "James"})
    body = {"kind": "wordpress", "settings": {"base_url": "https://acme.com", "username": "james"}, "secret": {"app_password": "pw-123-secret"}}
    assert client.post("/api/v1/projects/acme/publish/connections/check", json=body).status_code == 403
    answer = client.post("/api/v1/projects/acme/publish/connections/check", json=body, headers=H)
    assert answer.status_code == 200 and "pw-123-secret" not in answer.text
    steps = {s["key"]: s for s in answer.json()["steps"]}
    assert steps["seo"]["detail"].startswith("Yoast found")
    assert client.get("/api/v1/projects/acme/publish/connections").json()["connections"] == []


# ---------------------------------------------------------------------------
# Publish from Issues
# ---------------------------------------------------------------------------


def test_clean_fixes_keeps_only_issue_shaped_rows():
    from aeo.ranksim.publishing.service import clean_fixes

    rows = clean_fixes([{"id": "title-long-about", "title": "Title  too long"}, {"id": "Bad ID"}, "x", {"id": "title-long-about"}, {"id": "../x"}])
    assert rows == [{"id": "title-long-about", "title": "Title too long"}]


def _owned_routes(client, web):
    record = client.post("/api/v1/projects/acme/publish/ownership", json={"site": "acme.com"}, headers=H).json()
    web.pages["https://acme.com/"] = page(extra=f'<meta name="ranksim-verification" content="{record["token"]}">')
    client.post("/api/v1/projects/acme/publish/ownership/verify", json={"site": "acme.com"}, headers=H)


def test_live_publish_marks_fixed_issues_done_and_rollback_reopens(app_env):
    from aeo.ranksim.plugins import share

    client, web, router, ui = app_env
    project = ui.project("acme")
    share.set_issue_plan(project, "meta-missing-about", "planned", plan="Write a description", title="No meta description")
    url = "https://acme.com/about"
    web.pages[url] = page()

    def hook(request, m):
        data = json.loads(request.content)
        if data["event"] != "ranksim.ping":
            web.pages[url] = data["html"]
        return httpx.Response(200)

    router.on("POST", r"hooks", hook)
    _owned_routes(client, web)
    conn = client.post("/api/v1/projects/acme/publish/connections", json={"kind": "webhook", "site": "acme.com", "settings": {"url": "https://hooks.acme.test/x"}, "secret": {"signing_secret": "k" * 20}}, headers=H).json()
    fixes = [{"id": "meta-missing-about", "title": "No meta description"}, {"id": "h1-weak-about", "title": "Weak heading"}, {"id": "NOT VALID"}]
    entry = client.post("/api/v1/projects/acme/publish", json={"url": url, "html": page(h1="New", desc="Better."), "connection_id": conn["id"], "fixes": fixes}, headers=H).json()
    assert [f["id"] for f in entry["fixes"]] == ["meta-missing-about", "h1-weak-about"]
    assert entry["fixes_marked"] == "done"
    plans = share.issue_plans(project)
    assert plans["meta-missing-about"]["status"] == "done" and plans["meta-missing-about"]["plan"] == "Write a description"
    assert plans["h1-weak-about"]["status"] == "done"
    assert any(row["summary"] == "Fixed: Weak heading" for row in share.changelog(project))
    share.set_issue_plan(project, "h1-weak-about", "doing", title="Weak heading")  # the owner changed it since
    client.post(f"/api/v1/projects/acme/publish/history/{entry['id']}/rollback", headers=H)
    plans = share.issue_plans(project)
    # back to its status before the publish (Planned), and the issue the owner moved since is left alone
    assert plans["meta-missing-about"]["status"] == "planned" and plans["h1-weak-about"]["status"] == "doing"
    assert any(row["summary"] == "Reopened: No meta description" for row in share.changelog(project))
    assert not any(row["summary"] == "Reopened: Weak heading" for row in share.changelog(project))


def test_pull_request_marks_issues_only_once_merged_and_live(app_env):
    from aeo.ranksim.plugins import share

    client, web, router, ui = app_env
    project = ui.project("acme")
    url = "https://acme.com/about"
    old = page()
    web.pages[url] = old
    gh, state = gh_router({"about.html": old})
    router.routes.extend(gh.routes)
    _owned_routes(client, web)
    conn = client.post("/api/v1/projects/acme/publish/connections", json={"kind": "github", "site": "acme.com", "settings": {"repo": "acme/site"}, "secret": {"token": "ghp_x"}}, headers=H).json()
    new = page(desc="Fixed description.")
    entry = client.post("/api/v1/projects/acme/publish", json={"url": url, "html": new, "connection_id": conn["id"], "fixes": [{"id": "meta-short-about", "title": "Short description"}]}, headers=H).json()
    assert entry["status"] == "pull_request" and "fixes_marked" not in entry
    assert "meta-short-about" not in share.issue_plans(project)
    state["merged"] = True
    web.pages[url] = new
    checked = client.post(f"/api/v1/projects/acme/publish/history/{entry['id']}/verify", headers=H).json()
    assert checked["fixes_marked"] == "done" and share.issue_plans(project)["meta-short-about"]["status"] == "done"
    again = client.post(f"/api/v1/projects/acme/publish/history/{entry['id']}/verify", headers=H).json()
    assert sum(1 for row in share.changelog(project) if row["summary"] == "Fixed: Short description") == 1 and again["fixes_marked"] == "done"


def test_fix_sync_never_breaks_a_publish_without_share(app_env, monkeypatch):
    client, web, router, ui = app_env
    monkeypatch.setitem(sys.modules, "aeo.ranksim.plugins.share", None)
    url = "https://acme.com/about"
    web.pages[url] = page()
    router.on("POST", r"hooks", lambda r, m: httpx.Response(200))
    _owned_routes(client, web)
    conn = client.post("/api/v1/projects/acme/publish/connections", json={"kind": "webhook", "site": "acme.com", "settings": {"url": "https://hooks.acme.test/x"}, "secret": {"signing_secret": "k" * 20}}, headers=H).json()
    done = client.post("/api/v1/projects/acme/publish", json={"url": url, "html": page(h1="New"), "connection_id": conn["id"], "fixes": [{"id": "a-b", "title": "x"}]}, headers=H)
    assert done.status_code == 200 and done.json()["status"] == "live"


# ---------------------------------------------------------------------------
# Review round 3: what counts as live, and rollbacks that wait for a merge
# ---------------------------------------------------------------------------


def test_pull_request_is_an_owner_note_until_merged(app_env, monkeypatch):
    from aeo.ranksim.plugins import share

    logged = {}
    monkeypatch.setattr(share, "log_change", lambda project, kind, summary, **kw: logged.setdefault(kw["key"], (summary, kw.get("public", True))))
    client, web, router, ui = app_env
    url = "https://acme.com/about"
    web.pages[url] = page()
    gh, state = gh_router({"about.html": page()})
    router.routes.extend(gh.routes)
    _owned_routes(client, web)
    conn = client.post("/api/v1/projects/acme/publish/connections", json={"kind": "github", "site": "acme.com", "settings": {"repo": "acme/site"}, "secret": {"token": "t"}}, headers=H).json()
    entry = client.post("/api/v1/projects/acme/publish", json={"url": url, "html": page(h1="New"), "connection_id": conn["id"]}, headers=H).json()
    assert logged == {f"publish-pr:{entry['id']}": ("Opened a pull request for /about.", False)}
    client.post(f"/api/v1/projects/acme/publish/history/{entry['id']}/rollback", headers=H)  # closes the open PR
    assert logged[f"publish-closed:{entry['id']}"][1] is False
    assert not any(public for _summary, public in logged.values())


def test_merged_github_rollback_waits_for_the_revert_merge(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    old = page()
    web.pages[url] = old
    router, state = gh_router({"about.html": old})
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    entry = pub.publish(url, page(h1="New"), gh_connect(pub)["id"])
    state["merged"] = True
    web.pages[url] = page(h1="New")
    assert pub.verify_live(entry["id"])["status"] == "live"
    pending = pub.rollback(entry["id"])  # opens a revert PR
    assert pending["rollback_pending"] and not pending["rolled_back_at"] and not pending["can_rollback"]
    with pytest.raises(PublishError):
        pub.rollback(entry["id"])  # no second revert PR
    done = pub.verify_live(entry["id"])  # the mock says merged
    assert done["rolled_back_at"] and not done["rollback_pending"]


def test_saved_webflow_draft_turns_live_when_the_page_shows_it(tmp_path):
    web = Web()
    url = "https://acme.com/about"
    web.pages[url] = page()
    router, _ = wf_router(web)
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    conn = pub.add_connection(kind="webflow", site="acme.com", label="", settings={"site_id": "site1"}, secret={"api_token": "t"})
    new = page(title="New title")
    saved = pub.publish(url, new, conn["id"])
    assert saved["status"] == "saved"
    web.pages[url] = new  # the owner published the site from Webflow
    assert pub.verify_live(saved["id"])["status"] == "live"


def test_rollback_refused_while_publishing_and_url_variants_count_as_newer(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    owned(pub, web)
    url = "https://acme.com/about"
    web.pages[url] = page()
    web.pages[url + "/"] = page()
    conn = add_webhook(pub, router)
    first = pub.publish(url, page(h1="One"), conn["id"])
    pub.publish(url + "/", page(h1="Two"), conn["id"])
    with pytest.raises(PublishError) as err:
        pub.rollback(first["id"])
    assert err.value.status == 409 and "newer" in str(err.value)
    stored = pub.store.history_entry(first["id"])
    stored["status"] = "applying"
    pub.store.put_history(stored)
    with pytest.raises(PublishError) as err:
        pub.rollback(first["id"])
    assert "still being sent" in str(err.value)


def test_fixes_wait_for_a_confirmed_live_check(app_env):
    from aeo.ranksim.plugins import share

    client, web, router, ui = app_env
    url = "https://acme.com/about"
    web.pages[url] = page()
    router.on("POST", r"hooks", lambda r, m: httpx.Response(200))  # accepted, but the live page lags (a cache)
    _owned_routes(client, web)
    conn = client.post("/api/v1/projects/acme/publish/connections", json={"kind": "webhook", "site": "acme.com", "settings": {"url": "https://hooks.acme.test/x"}, "secret": {"signing_secret": "k" * 20}}, headers=H).json()
    new = page(h1="New")
    entry = client.post("/api/v1/projects/acme/publish", json={"url": url, "html": new, "connection_id": conn["id"], "fixes": [{"id": "h1-weak", "title": "Weak heading"}]}, headers=H).json()
    assert entry["verify"]["status"] == "pending" and "h1-weak" not in share.issue_plans(ui.project("acme"))
    web.pages[url] = new
    checked = client.post(f"/api/v1/projects/acme/publish/history/{entry['id']}/verify", headers=H).json()
    assert checked["fixes_marked"] == "done" and share.issue_plans(ui.project("acme"))["h1-weak"]["status"] == "done"


def test_checkpoint_saves_the_claims(tmp_path):
    web, router = Web(), Router()
    pub = make_publisher(tmp_path, web, router)
    saves = []
    pub.checkpoint = lambda: saves.append(1)
    owned(pub, web)
    url = "https://acme.com/about"
    web.pages[url] = page()
    entry = pub.publish(url, page(h1="New"), add_webhook(pub, router)["id"])
    pub.rollback(entry["id"])
    assert len(saves) == 2  # the 'applying' entry and the rollback claim
