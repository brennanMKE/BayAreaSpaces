# Bay Area Maker Event Aggregator: Handoff

**Owner:** Brennan · **Target machine:** M4 Pro Mac mini (64 GB), LM Studio + OpenCode
**Goal:** nightly job that collects events from Bay Area makerspaces and publishes a merged `.ics` and RSS feed.
**Companion file:** `sources.yaml` (machine-readable registry seed)

This document is self-contained. Hand it to Claude Code or OpenCode as the project brief.

---

# Part 1: Data sources

Feed URLs marked **verified** were confirmed by fetching the page on 2026-08-05.
Everything else is a lead requiring manual confirmation before wiring up.

## Difficulty tiers

| Tier | Meaning | Approach |
|------|---------|----------|
| **A** | Real ICS, RSS, or JSON feed | Parse directly. No LLM, no browser. |
| **B** | Structured data embedded in static HTML (JSON-LD, WP REST) | Deterministic parse. No browser. |
| **C** | JS-rendered or freeform prose | Needs a browser and/or LLM extraction. |

## San Francisco

### Noisebridge (Tier A + C)
272 Capp St, Mission. Four separate event surfaces, which is this project in miniature.

| Source | URL | Tier | Notes |
|---|---|---|---|
| Luma | `luma.com/noisebridge` | A | Described as the main public calendar. Sidebar has **Add iCal Subscription**; copy that link. *(verified: Luma documents per-calendar iCal feeds)* |
| Meetup | `meetup.com/noisebridge` | C | 14,403 members. Their own wiki calls it the most up to date list. *(verified: page is client-rendered; plain GET returns "Events 0")* |
| Wiki | `noisebridge.net/wiki/Events` | C | Only source for the standing weekly schedule (Go night, Wood Night, Five Minutes of Fame). Extract once, convert to hand-maintained RRULEs, then stop crawling. |
| noisebridge.today | associated Google Calendar | A | Separate member-run daily calendar. *(verified: referenced from the wiki)* |

### Sequoia Fabrica (Tier A)
1736 18th St, Potrero Hill. Brennan is a founding member, so access is a conversation not an engineering problem.

- **Bookwhen**: `bookwhen.com/sequoiafabrica`. Public iCal feed at
  `webcal://feeds.bookwhen.com/ical/{account}/{token}/public.ics?historic_month_count=1`.
  Event info only, no booking details. Defaults to 3 months back, 12 months forward.
  Token lives in Bookwhen admin under "Calendar feeds." *(verified: Bookwhen docs)*
- **Mastodon**: `sfba.social/@sequoiafabrica` (append `.rss`). Change signal only, not structured.

### Humanmade (Tier B)
655 Bryant St, SoMa. Nonprofit advanced manufacturing training center.
- **Eventbrite organizer page**: `eventbrite.com/o/humanmade-57286899753` *(verified: page exists, lists upcoming workshops)*. Parse embedded JSON-LD. Eventbrite has no usable public search API since 2019.
- `humanmade.org` is program marketing, not a calendar.

### Frontier Makerspace / Frontier Tower (Tier A)
995 Market St, floor 7 is the makerspace; the whole building runs events.
- **Luma**: `luma.com/frontiertower` *(verified)*. Same iCal subscription mechanism.
- High volume: their site claims 3+ events a day building-wide. Filter by venue string
  (`Makerspace`, `Arts and Music`, `Robotics`) or the feed will swamp everything else.
- **Meetup**: `meetup.com/frontier-makerspace` (Tier C).

### Unverified SF leads
Double Union (feminist/nonbinary makerspace, confirm still operating), The Shed, MakeX,
Port Product Lab, SFPL makerspaces (The Mix at Main, Mission Bay).

## East Bay

### Ace Makerspace (Tier A, best source on the list)
6050 Lowell St #214, Oakland. Formerly Ace Monster Toys.
Runs **The Events Calendar** (Tribe) on WordPress. *(verified by direct fetch)*

- **ICS**: `https://www.acemakerspace.org/?post_type=tribe_events&ical=1&eventDisplay=list&calendarVersion=3`
- **REST** (preferred): page meta shows `tec-api-version: v1`, so try
  `https://www.acemakerspace.org/wp-json/tribe/events/v1/events?start_date=YYYY-MM-DD&per_page=50`.
  Returns clean JSON with venue, cost, categories, ticket status. Paginate on `next_rest_url`.
- Also on Luma (`luma.com/acemakers`) and Meetup, but their Meetup page says explicitly
  **do not RSVP here**, so treat Meetup as a duplicate rather than a source.

### Sudo Room (Tier A)
Omni Commons, 4799 Shattuck Ave, Oakland. Free hackerspace, weekly hardware hack night.
- `sudoroom.org/calendar/` embeds several color-coded Google Calendars including an
  "Omni Commons General Calendar." *(verified)* View source, pull calendar IDs out of the
  iframe `src`, then use `https://calendar.google.com/calendar/ical/{ID}/public/basic.ics`.
- `omnicommons.org/wiki/Calendar` is stale (indexed content from 2014). Skip.

### Unverified East Bay leads
Counter Culture Labs (also at Omni), Mothership HackerMoms (Berkeley), Circuit Launch
(Oakland hardware), The Crucible (Oakland industrial arts, large class catalog worth having),
Bay Area Maker Farm (Alameda), PEV Works.
**Liberating Ourselves Locally closed in 2022.** Do not chase it.

## Peninsula and South Bay

### Hacker Dojo (Tier A, second-best source)
855 Maude Ave, Mountain View.
- **`events.hackerdojo.com`** runs a custom events app offering **iCal, RSS, JSON and CSV**
  feeds in the footer. *(verified via search index. The site blocks automated fetchers, so
  open it in a browser and copy the four URLs by hand.)* Prefer JSON.
- **Meetup**: `meetup.com/hackerdojo`, organized by Hacker Dojo plus 34 others, so it carries
  tenant-group events the main calendar may not.

### Maker Nexus (Tier B/C)
1330 Orleans Dr, Sunnyvale. 28,000 sq ft, heavy class schedule.
- `makernexus.org/classes-week-view`, `/classes`, `/community-events`. Squarespace with an
  embedded class calendar (month view plus an Agenda tab, reads like an embedded gCal).
  Inspect the iframe. If there is a calendar ID, this drops to Tier A.
- **ActivityHero**: `activityhero.com/biz/maker-nexus` carries youth programs not on the main list.
- `makernexuswiki.com` is the best human-maintained directory of Bay Area spaces found.
  Worth mining once for source discovery.

### Unverified South Bay leads
BioCurious, School of Visual Philosophy (San Jose), Foothill College Makerspace,
Kitchentown (San Mateo), Coyote Grange.

## North Bay (all unverified, low volume, low priority)
Chimera Arts (Sebastopol), Cyber Garage (Marin), Benicia Makerspace.

## Aggregators, for discovery and cross-check only
`dothebay.com/venues/{slug}`, `sf.funcheap.com`, `missionlocal.org/venue/{slug}` (also runs
The Events Calendar, so the same `?ical=1` trick works). Never treat these as authoritative.

## Platform notes

**Luma.** Per-calendar iCal subscription is officially supported. Multi-day events arrive as
all-day events, which will look wrong merged; normalize them. The `api.lu.ma` endpoints the
web app uses are undocumented and change without notice, so prefer the ICS.

**Meetup is a wall.** The open REST API is retired. What remains is OAuth-gated GraphQL at
`api.meetup.com/gql-ext`, and meaningful access requires a paid **Meetup Pro** organizer
subscription with no guarantee of approval. Public pages are client-rendered. Scraping
violates their ToS. For Ace, Hacker Dojo and Maker Nexus, Meetup duplicates their own
calendar anyway. **Excluded from `sources.yaml`.** Revisit only for a Meetup-only space.

**The Events Calendar (WordPress/Tribe).** Whenever you see `?ical=1` subscribe links or
`tec-api-version` in page meta, you get both an ICS feed and `/wp-json/tribe/events/v1/events`.
Build the adapter once, reuse it.

**Google Calendar embeds.** Any public embedded gCal yields
`https://calendar.google.com/calendar/ical/{ID}/public/basic.ics`.

## The highest-leverage move
Six spaces have real ICS feeds. That is most of the actual event volume in the Bay Area maker
scene. Ship those first with zero scraping, then use the working calendar as the pitch when
emailing the remaining organizers to ask for a feed. That conversation will get better
coverage than any amount of browser automation.

---

# Part 2: Web access strategy

The open question: **how does the pipeline reach Tier C pages, and does OpenCode need to be
involved at all?**

## What OpenCode actually ships

Built-in tools: `bash`, `read`, `write`, `edit`, `patch`, `glob`, `grep`, `list`, `webfetch`,
`websearch`, `task`, `todowrite`/`todoread`, `lsp_*`, plus a `question` tool.

Two things worth knowing:

1. **`websearch` is not missing, it is gated.** It uses Exa AI's hosted MCP service with no API
   key, but is only available when using the OpenCode provider **or** when the
   `OPENCODE_ENABLE_EXA` environment variable is set to any truthy value. Setting that env var
   turns on web search alongside a local model. That likely resolves the concern directly.
2. **`webfetch` retrieves a specific URL.** It does not render JavaScript, so it fails on
   Meetup and any other client-rendered site for the same reason a plain `curl` does.

## The real risk is tool calling, not tool availability

The documented failure mode with open-source models in OpenCode is the models themselves:
emitting `Write` when the tool is named `write` (producing `AI_NoSuchToolError`), or replying
with prose instead of emitting a tool call at all. LM Studio does support OpenAI-style function
calling through `/v1/chat/completions` and `/v1/responses`, and MCP through the API in 0.4.0+,
so the plumbing is there. Whether a given 27B or 35B quant drives a 14-tool agent loop reliably
across a 40-minute unattended run is a separate question, and the honest answer is: not reliably
enough to be the nightly runtime.

**Conclusion: do not put OpenCode in the nightly path.** Fetching is `httpx` in a Python script.
The local model is called directly over `/v1/chat/completions` with a JSON schema, for the two
narrow jobs in Part 3. OpenCode is the tool you *build and repair* the pipeline with, where you
are present to catch a mangled tool call.

## Custom tools: the escape hatch that matters

OpenCode supports custom tools as TypeScript/JavaScript files in `.opencode/tools/` (project)
or `~/.config/opencode/tools/` (global). The filename becomes the tool name. The TS file is
only the *definition*; it can shell out to anything.

This is the integration point for whichever browser approach you pick. One small
`.opencode/tools/browse.ts` that shells out to your fetcher gives OpenCode a working
JS-rendering retrieval tool, no MCP server required. It also gives the nightly Python pipeline
the same capability through the same CLI, so you build the browser once and both consumers use it.

## Option A: safaridriver / WebDriver

**The blocker you need to know before writing any code:** Safari's WebDriver sessions run in an
**isolated profile**. Per Apple's own description of the feature, a WebDriver session gets a
separate set of windows, tabs, preferences and persistent storage; existing tabs are hidden; a
distinctively-colored automation window is shown; and any local state such as cookies is deleted
on session completion. This is deliberate, for test isolation and privacy.

That defeats the stated reason for wanting it. You would **not** get "everything I would if I
were browsing directly myself." You get a clean, logged-out browser every time.

Other friction:
- No headless mode.
- One session at a time.
- Requires a GUI login session (matters for launchd scheduling; see Part 4).
- `Allow Remote Automation` must be toggled through Safari's Develop menu. Apple removed the
  `AllowRemoteAutomation` and `IncludeDevelopMenu` defaults keys in Safari 14 with no
  replacement, so this cannot be scripted on a fresh machine.
- `safaridriver --enable` requires admin authentication once.

**Verdict:** good for a genuinely stateless "render this public page and give me the DOM."
Wrong tool if session reuse is the point.

## Option B: WebKit app driven by RemoteControl

Your `RemoteControl` prototype already proves the hard part: a long-lived, bidirectional
`NSXPCConnection` between `remotectl` and a running SwiftUI app, with a launch-agent broker
handling endpoint discovery, streaming progress events, and meaningful exit codes (2 broker
unreachable, 3 app unavailable, 4 request failed, 5 session terminated). `watch --digest`
already demonstrates exactly the shape this needs: CLI asks the app to do a long job, app
streams progress, CLI gets a result.

What you would add: a `WKWebView` and a handful of commands.

```
remotectl fetch <url> --wait-for <selector> --timeout 30 --out page.html
remotectl fetch <url> --json-ld          # extract and emit schema.org Events
remotectl login <url>                    # opens a visible window, you log in by hand
remotectl session list|clear
```

Advantages over WebDriver:
- **Durable sessions.** A persistent `WKWebsiteDataStore` keeps cookies across runs. Log in
  once by hand; the nightly job inherits it. This is the capability WebDriver explicitly denies you.
- Real streaming progress over an already-working XPC channel.
- Full control over wait conditions, resource blocking (drop images and ads, cut fetch time),
  user agent, and what gets extracted. You can run `document.querySelectorAll` via
  `evaluateJavaScript` and return structured JSON instead of megabytes of HTML.
- No admin authentication, no Develop menu toggle, no GUI-only setup step.

Costs, stated plainly:
- It is an app you now maintain. A `WKWebView` fetcher is roughly a weekend to something usable
  and a long tail after that: navigation timeouts, redirect loops, "is the page actually done
  loading," memory growth across hundreds of loads.
- Still needs a GUI session, same as Safari. A `WKWebView` in a background-only process is
  possible but adds its own problems.
- `WKWebView` has its own data store. It cannot read Safari's cookie jar, so "everything I
  would see if I browsed myself" still means logging in once inside your app.

## Option C: Playwright with the WebKit build

Headless, scriptable, persistent contexts (`launch_persistent_context` keeps cookies on disk),
runs fine on Apple Silicon, and installs in one command. It is not Safari and does not touch
Safari's state, but it gives you the two properties you actually want (JS rendering and durable
sessions) for approximately zero engineering.

## Recommendation

**Phase 1 needs none of this.** Six ICS feeds, `httpx`, done. Do not build a browser to solve a
problem you do not have yet.

**When you hit Tier C, start with Playwright/WebKit.** Prove the pipeline needs a browser at
all, and find out which pages actually require one. Wrap it behind a CLI with the same command
surface you would give `remotectl fetch`. That keeps the interface stable.

**Build the RemoteControl WebKit app when, and only when, you want it for its own sake.** It is
a genuinely better long-term artifact than a Playwright wrapper, and it is a natural next step
for the pattern you already validated (RemoteControl's README says the pattern is headed into
Batty anyway). But treat it as the interesting project it is, not as a dependency of the
calendar. Otherwise the calendar ships in six months instead of six days.

**Skip safaridriver** unless a specific page turns out to need real Safari specifically. The
isolated-profile behavior removes its main appeal.

Either way, the seam is the same: a CLI that takes a URL and returns rendered HTML or extracted
JSON. `sources.yaml` names an adapter, the adapter shells out to that CLI, and swapping the
implementation underneath costs you nothing.

---

# Part 3: Pipeline

## Architectural constraint

Deterministic Python does fetching, parsing, date math, dedupe, and emission. The local model
is called for exactly two jobs:

| Job | Why a model |
|---|---|
| **HTML to JSON extraction** on Tier C sources | Freeform wiki and marketing pages have no stable selectors. A model reading cleaned text into a JSON schema survives layout churn that CSS selectors do not. |
| **Categorization and summarization** | Tagging woodworking / electronics / textiles / social / class-vs-open-shop, and writing a one-line RSS summary. Genuinely a language task. |

Everything else is code. An agent re-deciding how to parse Ace's calendar every night is
nondeterministic where determinism is required, and removes your ability to tell "the feed
changed" from "the model had a bad night."

## Layout

```
~/projects/maker-calendar/
  sources.yaml
  pipeline/
    fetch.py                # conditional GET, cache, rate limit, robots
    browse.py               # shells out to the browser CLI (Part 2)
    adapters/
      ics.py                # icalendar + recurring_ical_events
      gcal.py               # thin wrapper over ics.py
      tribe_rest.py         # The Events Calendar WP REST
      jsonld.py             # schema.org/Event from embedded JSON-LD
      llm_html.py           # cleaned text -> LM Studio -> JSON
    normalize.py
    dedupe.py
    enrich.py
    emit_ics.py
    emit_rss.py
    verify.py
  raw/YYYY-MM-DD/           # every raw payload, verbatim, keep 30 days
  db/events.sqlite
  out/{bayarea-makerspaces.ics, feed.xml, events.json, health.json}
  logs/
  AGENTS.md
```

Keeping `raw/` is what lets you diff yesterday against today and know in ten seconds whether
the source changed or your code did.

## Canonical schema

```python
{
  "uid": str, "space_id": str, "source_label": str,
  "title": str, "start_utc": str, "end_utc": str|None,
  "tz": str, "all_day": bool,
  "location_name": str|None, "address": str|None,
  "url": str, "price": str|None,       # keep price as source text, do not parse to float
  "description": str,                   # truncated, see Part 5
  "categories": [str], "summary_line": str,   # both LLM-assigned
  "rrule": str|None,
  "first_seen": str, "last_seen": str, "content_hash": str,
}
```

**UID stability makes or breaks this.** If UIDs churn, every subscriber sees every event as new
every night.
- Source supplies a UID (all ICS feeds do): keep it, namespaced as `{space_id}:{source_uid}`.
- Otherwise: `sha1(space_id + start_utc + normalize(title))[:16]` where `normalize` lowercases,
  strips punctuation, collapses whitespace.
- Never include a scrape timestamp, page position, or any LLM output in the UID.

**Timezone rule.** Parse to an aware datetime immediately, store UTC, carry the original tz
string. Assert and fail the run if a naive datetime reaches `normalize.py`.

## Fetch layer

- `httpx` with HTTP/2, one client, follow redirects.
- Conditional GET: store `ETag` / `Last-Modified` per source in SQLite. A 304 skips parsing
  entirely and just bumps `last_seen`.
- 2 seconds between requests to the same host. These are volunteer nonprofits on shared hosting.
- Honor `robots.txt` per host (`urllib.robotparser`), cached per run.
- User-Agent with a real contact address, so someone can email you instead of blocking you.
- 30s timeout, 2 retries with backoff, then mark the source `failed` and **carry forward the
  previous run's events**. A transient 503 must never silently delete a space from the calendar.

## Extraction, in order

**ICS / gCal.** `icalendar` to parse, `recurring-ical-events` to expand RRULEs over the horizon
(today to today+120d). Watch for `DTSTART;VALUE=DATE`, floating times with no TZID, and Luma's
multi-day-as-all-day behavior.

**Tribe REST.** `GET /wp-json/tribe/events/v1/events?start_date=...&per_page=50`, paginate on
`next_rest_url`. Prefer over the ICS export from the same site.

**JSON-LD.** Collect every `<script type="application/ld+json">`, parse, walk for `@type: Event`
(handle `@graph` and arrays). `extruct` handles the edge cases.

**LLM extraction**, only after the above fail:
1. Fetch (via the browser CLI if the page needs JS), strip `script`/`style`/`nav`/`footer`,
   convert to text with `trafilatura` or `readability-lxml`.
2. Truncate to roughly 6k tokens; chunk by date heading if longer.
3. Send to LM Studio with a strict JSON schema and today's date as an explicit anchor.
4. Validate against a Pydantic model. On failure, retry once with the validation error appended.
   On second failure, quarantine and log. Never emit unvalidated model output.

**The single most important constraint:** have the model return dates and times as
**source-verbatim strings**, not computed ISO timestamps. Let `dateutil` plus the known source
timezone do the parsing. A 27B model will confidently turn "Wednesday at 7pm" into
`2026-08-12T19:00:00Z`, be off by seven hours, and never flag it.

```python
# LM Studio: OpenAI-compatible at http://localhost:1234/v1
resp = client.chat.completions.create(
    model="qwen3.6-27b",
    temperature=0,
    messages=[
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": f"TODAY IS {today}. PAGE TEXT:\n\n{page_text}"},
    ],
    response_format={"type": "json_schema",
                     "json_schema": {"name": "events", "strict": True, "schema": EVENT_LIST_SCHEMA}},
)
```

**Model sizing.** Extraction is high-volume and low-difficulty. Do not burn Qwen3.6-27B or
Ornith-1.0-35B on every page. Use a small instruct model (3B to 8B) for extraction and
categorization; escalate to the big model only on quarantined pages. The 8 GB M2 mini could host
the small model over the LAN to keep the M4 free, but that is one more moving part. Start
single-machine.

## Dedupe and emit

**Dedupe.** Collision when: same `space_id`, starts within 30 minutes, and
`rapidfuzz.token_set_ratio(a, b) >= 85`. Keep the higher-`trust` record, merge in non-null fields
the winner lacks. Log every merge so you can tune the 85. For cross-space duplicates (a group
posting to both Frontier Tower's Luma and its own page), drop the `space_id` match but require
similarity >= 92 plus an address match.

**ICS output.**
- One `VCALENDAR`, `PRODID:-//brennan.sstools.co//maker-calendar//EN`,
  `X-WR-CALNAME:Bay Area Makerspaces`, `X-WR-TIMEZONE:America/Los_Angeles`.
- Emit expanded instances, not RRULEs. Cross-client recurrence handling is where this goes
  wrong, and you already have the expansion in memory.
- Every `VEVENT` gets `URL` pointing at the space's own page and `ORGANIZER` naming the space.
- Prefix `SUMMARY` with the space: `[Ace] Sewing 101 Bootcamp`.
- Also emit per-space files (`out/spaces/ace-makerspace.ics`). Cheap, and useful to people who
  only care about one space.

**RSS.** `feedgen`. `pubDate` = when you first saw the event, not the event date, so readers
surface newly announced events. Event date, space and price in the description. A `<category>`
per LLM tag.

Validate the ICS before it replaces the live file: round-trip through `icalendar`, assert it
parses and the event count is within tolerance.

## Health gates

The failure that will actually bite you is not a crash. It is a source quietly returning an
empty list while the run succeeds and the calendar shrinks.

Evaluate before overwriting `out/`:
- Per source: 0 events when the previous run had more than 0 → do not publish, carry forward, alert.
- Global: total count dropped more than 40% night over night → do not publish, alert.
- `start_utc` more than 2 years out, or in the past → drop and log.
- Empty title, or title over 200 chars → quarantine (a classic LLM-extraction signature).
- Write `out/health.json` every run: per-source status, counts, latency, HTTP status.

Alerting: `terminal-notifier` plus an appended `ALERTS.md` is enough. ntfy.sh if you want it on
your phone.

---

# Part 4: Scheduling and publishing

**Use launchd, not cron.** Cron on macOS is deprecated and handles sleep badly.

`~/Library/LaunchAgents/co.sstools.maker-calendar.plist`, `StartCalendarInterval` at 03:15,
`RunAtLoad` false, stdout/stderr into `logs/`.

Two gotchas:
- **LaunchAgents run only while the user is logged in.** A LaunchDaemon in
  `/Library/LaunchDaemons` survives reboot but runs as root with a different environment (no
  Homebrew paths, no `~/.zshrc`), so use absolute paths for everything. Note that **any browser
  approach needs a GUI session**, which argues for the agent plus auto-login rather than a daemon.
- The mini must be awake: `sudo pmset repeat wakeorpoweron MTWRFSU 03:10:00`.

The job needs LM Studio's server running. Enable its headless / run-on-login server mode and
health-check `GET http://localhost:1234/v1/models` first. If it is down, **skip the LLM stages
and emit from Tier A/B only** rather than failing the run. Make sure only one LM Studio instance
owns port 1234 (the Bionic conflict).

**Publishing.** Commit `out/` to a public GitHub repo and serve via GitHub Pages. Free,
versioned, and subscribers hitting GitHub instead of the mini means a home outage is invisible.
Pages serves `.ics` with a usable content type; `raw.githubusercontent.com` does not, so do not
hand out raw URLs. Publish `webcal://` links alongside `https://`.

Alternatives: Cloudflare Tunnel from the mini (no publish step, but the feed dies when the mini
does), or `rsync` to whatever hosts `brennan.sstools.co`.

---

# Part 5: Being a good citizen

- Public events only. Nothing behind a member login.
- Link back to the source page on every event. The point is to drive people to the spaces.
- **Publish a summary, not the full description.** Truncate to roughly 300 characters plus a
  link. Sidesteps the copyright question and is better UX in a merged feed.
- Respect `robots.txt` and rate limits even where scraping is permitted.
- Meetup's and Eventbrite's ToS prohibit scraping. Meetup is excluded for that reason; the
  Eventbrite JSON-LD parse is low-impact but it is your call.
- Publish an "about" page listing every source, the refresh cadence, and a one-email opt-out.
  Nobody has ever objected to a project that does this.

---

# Part 6: Phasing

| Phase | Scope | Outcome |
|---|---|---|
| **0** | Open each page by hand, fill the `TODO` URLs in `sources.yaml`. Half an afternoon. | A verified registry. Do not skip: a guessed feed URL fails silently as an empty calendar. |
| **1** | Fetch + ICS/gCal adapters + normalize + emit. **No LLM, no browser, no dedupe, no database.** Ace, Hacker Dojo, Sudo Room, Sequoia Fabrica, Noisebridge-Luma, Frontier-Luma. | A working merged `.ics` covering six spaces, in one evening. Ship this. |
| **2** | SQLite, conditional GET, stable UIDs, dedupe, RSS, health gates. | Something publishable that other people can subscribe to. |
| **3** | Tribe REST and JSON-LD adapters (richer Ace data, Humanmade). | Price, categories, ticket status. |
| **4** | Browser CLI (Playwright/WebKit first) plus `llm_html` adapter for the Noisebridge wiki and Maker Nexus. Categorization and summaries across everything. | The messy tail, and a genuinely useful RSS feed. |
| **5** | launchd, publishing, health alerting, OpenCode repair workflow. | Runs itself. |
| **6** | Email the spaces you could not parse. Show them the calendar. Ask for a feed. | Coverage no amount of scraping would have gotten you. |
| **Parallel** | RemoteControl + WebKit app, if and when you want it for its own sake. Drop-in replacement for the Phase 4 browser CLI. | A better artifact, on its own schedule, off the critical path. |

Phase 1 is deliberately unambitious. Six spaces with real ICS feeds is most of the actual event
volume, and having the artifact in hand makes every later decision easier to judge, including
whether Meetup or a custom browser is worth the trouble.

---

# Appendix: OpenCode usage

**Building.** Write `AGENTS.md` at the repo root covering the canonical schema, the adapter
interface contract, "never let a naive datetime past `normalize.py`," "never mutate `raw/`," and
the UID rules. Then build adapter by adapter in the TUI. Adapters are small, well-specified and
easily tested, which is the shape local models handle best.

**Enabling web search** with a local model:
```bash
export OPENCODE_ENABLE_EXA=1     # turns on the built-in websearch tool, no API key
```

**Custom browser tool** at `.opencode/tools/browse.ts`, shelling out to whichever fetcher you
settled on in Part 2. The filename becomes the tool name.

**Weekly repair pass.** When `health.json` shows a source at zero for three consecutive nights,
that adapter has drifted:
```bash
opencode run --model lmstudio/ornith-1.0-35b \
  "Source ace-makerspace returned 0 events for 3 nights. Diff raw/2026-08-01/ace-makerspace.html
   against raw/2026-08-05/ace-makerspace.html, diagnose what changed, and propose a patch to
   pipeline/adapters/tribe_rest.py. Do not modify files outside pipeline/adapters/."
```

Two caveats: `opencode run` is the documented non-interactive mode and `--format json` gives
parseable output, but there are open issues where the session permission preset blocks the
write/edit tools in fully non-interactive environments, so the agent may diagnose correctly and
then fail to apply the patch. **Run the repair pass in a git worktree and review the diff
yourself.** A bad adapter patch quietly loses a space from the calendar and you will not notice
for a week.
