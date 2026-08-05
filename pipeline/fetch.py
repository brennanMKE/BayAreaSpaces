"""HTTP layer: rate limiting, robots.txt, conditional GET, ``raw/`` archiving.

The only module that talks to the network. One ``httpx`` client with HTTP/2 and
redirects followed, shared for the whole run.

Responsibilities:

- Honor ``robots.txt`` per host (``urllib.robotparser``), cached per run. A
  disallow means we do not fetch it — not that we find an equivalent route.
  Path-level disallows bind us regardless of which group matched.
- Rate limit to 2 seconds per host (10 s for Ace) and honor ``Crawl-delay``.
  These are volunteer nonprofits on shared hosting.
- Conditional GET using the ``ETag`` / ``Last-Modified`` stored per source in
  SQLite; a 304 skips parsing entirely and just bumps ``last_seen``.
- 30 s timeout, 2 retries with backoff, then mark the source ``failed`` so
  ``normalize``/``dedupe`` carry forward the previous run's events.
- Archive every payload verbatim under ``raw/YYYY-MM-DD/``, write-once, kept 30
  days. Never mutate ``raw/`` — it is the evidence trail that answers "did the
  source change or did we?" in ten seconds.
- **HTTP 200 is not success.** Assert on content type *and* parse result, and
  treat disagreement as a failure. Observed live in this registry: ``?format=ical``
  returning 200 with ``text/html``; ``?ical=1`` returning a byte-identical copy
  of the homepage; and one endpoint returning 404 with a valid RSS body.
- Fetch only URLs named in ``sources.yaml``. No crawling, no link-following, no
  sitemap walking.

Implemented by issue 0006 (fetch layer). Stub only — scaffolded by issue 0004.
"""
