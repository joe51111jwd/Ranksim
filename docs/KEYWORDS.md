# Keyword volume and difficulty

US monthly search volume and a heuristic competitive score for a keyword list. The score is not a prediction of Google. A volume is blank when no source returns a number. Nothing in this tool estimates or fills in a search volume.

Every volume that is shown has a provider, a source URL, and a data date. `fetched_at` is when Ranksim stored the row. Keyword Planner figures such as 8,100 and 9,900 are the buckets the source published. They are shown as published.

## Commands

From the repo root:

```bash
python -m aeo.ranksim keywords volume --keywords "busybusy, hcss" --project SmartCrew
python -m aeo.ranksim keywords volume --file seeds.txt --project SmartCrew --out volume.csv
python -m aeo.ranksim keywords volume --file seeds.txt --project SmartCrew --json --refresh

python -m aeo.ranksim keywords difficulty --file seeds.txt --project SmartCrew
python -m aeo.ranksim keywords difficulty --keywords "hcss alternative" --project SmartCrew --json

python -m aeo.ranksim keywords report --file seeds.txt --project SmartCrew --out report.csv
python -m aeo.ranksim keywords import --csv tests/fixtures/keyword-seed/volumes.csv --project SmartCrew
```

`--project` stores the SQLite cache in that project's folder (`keyword_research.sqlite`). `--cache` is an explicit path. With neither, the cache is `data/projects/_keywords/keyword_research.sqlite`.

`keywords` with no subcommand still writes the SmartCrew `keywords.csv` gap list.

The web app has a Keywords page at `/keywords` (Volume, Report, Difficulty, and CSV import). JSON:

- `POST /api/keywords/volume`
- `POST /api/keywords/difficulty`
- `POST /api/keywords/report`

Body: `{"keywords": ["busybusy"], "project": "SmartCrew", "refresh": false}`.

On the box, with `SERPER_API_KEY` set in the environment and never printed:

```bash
python -m aeo.ranksim keywords import \
  --csv tests/fixtures/keyword-seed/volumes.csv \
  --project SmartCrew

python -m aeo.ranksim keywords volume \
  --file tests/fixtures/keyword-seed/priority.txt \
  --project SmartCrew \
  --out volume.csv

python -m aeo.ranksim keywords difficulty \
  --file tests/fixtures/keyword-seed/priority.txt \
  --project SmartCrew \
  --credit-cap 50

python -m aeo.ranksim keywords report \
  --file tests/fixtures/keyword-seed/priority.txt \
  --project SmartCrew \
  --out report.csv
```

`difficulty` prints a Serper credit estimate before it spends. One search with `num=10` is 1 credit (`search_credits` in the index credit helper). A keyword whose SERP is already cached for the UTC date costs nothing. The default cap is 50. `report` does not call Serper unless you pass `--fetch-serp`.

## Volume providers

Tried in order. The first usable number wins. A usable number has a provider, a source URL, and a data date.

1. seodata.dev. `GET https://api.seodata.dev/v1/keyword?q=<keyword>&country=us`. The public page is `https://www.seodata.dev/keyword/<slug>`, and that page says the figures are from the Google Ads API for the US. `cached_at` is the data date. HTTP 404, or a body with `volume` 0 and `cached` false, is no data. It is not stored as zero searches. If the API is unusable, the HTML page is parsed. The page states the same monthly volume, CPC, competition, and updated date.
2. Semrush public US overview pages, `https://www.semrush.com/website/<domain>/overview/`, only when the HTML contains a US organic keyword table and no captcha. A captcha or login wall stops further Semrush fetches for that run. Ranksim does not solve captchas. Domains come from `--semrush-domain`, `src/aeo/ranksim/kwresearch/semrush_overview_domains.txt`, and a narrow guess (`<word>.com` for a single word, or for a phrase that ends in a navigational word such as login). The keyword must appear in that table. A long tail with no overview to check stays blank for this provider.
3. Paid providers, only when the env vars are set. They are not called otherwise, and the values are never printed.
   - DataForSEO: `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD`. `POST https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live` with `location_code` 2840 (United States) and `language_code` en. The data date is the latest month in `monthly_searches`. A null `search_volume`, or a 0 with no monthly series, is no data.
   - Google Ads Keyword Planner: `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`, and `GOOGLE_ADS_CUSTOMER_ID`. Optional `GOOGLE_ADS_LOGIN_CUSTOMER_ID` and `GOOGLE_ADS_API_VERSION` (default `v26`). The call is `generateKeywordHistoricalMetrics` for geo `2840`, language `1000` (English), network `GOOGLE_SEARCH`, with `includeAverageCpc`. The volume is average monthly searches. The data date is the latest month in the series. CPC is `averageCpcMicros` only. Top-of-page bids are not treated as CPC. The source URL is the Keyword Planner historical metrics doc, so the customer id is not written into the row.
4. Imported CSV. `keywords import` writes the row into the cache and keeps `volume_source`, `source_url`, and `data_date` from the file. An imported number is kept until `--refresh` replaces it with a live number. If a refresh finds no live number, the imported number stays.

The sample file is `tests/fixtures/keyword-seed/volumes.csv` (pulled 2026-11-13). Some of those rows took a volume from a similar-keywords table on a related seodata.dev page. Import keeps that source URL and note. The live seodata provider does not copy a number from a different keyword.

Requests use `RanksimKeywordBot/1.0`, a per-host delay (default 1 second), and retries on 429 and 5xx. A 404 is not retried.

Cache TTL is 14 days for a live number and 1 day for a live blank. `--refresh` bypasses that.

## Difficulty formula

The score is an integer from 0 to 100. Higher means the top 10 looks harder for a new page. It is a heuristic. It is not a forecast of a Google rank.

Each factor has a raw value, a sub-score from 0 to 100, and a default weight. Factors that are missing or unknown are dropped. The weights of the factors that remain are divided by their sum, so they add up to 1. Coverage is the sum of the original weights of the factors that were present (the default weights add up to 1).

| Factor | Weight | Raw value | Sub-score |
| --- | --- | --- | --- |
| serp_composition | 0.30 | Counts of big-brand homepages, review/UGC sites, and other pages in the top 10 | Average of 100 per brand homepage, 70 per review/UGC result, 25 per other result |
| domain_authority | 0.25 | Tranco rank and Majestic Million GlobalRank for each ranking domain | Mean of the rank curve below. A domain on neither loaded list scores 15 and is flagged |
| exact_match | 0.15 | How many results contain the full keyword in the title or the URL slug | 100 times that count divided by the number of results |
| ai_overview | 0.10 | AI Overview field and cited domains | Empty field: 15. Present with no citations: 55. Otherwise 55 + 8 per cited domain, capped at 100 |
| cpc | 0.10 | CPC in USD from the volume source | `100 * log(1 + cpc) / log(1 + 50)`, capped at 100 |
| ads_competition | 0.10 | Ads competition from the volume source | Index 0 to 100 is used as-is. A 0 to 1 fraction is multiplied by 100. HIGH/MEDIUM/LOW are 90/55/25 |

Final score = the weighted mean of the present sub-scores, rounded to the nearest integer (halves round away from zero by adding 0.5 and taking the floor).

Brand homepage: the result URL is the site root (or a language root such as `/en/`), and the site is in `brand_homepages` in `src/aeo/ranksim/kwresearch/serp_composition.json`, or its Tranco or Majestic rank is 1,000 or better. Review and UGC sites are the `review_ugc` list in that same file (g2, capterra, getapp, softwareadvice, reddit, quora, youtube, linkedin, forbes, and the other domains listed there). A review/UGC domain is classified as review/UGC even when it is also a well known brand. Any other result is niche.

Authority lists are optional files. `--tranco` or `RANKSIM_TRANCO_PATH`, and `--majestic` or `RANKSIM_MAJESTIC_PATH`. If neither file is loaded, domain authority is dropped. It is not replaced with a guessed low score for every domain. Rank curve: 1 to 100 maps to 98, through 1,000 maps to 88, through 10,000 maps to 72, through 100,000 maps to 52, through 1,000,000 maps to 32, and anything weaker maps to 15. When both lists have the domain, the stronger (lower) rank is used.

AI Overview: the score reads `aiOverview` (or `ai_overview`) on the Serper JSON. If that field is absent, the factor is `unknown` and is dropped. A featured snippet or answer box is not treated as an AI Overview. Ranksim does not assume an AI Overview is present.

CPC and Ads competition come only from the volume row. If that row has neither, those factors are dropped.

The SERP is the existing Serper client: `POST https://google.serper.dev/search` with `gl=us`, `hl=en`, `num=10`. The response is cached per keyword and UTC date. A rerun the same day does not call Serper.

If no factor can be scored, the difficulty cell is blank and the reason says so.
