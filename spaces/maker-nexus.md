# Maker Nexus

**ID:** `maker-nexus` · **City:** Sunnyvale · **Region:** south-bay
**Address:** 1330 Orleans Dr, Sunnyvale, CA 94089
**Website:** https://www.makernexus.org/
**Status:** active — open 10:00am–10:00pm 7 days/week; staff page current, class calendar written to today (2026-08-05).
**Last researched:** 2026-08-05

## Summary

Maker Nexus is a 501(c)(3) nonprofit makerspace (EIN 82-3925333, founded 2018) in a 28,000 sq ft
building in Sunnyvale, with woodshop, metal shop, textiles, laser, CNC, 3D printing, glass and
electronics areas. Nearly all of its programming is paid instructional classes and equipment
certification trainings ("Equipment Training" safety checkouts), sold through **Amilia**, plus a
smaller set of free community events and member-run meetups (Board Game Night, 3D Printing
Thursdays, New Member Orientation).

Volume is roughly **110–135 event instances per month**, essentially all of them classes. The
publishing horizon is short: as of 2026-08-05 there are 171 future instances, but 112 of them fall
in August, 43 in September and 16 in October — Maker Nexus publishes about 4–8 weeks ahead and
backfills. This is the highest class volume of any space researched so far, and the feed is
dominated by repeated equipment-training sessions (76 distinct titles across 171 future
instances). **Recommend splitting classes into a separate feed, or filtering out
`(Equipment Training)` titles from the merged calendar**, or Maker Nexus will drown every other
space.

**Tier A confirmed.** The embedded calendar on `/classes` is a public Google Calendar, and there is
additionally an undocumented public JSON cache for community events. No browser or LLM needed.

## Verified feeds

Only sources you personally fetched and confirmed return real events.

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `gcal_ics` | `https://calendar.google.com/calendar/ical/c_dd13e6622c96fb917233442f8d9b9fc23848858063c36902f15d077b772bbd82%40group.calendar.google.com/public/basic.ics` | 200 | `text/calendar; charset=utf-8` | **3645 VEVENT total, 171 in the future** (2026-08-05 → 2026-10-31); archive back to 2023-12-10 | `X-WR-CALNAME: Amilia Published Classes`. **The primary source and a superset** — it carries both paid classes and the free community events (e.g. 18 × "Board Game Night"). **11 MB payload**; 0 RRULEs (all instances pre-expanded); every VEVENT has `LOCATION`; `DESCRIPTION` is HTML containing instructor, ages, capacity, prerequisites and an Amilia signup link. Filter to the horizon on parse. Verified with a negative control: a bogus calendar ID returns 404, so this 200 is genuine. |
| `json` (custom) | `https://storage.googleapis.com/makernexus_amilia_activities_cache/events.json` | 200 | `application/json` | **30 community events**, 2026-08-06 → 2026-10-22 | Public GCS object powering `/community-events`. **`Last-Modified: Wed, 05 Aug 2026 20:19:20 GMT` — regenerated today, actively maintained.** Far richer than the ICS: `Id`, `Url` (Amilia deep link), `Name`, `Description`, `CategoryName`, `SubCategoryName`, `Price`, `SpotsRemaining`, `MaxAttendance`, `StartDate`/`EndDate` (ISO-8601 **with -07:00 offset**), and a `Schedules[]` recurrence block. Forward-looking only, no history. Keys are `YYYY-MM-DD_<amiliaId>` — a ready-made stable UID. Community events only (`ProgramName: "Community Events"`), not the paid class catalog. |
| `llm_html` / `jsonld`-style HTML | `https://amilia.makernexuswiki.com/www/amiliagetclasseswebembed.php` | 200 | `text/html` | **63 sessions across 20 days**, 2026-08-06 → 2026-08-27 | The iframe behind `/classes-week-view`. A custom PHP renderer Maker Nexus runs on their wiki host. Structure is regular and parseable without a model: `<p class="dayofweek">YYYY-MM-DD  (Dayname)</p>` headers and `<p class="sessiontitle">Title <br> at HH:MM AM/PM</p>` cards, each linking to `app.amilia.com/store/en/makernexus/shop/activities/<id>`. Only ~3 weeks deep, so strictly worse coverage than the gcal — useful only as a cross-check that the gcal has not gone stale. Ignores `?format=json`/`?json=1` (always returns the same HTML). |

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| `json` | `https://storage.googleapis.com/makernexus_amilia_activities_cache/<name>.json` — probe for more objects | Three objects confirmed to exist in this bucket by name-guessing (`events.json`, `activities.json`, `camps.json`). There may be others (e.g. a per-season classes cache) that would give the class catalog in JSON instead of ICS. | `storage.objects.list` is denied anonymously (401), so the bucket cannot be enumerated. Ask Maker Nexus directly for the object names, or read the JS on a Squarespace page that renders classes. Probed and 404'd: `classes.json`, `class.json`, `sessions.json`, `calendar.json`, `activities_cache.json`, `all.json`, `index.json`, `youth.json`. |
| — | Amilia partner/public API for org `makernexus` | The whole operation runs on Amilia; a supported read API would be the cleanest possible source. | `https://app.amilia.com/api/v3/store/makernexus/activities` returns **401 JSON** — the endpoint shape is right but it is auth-gated. Maker Nexus staff could issue a key, or confirm no public tier exists. Worth one line in the outreach email. |
| — | A second Google Calendar for member-only or shop-closure events | A space this size often splits calendars. | Only **one** calendar ID appears anywhere on the site. I checked `/classes`, `/classes-week-view`, `/community-events`, `/youth-programs`, `/reserve-a-room`, `/hmoorengarvey-weekly-view-page` for `iframe src`, raw `@group.calendar.google.com` IDs, and base64 `src=` embed params. Ask staff whether a members-only calendar exists. |

## Dead ends

- **Squarespace `?format=json` / `?format=ical` / `?format=rss`** — checked on `/classes`,
  `/classes-week-view`, `/community-events`. `?format=json` returns **200 `application/json`** but
  it is the page-render blob, not events: `collection.typeName = "page"`, `collection.type = 10`,
  `items: []`. `?format=ical` returns **200 but `text/html`** — it is the ordinary HTML page, *not*
  an iCalendar document; wiring this up would silently produce zero events. `?format=rss` returns
  **400** with the body `Unknown response format for page type`. **None of these pages is a
  Squarespace Events collection**, so the format-suffix trick does not apply here. Also tried
  `/events` (301), `/events-1` (404), `/calendar` (404).
- **JSON-LD `schema.org/Event`** — the pages do carry `<script type="application/ld+json">`, but
  only `@type: WebSite` and `@type: LocalBusiness`. No `Event` objects anywhere. Nothing for the
  `jsonld` adapter.
- **ActivityHero** — `activityhero.com/biz/maker-nexus` is live (200) and the provider is real, but
  their own API says there is nothing to collect. `https://www.activityhero.com/api/v1/providers/62898`
  (200, `application/json`, undocumented but unauthenticated) returns
  **`"has_upcoming_sessions": false`**, alongside `"published": true`, `"out_of_business": false`,
  `"updated_at": "2026-07-25"`, 94 reviews, 5.0 rating. The page lists ~100 historical activity
  *names* (youth camps, after-school) with no attached session dates, and its JSON-LD is only
  `LocalBusiness` + `FAQPage`. **The claim that ActivityHero carries youth programs not on the main
  list is stale** — it may have been true in past summers, but there is nothing bookable there now.
  Re-check in spring when summer camps go on sale; do not wire it up today.
- **`camps.json` and `activities.json`** in the GCS bucket — both fetch 200 but are **stale and
  dateless**. `activities.json` (50 records, `Last-Modified: 2025-12-19`) lists programs
  "Autumn 2025" and "Winter–Spring 2026"; `camps.json` (14 records, `Last-Modified: 2025-07-25`)
  is "Summer 2025". Both are *course catalog templates* keyed by course name with a `NextClassDate`
  and a nested `Sessions` blob, not schedulable instances — and they have no `StartDate` field at
  all. Do not use as event sources.
- **Meetup** — `https://www.meetup.com/maker-nexus/events/rss/` genuinely works (200,
  `application/rss+xml`, 10 items, group "Maker Nexus Makerspace"), but it is **not usable as an
  event feed**: RSS items carry only `title`, `link`, `guid`, `description` and a `pubDate` that is
  the *posting* time, not the event time. There is no event start date in the feed at all. The ten
  items are also all duplicates of community events already in the Google Calendar (3D Printing
  Thursdays, Board Game Night, New Member Orientation). Consistent with the project's standing
  decision to exclude Meetup. (`meetup.com/makernexus` → 404; the correct slug is `maker-nexus`.)
- **Mastodon / Bluesky** — no account found. `https://sfba.social/@makernexus.rss` → 404,
  `https://bsky.app/profile/makernexus.bsky.social/rss` → 404, and neither platform is linked
  anywhere on the site. Maker Nexus is on legacy social only.
- **Sawyer, Bookwhen, Ticket Tailor, Acuity, Punchpass, Eventbrite, Luma** — searched the HTML of
  `/classes`, `/classes-week-view` and `/community-events` for all of these. Zero references. The
  booking stack is Amilia and nothing else.
- **GCS bucket enumeration** — `https://storage.googleapis.com/storage/v1/b/makernexus_amilia_activities_cache/o`
  returns **401**: anonymous callers lack `storage.objects.list`. Individual objects are public;
  the listing is not. Object names must be guessed or obtained from staff.
- **`amilia.makernexuswiki.com/www/`** directory index → **403 Forbidden**. Only the one known
  `.php` file is reachable.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Instagram | `@MakerNexus` | https://www.instagram.com/MakerNexus/ | none | link present in site footer | Most active channel by appearance; best DM target. |
| Facebook | `MakerNexus` | https://www.facebook.com/MakerNexus | none | link in footer, and confirmed as `fb_url` in the ActivityHero provider API | — |
| X / Twitter | `@maker_nexus` | https://twitter.com/maker_nexus | none | link in footer | ActivityHero record has `twitter_url: ""`, suggesting it is not actively maintained. |
| Meetup | `maker-nexus` | https://www.meetup.com/maker-nexus/ | `…/events/rss/` (200, real RSS) | fetched | Feed is real but carries no event dates — see Dead ends. |
| Mailchimp newsletter | list `50a269403c18e565ad3fe9b50` | https://www.makernexus.org/subscribe | none | signup form found in page source (`makernexus.us17.list-manage.com`) | **Human channel, not a feed.** |
| Discord / Slack | — | — | — | — | **None found.** No Discord or Slack invite appears on any Maker Nexus page. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| General | — | email | `info@makernexus.org` | site footer, `/contactus` |
| General | — | phone | (408) 685-2500 | site footer |
| General | — | contact form | https://www.makernexus.org/contactus | site nav |
| **Business Development and Events Manager** | **Jen Harte** | email | `jen.harte@makernexus.org` | `/our-team` |
| **Education Manager** | **Sarah W. Kramer** | email | `sarah.kramer@makernexus.org` | `/our-team` |
| Youth Programs Manager | Myles Barman | email | `myles.barman@makernexus.org` | `/our-team` |
| Co-Executive Director | Kelly Yamanishi | email | `kelly.yamanishi@makernexus.org` | `/our-team` |
| Co-Executive Director | Regina Sakols | email | `regina.sakols@makernexus.org` | `/our-team` |
| Operations Director | Robin Salafia | email | `robin.salafia@makernexus.org` | `/our-team` |
| Woodshop Manager | Kyle Geenen | email | `kyle.geenen@makernexus.org` | `/our-team` |
| Metal Shop Manager | Ginger LaVelle | email | `ginger.lavelle@makernexus.org` | `/our-team` |
| Makerspace Specialist | Haley Moore | email | `haley.moore@makernexus.org` | `/our-team` |
| Jobs | — | email | `jobs@makernexus.org` | `/jobs` |
| Physical | — | address | 1330 Orleans Dr, Sunnyvale, CA 94089 | site footer |

**Best outreach path:** Email **Jen Harte (`jen.harte@makernexus.org`), Business Development and
Events Manager** — she owns events and partnerships, which is exactly this ask — and cc **Sarah
Kramer (`sarah.kramer@makernexus.org`), Education Manager**, since the overwhelming majority of the
feed volume is her class schedule. Lead with the fact that their calendar already works and is
being consumed correctly, then ask two specific questions: (1) is there a members-only or
shop-closure calendar not linked from the public site, and (2) do they have or can they get an
Amilia API key, since `app.amilia.com/api/v3/store/makernexus/activities` returns 401 rather than
404. Worth mentioning that they already publish a clean public JSON cache at
`storage.googleapis.com/makernexus_amilia_activities_cache/events.json` — they may not realize how
useful that is, and asking them to keep it stable costs them nothing.

## Recommended `sources.yaml` entry

> **Note:** this uses an adapter value `json` that does not yet exist in the registry's adapter list
> (`ics`, `gcal_ics`, `tribe_rest`, `jsonld`, `nextdata`, `llm_html`). The `events.json` cache is a
> plain JSON document with a bespoke shape, so it needs either a small dedicated adapter or a generic
> "JSON + field mapping" adapter. Add the adapter name to the comment block at the top of
> `sources.yaml` when wiring this up.

```yaml
  - id: maker-nexus
    name: Maker Nexus
    city: Sunnyvale
    region: south-bay
    url: https://www.makernexus.org/
    sources:
      - adapter: gcal_ics
        calendar_id: c_dd13e6622c96fb917233442f8d9b9fc23848858063c36902f15d077b772bbd82@group.calendar.google.com
        label: amilia-published-classes
        trust: 100
        verified: true
        notes: >
          Verified 2026-08-05: 200 text/calendar, 3645 VEVENTs, 171 in the future.
          Superset - carries both paid classes and free community events. 11 MB
          payload with history back to 2023-12; clip to the horizon on parse. No
          RRULEs, all instances pre-expanded. DESCRIPTION is HTML (instructor,
          ages, capacity, Amilia signup link) - strip tags before emitting.
        filters:
          # ~110-135 instances/month, mostly repeated equipment-safety checkouts.
          # Without this Maker Nexus swamps every other space in the merged feed.
          title_excludes: ["(Equipment Training)"]

      - adapter: json
        url: https://storage.googleapis.com/makernexus_amilia_activities_cache/events.json
        label: amilia-community-events-cache
        trust: 90
        verified: true
        notes: >
          Verified 2026-08-05: 200 application/json, 30 forward-looking community
          events, Last-Modified same day. Dict keyed "YYYY-MM-DD_<amiliaId>" - use
          the key as the stable UID. Fields: Name, Description, Url, CategoryName,
          Price, SpotsRemaining, StartDate/EndDate (ISO-8601 with -07:00 offset).
          Richer than the ICS for the same events (price + capacity), so let it win
          the dedupe merge on those fields; the gcal still wins on coverage.

      - adapter: llm_html
        url: https://amilia.makernexuswiki.com/www/amiliagetclasseswebembed.php
        label: classes-week-view-embed
        trust: 40
        verified: true
        enabled: false
        notes: >
          The iframe behind /classes-week-view. Verified 200 text/html, 63 sessions
          over 20 days. Regular markup (p.dayofweek = "YYYY-MM-DD (Dayname)",
          p.sessiontitle = "Title <br> at HH:MM AM/PM") so it is parseable without a
          model. Only ~3 weeks deep - strictly worse coverage than the gcal. Keep
          disabled; use only to cross-check that the gcal has not gone stale.
```

## Discovery: other spaces found

Mined from `makernexuswiki.com`, a MediaWiki run by Maker Nexus. The directory lives at
**https://makernexuswiki.com/wiki/Maker_Spaces_in_the_US_and_Canada** (the pages
"Maker Spaces In San Francisco area", "Maker Space SFBA" and "Maker Spaces in Western US" all
redirect there), with an index at **https://makernexuswiki.com/wiki/Maker_Spaces** and a
`Category:Makerspace` reachable through the MediaWiki API that surfaces spaces not linked from the
main list. Everything already in `sources.yaml`, including the commented-out leads block, is
excluded below.

**Freshness warning:** most of the "defunct" flags on the wiki come from a single maintenance pass
dated 2024-03-21, and several individual space pages have not been touched since 2017. Verify
independently before adding any of these.

**Likely worth pursuing (live):**

| Name | City | URL | Notes |
|---|---|---|---|
| The Compound Gallery | Emeryville | http://thecompoundgallery.com | Best of the batch. Screen printing, etching, letterpress, ceramics, metal shop, blacksmithing, wood shop, plus an Art Lab with 3D printers and laser/vinyl cutters. Likely a real class catalog. |
| Girls Garage | Berkeley | http://girlsgarage.org | Design and construction school for girls and gender-expansive youth ages 9–18. Program-based, so probably a term schedule rather than a calendar. |
| Robot Garden | Livermore | https://www.robotgarden.org/about-us/ | East Bay (far east). Live. |
| Queerious Labs | San Francisco | https://queeriouslabs.com/ | Experimental art and technology community workshop. |
| Box Shop SF | San Francisco (Bayview/Hunters Point) | http://boxshopsf.org/ | Studio collective in shipping containers; sheet metal and sculpture, CNC plasma, lathe, mill. Studio lease required to use equipment (~$300/mo), so may run few public events. |
| Department of Spontaneous Combustion | Oakland | http://spontaneousfire.com/ | Fire-arts / metal collective. TIG, MIG, bandsaws. Open to the public Tuesdays, 24/7 for members — a standing weekly event worth capturing. |
| Lower 48 Woodshop | Oakland | http://www.lower48.org/ | Small woodshop inside the NIMBY complex. |
| Idea Fab Labs | Santa Cruz | https://santacruz.ideafablabs.com/ | Listed under "Greater Bay Area"; Santa Cruz is outside the current four regions — decide whether it is in scope. |
| Paw Print Prototyping | South Bay | https://pawprintprototyping.org/contact/ | Described on the wiki only as "a hackerspace"; no city given. Needs a look. |
| Santa Clara Adult Education woodshop | Santa Clara | https://santaclaraadulted.asapconnected.com/ | Community-college wood-only shop with open hours (Fri 6–10pm, Sat 11am–9pm), ~$150–187/mo. Took over after the Sawdust Shop closed. Runs on ASAP Connected, which may expose a course feed. |
| South San Francisco Public Library (LibLab) | South San Francisco | http://www.ssf.net/departments/library/services/liblab-makerspace | Free 3D printers (reservable online), Othermill, sewing, Arduino/Mindstorms/Cubelets. **Wiki URL is stale** — redirects to ssfca.gov and 404s; find the current path. |
| Foster City Library makerspace | Foster City | http://www.smcl.org | ~660 sq ft, craft-oriented. Wiki page is a stale concept-phase town-hall report. Status unverified. |
| Hayward Techies | Hayward | https://haywardtechies.club/ | **Not an operating space** — a meetup group trying to start one. Track only as a lead. |

**Confirmed or probably defunct — do not chase:**

| Name | City | URL | Notes |
|---|---|---|---|
| TechShop / TheShop.build | San Jose, SF, Menlo Park, Redwood City | https://makernexuswiki.com/wiki/TechShop | Closed 2017–2019. The wiki keeps a large archive of the closure and the Rasure litigation; Maker Nexus was founded by ex-TechShop members. Historical only. |
| ManyLabs | San Francisco | http://www.manylabs.org/ | Domain no longer resolves. |
| T8 Fab | Treasure Island, SF | http://www.t8fab.com/ | Wiki note 2024-03-21 "web site is gone"; domain no longer resolves. |
| Sheet Metal Alchemist (SMA Events) | Oakland (1960 Mandela Pkwy) | http://www.sma.events | Wiki note 2024-03-21 "web site is no longer up"; domain now resolves to an unrelated travel site. |
| FabLab SF | San Francisco | http://fablabsf.org/ | Wiki note 2024-03-21 "returns a server error"; still errors. The wiki maintainer never managed to find their address. |
| Studio for Metropolitan Craft | San Francisco | MetropolitanCraft.Etsy.com | Not a space — a 2017 offer from one maker to share a Trotec laser. Stale. |

**Not spaces, but useful for discovery:** the same page lists Bay Area maker groups — Cupertinker
(`meetup.com/Cupertinker-Space`), Techie/Maker Families (`meetup.com/techiefamilies/`), HomeBrew
Robotics (`groups.google.com/group/hbrobotics`), Makers San Jose (`meetup.com/TechshopSJ/`), and the
**Bay Area Consortium of Hackerspaces** (`wiki.hackerspaces.org/Bay_Area_Consortium_of_Hackerspaces`).
`https://makernexuswiki.com/wiki/Maker_Spaces` also points at an interactive Google map
(`https://maps.app.goo.gl/goaAddj1MVvbP4yr9`) and `https://wiki.hackerspaces.org/List_of_Hacker_Spaces`;
the wiki itself notes these maps each carry different data, so they are worth cross-referencing.

## Research log

- 2026-08-05 — Fetched `makernexus.org/classes`, `/classes-week-view`, `/community-events` as raw
  HTML with a browser UA (200, ~175–338 KB each) and grepped the source for `iframe src`, raw
  `@group.calendar.google.com` IDs, and base64 `src=` embed parameters.
- 2026-08-05 — **Confirmed the calendar ID.** `/classes` carries a single
  `calendar.google.com/calendar/embed` iframe whose `src` param base64-decodes to
  `c_dd13e6622c96fb917233442f8d9b9fc23848858063c36902f15d077b772bbd82@group.calendar.google.com`,
  matching the ID from the older file. Fetched `…/public/basic.ics` → 200 `text/calendar`, 11 MB,
  3645 VEVENTs, `X-WR-CALNAME: Amilia Published Classes`. Ran a negative control against a bogus
  calendar ID (404) to prove the 200 was not a generic catch-all. Counted future events by month
  and confirmed the calendar contains community events as well as classes (18 × Board Game Night).
  `public/full.ics` returns the identical payload.
- 2026-08-05 — Searched the other two pages for embeds and found **two sources not previously
  known**: `/classes-week-view` iframes a custom PHP renderer at
  `amilia.makernexuswiki.com/www/amiliagetclasseswebembed.php`, and `/community-events` reads
  `storage.googleapis.com/makernexus_amilia_activities_cache/events.json`. Fetched and validated
  both. Probed the GCS bucket by name for further objects: found `activities.json` and `camps.json`
  (both stale and dateless), 404'd on seven other guesses; bucket listing is 401.
- 2026-08-05 — Ran the Squarespace format-suffix checks (`?format=json|ical|rss`) across all three
  pages plus `/events`, `/events-1`, `/calendar`. Confirmed by parsing the JSON that these are
  `type: page` collections with `items: []`, that `?format=ical` returns HTML rather than
  iCalendar, and that `?format=rss` 400s. Documented as a dead end so the next person does not
  wire up a silently-empty feed.
- 2026-08-05 — Checked JSON-LD on all three pages (only `WebSite` / `LocalBusiness`, no `Event`);
  grepped for Sawyer / Bookwhen / Ticket Tailor / Acuity / Punchpass / Eventbrite / Luma (zero
  hits); tested Mastodon and Bluesky RSS guesses (both 404).
- 2026-08-05 — ActivityHero: fetched the biz page (200) and found an undocumented unauthenticated
  provider endpoint at `/api/v1/providers/62898` returning `has_upcoming_sessions: false`. Recorded
  as a dead end for now with a note to re-check before summer camp season.
- 2026-08-05 — Meetup: fetched the official RSS endpoint `meetup.com/maker-nexus/events/rss/`
  (200, real feed, 10 items) and parsed it with ElementTree to check the item fields. No event
  start date is present in any item, only a posting `pubDate`. No rendered Meetup pages were
  scraped.
- 2026-08-05 — Probed Amilia for a public read API: `app.amilia.com/api/v3/store/makernexus/activities`
  → 401 (auth-gated, not absent); `api.amilia.com` does not resolve.
- 2026-08-05 — **Bonus task:** mined `makernexuswiki.com` (a MediaWiki) for its Bay Area directory,
  via `Maker_Spaces_in_the_US_and_Canada`, the `Maker_Spaces` index, and `Category:Makerspace`
  through the MediaWiki API. Found 19 Bay Area spaces not already in `sources.yaml` (13 apparently
  live, 6 defunct or not real spaces), plus pointers to the Bay Area Consortium of Hackerspaces and
  two makerspace maps. Note that the wiki's Bay Area section otherwise matches the existing list
  closely, and several entries are duplicated within the page.
- 2026-08-05 — Pulled contacts from `/contactus`, `/our-team`, `/aboutus`, `/visit-maker-nexus` and
  `/jobs`: nine named staff with roles and individual addresses, plus `info@`, `jobs@` and a phone
  number. Confirmed operating status and hours (10:00–10:00 daily) from `/visit-maker-nexus`, and
  cross-checked `out_of_business: false` / `published: true` via the ActivityHero provider record.
