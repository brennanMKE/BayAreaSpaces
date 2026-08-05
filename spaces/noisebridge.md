# Noisebridge

**ID:** `noisebridge` · **City:** San Francisco · **Region:** sf
**Address:** 272 Capp St, San Francisco, CA 94110
**Website:** https://www.noisebridge.net/
**Status:** active — wiki edited 2026-08-04, Meetup RSS rebuilt 2026-08-05, 5MoF wiki pages exist through 2026-07; still at 272 Capp with ongoing publicly-discussed rent pressure.
**Last researched:** 2026-08-05

## Summary

Noisebridge is an anarchist, do-ocratic, donation-funded hackerspace in the Mission, founded 2007 and at 272 Capp St since 2020. It runs a dense standing schedule — roughly 30 recurring weekly/monthly events (wood shop, circuit hacking, gamedev, sewing, radio, robotics, laser cutter training, Five Minutes of Fame) plus one-off workshops. Volume is high, maybe 15-25 event instances a week, but it is spread across four surfaces that nobody reconciles: the wiki itself says events are "haphazardly cross-posted on Meetup, the Discord, and Google Calendar." There is no single machine-readable source that covers it all, and the one gCal that the wiki still embeds has been dead since 2024.

## Verified feeds

Only sources you personally fetched and confirmed return real events.

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `ics` | `https://api.lu.ma/ics/get?entity=calendar&id=cal-0aMKsxMfZ73gRpI` | 200 | `text/calendar; charset=utf-8` (23,934 B) | 46 VEVENT total, 2024-08-08 → 2026-08-21; **only 1 at/after today** | The real Luma feed. Calendar `api_id` `cal-0aMKsxMfZ73gRpI` extracted from the `luma.com/noisebridge` HTML. Clean UIDs (`evt-…@events.lu.ma`), no RRULEs (instances only). Self-described "Main public calendar for noisebridge events" but in practice used for one-offs and 5MoF; it is nearly empty going forward. |
| `llm_html` | `https://www.noisebridge.net/api.php?action=parse&page=Category:Events&prop=wikitext&format=json&formatversion=2` | 200 | `application/json; charset=utf-8` (41,436 B) | Full standing weekly schedule, ~30 recurring events | MediaWiki 1.39.13 API. Returns the raw wikitext of the schedule as clean `wikitable` rows (Tags / Time / Image / Title / Description) with `{{Recurring}}` and `{{RecurringNumbered|1st}}` markers. **Far better than scraping the rendered page** — recurrence is explicit in the markup. Note `/wiki/Events` is a redirect to `Category:Events`; `/w/api.php` is a 404, the API is at `/api.php`. |
| `rss` (change signal) | `https://www.meetup.com/noisebridge/events/rss/` | 200 | `application/rss+xml` (16,031 B) | 10 `<item>`s, all real current events (Gamebridge, School of Melee, …), `lastBuildDate` 2026-08-05 | **Meetup's official published feed still works.** Fetching it is not scraping. **But every item has only `title`, `link`, `guid`, `pubDate`, `description` — there is no event start date field.** `pubDate` is when the listing was posted, not when the event happens. Usable as a discovery/change signal and to confirm a group is alive; **not usable as an event source** without a second fetch of each event page, which the ToS forbids. |
| `rss` (change signal) | `https://www.noisebridge.net/wiki/Special:RecentChanges?feed=rss` | 200 | `application/xml; charset=UTF-8` (226,604 B) | 22 items, `lastBuildDate` 2026-08-05, top edit `Finances/Rent` 2026-08-04 | MediaWiki RecentChanges. No events; use it to detect when the schedule page changes so the hand-maintained RRULEs get revisited. `feed=atom` also works. |
| `rss` (social) | `https://sfba.social/@Noisebridge.rss` | 200 | `application/rss+xml; charset=utf-8` (2,079 B) | 1 item, dated 2022-11-08 | Real account ("Noisebridge Hackerspace"), but the only post is the 2022 `#introduction`. Feed is live, account is dormant. No event value. |

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| — | Ask Noisebridge to add the standing schedule to the Luma calendar as recurring events | Luma is the one surface that already emits a correct ICS; it is simply underused. One conversation converts this space from Tier C to Tier A permanently. | Email `secretary@noisebridge.net` / raise at the Tuesday meeting. See **Best outreach path**. |
| — | Ask whether anyone still holds edit rights on the "Noisebridge Daily" Google Calendar and whether it can be revived or unpublished | The calendar object still exists and is still world-readable and still embedded on the wiki front page; someone owns it. Reviving it is cheaper than building anything. | Ask in Discord `#meetup-infra` or at the Tuesday meeting. |
| `jsonld` | `https://dothebay.com/venues/noisebridge-hackerspace` | DoTheBay carries a Noisebridge venue page and aggregators of that type usually embed `schema.org/Event`. Cross-check only, never authoritative. | Fetch and grep for `application/ld+json`; walk for `@type: Event`. |
| `ics` | Per-event Meetup ICS (`.../events/{id}/ical/`) | Meetup historically exposed a per-event iCal link; if it still resolves, the RSS `guid` gives you the event id and you could get real dates from published endpoints only. | Probe one id from the RSS (e.g. `315718078`) and check status/content-type. Was not probed here to stay clearly on the right side of Meetup's ToS. |
| — | Discord `#events` / per-event channels | The wiki repeatedly points at Discord as where dates are actually confirmed ("Confirm dates in #woodshop on Discord"). | Human channel — no feed. Join `discord.gg/noisebridge` and read. |

## Dead ends

- **`https://noisebridge.today/` is gone.** The domain is now a parked page: title `noisebridge.today`, body "This domain may be for sale.", served with `assets.abovedomains.com/javascript/forsale.min.js`. The member-run daily calendar site no longer exists. Anything in older notes describing it as a live surface is stale.

- **The "Noisebridge Daily" Google Calendar resolves but is poisoned. Do not wire it up.**
  `https://calendar.google.com/calendar/ical/6a2a8ad7ac4d009565a90f6aafb901c2dafa352ce86b3164108721c6767271fb@group.calendar.google.com/public/basic.ics`
  → **200 `text/calendar`, 12,748 B, `X-WR-CALNAME:Noisebridge Daily`, 28 VEVENTs.** The ID from the older file is correct and it does return a parseable calendar. `full.ics` returns byte-identical content.
  **But it is abandoned:** the latest `DTSTART` of any kind is `2024-01-19`, and the newest `LAST-MODIFIED` is `2024-06-13`. Most RRULEs carry an `UNTIL` in 2023-2024 and have correctly expired. **Five do not**, and will expand into 2026 forever:
  - `🎇 Laser Cutter Training` — `FREQ=WEEKLY;BYDAY=SU`
  - `🐍 PyClass - Python Meetup` — `FREQ=WEEKLY;BYDAY=MO`
  - `🧼 Noisebridge Cleaning Mornings` — `FREQ=WEEKLY;BYDAY=SA`
  - `🏳 Conflict Resolution Council` — `FREQ=MONTHLY;BYDAY=1WE`
  - `💠 Advanced Geometry Meetup` — `FREQ=MONTHLY;BYDAY=2TH`

  This is the exact failure this project is built to avoid, inverted: not a silently empty feed but a silently *fabricating* one. It passes every health gate — 200, valid ICS, non-zero event count, plausible titles, dates in the future — while publishing five events a week that no one has confirmed have happened since early 2024. **`verified: false`, and leave it disabled.**

- **The second embedded calendar is 404.** The wiki front page also embeds `v4694n2t0jmpg2i9i2fck7uiuq2oo8f7@import.calendar.google.com`. Its `public/basic.ics` returns **404**. Import-type calendars are not publicly shareable that way. The wiki has already struck this link through (`<s>…</s>`), so it is knowingly dead.

- **No blog/RSS on noisebridge.net.** `/feed/`, `/rss`, and `/blog/feed/` all return **200 `text/html`, 72,954 B — byte-identical (md5 `2914422f…`) to the homepage.** MediaWiki soft-404s to the main page. These are not feeds; a naive fetcher would happily accept the 200 and parse nothing. There is no blog.

- **`https://www.noisebridge.net/w/api.php` → 404** and **`/wiki/api.php` → 404.** The API lives at `/api.php`.

- **`/wiki/Events` is a `#REDIRECT [[Category:Events]]`** — the wikitext of `Events` itself is 29 characters. Request `Category:Events` instead.

- **`Category:Events` membership is an archive, not a schedule.** 399 members, mostly historical (`25c3`, `5MoF/2022/03 17`, one-off 2011 workshops). Do not treat category membership as upcoming events. It is useful for one thing: the newest `5MoF/2026/07 16` page confirms 5MoF is still running.

- **The mailing lists are down.** `https://lists.noisebridge.net/` and both `listinfo/noisebridge-announce` and `listinfo/noisebridge-discuss` return **200 `text/plain`, 39 bytes: "Mailman list archives currently offline"**. The addresses may still deliver; the web archives and subscribe pages do not work. Do not send someone to those URLs.

- **`discuss.noisebridge.info` (the Discourse forum) is gone.** TLS fails — the cert presented is `CN=autothat.com` with SAN `*.autothat.com`. Ignoring cert validation returns 200 `application/json`, 58 bytes, and `/latest.rss` 404s. The domain no longer points at Noisebridge. Search results still reference old threads on it; they are unreachable.

- **No JSON-LD anywhere useful.** `noisebridge.net/wiki/Events` has zero `application/ld+json` blocks. `luma.com/noisebridge` has one, but it is page/organization metadata, not `Event` objects — the ICS supersedes it.

- **No Bluesky account found.** `bsky.app/profile/noisebridge.net/rss`, `noisebridge.bsky.social/rss`, and `noisebridge.sfba.social/rss` all 404. Search turned up nothing.

- **No Mastodon on the obvious big instances.** `mastodon.social/@noisebridge` and `hackaday.social/@noisebridge` both 404. The real account is on `sfba.social` (see Social).

- **`luma.com/noisebridge/ics` is not the feed.** It returns 200 but `text/html`, 160,583 B — the SPA shell, not a calendar. Likewise `api.lu.ma/ics/get?entity=calendar&id=noisebridge` (the slug) → **404 JSON**. The Luma ICS requires the internal `cal-…` api_id, not the vanity slug.

- **`robots.txt` note:** `https://noisebridge.net/robots.txt` (200) disallows `/wiki/86` and friends, and then fully disallows **`ClaudeBot`** and **`Amazonbot`** (`Disallow: /`). A custom pipeline UA is not covered by those rules, but the site clearly does not want LLM crawlers, which is one more argument for hitting `api.php` once for the schedule and then not crawling it nightly.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Mastodon | `@Noisebridge@sfba.social` | https://sfba.social/@Noisebridge | `https://sfba.social/@Noisebridge.rss` | yes — 200 `application/rss+xml`, 2,079 B | Live feed, dormant account: one post, 2022-11-08. Not a DM channel worth trying. |
| Discord | invite `discord.gg/noisebridge` | https://discord.gg/noisebridge | none | invite resolves 200 | **The real day-to-day channel.** The wiki routes date confirmation here (`#woodshop`, `#meetup-infra`, `#facilities…`, `#events`). Human channel, not a feed. Other live invites on the wiki: `discord.gg/cHd7j4ar9g`, `discord.gg/GtpDdX5`, `discord.gg/WjXksxMUdq`. |
| Meetup | `noisebridge` | https://www.meetup.com/noisebridge/ | `https://www.meetup.com/noisebridge/events/rss/` | yes — 200, 10 items, but **no event dates** | ~14.4k members. Wiki's own Contacts page calls it the "best up to date events list and good way to contact event hosts." |
| Instagram | `noisebridgehackerspace` | https://www.instagram.com/noisebridgehackerspace/ | none | profile URL from search; `instagram.com/noisebridge` also 200 | Not verified as official by a link from noisebridge.net. |
| X / Twitter | `NoisebridgeBot` | https://twitter.com/NoisebridgeBot | none | linked from the wiki homepage | A bot account, not staffed. `x.com/noisebridge` also returns 200 but X returns 200 for most paths. |
| YouTube | `@noisebridge` | https://www.youtube.com/@noisebridge | channel RSS possible via channel_id | 200, channel_id not resolved | Streams 5MoF and other `{{Streaming}}`-tagged events. |
| GitHub | `noisebridge` | https://github.com/noisebridge | org Atom feeds | 200 | Infra/code only. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| Admins / general | — | email | `secretary@noisebridge.net` | wiki `/wiki/Contacts` |
| Treasurer | — | email | `treasurer@noisebridge.net` | wiki `/wiki/Contacts` |
| Press | — | email | `press@noisebridge.net` | wiki `/wiki/Contacts` |
| Announcements list | — | email | `noisebridge-announce@lists.noisebridge.net` (low traffic, "updates on current and upcoming events") | wiki `/wiki/Mailing_list` — **web subscribe page currently offline** |
| Discussion list | — | email | `noisebridge-discuss@lists.noisebridge.net` | wiki `/wiki/Mailing_list` — page marked OUTDATED as of mid-2021; **web subscribe page currently offline** |
| Day-to-day community | — | Discord | https://discord.gg/noisebridge | wiki homepage |
| Consensus / decisions | whoever shows up | in person + Jitsi | **Noisebridge Weekly Meeting, Tuesdays 7:00pm**, 272 Capp St, also online via Jitsi | wiki `Category:Events`, `/wiki/Meetings` |
| Event onboarding | no named owner | process | "send a message and ask to become an event organizer" on Meetup; announce at 2 meetings before the event; add yourself to the wiki | wiki `/wiki/Hosting_an_Event` |
| Mailing address | — | post | Noisebridge, 2261 Market Street #235-A, San Francisco, CA 94114 | wiki `/wiki/Contacts` |
| Physical space | — | in person | 272 Capp St, San Francisco, CA 94110 — "early evenings is nearly guaranteed to find the space open - ring the doorbell!" | wiki `/wiki/Contacts` |

**Best outreach path:** Email `secretary@noisebridge.net` with a link to the working merged calendar, and — because Noisebridge is do-ocratic and consensus-run, with no calendar owner and no events coordinator — also show up (or Jitsi in) to the **Tuesday 7:00pm weekly meeting**, where new events and proposals are actually announced. The concrete ask is small and self-interested for them: put the standing weekly schedule into the existing Luma calendar as recurring events, and either revive or unpublish the dead "Noisebridge Daily" gCal that their own front page still embeds. Do not rely on the mailing lists; that infrastructure is currently offline. Discord is the fastest way to find the individual humans who run each night.

## Recommended `sources.yaml` entry

```yaml
  - id: noisebridge
    name: Noisebridge
    city: San Francisco
    region: sf
    url: https://www.noisebridge.net/
    sources:
      - adapter: ics
        url: https://api.lu.ma/ics/get?entity=calendar&id=cal-0aMKsxMfZ73gRpI
        label: luma
        trust: 100
        verified: true
        notes: >
          Real ICS, 46 events, clean evt-*@events.lu.ma UIDs, no RRULEs.
          Self-described "main public calendar" but sparse: only 1 event at or
          after 2026-08-05. Do not expect this alone to cover Noisebridge.
          Calendar api_id cal-0aMKsxMfZ73gRpI came from the luma.com/noisebridge
          HTML; the vanity slug does NOT work against api.lu.ma.

      - adapter: llm_html
        url: https://www.noisebridge.net/api.php?action=parse&page=Category:Events&prop=wikitext&format=json&formatversion=2
        label: wiki-recurring
        trust: 40
        verified: true
        notes: >
          MediaWiki API wikitext, not rendered HTML. Returns the standing weekly
          schedule as wikitable rows with explicit {{Recurring}} /
          {{RecurringNumbered|1st}} recurrence markers. Extract ONCE, convert to
          hand-maintained RRULEs (see the standing schedule table in
          spaces/noisebridge.md), then stop crawling. Watch
          Special:RecentChanges?feed=rss to know when to redo it.
          NOTE: /wiki/Events is a redirect; the content lives on Category:Events.
          robots.txt fully disallows ClaudeBot and Amazonbot.

      # DISABLED — verified broken in a way that passes health checks.
      # Returns 200 text/calendar with 28 VEVENTs, but the calendar has been
      # abandoned since 2024-01 (newest LAST-MODIFIED 2024-06-13) and five
      # RRULEs have no UNTIL, so it fabricates ~5 plausible events per week
      # forever. See "Dead ends" in spaces/noisebridge.md before re-enabling.
      # - adapter: gcal_ics
      #   calendar_id: 6a2a8ad7ac4d009565a90f6aafb901c2dafa352ce86b3164108721c6767271fb@group.calendar.google.com
      #   label: noisebridge-today
      #   trust: 70
      #   verified: false

      # NOT AN EVENT SOURCE — official Meetup RSS, 200, 10 real current items,
      # but items carry no event start date (only pubDate = when posted).
      # Keep as a liveness/discovery signal only. Meetup remains excluded from
      # the pipeline per the project-wide policy.
      # - adapter: rss
      #   url: https://www.meetup.com/noisebridge/events/rss/
      #   label: meetup-discovery
      #   trust: 10
      #   verified: true
```

## Standing weekly schedule (from the wiki)

Source: `api.php?action=parse&page=Category:Events&prop=wikitext`. `W` = every week. Ordinals mean that week of the month. Convert these to hand-maintained RRULEs; do not crawl nightly.

| Day | Time | Event | Notes |
|---|---|---|---|
| Monday | 6:00pm–9:00pm | Woodhacking Mondays | W. Wood Shop. Dates confirmed in Discord `#woodshop` / Meetup. |
| Monday | 7:00pm | Meetups/Infra | W. Self-hosting and infra. Discord `#meetup-infra`. |
| Monday | 7:00pm | Circuit Hacking Monday | W. Electronics/Arduino/soldering. "Most Mondays." |
| Tuesday | 5:00pm | Premeeting Social Story Time | W. Streamed. Precedes the weekly meeting. |
| **Tuesday** | **7:00pm** | **Noisebridge Weekly Meeting** | **W. In person + Jitsi, streamed. The consensus meeting — this is where to raise a calendar proposal.** |
| Tuesday | 7:00pm | San Francisco Writers Workshop | W. Free drop-in, 1st-floor Hackitorium. |
| Wednesday | 6:30pm–10:30pm | Sewing Circle | W. Space limited. |
| Wednesday | 7:00pm–10:00pm | Gamebridge — Game/Dev Wednesdays | W. Streamed. Also the most active Meetup listing. |
| Wednesday | 7:00pm–9:00pm | Hambridge / Radiobridge | W. Streamed. SDR, mesh, HAM. Joint with Sudo Room. |
| Thursday | 7:00pm–10:00pm | Tablebridge | 1st Thu. Tabletop gaming and design. |
| Thursday | 5:00pm–8:00pm | HackGames | 2nd Thu. Precedes DC415. |
| Thursday | 8:00pm–9:30pm | DC415 | 2nd Thu. Defcon 415 security meetup. |
| Thursday | 7:00pm–8:00pm | Ten Minutes of Game (10MoG) | 3rd Thu. Streamed. Precedes 5MoF. |
| Thursday | 7:30pm–9:00pm | Five Minutes of Fame (5MoF) | 3rd Thu. Streamed. Flagship event; also the one thing reliably on Luma. |
| Thursday | 6:00pm–8:00pm | Songbridge | 4th Thu. Streamed. |
| Thursday | 6:00pm–7:00pm | Sewing Guild Meeting | 4th Thu. Streamed. |
| Thursday | 8:00pm–10:30pm | Resident Electronic Music | 4th Thu. Streamed. Open mic. |
| Friday | 9:00am–5:00pm | Hack on Noisebridge! | W. **Flagged pink on the wiki = "caution maybe dead."** Cleaning/reorganizing. Verify before publishing. |
| Friday | 8:30pm–11:30pm | School of Melee | 1st & 3rd Fri per the row tags. **Wiki is self-contradictory** — the description says "1st & 2nd Fridays." Confirm before encoding. |
| Friday | 7:00pm–11:59pm | Showcase Showdown | 2nd & 4th Fri. Indie game showcase and tourneys. |
| Friday | 7:00pm–10:00pm | Cyberbridge | 1st Fri. Cyberpunk / wearable XR. |
| Saturday | 12:00pm–2:00pm | Robotics Meetup | W. |
| Saturday | 4:00pm–8:00pm | Spacebridge Weekly Meeting | W. Resident space program. |
| Saturday | 8:00pm | Noisebridge Cinema! | W. |
| Saturday | 2:00pm–5:00pm | Unitybridge Unity Meetup | 1st Sat. |
| Saturday | 2:00pm–5:00pm | Unrealbridge Unreal Meetup | 2nd Sat. |
| Saturday | 2:00pm–6:00pm | Godot Meetup | 3rd Sat. |
| Saturday | 1:00pm–3:00pm | Building Guitar Pedals Workshop | 4th Sat. Discord `#pedal-building`. |
| Saturday | 2:00pm–5:00pm | Graphicsbridge | 4th Sat. |
| Saturday | 5:00pm–8:00pm | Decentralized Web | 4th Sat. |
| Sunday | 12:00pm–1:30pm | 3D Printing Class | Every other week — **no fixed anchor given on the wiki.** Needs a human to pin the phase before it can be an RRULE. |
| Sunday | 2:00pm–4:00pm | Laser Cutter safety training | W. Streamed. Limited availability. |
| Sunday | 2:00pm–3:00pm | Fabrication 101 / Wood Shop Sundays | 2nd Sun. |
| Sunday | 3:00pm–6:00pm | Handsewing Workshop | W, **on request only** — spontaneous. Probably should not be published as a scheduled event. |

Excluded deliberately: **TRASH NIGHT** (Mon and Thu, "you-o-clock") is a space chore, not a public event; the wiki highlights it in blue as space management.

Two encoding hazards carried over from the wiki: several Friday rows give end times as `11:30am` / `11:59am` where `pm` is obviously meant, and the `Recurring` vs `RecurringNumbered` tags disagree with the prose in the School of Melee row.

## Research log

- 2026-08-05 — Read `maker-calendar-handoff.md` and `sources.yaml` for adapter types and trust conventions.
- 2026-08-05 — Status-probed 6 candidate endpoints with `curl -w '%{http_code} %{content_type} %{size_download}'`: `luma.com/noisebridge`, `api.lu.ma` with the slug, the older-file gCal ID, Meetup RSS, `wiki/Events`, `noisebridge.today`.
- 2026-08-05 — **noisebridge.today: fetched the body, found a domain-parking page** ("This domain may be for sale", `abovedomains.com` for-sale script). The site is gone.
- 2026-08-05 — **Confirmed the gCal ID from the older file resolves** (200, `text/calendar`, 12,748 B, `X-WR-CALNAME:Noisebridge Daily`, 28 VEVENTs) — then parsed it and found max `DTSTART` 2024-01-19, max `LAST-MODIFIED` 2024-06-13, and 5 RRULEs with no `UNTIL`. Re-fetched `full.ics` to rule out a windowed export; byte-identical. Classified as a fabricating feed, not a working one.
- 2026-08-05 — Pulled `luma.com/noisebridge` HTML with a browser UA and grepped for `api_id`, recovering `cal-0aMKsxMfZ73gRpI`. Fetched `api.lu.ma/ics/get?entity=calendar&id=cal-0aMKsxMfZ73gRpI` → 200 `text/calendar`, 46 VEVENTs; counted only 1 with `DTSTART >= 20260805`. Confirmed `luma.com/noisebridge/ics` and the slug-based api.lu.ma call are both wrong.
- 2026-08-05 — Fetched Meetup's official RSS: 200, `application/rss+xml`, 10 items, `lastBuildDate` 2026-08-05. Enumerated every XML tag in the document to confirm there is **no event-date element**. Did not fetch any rendered Meetup page.
- 2026-08-05 — Located the MediaWiki API at `/api.php` (`/w/api.php` and `/wiki/api.php` both 404); siteinfo shows MediaWiki 1.39.13, site timezone `America/Los_Angeles`. Found `Events` is a 29-char redirect; pulled `Category:Events` wikitext (41 KB) and transcribed the full standing schedule from the wikitable rows.
- 2026-08-05 — Grepped the wiki homepage for `iframe`/embed URLs: found two gCals — the base64-encoded `src` decoding to the same `6a2a8ad7…` ID (confirming provenance), and `v4694n2t0jmpg2i9i2fck7uiuq2oo8f7@import.calendar.google.com`, whose ICS 404s and which the wiki already strikes through.
- 2026-08-05 — Checked `/feed/`, `/rss`, `/blog/feed/` — all 200 but md5-identical to the homepage (soft 404). No blog exists.
- 2026-08-05 — Verified `Special:RecentChanges?feed=rss` (200, 22 items, edits dated 2026-08-04) and `feed=atom`.
- 2026-08-05 — Social sweep: found `sfba.social/@Noisebridge` + working `.rss` (dormant since 2022); ruled out `mastodon.social` and `hackaday.social`; three Bluesky handle guesses all 404.
- 2026-08-05 — Contacts from `/wiki/Contacts`, `/wiki/Mailing_list`, `/wiki/Hosting_an_Event`. Discovered `lists.noisebridge.net` returns "Mailman list archives currently offline" (39 B) for the root and both listinfo pages, and that `discuss.noisebridge.info` now presents an `autothat.com` certificate — the forum is gone.
- 2026-08-05 — Read `robots.txt`: `ClaudeBot` and `Amazonbot` fully disallowed; `/wiki/86` disallowed; no general crawl ban.
- 2026-08-05 — Operating status cross-checked via search (space active at 272 Capp since 2020, ongoing rent/finance pressure) and corroborated by live wiki edits to `Finances/Rent` on 2026-08-04 and a `5MoF/2026/07 16` page.
