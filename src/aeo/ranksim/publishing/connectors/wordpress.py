"""WordPress through the REST API with an application password (Users, Profile, Application Passwords).

Finds the post or page by its URL, then:

- main heading -> post title (when the live heading is the post title)
- title tag    -> Rank Math or Yoast SEO title when the plugin lets the API write it; otherwise the post title when
                  the title tag is "post title + site suffix"
- description  -> Rank Math or Yoast description when writable; otherwise the excerpt
- body         -> post content, from the theme's content area (.entry-content and friends)

Rollback writes the previous raw values back.
"""

from __future__ import annotations

import html as htmllib
from urllib.parse import unquote, urlsplit

import httpx

from ..htmlparts import parts, text_of
from .base import SHORTCODE, Connector, ConnectorError, Prepared, path_of, region_matches, same_page, strip_suffix_title

YOAST_TITLE, YOAST_DESC = "_yoast_wpseo_title", "_yoast_wpseo_metadesc"
RM_TITLE, RM_DESC = "rank_math_title", "rank_math_description"


class WordPress(Connector):
    kind = "wordpress"
    label = "WordPress"
    setting_fields = ("base_url", "username", "content_selector")
    secret_fields = ("app_password",)
    default_regions = (".entry-content", ".wp-block-post-content", ".post-content", ".page-content", "article .content")
    summary = "Changes the post or page title, content and excerpt, and the Yoast or Rank Math SEO title and description."
    limits = (
        "Pages built with a page builder (Elementor, Divi) keep their layout in builder data, so body changes may not show.",
        "Yoast only lets the API write SEO fields when the site exposes them. Otherwise change those in WordPress.",
    )

    # -- plumbing ----------------------------------------------------------

    @property
    def root(self) -> str:
        base = str(self.settings.get("base_url", "")).rstrip("/")
        if not base:
            raise ConnectorError("Add the WordPress site address.")
        return self.safe_url(base + "/").rstrip("/") + "/wp-json"

    @property
    def auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(str(self.settings.get("username", "")), self.secret.get("app_password", ""))

    def call(self, method: str, path: str, **kwargs) -> httpx.Response:
        return self.request(method, self.root + path, auth=self.auth, **kwargs)

    def check(self) -> str:
        me = self.call("GET", "/wp/v2/users/me", params={"context": "edit"}).json()
        return f"{me.get('name') or me.get('slug') or 'user'} on {self.settings.get('base_url')}"

    def seo_plugin(self) -> str:
        try:
            namespaces = self.call("GET", "/").json().get("namespaces") or []
        except (ConnectorError, ValueError, AttributeError):
            return ""
        if "rankmath/v1" in namespaces:
            return "rankmath"
        if "yoast/v1" in namespaces:
            return "yoast"
        return ""

    def locate(self, url: str) -> dict:
        path = path_of(url)
        if path == "/":
            try:
                front = int(self.call("GET", "/wp/v2/settings").json().get("page_on_front") or 0)
            except (ConnectorError, ValueError):
                front = 0
            if not front:
                raise ConnectorError("This homepage is not a WordPress page (it lists posts), so the API cannot change it.")
            item = self.call("GET", f"/wp/v2/pages/{front}", params={"context": "edit"}).json()
            return {"type": "pages", "item": item}
        slug = unquote(path.rstrip("/").rsplit("/", 1)[-1])
        for kind in ("pages", "posts"):
            items = self.call("GET", f"/wp/v2/{kind}", params={"slug": slug, "context": "edit"}).json()
            for item in items if isinstance(items, list) else []:
                if same_page(str(item.get("link", "")), url):
                    return {"type": kind, "item": item}
        raise ConnectorError(f"No WordPress post or page has the address {urlsplit(url).path or '/'}.")

    # -- publish -----------------------------------------------------------

    def prepare(self, url: str, before_html: str, after_html: str, *, slug: str = "") -> Prepared:
        found = self.locate(url)
        item, kind = found["item"], found["type"]
        raw = lambda key: (item.get(key) or {}).get("raw", "") if isinstance(item.get(key), dict) else str(item.get(key) or "")  # noqa: E731
        rendered = lambda key: (item.get(key) or {}).get("rendered", "") if isinstance(item.get(key), dict) else ""  # noqa: E731
        post_title_text = htmllib.unescape(text_of(rendered("title")) or raw("title"))
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        plugin = self.seo_plugin()
        before, after = parts(before_html, self.regions()), parts(after_html, self.regions())
        rows = self.rows_for(before, after)
        payload: dict = {"post": {}, "meta": {}, "rankmath": {}}
        previous = {"title": raw("title"), "content": raw("content"), "excerpt": raw("excerpt"), "meta": {}, "rankmath": {}}
        warnings: list[dict] = []
        changing = {row["field"] for row in rows}

        for row in rows:
            field = row["field"]
            if field == "h1":
                if before.h1 == post_title_text:
                    payload["post"]["title"] = after.h1
                    row.update(apply=True, note="Sets the WordPress title.")
                else:
                    row["note"] = "The page heading is not the WordPress title here, so it is left as is."
            elif field == "title":
                if plugin == "rankmath":
                    payload["rankmath"][RM_TITLE] = after.title
                    previous["rankmath"][RM_TITLE] = before.title
                    row.update(apply=True, note="Sets the Rank Math SEO title.")
                elif plugin == "yoast" and YOAST_TITLE in meta:
                    payload["meta"][YOAST_TITLE] = after.title
                    previous["meta"][YOAST_TITLE] = meta.get(YOAST_TITLE, "")
                    row.update(apply=True, note="Sets the Yoast SEO title.")
                elif plugin == "yoast":
                    row["note"] = "Yoast does not let the API change the SEO title on this site. Change it in WordPress."
                elif "h1" not in changing and (new := strip_suffix_title(before.title, post_title_text, after.title)):
                    payload["post"]["title"] = new
                    row.update(apply=True, note="Sets the WordPress title, which this theme uses for the title tag.")
                else:
                    row["note"] = "No SEO plugin here, and the title tag is not built from the WordPress title."
            elif field == "description":
                if plugin == "rankmath":
                    payload["rankmath"][RM_DESC] = after.description
                    previous["rankmath"][RM_DESC] = before.description
                    row.update(apply=True, note="Sets the Rank Math description.")
                elif plugin == "yoast" and YOAST_DESC in meta:
                    payload["meta"][YOAST_DESC] = after.description
                    previous["meta"][YOAST_DESC] = meta.get(YOAST_DESC, "")
                    row.update(apply=True, note="Sets the Yoast description.")
                elif plugin == "yoast":
                    row["note"] = "Yoast does not let the API change the description on this site. Change it in WordPress."
                else:
                    payload["post"]["excerpt"] = after.description
                    row.update(apply=True, note="Saved as the excerpt. Your theme decides whether it is used as the description.")
            elif field == "body":
                if not after.region_selector:
                    row["note"] = "The post content area was not found on the page. Set a content selector on the connection."
                elif not region_matches(before, rendered("content")):
                    row["note"] = "The content area on the page does not match this post's content, so the body is left as is."
                else:
                    payload["post"]["content"] = after.region_html
                    row.update(apply=True, note="Replaces the post content.")
                    current = raw("content")
                    if "<!-- wp:" in current:
                        warnings.append({"code": "blocks", "confirm": True, "text": "This post uses blocks. Publishing the body saves it as plain HTML, so the blocks become one Classic block."})
                    if SHORTCODE.search(current):
                        warnings.append({"code": "shortcodes", "confirm": True, "text": "This post uses shortcodes. Publishing the body replaces them with the HTML they produced."})
        warnings += self.embed_warning(before, after, rows)
        return Prepared(
            url=url,
            target={"type": kind, "id": item.get("id"), "link": item.get("link"), "plugin": plugin or "none"},
            rows=rows,
            warnings=warnings,
            payload=payload,
            previous=previous,
        )

    def _write(self, kind: str, item_id, post: dict, meta: dict, rankmath: dict) -> None:
        body = dict(post)
        if meta:
            body["meta"] = meta
        if body:
            self.call("POST", f"/wp/v2/{kind}/{item_id}", json=body)
        if rankmath:
            self.call("POST", "/rankmath/v1/updateMeta", json={"objectType": "post", "objectID": item_id, "meta": rankmath})

    def apply(self, prepared: Prepared, *, options: dict | None = None) -> dict:
        p, t = prepared.payload, prepared.target
        self._write(t["type"], t["id"], p["post"], p["meta"], p["rankmath"])
        return {"status": "live", "link": t.get("link"), "id": t["id"], "type": t["type"]}

    def rollback(self, entry: dict) -> dict:
        prev, remote, payload = entry["previous"], entry["remote"], entry.get("payload") or {}
        post = {key: prev[key] for key in (payload.get("post") or {})}
        meta = {key: prev["meta"].get(key, "") for key in (payload.get("meta") or {})}
        rankmath = {key: prev["rankmath"].get(key, "") for key in (payload.get("rankmath") or {})}
        self._write(remote["type"], remote["id"], post, meta, rankmath)
        return {"status": "live"}
