# US search volume for SmartCrew-related keywords

Pulled Friday, November 13, 2026 (America/New_York). This is what people type into Google, not where SmartCrew ranks.

## Sources

These worked:

- seodata.dev public keyword pages and API. The company says the numbers come from the Google Ads API (the same source as Keyword Planner). Country was set to US. Page and API dates range from May 8, 2026 to November 13, 2026 depending on the keyword. Most category terms were last updated in May to October 2026. A few brand lookups (fleetio, jobnimbus, plangrid, tsheets, tenna, workyard, and others) were fetched live today.
- Semrush public US organic keyword tables on competitor traffic pages, last updated November 11 or 12, 2026. Used only when seodata.dev did not return a Google Ads number, or noted as a second figure when both exist.

These did not work, or only worked in a limited way:

- No DataForSEO, Semrush, or Ahrefs API keys were in the environment.
- Ahrefs free Keyword Generator accepted US/Google requests at `https://ahrefs.com/v4/stGetFreeKeywordIdeas` but returned InvalidCaptcha without a captcha token. I did not bypass that.
- Semrush's free Keyword Search Volume Checker also requires reCAPTCHA v3, so it could not be used from curl.
- SE Ranking's free checker only opens a logged-in research app and did not return volumes here.
- Google Trends only gives relative interest, not monthly counts, so it was not used as volume.
- Search Console impressions were used only as seed ideas. They are not search volume, because they only count times SmartCrew appeared.

## Coverage

The research list is 200 keywords, saved in `keywords.csv` and `keywords_plain.txt` (one phrase per line, ready to paste into Google Keyword Planner).

- 125 of 200 have a US monthly volume number.
- 119 of those come from seodata.dev / Google Ads data.
- 6 come only from Semrush public tables (all of them are login-style queries such as "procore login").
- 75 have a blank volume. I did not guess. Many of those are long-tail dirt work, excavation, and comparison phrases. For several, including "dirt work software", "best dirt work software", "dirtwork software", "hcss alternative", "hcss alternatives", "best excavation software", and "smartcrew", seodata.dev returned HTTP 404 and no usable Google Ads number.

Google Ads volumes arrive in standard Keyword Planner buckets (for example 8,100, 9,900, 12,100, 22,200, 74,000). When two different brands show the same number, that is the bucket, not a copy-paste error.

Where Semrush and seodata.dev both have a number, they sometimes disagree. Examples: servicetitan is 74,000 in seodata.dev and 33,100 in Semrush; housecall pro is 60,500 vs 49,500; construction management software is 9,900 vs 6,600. The table below uses seodata.dev when it has a Google Ads figure, and names the Semrush figure in the notes.

## Top 25 on the 200-keyword list

Ranked by the US monthly volume we actually recorded. Brand and login searches are included because they are real searches. Ambiguous terms are flagged.

| Rank | Keyword | US monthly volume | Source | Data date | Notes |
| ---: | --- | ---: | --- | --- | --- |
| 1 | procore | 301,000 | seodata.dev (Google Ads, US) | 2026-07-23 | Semrush public table agrees at 301,000 (Nov 11, 2026). |
| 2 | procore login | 110,000 | Semrush public US table | Nov 11, 2026 | seodata.dev did not return a Google Ads number. Navigational login search. |
| 3 | jobber | 110,000 | seodata.dev (Google Ads, US) | 2026-09-09 | Ambiguous. Jobber is also a common word for a wholesaler, so much of this volume may not be the software. |
| 4 | servicetitan | 74,000 | seodata.dev (Google Ads, US) | 2026-09-09 | Semrush lists 33,100 (Nov 11, 2026). |
| 5 | companycam | 74,000 | seodata.dev (Google Ads, US) | 2026-11-02 | |
| 6 | bluebeam | 74,000 | seodata.dev (Google Ads, US) | 2026-11-09 | |
| 7 | service titan | 60,500 | seodata.dev (Google Ads, US) | 2026-09-10 | Spaced spelling of ServiceTitan. |
| 8 | housecall pro | 60,500 | seodata.dev (Google Ads, US) | 2026-05-15 | Semrush lists 49,500 (Nov 11, 2026). |
| 9 | buildertrend | 49,500 | seodata.dev (Google Ads, US) | 2026-06-25 | Semrush lists 40,500 (Nov 11, 2026). |
| 10 | tenna | 40,500 | seodata.dev (Google Ads, US) | 2026-11-13 | Ambiguous. Related terms include Deltarune Tenna, a video game character, so much of this volume is likely not the equipment platform. |
| 11 | plangrid | 33,100 | seodata.dev (Google Ads, US) | 2026-11-13 | |
| 12 | buildertrend login | 27,100 | Semrush public US table | Nov 11, 2026 | seodata.dev did not return a Google Ads number. Navigational login search. |
| 13 | quickbooks time | 27,100 | seodata.dev (Google Ads, US) | 2026-11-10 | |
| 14 | autodesk construction cloud | 27,100 | seodata.dev (Google Ads, US) | 2026-06-05 | |
| 15 | procore log in | 22,200 | Semrush public US table | Nov 11, 2026 | seodata.dev did not return a Google Ads number. Navigational login search. |
| 16 | fleetio | 22,200 | seodata.dev (Google Ads, US) | 2026-11-13 | Semrush agrees at 22,200 (Nov 11, 2026). |
| 17 | housecall pro login | 22,200 | Semrush public US table | Nov 11, 2026 | seodata.dev did not return a Google Ads number. Navigational login search. |
| 18 | jobnimbus | 22,200 | seodata.dev (Google Ads, US) | 2026-11-13 | |
| 19 | tsheets | 18,100 | seodata.dev (Google Ads, US) | 2026-11-13 | Legacy name for QuickBooks Time. |
| 20 | clockshark | 12,100 | seodata.dev (Google Ads, US) | 2026-05-18 | |
| 21 | fieldwire | 12,100 | seodata.dev (Google Ads, US) | 2026-10-04 | |
| 22 | construction management software | 9,900 | seodata.dev (Google Ads, US) | 2026-05-21 | Highest-volume non-brand category term on the list. Semrush lists 6,600 (Nov 11, 2026). |
| 23 | project management software for construction | 9,900 | seodata.dev (Google Ads, US) | 2026-10-13 | |
| 24 | fleet management software | 9,900 | seodata.dev (Google Ads, US) | 2026-06-17 | Broad fleet market, not only construction. Semrush lists 6,600 (Nov 11, 2026). |
| 25 | fleet manager software | 9,900 | seodata.dev (Google Ads, US) | 2026-05-09 | Broad fleet market. |

Just below the cut, still on the 200-keyword list: busybusy 9,900 and jobtread 9,900 (both seodata.dev). Then construction software 8,100, software for construction management 8,100, hcss 8,100, and raken 8,100.

## What this means for the dirt work and heavy civil list

The buyer phrases SmartCrew has been targeting are real, but their Keyword Planner volumes are small or missing.

- excavation software: 70
- earthwork software: 480 (the strongest nearby category term we found)
- earthworks takeoff software: 210
- excavation estimating software: 140
- dump truck software: 110
- hauling software: 170
- heavy civil construction software: 30
- heavy civil software: 30
- heavy construction software: 30
- dirt work software, dirtwork software, best dirt work software, best excavation software, hcss alternative: no Google Ads number returned

Brand searches are where the volume is. Among closer competitors: hcss is 8,100, heavy job is 1,000, hcss heavyjob is 390, heavybid is 590, hcss heavybid is 260, b2w is 1,900, b2w software is 480, trimble viewpoint and viewpoint software are 880 each, agtek is 1,600, quoteiq is 1,600, workyard is 2,400, assignar is 720, contractor foreman is 4,400.

The biggest non-brand software categories on the list, after construction management, are estimating (construction estimating software and estimating software both 6,600), field service software (6,600, but that market is much broader than dirt work), and construction time tracking (3,600).

## Files

- `keywords.csv` and `keywords_plain.txt`: the 200-keyword list.
- `volumes.csv`: every keyword, with volume, source, date, CPC when available, and notes. Blanks mean no source returned a number.
- `related-outside-list.csv`: other phrases seodata.dev returned in "similar keyword" tables that were not on the 200 list. Useful context, not used in the top 25.

`keywords_plain.txt` can be pasted into Google Keyword Planner for a second pass, especially on the 75 blanks and on the login queries where Semrush and Google Ads data do not agree.
