"""Load committed HTML into the rank database when it is empty.

A fresh clone has no sqlite file. The labeled export and the saved pages in
smartcrew/ are enough to start the demo. This does not crawl and does not
invent pages that are not in those files.
"""

from __future__ import annotations

from pathlib import Path

from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.saved_html import load_saved_html
from aeo.ranksim.store import RsPage, open_session

def _smartcrew(name: str) -> Path:
    """A bundled folder under the repo root (``RANKSIM_ROOT`` overrides), whatever the cwd."""
    from aeo.ranksim.keywords import repo_root

    return repo_root() / "smartcrew" / name


def draft_folders() -> tuple[Path, ...]:
    return (_smartcrew("pr8"), _smartcrew("pr8_v2"), _smartcrew("pr8_v3"))


def flag_known_drafts(database: str, folders: tuple[Path, ...] | None = None) -> int:
    """Mark stored pages whose HTML is byte-identical to a bundled draft as draft_bundle.

    Databases seeded before page provenance existed stored the pr8 drafts as ordinary pages
    under the live URLs. This finds them by content, so they stop counting as evaluation rows.
    """
    import hashlib

    from aeo.ranksim.saved_html import load_saved_html as load_dir

    digests = set()
    for folder in folders if folders is not None else draft_folders():
        if (folder / "manifest.csv").is_file():
            found, _missing = load_dir(folder)
            digests.update(hashlib.sha256(html.encode("utf-8", "replace")).hexdigest() for html in found.values())
    if not digests:
        return 0
    session = open_session(database)
    flagged = 0
    try:
        for row in session.query(RsPage).filter(RsPage.status == "fetched").all():
            # Only rows written before provenance existed. A tagged crawl of a page that later
            # went live with the draft's exact HTML is a real crawl and stays one.
            if row.source not in (None, "unknown") or not row.raw_html:
                continue
            digest = row.html_sha256 or hashlib.sha256(row.raw_html.encode("utf-8", "replace")).hexdigest()
            if digest in digests:
                row.source = "draft_bundle"
                flagged += 1
        session.commit()
    finally:
        session.close()
    return flagged


def backfill_js_shell(database: str) -> int:
    """Check stored pages written before the js_shell column existed. Returns how many are shells."""
    from aeo.ranksim.jsshell import is_js_shell

    session = open_session(database)
    found = 0
    try:
        for row in session.query(RsPage).filter(RsPage.status == "fetched", RsPage.js_shell.is_(None)).all():
            row.js_shell = bool((row.raw_html or "").strip()) and is_js_shell(row.raw_html, row.word_count)
            found += int(row.js_shell)
        session.commit()
    finally:
        session.close()
    return found


def seed_corpus_if_empty(database: str) -> str:
    flagged = flag_known_drafts(database)
    session = open_session(database)
    try:
        stored = (
            session.query(RsPage)
            .filter(RsPage.status == "fetched")
            .all()
        )
        ready = sum(1 for row in stored if (row.raw_html or "").strip())
    finally:
        session.close()
    if ready:
        note = f"Using {ready} pages already stored."
        if flagged:
            note += f" {flagged} stored pages match bundled draft HTML and were marked as drafts, not evaluation rows."
        return note
    saved, missing = load_saved_html()
    bundled = _bundle_html(_smartcrew("pr8_v3"))
    session = open_session(database)
    count = 0
    drafts = 0
    try:
        for url, html in saved.items():
            if not html.strip():
                continue
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched", source="saved_html"))
            count += 1
        for url, html in bundled.items():
            if not html.strip():
                continue
            # pr8_v3 is unpublished draft HTML for live SmartCrew URLs. Google's labels describe
            # the live pages, so these rows are kept for the demo but never used as examples.
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched", source="draft_bundle"))
            count += 1
            drafts += 1
    finally:
        session.close()
    note = (
        f"Seeded {count} pages from smartcrew/saved_html and smartcrew/pr8_v3. "
        f"{drafts} are unpublished drafts and are excluded from accuracy numbers. "
        "This seed is a demo corpus, not a crawl: run index-serp for an evaluation corpus."
    )
    if missing:
        note += f" {len(missing)} saved-HTML manifest rows had no file and were skipped."
    return note


def _bundle_html(folder: Path) -> dict[str, str]:
    manifest = folder / "manifest.csv"
    if not manifest.is_file():
        return {}
    from aeo.ranksim.saved_html import load_saved_html as load_dir

    found, _missing = load_dir(folder)
    return found
