# Bay Area Spaces

A nightly job that collects events from Bay Area makerspaces and publishes a merged
`.ics` calendar and RSS feed.

Most Bay Area makerspaces publish events somewhere — but "somewhere" means a Luma page,
a WordPress plugin, an embedded Google Calendar, a Bookwhen account, a MediaWiki page, or
nothing at all. This project reads all of them, normalizes the result, and emits one
calendar you can subscribe to.

**Status: design complete, no code yet.** This repo currently holds the project brief, the
source registry, and reference data. See [Getting started](#getting-started) for what to
build first.

## Contents

| File | What it is |
|---|---|
| [`maker-calendar-handoff.md`](maker-calendar-handoff.md) | The full project brief: data sources, web-access strategy, pipeline design, scheduling, ethics, phasing. Read this first. |
| [`sources.yaml`](sources.yaml) | Machine-readable source registry. Several `url` fields are still `TODO` and must be filled in by hand. |
| `references/feeds.json` | An earlier, partly-verified list of concrete feed URLs — useful for filling in `sources.yaml` TODOs. |
| `references/categories.json` | Seed taxonomy (Electronics, Fabrication…) with per-interest keywords, for event categorization. |

## How it works

Deterministic Python does fetching, parsing, date math, dedupe, and emission. A local model
(LM Studio, OpenAI-compatible at `http://localhost:1234/v1`) is called for exactly two jobs:
extracting events from freeform HTML that has no stable selectors, and assigning categories
plus a one-line summary. Everything else is code — an agent re-deciding how to parse a
calendar every night makes "the feed changed" indistinguishable from "the model had a bad night."

```
sources.yaml ─▶ fetch ─▶ adapters ─▶ normalize ─▶ dedupe ─▶ enrich ─▶ emit ─▶ out/
                 │         ics                                (LLM)     ics
             conditional   gcal                                         rss
             GET, cache,   tribe_rest                                   json
             rate limit    jsonld
                           llm_html
```

Planned layout:

```
sources.yaml
pipeline/
  fetch.py            # conditional GET, cache, rate limit, robots
  browse.py           # shells out to the browser CLI, for JS-rendered pages
  adapters/           # ics, gcal, tribe_rest, jsonld, llm_html
  normalize.py  dedupe.py  enrich.py
  emit_ics.py   emit_rss.py  verify.py
raw/YYYY-MM-DD/       # every raw payload, verbatim, kept 30 days
db/events.sqlite
out/                  # bayarea-makerspaces.ics, feed.xml, events.json, health.json
```

Keeping `raw/` is what lets you diff yesterday against today and know in ten seconds
whether the source changed or your code did.

### Source tiers

| Tier | Meaning | Approach |
|---|---|---|
| **A** | Real ICS, RSS, or JSON feed | Parse directly. No LLM, no browser. |
| **B** | Structured data in static HTML (JSON-LD, WP REST) | Deterministic parse. No browser. |
| **C** | JS-rendered or freeform prose | Needs a browser and/or LLM extraction. |

Currently registered spaces: Ace Makerspace, Hacker Dojo, Sudo Room, Noisebridge,
Sequoia Fabrica, Frontier Makerspace, Humanmade, Maker Nexus. Unverified leads across SF,
the East Bay, the Peninsula/South Bay, and the North Bay are listed at the bottom of
`sources.yaml`.

**Meetup is deliberately excluded.** The open REST API is retired, what remains is
OAuth-gated GraphQL requiring a paid Pro subscription, public pages are client-rendered,
and scraping violates their ToS. For most of these spaces it duplicates their own calendar
anyway. Note that `references/feeds.json` predates this decision and still lists Meetup RSS
URLs; do not carry those into `sources.yaml`.

### Rules that matter

- **UID stability makes or breaks this.** If UIDs churn, every subscriber sees every event
  as new every night. Reuse the source UID namespaced as `{space_id}:{source_uid}`, else
  `sha1(space_id + start_utc + normalize(title))[:16]`. Never include a scrape timestamp,
  page position, or any LLM output in a UID.
- **Never let a naive datetime past `normalize.py`.** Parse to aware immediately, store UTC,
  carry the original tz string.
- **The model returns source-verbatim date strings**, never computed ISO timestamps. Let
  `dateutil` plus the known source timezone do the math. A small model will confidently turn
  "Wednesday at 7pm" into a timestamp that is off by seven hours and never flag it.
- **A failed source carries forward its previous events.** A transient 503 must never
  silently delete a space from the calendar.
- **Health gates run before `out/` is overwritten**: a source dropping to zero, or a global
  count drop over 40% night-over-night, blocks publication and alerts instead.

## Getting started

Phase 0 is manual and cannot be skipped: open each page by hand and fill in the `TODO`
URLs in `sources.yaml`. A guessed feed URL fails silently as an empty calendar. Start from
`references/feeds.json`, which already has several of them.

Phase 1 is six spaces with real ICS feeds — Ace, Hacker Dojo, Sudo Room, Sequoia Fabrica,
Noisebridge (Luma), Frontier (Luma) — using `httpx` and `icalendar`, with no LLM, no
browser, no dedupe, and no database. That is most of the actual event volume in the Bay
Area maker scene, and it is one evening of work. Ship it before building anything else.

Later phases add SQLite and health gates (2), the Tribe REST and JSON-LD adapters (3), a
browser CLI and LLM extraction for the messy tail (4), launchd scheduling and publishing
(5), and emailing the spaces that could not be parsed to ask for a feed (6). Full table in
[Part 6 of the handoff](maker-calendar-handoff.md#part-6-phasing).

### Running it, eventually

Scheduled with **launchd**, not cron — `~/Library/LaunchAgents/co.sstools.maker-calendar.plist`,
`StartCalendarInterval` at 03:15, with the machine kept awake via
`sudo pmset repeat wakeorpoweron MTWRFSU 03:10:00`. If LM Studio's server is not answering
on port 1234, the run skips the LLM stages and emits Tier A/B only rather than failing.

Output is committed to a public repo and served over GitHub Pages, which sends `.ics` with
a usable content type (`raw.githubusercontent.com` does not — do not hand out raw URLs).
Publish `webcal://` links alongside `https://`.

## Being a good citizen

These are volunteer nonprofits on shared hosting. The pipeline:

- indexes public events only, nothing behind a member login
- links back to the source page on every event — the point is to drive people to the spaces
- publishes a ~300-character summary plus a link, not the full description
- respects `robots.txt` and waits 2 seconds between requests to the same host
- sends a User-Agent with a real contact address, so someone can email instead of block
- ships an "about" page listing every source, the refresh cadence, and a one-email opt-out

## License

MIT. See [LICENSE](LICENSE).
