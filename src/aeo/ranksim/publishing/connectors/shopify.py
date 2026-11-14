"""Shopify through the Admin GraphQL API with a custom app's Admin API access token.

Online store pages (/pages/<handle>) and blog articles (/blogs/<blog>/<handle>):

- main heading -> page or article title (when the live heading is that title)
- title tag    -> the SEO title (metafield global.title_tag)
- description  -> the SEO description (metafield global.description_tag)
- body         -> page or article body, from the theme's content area (.rte and friends)

Products and collections are not covered yet. Rollback writes the previous values back, and removes an SEO field
that did not exist before.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from ..htmlparts import parts
from .base import Connector, ConnectorError, Prepared, path_of, region_matches

API_VERSION = "2025-10"
SHOP = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
SEO = {"title": "title_tag", "description": "description_tag"}

FIELDS = """
  id title handle body
  seoTitle: metafield(namespace: "global", key: "title_tag") { value }
  seoDescription: metafield(namespace: "global", key: "description_tag") { value }
"""
PAGE_QUERY = "query($q: String!) { pages(first: 1, query: $q) { nodes { %s } } }" % FIELDS
# Blog.articles takes no query argument, so articles are looked up top level and matched on their blog's handle.
ARTICLE_QUERY = "query($a: String!) { articles(first: 25, query: $a) { nodes { %s summary blog { handle } } } }" % FIELDS
PAGE_UPDATE = "mutation($id: ID!, $page: PageUpdateInput!) { pageUpdate(id: $id, page: $page) { page { id } userErrors { field message } } }"
ARTICLE_UPDATE = (
    "mutation($id: ID!, $article: ArticleUpdateInput!) { articleUpdate(id: $id, article: $article) "
    "{ article { id } userErrors { field message } } }"
)
METAFIELDS_DELETE = (
    "mutation($m: [MetafieldIdentifierInput!]!) { metafieldsDelete(metafields: $m) "
    "{ deletedMetafields { key } userErrors { field message } } }"
)


class Shopify(Connector):
    kind = "shopify"
    label = "Shopify"
    setting_fields = ("shop", "content_selector")
    secret_fields = ("access_token",)
    default_regions = (".rte", ".page__content", ".article-template__content", ".article__content", ".page-content")
    summary = "Changes online store pages and blog posts: title, SEO title, SEO description and body."
    limits = ("Products and collections are not covered yet.", "Theme text outside the page or post body is set in the theme editor.")

    @property
    def endpoint(self) -> str:
        shop = str(self.settings.get("shop", "")).strip().lower().removeprefix("https://").rstrip("/")
        if not SHOP.match(shop):
            raise ConnectorError("Add the store's myshopify.com address, for example your-store.myshopify.com.")
        return f"https://{shop}/admin/api/{API_VERSION}/graphql.json"

    def gql(self, query: str, variables: dict | None = None) -> dict:
        response = self.request(
            "POST",
            self.endpoint,
            json={"query": query, "variables": variables or {}},
            headers={"X-Shopify-Access-Token": self.secret.get("access_token", "")},
        )
        data = response.json()
        if data.get("errors"):
            first = data["errors"][0]
            raise ConnectorError(f"Shopify said: {(first.get('message') if isinstance(first, dict) else first)}"[:200])
        return data.get("data") or {}

    @staticmethod
    def user_errors(result: dict) -> None:
        errors = result.get("userErrors") or []
        if errors:
            raise ConnectorError("Shopify said: " + "; ".join(str(e.get("message")) for e in errors[:3]))

    def check(self) -> str:
        return (self.gql("{ shop { name } }").get("shop") or {}).get("name") or "store"

    def inspect(self, url: str = "") -> list[dict]:
        """The guided Shopify check: store address, token, permissions, this page, and what the API can change."""
        steps: list[dict] = []

        def step(key, label, ok, detail):
            steps.append({"key": key, "label": label, "ok": ok, "detail": detail})
            return ok

        try:
            self.endpoint
        except ConnectorError as exc:
            step("site", "Store address", False, str(exc))
            return steps
        step("site", "Store address", True, f"Using {self.settings.get('shop', '').strip().lower()}.")
        try:
            data = self.gql("{ shop { name } currentAppInstallation { accessScopes { handle } } }")
        except ConnectorError as exc:
            text = str(exc)
            if " 401" in text or " 403" in text:
                text = "Shopify did not accept the token. Paste the Admin API access token of your custom app (it starts with shpat_)."
            elif " 404" in text:
                text = "No Shopify store answered at that address. Check the myshopify.com address."
            step("login", "Access token", False, text)
            return steps
        step("login", "Access token", True, f"Connected to {(data.get('shop') or {}).get('name') or 'your store'}.")
        scopes = {s.get("handle") for s in ((data.get("currentAppInstallation") or {}).get("accessScopes") or [])}
        missing = [scope for scope in ("write_online_store_pages", "write_content") if scope not in scopes]
        if not scopes:
            step("rights", "Permissions", True, "Shopify did not list the app's permissions. Ranksim finds out when it publishes.")
        else:
            step("rights", "Permissions", not missing,
                 "The app can change pages and blog posts." if not missing
                 else f"The app is missing {', '.join(missing)}. Add it in the app's Admin API scopes, then reinstall the app.")
        if url:
            try:
                found = self.locate(url)
                noun = "page" if found["type"] == "page" else "blog post"
                step("page", "This page", True, f"Found the {noun} \"{found['item'].get('title') or found['item'].get('handle')}\".")
            except ConnectorError as exc:
                step("page", "This page", False, str(exc))
        step("limits", "What Ranksim can change", True,
             "Page and blog post titles, body text, SEO titles and descriptions. Not products, collections or theme text, which stay in Shopify.")
        return steps

    def locate(self, url: str) -> dict:
        segments = [unquote(s) for s in path_of(url).strip("/").split("/") if s]
        if len(segments) == 2 and segments[0] == "pages":
            nodes = ((self.gql(PAGE_QUERY, {"q": f"handle:{segments[1]}"}).get("pages") or {}).get("nodes")) or []
            if nodes and nodes[0].get("handle") == segments[1]:
                return {"type": "page", "item": nodes[0]}
        elif len(segments) == 3 and segments[0] == "blogs":
            articles = ((self.gql(ARTICLE_QUERY, {"a": f"handle:{segments[2]}"}).get("articles") or {}).get("nodes")) or []
            for article in articles:
                if article.get("handle") == segments[2] and (article.get("blog") or {}).get("handle") == segments[1]:
                    return {"type": "article", "item": article}
        else:
            raise ConnectorError("Only Shopify pages (/pages/...) and blog posts (/blogs/...) can be published here.")
        raise ConnectorError(f"No Shopify page or blog post has the address {path_of(url)}.")

    def prepare(self, url: str, before_html: str, after_html: str, *, slug: str = "") -> Prepared:
        found = self.locate(url)
        item, kind = found["item"], found["type"]
        before, after = parts(before_html, self.regions()), parts(after_html, self.regions())
        rows = self.rows_for(before, after)
        fields: dict = {}
        seo: dict = {}
        previous = {"fields": {}, "seo": {}}
        for row in rows:
            field = row["field"]
            if field == "h1":
                if before.h1 == (item.get("title") or "").strip():
                    fields["title"] = after.h1
                    previous["fields"]["title"] = item.get("title")
                    row.update(apply=True, note=f"Sets the {kind} title.")
                else:
                    row["note"] = f"The heading is not the {kind} title here, so it is left as is."
            elif field in SEO:
                key = SEO[field]
                seo[key] = after.title if field == "title" else after.description
                current = (item.get("seoTitle" if field == "title" else "seoDescription") or {}).get("value")
                previous["seo"][key] = current
                row.update(apply=True, note="Sets the SEO " + ("title." if field == "title" else "description."))
            elif field == "body":
                if after.region_selector and region_matches(before, item.get("body") or ""):
                    fields["body"] = after.region_html
                    previous["fields"]["body"] = item.get("body")
                    row.update(apply=True, note=f"Replaces the {kind} body.")
                else:
                    row["note"] = f"The content area on the page does not match this {kind}'s body, so it is left as is."
        return Prepared(
            url=url,
            target={"type": kind, "id": item["id"], "handle": item.get("handle")},
            rows=rows,
            warnings=self.embed_warning(before, after, rows),
            payload={"fields": fields, "seo": seo},
            previous=previous,
        )

    def _write(self, kind: str, item_id: str, fields: dict, seo: dict, remove: list[str]) -> None:
        body = dict(fields)
        if seo:
            body["metafields"] = [
                {"namespace": "global", "key": key, "type": "single_line_text_field" if key == "title_tag" else "multi_line_text_field", "value": value}
                for key, value in seo.items()
            ]
        if body:
            if kind == "page":
                self.user_errors(self.gql(PAGE_UPDATE, {"id": item_id, "page": body})["pageUpdate"])
            else:
                self.user_errors(self.gql(ARTICLE_UPDATE, {"id": item_id, "article": body})["articleUpdate"])
        if remove:
            self.user_errors(
                self.gql(METAFIELDS_DELETE, {"m": [{"ownerId": item_id, "namespace": "global", "key": key} for key in remove]})["metafieldsDelete"]
            )

    def apply(self, prepared: Prepared, *, options: dict | None = None) -> dict:
        t, p = prepared.target, prepared.payload
        self._write(t["type"], t["id"], p["fields"], p["seo"], [])
        return {"status": "live", "type": t["type"], "id": t["id"]}

    def rollback(self, entry: dict) -> dict:
        prev, remote = entry["previous"], entry["remote"]
        seo = {k: v for k, v in prev["seo"].items() if v is not None}
        remove = [k for k, v in prev["seo"].items() if v is None]
        self._write(remote["type"], remote["id"], dict(prev["fields"]), seo, remove)
        return {"status": "live"}
