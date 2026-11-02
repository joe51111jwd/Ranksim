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

PR8_V3 = Path("smartcrew/pr8_v3")


def seed_corpus_if_empty(database: str) -> str:
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
        return f"Using {ready} pages already stored."
    saved, missing = load_saved_html()
    bundled = _bundle_html(PR8_V3)
    session = open_session(database)
    count = 0
    try:
        for url, html in {**saved, **bundled}.items():
            if not html.strip():
                continue
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
            count += 1
    finally:
        session.close()
    note = f"Seeded {count} pages from smartcrew/saved_html and smartcrew/pr8_v3."
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
