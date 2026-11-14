# Workstream 3: discover

Owner: build session `claude/rs-discover`. Spec from James: "Competitors: if you just look up their site, all their
keyword searches that pop up... that needs to auto-pull. Needs to be really simple."

## What changes

1. **Search discovery** (`discovery.py` after P0; `discovery_next.py` until then).
   Candidates come from several sources, each labeled and weighted:

   | Source | Label | Base weight |
   | --- | --- | --- |
   | Site title parts | `title` | 3.0 |
   | H1 | `h1` | 3.0 |
   | URL slug | `slug` | 2.0 |
   | H2 | `h2` | 1.0 |
   | Meta description noun phrases | (not used, too noisy) | |
   | Google related searches stored with a pull | `related` | 1.5 |
   | Google people also ask stored with a pull | `paa` | 1.0 |
   | Titles of other sites' pages in the top 10 | `competitor` | 1.0 per page, needs 2 pages |
   | Plugin `discovery_sources` rows (GSC etc.) | the row's `source` | `1.5 + log10(1 + weight)` |

   Taglines are not searches. A long title part is cut into the noun phrase that ends in a product word
   ("All-in-one dirtwork software for excavation contractors" gives "all in one dirtwork software" and
   "dirtwork software for excavation contractors" is kept only if short enough). Phrases with "you", "your", "we",
   "our" and imperative openers ("run", "get", "build") are dropped as taglines.

   Brand: the brand phrase, the brand without spaces ("dirt crew", "dirtcrew"), and the domain label tokens. A two
   word brand blocks the pair, not each word, so "Dirt Crew" never blocks "dirt work software".

   Score = evidence x relevance x value. Evidence is the sum of source weights with a bonus for more than one
   distinct source. Relevance is how many of the phrase's words appear in the site's own titles, H1s, H2s and meta
   descriptions (outside sources below 0.5 are dropped, so a SERP's off-topic related searches stay out). Value comes
   from intent: transactional 1.0, commercial 0.9, informational 0.6, navigational 0.25.

   Intent rules (first match wins, the rule is reported): login or account words: navigational; buy, price, cost,
   quote, demo, trial, near me, hire: transactional; best, top, vs, alternative, review, compare, software, app, tool,
   platform, company, contractor, service: commercial; how, what, why, when, guide, tips, examples, question form:
   informational; a known brand alone (yours or a competitor's): navigational; otherwise informational.

   Near duplicates merge (plural s, "all in 1" = "all in one", "dirtwork" = "dirt work", word order, Jaccard >= 0.75).
   The merged entry keeps every source. `MAX_SEARCHES = 20` (was 15). It stays a constant because each search is one
   Google credit per pull.

   Google Autocomplete is not used. Its terms do not clearly permit automated use, so it is left out.

2. **Competitor discovery** (`competitors.py`). Every domain in the top 10 gets a kind: `you`, `competitor`,
   `directory` (G2, Capterra, GetApp, Software Advice, TrustRadius, Yelp, Clutch...), `marketplace`, `forum`
   (Reddit, Quora, community subdomains), `social`, `video` (YouTube, Vimeo), `publisher` (Wikipedia, Forbes, news,
   review blogs, `.gov`, `.edu`). Known lists first, then transparent heuristics (domain words like "review" or
   "best", listicle titles on every page, Article only schema vs Product or SoftwareApplication schema). Each row has
   a `reason`. Competitors rank by visibility: sum over searches of `(11 - position) / 10`.
   Directories are reported as their own group because their listicles rank. A business's subdomains
   (`help.hcss.com`, `hcss.com`) merge into one competitor row with a `hosts` list; forum and app store subdomains
   stay separate. Country versions of known sites (`amazon.co.uk`, `capterra.ca`) match by name.
   Phase B note: once rows merge by registrable domain, `report["rankings"][...]["top"][...]["competitor"]` must
   compare `base_domain(item.domain)` to the competitor list, not the raw host.

3. **Competitor profiles**: per competitor, the searches they rank for (position, page, title), best pages, average
   position, title patterns (separator, brand suffix, year, number, common words, average length), word counts
   (median, range), structured data types, and heading counts.

4. **Content gaps**: per search, compare the user's page (the ranking page, else the best matching page) with the
   pages above it (or the top 10 when not ranking). Report headings topics, terms (one and two word) and structured
   data types that at least 2 pages and at least 40% of those pages share but the user's page lacks. Every gap lists
   the pages that have it. Footer, nav, aside and header headings are ignored, word forms match ("schedule crews"
   covers "crew scheduling"), the user's own brand is never a gap, and when the user's page is not stored the gap
   says `checked: false` with a note.

## API

`report["rankings"]`, `report["competitors"]` and `report["share"]` keep their shapes. `share` rows gain `kind`
and `reason`. New keys:

- `report["discovery"]`: `{limit, searches: [{q, score, intent, intent_rule, relevance, sources: [{source, weight,
  detail}], variants, page}], dropped: [{q, reason}], source_counts}`
- `report["competitor_profiles"]`: `{competitors: [...profile], directories: [...], platforms: [...], publishers: [...]}`
- `report["gaps"]`: `[{q, page, compared: [urls], topics: [...], terms: [...], schema: [...], words: {...}}]`

## SERP features contract (for the tracking session)

Discovery reads related searches and people also ask for each search from the latest pull's `google.json`. Each
entry in `searches` may carry `related_searches` (or `related`) and `people_also_ask` (or `paa`): lists of strings or
objects with `query`, `question`, `title` or `q`. Missing keys are fine; discovery degrades to the other sources.

## UI (phase B)

- `js/screens/searches.js`: table of search, your position, PREDICTED position, intent, source, best page, Open in
  editor. Row opens a sheet with the real top 10 and the gaps for that search.
- `js/screens/competitors.js`: competitor profile cards, gaps, and the directories group.

## Phases

- Phase A: new logic in `discovery_next.py` and `competitor_profiles.py`, tests in `tests/test_discovery.py`.
- Phase B (after P0): move into `discovery.py` and `competitors.py`, wire into analysis and `build_report`, build the
  two screens, verify with Playwright against the SmartCrew import.
