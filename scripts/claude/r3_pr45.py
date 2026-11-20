"""SmartCrew PR 45 (a real pre-registered edit): save the after-HTML and freeze PREDICTED directions.

  python scripts/claude/r3_pr45.py status    # is the edit live? one read-only GET per page, nothing saved
  python scripts/claude/r3_pr45.py after     # save the live HTML of the 15 pages (only once the edit is live)
  python scripts/claude/r3_pr45.py predict   # frozen content scorer on before vs after HTML -> ledger

The before-HTML was saved by the owner at 6:58 PM ET on 2026-11-19 in
/workspace/ranksim-data/snapshots/2026-11-19-pr45-before-html/. This script only
reads the public pages (plain GET, the same as any visitor). It never writes to
the client site and never touches the PR 41 ledger.

The ledger (evals/claude/r3/pr45/ledger.json) holds, for every (target search,
edited page): the frozen scorer's score on the before-HTML and on the after-HTML
for that search, and the PREDICTED direction (up, down or no change). It is
written from page HTML only. No search result pulled after the merge is read
anywhere in this file. Outcomes are a later confirmation, not part of round 3's test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BASE = "https://www.smartcrewhq.com"
BEFORE_DIR = Path("/workspace/ranksim-data/snapshots/2026-11-19-pr45-before-html")
AFTER_DIR = Path("/workspace/ranksim-data/snapshots/2026-11-19-pr45-after-html")
OUT = REPO / "evals" / "claude" / "r3" / "pr45"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
# The marker the brief names: a new FAQ on the edited pages.
MARKER = "free alternative to raken"
# Target searches named in GOAL.md, round 3 note.
SEARCHES = [
    "raken alternative",
    "raken alternatives",
    "free alternative to raken",
    "raken pricing",
    "busybusy alternative",
    "clockshark alternative",
    "b2w alternative",
    "earthwork software",
    "construction software per user pricing",
]


def before_manifest() -> list[tuple[str, str]]:
    """(path, file name) for the 15 edited pages, from the owner's manifest."""
    out = []
    for line in (BEFORE_DIR / "manifest.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            out.append((parts[2], parts[3]))
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_all() -> list[dict]:
    import httpx

    records = []
    with httpx.Client(timeout=40.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for path, name in before_manifest():
            stamp = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
            response = client.get(BASE + path)
            body = response.content
            before = (BEFORE_DIR / name).read_bytes()
            records.append(
                {
                    "path": path,
                    "name": name,
                    "fetched_at": stamp,
                    "status": response.status_code,
                    "bytes": len(body),
                    "sha256": sha256_bytes(body),
                    "same_as_before": sha256_bytes(body) == sha256_bytes(before),
                    "marker": MARKER in body.decode("utf-8", "replace").lower(),
                    "marker_before": MARKER in before.decode("utf-8", "replace").lower(),
                    "body": body,
                }
            )
            time.sleep(1.0)
    return records


def status() -> int:
    records = fetch_all()
    changed = sum(1 for r in records if not r["same_as_before"])
    marked = sum(1 for r in records if r["marker"])
    print(f"pages={len(records)} ok={sum(1 for r in records if r['status'] == 200)} bytes_changed_vs_before={changed} with_marker_now={marked} with_marker_before={sum(1 for r in records if r['marker_before'])}")
    return 0


def after() -> int:
    if AFTER_DIR.exists() and any(AFTER_DIR.iterdir()):
        raise SystemExit(f"{AFTER_DIR} already holds files. Not overwriting.")
    records = fetch_all()
    marked = sum(1 for r in records if r["marker"])
    if any(r["status"] != 200 for r in records):
        raise SystemExit("A page did not return 200. Nothing saved.")
    # The new FAQ sits on the Raken comparison page. Live means: that page shows it and most pages differ from the before copy.
    raken = next(r for r in records if "raken" in r["path"])
    changed = sum(1 for r in records if not r["same_as_before"])
    if not raken["marker"] or changed < 12:
        raise SystemExit(f"The edit does not look live yet (raken FAQ present: {raken['marker']}, pages changed: {changed}). Nothing saved.")
    AFTER_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    sums = []
    for r in records:
        (AFTER_DIR / r["name"]).write_bytes(r["body"])
        manifest.append(f"{r['fetched_at']} {r['status']} {r['path']} {r['name']}")
        sums.append(f"{r['sha256']}  {r['name']}")
    (AFTER_DIR / "manifest.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    (AFTER_DIR / "SHA256SUMS").write_text("\n".join(sorted(sums, key=lambda line: line.split()[1])) + "\n", encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    summary = [{k: v for k, v in r.items() if k != "body"} for r in records]
    (OUT / "after_fetch.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"saved {len(records)} pages to {AFTER_DIR}; with_marker={marked}; bytes_changed_vs_before={sum(1 for r in records if not r['same_as_before'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["status", "after", "predict"])
    args = parser.parse_args(argv)
    if args.command == "status":
        return status()
    if args.command == "after":
        return after()
    from scripts.claude import r3_pr45_predict

    return r3_pr45_predict.main()


if __name__ == "__main__":
    raise SystemExit(main())
