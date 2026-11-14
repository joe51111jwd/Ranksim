"""Webflow through the Data API v2 with a site token (Site settings, Apps and integrations, API access).

What the API allows, and so what this does:

- Static pages: the SEO title and meta description (page metadata). The text on a static page cannot be changed
  through the API; that needs the Designer. Metadata changes go live when the site is published, and publishing the
  site also pushes anything else staged in Webflow, so that step needs an explicit confirm.
- CMS item pages (blog posts and other collection items): the item name (main heading), an SEO title field and a
  description field when the collection has them, and the rich text body. The item is published right after the update.

Rollback writes the previous values back the same way.
"""

from __future__ import annotations

from urllib.parse import unquote

from ..htmlparts import parts, text_of
from .base import Connector, ConnectorError, Prepared, path_of

API = "https://api.webflow.com/v2"
SEO_TITLE_FIELDS = ("seo-title", "meta-title", "title-tag")
DESCRIPTION_FIELDS = ("meta-description", "seo-description", "description", "summary", "post-summary", "excerpt")
MAX_ITEMS = 2000


class Webflow(Connector):
    kind = "webflow"
    label = "Webflow"
    setting_fields = ("site_id", "content_selector", "title_field", "description_field", "body_field")
    secret_fields = ("api_token",)
    default_regions = (".w-richtext",)
    summary = "Changes page SEO titles and descriptions, and CMS items: name, SEO fields and rich text body."
    limits = (
        "Webflow's API cannot change the text on a static page. Change it in the Designer.",
        "Page title and description changes go live only when the site is published, which also publishes anything else staged.",
    )

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.secret.get('api_token', '')}", "accept": "application/json"}

    @property
    def site_id(self) -> str:
        value = str(self.settings.get("site_id", "")).strip()
        if not value or not value.isalnum():
            raise ConnectorError("Add the Webflow site ID.")
        return value

    def call(self, method: str, path: str, **kwargs):
        return self.request(method, API + path, headers=self.headers, **kwargs)

    def check(self) -> str:
        site = self.call("GET", f"/sites/{self.site_id}").json()
        return site.get("displayName") or site.get("shortName") or self.site_id

    def inspect(self, url: str = "") -> list[dict]:
        """The guided Webflow check: site and token, reading access, the domain, this page, and what the API can change."""
        steps: list[dict] = []

        def step(key, label, ok, detail):
            steps.append({"key": key, "label": label, "ok": ok, "detail": detail})
            return ok

        try:
            site_id = self.site_id
        except ConnectorError as exc:
            step("site", "Site ID", False, str(exc))
            return steps
        response = self.request("GET", f"{API}/sites/{site_id}", headers=self.headers, expect=None)
        if response.status_code in (401, 403):
            step("login", "Site token", False, "Webflow did not accept the token. Generate a site API token in the site's Settings, Apps and integrations.")
            return steps
        if response.status_code == 404:
            step("site", "Site ID", False, "Webflow has no site with that ID for this token. Copy the Site ID from the site's Settings, General.")
            return steps
        if response.status_code != 200:
            step("login", "Site token", False, f"Webflow said {response.status_code}.")
            return steps
        site = response.json()
        step("login", "Site token", True, f"Connected to {site.get('displayName') or site.get('shortName') or site_id}.")
        try:
            self.pages()
            self.call("GET", f"/sites/{site_id}/collections")
            step("rights", "Permissions", True, "The token can read pages and CMS. Writing needs Pages and CMS write access, checked when you publish.")
        except ConnectorError as exc:
            step("rights", "Permissions", False, f"The token cannot read pages or CMS ({exc}). Give it Pages and CMS access.")
        if url:
            try:
                found = self.locate(url)
                if found["type"] == "page":
                    step("page", "This page", True, "This is a static page. Webflow's API can change its SEO title and description. Its text needs the Designer.")
                else:
                    name = (found["collection"] or {}).get("displayName") or (found["collection"] or {}).get("slug")
                    step("page", "This page", True, f"This is a CMS item in {name}. Ranksim can change its name, SEO fields and rich text.")
            except ConnectorError as exc:
                step("page", "This page", False, str(exc))
        step("limits", "What Ranksim can change", True,
             "Static pages: SEO title and description, live when the site is published. CMS items: name, SEO fields and rich text, live right away. Not the text of static pages.")
        return steps

    # -- finding the page or item -----------------------------------------

    def pages(self) -> list[dict]:
        found, offset = [], 0
        while True:
            data = self.call("GET", f"/sites/{self.site_id}/pages", params={"limit": 100, "offset": offset}).json()
            batch = data.get("pages") or []
            found += batch
            total = (data.get("pagination") or {}).get("total", len(found))
            offset += len(batch)
            if not batch or offset >= total:
                return found

    def items(self, collection_id: str) -> list[dict]:
        found, offset = [], 0
        while offset < MAX_ITEMS:
            data = self.call("GET", f"/collections/{collection_id}/items", params={"limit": 100, "offset": offset}).json()
            batch = data.get("items") or []
            found += batch
            total = (data.get("pagination") or {}).get("total", len(found))
            offset += len(batch)
            if not batch or offset >= total:
                break
        return found

    def locate(self, url: str) -> dict:
        path = path_of(url)
        for page in self.pages():
            if page.get("collectionId"):
                continue
            published = page.get("publishedPath") or ("/" + str(page.get("slug") or "")).rstrip("/") or "/"
            if (published.rstrip("/") or "/") == path:
                return {"type": "page", "page": page}
        segments = [unquote(s) for s in path.strip("/").split("/") if s]
        if len(segments) >= 2:
            prefix, item_slug = "/".join(segments[:-1]), segments[-1]
            collections = self.call("GET", f"/sites/{self.site_id}/collections").json().get("collections") or []
            for collection in collections:
                if collection.get("slug") == prefix or collection.get("slug") == segments[-2]:
                    for item in self.items(collection["id"]):
                        if (item.get("fieldData") or {}).get("slug") == item_slug:
                            schema = self.call("GET", f"/collections/{collection['id']}").json()
                            return {"type": "item", "collection": collection, "schema": schema, "item": item}
        raise ConnectorError(f"No Webflow page or CMS item has the address {path}.")

    # -- publish -----------------------------------------------------------

    def _field(self, schema: dict, setting: str, names: tuple[str, ...], types: tuple[str, ...]) -> str:
        fields = schema.get("fields") or []
        chosen = str(self.settings.get(setting, "")).strip()
        slugs = {f.get("slug"): f for f in fields}
        if chosen:
            return chosen if chosen in slugs else ""
        for name in names:
            if name in slugs and (not types or slugs[name].get("type") in types):
                return name
        return ""

    def prepare(self, url: str, before_html: str, after_html: str, *, slug: str = "") -> Prepared:
        found = self.locate(url)
        before, after = parts(before_html, self.regions()), parts(after_html, self.regions())
        rows = self.rows_for(before, after)
        warnings: list[dict] = []
        if found["type"] == "page":
            page = found["page"]
            seo = dict(page.get("seo") or {})
            payload = {"seo": {}}
            for row in rows:
                if row["field"] == "title":
                    payload["seo"]["title"] = after.title
                    row.update(apply=True, note="Sets the page SEO title.")
                elif row["field"] == "description":
                    payload["seo"]["description"] = after.description
                    row.update(apply=True, note="Sets the page meta description.")
                else:
                    row["note"] = "Webflow's API cannot change text on a static page. Change it in the Designer."
            if payload["seo"]:
                warnings.append(
                    {
                        "code": "site_publish",
                        "confirm": False,
                        "text": "Webflow saves page settings as a draft. Tick Publish the site to make them live now. That also publishes anything else staged in Webflow.",
                        "option": "publish_site",
                    }
                )
            return Prepared(
                url=url,
                target={"type": "page", "id": page.get("id"), "path": page.get("publishedPath")},
                rows=rows,
                warnings=warnings,
                payload=payload,
                previous={"seo": {k: seo.get(k, "") for k in payload["seo"]}},
            )

        item, schema, collection = found["item"], found["schema"], found["collection"]
        data = item.get("fieldData") or {}
        title_field = self._field(schema, "title_field", SEO_TITLE_FIELDS, ("PlainText",))
        desc_field = self._field(schema, "description_field", DESCRIPTION_FIELDS, ("PlainText", "RichText"))
        body_field = self._field(schema, "body_field", (), ())
        if not body_field:
            for f in schema.get("fields") or []:
                if f.get("type") == "RichText" and f.get("slug") != desc_field and text_of(str(data.get(f.get("slug"), ""))) == before.region_text:
                    body_field = f["slug"]
                    break
        update: dict = {}
        for row in rows:
            field = row["field"]
            if field == "h1":
                if before.h1 == str(data.get("name", "")).strip():
                    update["name"] = after.h1
                    row.update(apply=True, note="Sets the item name.")
                else:
                    row["note"] = "The heading is not the item name, so it is left as is."
            elif field == "title":
                if title_field:
                    update[title_field] = after.title
                    row.update(apply=True, note=f"Sets the {title_field} field.")
                else:
                    row["note"] = "This collection has no SEO title field. Set one in the connection to change it."
            elif field == "description":
                if desc_field:
                    update[desc_field] = after.description
                    row.update(apply=True, note=f"Sets the {desc_field} field.")
                else:
                    row["note"] = "This collection has no description field. Set one in the connection to change it."
            elif field == "body":
                if body_field and after.region_selector:
                    update[body_field] = after.region_html
                    row.update(apply=True, note=f"Replaces the {body_field} rich text.")
                else:
                    row["note"] = "The rich text body could not be matched to a field of this item."
        warnings += self.embed_warning(before, after, rows)
        return Prepared(
            url=url,
            target={"type": "item", "id": item.get("id"), "collection_id": collection.get("id"), "collection": collection.get("slug")},
            rows=rows,
            warnings=warnings,
            payload={"fieldData": update},
            previous={"fieldData": {k: data.get(k, "") for k in update}},
        )

    def _publish_site(self) -> None:
        domains = self.call("GET", f"/sites/{self.site_id}/custom_domains").json().get("customDomains") or []
        body = {"publishToWebflowSubdomain": True}
        if domains:
            body["customDomains"] = [d["id"] for d in domains if d.get("id")]
        self.call("POST", f"/sites/{self.site_id}/publish", json=body, expect=(200, 202))

    def _write(self, target: dict, payload: dict, *, publish_site: bool) -> dict:
        if target["type"] == "page":
            if payload.get("seo"):
                self.call("PUT", f"/pages/{target['id']}", json={"seo": payload["seo"]})
                if publish_site:
                    self._publish_site()
                    return {"status": "live"}
                return {"status": "saved", "note": "Saved in Webflow. Publish the site to make it live."}
            return {"status": "live"}
        cid, iid = target["collection_id"], target["id"]
        if payload.get("fieldData"):
            self.call("PATCH", f"/collections/{cid}/items/{iid}", json={"fieldData": payload["fieldData"]})
            self.call("POST", f"/collections/{cid}/items/publish", json={"itemIds": [iid]}, expect=(200, 202))
        return {"status": "live"}

    def apply(self, prepared: Prepared, *, options: dict | None = None) -> dict:
        publish_site = bool((options or {}).get("publish_site"))
        result = self._write(prepared.target, prepared.payload, publish_site=publish_site)
        return {**result, **{k: prepared.target.get(k) for k in ("type", "id", "collection_id")}}

    def rollback(self, entry: dict) -> dict:
        remote = entry["remote"]
        return self._write(remote, entry["previous"], publish_site=bool(entry.get("options", {}).get("publish_site")))
