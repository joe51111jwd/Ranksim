# Round 3 study 1: site-restricted order against ordinary order (scripts/claude/r3_validity.py)

REAL positions only. Ordinary lists are design days (through 2026-11-17). Site-restricted lists were pulled on 2026-11-19.

## A. Site-restricted list (Nov 19) against the ordinary Nov 16 deep list

- Lists: 70 on 36 sites. Results per list: {'2': 3, '3': 2, '6': 3, '7': 9, '8': 1, '9': 1, '10': 51}.
- Same-site URLs in the ordinary top 30: 168. Found in the site-restricted top 10: 109.
- Where the site's best ordinary page sits in the site-restricted list: {'1': 42, '2': 7, '3': 1, '4': 1, '5': 2}. Not listed: 17.
- Pairs with both URLs in both lists, same order: 62/74 (0.838) by list [0.735, 0.911] (43 lists), by site [0.738, 0.906] (29 sites).
  - both in ordinary 11 to 30: 8/10 (0.800) by list [0.600, 1.000] (8 lists), by site [0.600, 1.000] (8 sites)
  - both in ordinary top 10: 12/13 (0.923) by list [0.903, 1.000] (4 lists), by site [0.903, 1.000] (4 sites)
  - one in ordinary top 10: 42/51 (0.824) by list [0.694, 0.918] (35 lists), by site [0.697, 0.903] (24 sites)
- Pairs with one URL in the site-restricted top 10 and the other not: the listed one is the higher one in the ordinary list in 51/74 (0.689) by list [0.585, 0.828] (20 lists), by site [0.533, 0.840] (14 sites).
  - both in ordinary 11 to 30: 10/25 (0.400) by list [0.277, 0.688] (9 lists), by site [0.255, 0.688] (7 sites)
  - both in ordinary top 10: 5/6 (0.833) by list [0.800, 1.000] (2 lists), by site [0.800, 1.000] (2 sites)
  - one in ordinary top 10: 36/43 (0.837) by list [0.632, 0.923] (16 lists), by site [0.608, 0.941] (12 sites)

## B. Yardstick: two ordinary pulls of one search on different design days

| Gap | Same-site pairs, same order | All pairs, same order |
| --- | --- | --- |
| gap 1 day | 19/20 (0.950) by list [0.800, 1.000] (12 lists), by site [0.812, 1.000] (5 sites) | 1078/1155 (0.933) by list [0.903, 0.958] (44 lists), by site [0.918, 0.950] (123 sites) |
| gap 2 to 3 days | 44/50 (0.880) by list [0.727, 0.981] (16 lists), by site [0.625, 1.000] (5 sites) | 2332/2680 (0.870) by list [0.836, 0.901] (30 lists), by site [0.846, 0.892] (66 sites) |
| gap 4 to 5 days | 42/49 (0.857) by list [0.653, 1.000] (16 lists), by site [0.739, 1.000] (4 sites) | 1964/2324 (0.845) by list [0.816, 0.873] (30 lists), by site [0.824, 0.867] (61 sites) |

## C. The same site-restricted list pulled twice on one evening

- Lists: 22. URLs: 187 and 186, shared 156.
- Same first page: 19 of 22.
- Pair order agreement: 486/554 (0.877) by list [0.799, 0.944] (22 lists), by site [0.799, 0.944] (22 sites).
  - gap 1: 99/118 (0.839) by list [0.764, 0.911] (20 lists), by site [0.764, 0.911] (20 sites)
  - gap 2: 88/105 (0.838) by list [0.743, 0.925] (18 lists), by site [0.743, 0.925] (18 sites)
  - gap 3 to 4: 148/169 (0.876) by list [0.789, 0.947] (20 lists), by site [0.789, 0.947] (20 sites)
  - gap 5 to 9: 151/162 (0.932) by list [0.855, 0.994] (18 lists), by site [0.855, 0.994] (18 sites)
  - pairs that include the first page: 121/133 (0.910) by list [0.767, 1.000] (21 lists), by site [0.767, 1.000] (21 sites)
