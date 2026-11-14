# Discovery and competitor review (audit session, Nov 14, 2026)

Run on integration 4ce8d50 plus the audit branch, local server, no Serper key. Each site was analyzed live
(`POST /api/v1/sites`), then `evals/forward-2026-11-09/forward_serp.csv` was imported and pulled so the competitor
views have real Google results. All four sites are in the same niche, so the same 30 SERPs apply to each.

Sites: www.smartcrewhq.com (client), mitti.com, www.getonecrew.com, www.hcss.com.

## Discovery (searches found from the site)

Of the 20 searches per site, roughly 12 are plausible for SmartCrew, 3 for Mitti, 8 for OneCrew and 6 for HCSS.

### 1. The page sample decides the searches, and it is a poor sample
- **Mitti:** 22 of the 32 pages read are `/legal/*` or language homes (`/de`, `/es`, `/fr`, `/nl`, `/pt-BR`). The
  product pages are barely read.
- **HCSS:** 30 of 35 pages are `/blog/` and `/news/` posts.
- **OneCrew:** the reader picked up `/schedule/demo-lg`, `/demo-md`, `/demo-sm`, `/referrals/signup`,
  `/summit/register`, `/stripeit` and five `/dev-tc/*` staging pages.
- **SmartCrew:** the reader took 30 of about 55 sitemap pages and missed every `/compare/*` page. So "hcss
  alternative" and "b2w alternative", SmartCrew's top targets, are not discovered at all, although the pages exist.

Suggestion: rank the sitemap URLs before the cap.
- Put first: the home page, then product, feature, solution, industry, compare and vs pages, then the blog.
- Leave out: `/legal`, `/privacy`, `/terms`, `/cookie`, `/careers`, `/jobs`, `/press`, `/news`, `/schedule`,
  `/demo`, `/referral`, `/signup`, `/register`, `/login`, `/dev*`, `/staging`, `/test`, and paths that start with
  another language code. Use `<html lang>` too.

### 2. Boilerplate and legal phrases become searches
Mitti's list:
- "service specific terms and conditions" (ranked first, intent commercial)
- "embedded software supplemental terms"
- "privacy portal"
- "master partner agreement"
- "website terms and conditions"
- "safeshed terms and conditions"
- "acceptable use policy"
- "brentford bootcamp 2023 terms and conditions"
- "new standard contractual clauses"
- "adequacy decision"
- "agb and datenschutz", "terminos y privacidad" (not English)

Suggestion: drop phrases from legal pages, and phrases made of legal words (terms, conditions, privacy, policy,
agreement, clauses, gdpr, cookie, adequacy, sub-processors).

### 3. Section labels, taglines and press headlines pass as searches
| Site | Phrases |
| --- | --- |
| SmartCrew | "what it does and costs" (transactional), "pricing setup crews and equipment", "leads to crew in one app", "guides for running a dirtwork company", "software alternatives by crew size" |
| Mitti | "workplace operations system", "operations system", "partner program", "use of software", "no charge services" |
| OneCrew | "product updates", "top picks for 2026", "refer a contractor", "first end to end platform", "deliver industry first unified platform", "powering the best in the business", "software trusted by top paving contractors", "paving and construction contractor resources" |
| HCSS | "q3 2025", "q2 2025 release", "q4 2025 1 heavybid release", "usa today top workplace", "always on support always human", "heat stress awareness", "asc boot camp", "consulting connect", "when greatness in construction aligns" |

Suggestion:
- A search should name a product or category noun (software, app, platform, crm, estimating, takeoff...) or a
  known brand, or come from Google (related searches, people also ask, Search Console).
- Title-case marketing lines with no category noun should need a second source.
- Drop phrases that are mostly a year or a release name.

### 4. Slug-only phrases from utility URLs
"demo lg", "demo md" and "demo sm" each score 5.25 as transactional searches, from slugs alone. A slug should never
be the only source for a short phrase (under 3 words) or for a phrase on a utility path.

### 5. Truncated titles
"Embedded Software Supplemental…" became its own search, "embedded software supplemental", next to the full one.
Titles that end in an ellipsis should be skipped or merged.

### 6. Intent labels
- "service specific terms and conditions": commercial
- "potential change order": transactional
- "usa today top workplace": commercial
- "what it does and costs": transactional
- "overbilling vs underbilling": commercial, but it is an informational accounting topic ("vs" alone should not make
  a phrase commercial when neither side is a product)

### 7. Relevance is always 1
Every phrase from the site's own pages scores relevance 1, so relevance never demotes off-topic own-site phrases
such as legal pages or press releases. Consider measuring it against the home page and product pages only.

### 8. Near duplicates and years
- "grading contractor software" and "grading contractor software for machine hours" both kept.
- "best excavation software for contractors 2026" kept while "excavation software" exists.
- "hcss vs busybusy 2026" keeps the year.

Searchers rarely type the year, so a year variant should merge into its base.

## Competitors (after importing the real SERP CSV)

1. **The order of picked competitors does not follow share.**
   - SmartCrew's picks: hcss.com (26 slots), myquoteiq.com (13), agtek.com (12), sage.com (6), stackct.com (8).
   - synkedup.com (8 slots) and fieldwire.com (7) are not picked; sage.com (6) is.
   - If visibility is the intended order, show it on the Competitors screen. Sage is a general accounting company,
     not a dirt work software competitor.
2. **Help-center subdomains are counted as competitor slots:** "includes hcss.com, help.hcss.com". A help article
   (help.hcss.com "How to setup package alternates") ranks for "hcss alternative" by name match, not as a rival
   page. Consider counting support subdomains apart.
3. **Directories:** glassdoor.com is listed as a software directory (it is jobs and reviews of employers).
4. **The page matched to a search can be the wrong one.** For "excavation software", SmartCrew's matched page is
   `/best-construction-management-software-for-small-business/`, while `/best-excavation-software/` and
   `/excavation-software-for-contractors-with-5-to-100-employees/` exist. The content gap then compares the wrong
   page. (This is `_best_page` in report.py.)
5. **`report["gaps"]` has two shapes.** It is a list for SmartCrew and an empty dict `{}` for Mitti, OneCrew and
   HCSS, even though all four have the same 30 checked searches. Either the gaps did not run for those projects or
   the empty case returns the wrong type. The Competitors screen should get one shape.
6. **What looks right:**
   - The own site is marked `you` in share.
   - Forums, video, social and marketplaces are separated from competitors.
   - Competitor reasons are given ("has demo, trial or quote buttons").
   - HCSS's own view moves hcss.com to `you` and picks the rest correctly.
