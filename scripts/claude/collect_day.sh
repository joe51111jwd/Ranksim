#!/usr/bin/env bash
# Daily Ranksim collection for the Claude goal work. Meant for a once-a-day scheduler.
#
#   Step 1  Free HTML re-fetch of every URL seen in a top-10 SERP in the last
#           RANKSIM_REFETCH_DAYS days (default 5). Plain HTTP, robots.txt obeyed,
#           3 s per-host delay, no Serper, no key. Appends dated rows to `pages`.
#           Prints aggregate counts only (held-out SERP days feed the URL list).
#   Step 2  Prints, but does NOT run, the three SERP pull commands. Those spend
#           Serper credits and belong to the lead (scripts/claude/pull_serps.py).
#
# Usage:   scripts/claude/collect_day.sh
# Cron (America/New_York box clock), 06:15 every day:
#   15 6 * * * /workspace/ranksim-claude/scripts/claude/collect_day.sh >> /workspace/ranksim-claude/evals/claude/pipeline/cron.log 2>&1
# This box has no crontab binary. See evals/claude/pipeline/notes.md for a loop alternative.
set -euo pipefail

REPO="${RANKSIM_REPO:-/workspace/ranksim-claude}"
DAYS="${RANKSIM_REFETCH_DAYS:-5}"
QUERIES="${RANKSIM_PULL_QUERIES:-/workspace/ranksim-data/queries/smartcrew-30-original.txt}"
TODAY="$(TZ=America/New_York date +%F)"
LOG_DIR="$REPO/evals/claude/pipeline"

cd "$REPO"
# shellcheck disable=SC1091
source "$REPO/.venv/bin/activate"
mkdir -p "$LOG_DIR"

echo "== $(date -u +%FT%TZ) collect_day $TODAY: step 1, HTML re-fetch (last $DAYS days of top-10 URLs)"
python scripts/claude/refetch_html.py --since-days "$DAYS" --label "daily-$TODAY"

echo "== step 2, SERP pulls (not run here; they spend Serper credits). Commands:"
for label in pull-a pull-b pull-c; do
  echo "python scripts/claude/pull_serps.py --label $label --queries $QUERIES --date $TODAY"
done
