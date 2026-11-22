# Fixtures for tests/test_forecast_trainingset.py

Tiny files in the real layouts of each source. All domains and values are SYNTHETIC EDITS made for the tests.

- `nrd_day1.txt`, `nrd_day2.txt`: two versions of cenk/nrd `nrd-last-10-days.txt` (header lines, then one domain per line, sorted).
- `rdap_found.json`: a Verisign RDAP domain answer, trimmed. It keeps a registrar entity with a vCard so the test can prove contacts are never stored.
- `doh_ok.json`, `doh_nxdomain.json`: Google DNS-over-HTTPS answers.
- Common Crawl CDX lines (`surt timestamp {json}`), cluster.idx lines and WARC records are built by the test in those layouts, so the offsets match.
- `home_*.html`: home pages put inside WARC records by the test.
- `dfs_keywords_US_en.json`: three records in the layout of DataForSEO's `google/keywords/US_en.json.gz` sample.
