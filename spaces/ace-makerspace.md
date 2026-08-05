# Ace Makerspace

**ID:** `ace-makerspace` · **City:** Oakland · **Region:** east-bay
**Address:** 6050 Lowell Street, Suite #214, Oakland, CA 94608
**Website:** https://www.acemakerspace.org/
**Status:** active — 92 upcoming events on the calendar as of 2026-08-05, events running today; no closure or relocation signals anywhere on the site.
**Last researched:** 2026-08-05

## Summary

Ace Makerspace (formerly Ace Monster Toys, "AMT") is a 501(c)(3) nonprofit member-supported makerspace founded in 2010, occupying several suites at 6050 Lowell St in Oakland. It runs a heavy, highly regular class and open-shop schedule across ten programme areas — textiles, laser, woodworking/workshop, 3D printing, electronics, metal machining, art, social, outreach and operations meetings — plus tours and new member orientations. Volume is roughly 90-130 events over the next 120 days (92 upcoming via REST, 126 VEVENTs in the ICS export including past-window entries), which makes this the single richest and cleanest source in the registry. The site runs WordPress with The Events Calendar (Tribe) 6.17.0, so it exposes a full REST API, ICS exports at list/category/venue scope, an events RSS feed, and JSON-LD — all working.

## Verified feeds

Only sources I personally fetched and confirmed return real events.

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `tribe_rest` | `https://www.acemakerspace.org/wp-json/tribe/events/v1/events?start_date=2026-08-05&per_page=50` | 200 | `application/json` | **92 upcoming**, 50/page, `total_pages: 2` | **Best source.** Full field set: `cost`, `cost_details`, `categories`, `venue` (with address), `organizer`, `ticketed`, `is_virtual`, `utc_start_date`, `timezone`, `image`, `description`. Paginate on `next_rest_url`. Defaults `end_date` to +2 years. Cost strings arrive HTML-escaped (`&#036;20.00`) — unescape. |
| `ics` | `https://www.acemakerspace.org/calendar/list/?ical=1` | 200 | `text/calendar` | **126 VEVENTs** | Cleanest ICS URL. `PRODID:-//Ace Makerspace - ECPv6.17.0//NONSGML v1.0//EN`, `X-WR-CALNAME:Ace Makerspace`. Proper `DTSTART;TZID=America/Los_Angeles`, stable UIDs (`{postid}-{start}-{end}@www.acemakerspace.org`), `CATEGORIES` present. |
| `ics` | `https://www.acemakerspace.org/?post_type=tribe_events&ical=1&eventDisplay=list&calendarVersion=3` | 200 | `text/calendar` | **126 VEVENTs** | The URL from the handoff doc. Byte-identical to the `/calendar/list/` form above — pick one, not both. |
| `ics` (category) | `https://www.acemakerspace.org/calendar/category/{slug}/?ical=1` | 200 | `text/calendar` | textiles 15, workshop 16, laser 11, 3d-printing 2 | **Category-scoped ICS feeds do exist and are correctly filtered** — every `CATEGORIES` line in each feed matched the requested category. Ten slugs (see below). A bogus slug correctly 404s, so these are real filters, not silent fallbacks. |
| `ics` (venue) | `https://www.acemakerspace.org/venue/ace-makerspace-suite-214/?ical=1` | 200 | `text/calendar` | **108 VEVENTs** | Venue-scoped ICS also works. Useful only if you ever want to exclude off-site events. |
| `tribe_rest` | `https://www.acemakerspace.org/wp-json/tribe/events/v1/categories` | 200 | `application/json` | 10 categories | `3d-printing-events` (52), `art-events` (57), `electronics-events` (40), `laser-events` (162), `metal-machining-events` (15), `operations-and-meetings` (54), `outreach-events` (329), `social-events` (104), `textiles-events` (366), `workshop-events` (419). Counts are all-time, not upcoming. |
| `tribe_rest` | `https://www.acemakerspace.org/wp-json/tribe/events/v1/venues` | 200 | `application/json` | 8 venues | Suites 214, 206, 113 at 6050 Lowell St; plus `Ace Virtual Event`, Jack London Square, Henry J Kaiser Center, Oakland First Friday, 2111 Franklin St. Confirms events happen off-site too. |
| `ics` (RSS variant) | `https://www.acemakerspace.org/?post_type=tribe_events&feed=rss2` | 200 | `application/rss+xml` | 10 items, real upcoming events | The canonical Tribe events RSS. Capped at 10 items by WordPress `posts_per_rss` — a change signal, not full coverage. Item links are real `/event/{slug}` permalinks. |
| `jsonld` | `https://www.acemakerspace.org/calendar/` | 200 | `text/html` | 12 `schema.org/Event` objects | One `<script type="application/ld+json">` block containing a 12-element array of `Event`, with `startDate` (`2026-08-05T18:00:00-07:00`), `name`, `location.name`. Only covers the current list-view page. Redundant given REST works. |
| `ics` (external) | `https://www.meetup.com/ace-makerspace/events/rss/` | 200 | `application/rss+xml` | 10 items, real upcoming, `lastBuildDate` today | **Meetup's official published RSS endpoint still works** (contradicting the handoff's assumption). `ace-monster-toys` is an alias returning the identical feed. Content duplicates their own calendar exactly, and their Meetup page says "do not RSVP here" — so this is a cross-check, not a source. |

Human-channel feeds (real, but not event data):

| Adapter | URL | HTTP | Content-Type | Items | Notes |
|---|---|---|---|---|---|
| RSS | `https://www.acemakerspace.org/feed/` | 200 | `application/rss+xml` | 10 posts, newest 2026-05-01 | Blog, not events. Low cadence (~2 posts in 2026). Change signal only. `/rss` is a byte-identical alias. |
| RSS | `https://www.youtube.com/feeds/videos.xml?channel_id=UCpmLqLFDGX2FVoCS_utTHwA` | 200 | `text/xml` | 15 entries | Shop tutorials and tool demos. Not events. |

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| `ics` (category) | The 6 category slugs I did not fetch: `art-events`, `electronics-events`, `metal-machining-events`, `operations-and-meetings`, `outreach-events`, `social-events` | The other 4 slugs all worked and correctly filtered; the pattern is uniform TEC behaviour | `curl -sS "https://www.acemakerspace.org/calendar/category/art-events/?ical=1" \| grep -c BEGIN:VEVENT` and confirm `CATEGORIES` lines all match |
| `tribe_rest` | `/wp-json/tribe/events/v1/events?categories=laser-events` | TEC v1 REST documents a `categories` filter param; would let you pull scoped JSON instead of scoped ICS | Compare returned `total` against the category ICS VEVENT count |
| — | Whether `total: 92` is capped by the default `end_date` of +2 years | REST defaulted `end_date=2028-08-05`; the horizon is 120 days so this is almost certainly not binding | Pass an explicit `end_date` 120 days out and compare counts |
| — | Recurring-event representation | The ICS emits expanded instances (no `RRULE` seen in sampled events), and there is a `/series/` URL pattern on the site (`/series/textiles-open-studio-with-team-ace/`) | Grep the full ICS for `RRULE:` — if absent, no recurrence expansion needed at all |

## Dead ends

Things I checked that do **not** work. Two of these are silent-failure traps, which is exactly the failure mode this project cares about.

- **`https://www.acemakerspace.org/calendar/photo/?ical=1`** — the candidate from the older file. Returns **HTTP 200 and a byte-identical copy of the full 126-event list feed** (`cmp` confirms identical). `photo` is not a category — it is one of The Events Calendar's *view* names (list / month / day / photo / map), so this URL is just the full calendar rendered in Photo view. **Do not wire this up believing it is a "photo" category feed.** The real category path is `/calendar/category/{slug}/`.
- **`https://www.acemakerspace.org/calendar/feed/`** — returns **HTTP 404 while serving a 9.8 KB RSS body that contains 10 real event items**. Any fetcher that trusts the body without checking the status will happily parse it; any fetcher that checks the status will drop it. Do not use. The canonical events RSS is `?post_type=tribe_events&feed=rss2`, which returns a proper 200.
- **`https://www.acemakerspace.org/events/feed/`** — HTTP 200 but it is a **comments** feed with an empty `<title>` and **zero items** (706 bytes). The events base slug on this site is `/calendar/`, not `/events/`, so this path resolves to nothing useful.
- **`https://luma.com/acemakers`** — **this is not Ace Makerspace.** The handoff doc lists it as one of their surfaces; that appears to be wrong. The calendar (`cal-E6JYPZ49ws8IHsZ`, display name "AceMakers") exposes a working iCal feed at `https://api.lu.ma/ics/get?entity=calendar&id=cal-E6JYPZ49ws8IHsZ` (HTTP 200, 5 VEVENTs) but every event is an IT-consulting sales talk — "Selling Into Enterprise, as an IT Consultant", "Positioning That Drives Sales", "BUILD THE NEXT GROWTH HACK: GTM Hackathon" — dated Dec 2024 through Feb 2026. No website, Instagram or geo field on the Luma calendar links it to Ace. **Technically a working feed, semantically the wrong organisation. Do not add it.** Remove the Luma reference for Ace from the handoff doc.
- **`https://www.acemakerspace.org/feed.xml`** — HTTP 404.
- **`/rss`** — HTTP 200 but byte-identical to `/feed/` (the blog). Not a separate source.
- **Google Calendar embed** — none. The only `<iframe>` elements on the homepage, calendar, about and contact pages are **Google Maps** embeds. There is no public gCal to derive a `basic.ics` from.
- **Mastodon, Bluesky, Eventbrite, Bookwhen, Ticket Tailor, ActivityHero, Discord, Patreon, TikTok** — no links to any of these anywhere on the site. Speculative probes `https://bsky.app/profile/acemakerspace.bsky.social/rss` and `https://sfba.social/@acemakerspace.rss` both returned 404. Ticketing is handled in-house via WooCommerce (see robots.txt) — there is no third-party ticketing organiser page to parse.
- **Bogus category control test** — `/calendar/category/this-does-not-exist/?ical=1` returns a clean 404 with a zero-byte body, which is what makes the category feeds trustworthy.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Instagram | `@ace_makerspace` | https://www.instagram.com/ace_makerspace/ | none | Profile 200 | Likely the most active channel; best DM target |
| Facebook | `acemakerspace` | https://facebook.com/acemakerspace | none | Profile 200 | Linked from site footer; contact page names FB as a contact route |
| YouTube | `UCpmLqLFDGX2FVoCS_utTHwA` | https://www.youtube.com/channel/UCpmLqLFDGX2FVoCS_utTHwA | `https://www.youtube.com/feeds/videos.xml?channel_id=UCpmLqLFDGX2FVoCS_utTHwA` | **Yes** — 200, 15 entries | Tool tutorials, not events |
| LinkedIn | `acemakerspace` | https://www.linkedin.com/company/acemakerspace | none | Profile 200 | Low activity expected |
| Meetup | `ace-makerspace` | https://www.meetup.com/ace-makerspace/ | `https://www.meetup.com/ace-makerspace/events/rss/` | **Yes** — 200, 10 real events | Alias slug `ace-monster-toys` serves the identical feed. Page says "do not RSVP here" |
| Slack | `acemakerspace` | https://join.slack.com/t/acemakerspace/shared_invite/zt-395k33tx3-SXJ9Xi5mFi3BzEu9Z~sI8Q | n/a | Link present on contact page | Human channel. Contact page: "all Ace members use Slack communication channel heavily," but explicitly advises emailing officers rather than Slack-DMing them |
| Mastodon | — | — | — | Not found | No account discovered |
| Bluesky | — | — | — | Not found | No account discovered |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| General enquiries | — | Email | `info@acemakerspace.org` | `mailto:` link on /contact-us/ |
| Officers & stewards | — | Email | `officers@acemakerspace.org` | /contact-us/ — the page explicitly directs officer/steward enquiries here rather than via Slack |
| Chair, Board of Directors & CEO | Sylvia Gonzalez (she/her) | via `officers@` | no direct address published | /leadership-team/ |
| Board Member & Secretary | Kayla Rakitt (she/her) | via `officers@` | no direct address published | /leadership-team/ |
| Board Member & Treasurer | David R. Perry (he/him) | via `officers@` | no direct address published | /leadership-team/ |
| Board Member | Samantha "Pixie" Piszkiewicz (she/her) | via `officers@` | no direct address published | /leadership-team/ |
| Assistant Treasurer | Jun Zou (she/her) | via `officers@` | no direct address published | /leadership-team/ |
| Contact form / hub | — | Web | https://www.acemakerspace.org/contact-us/ | — |
| Mailing list | — | Mailchimp | `https://acemonstertoys.us11.list-manage.com/subscribe/post?u=978ae6e85d51d7c4091542c43&id=599f3d0d1c` | Embedded signup form on /contact-us/. Note the legacy `acemonstertoys` list domain |
| Community chat | — | Slack | invite link above (stated to expire 2030) | /contact-us/#Slack |
| Physical | — | Mail | 6050 Lowell Street, Suite #214, Oakland, CA 94608 | /contact-us/ |

**Best outreach path:** Email `officers@acemakerspace.org` — the contact page routes steward and officer business there specifically, and with no dedicated events or programming staff listed, the volunteer board is the decision-making body. Address it to Sylvia Gonzalez (Chair & CEO) by name and cc `info@acemakerspace.org`. Because their feeds already work perfectly, the ask is not "please give us a feed" but "we're already ingesting your public ICS/REST correctly, here's the merged calendar, please tell us if you'd like anything changed or excluded" — which is also the strongest possible demo when approaching the spaces that have no feed at all.

## Recommended `sources.yaml` entry

```yaml
  - id: ace-makerspace
    name: Ace Makerspace
    city: Oakland
    region: east-bay
    url: https://www.acemakerspace.org/
    rate_limit_seconds: 10   # robots.txt sets Crawl-delay: 10, above the global default of 2
    sources:
      - adapter: tribe_rest
        url: https://www.acemakerspace.org/wp-json/tribe/events/v1/events
        params: { per_page: 50 }
        label: tribe-rest
        trust: 100
        verified: true
        notes: >
          Confirmed 2026-08-05: HTTP 200, 92 upcoming events over 2 pages.
          Paginate on next_rest_url. Richest payload (cost, categories, venue,
          organizer, ticketed, is_virtual, utc_start_date). Unescape HTML
          entities in `cost` (e.g. "&#036;20.00").
      - adapter: ics
        url: https://www.acemakerspace.org/calendar/list/?ical=1
        label: tribe-ics-list
        trust: 90
        verified: true
        notes: >
          Confirmed 2026-08-05: HTTP 200, text/calendar, 126 VEVENTs.
          Byte-identical to the longer ?post_type=tribe_events&ical=1 form.
          Fallback for the REST feed; do not ingest both as separate sources.
      - adapter: jsonld
        url: https://www.acemakerspace.org/calendar/
        label: calendar-jsonld
        trust: 50
        verified: true
        notes: >
          Confirmed 2026-08-05: 12 schema.org/Event objects in one ld+json
          block. Current-page only. Third-line fallback behind REST and ICS.

# Cross-check only, not ingested (Meetup excluded by project policy; content is a
# verbatim duplicate of the site calendar and the group says "do not RSVP here"):
#   https://www.meetup.com/ace-makerspace/events/rss/   -> 200, 10 real events
#
# Category-scoped ICS, if you ever want per-programme calendars. Verified slugs
# marked (v); pattern is /calendar/category/{slug}/?ical=1
#   3d-printing-events (v)  art-events            electronics-events
#   laser-events (v)        metal-machining-events  operations-and-meetings
#   outreach-events         social-events         textiles-events (v)
#   workshop-events (v)
```

## Research log

- 2026-08-05 — Read `maker-calendar-handoff.md` and `sources.yaml` for adapter conventions and trust semantics.
- 2026-08-05 — Batch `curl -o /dev/null -w '%{http_code} %{content_type}'` across 8 candidate endpoints; all returned 200. Confirmed `tec-api-version: v1` and `tec-api-origin` meta on `/calendar/`, and TEC version 6.17.0 from the ICS `PRODID`.
- 2026-08-05 — Fetched full ICS bodies and counted VEVENTs (126). Ran `cmp` between the documented list feed and the `/calendar/photo/?ical=1` candidate: **byte-identical**, establishing that `photo` is a TEC view name, not a category.
- 2026-08-05 — Pulled `/wp-json/tribe/events/v1/categories` (10 categories with slugs) and `/venues` (8 venues) to derive the real category URL base, which is `/calendar/category/{slug}/`, not `/events/category/`.
- 2026-08-05 — Fetched 4 category ICS feeds and verified every `CATEGORIES` line matched the requested slug. Ran a bogus-slug control (`this-does-not-exist`) which returned 404, proving the filtering is real rather than a silent fallback to the full list. Also verified venue-scoped ICS (108 VEVENTs).
- 2026-08-05 — Paginated the Tribe REST events endpoint: 92 upcoming, `total_pages: 2`, `next_rest_url` present, default `end_date` of +2 years. Dumped the full field list and category/venue histograms.
- 2026-08-05 — Tested five RSS/Atom paths. Found `/calendar/feed/` serves **404-with-a-populated-body** and `/events/feed/` is an empty comments feed; identified `?post_type=tribe_events&feed=rss2` as the canonical 200-returning events RSS (10-item cap). `/rss` is a blog-feed alias; `/feed.xml` 404s.
- 2026-08-05 — Extracted and parsed the `ld+json` block from `/calendar/`: 12 `Event` objects with correct offset-aware `startDate`.
- 2026-08-05 — Grepped all `<iframe src>` on home/calendar/about/contact: **Google Maps only, no Google Calendar**. Grepped sitewide for mastodon/bsky/eventbrite/discord/patreon/tiktok — zero hits. Speculative Bluesky and sfba.social RSS probes both 404.
- 2026-08-05 — Pulled `luma.com/acemakers` `__NEXT_DATA__`, extracted calendar `api_id` `cal-E6JYPZ49ws8IHsZ`, and fetched `https://api.lu.ma/ics/get?entity=calendar&id=...`. Feed works (5 VEVENTs) but all events are IT-consulting sales talks — **wrong organisation**; flagged as a correction to the handoff doc.
- 2026-08-05 — Tested Meetup's official RSS endpoint against 5 candidate slugs (published-feed endpoint only; no page scraping). `ace-makerspace` and `ace-monster-toys` both return 200 with 10 real upcoming events and today's `lastBuildDate`.
- 2026-08-05 — Harvested contact details by grepping raw HTML for `mailto:` and address patterns (WebFetch redacts email addresses, so curl+grep was required): `info@` and `officers@`, Mailchimp list ID, Slack invite. Read `/leadership-team/` for the five named board members and officers, and `/about/` which confirms 501(c)(3), founded 2010, formerly Ace Monster Toys.
- 2026-08-05 — Read `robots.txt`: **`Crawl-delay: 10`**, above the project's default `rate_limit_seconds: 2`. Only WooCommerce and wp-admin paths are disallowed; every feed above is permitted.
