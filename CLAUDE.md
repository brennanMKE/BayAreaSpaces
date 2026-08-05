# CLAUDE.md

Guidance for Claude Code and OpenCode working in this repository.

Claude Code is the tool being used for planning and building right now. `AGENTS.md` for
OpenCode comes later, and when it does it should be thin and point back at this file rather
than restating the invariants in two places that will drift.

## What this repo is

The **data collection half** of a Bay Area makerspace event aggregator. A nightly job on a
local Mac mini reads a registry of event sources, fetches and parses them, normalizes and
dedupes the result, and publishes a clean event dataset.

The full design brief is [`maker-calendar-handoff.md`](maker-calendar-handoff.md). Read it
before making architectural decisions — this file records what has changed since it was
written, not what it already says.

**Status: design complete, no pipeline code exists yet.** The repo currently holds the
brief, the source registry, per-space research notes, and reference data.

## What this repo is NOT

**The website is a separate project.** It consumes the data produced here and serves the
public calendar and RSS feed. Do not build web UI, hosting, or subscriber-facing endpoints
in this repo. If a task seems to need them, it belongs in the website project.

This split supersedes Part 4 of the handoff, which proposed committing `out/` to GitHub
Pages. That is no longer the publishing path.

## Architecture

```
   ┌─ this repo ─────────────────────────────────┐   ┌─ separate project ──┐
   │                                             │   │                     │
   │  sources.yaml                               │   │  website            │
   │      ↓                                      │   │   - calendar (.ics) │
   │  fetch → adapters → normalize → dedupe      │   │   - RSS feed        │
   │      ↓                    ↓                 │   │                     │
   │  raw/            SQLite (local working)     │   └─────────────────────┘
   │                           ↓                 │             ↑
   │                        publish ─────────────┼─────────────┘
   └─────────────────────────────────────────────┘   Postgres on AWS EC2
```

**Runtime:** Mac mini (M4 Pro, 64 GB), scheduled with launchd. LM Studio serves local
models over its OpenAI-compatible API at `http://localhost:1234/v1`.

**Local store — SQLite.** The working store for the nightly run: `ETag`/`Last-Modified`
per source for conditional GET, event history for carry-forward on source failure, dedupe
state, run health. Use SQLite freely for anything that is per-run bookkeeping or a cache.
It is the fast, zero-dependency default and it stays on the mini.

**Published store — Postgres on AWS EC2 (Linux).** The already-running database that the
website reads. The nightly job pushes the finished, validated event set there. Treat it as
the handoff boundary between the two projects: this repo writes, the website reads.

Open questions to settle before writing the publish step — ask rather than assume:
- Connection method from the mini (direct, SSH tunnel, VPN, or an API in front of it)
- Whether the website owns the Postgres schema or this repo does
- Upsert semantics: is the published table replaced per run, or merged on `uid`?

Nothing should push to Postgres until these are answered. Until then, emit to `out/` and
treat that as the interface.

## Language: Python

**The pipeline is Python.** Decided 2026-08-05, in preference to Go.

The deciding factor is calendar parsing, and the source survey made it concrete rather than
theoretical. Real feeds in this registry contain unbounded RRULEs, recurrences pre-expanded
to 2058, mixed `DTSTART` forms within a single feed (bare-UTC `Z` alongside `TZID=`),
`VALUE=DATE` all-day events, and Luma's multi-day-as-all-day behavior. `icalendar` plus
`recurring-ical-events` absorbs that; the Go equivalents are thin enough that RRULE expansion
would become our own code, and that is a bad place to own bugs.

The rest of the stack follows: `httpx` (HTTP/2, conditional GET), `extruct` for JSON-LD edge
cases (`@graph`, arrays and `ItemList` wrappers all appear in the wild here), `rapidfuzz` for
dedupe, `dateutil` for date math, `feedgen` for RSS, and **Pydantic for validating local-model
output** — the rule "never emit unvalidated model output" is one decorator in Python and
hand-rolled plumbing in Go.

Go's advantages do not apply to this workload. The run is dominated by deliberate waiting
(2 s per host, 10 s for Ace), not compute, so goroutines buy nothing; and a single static
binary solves a deployment problem we do not have on one Mac mini. Use `uv` for dependencies.

The one genuine point in Go's favor, recorded honestly: `time.Time` always carries a location,
which would structurally enforce the no-naive-datetime invariant that Python leaves to
discipline. Not worth the rest — enforce it with an assertion in `normalize.py` instead.

## The nightly run

**One deterministic Python process iterates `sources.yaml` and collects everything.** No agent
in the loop, no per-space context to manage, because a `for` loop over sources costs nothing.

```
launchd 03:15  →  python -m pipeline run
                    ├─ for each space, for each enabled source:
                    │     fetch (conditional GET, rate limit, robots) → raw/
                    │     adapter → normalize → filter
                    ├─ dedupe across all spaces
                    ├─ enrich  ← the only stage that calls a model
                    ├─ health gates
                    └─ publish (out/, later Postgres) + health.json
```

Sequencing spaces one at a time is fine and is the default — it falls naturally out of the
rate limiting, and the whole run is minutes on this hardware.

**Structured data is the norm, not the exception.** Of 21 verified sources, 20 are
deterministically parseable: ICS, Google Calendar ICS, WP REST, JSON, JSON-LD, `__NEXT_DATA__`,
RSS and one server-rendered HTML table. Exactly one registered source needs a model
(`llm_html`, the Noisebridge wiki wikitext) — and the plan for that one is to extract it
*once*, convert the standing schedule to hand-maintained RRULEs, and stop crawling it. Treat
every new `llm_html` entry as a small failure to find the real feed, and look harder first.

## Where the model is used

The local model (LM Studio, OpenAI-compatible at `http://localhost:1234/v1`) is called
directly from Python with a strict JSON schema, for **two jobs only**:

1. **Unstructured HTML → JSON extraction**, where no stable selectors exist
2. **Categorization and summarization** — tags plus a one-line RSS summary

Use a small instruct model (3B–8B); escalate to a larger one only on quarantined pages.

If LM Studio is not answering, **skip the model stages and publish Tier A/B only** rather than
failing the run. Structured data must never be held hostage to the model being up.

## OpenCode's role: repair, not collection

**OpenCode is not in the nightly path.** It is for building the pipeline and repairing it when
a source drifts — work where a human is present.

This is where per-space context isolation genuinely matters, and where it belongs. When
`health.json` shows a source at zero for three consecutive nights, run OpenCode against *that
one space*: the raw diff, one adapter file in scope, one space of context.

```bash
opencode run --model lmstudio/<model> \
  "Source ace-makerspace returned 0 events for 3 nights. Diff raw/2026-08-01/… against
   raw/2026-08-05/…, diagnose what changed, and propose a patch to
   pipeline/adapters/tribe_rest.py. Do not modify files outside pipeline/adapters/."
```

Run it in a git worktree and review the diff. A bad adapter patch quietly loses a space and
you will not notice for a week.

If OpenCode is ever given a larger role, the constraint is: **it may propose, but Python
validates and publishes.** An agent must never write to `out/` or Postgres directly, and the
health gates stay in deterministic code. The documented failure mode for local models in
agent loops — emitting `Write` when the tool is `write`, or replying with prose instead of a
tool call — looks exactly like a source going down, and must not be able to reach subscribers.

## Expect this to be refined

The extraction and sorting process is expected to change as sources drift and as the local
models improve. Design for that: adapters are small and swappable, `sources.yaml` is the
only place that names them, and `raw/` is kept so yesterday can be diffed against today.
When something breaks, the first question is always "did the source change or did we?" —
and `raw/` is what answers it in ten seconds.

## Repo map

| Path | What it is |
|---|---|
| `maker-calendar-handoff.md` | The design brief. Authoritative except where this file overrides it. |
| `sources.yaml` | The source registry. The only place adapters are named. |
| `spaces/*.md` | Per-space research notes: verified feeds, leads, dead ends, social, contacts. |
| `spaces/README.md` | Coverage table and the outreach index (who to email, and the ask). |
| `references/feeds.json` | Earlier partly-verified feed URLs, now merged into `sources.yaml`. |
| `references/categories.json` | Seed taxonomy with per-interest keywords, for categorization. |
| `README.md` | Public-facing project overview. |

Planned, not yet created: `pipeline/` (with `adapters/`), `raw/`, `db/`, `out/`, `logs/`,
`pyproject.toml`. Module layout is in Part 3 of the handoff. `AGENTS.md` comes later.

## Invariants

These are the rules that break the project quietly if violated. Do not relax them without
being asked to.

- **UID stability.** If UIDs churn, every subscriber sees every event as new every night.
  Reuse the source UID namespaced as `{space_id}:{source_uid}`; otherwise
  `sha1(space_id + start_utc + normalize(title))[:16]`. Never include a scrape timestamp,
  page position, or any LLM output in a UID.
- **Never let a naive datetime past `normalize.py`.** Parse to aware immediately, store UTC,
  carry the original tz string. Assert and fail the run.
- **The model returns source-verbatim date strings**, never computed ISO timestamps.
  `dateutil` plus the known source timezone does the math.
- **A failed source carries forward its previous events.** A transient 503 must never
  silently delete a space from the calendar.
- **Never mutate `raw/`.** It is the evidence trail. Write once, keep 30 days.
- **Health gates run before anything is published.** A source dropping to zero, or a global
  count drop over 40% night-over-night, blocks publication and alerts instead. See
  *Health gates* below — count-based gates alone are not sufficient.
- **Never guess a feed URL.** A wrong one fails silently as an empty calendar, which is the
  worst outcome this project has. Unverified sources are marked `verified: false` and are
  not trusted until someone fetches them.
- **HTTP 200 is not success.** Assert on content type *and* parse result, and treat
  disagreement as a failure rather than picking one. Verified live examples:
  `?format=ical` returning 200 with `text/html`; `?ical=1` returning 200 with a
  byte-identical copy of the homepage; a per-category feed returning 200 with the
  unfiltered full list; and one endpoint returning **404 with a populated, valid RSS body**.
- **A wrong adapter is as silent as a wrong URL.** Humanmade's Eventbrite page was
  registered as `jsonld`; Eventbrite has since moved to `__NEXT_DATA__`, so that source
  would have returned empty forever without ever erroring.
- **Filters drop events silently.** Any `location_contains` filter must decide what happens
  to events with an empty or non-address `LOCATION` — 15% of Frontier Tower's events set it
  to a bare Luma URL. Default to keeping them (`location_allow_when_missing`), not dropping.
- **Public events only**, link back to the source on every event, publish a ~300-character
  summary rather than the full description, respect `robots.txt`, and rate limit to 2
  seconds per host. These are volunteer nonprofits on shared hosting.

## Health gates

The failure that bites is not a crash — it is a source quietly going wrong while the run
succeeds. Counting events is necessary but not sufficient, because the 2026-08-05 source
survey turned up four distinct cases a count-based gate gets wrong:

| Case | Example | Gate |
|---|---|---|
| Abandoned feed that keeps generating | Noisebridge's old gCal: 28 VEVENTs, dead since Jan 2024, five RRULEs with **no `UNTIL`** — invents ~5 events a week forever at a constant rate | `max_stale_days` on `LAST-MODIFIED` |
| Legitimately empty | The Box Shop, ~8–12 events/year, routinely zero upcoming | `allow_zero` |
| Capped or truncated feed | Hacker Dojo's Meetup iCal: hard 10-event cap, no pagination — a ~1-week horizon, so nightly deltas are noise | `ignore_count_drop` |
| Never yet non-zero | Humanmade's Eventbrite organizer: 0 upcoming, so "went to zero" can never fire but a naive alert fires nightly | `require_nonzero_once` |

Two further rules from the same survey:

- **Count post-expansion events inside the horizon, not VEVENTs.** Sequoia Fabrica's
  calendar has 89 VEVENTs and ~7 live ones; Maker Nexus has 3645 and 171. Raw counts say
  almost nothing about whether a feed is healthy.
- **A short publishing horizon is not a decline.** Maker Nexus posts 4–8 weeks ahead, so the
  far end of a 120-day window is legitimately empty every night.

## Conventions

- `sources.yaml` entries carry `adapter`, `url` (or `calendar_id` for `gcal_ics`), optional
  `label`, `trust`, and `verified: true|false`. Higher `trust` wins in dedupe; a space's own
  site always outranks an aggregator.
- Research findings go in `spaces/<space-id>.md`, one file per space, on the shared
  template. Record dead ends — they stop the next person repeating the search.
- Adapters in use: `ics`, `gcal_ics`, `tribe_rest`, `jsonld`, `nextdata`, `embedded_json`,
  `json`, `rss`, `bookwhen_html`, `llm_html`. Only `llm_html` involves a model; everything
  else is deterministic. Add new adapter names to the header comment in `sources.yaml`.
- **`robots.txt` disallow means we do not fetch it** — not that we find an equivalent route.
  The Box Shop disallows `?format=json` and `?format=ical` while allowing `?format=rss`, and
  the RSS route happens to be sufficient. If a permitted route were *not* sufficient, the
  answer is to ask the space, not to work around the file.
- **Read each host's `robots.txt` on its own terms.** They are not interchangeable: The Box
  Shop's AI-agent group collapses into a no-op, while `lower48.org` gives ClaudeBot,
  anthropic-ai, GPTBot, CCBot and 21 others their own `Disallow: /`.

### AI-agent disallows: settled 2026-08-05

Some hosts (`lower48.org`) disallow ClaudeBot, anthropic-ai, GPTBot, CCBot and similar by
name while permitting `*`. **The pipeline follows the `*` group and may fetch.** Under
RFC 9309 a crawler matches exactly one group — the one naming its product token, or `*` if
none matches; groups do not merge. `bayarea-maker-calendar` matches no named group. Sites
enumerate specific bots precisely because they want different rules for different agents,
and those rules target training-corpus ingestion, not an aggregator that reads a calendar
and links back.

**But a Claude/Codex research agent browsing these sites is a different actor and IS covered
by those disallows.** Respect them when researching. The nightly pipeline and an AI agent
doing discovery are not interchangeable, and the permission that applies to one does not
transfer to the other.

This holds only while all of the following are true. They are the argument, not decoration:

- **Honest User-Agent with a working contact address.** Never impersonate a browser or
  another crawler to obtain a more permissive group. That would be evasion and would void
  everything above.
- **Fetch only the URLs in `sources.yaml`.** No crawling, no link-following, no sitemap
  walking. This is what makes "we only want the events" a description of behavior rather
  than of intent.
- **Path-level disallows bind us regardless of group.** `lower48.org` disallows
  `?format=ical` and `?format=json` for `*`, so those stay off-limits even though we are
  permitted generally.
- **Honor `Crawl-delay`** and use conditional GET, so steady state is mostly 304s.
- **A one-email opt-out, honored immediately and without argument.** `robots.txt` is a blunt
  instrument written for crawlers; a human asking us to stop outranks it. Being technically
  in the right is not the goal.

When emailing a space anyway, just ask. An explicit yes retires the question for that host.
- Meetup is excluded by default: the open API is retired, what remains is OAuth-gated
  GraphQL behind a paid Pro subscription, and scraping the client-rendered pages violates
  their ToS. Their *published* feed endpoints are a separate question and were tested per
  space on 2026-08-05: the RSS feeds carry **no event start date at all** (only `pubDate`),
  so they are useless as event sources; the iCal feed does carry real dates but caps at 10
  events. Hacker Dojo is the one documented exception, because Meetup is its only surviving
  source — `events.hackerdojo.com` has returned Cloudflare 525 for over a year.
