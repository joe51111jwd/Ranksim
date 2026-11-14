"""GitHub for static sites: commit the edited file on a new branch and open a pull request.

Settings: ``repo`` (owner/name), ``branch`` (the base, default main), ``root`` (folder the site is built from, for
example ``public``), and ``paths``: optional ``/url/path=file/path.html`` lines for pages that do not follow the
usual layout. Without a mapping, ``/`` is ``index.html`` and ``/about`` is ``about.html`` or ``about/index.html``.

When the file in the repository is the page as served, the whole edited page is committed. When the file is a
template (it differs from the live page), only the title tag, meta description and main heading are patched, and only
where each old value appears exactly once. Nothing is live until the pull request is merged; the live check then
confirms it. Rollback closes an open pull request and deletes its branch, or opens a revert pull request once merged.
"""

from __future__ import annotations

import base64
import html as htmllib
import re
import secrets
import time

from ..htmlparts import parts, same_document, squash
from .base import Connector, ConnectorError, Prepared, document_embed_warning, path_of

API = "https://api.github.com"
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SAFE_PATH = re.compile(r"^[A-Za-z0-9_.\-/]+$")


class GitHub(Connector):
    kind = "github"
    label = "GitHub"
    setting_fields = ("repo", "branch", "root", "paths", "content_selector")
    secret_fields = ("token",)
    summary = "Commits the edited page on a new branch and opens a pull request. It goes live when you merge it."
    limits = (
        "When the file is a template rather than the served page, only the title, description and heading are changed.",
        "Nothing is live until the pull request is merged and the site rebuilds.",
    )

    @property
    def repo(self) -> str:
        repo = str(self.settings.get("repo", "")).strip().removeprefix("https://github.com/").strip("/")
        if not REPO.match(repo):
            raise ConnectorError("Add the repository as owner/name.")
        return repo

    @property
    def base(self) -> str:
        branch = str(self.settings.get("branch") or "main").strip()
        if not SAFE_PATH.match(branch):
            raise ConnectorError("That branch name cannot be used.")
        return branch

    def call(self, method: str, path: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.secret.get('token', '')}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        return self.request(method, f"{API}/repos/{self.repo}{path}", headers=headers, **kwargs)

    def check(self) -> str:
        data = self.call("GET", "").json()
        if not (data.get("permissions") or {}).get("push", True):
            raise ConnectorError("This token cannot push to the repository.")
        return data.get("full_name") or self.repo

    def candidates(self, url: str) -> list[str]:
        path = path_of(url)
        mapping = {}
        for line in str(self.settings.get("paths", "")).replace(",", "\n").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                mapping[("/" + key.strip().strip("/")) if key.strip() != "/" else "/"] = value.strip().strip("/")
        root = str(self.settings.get("root", "")).strip().strip("/")
        prefix = f"{root}/" if root else ""
        if path in mapping:
            found = [mapping[path]]
        elif path == "/":
            found = [prefix + "index.html"]
        else:
            stem = path.strip("/")
            found = [prefix + stem] if stem.endswith((".html", ".htm")) else [prefix + stem + ".html", prefix + stem + "/index.html"]
        for candidate in found:
            if ".." in candidate.split("/") or not SAFE_PATH.match(candidate):
                raise ConnectorError("That file path cannot be used.")
        return found

    def read_file(self, path: str, ref: str) -> dict | None:
        response = self.call("GET", f"/contents/{path}", params={"ref": ref}, expect=(200, 404))
        if response.status_code == 404:
            return None
        data = response.json()
        if not isinstance(data, dict) or data.get("type") != "file":
            return None
        data["text"] = base64.b64decode(data.get("content") or "").decode("utf-8", errors="replace")
        return data

    def prepare(self, url: str, before_html: str, after_html: str, *, slug: str = "") -> Prepared:
        found = None
        for path in self.candidates(url):
            found = self.read_file(path, self.base)
            if found:
                break
        if not found:
            raise ConnectorError(f"No file for {path_of(url)} in {self.repo} on {self.base}. Add a path mapping to the connection.")
        source = found["text"]
        before, after = parts(before_html, self.regions()), parts(after_html, self.regions())
        rows = self.rows_for(before, after)
        warnings: list[dict] = []
        if same_document(source, before_html):
            new_text = after_html
            for row in rows:
                row.update(apply=True, note="Committed as part of the edited file.")
            warnings += document_embed_warning(before_html, after_html)
            mode = "whole file"
        else:
            new_text = source
            mode = "patch"
            for row in rows:
                field = row["field"]
                patched = None
                if field == "title":
                    patched = _replace_once(new_text, r"(<title[^>]*>)(.*?)(</title>)", before.title, after.title)
                elif field == "description":
                    patched = _replace_meta_description(new_text, before.description, after.description)
                elif field == "h1":
                    patched = _replace_once(new_text, r"(<h1[^>]*>)(.*?)(</h1>)", before.h1, after.h1)
                if patched is not None:
                    new_text = patched
                    row.update(apply=True, note="Patched in the source file.")
                elif field == "body":
                    row["note"] = "The file is a template, not the served page, so the body is not changed here."
                else:
                    row["note"] = "The old value was not found exactly once in the source file."
        return Prepared(
            url=url,
            target={"repo": self.repo, "base": self.base, "path": found["path"], "sha": found.get("sha"), "mode": mode},
            rows=rows,
            warnings=warnings,
            payload={"text": new_text},
            previous={"text": source, "sha": found.get("sha")},
            live_after_apply=False,
        )

    def _branch(self, slug: str) -> str:
        clean = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")[:40] or "page"
        return f"ranksim/{clean}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{secrets.token_hex(3)}"

    def _open_pr(self, *, path: str, text: str, sha: str | None, title: str, body: str, slug: str) -> dict:
        base_sha = self.call("GET", f"/git/ref/heads/{self.base}").json()["object"]["sha"]
        branch = self._branch(slug)
        self.call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        put = {"message": title, "content": base64.b64encode(text.encode("utf-8")).decode("ascii"), "branch": branch}
        if sha:
            put["sha"] = sha
        self.call("PUT", f"/contents/{path}", json=put)
        pr = self.call("POST", "/pulls", json={"title": title, "head": branch, "base": self.base, "body": body}).json()
        return {"status": "pull_request", "branch": branch, "pr_number": pr.get("number"), "pr_url": pr.get("html_url"), "path": path}

    def apply(self, prepared: Prepared, *, options: dict | None = None) -> dict:
        t = prepared.target
        changed = ", ".join(row["label"].lower() for row in prepared.rows if row.get("apply")) or "page"
        return self._open_pr(
            path=t["path"],
            text=prepared.payload["text"],
            sha=t.get("sha"),
            title=f"Ranksim: update {path_of(prepared.url)}",
            body=f"Edited in Ranksim: {changed}.\n\nPage: {prepared.url}",
            slug=path_of(prepared.url).strip("/") or "home",
        )

    def merged(self, remote: dict) -> bool:
        pr = self.call("GET", f"/pulls/{remote['pr_number']}").json()
        return bool(pr.get("merged"))

    def rollback(self, entry: dict) -> dict:
        remote = entry["remote"]
        pr = self.call("GET", f"/pulls/{remote['pr_number']}").json()
        if not pr.get("merged"):
            if pr.get("state") == "open":
                self.call("PATCH", f"/pulls/{remote['pr_number']}", json={"state": "closed"})
            self.call("DELETE", f"/git/refs/heads/{remote['branch']}", expect=(204, 422, 404))
            return {"status": "closed", "note": "The pull request was closed and its branch deleted. Nothing went live."}
        current = self.read_file(remote["path"], self.base)
        result = self._open_pr(
            path=remote["path"],
            text=entry["previous"]["text"],
            sha=(current or {}).get("sha"),
            title=f"Ranksim: revert {path_of(entry['url'])}",
            body=f"Reverts pull request #{remote['pr_number']}.",
            slug="revert-" + (path_of(entry["url"]).strip("/") or "home"),
        )
        result["note"] = "A revert pull request is open. Merge it to undo the change."
        return result


def _replace_once(text: str, pattern: str, old: str, new: str) -> str | None:
    hits = [m for m in re.finditer(pattern, text, flags=re.I | re.S) if squash(htmllib.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))) == old]
    if len(hits) != 1 or "<" in hits[0].group(2):
        return None
    m = hits[0]
    return text[: m.start(2)] + htmllib.escape(new, quote=False) + text[m.end(2):]


def _replace_meta_description(text: str, old: str, new: str) -> str | None:
    tags = [m for m in re.finditer(r"<meta\b[^>]*>", text, flags=re.I) if re.search(r"name\s*=\s*[\"']description[\"']", m.group(0), re.I)]
    if len(tags) != 1:
        return None
    tag = tags[0]
    content = re.search(r"content\s*=\s*([\"'])(.*?)\1", tag.group(0), re.I | re.S)
    if not content or squash(htmllib.unescape(content.group(2))) != old:
        return None
    quote = content.group(1)
    start = tag.start() + content.start(2)
    end = tag.start() + content.end(2)
    return text[:start] + htmllib.escape(new, quote=True) + text[end:]
