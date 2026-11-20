# Round 4 data inventory: what is on this box for the master plan

Written 2026-11-20 by a read-only research subagent. Every count below was computed on this box from the raw store, the index, the repo and the files named. The queries are in the appendix. Nothing was fitted or scored. No accuracy of any signal against any ranking was computed. Zero Serper credits were spent and no Serper host was called. No held-out list of plan `heldout1` exists, and none was read. The only file written is this memo (plus throwaway data in /tmp). Nothing under /workspace/ranksim-data was written. No outside source was needed for this topic, so every number is graded MEASURED (local, read-only).

Words used below. **c100** is the 100 construction searches (the original 30 plus the 70 added on Nov 16). **o30** and **a70** are those two parts. **p35** is the 35 PR 41 baseline searches, **cs4** the 4 ClockShark searches, **rev15** the 15 Revorion searches (revenue recovery, a different niche). **Readable** means the r3 page store rule: a good plain-HTTP or browser fetch (status 200 or empty, at least 500 bytes) whose parsed main text has at least 50 words, using every fetch stored through now. **Platform** means user-content sites (reddit, youtube, facebook, quora, linkedin, instagram, x, medium, wikipedia) or review and listing aggregators (g2, capterra, softwareadvice, getapp, trustradius, sourceforge, slashdot, itqlick, spotsaas, softwareconnect, crozdesk, saasworthy, softwaresuggest, glassdoor, indeed, apple, google, forbes).

## What this means for the master plan

1. **Cross-site training pairs exist in useful numbers, but only as top-10 and top-30 lists.** The c100 consensus top 10s hold 3,784 distinct cross-site (search, URL pair) items with both pages readable, over 484 readable URLs, across all days through Nov 19. Per day it is about 550 to 800 for o30 and 1,000 to 1,400 for a70. The Nov 16 deep list adds 16,264 readable cross-site pairs down to position 30 on one day. Plan the site-strength step on these, and say in the plan that one day of top-30 depth is all there is.
2. **A global authority list cannot be the site-strength signal for a small vendor.** Majestic Million and Tranco top-1m are on disk (`/workspace/kw-sample-2026-11-13/`). They cover 242 and 244 of the 428 c100 top-10 domains (about 72% of rows). The client's own domain is in neither. The local link graph is 97.6% internal links: only 94 of 428 top-10 domains receive any cross-domain edge in it. Common Crawl ranks exist only as cached values for 135 domains. So the plan needs a niche-internal standing signal that a small site can have a value for. The cheapest one already measured on this box is SERP footprint: how many searches and search groups list the domain. It must be computed leave-the-search-group-out to avoid leakage. `src/aeo/ranksim/site_history.py` already implements a version of it.
3. **Platforms need their own path.** In c100 top-10 rows, 22.3% are user-content platforms and 10.0% are review aggregators. Only 22.7% and 44.4% of those rows are readable, against 88.9% for ordinary vendor and publisher sites. A content scorer cannot read most of them. Give them a fixed-slot or history prior and report every result with and without them.
4. **The unused test2 sites are thin for a cross-site test.** 91 of the 145 test2 sites appear in an ordinary top 10 through Nov 19. They sit on 94 searches, in 199 (search, site) combinations, 86 of them on test search groups. 54 of the 91 appear on one search only. Plan the cross-site test unit as held-out search groups (52 test groups, 63 searches) with the test2 sites as a stricter slice, not as test2 sites alone.
5. **Finish the round 3 freeze before anything else touches the scorer.** `evals/preregistered/claude-r3-samesite/manifest.json` does not exist yet, and the branch is one commit ahead of origin (unpushed). Exact remaining steps and commands are in section 10. The test costs 208 credits.
6. **The PR 45 ledger is blocked on that freeze and is weaker than the brief implies.** `r3_pr45.py predict` refuses to run until the r3 manifest verifies. No ledger has been written. Three of the nine PR 45 target searches (`raken alternatives`, `free alternative to raken`, `construction software per user pricing`) were never pulled before the merge, so they can never have a before rank. The FAQ marker phrase was found on 1 of the 15 after pages, not 15.
7. **Do not plan new full three-pull consensus days of the 100 searches.** One costs 300 credits. With 617 credits, a 250 reserve, about 90 a day used by the other team and 208 owed to the round 3 test, there is no room. The freshest cross-site truth is Nov 19: a70 with three pulls, o30 with two or three.
8. **A free fetch pass would lift the a70 data most.** Only 59.0% of a70 Nov 19 top-10 rows are readable, against 74 to 79% for o30 days. Those pulls came after the Nov 19 refetch. One plain-HTTP pass over Nov 19 URLs without text costs no credits.
9. **Reuse before writing new code.** Pairwise-to-position aggregation (Copeland, Bradley-Terry, window choice) is already in `position_aggregate.py`. Off-page loaders are in `offpage.py` and `pair_order.py`. The frozen content scorer has a save and load API (`r3_model.FrozenScorer`). Hash-chained prediction ledgers are in `ledger.py` and `draft_ledger.py`. Bootstrap intervals are in `scripts/claude/metrics.py`. Section 8 lists entry points and tests.
10. **Fix four tooling traps before a coding agent starts.** `pyarrow` is not installed in the repo venv, so the index parquet files cannot be read there. `index.sqlite` holds the same tables with the same row counts. `r3_pages.load_pages` writes its parse cache under /workspace/ranksim-data, so a read-only agent must not call it. `scripts/claude/t1b_postmortem.py` is untracked although its outputs are committed. `evals/claude/r3/t1-postmortem/notes.md`, which theory R3.8 cites, does not exist.
11. **First-party data for the client exists and no round 3 script reads it.** Search Console exports are in `/workspace/seo-tracker/smartcrew/gsc-export/` (last 3 months: 429 queries, 107 pages, with clicks, impressions and average position) and `gsc-compare-2026-11-16/` (580 queries, two 4-day windows). No file in `scripts/claude/` reads them. Three product modules mention Search Console. This is the only standing evidence for the client's own site, and it could anchor the client's site-strength value.

## 1. Ordinary SERP data

**For the plan:** cross-site truth is dense only for o30 (Nov 6 to Nov 19). a70 has two days (Nov 16 with one pull, Nov 19 with three pulls). Nothing is dated Nov 20 yet.

Raw store `/workspace/ranksim-data/ranksim_raw.sqlite` (3.97 GB, append-only, update and delete triggers raise):

| Table | Columns | Rows |
| --- | --- | ---: |
| serp_results | id, query, snapshot_date, fetched_at_utc, gl, hl, num, position, url, title, snippet, raw_json, source | 14,096 |
| serp_raw | id, query, fetched_at_utc, gl, hl, num, endpoint, provider, request_params, raw_json, http_status, credits, source, snapshot_date | 1,433 requests (1,433 credits recorded) |
| pages | id, url, fetched_at_utc, fetch_method, http_status, content_type, html, sha256 | 12,777 fetches, 10,632 URLs, 11,028 with HTML of 500 bytes or more |

Top-10 pulls by day and query set, as `rsdata.load_rows("2026-11-19")` returns them (keyword-sample and smoke rows are skipped by rsdata):

| Day | Set | Searches | Pulls per search | Rows | Sources |
| --- | --- | ---: | --- | ---: | --- |
| Nov 6 | o30 | 31 | 1 | 304 | smartcrew-snapshot |
| Nov 9 | o30 | 30 | 1 | 299 | smartcrew-snapshot |
| Nov 11 | o30 | 30 | 1 | 296 | smartcrew-snapshot |
| Nov 13 | o30 | 30 | 1 | 298 | smartcrew-snapshot |
| Nov 14 | keyword sample | 17 | 1 | 150 | skipped by rsdata |
| Nov 16 | o30 | 30 | 4 (morning, q100, noise-a, noise-b) | 1,197 | consensus uses the 3 evening pulls |
| Nov 16 | a70 | 70 | 1 | 689 | smartcrew-q100 |
| Nov 16 | p35 | 35 | 1 | 324 | daily:2026-11-16 |
| Nov 16 | rev15 | 15 | 1 | 144 | revorion-snapshot |
| Nov 16 | deep | 100 | 270 page requests | 2,678 | `:abspos` rows, positions 1 to 30 |
| Nov 17 | o30 | 30 | 3 | 895 | daily pull-a, b, c |
| Nov 17 | cs4 | 4 | 1 | 34 | daily:2026-11-17 |
| Nov 17 | rev15 | 15 | 1 | 146 | |
| Nov 18 | o30 | 30 | 2 to 3 (75 pulls) | 746 | daily:2026-11-18 |
| Nov 18 | rev15 | 15 | 1 | 146 | |
| Nov 19 | o30 | 30 | 2 to 3 (84 pulls) | 831 | daily:2026-11-19 |
| Nov 19 | a70 | 70 | 3 | 2,064 | claude-daily pull-a, b, c |
| Nov 19 | rev15 | 15 | 1 | 147 | |

- The 31st o30 search is `all-in-one heavy civil software`, pulled only on Nov 6.
- The last ordinary top-10 fetch is 2026-11-19T20:56Z (15:56 ET), before the PR 45 merge at about 20:00 ET. No ordinary list after the merge is stored.
- Round 3 site-restricted pulls are in `serp_raw` only (sources `claude-r3:validity-a`, `train-a`, `noise-a`, 274 requests) and never in `serp_results`.

Query sets on disk:

| File | Searches | Notes |
| --- | ---: | --- |
| /workspace/ranksim-data/queries/smartcrew-100.txt | 100 | o30 first, then a70 |
| /workspace/ranksim-data/queries/smartcrew-100-manifest.csv | 100 | idx, query, origin |
| /workspace/ranksim-data/queries/smartcrew-30-original.txt | 30 | |
| /workspace/ranksim-data/queries/smartcrew-pr41-baseline.txt | 35 | p35, no overlap with the 100 |
| /workspace/ranksim-data/queries/smartcrew-clockshark.txt | 4 | cs4, no overlap |
| evals/claude/queries/smartcrew-a70.txt | 70 | identical to the 100 minus the 30 |
| /workspace/seo-tracker/smartcrew/serp_google_2026-10-29.csv | 16 | 135 rows, positions 1 to 10, not in the raw store as a day series |
| /workspace/seo-tracker/smartcrew/serp_google_neutral_full_2026-10-30.csv | 144 | client position and top-1 domain only |
| /workspace/seo-tracker/smartcrew/rank-baseline-2026-11-10.csv, rank-postfix-2026-11-10.csv | 120 | rank columns from several engines |
| /workspace/seo-tracker/revorion/serp_google_2026-11-16 to 11-19.csv | 15 | the rev15 pulls, also in the raw store |

`scripts/claude/rsdata.py`: `load_rows(max_date)` returns every SERP row with `snapshot_date <= max_date` (asserted), drops the page-relative deep copy and the keyword-sample and smoke sources, and labels `kind` as `top10` or `deep`. `consensus_table` takes the median over the day's main session of pulls (absent counts 11, a strict majority needed for the top 10). `query_groups` labels o30, a70, p35, rev, other. `load_page_index(max_date)` seals page fetches at max_date plus one day, 05:00 UTC. The design seal is `DESIGN_MAX_DATE = "2026-11-17"`. Nov 18 and Nov 19 have since been scored once and are now design data (RESULTS-claude.md 4.3).

Snapshot folders: `2026-11-18-pull-c` holds only a query manifest (15 of its searches made it into the store). `2026-11-19-pull-c` holds a manifest and no HTML (24 searches in the store). Nothing is dated 2026-11-20.

## 2. Cross-site material

**For the plan:** about 430 domains in c100, most seen on one search. A few incumbents and platforms dominate rows. Readable cross-site pairs number in the thousands, not tens of thousands, outside the deep list.

Domains (registrable, `site_key`) in ordinary top-10 rows, all days through Nov 19:

| Scope | Searches | Search groups | Rows | URLs | Domains | Domains on 1 / 2 / 3 to 5 / 6 to 10 / more than 10 searches | Domains in 1 / 2 / 3 to 5 / 6 to 10 / more than 10 groups |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| c100 | 101 | 33 | 7,619 | 1,209 | 428 | 256 / 64 / 50 / 27 / 31 | 313 / 56 / 39 / 11 / 9 |
| construction (c100, p35, cs4) | 140 | 63 | 7,977 | 1,409 | 458 | 261 / 78 / 51 / 28 / 40 | 320 / 67 / 41 / 13 / 17 |
| all 155 | 155 | 71 | 8,560 | 1,643 | 621 | 396 / 97 / 59 / 29 / 40 | 476 / 71 / 42 / 15 / 17 |

Largest c100 domains by searches (searches, groups, rows): reddit.com 95, 31, 847; youtube.com 70, 18, 572; hcss.com 62, 11, 631; the client 60, 13, 525; facebook.com 38, 13, 196; capterra.com 37, 12, 139; g2.com 32, 19, 145; stackct.com 31, 3, 214; softwareadvice.com 29, 17, 109; myquoteiq.com 28, 3, 275; agtek.com 25, 2, 209; trimble.com 25, 13, 167.

Readability: 70.3% of c100 top-10 rows are readable, but only 51.0% of distinct c100 URLs (frequent URLs are better covered). Of all 2,322 SERP URL keys (top 10 and deep), 1,537 have a good fetch and 1,298 are readable.

Rows by kind, c100 raw top-10 rows (7,619):

| Kind | Rows | Share | Readable share |
| --- | ---: | ---: | ---: |
| Ordinary sites | 4,633 | 60.8% | 0.889 |
| User-content platforms | 1,700 | 22.3% | 0.227 |
| Review and listing aggregators | 761 | 10.0% | 0.444 |
| Client site | 525 | 6.9% | 0.981 |

Platform rows: reddit 847, youtube 572, facebook 196, g2 145, capterra 139, softwareadvice 109, slashdot 74, trustradius 62, itqlick 58, quora 31, linkedin 30, spotsaas 30, google 24, softwareconnect 21, sourceforge 19, glassdoor 18, indeed 17, apple 15, forbes 15, wikipedia 13, medium 6, crozdesk 5, getapp 5, instagram 4, saasworthy 3, softwaresuggest 2, x 1.

Cross-site pairs per day, consensus top 10 (rsdata rule), pairs of URLs from different domains:

| Day | Set | Lists | Rows | Readable rows | All pairs | Cross-site | Both readable | Both readable, strict order | Both readable, no platform, no client | Same-site both readable |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Nov 6 | o30 | 31 | 304 | 0.753 | 1,341 | 1,311 | 744 | 744 | 504 | 7 |
| Nov 9 | o30 | 30 | 299 | 0.756 | 1,341 | 1,317 | 767 | 767 | 507 | 9 |
| Nov 11 | o30 | 30 | 296 | 0.787 | 1,314 | 1,293 | 798 | 798 | 536 | 8 |
| Nov 13 | o30 | 30 | 298 | 0.768 | 1,332 | 1,308 | 781 | 781 | 440 | 12 |
| Nov 16 | o30 | 30 | 271 | 0.764 | 1,132 | 1,097 | 643 | 636 | 367 | 13 |
| Nov 16 | a70 | 70 | 689 | 0.695 | 3,054 | 2,961 | 1,393 | 1,393 | 888 | 53 |
| Nov 16 | p35 | 35 | 324 | 0.596 | 1,365 | 1,256 | 411 | 411 | 307 | 78 |
| Nov 16 | rev15 | 15 | 144 | 0.632 | 623 | 622 | 250 | 250 | 244 | 1 |
| Nov 17 | o30 | 30 | 269 | 0.773 | 1,123 | 1,092 | 657 | 651 | 346 | 14 |
| Nov 17 | cs4 | 4 | 34 | 0.647 | 141 | 135 | 60 | 60 | 47 | 2 |
| Nov 17 | rev15 | 15 | 146 | 0.596 | 640 | 639 | 229 | 229 | 229 | 0 |
| Nov 18 | o30 | 29 | 246 | 0.760 | 986 | 948 | 556 | 545 | 308 | 18 |
| Nov 18 | rev15 | 15 | 146 | 0.623 | 640 | 629 | 241 | 241 | 241 | 10 |
| Nov 19 | o30 | 29 | 243 | 0.794 | 955 | 928 | 583 | 571 | 321 | 16 |
| Nov 19 | a70 | 70 | 631 | 0.612 | 2,618 | 2,482 | 1,040 | 1,029 | 733 | 30 |
| Nov 19 | rev15 | 15 | 147 | 0.415 | 649 | 627 | 155 | 155 | 155 | 0 |

- Distinct over all days, c100: 3,784 cross-site (search, URL pair) items with both pages readable, on 484 readable URLs of 802 consensus top-10 URLs. Most per-day pairs repeat from day to day.
- Same-site pairs in ordinary top 10s stay small (7 to 53 a day). This is why round 3 moved to site-restricted lists.

## 3. Deep lists

**For the plan:** there is exactly one top-30 day (Nov 16, the 100 searches). Every other stored pull stops at 10.

- Source `smartcrew-index-deep:2026-11-16:abspos`: 270 requests (page 1 sent with num=100 but returned 10 results, then page 2 for 100 searches and page 3 for 70), 2,678 rows, absolute positions 1 to 30. 70 searches reach position 30. 30 stop at about 20 (lowest maximum 19). After keeping each URL's best position per search: 2,516 rows, 1,376 URLs, 513 domains.
- By band: positions 1 to 10, 985 rows, 216 domains, 71.1% readable. 11 to 20, 910 rows, 305 domains, 73.8% readable. 21 to 30, 621 rows, 272 domains, 73.4% readable.
- Cross-site pairs inside the deep lists: 30,459, of which 16,264 have both pages readable.
- The client domain is in the deep top 30 for 61 searches and in some c100 top 10 for 60.
- No other pull goes beyond 10. In `serp_raw` every other request is page 1 with num 10. The round 3 site-restricted lists are page 1 only.
- Trap: the source without `:abspos` holds page-relative positions 1 to 10. rsdata drops it. The index `candidates` table also stores the deep snapshot with page-relative positions (2,516 rows all labeled band 1-10). Do not take deep positions from the index.

## 4. Site-restricted lists already pulled

**For the plan:** about 5,600 readable same-site pairs on about 100 train-fold sites. Most of these sites also show in ordinary top 10s, so the same sites can link the same-site scorer to cross-site data.

Counted from the chunk files in `evals/claude/r3/serp/` with `r3_data.load_label`, `load_requests` and `list_pairs`. Readability uses the rule above with every stored fetch, so it differs a little from the frozen 5,623 pairs (which use the r3 cell and fetch rules).

| Label | Requests | Lists with results | Sites | Searches | Site folds | Own-site rows | Distinct pages | Readable pages | Own-site pairs | Both readable | Lists with a readable pair | Sites with a readable pair |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| validity-a | 70 | 70 | 36 | 45 | 25 train, 11 outside universe | 620 | 550 | 259 | 2,602 | 1,004 | 44 | 26 |
| train-a | 180 | 169 | 100 | 76 | 100 train | 1,469 | 1,172 | 972 | 6,101 | 4,669 | 146 | 81 |
| noise-a (repeat of 24 validity lists) | 24 | 22 | 24 | 20 | 23 train, 1 outside | 186 | 186 | 134 | 766 | 466 | 18 | 18 |

Overlap with ordinary data on the same search:

| Label | (search, site) also in an ordinary top 10 of that search, any day | Also in the Nov 16 deep top 30 | Sites in any ordinary top 10 | Own-site pages also in that search's ordinary top 10 |
| --- | --- | --- | --- | ---: |
| validity-a | 62 of 70 | 70 of 70 | 36 of 36 | 72 |
| train-a | 100 of 180 | 146 of 180 | 83 of 100 | 91 |
| noise-a | 19 of 24 | 24 of 24 | 24 of 24 | 22 |

The 11 validity sites outside the universe are platform or unreadable sites (the universe drops them). validity-a has low readability (47%) because it includes platform sites.

## 5. The round 3 universe split

**For the plan:** test2 is the only untouched site fold. It is small in ordinary top 10s. The test search groups are the larger unseen resource.

`evals/claude/r3/plans/universe.json`: 2,004 (search, site) combinations, 579 sites, 155 searches, 71 search groups, seed 20261119, design max date 2026-11-17. Sites: train 237, test1 197, test2 145. Groups: 19 train (92 searches), 52 test (63 searches).

| Site fold | Group fold | Combos | Sites | Searches | Groups |
| --- | --- | ---: | ---: | ---: | ---: |
| train | train | 931 | 171 | 92 | 19 |
| train | test | 210 | 111 | 60 | 50 |
| test1 | train | 366 | 135 | 87 | 19 |
| test1 | test | 177 | 93 | 59 | 48 |
| test2 | train | 221 | 101 | 72 | 19 |
| test2 | test | 99 | 62 | 45 | 34 |

Each fold in ordinary top-10 rows through Nov 19:

| Site fold | Sites | Sites in an ordinary top 10 | (search, site) combos | Searches | Rows | Sites on 1 / 2 / 3 to 5 / 6 to 10 / more than 10 searches | Combos on test groups | Sites on Nov 18 to 19 | Sites on c100 searches |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| train | 237 | 184 | 779 | 152 | 3,537 | 102 / 27 / 22 / 14 / 19 | 179 | 128 | 137 |
| test1 | 197 | 129 | 344 | 136 | 1,008 | 77 / 26 / 14 / 5 / 7 | 148 | 94 | 87 |
| test2 | 145 | 91 | 199 | 94 | 593 | 54 / 21 / 9 / 4 / 3 | 86 | 64 | 59 |

- 217 of the 621 domains in ordinary top 10s are outside the universe: platforms, the client, sites plain HTTP never read, and sites first seen on Nov 18 or 19.
- The held-out plan `heldout1` (meta only read): 104 requests, 93 sites, 46 searches, sha256 `055e6a71...`. Plan `test1` (132 requests) is marked superseded and was never pulled.

## 6. The local page index

**For the plan:** the index is a text store, not a link graph of the web. Its link data is almost all internal.

Location `/workspace/ranksim-data/index/`. The repo venv has no `pyarrow`, so I read the parquet footers by hand for counts and used `index.sqlite` for content. They match.

| Table | Rows | Columns |
| --- | ---: | --- |
| pages | 10,450 | url, final_url, canonical, title, meta_description, headings, main_text, word_count, schema_types, content_hash, published, updated, source, captured_on, status, miss_reason, attempts, pagerank, domain_rank, domain, anchor_text, inlinks, outlinks, table_count |
| edges | 178,448 | src, dst, anchor |
| domain_stats | 577 | domain, pages, full_text, outlinks, inlinks, mean_pagerank |
| candidates | 47,100 | url, query, position, source, snapshot_id, rank_band |
| builds | 3 | build log |

- pages: 8,190 full, 2,260 missing; 7,612 with at least 50 words; 581 domains; sources http 7,695, snapshot_html 439, wayback 138, browser 96, serper_scrape 16, empty 2,066. 8,175 captured on 2026-11-16. Main miss reasons: no archive capture 856, not fetched 786, empty archive capture 265, thin 194.
- edges: 174,203 internal (97.6%), 4,245 cross-domain, from 171 source domains to 117 target domains, 491 distinct domain pairs. 94 of the 428 c100 top-10 domains receive at least one cross-domain edge (median 2 linking domains, maximum 39).
- domain_stats: per-domain page count, full-text count, outlink and inlink counts inside this crawl, and the mean of a PageRank over this crawl (values near 0.0001). 331 of the 428 c100 top-10 domains are in it.
- candidates: 40,037 internal-link candidates without a label, 100 project-site rows, 985 Serper live rows, and snapshot rows for Nov 6 to Nov 16 (1 to 10 only).
- Home pages: 164 of the 428 c100 top-10 domains have a good home-page fetch in the raw store (213 of 621 for all 155). The index alone has 55 with at least 50 words. Round 3 fetched homes only for validity, train and test1 sites.
- Raw store extras: 138 Wayback captures (138 URLs, dated 2015 to 2026-11-16) stored by the earlier team. web.archive.org cannot be reached from this box now.
- coverage.json reports 2,871 labeled rows with 81.1% text and 10,450 URLs with 78.4% text.

## 7. Off-page files and off-page code

**For the plan:** two free million-domain lists are on disk. Common Crawl is not. Paid sources stay off.

| File | Contents | Coverage |
| --- | --- | --- |
| evals/preregistered/forward-pair-2026-11-16/offpage.tsv | 135 domains; tranco_popularity (80 filled), majestic_log (83), cc_pagerank (128), cc_harmonic (128) | domains of the five o30 snapshots through Nov 16: 97.5% of o30 rows through Nov 19, 86.8% of c100 rows, 80.6% of all 155 |
| /workspace/kw-sample-2026-11-13/majestic_million.csv | 1,000,000 domains with GlobalRank, RefSubNets, RefIPs | 242 of 428 c100 domains (73.1% of rows); train 112 of 237, test1 99 of 197, test2 69 of 145 sites |
| /workspace/kw-sample-2026-11-13/top-1m.csv | Tranco, 1,000,000 domains | 244 of 428 c100 domains (72.1% of rows); train 111, test1 95, test2 68 |
| Common Crawl domain rank file | not found on disk (only the cached values in offpage.tsv) | |
| Open PageRank, Moz | not found | |

Neither list contains the client domain. `scripts/run_pair_experiments.py` already looks for both files.

What the code implements:

- `src/aeo/ranksim/authority.py` (58 lines): `parse_backlink_rank`, `DataForSeoAuthority`, `authority_client(settings)`. A DataForSEO backlink rank client. It is a paid service and stays off without a key. Do not use.
- `src/aeo/ranksim/offpage.py` (145 lines): `load_majestic_million(path)` gives domain to log1p referring subnets. `load_tranco_popularity(path)` gives domain to 1/log2(rank+1). `load_cc_pagerank(path)` reads a local Common Crawl rank file. `open_pagerank_scores` needs a free signup key and is off. `signal_tables` bundles them. A domain the source did not list stays missing.
- `src/aeo/ranksim/linkgraph.py` (284 lines): `crawl_link_graph`, `collect_graph(settings, labels, saved_html=..., our_domain=...)`, `attach_graph`, `save_graph`, `load_graph`, `extract_anchors`. A polite crawl of fetched pages and their outbound links to a set depth. Its docstring says in-crawl inlinks are not web backlinks.
- `src/aeo/ranksim/graph.py` (51 lines): `pagerank(edges)` and `domain_pagerank(edges)` over the crawled links only.
- `src/aeo/ranksim/site_history.py` (384 lines): `site_key`, `url_key`, `SiteIndex`, `fit_site_history`, `site_history_position`, `site_history_backtest`. A content-blind slot from how many of the project's other searches list the site in their top 10, fit by a pairwise logistic model.

## 8. Code a later coding agent would reuse

**For the plan:** the pieces for "content score plus site term, then pairs to positions" all exist. What is missing is the site term and the joining code.

| File | What it does | Entry points | Tests |
| --- | --- | --- | --- |
| src/aeo/ranksim/whatif.py (1,033 lines) | Before and after LambdaMART scores for saved HTML against the crawled labeled set | `overrides_from_html`, `score_whatif`, `score_many`, `render_whatif`, `render_three_way`, `render_regression`; used by the CLI `test` commands | tests/test_whatif.py (2) |
| src/aeo/ranksim/new_page.py (1,071 lines) | Scores for a page with no history: domain prior, content and URL z-scores, a calibrated blend, frozen gates | `domain_prior_scores`, `content_query_scores`, `choose_blend_weight`, `evaluate_held_out`, `write_new_page_ledger`, `main` (CLI `new-page-eval`) | tests/test_new_page.py |
| src/aeo/ranksim/pair_order.py (1,685 lines) | The current product ranker: snapshot loading, HTML parse, off-page cache, history transitions, many model classes | `load_serp_snapshot`, `parse_html`, `load_offpage_cache`, `build_transition`, `fit_experiment`, `load_model`, `Offpage` | tests/test_pair_order.py, test_position_metrics.py, test_position_aggregate.py |
| src/aeo/ranksim/position_aggregate.py (376 lines) | Pairwise win probabilities to positions, with a 3-wide window | `pairwise_probability_matrix`, `copeland_scores`, `borda_scores`, `bradley_terry_scores`, `aggregate_positions`, `choose_window_shift` | tests/test_position_aggregate.py |
| src/aeo/ranksim/bands.py (329 lines) | Position bands, Winkler score, width chosen on earlier dates | `observed_band`, `model_band`, `persistence_band`, `collect_bands`, `choose_band_rule` | tests/test_bands.py |
| src/aeo/ranksim/hybrid.py (107 lines) | Persistence for seen URLs, model insertion for new or changed URLs | `insert_into_persistence`, `new_url_scores`, `three_wide`, `bootstrap_difference` | tests/test_hybrid.py |
| src/aeo/ranksim/draft_ledger.py (77 lines) | Freezing a predicted position for an unpublished page | `text_sha256`, `prediction_sha256`, `score_dates`, `missing_queries` | tests/test_draft_ledger.py |
| src/aeo/ranksim/ledger.py (362 lines) | Append-only, hash-chained prediction ledger in the app database | `record_prediction`, `record_outcome`, `verify_ledger`, `score_ledger` | tests/test_ledger_workspace.py, test_forward_eval.py, test_new_page.py and others |
| scripts/claude/r3_model.py (414 lines) | Same-site scorers (bm25f, vote, logit, gbm, blend), site-held-out CV, two-way bootstrap | `FrozenScorer.fit`, `.save`, `.load`, `.score_parts`, `.score`; `cross_validate`, `accuracy`, `difference`, `twoway_interval` | tests/test_claude_r3_core.py |
| scripts/claude/r3_features.py (238 lines) | History-free features (text, URL, shape, site tiers), BM25F with a frozen corpus | `static_page`, `lexical_features`, `query_flags`, `site_structure` | tests/test_claude_r3_core.py |
| scripts/claude/r3_semantic.py (958 lines) | CPU onnxruntime cross-encoders (MiniLM L6, L12) and bge-small embeddings, pinned, cached | `ensure_models`, `verify_models`, `cross_scores`, `cross_scores_pairs`, `embed`; CLI `download`, `verify`, `bench` | tests/test_claude_r3_semantic.py (20) |
| scripts/claude/r3_dataset.py (264 lines) | One row per (list, page) with features; pair table; scoring of arbitrary pages | `build_table`, `pair_table`, `table_for_pages`, `search_groups`, `make_df_table` | none found |
| scripts/claude/r3_score.py (427 lines) | Held-out two-pull scoring with guards | `main` (`--prereg`) | tests/test_claude_r3_core.py |
| scripts/claude/r3_pr45.py (147 lines) | PR 45 status check, after-HTML save, predict | CLI `status`, `after`, `predict` | none found |
| scripts/claude/r3_pr45_predict.py (146 lines) | Before vs after scores per target search, call rule plus or minus 0.10 list SD | `main` (via `r3_pr45.py predict`) | none found |
| scripts/claude/metrics.py (99 lines) | band3, err3, query bootstrap of rates and differences | `band3_hit`, `err3_hit`, `bootstrap_rate`, `bootstrap_difference` | tests/test_claude_heldout.py |

Also relevant: `r3_pages.py` (page store, no tests, writes a cache under /workspace/ranksim-data), `r3_data.py` (chunk loader, tested), `r3_plans.py` (plans, no tests), `r3_pull.py` (tested with `--no-git`), `r3_fetch.py`, `r3_fit.py`, `freeze.py` (tested), `rsdata.py` (tests/test_claude_rsdata.py, 5).

## 9. The live-edit ledgers

**For the plan:** neither ledger has an outcome yet. PR 41 is history-model output. PR 45 is not written.

PR 41 (`evals/draft-ledger/smartcrew-pr41.md` and `.jsonl`, not modified):

- 147 JSONL rows, all labeled PREDICTED: 143 `frozen`, 4 `no_baseline` (the four bare ClockShark searches). 115 rewrites, 32 new URLs. 74 distinct searches, 17 pages.
- Two freezes: 69 rows at head `cbb50da` (frozen 2026-11-17T01:07Z and 01:24Z, before the merge) and 78 rows at merge head `c9b0eae` (publish about 2026-11-18T03:07Z).
- Model: `model-heldout-nov16.json`, the history-heavy pair ranker (94% of gain from history per RESULTS 3.5).
- Score dates: publish plus 7 (Nov 25, inside the core update), plus 14 (Dec 2), plus 28 (Dec 16). No outcome field exists in any row.
- The markdown header still says "PR 41 is not merged". A later section of the same file records the freeze at merge.

PR 45:

- Saved: `/workspace/ranksim-data/snapshots/2026-11-19-pr45-before-html/` (15 pages, manifest, SHA256SUMS, 18:57 ET) and `-after-html/` (15 pages, 20:10 ET). `evals/claude/r3/pr45/after_fetch.json` records 15 of 15 pages with status 200, 15 of 15 changed from before, the marker phrase "free alternative to raken" on 1 of 15 after pages and 0 before.
- Frozen: nothing. `evals/claude/r3/pr45/ledger.json` does not exist. `r3_pr45_predict.main` first calls `verify_manifest` on `evals/preregistered/claude-r3-samesite`, which has no manifest yet.
- Target searches (9): 6 are in the 155 tracked searches (`raken alternative`, `raken pricing`, `busybusy alternative`, `clockshark alternative`, `b2w alternative`, `earthwork software`). 3 were never pulled and have no before rank.
- To do: freeze r3, run `python scripts/claude/r3_pr45.py predict`, commit the ledger before any after-merge result of those searches is read. Outcomes are a later confirmation only.

## 10. The pending round 3 held-out test

**For the plan:** six steps remain, none started. Their order is enforced by `r3_score.py` guards.

State now: `evals/preregistered/claude-r3-samesite/` holds `preregistration.md` and `test.json` only. No `manifest.json`. No `heldout1-a` or `heldout1-b` chunk exists. No result directory `evals/claude/r3/heldout/claude-r3-samesite/` exists. The branch is 1 commit ahead of origin (`e9a3abf`, the GOAL.md note). Commands below were not run.

1. **Manifest.** The preregistration says the manifest covers `r3_score.py` and every repo file it imports, plus `r3_pull.py`, `r3_fetch.py`, the three model folders, `models.json`, the universe and heldout1 plan files, `model-next.json`, `offpage.tsv` and `test.json`. The repo does not record the exact command. Built from `freeze.py`'s usage, it would be: `python scripts/claude/freeze.py closure evals/preregistered/claude-r3-samesite scripts.claude.r3_score scripts.claude.r3_pull scripts.claude.r3_fetch -- evals/preregistered/claude-r3-samesite/test.json evals/preregistered/claude-r3-samesite/preregistration.md evals/claude/r3/models.json evals/claude/r3/plans/universe.json evals/claude/r3/plans/heldout1.jsonl evals/claude/r3/plans/heldout1.meta.json evals/claude/r3/plans/heldout1.slices.json evals/preregistered/forward-pair-2026-11-16/model-next.json evals/preregistered/forward-pair-2026-11-16/offpage.tsv` plus every file of `evals/claude/r3/model/page-blend`, `page-site-blend` (df_table.json, fit_info.json, gbm.txt, logit.json, spec.json) and `text-logit` (no gbm.txt). Then `python scripts/claude/freeze.py verify evals/preregistered/claude-r3-samesite`.
2. **Freeze commit and push** of the manifest (and the unpushed commit). `require_frozen_dir` refuses unless both are on origin.
3. **PR 45 ledger** right after the freeze: `python scripts/claude/r3_pr45.py predict`, commit, push.
4. **Pull A**: `python scripts/claude/r3_pull.py --label heldout1-a --plan /workspace/ranksim-claude/evals/claude/r3/plans/heldout1.jsonl --max-credits 104`. 104 credits, reserve default 260, chunks of 25, each chunk committed and pushed by the script.
5. **Fetch pass**: `python scripts/claude/r3_fetch.py --label heldout1-a` (fresh-since default 2026-11-16), once, after pull A ends, no browser, no second pass.
6. **Pull B**: same command with `--label heldout1-b`, at least 30 minutes and at most 6 hours after pull A per list. 104 credits.
7. **Score once**: `python scripts/claude/r3_score.py --prereg evals/preregistered/claude-r3-samesite`. Writes `evals/claude/r3/heldout/claude-r3-samesite/results.json`, `pairs.csv`, `report.md`. Commit and push.

Budget: 208 credits for steps 4 and 6. From 617 with a 250 reserve that leaves about 159, before the other team's daily spend of about 90.

## 11. Test suite

- Run with `python -m pytest` (CI: `.github/workflows/tests.yml`, Python 3.11 to 3.13, with keys unset and with fake keys).
- `pytest --collect-only` collects 597 tests in 58 files. I did not run the suite. The last recorded run (RESULTS 4.0) was 485 passed and 6 failed, all 6 in `tests/test_layered_fetch.py` because this box's DNS returns 198.18.x.x proxy addresses.
- Claude-script tests: 128 in 10 files: test_claude_heldout 7, r2_samesite_check 13, r2_t2_instruments 24, r2_t3_latent 16, r2_wayback 21, r3_core 12, r3_semantic 20, refetch 6, rsdata 5, t3_movers 4.
- No direct test file found for `r3_dataset.py`, `r3_pages.py`, `r3_plans.py`, `r3_fit.py`, `r3_pr45.py`, `r3_pr45_predict.py`, `t1b_postmortem.py`.

## Surprises and flags

1. `evals/preregistered/claude-r3-samesite/manifest.json` is missing although the preregistration text says it exists. The freeze has not happened.
2. The branch is 1 commit ahead of origin. Box restores roll local disk back. Push before relying on it.
3. `scripts/claude/t1b_postmortem.py` (1,375 lines) is untracked, but its outputs in `evals/claude/r3/t1-postmortem/` were committed in `f0da90c`. The results are not reproducible from git.
4. `evals/claude/r3/t1-postmortem/notes.md`, cited in theory R3.8, does not exist. The folder has results.md, results.json and rows.csv.
5. a70 has no pull on Nov 17 or Nov 18, so "three pulls a day from Nov 16" holds for o30 only (and only two to three on Nov 18 and 19). Nothing is dated Nov 20.
6. `pyarrow` is missing from the repo venv. The task note assumed it.
7. `r3_pages.load_pages` writes `/workspace/ranksim-data/cache/r3_pages/parsed-v1.pkl` when it parses a new page. The cache holds 1,470 entries. 2,346 good fetches of SERP URLs and 442 of site-restricted pages are not in it.
8. PR 45: the marker phrase appears on 1 of 15 after pages. Three of nine target searches have no pre-merge pull.
9. The PR 41 ledger header still says "not merged".
10. The index `candidates` table keeps page-relative deep positions.
11. The client domain is in neither Majestic Million nor Tranco.

## Appendix: queries run (read-only, throwaway, not saved)

All SQLite connections used `sqlite3.connect("file:<path>?mode=ro", uri=True)`. Python ran in the repo venv with `scripts/claude`, `src` and the repo root on sys.path.

```sql
-- raw store schema and SERP counts
SELECT type, name, sql FROM sqlite_master;
SELECT snapshot_date, source, count(*), count(DISTINCT query), count(DISTINCT fetched_at_utc), min(position), max(position)
  FROM serp_results GROUP BY snapshot_date, source;
SELECT snapshot_date, source, endpoint, count(*), count(DISTINCT query), sum(credits), min(fetched_at_utc), max(fetched_at_utc)
  FROM serp_raw GROUP BY snapshot_date, source, endpoint;
SELECT source, request_params FROM serp_raw;            -- page and num per request, tallied in Python
SELECT fetch_method, substr(fetched_at_utc,1,10), count(*), count(DISTINCT url),
       sum(length(html) >= 500), sum(http_status = 200) FROM pages GROUP BY 1, 2;
SELECT count(*), count(DISTINCT url) FROM pages WHERE fetch_method LIKE 'wayback%';
SELECT max(fetched_at_utc) FROM serp_results;  -- also serp_raw, pages
-- readability (same filter as r3_pages.fetch_index)
SELECT id, url, fetched_at_utc, fetch_method, http_status, length(html) FROM pages
  WHERE fetch_method IN ('http','browser') AND length(html) >= 500 AND (http_status IS NULL OR http_status = 200);
SELECT html FROM pages WHERE id = ?;   -- only for ids missing from the parse cache, parsed in memory, never printed
-- index
SELECT * FROM domain_stats;
SELECT url, status, miss_reason, source, word_count, domain, captured_on FROM pages;   -- index.sqlite
SELECT src, dst FROM edges;
SELECT source, snapshot_id, rank_band, count(*), count(DISTINCT query), min(position), max(position)
  FROM candidates GROUP BY 1, 2, 3;
```

```python
rows = rsdata.load_rows("2026-11-19"); groups = rsdata.query_groups(rows)
# pulls per (day, set, search): distinct (source, fetched_at) among kind == "top10"
cons = rsdata.consensus_table(rows); cons = cons[cons.cons <= 10]
# cross-site pairs: itertools.combinations within each (query, day), site differs, both keys readable
# readable = keys whose newest-or-any good fetch parses (t2_changes.extract_page) to >= 50 words,
#   word counts from /workspace/ranksim-data/cache/r3_pages/parsed-v1.pkl (read only) or parsed in memory
# domain spread: groupby(site).agg(nunique query, nunique search group from universe.json groups)
# site-restricted: r3_data.load_label / load_requests / list_pairs for validity-a, train-a, noise-a
# universe: pandas over universe.json combos, site_fold, group_fold
# offpage coverage: set membership of site_key(domain) in offpage.tsv, majestic_million.csv Domain, top-1m.csv
# parquet row counts: Thrift compact parse of each footer (FileMetaData fields 2 and 3), no pyarrow
# tests: python -m pytest --collect-only -q (not run)
```
