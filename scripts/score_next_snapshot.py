"""Score the next Serper snapshot with the frozen full-coverage predictor.

Does not refit. Does not edit frozen files. Appends PREDICTED and REAL rows
to the track-record ledger.

    python scripts/score_next_snapshot.py \
        --snapshot-csv path/to/serp.csv \
        --html-dir path/to/html \
        --observed-on YYYY-MM-DD
"""

from __future__ import annotations

import sys

from aeo.ranksim.full_coverage import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
