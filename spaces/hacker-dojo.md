# Hacker Dojo

**ID:** `hacker-dojo` · **City:** Mountain View · **Region:** peninsula
**Address:** 855 Maude Ave, Mountain View, CA 94043
**Website:** https://hackerdojo.org/
**Status:** active — open daily 10am–9pm public / 24-7 member access; site repo last pushed 2026-08-04 and 10 events on the books this week.
**Last researched:** 2026-08-05

## Summary

Hacker Dojo is a large, long-running nonprofit hackerspace and coworking space in Mountain View (founded 2009, EIN 26-4812213). It is much more of a software/AI/startup community venue than a fabrication shop — the current calendar is Rust and Verilog meetups, AI security and 3D printing nights, a book club, game nights and a community BBQ, plus a startup accelerator and a summer camp program. Volume is high: **10 distinct events in the six days from 2026-08-05 to 2026-08-11**, most of them run by tenant groups rather than by Dojo staff.

The critical finding for this project: **Hacker Dojo no longer operates its own calendar.** The custom app at `events.hackerdojo.com` that the handoff doc describes is dead (Cloudflare 525) and is not linked from their website anywhere. Their event workflow now cross-posts to Meetup and Luma, and only the **Meetup feeds actually carry events today**.

## Verified feeds

Only sources personally fetched and confirmed to return real events, 2026-08-05.

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `ics` | `https://www.meetup.com/hackerdojo/events/ical/` | 200 | `text/calendar` | 10 VEVENTs, 2026-08-05 → 2026-08-11 | **Preferred.** Meetup's own published iCal endpoint (`PRODID:-//Meetup//Meetup Calendar 1.0//EN`, `X-WR-CALNAME:Hacker Dojo`). Clean `UID` (`event_315590395@meetup.com`), `DTSTART/DTEND;TZID=America/Los_Angeles`, `SUMMARY`, `DESCRIPTION`, `URL`, `STATUS`. **No `LOCATION` field** — hardcode the address. **Hard cap of 10 events, no pagination** (`?count=50` and `?page=2` both still return 10). |
| `rss` (adapter does not exist yet) | `https://www.meetup.com/hackerdojo/events/rss/` | 200 | `application/rss+xml` | 10 `<item>`s | Same 10 events. `<title>Events - Hacker Dojo</title>`, generator `jpmonette/feed`. `pubDate` is the **event announcement time**, not the event start time, so this feed alone cannot tell you when an event happens — the ICS is strictly better for scheduling. Useful only as a change-detection signal. |

Sample confirmed events: *Wiki3: Own Your Own AI Workshop* (Aug 5, 18:00), *South Bay AI Builders Connect* (Aug 6), *RUST MEETUP at HACKER DOJO* (Aug 6, 19:00), *Community BBQ* (Aug 7, 12:00), *Hacker's Book Club* (Aug 7), *ULTIMATE GAME NIGHT* (Aug 8), *Verilog Meetup* (Aug 9, 11:00), *Breaking Models 🚨AI Security*, *💥3D Printing💥*, *SupperHappyFundHouse Planning Committee*.

### Policy note: this overrides the handoff doc's Meetup exclusion

`maker-calendar-handoff.md` excludes Meetup on two grounds — ToS forbids scraping, and it duplicates the space's own calendar. **Neither applies here.**

1. These are Meetup's **own published feed endpoints**, not scraped pages. No rendering, no ToS problem. (The rendered `/events/` page really is client-side and really does show "0 events" to a plain GET — the feeds are the only thing that works, which is the opposite of the usual situation.)
2. There is no Dojo calendar left to duplicate. Meetup is not a redundant mirror for this space, it is **the only working source**.

**The 10-event cap is the real operational constraint.** At Hacker Dojo's volume that is roughly a **one-week horizon**, not the 120-day horizon the pipeline assumes. Events more than ~10 slots out are invisible until they enter the window. Implications: poll daily (nightly is adequate), never treat a shrinking window as an error, and expect the health gate "0 events when previous run had >0" to be the only meaningful check — a day-over-day count drop is normal here.

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| `ics` | `https://api.lu.ma/ics/get?entity=calendar&id=cal-3cBqoSizZbzVkye` | **The URL is correct and live** — returns HTTP 200, `text/calendar`, `PRODID:-//Luma//Hacker Dojo Calendar//EN`, `X-WR-CALNAME:Hacker Dojo Calendar`. It is simply **empty right now** (0 VEVENTs, 237 bytes). Luma is where their Summer Camp and ticketed programs run, so if they start using the calendar rather than standalone event pages, this fills in with zero code changes. | Re-fetch monthly; count `BEGIN:VEVENT`. Wire it up now with `verified: false` — it costs nothing and it is already pointing at the right place. |
| — | Ask Hacker Dojo directly for a feed | Their Event Request Form asks third-party hosts for their **"Luma and Meetup usernames"**, proving staff already maintain a canonical internal list to cross-post from. That list is the real source. | Email `info@hackerdojo.com` (see Contact). This is the single highest-value action for this space. |
| `ics` / `jsonld` | `events.hackerdojo.com` four feeds — `/events.ics`, `/events.json`, `/events.rss`, `/events.csv` | Confirmed via Wayback CDX to be the exact historical paths (last good captures: `events.csv` 2019-07-22 `text/csv`, `events.ics` `text/calendar`, `events.json` `application/json`, `events.rss` `application/xml`). If the origin is ever repaired these are the right URLs. | `curl -o /dev/null -w '%{http_code}'` on each. Anything other than 525 means it is back. Realistically: **do not wait for this.** |
| `nextdata` / `llm_html` | `https://hackerdojo.spaces.nexudus.com/en/events` | Nexudus is their member-management platform and does have a public-events concept. Page returns 200. | Returns a ~467 KB JS shell identical in size to the portal root, with no event list, no `.ics` link and no JSON-LD in the static HTML. Would need a browser. Low priority — likely member bookings, not public events. |

## Dead ends

Checked and confirmed non-working. Do not repeat these.

- **`events.hackerdojo.com` — DEAD, and this is the headline finding.** Every path returns **HTTP 525** (`error code: 525`, 16-byte body, `server: cloudflare`): `/`, `/feed`, `/feed.json`, `/events.json`, `/ical`, `/events.ics`, `/rss`, `/csv`, `/api/events`, plus the four canonical `/events.{ics,json,rss,csv}`.
  **525 means Cloudflare cannot complete a TLS handshake with the origin server** — the origin is down or misconfigured. This is **not a bot block.** The handoff doc's "the site blocks automated fetchers, so open it in a browser and copy the four URLs by hand" is mistaken: a human with a browser will see the same Cloudflare error page. **There is nothing for a human to click.** Do not send anyone to do this by hand.
  Wayback confirms this is long-standing, not a blip: root captures run 200 through 2022, then 2025 captures are 8× `301` and 6× `525`. It has been broken for well over a year. `hackerdojo.org` does not link to it from any page (grep count: 0), and their site source repo contains no reference to it. **It is abandoned, not merely down.**
- **`hackerdojo.org` is NOT WordPress.** It resolves to GitHub Pages IPs (185.199.108–111.153) and is a static site built from the public repo `hd-admin/hackerdojo.org`. So there is no The Events Calendar and no WP REST:
  - `/feed/` → 404 · `/feed.xml` → 404 · `/events` → 404 · `/events/` → 404 · `/wp-json/tribe/events/v1/events` → 404
  - `/?ical=1` → 200 but it just serves the unchanged homepage (identical 74,682 bytes); a query string on a static host is meaningless. **This is exactly the silent-failure trap the project warns about — it looks like a 200 and parses to nothing.**
  - No `generator` or `tec-api-version` meta tag.
- **No JSON-LD anywhere.** `application/ld+json` block count is **0** on the homepage and 0 on the Nexudus portal.
- **No Google Calendar embed.** The only two iframes on the site are a YouTube video (`youtube.com/embed/YprmMvHaWUM`) and a Google **Maps** embed. No `calendar.google.com` string appears anywhere in the page source, so there is no calendar ID to extract and the `gcal_ics` route is closed.
- **Mastodon `mastodon.social/@HackerDojo`** — `.rss` returns 200 with 7 items, but the newest post is **2022-11-18**. Account abandoned. Verified real (title `HackerDojo`), but useless.
- **`sfba.social/@hackerdojo.rss`** → 404. No account on the Bay Area instance.
- **Bluesky `hackerdojo.bsky.social`** — `/rss` returns 200 but contains only **1 `<item>`**; latest post 2025-09-03. Verified genuine (posts link to meetup.com/hackerdojo events). Too sparse and stale to be a change signal. `bsky.app/profile/hackerdojo.com/rss` → 404.
- **Wiki `wiki.hackerdojo.com`** — MediaWiki 1.38.2 recent-changes Atom feed is live and valid (`?title=Special:RecentChanges&feed=atom`) but returned **zero entries** for the last 7 days. Wiki is dormant, and it is a change feed, not an events feed.
- **Eventbrite** — `eventbrite.com/o/hacker-dojo` → 404. No organizer page.
- **Meetup rendered pages** — `/events/calendar/` returns 200 / 260 KB of HTML but is client-rendered and shows 0 events to a plain GET, as the handoff doc predicts. Scraping it would also breach ToS. Use the feed endpoints above instead.
- **Ticket Tailor / Bookwhen** — a "Chips @ Dojo" event in March 2026 was ticketed via Ticket Tailor, but under the **third-party organizer `asfigo`**, not Hacker Dojo. Not a Dojo-controlled source. Same story with Repair Café Silicon Valley, which books the venue but publishes on its own site. **Some venue events therefore appear in no Dojo-controlled feed at all** — the Meetup feed is not complete coverage of what happens at 855 Maude.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Meetup | `hackerdojo` | https://www.meetup.com/hackerdojo/ | `/events/ical/` and `/events/rss/` | **Yes** | ~19,800 members. The only live event source. Organized by Hacker Dojo plus ~34 tenant groups, so it carries meetups the Dojo itself does not run. |
| Luma | `hackerdojo` | https://luma.com/user/hackerdojo | `api.lu.ma/ics/get?entity=calendar&id=cal-3cBqoSizZbzVkye` | Endpoint yes, content no | 16 events hosted, joined July 2024. Used for ticketed programs (Summer Camp). Calendar ICS is valid but empty. |
| Discord | Hacker Dojo | https://discord.gg/qFWuymdqK5 | none | Yes | **Human channel, not a feed.** Invite resolves to guild "Hacker Dojo" (id 698267668918173827), ~2,023 members / ~185 online, non-expiring. Linked from their homepage, so official. Best informal DM route. |
| X / Twitter | `@hackerdojo` | https://x.com/hackerdojo | none (200 but login-walled) | Partial | Linked from homepage. No RSS. |
| Bluesky | `hackerdojo.bsky.social` | https://bsky.app/profile/hackerdojo.bsky.social | `/rss` → 200, 1 item | Yes, but stale | Last post 2025-09-03. |
| Mastodon | `@HackerDojo` | https://mastodon.social/@HackerDojo | `.rss` → 200, 7 items | Yes, but dead | Last post 2022-11-18. |
| Instagram | `hackerdojo` | https://www.instagram.com/hackerdojo/ | none | 200 only | Login-walled. |
| Facebook | `hackerdojo` | https://facebook.com/hackerdojo | none | No — 400 | Rejects unauthenticated GET. |
| YouTube | `@hackerdojo` | https://www.youtube.com/@hackerdojo | channel RSS possible | Not tested | Talk recordings, not scheduling. |
| Newsletter | Kit / ConvertKit | https://hacker-dojo.kit.com/0771bb4bcb | none | Yes | **Human channel.** Volunteer signup form, signed "Events Committee". No newsletter signup exists on hackerdojo.org itself. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| General / info | — | Email | `info@hackerdojo.com` — **verified**, the only email published on the site. Note the domain is **hackerdojo.com**, not .org | `mailto:` on https://hackerdojo.org/summer-camp/ |
| Events volunteering | Events Committee | Email | `volunteer@hackerdojo.com` — verified | https://hacker-dojo.kit.com/0771bb4bcb |
| **Events / booking** | — | **Google Form** | [Hacker Dojo Event Request Form](https://docs.google.com/forms/d/e/1FAIpQLSeLsDfSnzmwsG4Qok369v-Rnw8O_SO1u17OPUg3_upcp7n42w/viewform) — asks hosts for their **Luma and Meetup usernames** | hackerdojo.org |
| Executive Director | **Qi Diaz** | LinkedIn `qidiaz` | Interim from Mar 2025, now ED. **Right person for a partnership ask.** | FY2025 IRS 990; hackerdojo.org/accelerator |
| Board Director + Summer Camp organizer | **Eva Carrender** | LinkedIn `evacarrender` | Best-documented **programming/events** person — Summer Camp page reads "Organized by Hacker Dojo & Eva Carrender" | hackerdojo.org/summer-camp/ |
| Board Director (former Chair) | Emily Johnson | LinkedIn `emilymjohnson` | — | hackerdojo.org; FY2025 990 |
| Board Director | Marco Palacios | LinkedIn `marcopalacios` | — | hackerdojo.org |
| Board Director | Peter Theobald | LinkedIn `peterjtheobald` | — | hackerdojo.org |
| Board Director | Clifford Miranda | — | On the 990, not on the website | FY2025 990 |
| Board Advisor / co-founder | David Weekly | LinkedIn `dweekly` | Founded the Dojo in 2009 | hackerdojo.org |
| Board Advisor | Mark Stofer | LinkedIn `mark-stofer-899b63277` | Brand & marketing | hackerdojo.org |
| Phone | — | Voice | (650) 429-8605 | hackerdojo.org |
| Tours | Front desk | Calendly | https://calendly.com/hdfrontdesk/30min | hackerdojo.org |
| Address | — | Physical | 855 Maude Ave, Mountain View, CA 94043 | hackerdojo.org |

**Best outreach path:** Email **`info@hackerdojo.com`** addressed to **Qi Diaz (Executive Director)**, cc'ing or naming **Eva Carrender** as the events person. The pitch writes itself and is unusually strong here: their own events app has been returning a Cloudflare 525 for over a year, they are cross-posting by hand to two platforms, and the Meetup feed the public can actually reach is truncated at 10 events. Offer them a working merged feed and ask for the internal list they already maintain to cross-post from. If email stalls, the Discord (~2,023 members, ~185 online) is a live and genuinely responsive back channel.

## Recommended `sources.yaml` entry

```yaml
  - id: hacker-dojo
    name: Hacker Dojo
    city: Mountain View
    region: peninsula
    url: https://hackerdojo.org/
    sources:
      # NOTE: this space is a documented exception to the project-wide Meetup
      # exclusion. These are Meetup's own PUBLISHED feed endpoints, not scraped
      # pages, so there is no ToS issue -- and Hacker Dojo has no calendar of
      # its own left to duplicate. events.hackerdojo.com has returned Cloudflare
      # 525 (origin unreachable) for over a year and is unlinked from their site.
      # Meetup is currently the ONLY working source for this space.
      - adapter: ics
        url: https://www.meetup.com/hackerdojo/events/ical/
        label: meetup-ical
        trust: 100
        verified: true
        notes: >
          HARD CAP OF 10 EVENTS, no pagination (?count= and ?page= are ignored).
          At this space's volume that is roughly a 1-week horizon, not 120 days.
          Do NOT treat a day-over-day count drop as a failure for this source;
          only alert when it returns 0 after a run with >0. Feed has no LOCATION
          field -- inject "855 Maude Ave, Mountain View, CA 94043". Times are
          correctly zoned (DTSTART;TZID=America/Los_Angeles). UIDs are stable
          (event_<id>@meetup.com), so namespace them as hacker-dojo:<uid>.

      # Correct, live Luma calendar URL -- returns 200 text/calendar with
      # X-WR-CALNAME:Hacker Dojo Calendar, but 0 VEVENTs as of 2026-08-05.
      # Costs nothing to poll and starts working the day they use it.
      - adapter: ics
        url: https://api.lu.ma/ics/get?entity=calendar&id=cal-3cBqoSizZbzVkye
        label: luma-calendar
        trust: 90
        verified: false
        notes: >
          Endpoint verified correct, content currently empty. Luma is where their
          ticketed programs (Summer Camp) run, so this may populate. Re-check monthly.

      # DEAD. Retained only so nobody re-derives these URLs. All four return 525.
      # - adapter: ics
      #   url: https://events.hackerdojo.com/events.ics
      # - adapter: jsonld
      #   url: https://events.hackerdojo.com/events.json
      #   # also /events.rss and /events.csv -- paths confirmed via Wayback CDX
```

## Research log

- 2026-08-05 — Probed 9 candidate paths on `events.hackerdojo.com` (`/`, `/feed`, `/feed.json`, `/events.json`, `/ical`, `/events.ics`, `/rss`, `/csv`, `/api/events`) with a realistic desktop Chrome User-Agent. **All 9 returned HTTP 525** with a 16-byte `error code: 525` body from Cloudflare. Confirmed via response headers (`server: cloudflare`, `cf-ray`) that this is an origin TLS handshake failure, **not** a bot block — so the handoff doc's "open it in a browser and copy the URLs by hand" instruction cannot work and was corrected.
- Queried the Wayback CDX API for `events.hackerdojo.com*` and recovered the four canonical historical feed paths: `/events.ics` (`text/calendar`), `/events.json` (`application/json`), `/events.rss` (`application/xml`), `/events.csv` (`text/csv`, last good capture 2019-07-22). Tested all four live — all 525. Year-bucketed the root captures: HTTP 200 through 2022, then 2025 shows 8× 301 and 6× 525, establishing the outage as long-standing rather than transient.
- Fetched `hackerdojo.org` (74,682 bytes) and grepped the source for iframes, JSON-LD, generator meta and outbound links. Found zero `application/ld+json` blocks, zero `calendar.google.com` references, and only two iframes (YouTube + Google Maps). `dig` showed GitHub Pages IPs, confirming a static site; the public source repo `hd-admin/hackerdojo.org` was last pushed 2026-08-04, confirming the org is active.
- Tested the WordPress/Tribe hypothesis and ruled it out: `/feed/`, `/feed.xml`, `/events`, `/events/`, `/wp-json/tribe/events/v1/events` all 404. `/?ical=1` returns 200 but serves the byte-identical homepage — flagged as a silent-failure trap.
- Discovered `lu.ma/user/hackerdojo`, `meetup.com/hackerdojo/events/calendar/`, `wiki.hackerdojo.com`, `discord.gg/qFWuymdqK5` and `hackerdojo.spaces.nexudus.com` in the homepage link graph.
- **Tested Meetup's published feed endpoints — both work.** `/events/rss/` → 200 `application/rss+xml`, 10 `<item>`s; `/events/ical/` → 200 `text/calendar`, 10 VEVENTs spanning 2026-08-05 to 2026-08-11 with real titles, TZID-qualified start/end times, stable UIDs and per-event URLs. Probed for pagination (`?count=50`, `?page=2`) — both capped at 10, so the cap is a hard platform limit. Noted the absence of `LOCATION` and that RSS `pubDate` is announcement time, not event time.
- Extracted Luma calendar id `cal-3cBqoSizZbzVkye` ("Hacker Dojo Calendar") from `lu.ma/hackerdojo`, then fetched `api.lu.ma/ics/get?entity=calendar&id=...` → 200 `text/calendar` with the correct `X-WR-CALNAME` but **0 VEVENTs** (237 bytes). Recorded as a correct-but-empty endpoint rather than a verified feed.
- Tested social feeds by appending `.rss` / `/rss`: Mastodon `@HackerDojo` (7 items, newest 2022-11-18), Bluesky `hackerdojo.bsky.social` (1 item, newest 2025-09-03), `sfba.social/@hackerdojo` (404), MediaWiki recent-changes Atom (valid, 0 entries in 7 days). Verified each account's identity from feed titles and post content rather than assuming the handle was correct.
- Checked and ruled out Eventbrite (`/o/hacker-dojo` → 404) and the Nexudus member portal (`/en/events` returns a 467 KB JS shell with no event list, no JSON-LD, no `.ics` link).
- Ran a parallel contact-research pass over hackerdojo.org subpages, the FY2025 IRS Form 990 via ProPublica, and Mountain View Voice coverage. Confirmed `info@hackerdojo.com` and `volunteer@hackerdojo.com` as published addresses, identified Qi Diaz as current ED (succeeding Eric Hess ~Mar 2025) and the five-person board, and located the Event Request Form — which asks hosts for their Luma **and** Meetup usernames, confirming the cross-post workflow. Rejected a LeadIQ-sourced `First.Last@` pattern as unverified third-party scrape. Operating status corroborated by a Sept 2026 Repair Café booking, an Aug 2026 fundraiser, a May–June 2026 accelerator cohort and a live 2,023-member Discord; FY2025 financials show a ~$61k deficit on $495k revenue and a lease running to roughly 2027.
