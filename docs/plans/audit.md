# Audit workstream plan (session 4, branch `claude/rs-audit`)

James's spec: "Put in your site, and it just says, look at all the issues. These are specific issues. You can
point them out. There are automatic comments that will populate for each part."

Goal: every issue points at one exact element of the page, says in plain words what is wrong with the numbers,
says what to change, and where possible offers ready-to-paste text built only from the page's own words.

## Phase A (before P0): the engine, in new files only

- `src/aeo/ranksim/audit_rules.py`: the whole engine. Becomes `audit.py` after P0.
- `tests/test_audit.py`: one fixture page per check, selectors verified with two real HTML parsers
  (lxml and Python's html.parser), no duplicate issues, severity order, the script-built page case, and
  SmartCrew's real homepage snapshot (`smartcrew/pr8/home.html`).

### Pieces

- `PageDoc(url, html, status, error, http_status, headers, image_bytes)`: one page parsed once with
  BeautifulSoup and lxml. Knows its title, meta description, robots tags, canonical, viewport, headings,
  main content block, links, images, JSON-LD (parsed or with the parse error), schema types, Open Graph and
  Twitter tags, visible words, words hidden by CSS (`hidden`, inline `display:none`, and classes the page's own
  `<style>` hides such as `.crawl-boot{clip:rect(0,0,0,0)}`), and script weight.
- `AuditContext(pages, rankings, docs, project, robots_txt, statuses, brand)`: what the checks read.
  `rankings` are the report rows (`q, position, url, checked, top[]`), `docs` the top-10 pages as PageDocs,
  `statuses` the crawl status of internal URLs (for broken links).
- `selector_for(tag)`: a selector that matches exactly that element. Head tags use attributes
  (`meta[name="description"]`, `link[rel="canonical"]`), body tags use the nearest unique id and then
  `tag:nth-of-type(n)` steps. Loose table rows use a descendant step so the selector still matches after a
  browser inserts `tbody`. Script elements are never part of a body path, so the editor preview (which strips
  scripts) still matches.
- `CHECKS` registry plus `register_check`. `run_audit(ctx)` runs `CHECKS` and every check from the
  `audit_checks` plugin hook (BUILD_PLAN 4.2). A failing check is logged and skipped.
- `finalize`: drops duplicates (same id, or same kind, page, search, element and title), merges the same
  template element with the same fix across pages into one issue with `pages` and `count`, scores, and sorts by
  severity, then score, then ranking issues first.
- `group_issues` (by page and by search) and `summarize` (counts) for the Issues screen and the Overview.

### Issue shape (BUILD_PLAN 4.4 plus extra keys)

```
{id, severity, kind, title, comment, fix, page, search?,
 element?: {selector, text, tag, attr?},
 suggestion?, action?, evidence?: [str],
 impact (1-10), effort (1-3), score, pages?: [url], count?}
```

`action` says how the suggestion applies to the element, so "Apply suggestion" in the editor is mechanical:

| action | meaning |
| --- | --- |
| `text` | replace the element's text |
| `attr` | set `element.attr` to the suggestion |
| `retag` | change the tag name to the suggestion (`h2`), keeping children and attributes |
| `append`, `prepend`, `after` | insert the suggestion as HTML inside the end, inside the start, or after the element |
| `copy` | nothing to apply in the page (a cleaner URL); the user copies it |

Score: `impact * weight / effort`, weight grows a little when one template fix covers several pages.

### Checks

| Area | Kinds |
| --- | --- |
| Indexability | `unreadable` (error, non-200, redirect), `robots-blocked`, `noindex` (meta and X-Robots-Tag), `canonical-other`, `canonical-missing` |
| Script-built pages | `script-built`: under 50 visible words and either 20+ hidden words or an app root with scripts. Points at the hidden crawl copy (SmartCrew's `div.crawl-boot`). |
| Title | `title-missing`, `title-long` (>60), `title-short` (<25), `title-duplicate`, `title-search` (target search missing) |
| Meta description | `meta-missing`, `meta-long` (>160), `meta-short` (<70), `meta-duplicate` |
| Headings | `h1-missing`, `h1-many`, `heading-skip`, `h1-search` |
| Content | `thin` (<300 words), `thin-vs-top` (under 65% of the top 3 median), `no-answer` (no H1 to H3 names the search) |
| Links | `few-links` (under 3 internal links outside nav, header, footer), `broken-link` (crawl status 4xx or error), `link-generic` ("click here", "read more") |
| Images | `img-alt` (no alt attribute), `img-huge` (no width and height, and 300 KB+ from the crawl or 1600px+ from srcset or file name) |
| Structured data | `schema-missing`, `schema-invalid` (with a repaired copy when a trailing comma was the only problem), `schema-gap` (FAQPage, Product, Organization and others that 2 of the top 3 use) |
| Social | `og-missing`, `twitter-missing` |
| Mobile | `viewport-missing` (missing or fixed width) |
| URL | `url-hygiene` (capitals, underscores, spaces, query strings, length, depth) |
| Rankings per search | `not-ranking`, `no-page`, `behind` (with the specific gaps: words, tables, FAQ, schema, H1 and title words) |

Suggestions never invent facts. They reuse the title, H1, the first real paragraph, question headings and
the paragraph under each (for FAQPage JSON-LD), image file names, the target page's H1 (for generic link text),
the brand from `og:site_name` or the common title suffix, and the tracked search, cased the way the page writes
it (HCSS, QuickBooks). When nothing honest can be built, the issue has no suggestion.

The target search for a page comes from the report rankings: the search it ranks for, or the one the report
picked it as the best page for.

## Phase B (done)

1. Rebased onto P0. `audit_rules.py` became `audit.py` and keeps P0's contract: `AuditContext(project, pages,
   rankings, docs, database, meta)` with SitePage `pages`, `readable` and `docs` for plugins, plus `parsed`,
   `ok_docs` and `doc(url)` (PageDocs) for the element checks; `CHECKS` and `@check`; plugin issues validated as
   before and given ids from kind, page, search and selector; every id made unique.
2. `audit()` reads the stored raw HTML itself from the `database` build_report already passes, so `report.py` is
   unchanged. Top 10 pages are compared on their stored facts (words, tables, FAQ) and a regex read of their
   JSON-LD, without a second parse. Built-in results are cached by a digest of every input, so a repeat report
   load skips the audit. SitePages passed without HTML (old callers) still get the page-level checks.
3. `js/screens/issues.js`: filters for priority, page and search, group by page or by search, and a sheet with
   the element, the comment, the fix, the suggestion (copy), details, the pages a template issue is on, the top
   results, and Fix in editor (`?page=&q=&focus=`).
4. `js/plugins/annotations.js` and `css/plugins/annotations.css`: numbered pins in the preview (`data-rs`), the
   page's own elements marked only with `data-rs-annotations`, a Comments panel (order 15) with Apply suggestion
   through `ctx.replaceText` and `ctx.edit`, Copy for suggestions the preview can't hold (JSON-LD scripts), and
   the `focus` param selected once on `editor-ready`.
5. Checked in Chromium against a local server with SmartCrew read live and `forward_serp.csv` imported.

## Risks

- lxml and the browser can disagree on broken markup. Selectors avoid script and table-body steps, and the
  Phase B Playwright check runs every selector against the browser DOM. A selector that matches nothing is
  listed in the panel without a marker, never pinned on the wrong element.
- Image byte sizes are only known if the crawl records them; otherwise only width hints count.
- `schema-gap` and `thin-vs-top` need the top pages' HTML, which only exists after a pull with search data.
