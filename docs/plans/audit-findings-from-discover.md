# Audit findings on real sites (discover session, Nov 14, 2026)

Integration 4ce8d50, local server, no Serper key. SmartCrew had `forward_serp.csv` imported. hcss.com, mitti.com
and myquoteiq.com were opened by URL with no Google data. Every issue was checked against the stored raw HTML
(`rs_pages.raw_html`). All 103 SmartCrew, 68 HCSS and 67 Mitti selectors match exactly one element, and
`element.text` matches that element's text. Title and meta lengths are measured correctly. No generated comment
contains an em or en dash.

The problems below are for the audit session (audit.py). Line numbers refer to 4ce8d50. Within each section the
false alarms come first.

## High severity false alarms

1. **canonical-other (all 4 SmartCrew issues).** `/all-in-one-dirt-work/`, `/best-all-in-one-dirt-work-software/`,
   `/construction-management-software/` and `/best-construction-management-software-for-small-business/` serve the
   same HTML, byte for byte, as the page their canonical names. That canonical is correct, so "point it at itself"
   would create duplicate content.
   - Cause: `check_indexable` (1081-1090).
   - Fix: skip the issue, or make it a low note, when the canonical target has the same text or is not crawled.
2. **The alias pages also get 16 more issues** (title-search, h1-search, no-answer, behind, title-long, meta-long,
   not-ranking, schema-gap).
   - Cause: `ok_docs` (785) still includes non-self-canonical pages. Only the duplicate checks use `indexed()` (789).
   - Fix: leave alias pages out of the content checks.
3. **unreadable, high (4 myQuoteIQ pages), plus a "Broken link to /" that follows from them.**
   - `https://myquoteiq.com/` loads with a 200 in about 10s (curl). Our fetch timed out, and the comment then says
     "If Google gets the same, this page can't rank".
   - Fix:
     - Retry once with a longer timeout.
     - Word a timeout as "took over N seconds to answer", at medium or low severity.
     - Never turn our own fetch failure into a broken-link issue.

## Searches the tool guessed, or that the page does not target

4. **title-search and h1-search with no search data (HCSS 5, Mitti 5).** For example, `/blog/award-nominees-2023/`
   gets "is your page for 'when greatness in construction aligns'". `target()` (821) returns `rows[0]["q"]` even when
   the row is unchecked, and the search came from the page's own title.
   - Fix: no target without checked Google data, or low severity worded as "might be searched".
5. **title-search, h1-search, no-answer and title-long push a different search onto a page with another topic.**
   `/construction-management-software/` already ranks #2 and #8 for its search, yet the issue suggests a new title.
   - Fix: never push a target into the title or H1 when the page already ranks top 3, or when its title and H1 cover
     another topic.
6. **Word matching.** "dirt work" is flagged as missing from "Dirtwork", and the reverse. "all in 1" never matches
   "one". The Mitti title "Términos" is flagged as missing "terminos", and the suggestion drops the accent.
   - Cause: `tokens`, `covers` and `missing_words` (171-188), and `cased` (190).
   - Fix: compare with spaces and hyphens removed, treat 1 as one, and fold accents with NFKD.

## Wrong counts and comparisons

7. **schema-gap says "8 of the top 3 mark up BreadcrumbList".** `users[kind]` is gathered across all searches (1597),
   and its entries are bare paths. Also, BreadcrumbList on a home page is not a gap (no `is_home` exemption).
8. **The named "top 3" are not the pages that were compared.** `leaders()` (837) names results that have no stored
   HTML, while `top_docs()` (825) supplies the facts.
9. **thin-vs-top word counts mix two counting methods.** `PageDoc.words` (437) depends on whether the page was
   parsed. The home page is also compared to list articles ("aim for 2,552 words").
10. **thin counts nav and footer words.** For example, Mitti `/legal` has 61 of its 130 words from the footer. Legal
    pages should also be exempt from thin (10 Mitti issues).
11. **few-links.**
    - `/?try=home` and `/` count as different pages, because `url_key` keeps the query string (223).
    - Legal pages are flagged: 15 Mitti issues, plus SmartCrew's privacy and terms pages.
    - The comment says "links to 1 pages" (1406).
12. **www and bare home pages are treated as two pages.** For HCSS this creates title-duplicate, meta-duplicate and
    og-missing pairs, and "the same element is on 2 pages".
    - Fix: dedupe `parsed` by `url_key` (744).
13. **link-generic.**
    - Desktop and mobile copies of the same link are counted twice (HCSS home: 10 issues for 5 links).
    - External links are flagged (all 11 Mitti issues, 5 for HCSS). Five "here" links to the same URL produce five
      issues.
    - Fix: internal links only, one issue per (url, label) per page.
14. **img-alt on decorative SVG icons that sit next to a heading or a text link** (HCSS). The right fix there is
    `alt=""`.
15. **url-hygiene flags `/pt-BR`.** It is a language code and the site's own hreflang value.

## Suggestions that can't be pasted

16. **title-long suggestions end in "..."** (SmartCrew 11, Mitti 9, HCSS 6). Cause: 1172. Mitti's brand suffix
    "Mitti (by SafetyCulture)" is never stripped, because `core_title` (914) compares the brand exactly.
17. **meta suggestions.**
    - Several are cut off and end in "...".
    - One is shorter than META_MIN, so it would trigger meta-short itself.
    - The HCSS suggestion is the site-wide event banner, "February 15–18 · Houston, TX ...". It contains an en dash.
      Cause: `_meta_from_page` (1218) uses whole-page paragraphs.
    - Fix: prefer og:description, then paragraphs from main only.
    - Never suggest text that ends in "..." or is under 70 characters.
18. **title-long at 61 to 64 characters, at medium severity.** Most of these fit within Google's roughly 600px limit
    (61 characters measures 557px). Twenty of myQuoteIQ's 25 issues are this one.
    - Fix: a pixel estimate, or about 65 characters.
    - An overflow that is only the brand is not worth an issue.
    - `short()` (167) also drops a whole word when the cut lands exactly on a space.

## Wording

19. Stopword stripping produces "missing 'all one dirt work management software'".
20. "links to 1 pages". `plural()` is needed (1406).
21. schema-gap lists 16 type names (Answer, ListItem, QuantitativeValue...). Only name the types that matter.
22. "but they mark up AggregateRating" has no antecedent for "they".
23. not-ranking says "close the gaps" when it lists no gaps.
24. heading text is split mid-word: `<h3><strong>QR Code Form</strong>s</h3>` reads "QR Code Form s". Inline elements
    should be joined with "".
25. An `<h3>` wrapping a whole section is reported as a level skip. It should be reported as invalid markup.
26. og-missing says "the preview is blank or wrong" when only og:description is missing.

Evidence scripts: `auditreview/` and `auditreview2/` in the discover session scratchpad (not in git).
