# Same-site pairs: generated tables (scripts/claude/r2_samesite_pairs.py)

Design cutoff 2026-11-17. Max SERP day loaded 2026-11-17. Max page fetch day loaded 2026-11-17. Positions REAL, model scores PREDICTED.

Folds: {'A': 94, 'B': 61} searches, 71 components. Same-site U1 pairs per fold {'A': 330, 'B': 135}, searches {'A': 59, 'B': 29}. Largest search groups: [[35, 'all in 1 dirt work software'], [16, 'hcss alternative'], [9, 'construction time tracking'], [8, 'all in one heavy civil software'], [5, 'best job costing software'], [4, 'AI for revenue recovery']].

## Q1 counts

| count | U1 Nov 16 lists | U2 top-10 consensus days |
| --- | ---: | ---: |
| searches_with_lists | 150 | 155 |
| pairs_total | 33281 | 8726 |
| same_site_pairs | 944 | 282 |
| same_site_per_search_mean | 6.29 | 1.82 |
| same_site_per_search_median | 3.00 | 1.00 |
| same_site_per_search_max | 82 | 21 |
| searches_with_same_site_pair | 109 | 83 |
| same_site_sites | 56 | 21 |
| same_site_top5_share | 0.81 | 0.76 |
| same_site_usable_pairs | 465 | 159 |
| same_site_usable_searches | 88 | 54 |
| same_site_usable_sites | 40 | 14 |
| same_site_usable_top5_share | 0.79 | 0.81 |
| same_site_duplicate_content_dropped | 5 | 7 |
| cross_site_pairs | 32337 | 8444 |
| cross_site_usable_pairs | 16580 | 3741 |
| cross_site_usable_searches | 134 | 139 |
| cross_site_usable_sites | 441 | 242 |

U1 top-5 sites (all same-site pairs): [['hcss.com', 368], ['youtube.com', 178], ['trimble.com', 89], ['reddit.com', 73], ['smartcrewhq.com', 57]]
U1 top-5 sites (usable): [['hcss.com', 200], ['smartcrewhq.com', 52], ['trimble.com', 52], ['youtube.com', 38], ['agtek.com', 26]]
U2 top-5 sites (usable): [['hcss.com', 72], ['trimble.com', 23], ['smartcrewhq.com', 12], ['assignar.com', 11], ['busybusy.com', 11]]
U1 by source: {'deep': {'searches': 100, 'same_site': 834, 'cross_site': 30459}, 'top10': {'searches': 50, 'same_site': 110, 'cross_site': 1878}}
U1 same-site pairs by zone: {'both top10': 241, 'one top10': 481, 'both 11-30': 222}
U1 deep list: 162 of 2678 rows repeat a URL already listed higher in the same list
U2: pair-day instances 13380, tied instances 26, split pairs dropped 151, same-site unanimous 279, same-site seen on 2+ days 28
Pages: {'urls_in_pairs': 1819, 'urls_with_any_fetch': 1276, 'urls_with_usable_page': 1084, 'fetch_day': {'2026-11-16': 1012, '2026-11-13': 13, '2026-11-06': 12, '2026-11-17': 12, '2026-11-09': 15, '2026-11-11': 20}, 'method': {'http:date-only': 498, 'http': 448, 'browser:date-only': 87, 'browser': 51}}

## Q2 single features (share where the page with the better feature value ranks higher; ties and missing left out)

| feature | direction | U1 same-site decided | ties / missing | U1 cross-site decided | ties / missing | same minus cross |
| --- | --- | --- | --- | --- | --- | --- |
| title_cov | larger first | 123/204 (0.603) [0.507, 0.689] | 261 / 0 | 4868/9408 (0.517) [0.490, 0.546] | 7172 / 0 | +0.086 [-0.004, +0.171] |
| h1_cov | larger first | 151/240 (0.629) [0.544, 0.697] | 225 / 0 | 5464/10480 (0.521) [0.495, 0.546] | 6100 / 0 | +0.108 [+0.028, +0.179] |
| slug_cov | larger first | 134/201 (0.667) [0.584, 0.744] | 264 / 0 | 5628/11200 (0.502) [0.482, 0.523] | 5380 / 0 | +0.164 [+0.080, +0.238] |
| title_phrase | larger first | 31/39 (0.795) [0.667, 0.941] | 426 / 0 | 1824/3322 (0.549) [0.495, 0.604] | 13258 / 0 | +0.246 [+0.121, +0.404] |
| bm25_body | larger first | 262/464 (0.565) [0.516, 0.616] | 1 / 0 | 8568/16575 (0.517) [0.498, 0.537] | 5 / 0 | +0.048 [+0.001, +0.097] |
| bm25f | larger first | 277/464 (0.597) [0.548, 0.647] | 1 / 0 | 8657/16577 (0.522) [0.504, 0.542] | 3 / 0 | +0.075 [+0.028, +0.122] |
| first100_cov | larger first | 123/219 (0.562) [0.492, 0.635] | 246 / 0 | 5108/9933 (0.514) [0.491, 0.538] | 6647 / 0 | +0.047 [-0.019, +0.119] |
| log_words | larger first | 239/465 (0.514) [0.464, 0.573] | 0 / 0 | 8161/16578 (0.492) [0.477, 0.508] | 2 / 0 | +0.022 [-0.030, +0.080] |
| url_depth | smaller first | 121/204 (0.593) [0.505, 0.667] | 261 / 0 | 5669/10748 (0.527) [0.505, 0.551] | 5832 / 0 | +0.066 [-0.026, +0.139] |
| is_home | larger first | 40/63 (0.635) [0.472, 0.780] | 402 / 0 | 1277/2308 (0.553) [0.507, 0.606] | 14272 / 0 | +0.082 [-0.076, +0.219] |
| serp_title_all | larger first | 47/54 (0.870) [0.779, 0.964] | 411 / 0 | 2527/4657 (0.543) [0.499, 0.588] | 11923 / 0 | +0.328 [+0.238, +0.433] |
| recency | larger first | 201/346 (0.581) [0.530, 0.639] | 33 / 86 | 4456/8582 (0.519) [0.500, 0.539] | 60 / 7938 | +0.062 [+0.008, +0.126] |
| url_len | smaller first | 231/412 (0.561) [0.508, 0.615] | 53 / 0 | 8211/16279 (0.504) [0.488, 0.520] | 301 / 0 | +0.056 [+0.002, +0.109] |
| inlinks | larger first | 174/277 (0.628) [0.556, 0.696] | 188 / 0 | 5632/10091 (0.558) [0.535, 0.580] | 6489 / 0 | +0.070 [+0.004, +0.136] |
| age_published | larger first | 133/280 (0.475) [0.432, 0.519] | 29 / 156 | 3152/5838 (0.540) [0.514, 0.564] | 6 / 10736 | -0.065 [-0.111, -0.020] |
| canonical_elsewhere | smaller first | 9/21 (0.429) [0.227, 0.684] | 444 / 0 | 311/707 (0.440) [0.348, 0.538] | 15873 / 0 | -0.011 [-0.181, +0.230] |

U2 (consensus days) same-site and cross-site, decided pairs:

| feature | U2 same-site | U2 cross-site |
| --- | --- | --- |
| title_cov | 43/68 (0.632) [0.479, 0.750] | 1157/2110 (0.548) [0.510, 0.589] |
| h1_cov | 56/94 (0.596) [0.479, 0.707] | 1259/2361 (0.533) [0.497, 0.569] |
| slug_cov | 43/75 (0.573) [0.439, 0.699] | 1273/2585 (0.492) [0.460, 0.526] |
| title_phrase | 17/22 (0.773) [0.625, 0.947] | 284/554 (0.513) [0.450, 0.574] |
| bm25_body | 92/159 (0.579) [0.481, 0.675] | 1876/3740 (0.502) [0.477, 0.526] |
| bm25f | 96/159 (0.604) [0.511, 0.696] | 1926/3741 (0.515) [0.491, 0.538] |
| first100_cov | 44/79 (0.557) [0.429, 0.679] | 1213/2410 (0.503) [0.471, 0.536] |
| log_words | 94/159 (0.591) [0.500, 0.685] | 1861/3741 (0.497) [0.472, 0.523] |
| url_depth | 55/86 (0.640) [0.507, 0.746] | 1284/2501 (0.513) [0.478, 0.549] |
| is_home | 21/36 (0.583) [0.400, 0.742] | 378/644 (0.587) [0.508, 0.667] |
| serp_title_all | 21/26 (0.808) [0.696, 1.000] | 485/864 (0.561) [0.504, 0.614] |
| recency | 73/121 (0.603) [0.518, 0.683] | 1296/2371 (0.547) [0.521, 0.573] |
| url_len | 96/154 (0.623) [0.532, 0.712] | 1913/3678 (0.520) [0.494, 0.548] |
| inlinks | 79/100 (0.790) [0.696, 0.868] | 1417/2432 (0.583) [0.550, 0.615] |
| age_published | 50/101 (0.495) [0.406, 0.577] | 805/1561 (0.516) [0.481, 0.549] |
| canonical_elsewhere | 3/5 (0.600) [0.000, 1.000] | 98/231 (0.424) [0.298, 0.580] |

## Q3 model (fit on one fold, scored on the other)

| set | accuracy |
| --- | --- |
| u1_same_site | 226/465 (0.486) [0.428, 0.536] |
| u1_cross_site_same_model | 8221/16580 (0.496) [0.479, 0.515] |
| u2_same_site | 76/159 (0.478) [0.371, 0.575] |
| u2_same_site_unanimous_multi_day | 3/7 (0.429) [0.000, 0.818] |
| u2_cross_site_same_model | 1809/3741 (0.484) [0.455, 0.511] |
| u1_same_site_leave_one_site_out | 231/465 (0.497) [0.437, 0.547] |
| u1_same_site_without_ugc | 216/427 (0.506) [0.452, 0.555] |
| u1_same_site_scored_by_cross_site_model | 227/465 (0.488) [0.439, 0.538] |
| u1_same_site_pairs_only_one_fold | 209/429 (0.487) [0.428, 0.539] |
| u1_same_site fold A | 162/330 (0.491) [0.418, 0.554] |
| u1_same_site fold B | 64/135 (0.474) [0.380, 0.552] |
| without top-5 sites, model | 55/97 (0.567) [0.465, 0.651] |
| without top-5 sites, leave-one-site-out | 55/97 (0.567) [0.465, 0.651] |

Same minus cross (model): -0.010 [-0.066, +0.040]. Gap-matched cross-site point: 0.495. Same-site pairs present in both folds: 36. Model ties (scored 0.5): 0. Distinct same-site URL pairs: 355 on 232 URLs.

Per-search macro averages (each search weighs the same), U1 same-site:

| scorer | macro accuracy |
| --- | --- |
| model | 0.457 [0.386, 0.527] (88 searches) |
| loso | 0.465 [0.394, 0.538] (88 searches) |
| more_title_terms_first_ties_half | 0.537 [0.483, 0.589] (88 searches) |
| bm25f_ties_half | 0.605 [0.536, 0.678] (88 searches) |
| shorter_url_first_ties_half | 0.582 [0.521, 0.642] (88 searches) |

Single features by position zone, U1 same-site, decided pairs:

| feature | zone | decided |
| --- | --- | --- |
| bm25f | both 11-30 | 30/78 (0.385) [0.278, 0.500] |
| bm25f | both top10 | 89/143 (0.622) [0.529, 0.715] |
| bm25f | one top10 | 158/243 (0.650) [0.569, 0.723] |
| title_cov | both 11-30 | 12/25 (0.480) [0.273, 0.667] |
| title_cov | both top10 | 35/55 (0.636) [0.489, 0.769] |
| title_cov | one top10 | 76/124 (0.613) [0.488, 0.729] |
| url_depth | both 11-30 | 11/22 (0.500) [0.286, 0.762] |
| url_depth | both top10 | 48/78 (0.615) [0.494, 0.732] |
| url_depth | one top10 | 62/104 (0.596) [0.434, 0.729] |

POST-HOC equal-weight vote of pre-stated directions (POST-HOC. Added after the pre-stated fitted model scored at chance. Not a pre-stated test.):

| vote | accuracy (ties half) |
| --- | --- |
| u1_same_full | 284.5/465 (0.612) [0.561, 0.661] |
| u1_same_text | 277/465 (0.596) [0.547, 0.640] |
| u1_cross_full | 8581.5/16580 (0.518) [0.501, 0.536] |
| u1_cross_text | 8567.5/16580 (0.517) [0.499, 0.535] |
| u1_same_text_without_top5_sites | 65.5/97 (0.675) [0.579, 0.765] |
| u1_same_text_without_youtube | 265/427 (0.621) [0.577, 0.662] |
| u2_same_full | 100.5/159 (0.632) [0.545, 0.718] |
| u2_same_text | 96.5/159 (0.607) [0.533, 0.682] |
| u1_same_text, zone both 11-30 | 29/79 (0.367) [0.260, 0.481] |
| u1_same_text, zone both top10 | 88.5/143 (0.619) [0.529, 0.708] |
| u1_same_text, zone one top10 | 159.5/243 (0.656) [0.571, 0.725] |
| u1_same_text macro | 0.589 [0.518, 0.660] |

Same-site sites by zone: {'both 11-30': [['hcss.com', 37], ['youtube.com', 12], ['smartcrewhq.com', 7], ['trimble.com', 6]], 'both top10': [['hcss.com', 65], ['trimble.com', 22], ['smartcrewhq.com', 11], ['assignar.com', 11]], 'one top10': [['hcss.com', 98], ['smartcrewhq.com', 34], ['youtube.com', 25], ['trimble.com', 24]]}

| cross-site zone | model |
| --- | --- |
| both 11-30 | 3155/6338 (0.498) [0.478, 0.519] |
| both top10 | 1233/2534 (0.487) [0.459, 0.515] |
| one top10 | 3833/7708 (0.497) [0.464, 0.528] |

| baseline | set | decided | ties | all pairs, ties as half |
| --- | --- | --- | --- | --- |
| shorter_url_first | u1_same | 231/412 (0.561) [0.508, 0.615] | 53 | 257.5/465 (0.554) [0.509, 0.599] |
| shorter_url_first | u1_cross | 8211/16279 (0.504) [0.488, 0.520] | 301 | 8361.5/16580 (0.504) [0.488, 0.520] |
| shorter_url_first | u2_same | 96/154 (0.623) [0.532, 0.712] | 5 | 98.5/159 (0.619) [0.533, 0.701] |
| more_title_terms_first | u1_same | 123/204 (0.603) [0.507, 0.689] | 261 | 253.5/465 (0.545) [0.505, 0.585] |
| more_title_terms_first | u1_cross | 4868/9408 (0.517) [0.490, 0.546] | 7172 | 8454/16580 (0.510) [0.494, 0.525] |
| more_title_terms_first | u2_same | 43/68 (0.632) [0.479, 0.750] | 91 | 88.5/159 (0.557) [0.491, 0.611] |

Model minus title baseline (ties half), U1 same-site: -0.059 [-0.114, -0.008]
Model minus shorter-URL baseline (ties half), U1 same-site: -0.068 [-0.129, -0.012]

| top site | model | leave-one-site-out |
| --- | --- | --- |
| hcss.com | 87/200 (0.435) [0.309, 0.521] | 88/200 (0.440) [0.318, 0.525] |
| smartcrewhq.com | 30/52 (0.577) [0.457, 0.684] | 32/52 (0.615) [0.488, 0.732] |
| trimble.com | 32/52 (0.615) [0.400, 0.727] | 33/52 (0.635) [0.454, 0.735] |
| youtube.com | 10/38 (0.263) [0.118, 0.386] | 10/38 (0.263) [0.118, 0.386] |
| agtek.com | 12/26 (0.462) [0.227, 0.762] | 13/26 (0.500) [0.273, 0.762] |

Single features on the top sites, decided pairs:

| site | feature | decided |
| --- | --- | --- |
| hcss.com | title_cov | 58/99 (0.586) [0.418, 0.706] |
| hcss.com | h1_cov | 61/92 (0.663) [0.500, 0.770] |
| hcss.com | slug_cov | 52/82 (0.634) [0.438, 0.773] |
| hcss.com | bm25f | 117/200 (0.585) [0.528, 0.645] |
| hcss.com | url_depth | 57/80 (0.713) [0.560, 0.814] |
| smartcrewhq.com | title_cov | 22/35 (0.629) [0.481, 0.767] |
| smartcrewhq.com | h1_cov | 22/33 (0.667) [0.512, 0.812] |
| smartcrewhq.com | slug_cov | 23/32 (0.719) [0.538, 0.865] |
| smartcrewhq.com | bm25f | 30/52 (0.577) [0.400, 0.700] |
| smartcrewhq.com | url_depth | 10/18 (0.556) [0.316, 0.846] |
| trimble.com | title_cov | 5/5 (1.000) [1.000, 1.000] |
| trimble.com | h1_cov | 19/32 (0.594) [0.350, 0.793] |
| trimble.com | slug_cov | 16/24 (0.667) [0.417, 0.875] |
| trimble.com | bm25f | 35/52 (0.673) [0.507, 0.870] |
| trimble.com | url_depth | 21/38 (0.553) [0.471, 0.722] |
| youtube.com | title_cov | 6/23 (0.261) [0.083, 0.526] |
| youtube.com | h1_cov | 6/23 (0.261) [0.083, 0.526] |
| youtube.com | slug_cov | 0/0 |
| youtube.com | bm25f | 15/38 (0.395) [0.258, 0.552] |
| youtube.com | url_depth | 0/0 |
| agtek.com | title_cov | 4/5 (0.800) [0.500, 1.000] |
| agtek.com | h1_cov | 7/8 (0.875) [0.625, 1.000] |
| agtek.com | slug_cov | 10/17 (0.588) [0.250, 0.810] |
| agtek.com | bm25f | 18/26 (0.692) [0.471, 0.875] |
| agtek.com | url_depth | 5/15 (0.333) [0.062, 0.611] |

Same-site pairs by fold and site: {'A': [['hcss.com', 158], ['smartcrewhq.com', 39], ['youtube.com', 37], ['trimble.com', 26], ['agtek.com', 20], ['all', 330]], 'B': [['hcss.com', 42], ['trimble.com', 26], ['assignar.com', 16], ['smartcrewhq.com', 13], ['workyard.com', 7], ['all', 135]]}

| zone or group | model |
| --- | --- |
| both 11-30 | 28/79 (0.354) [0.262, 0.463] |
| both top10 | 68/143 (0.476) [0.356, 0.589] |
| one top10 | 130/243 (0.535) [0.458, 0.604] |
| a70 | 141/277 (0.509) [0.442, 0.565] |
| o30 | 44/110 (0.400) [0.283, 0.512] |
| p35 | 41/78 (0.526) [0.378, 0.657] |

| position gap | same-site model | cross-site model |
| --- | --- | --- |
| 1-2 | 23/51 (0.451) [0.317, 0.578] | 1265/2524 (0.501) [0.485, 0.517] |
| 3-5 | 39/70 (0.557) [0.443, 0.672] | 1640/3293 (0.498) [0.482, 0.514] |
| 6-10 | 59/136 (0.434) [0.349, 0.517] | 2093/4256 (0.492) [0.473, 0.511] |
| 11+ | 105/208 (0.505) [0.431, 0.577] | 3223/6507 (0.495) [0.462, 0.527] |

Model weights (standardized coefficients):

- fit_A_score_B (train pairs 330): title_cov -0.280, h1_cov +0.135, slug_cov +0.608, title_phrase -0.174, bm25_body -0.826, bm25f +0.175, first100_cov +0.465, log_words -0.088, url_depth -0.274, is_home +0.114, serp_title_all +0.684, recency +0.869
- fit_B_score_A (train pairs 135): title_cov +0.915, h1_cov -0.545, slug_cov +1.185, title_phrase +0.922, bm25_body +1.145, bm25f +0.806, first100_cov -0.815, log_words -0.076, url_depth -0.541, is_home +0.946, serp_title_all -0.247, recency -0.408

## Q4 version sensitivity

```
{
 "note": "Model scores PREDICTED. URL features and SERP title held at day A; dates aged to fetch day B for both versions.",
 "material": {
  "rows": 28,
  "fetch_pairs": 13,
  "urls": 11,
  "moved_rows": 17,
  "moved_up": 7,
  "moved_down": 10,
  "score_unchanged_all_rows": 0,
  "score_unchanged_moved_rows": 0,
  "moved_rows_nonzero_delta": 17,
  "sign_agrees_with_move": 9,
  "text_model_nonzero_moved": 17,
  "text_model_sign_agrees": 10,
  "rows_where_a_title_or_h1_feature_changed": 13,
  "post_hoc_text_vote_nonzero_moved": 16,
  "post_hoc_text_vote_sign_agrees": 7,
  "post_hoc_full_vote_nonzero_moved": 16,
  "post_hoc_full_vote_sign_agrees": 7,
  "feature_change_counts": {
   "title_cov": 1,
   "bm25_body": 27,
   "bm25f": 28,
   "first100_cov": 6,
   "log_words": 27,
   "recency": 26,
   "h1_cov": 8,
   "title_phrase": 5
  },
  "median_abs_delta": 0.47013815257643543
 },
 "control_no_material_change": {
  "rows": 735,
  "fetch_pairs": 249,
  "urls": 97,
  "moved_rows": 437,
  "moved_up": 172,
  "moved_down": 265,
  "score_unchanged_all_rows": 656,
  "score_unchanged_moved_rows": 395,
  "moved_rows_nonzero_delta": 42,
  "sign_agrees_with_move": 24,
  "text_model_nonzero_moved": 34,
  "text_model_sign_agrees": 18,
  "rows_where_a_title_or_h1_feature_changed": 0,
  "post_hoc_text_vote_nonzero_moved": 34,
  "post_hoc_text_vote_sign_agrees": 18,
  "post_hoc_full_vote_nonzero_moved": 40,
  "post_hoc_full_vote_sign_agrees": 20,
  "feature_change_counts": {
   "bm25_body": 68,
   "bm25f": 68,
   "log_words": 59,
   "recency": 24
  },
  "median_abs_delta": 0.0
 }
}
```

## Q5 structure versus text (same-site U1, fold-safe)

| model or slice | accuracy |
| --- | --- |
| model full | 226/465 (0.486) [0.428, 0.536] |
| model text | 230.5/465 (0.496) [0.450, 0.544] |
| model structure | 251.5/465 (0.541) [0.501, 0.576] |
| model full_minus_structure | 217/465 (0.467) [0.422, 0.511] |
| structure-tied pairs (261), model full | 124/261 (0.475) [0.428, 0.525] |
| structure-tied pairs (261), model text | 131/261 (0.502) [0.439, 0.573] |
| structure-informative pairs (204), model full | 102/204 (0.500) [0.396, 0.587] |
| structure-informative pairs (204), model text | 99.5/204 (0.488) [0.407, 0.563] |
| structure-informative pairs (204), model structure | 121/204 (0.593) [0.505, 0.667] |
| structure-tied, title_cov decided | 63/117 (0.538) [0.409, 0.673] |
| structure-tied, bm25f decided | 166/261 (0.636) [0.578, 0.689] |
| homepage ranks higher (63 pairs with one homepage) | 40/63 (0.635) [0.472, 0.780] |
| structure-tied, POST-HOC text vote | 160.5/261 (0.615) [0.551, 0.673] |
| structure-informative, POST-HOC text vote | 116.5/204 (0.571) [0.497, 0.633] |
| structure-informative, bm25f decided | 111/203 (0.547) [0.472, 0.624] |

Structure call versus BM25F call on structure-informative pairs: {'pairs': 204, 'agree': 83, 'agree_and_right': 56, 'disagree': 120, 'disagree_structure_right': 65, 'disagree_bm25f_right': 55}

Text minus structure: -0.045 [-0.103, +0.016]. Full minus text: -0.010 [-0.063, +0.037]. Full minus structure: -0.055 [-0.104, -0.009].

| extra feature | decided | ties / missing |
| --- | --- | --- |
| url_len | 231/412 (0.561) [0.508, 0.615] | 53 / 0 |
| inlinks | 174/277 (0.628) [0.556, 0.696] | 188 / 0 |
| age_published | 133/280 (0.475) [0.432, 0.519] | 29 / 156 |
| canonical_elsewhere | 9/21 (0.429) [0.227, 0.684] | 444 / 0 |

Inlink coverage: {'pages_with_any_same_site_inlink': 359, 'pages': 1084}. Pairs with a canonical pointing elsewhere: 24.
