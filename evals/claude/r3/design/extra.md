# Round 3 design, extra studies (scripts/claude/r3_design_extra.py)

Training lists only. PREDICTED scorer outputs against REAL site-restricted list order. Design, not a test.

## A. Other readings of topic match, nothing fitted

18 single readings range from 0.636 to 0.694. Three unfitted mixes: 0.695, 0.684, 0.694.

| Reading | Pair accuracy [95% by site] |
| --- | --- |
| mix: cross-encoder l6 + embedding, title H1 meta | 0.695 [0.672, 0.718] |
| cross-encoder minilm-l6: title, H1 and meta | 0.694 [0.673, 0.716] |
| mix: title, URL words, intro, headings | 0.694 [0.673, 0.717] |
| cross-encoder minilm-l6: title alone | 0.693 [0.671, 0.715] |
| cross-encoder minilm-l12: title, H1 and meta | 0.692 [0.672, 0.712] |
| embedding bge-small: title, H1 and meta | 0.687 [0.665, 0.710] |
| mix: both cross-encoders + embedding on title H1 meta, plus URL words | 0.684 [0.663, 0.706] |
| cross-encoder minilm-l12: title alone | 0.681 [0.660, 0.702] |
| embedding bge-small: title alone | 0.681 [0.658, 0.704] |
| cross-encoder minilm-l12: intro (first 120 words) | 0.667 [0.649, 0.686] |
| cross-encoder minilm-l6: intro (first 120 words) | 0.665 [0.646, 0.684] |
| embedding bge-small: intro (first 120 words) | 0.664 [0.642, 0.684] |
| embedding bge-small: headings | 0.662 [0.640, 0.685] |
| cross-encoder minilm-l6: H1 alone | 0.655 [0.630, 0.680] |
| cross-encoder minilm-l12: H1 alone | 0.650 [0.627, 0.672] |
| cross-encoder minilm-l6: headings | 0.647 [0.626, 0.668] |
| embedding bge-small: H1 alone | 0.646 [0.627, 0.665] |
| cross-encoder minilm-l12: headings | 0.644 [0.624, 0.666] |
| cross-encoder minilm-l6: URL words | 0.639 [0.613, 0.666] |
| embedding bge-small: URL words | 0.639 [0.613, 0.666] |
| cross-encoder minilm-l12: URL words | 0.636 [0.612, 0.660] |

## B. Training lists pulled twice, 40 minutes apart

20 lists, 594 pairs of the first pull. Status under the second pull: {"consensus_both": 365, "consensus_lower_missing": 90, "disagree_flipped": 55, "disagree_higher_missing": 29, "both_missing": 55}.

| Truth definition | blend, site held out | blend, site and search group held out | bm25f |
| --- | --- | --- | --- |
| single pull, all pairs | 0.672 | 0.668 | 0.567 |
| consensus (missing page ranks below) | 0.686 | 0.675 | 0.580 |
| half credit on all pairs | 0.657 | 0.646 | 0.569 |
| consensus, both pages in the second pull | 0.696 | 0.679 | 0.586 |
| flipped in the second pull | 0.545 | 0.600 | 0.527 |
| lower page missing from the second pull | 0.644 | 0.656 | 0.556 |
| higher page missing from the second pull | 0.724 | 0.759 | 0.414 |
| both missing | 0.655 | 0.636 | 0.582 |
