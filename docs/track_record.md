# Prediction track record

The track record is how a PREDICTED rank is checked later against a REAL Google snapshot. It is not a live rank, and it is not a promise about future accuracy.

## What gets stored

`python -m aeo.ranksim track-record record` appends one row to `data/track-record.csv` (or `--ledger`). The row stores the time, query, URL, predicted rank, how many candidates that rank was out of, confidence, and a note that the value is PREDICTED. The default confidence is `UNSURE`. `data/` is gitignored. Nothing here calls a search API.

```bash
python -m aeo.ranksim track-record record \
  --query "example software" \
  --url "https://example.com/docs" \
  --predicted-rank 2 \
  --predicted-of 6 \
  --confidence UNSURE
```

## How it is scored

`track-record score` reads that predictions file and a later REAL file with `query,url,position`. Agreement is pairwise, and only for URLs that appear in both files for the same query. The REAL positions decide which URL was actually ahead. They are not model features, and this command does not train.

If the overlap has no pair, the command says no pairwise accuracy is reported. A number from a handful of pairs is still only that overlap. It is not a broad accuracy claim. `llm_relevance` is not part of this score and is not a default feature.

```bash
python -m aeo.ranksim track-record score data/track-record.csv later.csv
```

## Browser JSONL

Authorized local captures can be turned into the REAL file shape:

```bash
python -m aeo.ranksim import-serp capture.jsonl --out later.csv
```

A JSONL row has `q`, `organic`, and `ts`. An organic item is often `[breadcrumb, title, snippet]`. Only text that already contains `http://` or `https://` is kept. Position is the 1-based place in that organic list. Items with no http URL are skipped. The command does not invent a URL and does not fetch one. A CSV or JSON file that already has `query`, `url`, and `position` is accepted the same way. Do not commit client captures.
