# Round 3 design: ordering the pages of one site for one search (scripts/claude/r3_design.py)

Train lists only (validity-a, train-a). REAL list order from Serper. Model numbers PREDICTED, from site-held-out cross-validation. Ties count half. Design, not a test.

Counts: {"lists_pulled": 212, "listed_pages": 1836, "usable_pages": 1476, "lists_with_pairs": 182, "pairs": 5623, "sites_with_pairs": 87, "searches_with_pairs": 80, "distinct_usable_urls": 1160, "top5_sites_by_pairs": {"getjobber.com": 135, "apple.com": 126, "clockify.me": 126, "foreman.co": 126, "knowify.com": 126}}

| Scorer | All pairs | Minus BM25F | Also holding out search groups |
| --- | --- | --- | --- |
| bm25f | 3452.0/5623 (0.614) list [0.592, 0.636] site [0.588, 0.640] query [0.593, 0.638] | +0.000 list [+0.000, +0.000] site [+0.000, +0.000] query [+0.000, +0.000] |  |
| vote | 3834.0/5623 (0.682) list [0.663, 0.700] site [0.660, 0.702] query [0.664, 0.700] | +0.068 list [+0.050, +0.086] site [+0.049, +0.088] query [+0.050, +0.086] |  |
| ce_title alone | 3903.0/5623 (0.694) list [0.674, 0.714] site [0.673, 0.716] query [0.672, 0.715] | +0.080 list [+0.055, +0.105] site [+0.051, +0.109] query [+0.055, +0.102] |  |
| ce_body_max alone | 3658.0/5623 (0.651) list [0.632, 0.668] site [0.631, 0.671] query [0.631, 0.671] | +0.037 list [+0.016, +0.056] site [+0.018, +0.056] query [+0.015, +0.058] |  |
| logit text | 3894.5/5623 (0.693) list [0.675, 0.712] site [0.672, 0.714] query [0.674, 0.712] | +0.079 list [+0.054, +0.102] site [+0.051, +0.106] query [+0.052, +0.103] | 3767.5/5623 (0.670) list [0.650, 0.690] site [0.648, 0.693] query [0.649, 0.691] |
| logit text+url | 3961.0/5623 (0.704) list [0.686, 0.721] site [0.685, 0.724] query [0.685, 0.722] | +0.091 list [+0.066, +0.115] site [+0.062, +0.117] query [+0.063, +0.116] | 3646.0/5623 (0.648) list [0.627, 0.669] site [0.623, 0.674] query [0.626, 0.670] |
| logit text+url+shape | 3947.0/5623 (0.702) list [0.683, 0.719] site [0.682, 0.721] query [0.682, 0.721] | +0.088 list [+0.065, +0.112] site [+0.061, +0.113] query [+0.062, +0.113] | 3684.0/5623 (0.655) list [0.632, 0.678] site [0.627, 0.680] query [0.630, 0.680] |
| logit text+url+shape+site | 3985.0/5623 (0.709) list [0.689, 0.727] site [0.689, 0.729] query [0.687, 0.729] | +0.095 list [+0.070, +0.120] site [+0.065, +0.122] query [+0.067, +0.121] | 3679.0/5623 (0.654) list [0.632, 0.677] site [0.626, 0.680] query [0.630, 0.679] |
| logit lexical only | 3770.0/5623 (0.670) list [0.652, 0.690] site [0.648, 0.691] query [0.650, 0.691] | +0.057 list [+0.035, +0.077] site [+0.032, +0.080] query [+0.034, +0.079] | 3635.0/5623 (0.646) list [0.625, 0.669] site [0.622, 0.671] query [0.624, 0.668] |
| logit lexical+url | 3897.0/5623 (0.693) list [0.674, 0.710] site [0.674, 0.714] query [0.671, 0.713] | +0.079 list [+0.056, +0.101] site [+0.053, +0.105] query [+0.052, +0.105] | 3490.0/5623 (0.621) list [0.596, 0.645] site [0.593, 0.648] query [0.595, 0.645] |
| gbm text+url+shape | 3937.0/5623 (0.700) list [0.682, 0.718] site [0.681, 0.719] query [0.680, 0.720] | +0.086 list [+0.061, +0.111] site [+0.057, +0.113] query [+0.057, +0.113] | 3633.5/5623 (0.646) list [0.626, 0.666] site [0.625, 0.667] query [0.624, 0.667] |
| gbm text+url+shape+site | 4001.5/5623 (0.712) list [0.693, 0.730] site [0.692, 0.731] query [0.692, 0.731] | +0.098 list [+0.073, +0.123] site [+0.068, +0.125] query [+0.069, +0.124] | 3620.0/5623 (0.644) list [0.622, 0.665] site [0.620, 0.667] query [0.621, 0.666] |
| blend text+url+shape | 3988.5/5623 (0.709) list [0.691, 0.727] site [0.689, 0.729] query [0.690, 0.727] | +0.095 list [+0.071, +0.119] site [+0.066, +0.122] query [+0.069, +0.120] | 3771.0/5623 (0.671) list [0.650, 0.691] site [0.648, 0.693] query [0.649, 0.693] |
| blend text+url+shape+site | 4015.0/5623 (0.714) list [0.694, 0.732] site [0.695, 0.734] query [0.694, 0.733] | +0.100 list [+0.075, +0.125] site [+0.071, +0.128] query [+0.072, +0.126] | 3780.0/5623 (0.672) list [0.653, 0.692] site [0.650, 0.694] query [0.650, 0.695] |

## Slices

| Slice | bm25f | vote | logit text+url+shape | gbm text+url+shape | blend text+url+shape | blend text+url+shape+site |
| --- | --- | --- | --- | --- | --- | --- |
| all pairs | 0.614 (5623) | 0.682 (5623) | 0.702 (5623) | 0.700 (5623) | 0.709 (5623) | 0.714 (5623) |
| pairs with the list's first page | 0.761 (1253) | 0.834 (1253) | 0.876 (1253) | 0.869 (1253) | 0.879 (1253) | 0.873 (1253) |
| pairs without the first page | 0.572 (4370) | 0.638 (4370) | 0.652 (4370) | 0.652 (4370) | 0.661 (4370) | 0.668 (4370) |
| adjacent in the list (gap 1) | 0.532 (1207) | 0.568 (1207) | 0.570 (1207) | 0.581 (1207) | 0.580 (1207) | 0.591 (1207) |
| gap 2 to 4 | 0.606 (2692) | 0.668 (2692) | 0.686 (2692) | 0.678 (2692) | 0.688 (2692) | 0.692 (2692) |
| gap 5 or more | 0.684 (1724) | 0.783 (1724) | 0.819 (1724) | 0.818 (1724) | 0.832 (1724) | 0.834 (1724) |
| same template (same first folder, same depth, depth 2 or more) | 0.640 (2038) | 0.700 (2038) | 0.697 (2038) | 0.694 (2038) | 0.700 (2038) | 0.708 (2038) |
| both in the list's top 5 | 0.638 (1511) | 0.698 (1511) | 0.727 (1511) | 0.719 (1511) | 0.732 (1511) | 0.732 (1511) |
| swap pairs (286 rows on 37 sites) | 0.563 | 0.645 | 0.629 | 0.608 | 0.612 | 0.601 |

## Single features (larger value first; ties half)

| Feature | All pairs | Decided pairs |
| --- | --- | --- |
| ce_title | 0.694 [0.673, 0.716] | 0.694 of 5613 |
| emb_title | 0.687 [0.665, 0.710] | 0.687 of 5613 |
| emb_body_max | 0.666 [0.644, 0.689] | 0.667 of 5605 |
| ce_body_top3 | 0.661 [0.639, 0.684] | 0.661 of 5617 |
| title_cov | 0.659 [0.640, 0.678] | 0.797 of 3009 |
| title_focus | 0.659 [0.638, 0.678] | 0.695 of 4577 |
| title_idf_cov | 0.655 [0.634, 0.676] | 0.756 of 3390 |
| ce_body_max | 0.651 [0.631, 0.671] | 0.651 of 5615 |
| h1_cov | 0.634 [0.614, 0.653] | 0.748 of 3035 |
| slug_focus | 0.630 [0.607, 0.653] | 0.682 of 4016 |
| h1_idf_cov | 0.629 [0.607, 0.651] | 0.716 of 3366 |
| body_density | 0.628 [0.604, 0.652] | 0.629 of 5601 |
| meta_cov | 0.625 [0.604, 0.646] | 0.717 of 3239 |
| first100_cov | 0.617 [0.595, 0.638] | 0.700 of 3275 |
| bm25f_norm | 0.614 [0.588, 0.640] | 0.614 of 5603 |
| slug_idf_cov | 0.612 [0.589, 0.635] | 0.693 of 3264 |
| bm25_body_norm | 0.610 [0.584, 0.637] | 0.611 of 5602 |
| slug_cov | 0.610 [0.588, 0.632] | 0.704 of 3029 |
| anchor_cov | 0.589 [0.570, 0.607] | 0.739 of 2094 |
| head_cov | 0.580 [0.562, 0.599] | 0.652 of 2962 |
| in_list_link_share | 0.557 [0.538, 0.576] | 0.610 of 2920 |
| all_terms_fields | 0.554 [0.541, 0.568] | 0.882 of 799 |
| body_min_window | 0.449 [0.422, 0.473] | 0.437 of 4621 |
| all_terms_title | 0.549 [0.536, 0.563] | 0.905 of 685 |
| has_anchor | 0.544 [0.529, 0.559] | 0.639 of 1794 |
| body_phrase_log | 0.539 [0.526, 0.553] | 0.752 of 866 |
| body_cov | 0.538 [0.522, 0.555] | 0.599 of 2146 |
| pt_product | 0.536 [0.517, 0.555] | 0.601 of 2003 |
| title_phrase | 0.527 [0.518, 0.537] | 0.896 of 386 |
| home_linked | 0.527 [0.513, 0.542] | 0.615 of 1321 |
| url_depth | 0.474 [0.454, 0.493] | 0.431 of 2106 |
| url_len | 0.475 [0.450, 0.501] | 0.474 of 5486 |
| pt_blog | 0.476 [0.462, 0.491] | 0.387 of 1173 |
| n_headings | 0.522 [0.493, 0.550] | 0.525 of 4965 |
| h1_phrase | 0.522 [0.513, 0.532] | 0.841 of 359 |
| is_home | 0.521 [0.511, 0.530] | 0.730 of 503 |
| slug_phrase | 0.518 [0.511, 0.526] | 0.813 of 321 |
| title_len | 0.484 [0.460, 0.507] | 0.479 of 4439 |
| has_product_schema | 0.515 [0.502, 0.527] | 0.611 of 745 |
| log_internal_links | 0.488 [0.458, 0.516] | 0.486 of 4787 |
| pt_industry | 0.511 [0.502, 0.522] | 0.704 of 301 |
| has_article_schema | 0.491 [0.474, 0.507] | 0.459 of 1241 |
| pt_pricing | 0.491 [0.484, 0.497] | 0.367 of 373 |
| has_date | 0.507 [0.496, 0.520] | 0.547 of 896 |
| log_external_links | 0.506 [0.482, 0.531] | 0.510 of 3253 |
| pt_listing | 0.494 [0.488, 0.500] | 0.343 of 204 |
| pt_compare | 0.505 [0.491, 0.517] | 0.538 of 702 |
| log_words | 0.496 [0.468, 0.522] | 0.496 of 5609 |
| pt_help | 0.497 [0.492, 0.501] | 0.377 of 151 |
| has_faq_schema | 0.502 [0.492, 0.513] | 0.516 of 874 |
