# Generated tables (python -m scripts.claude.r2_t2_signal). Do not edit by hand.

## Q1 stratified (date pair x start band), material vs not material

| outcome | changed rows matched | changed | matched unchanged | diff [95% query CI] | URL-cluster CI | controls-fixed CI |
| --- | ---: | ---: | ---: | --- | --- | --- |
| moved | 25 of 28 | 0.640 | 0.557 | +0.083 [-0.082, +0.267] | [-0.113, +0.348] | [-0.062, +0.230] |
| absd | 25 of 28 | 1.720 | 1.936 | -0.216 [-1.049, +0.593] | [-0.892, +0.744] | [-0.945, +0.588] |
| crossed | 25 of 28 | 0.160 | 0.281 | -0.121 [-0.258, +0.083] | [-0.283, +0.168] | [-0.249, +0.059] |

## Q1 material_vs_nochange

| outcome | treated matched | treated | matched control | diff [95% CI] | share caused, point / upper 95 |
| --- | ---: | ---: | ---: | --- | --- |
| moved | 25 of 28 | 0.640 | 0.558 | +0.082 [-0.093, +0.281] | 0.129 / 0.348 |
| absd | 25 of 28 | 1.720 | 1.933 | -0.213 [-1.066, +0.602] | 0.000 / 0.265 |
| crossed | 25 of 28 | 0.160 | 0.288 | -0.128 [-0.265, +0.074] | 0.000 / 0.264 |

## Q1 material_vs_not_interior

| outcome | treated matched | treated | matched control | diff [95% CI] | share caused, point / upper 95 |
| --- | ---: | ---: | ---: | --- | --- |
| moved | 16 of 16 | 0.875 | 0.660 | +0.215 [+0.033, +0.400] | 0.246 / 0.415 |
| absd | 16 of 16 | 2.125 | 1.995 | +0.130 [-0.785, +0.960] | 0.061 / 0.366 |
| crossed | 16 of 16 | 0.125 | 0.228 | -0.103 [-0.325, +0.154] | 0.000 / 0.540 |

## Q1 material_vs_not_interior_url_cluster

| outcome | treated matched | treated | matched control | diff [95% CI] | share caused, point / upper 95 |
| --- | ---: | ---: | ---: | --- | --- |
| moved | 16 of 16 | 0.875 | 0.660 | +0.215 [+0.032, +0.389] | 0.246 / 0.400 |
| absd | 16 of 16 | 2.125 | 1.995 | +0.130 [-0.661, +0.929] | 0.061 / 0.340 |
| crossed | 16 of 16 | 0.125 | 0.228 | -0.103 [-0.282, +0.216] | 0.000 / 0.555 |

## Q1 material_vs_not_interior_site_cluster

| outcome | treated matched | treated | matched control | diff [95% CI] | share caused, point / upper 95 |
| --- | ---: | ---: | ---: | --- | --- |
| moved | 16 of 16 | 0.875 | 0.660 | +0.215 [-0.108, +0.384] | 0.246 / 0.408 |

## Q1 material_vs_not_without_client

| outcome | treated matched | treated | matched control | diff [95% CI] | share caused, point / upper 95 |
| --- | ---: | ---: | ---: | --- | --- |
| moved | 10 of 10 | 0.800 | 0.543 | +0.257 [-0.003, +0.496] | 0.322 / 0.528 |
| absd | 10 of 10 | 2.000 | 1.420 | +0.580 [-0.700, +1.918] | 0.290 / 0.611 |
| crossed | 10 of 10 | 0.300 | 0.149 | +0.151 [-0.135, +0.433] | 0.502 / 0.789 |

## Q1 material_vs_not_interior_without_client

| outcome | treated matched | treated | matched control | diff [95% CI] | share caused, point / upper 95 |
| --- | ---: | ---: | ---: | --- | --- |
| moved | 8 of 8 | 0.875 | 0.622 | +0.253 [-0.010, +0.500] | 0.290 / 0.514 |
| absd | 8 of 8 | 1.750 | 1.497 | +0.253 [-0.816, +1.352] | 0.144 / 0.531 |

## Q1 client_only_interior

| outcome | treated matched | treated | matched control | diff [95% CI] | share caused, point / upper 95 |
| --- | ---: | ---: | ---: | --- | --- |
| moved | 2 of 8 | 0.500 | 1.000 | -0.500 [-1.000, +0.000] | 0.000 / 0.000 |
| absd | 2 of 8 | 2.500 | 3.125 | -0.625 [-3.333, +1.000] | 0.000 / 0.200 |

## Q1 anychange_vs_none

| outcome | treated matched | treated | matched control | diff [95% CI] | share caused, point / upper 95 |
| --- | ---: | ---: | ---: | --- | --- |
| moved | 97 of 101 | 0.536 | 0.540 | -0.004 [-0.121, +0.114] | 0.000 / 0.186 |
| absd | 97 of 101 | 1.237 | 1.793 | -0.556 [-1.116, +0.091] | 0.000 / 0.052 |
| crossed | 97 of 101 | 0.144 | 0.241 | -0.096 [-0.182, +0.003] | 0.000 / 0.018 |

Interior material rows: agtek.com/products/underground [best excavation software] 2026-11-13>2026-11-16 4->2; agtek.com/solutions/takeoff [all in 1 dirt work software] 2026-11-13>2026-11-16 3->1; agtek.com/solutions/takeoff [all in one dirt work software] 2026-11-13>2026-11-16 3->2; agtek.com/solutions/takeoff [best all in 1 dirt work software] 2026-11-13>2026-11-16 4->6; agtek.com/solutions/takeoff [best all in one dirt work software] 2026-11-13>2026-11-16 5->6; bidsyncai.com/blog/heavy-civil-construction-software [all in one heavy civil software] 2026-11-13>2026-11-16 6->11; ownrops.com/guides/best-land-clearing-software [dirt work software with crm] 2026-11-13>2026-11-16 10->11; www.hcss.com/products/construction-project-management-software [hcss heavyjob alternative] 2026-11-13>2026-11-16 2->2; www.smartcrewhq.com/all-in-one-dirt-work [all in 1 dirt work software] 2026-11-09>2026-11-11 8->9; www.smartcrewhq.com/all-in-one-dirt-work [all in 1 dirt work software] 2026-11-11>2026-11-13 9->9; www.smartcrewhq.com/all-in-one-dirt-work [best all-in-one dirtworks software] 2026-11-11>2026-11-13 5->10; www.smartcrewhq.com/all-in-one-dirt-work-software [best all-in-one dirtworks software] 2026-11-09>2026-11-11 3->1; www.smartcrewhq.com/all-in-one-dirt-work-software [all in one dirtwork software] 2026-11-11>2026-11-16 4->10; www.smartcrewhq.com/blog/best-software-small-dirt-work-contractors [dirt work management software] 2026-11-09>2026-11-11 6->7; www.smartcrewhq.com/compare/hcss-heavybid-alternative [hcss heavybid alternative] 2026-11-09>2026-11-11 10->9; www.smartcrewhq.com/dirt-work-software-with-crm [dirt work software with crm] 2026-11-09>2026-11-11 2->6

## Q1 dose bins (edit tokens) vs no change

| bin | rows | moved | P(moved) changed vs matched none, diff [CI] | mean abs change, diff [CI] |
| --- | ---: | ---: | --- | --- |
| 1-49 | 42 | 23 | 0.590 vs 0.510, diff +0.080 [-0.050, +0.216] | 1.56 vs 1.68, diff -0.11 [-0.80, +0.73] |
| 50-249 | 55 | 27 | 0.481 vs 0.557, diff -0.075 [-0.215, +0.072] | 0.91 vs 1.86, diff -0.96 [-1.69, -0.17] |
| 250+ | 3 | 2 | 0.667 vs 0.670, diff -0.003 [-0.700, +0.387] | 2.00 vs 1.85, diff +0.15 [-3.04, +3.34] |
| title_or_h1 | 26 | n/a | 0.609 vs 0.541, diff +0.067 [-0.106, +0.276] | 1.61 vs 1.95, diff -0.34 [-1.18, +0.41] |

No-change rows: {'rows': 663, 'moved': 402, 'urls': 92}

Slope of moved on log(1 + edit tokens), within strata: -0.0017 [-0.0290, +0.0269] (763 rows)

Slope of absd on log(1 + edit tokens), within strata: -0.1314 [-0.2583, +0.0184] (763 rows)

## Q2 inputs

```
{
 "z_alpha_two_sided_95": 1.959964,
 "z_power_80": 0.841621,
 "changed_and_moved_rows": 17,
 "interior_changed_and_moved_rows": 14,
 "s_interior_share": 0.823529,
 "r_content_step_fires_interior": 0.357143,
 "content_fires_rows": 5,
 "a0_position_rule_unchanged_moved_all": {
  "hits": 303,
  "n": 437,
  "rate": 0.693364,
  "lo": 0.647316,
  "hi": 0.736489,
  "queries": 30
 },
 "a0_position_rule_unchanged_moved_interior": {
  "hits": 250,
  "n": 384,
  "rate": 0.651042,
  "lo": 0.60567,
  "hi": 0.693629,
  "queries": 30
 },
 "f_point_attributable_share_interior": 0.245757,
 "f_upper95_interior_max_of_query_and_url_cluster": 0.414753,
 "f_upper95_interior_query_cluster": 0.414753,
 "f_upper95_interior_url_cluster": 0.399631,
 "f_all_rows_point_and_upper95": [
  0.129399,
  0.335452
 ],
 "deff_moved_rows_per_changed_url_pair": 1.416667,
 "yield_url_days": 728.0,
 "yield_urls": 98,
 "changed_and_moved_rows_per_url_day": 0.023352,
 "changed_and_moved_rows_per_week_current_set": 16.019231,
 "changed_and_moved_rows_per_week_per_100_urls": 16.346154,
 "cap_on_accuracy_gain_perfect_content_gated_f_upper": 0.170781,
 "cap_on_accuracy_gain_perfect_content_gated_f_point": 0.101194
}
```

| a_c | f | q | gated gain | gated rows | gated weeks | ungated gain | ungated rows | ungated weeks | ungated break-even f |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.500 | 0.246 | 0.6 | +0.0202 | 2,735 | 170.8 | -0.0263 | never | never | 0.602 |
| 0.500 | 0.246 | 0.7 | +0.0405 | 676 | 42.2 | -0.0190 | never | never | 0.430 |
| 0.500 | 0.246 | 0.8 | +0.0607 | 294 | 18.4 | -0.0118 | never | never | 0.335 |
| 0.500 | 0.415 | 0.6 | +0.0342 | 1,616 | 100.9 | -0.0138 | never | never | 0.602 |
| 0.500 | 0.415 | 0.7 | +0.0683 | 396 | 24.7 | -0.0016 | never | never | 0.430 |
| 0.500 | 0.415 | 0.8 | +0.1025 | 170 | 10.6 | +0.0106 | 14,534 | 907.3 | 0.335 |
| 0.500 | 0.500 | 0.6 | +0.0412 | 1,339 | 83.6 | -0.0075 | never | never | 0.602 |
| 0.500 | 0.500 | 0.7 | +0.0824 | 326 | 20.4 | +0.0072 | 31,534 | 1,968.5 | 0.430 |
| 0.500 | 0.500 | 0.8 | +0.1235 | 139 | 8.7 | +0.0219 | 3,397 | 212.0 | 0.335 |
| 0.500 | 1.000 | 0.6 | +0.0824 | 664 | 41.4 | +0.0294 | 1,879 | 117.3 | 0.602 |
| 0.500 | 1.000 | 0.7 | +0.1647 | 158 | 9.8 | +0.0588 | 461 | 28.8 | 0.430 |
| 0.500 | 1.000 | 0.8 | +0.2471 | 64 | 4.0 | +0.0882 | 199 | 12.4 | 0.335 |
| 0.651 | 0.246 | 0.6 | -0.0103 | never | never | -0.0372 | never | never | 1.510 |
| 0.651 | 0.246 | 0.7 | +0.0099 | 10,063 | 628.2 | -0.0300 | never | never | 0.755 |
| 0.651 | 0.246 | 0.8 | +0.0302 | 1,002 | 62.6 | -0.0227 | never | never | 0.503 |
| 0.651 | 0.415 | 0.6 | -0.0174 | never | never | -0.0322 | never | never | 1.510 |
| 0.651 | 0.415 | 0.7 | +0.0167 | 5,958 | 372.0 | -0.0200 | never | never | 0.755 |
| 0.651 | 0.415 | 0.8 | +0.0509 | 589 | 36.8 | -0.0078 | never | never | 0.503 |
| 0.651 | 0.500 | 0.6 | -0.0210 | never | never | -0.0297 | never | never | 1.510 |
| 0.651 | 0.500 | 0.7 | +0.0202 | 4,941 | 308.5 | -0.0150 | never | never | 0.755 |
| 0.651 | 0.500 | 0.8 | +0.0613 | 487 | 30.4 | -0.0003 | never | never | 0.503 |
| 0.651 | 1.000 | 0.6 | -0.0420 | never | never | -0.0150 | never | never | 1.510 |
| 0.651 | 1.000 | 0.7 | +0.0403 | 2,465 | 153.9 | +0.0144 | 6,922 | 432.1 | 0.755 |
| 0.651 | 1.000 | 0.8 | +0.1227 | 238 | 14.9 | +0.0438 | 686 | 42.8 | 0.503 |

## Q3a/b noise floor: pull pairs

| comparison | pull pairs | title differs (exact) | title differs (substantive) | snippet differs | date shown in one only | date value differs (both parsed) |
| --- | ---: | --- | --- | --- | --- | --- |
| same day, all | 1865 | 50/1865 (0.027) [0.012, 0.044] | 1/1865 (0.001) [0.000, 0.002] | 445/1865 (0.239) [0.191, 0.286] | 29/801 (0.036) [0.016, 0.058] | 0/763 (0.000) [0.000, 0.000] |
| same day 2026-11-16 | 1216 | 45/1216 (0.037) [0.015, 0.065] | 0/1216 (0.000) [0.000, 0.000] | 290/1216 (0.238) [0.193, 0.285] | 23/513 (0.045) [0.015, 0.080] | 0/484 (0.000) [0.000, 0.000] |
| same day 2026-11-17 | 649 | 5/649 (0.008) [0.000, 0.017] | 1/649 (0.002) [0.000, 0.005] | 155/649 (0.239) [0.175, 0.304] | 6/288 (0.021) [0.000, 0.045] | 0/279 (0.000) [0.000, 0.000] |
| same day, gap <1.5h | 1238 | 22/1238 (0.018) [0.007, 0.031] | 1/1238 (0.001) [0.000, 0.003] | 296/1238 (0.239) [0.190, 0.291] | 14/541 (0.026) [0.008, 0.049] | 0/521 (0.000) [0.000, 0.000] |
| same day, gap >4h | 627 | 28/627 (0.045) [0.014, 0.088] | 0/627 (0.000) [0.000, 0.000] | 149/627 (0.238) [0.184, 0.295] | 15/260 (0.058) [0.016, 0.107] | 0/242 (0.000) [0.000, 0.000] |
| consecutive pulled days, all | 1282 | 100/1282 (0.078) [0.055, 0.102] | 14/1282 (0.011) [0.005, 0.017] | 317/1282 (0.247) [0.209, 0.288] | 70/523 (0.134) [0.095, 0.180] | 1/447 (0.002) [0.000, 0.007] |
| 2026-11-06>2026-11-09 | 202 | 46/202 (0.228) [0.161, 0.296] | 4/202 (0.020) [0.000, 0.046] | 55/202 (0.272) [0.187, 0.356] | 32/78 (0.410) [0.306, 0.535] | 0/46 (0.000) [0.000, 0.000] |
| 2026-11-09>2026-11-11 | 208 | 6/208 (0.029) [0.000, 0.068] | 1/208 (0.005) [0.000, 0.015] | 49/208 (0.236) [0.172, 0.306] | 3/81 (0.037) [0.000, 0.098] | 1/78 (0.013) [0.000, 0.040] |
| 2026-11-11>2026-11-13 | 194 | 13/194 (0.067) [0.023, 0.120] | 1/194 (0.005) [0.000, 0.017] | 48/194 (0.247) [0.175, 0.321] | 10/75 (0.133) [0.043, 0.263] | 0/64 (0.000) [0.000, 0.000] |
| 2026-11-13>2026-11-16 | 242 | 26/242 (0.107) [0.063, 0.158] | 7/242 (0.029) [0.012, 0.047] | 62/242 (0.256) [0.192, 0.327] | 22/108 (0.204) [0.106, 0.315] | 0/85 (0.000) [0.000, 0.000] |
| 2026-11-16>2026-11-17 | 436 | 9/436 (0.021) [0.007, 0.039] | 1/436 (0.002) [0.000, 0.007] | 103/436 (0.236) [0.183, 0.290] | 3/181 (0.017) [0.000, 0.043] | 0/174 (0.000) [0.000, 0.000] |

## Q3d re-index events per day pair (substantive title change or date value change, stable within each day)

| day pair | gap days | units shown both days | multi-pull both days | display title changes | substantive title events | date value events | date shown/hidden | re-index events | per calendar day | URLs | sites | URLs confirmed | expected from same-day noise | title shown before | strict events (URLs, sites) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2026-11-06>2026-11-09 | 3 | 202 | 0 | 46 | 4 | 0 | 32 | 4 | 1.3 | 4 | 4 | 0 | 0.1 | 1 | 3 (3, 3) |
| 2026-11-09>2026-11-11 | 2 | 208 | 0 | 6 | 1 | 1 | 3 | 2 | 1.0 | 2 | 2 | 1 | 0.1 | 0 | 2 (2, 2) |
| 2026-11-11>2026-11-13 | 2 | 194 | 0 | 13 | 1 | 0 | 10 | 1 | 0.5 | 1 | 1 | 0 | 0.1 | 0 | 1 (1, 1) |
| 2026-11-13>2026-11-16 | 3 | 242 | 0 | 19 | 7 | 0 | 17 | 7 | 2.3 | 4 | 4 | 1 | 0.1 | 2 | 5 (2, 2) |
| 2026-11-16>2026-11-17 | 1 | 436 | 233 | 6 | 1 | 0 | 1 | 1 | 1.0 | 1 | 1 | 0 | 0.1 | 0 | 1 (1, 1) |

Examples of substantive title events:
- 2026-11-06>2026-11-09: https://congenius.com/industries/excavation-estimating-software: 'The Best Excavation Estimating, Takeoff, and Proposal Software' -> 'The Best Estimating Software for Excavation & Earthwork'
- 2026-11-06>2026-11-09: https://www.fieldwire.com/blog/best-construction-management-software: '13 Best Construction Management Software (2026 Comparison)' -> '13 Best Construction Management Software in 2026'
- 2026-11-06>2026-11-09: https://www.hcss.com/: 'HCSS' -> 'HCSS: Construction Software for Contractors of Every Size' (title shown before)
- 2026-11-06>2026-11-09: https://www.ingenious.build/blog-posts/best-civil-project-management-software: 'Best Civil Construction Project Management Software (2026 Guide)' -> 'Top Civil Construction Project Management Software (2026)'
- 2026-11-09>2026-11-11: https://www.smartcrewhq.com/blog/best-software-small-dirt-work-contractors: 'Best All-in-One Software for Small Dirtwork Contractors (2026)' -> 'Best all-in-one software for small dirtwork contractors'
- 2026-11-11>2026-11-13: https://www.explorer-software.com/erp/contractor-types/heavy-civil-contractor: 'Eclipse ERP - Heavy Civil Contractor' -> 'Construction Software for Heavy Civil Contractors | Eclipse ERP'
- 2026-11-13>2026-11-16: https://agtek.com/solutions/takeoff: 'Construction Takeoff Software for Earthworks and Excavation' -> 'Earthworks Takeoff Software for Construction'
- 2026-11-13>2026-11-16: https://www.fieldwire.com/blog/best-construction-management-software: '13 Best Construction Management Software (2026 Comparison)' -> '13 Best Construction Management Software in 2026' (title shown before)
- 2026-11-13>2026-11-16: https://www.hcss.com/products/construction-project-management-software: 'Construction Management Software | HeavyJob' -> 'Construction Field & Project Management Software'
- 2026-11-13>2026-11-16: https://www.explorer-software.com/erp/contractor-types/heavy-civil-contractor: 'Construction Software for Heavy Civil Contractors | Eclipse ERP' -> 'Eclipse ERP - Heavy Civil Contractor' (title shown before)
- 2026-11-16>2026-11-17: https://www.smartcrewhq.com/compare/hcss-alternative: 'hcss alternative' -> 'HCSS Alternative: Read This Before You Sign'

Totals: {"units": 1282, "title_stable_units": 1282, "reindex_events": 15, "title_events": 14, "date_value_events": 1, "display_title_events": 90, "verified_units": 233, "reindex_events_verified": 0, "event_urls": 10, "event_sites": 8, "strict_events": 12, "strict_event_urls": 9, "strict_event_sites": 8, "title_events_seen_before": 3}

## Q3e movement after a re-index event (stratified by window and start band)

| window | signal | event rows | P(moved) diff [CI] | mean abs change diff [CI] | crossed top 10 diff [CI] |
| --- | --- | ---: | --- | --- | --- |
| same | reindex_event | 15 | 0.600 vs 0.594, diff +0.006 [-0.246, +0.295] | 0.87 vs 1.34, diff -0.47 [-0.98, +0.03] | 0.067 vs 0.084, diff -0.017 [-0.111, +0.118] |
| same | reindex_event_strict | 12 | 0.667 vs 0.611, diff +0.056 [-0.218, +0.394] | 1.00 vs 1.42, diff -0.42 [-0.95, +0.12] | 0.083 vs 0.068, diff +0.016 [-0.074, +0.166] |
| same | title_event | 14 | 0.643 vs 0.587, diff +0.055 [-0.175, +0.307] | 0.93 vs 1.33, diff -0.41 [-0.89, +0.05] | 0.071 vs 0.090, diff -0.018 [-0.118, +0.126] |
| same | date_value_event | 1 | 0.000 vs 0.687, diff -0.687 [-0.824, -0.535] | 0.00 vs 1.40, diff -1.40 [-1.83, -1.02] | 0.000 vs 0.000, diff +0.000 [+0.000, +0.000] |
| same | display_event | 90 | 0.567 vs 0.543, diff +0.024 [-0.063, +0.104] | 1.23 vs 1.13, diff +0.11 [-0.23, +0.42] | 0.067 vs 0.043, diff +0.024 [-0.020, +0.081] |
| same | reindex_event, URL clusters | 15 | 0.600 vs 0.594, diff +0.006 [-0.369, +0.256] | | |
| next | reindex_event | 14 | 0.357 vs 0.542, diff -0.185 [-0.399, +0.018] | 0.79 vs 1.24, diff -0.45 [-1.00, +0.14] | 0.214 vs 0.176, diff +0.038 [-0.130, +0.249] |
| next | reindex_event_strict | 11 | 0.364 vs 0.550, diff -0.186 [-0.384, +0.083] | 0.73 vs 1.23, diff -0.50 [-1.10, +0.36] | 0.182 vs 0.180, diff +0.002 [-0.178, +0.278] |
| next | title_event | 13 | 0.385 vs 0.517, diff -0.132 [-0.333, +0.045] | 0.85 vs 1.19, diff -0.34 [-0.87, +0.21] | 0.231 vs 0.151, diff +0.080 [-0.075, +0.273] |
| next | date_value_event | 1 | 0.000 vs 0.864, diff -0.864 [-0.952, -0.774] | 0.00 vs 1.88, diff -1.88 [-2.24, -1.54] | 0.000 vs 0.500, diff -0.500 [-0.626, -0.362] |
| next | display_event | 84 | 0.607 vs 0.560, diff +0.047 [-0.096, +0.167] | 1.85 vs 1.56, diff +0.29 [-0.24, +0.76] | 0.214 vs 0.177, diff +0.037 [-0.057, +0.127] |
| next | reindex_event, URL clusters | 14 | 0.357 vs 0.542, diff -0.185 [-0.420, +0.075] | | |

## Q3e direction

- same: title-event rows that moved 9, with a query-term change 2 (more terms 1, fewer 1). Term rule 2/2 (1.000) [1.000, 1.000]; position rule on same rows 1/2 (0.500) [0.500, 0.500]; difference +0.500 [+0.500, +0.500]. Date-newer-goes-up on 0 moved date-event rows: 0/0.
- next: title-event rows that moved 5, with a query-term change 2 (more terms 1, fewer 1). Term rule 1/2 (0.500) [0.500, 0.500]; position rule on same rows 2/2 (1.000) [1.000, 1.000]; difference -0.500 [-0.500, -0.500]. Date-newer-goes-up on 0 moved date-event rows: 0/0.

## Q3c title tag changes in our fetches vs the SERP title

Labels per pulled day: O = SERP title matches the old tag only, N = new tag only, B = both, x = neither, m = mixed within the day.

| URL | tag changed between fetches | old tag | new tag | queries | SERP days on or after new fetch | first SERP day with new tag | lag (days) | SERP title events after old fetch | verdict |
| --- | --- | --- | --- | ---: | --- | --- | ---: | --- | --- |
| https://agtek.com/products/underground | 2026-11-13 > 2026-11-16 | Underground Utility & Excavation Takeoff Software - AGTEK | Underground Utility Estimating Software - AGTEK | 1 | 2026-11-16, 2026-11-17 | None | None |  | SERP still shows the old tag |
| https://agtek.com/solutions/takeoff | 2026-11-13 > 2026-11-16 | Construction Takeoff Software / Earthworks Takeoff Guide-AGTEK - AGTEK | Earthworks Takeoff Software for Construction - AGTEK | 12 | 2026-11-16, 2026-11-17 | 2026-11-16 | 0 | 2026-11-13>2026-11-16 | SERP took the new tag |
| https://www.hcss.com/products/construction-project-management-software | 2026-11-13 > 2026-11-16 | Construction Management Software / HeavyJob | Construction Field & Project Management Software / HeavyJob | 8 | 2026-11-16, 2026-11-17 | 2026-11-16 | 0 | 2026-11-13>2026-11-16 | SERP took the new tag |
| https://www.smartcrewhq.com/all-in-one-dirt-work | 2026-11-09 > 2026-11-11 | Best all in one dirtworks software | Best All in One Dirtworks Software (2026): Honest Contractor Guide | 11 | 2026-11-11, 2026-11-13, 2026-11-16, 2026-11-17 | None | None |  | SERP shows neither tag |
| https://www.smartcrewhq.com/all-in-one-dirt-work | 2026-11-11 > 2026-11-13 | Best All in One Dirtworks Software (2026): Honest Contractor Guide | Best All-in-One Dirtwork and Heavy Civil Software (2026) | 11 | 2026-11-13, 2026-11-16, 2026-11-17 | None | None |  | SERP shows neither tag |
| https://www.smartcrewhq.com/all-in-one-dirt-work-software | 2026-11-09 > 2026-11-11 | Best all in one dirtworks software | Best All in One Dirtworks Software (2026): Honest Contractor Guide | 6 | 2026-11-11, 2026-11-16, 2026-11-17 | None | None |  | SERP still shows the old tag |
| https://www.smartcrewhq.com/all-in-one-dirt-work-software | 2026-11-11 > 2026-11-16 | Best All in One Dirtworks Software (2026): Honest Contractor Guide | Best All-in-One Dirtwork and Heavy Civil Software (2026) | 6 | 2026-11-16, 2026-11-17 | None | None |  | SERP shows neither tag |
| https://www.smartcrewhq.com/best-all-in-one-dirt-work-software | 2026-11-09 > 2026-11-13 | Best all in one dirtworks software | Best All-in-One Dirtwork and Heavy Civil Software (2026) | 6 | 2026-11-13, 2026-11-16, 2026-11-17 | None | None |  | SERP shows neither tag |
| https://www.smartcrewhq.com/blog/best-software-small-dirt-work-contractors | 2026-11-09 > 2026-11-11 | Best dirt work software for small companies | Best Dirt Work Software for Small Companies (2026) | 5 | 2026-11-11, 2026-11-13, 2026-11-16, 2026-11-17 | None | None | 2026-11-09>2026-11-11 | SERP shows neither tag |
| https://www.smartcrewhq.com/compare/hcss-heavybid-alternative | 2026-11-09 > 2026-11-11 | HCSS HeavyBid alternative | HCSS HeavyBid Alternative? Keep HeavyBid, Fix the Field | 5 | 2026-11-11, 2026-11-13, 2026-11-16, 2026-11-17 | None | None |  | SERP still shows the old tag |
| https://www.smartcrewhq.com/dirt-work-software-with-crm | 2026-11-09 > 2026-11-11 | Dirt work software with CRM | Dirt Work Software With CRM: Leads to Crew in One App | 2 | 2026-11-11, 2026-11-13, 2026-11-16, 2026-11-17 | None | None |  | SERP still shows the old tag |

- https://agtek.com/products/underground (2026-11-13 > 2026-11-16): 2026-11-06 old; 2026-11-09 old; 2026-11-11 old; 2026-11-13 old; 2026-11-16 old; 2026-11-17 old
- https://agtek.com/solutions/takeoff (2026-11-13 > 2026-11-16): 2026-11-06 neither; 2026-11-09 neither; 2026-11-11 neither; 2026-11-13 neither; 2026-11-16 new; 2026-11-17 new
- https://www.hcss.com/products/construction-project-management-software (2026-11-13 > 2026-11-16): 2026-11-06 old; 2026-11-09 old; 2026-11-11 old; 2026-11-13 old; 2026-11-16 new; 2026-11-17 new
- https://www.smartcrewhq.com/all-in-one-dirt-work (2026-11-09 > 2026-11-11): 2026-11-06 neither; 2026-11-09 neither; 2026-11-11 neither; 2026-11-13 neither; 2026-11-16 neither; 2026-11-17 neither
- https://www.smartcrewhq.com/all-in-one-dirt-work (2026-11-11 > 2026-11-13): 2026-11-06 neither; 2026-11-09 neither; 2026-11-11 neither; 2026-11-13 neither; 2026-11-16 neither; 2026-11-17 neither
- https://www.smartcrewhq.com/all-in-one-dirt-work-software (2026-11-09 > 2026-11-11): 2026-11-06 neither; 2026-11-09 neither; 2026-11-11 neither; 2026-11-16 neither/old; 2026-11-17 neither
- https://www.smartcrewhq.com/all-in-one-dirt-work-software (2026-11-11 > 2026-11-16): 2026-11-06 neither; 2026-11-09 neither; 2026-11-11 neither; 2026-11-16 neither; 2026-11-17 neither
- https://www.smartcrewhq.com/best-all-in-one-dirt-work-software (2026-11-09 > 2026-11-13): 2026-11-06 neither; 2026-11-09 neither; 2026-11-13 neither; 2026-11-16 neither; 2026-11-17 neither
- https://www.smartcrewhq.com/blog/best-software-small-dirt-work-contractors (2026-11-09 > 2026-11-11): 2026-11-09 neither; 2026-11-11 neither; 2026-11-13 neither; 2026-11-16 neither; 2026-11-17 neither
- https://www.smartcrewhq.com/compare/hcss-heavybid-alternative (2026-11-09 > 2026-11-11): 2026-11-06 old; 2026-11-09 old; 2026-11-11 old; 2026-11-13 old; 2026-11-16 old; 2026-11-17 old
- https://www.smartcrewhq.com/dirt-work-software-with-crm (2026-11-09 > 2026-11-11): 2026-11-06 old; 2026-11-09 old; 2026-11-11 old; 2026-11-13 old; 2026-11-16 old; 2026-11-17 old

Material rows vs SERP event in the same window: {"event|moved": 5, "event|static": 1, "no event|moved": 8, "no event|static": 1, "not shown both days|moved": 4, "not shown both days|static": 9}

## Q4 lag

| window after the fetch pair | rows | material rows | P(moved) diff [CI] | URL-cluster CI | mean abs change diff [CI] | URL-cluster CI | excluding pages changed again | without client site, P(moved) | without client site, mean abs change |
| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |
| next | 763 | 28 | 0.429 vs 0.401, diff +0.027 [-0.166, +0.205] | [-0.109, +0.152] | 2.18 vs 1.07, diff +1.11 [-0.36, +2.65] | [-0.14, +2.45] | 0.455 vs 0.410, diff +0.044 [-0.126, +0.184] | 0.100 vs 0.248, diff -0.148 [-0.321, +0.058] | 0.10 vs 0.54, diff -0.44 [-0.77, -0.11] |
| next2 | 534 | 15 | 0.400 vs 0.281, diff +0.119 [-0.094, +0.327] | [-0.225, +0.429] | 2.13 vs 0.78, diff +1.35 [+0.09, +2.51] | [-0.69, +3.33] | 0.400 vs 0.281, diff +0.119 [-0.094, +0.327] | n/a | n/a |

Client site only, next window: P(moved) 0.500 vs 0.200, diff +0.300 [-0.250, +0.750] (6 of 18 changed rows matched); mean abs change 3.83 vs 0.94, diff +2.89 [-1.25, +6.39]

Client site only, next2 window: P(moved) 0.167 vs 0.333, diff -0.167 [-0.806, +1.000] (6 of 15 changed rows matched); mean abs change 1.00 vs 0.33, diff +0.67 [-0.81, +6.00]

Material rows by window: {"same_moved": 17, "same_rows": 28, "next_moved": 12, "next_rows": 28, "next2_moved": 6, "next2_rows": 15, "still_in_place_same_then_moved_next": 3, "rows_static_same_window": 11, "static_same_window_with_next_day": 11}

Patterns (same, next, next2; M moved, - static, ? no later day): {"M-?": 7, "--?": 4, "MM-": 4, "---": 3, "MMM": 3, "MM?": 2, "-M-": 2, "M-M": 1, "--M": 1, "-MM": 1}

- agtek.com/products/underground [best excavation software] fetch window 2026-11-13>2026-11-16: 4 -> 2 -> 2 -> None
- agtek.com/solutions/takeoff [all in 1 dirt work software] fetch window 2026-11-13>2026-11-16: 3 -> 1 -> 1 -> None
- agtek.com/solutions/takeoff [all in one dirt work software] fetch window 2026-11-13>2026-11-16: 3 -> 2 -> 2 -> None
- agtek.com/solutions/takeoff [best all in 1 dirt work software] fetch window 2026-11-13>2026-11-16: 4 -> 6 -> 6 -> None
- agtek.com/solutions/takeoff [best all in one dirt work software] fetch window 2026-11-13>2026-11-16: 5 -> 6 -> 6 -> None
- agtek.com/solutions/takeoff [best all-in-one dirt work and excavation software] fetch window 2026-11-13>2026-11-16: 11 -> 11 -> 11 -> None
- agtek.com/solutions/takeoff [best dirt work software] fetch window 2026-11-13>2026-11-16: 11 -> 5 -> 4 -> None
- bidsyncai.com/blog/heavy-civil-construction-software [all in one heavy civil software] fetch window 2026-11-13>2026-11-16: 6 -> 11 -> 11 -> None
- ownrops.com/guides/best-land-clearing-software [dirt work software with crm] fetch window 2026-11-13>2026-11-16: 10 -> 11 -> 11 -> None
- www.hcss.com/products/construction-project-management-software [hcss heavyjob alternative] fetch window 2026-11-13>2026-11-16: 2 -> 2 -> 2 -> None
- www.smartcrewhq.com/all-in-one-dirt-work [all in 1 dirt work software] fetch window 2026-11-09>2026-11-11: 8 -> 9 -> 9 -> 6
- www.smartcrewhq.com/all-in-one-dirt-work [all in one dirt work management software] fetch window 2026-11-09>2026-11-11: 11 -> 11 -> 11 -> 11
- www.smartcrewhq.com/all-in-one-dirt-work [all in one dirt work software] fetch window 2026-11-09>2026-11-11: 11 -> 11 -> 11 -> 6
- www.smartcrewhq.com/all-in-one-dirt-work [all in one dirtwork software] fetch window 2026-11-09>2026-11-11: 11 -> 11 -> 8 -> 11
- www.smartcrewhq.com/all-in-one-dirt-work [all in 1 dirt work software] fetch window 2026-11-11>2026-11-13: 9 -> 9 -> 6 -> 6
- www.smartcrewhq.com/all-in-one-dirt-work [all in one dirt work management software] fetch window 2026-11-11>2026-11-13: 11 -> 11 -> 11 -> 11
- www.smartcrewhq.com/all-in-one-dirt-work [all in one dirt work software] fetch window 2026-11-11>2026-11-13: 11 -> 11 -> 6 -> 6
- www.smartcrewhq.com/all-in-one-dirt-work [all in one dirtwork software] fetch window 2026-11-11>2026-11-13: 11 -> 8 -> 11 -> 5
- www.smartcrewhq.com/all-in-one-dirt-work [best all-in-one dirtworks software] fetch window 2026-11-11>2026-11-13: 5 -> 10 -> 2 -> 2
- www.smartcrewhq.com/all-in-one-dirt-work-software [best all in one dirt work management software] fetch window 2026-11-09>2026-11-11: 11 -> 11 -> 11 -> 11
- www.smartcrewhq.com/all-in-one-dirt-work-software [best all-in-one dirtworks software] fetch window 2026-11-09>2026-11-11: 3 -> 1 -> 11 -> 1
- www.smartcrewhq.com/all-in-one-dirt-work-software [all in one dirtwork software] fetch window 2026-11-11>2026-11-16: 4 -> 10 -> 1 -> None
- www.smartcrewhq.com/all-in-one-dirt-work-software [best all in one dirt work management software] fetch window 2026-11-11>2026-11-16: 11 -> 11 -> 11 -> None
- www.smartcrewhq.com/all-in-one-dirt-work-software [best all-in-one dirtworks software] fetch window 2026-11-11>2026-11-16: 1 -> 1 -> 1 -> None
- www.smartcrewhq.com/best-all-in-one-dirt-work-software [best all-in-one dirtworks software] fetch window 2026-11-09>2026-11-13: 1 -> 2 -> 11 -> 11
- www.smartcrewhq.com/blog/best-software-small-dirt-work-contractors [dirt work management software] fetch window 2026-11-09>2026-11-11: 6 -> 7 -> 11 -> 11
- www.smartcrewhq.com/compare/hcss-heavybid-alternative [hcss heavybid alternative] fetch window 2026-11-09>2026-11-11: 10 -> 9 -> 11 -> 6
- www.smartcrewhq.com/dirt-work-software-with-crm [dirt work software with crm] fetch window 2026-11-09>2026-11-11: 2 -> 6 -> 2 -> 2
