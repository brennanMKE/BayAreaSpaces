# Sudo Room

**ID:** `sudo-room` · **City:** Oakland · **Region:** east-bay
**Address:** Omni Commons, 4799 Shattuck Ave, Oakland, CA 94609 (enter at the back of the building)
**Website:** https://sudoroom.org/
**Status:** active — weekly recurring events are being published for the next 4 months on two independent feeds; wiki last edited 2026-05-31, Mastodon posted 2026-08-02.
**Last researched:** 2026-08-05

## Summary

Sudo Room is a free, volunteer-run, consensus-governed hackerspace occupying part of Omni Commons in North Oakland. It runs a standing weekly schedule — Hardware Hack Night & Fix-it Clinic (Tue), Radio Wednesdays, Software/Electronics Thursday Hack Night, Women & Non-Binary "Hack and Do Whatever" Night (Mon) — plus member meetings, occasional workshops, film/AI/XR project nights, and one-off events like Darkmode. Volume is roughly 15–20 events per month, overwhelmingly recurring. Both of its calendars are genuinely machine-readable, which puts it in Tier A with no scraping required.

**Correction to the handoff brief:** `sudoroom.org/calendar/` contains **zero Google Calendar embeds**. Grep for `calendar.google.com` on that page returns 0 hits. The color codes listed on the page (`#aa0000 - sudo room events`, `#338800 - counter culture labs`, etc.) are **WP-FullCalendar event category colors**, not separate Google Calendars. The heading "Omni Commons General Calendar" sits above an **Airtable** embed, not a gCal iframe. There is no `gcal_ics` adapter to build here.

## Verified feeds

Only sources personally fetched and confirmed on 2026-08-05.

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `ics` | `https://sudoroom.org/?ical=1` | 200 | `text/calendar; charset=utf-8` | 5057 VEVENTs total, range 2012-01-11 → **2058-02-19**; **73 fall in the next 120 days** | The primary and most complete feed. WordPress **Events Manager** (`PRODID:-//wp-events-plugin.com//7.2.3.1//EN`). **8.0 MB response.** See warnings below. |
| `ics` | `https://api.lu.ma/ics/get?entity=calendar&id=cal-bqAGiRcc3VT4oYW` | 200 | `text/calendar; charset=utf-8` | 269 VEVENTs, range 2024-07-16 → 2026-09-29; **19 future** | Luma calendar `lu.ma/sudoroom`, `X-WR-CALNAME:SudoRoom`. Clean, small (146 KB), one `https://luma.com/<slug>` URL per event. Fewer events than the WP feed but better one-off coverage. |
| `rss` | `https://sudoroom.org/events/feed/` | 200 | `application/rss+xml` | 1715 `<item>`s, `<title>Sudo Room - Events</title>`, first items 2026-08-06 → forward | RSS over the same `event` post type. `pubDate` is the **event start**, not the publish date. Useful only as a fallback / change signal — 774 KB and no structured end time or location. |
| `rss` | `https://sudoroom.org/feed/` | 200 | `application/rss+xml` | 10 items, newest 2026-07-16 | Blog posts, not events. Announcement/change signal only. |
| `atom` | `https://sudoroom.org/wiki/Special:RecentChanges?feed=atom` | 200 | `application/xml` | valid Atom, `updated: 2026-08-05T20:25:24Z` | MediaWiki RecentChanges (redirects to `/mediawiki/api.php?action=feedrecentchanges`). Liveness signal only — no events. |
| `rss` | `https://sfba.social/@sudoroom.rss` | 200 | `application/rss+xml` | 20 items, newest **2026-08-02** | Mastodon. Real announcements, most linking to `luma.com/<slug>`. Change signal. |
| `rss` | `https://bsky.app/profile/sudoroom.bsky.social/rss` | 200 | `application/xml` | 17 items, newest 2026-07-18 | Bluesky (resolves to `did:plc:7buslbhyxygifara7bs7kqxr`). Lower cadence than Mastodon. |

### Warnings for the `sudoroom.org/?ical=1` adapter

1. **8 MB per fetch.** Query params are ignored — `&scope=future`, `&limit=200`, and `&category=sudo-room-events` all return the identical 7,967,575-byte body with all 5057 events. You must fetch the whole thing and filter client-side, or cache aggressively with `If-Modified-Since`.
2. **Recurrences are pre-expanded to 2058.** Weekly events are materialized as individual VEVENTs (52/year through 2058), not RRULEs. Hard-cap on the horizon window or you will ingest 30 years of phantom hack nights.
3. **`LOCATION` is stale in both calendars.** Every VEVENT says `Sudo Room, 549 48th St, Oakland, CA, 94609` with `GEO:37.835055;-122.264256` — that is the space's **pre-2014 address**. The Luma feed repeats the same wrong venue record. **Override location to `Omni Commons, 4799 Shattuck Ave, Oakland, CA 94609`** in the adapter. `sudoroom.org/contact/` confirms the real address is 4799 Shattuck.
4. Both feeds carry events that are **not at Sudo Room** — categories include `noisebridge`, `east bay food not bombs`, `oakland museum`, `bike parties`, `events happening elsewhere` (`#84459b`). Some Luma events are at The Box Shop, 951 Hudson Ave, San Francisco. If you don't want cross-posted third-party events, filter on `CATEGORIES` (keep `sudo room events`, `hack night`, `member meeting`, `radio wednesdays`, etc.) or you'll double-count Noisebridge.

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| — | Airtable "Public Calendar" — `https://airtable.com/embed/shrxXjo869sxBzHmr` (view `viwJFaj6nFsS1poak`, table `tblgCW1CuG5eilxrD`) | This is the **Omni Commons house calendar**, embedded under the "Omni Commons General Calendar" heading on `sudoroom.org/calendar/`. It would cover several Omni member collectives at once — the highest-leverage single source at this address. | Base HTML fetches 200 but is a JS shell. `/v0.3/view/shrxXjo869sxBzHmr/readSharedViewData` → **302 to `/login`**. `/v0.3/view/viwJFaj6nFsS1poak/downloadCsv` → **403** behind a PerimeterX bot wall. Needs the signed `accessPolicy` blob + `x-airtable-application-id` header lifted from the embed page, which is undocumented and will break. **Ask Omni's Commons WG to enable the Airtable calendar view's native iCal/ICS sync instead** — Airtable supports this natively and it turns the whole building into one Tier-A feed. |
| `ics` | Counter Culture Labs (also at Omni) | Only event surface linked from `counterculturelabs.org` is `https://www.meetup.com/Counter-Culture-Labs/events/calendar/`. Site is Weebly, no gCal, no JSON-LD, no email in source. | Out of scope for this file; when you research CCL, note that Meetup is their sole surface, which makes the Omni shared-calendar pitch more valuable, not less. |
| `ics` | Omni Commons house feed, once `omnicommons.org/blog/` is repaired | The WordPress install exists (`omnicommons.org/occupy/` returns 200) and the Commons WG maintains a real calendar. | Re-test `https://omnicommons.org/calendar/` and `https://omnicommons.org/blog/feed/` periodically; currently 500. |

## Dead ends

- **Google Calendar embeds — do not exist.** `grep -c 'calendar.google.com' sudoroom.org/calendar/` → **0**. There is no calendar ID to extract and no `gcal_ics` source for this space. The brief's premise was wrong; the colored entries are WP-FullCalendar categories. Remove both `gcal_ics` TODO stubs from `sources.yaml`.
- **`https://omnicommons.org/calendar` and `/calendar/`** → 301 to `https://omnicommons.org/blog/calendar/` → **HTTP 500**. So does `/blog/` and `/blog/feed/`. The Omni WordPress blog is broken as of 2026-08-05. `/occupy/` (the event request page) still serves 200, so it's a per-route failure, not a dead host.
- **`omnicommons.org/wiki/Calendar` is NOT "stale 2014"** — correcting the brief again. It carries delegate-meeting minutes through **2025/08/12** and states in its own text: *"This calendar is for archival purposes only. Our event calendar is live at https://omnicommons.org/calendar!"* It is an archive of governance meetings, not a public events source, and the URL it points at is the one returning 500. Still a dead end for events, but for a different reason than recorded.
- `https://omnicommons.org/feed/`, `/wp-json/`, `/events/feed/` → **404** (host is Apache serving the MediaWiki at root; WordPress lives under `/blog/`).
- **The Events Calendar (Tribe) is not in use.** No `tec-api-version` meta, no `/wp-json/tribe/*`. It's Events Manager instead.
- **No usable WP REST for events.** `sudoroom.org/wp-json/` exposes an `events-manager/v1` namespace but its only route is `/events-manager/v1/uploads`. `wp/v2/event` → **404**; the `event` post type is not REST-exposed. `?ical=1` is the only structured export.
- **No JSON-LD anywhere.** Zero `application/ld+json` blocks on `/`, `/calendar/`, `/contact/`, or `/events/`. No `jsonld` adapter possible.
- `https://sudoroom.org/feed.xml` → 404. `https://sudoroom.org/other-contacts/` → 404 (the sidebar "Other contacts" link is broken; it resolves to `/contact`).
- **`https://www.meetup.com/sudoroom/events/rss/` → 404.** Sudo Room has no Meetup group. (Counter Culture Labs does; Sudo Room does not.)
- `https://sudoroom.org/event-request/` renders *"Unauthorized Access — You do not have the rights to manage this Event."* The public event-submission form is broken for logged-out visitors. Worth mentioning in outreach as a small favor you can flag for them.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Mastodon | `@sudoroom@sfba.social` | https://sfba.social/@sudoroom | `https://sfba.social/@sudoroom.rss` | **yes** — 200, 20 items, newest 2026-08-02 | Most active channel. Posts link to `luma.com/<slug>`. Best DM target. |
| Bluesky | `@sudoroom.bsky.social` | https://bsky.app/profile/sudoroom.bsky.social | `https://bsky.app/profile/sudoroom.bsky.social/rss` | **yes** — 200, 17 items, newest 2026-07-18 | Display name "SudoRoom Hackerspace". |
| Luma | `sudoroom` | https://lu.ma/sudoroom | ICS above (`cal-bqAGiRcc3VT4oYW`) | **yes** | The site calls this the place to go for events. |
| Instagram | `@sudo.room` | https://www.instagram.com/sudo.room/ | none | n/a | No public feed endpoint. |
| YouTube | `@sudoroomHackerspace` | https://www.youtube.com/@sudoroomHackerspace | channel RSS exists in principle | no | Talk recordings, not event listings. |
| Twitter/X | `@sudoroom` | https://twitter.com/sudoroom/ | none | no | Listed on their contact page; assume low activity. |
| GitHub | `sudoroom` | https://github.com/sudoroom | n/a | no | Code, not events. |
| Discord | invite `zmEzQuzCWX` | https://discord.gg/zmEzQuzCWX | none (human channel) | invite resolves 200 | Linked from the wiki. Human channel. |
| IRC | `#sudoroom` on libera.chat | https://sudoroom.org/chat | none (human channel) | n/a | Their own page says: if nobody answers on IRC, email the list instead. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| General | — | email | `info@sudoroom.org` | `sudoroom.org/contact/` (obfuscated as "info [at] sudoroom [dot] org"); also plain-text on `omnicommons.org/wiki/Collectives` |
| Main discussion list (**the decision venue**) | — | Mailman 3 / Postorius | `sudo-discuss@sudoroom.org` — info page: https://sudoroom.org/lists/postorius/lists/sudo-discuss.sudoroom.org/ | Verified 200. *"General discussion list for Sudo Room… archived, published publicly on the web."* Open subscription without an account. |
| List owners | — | email | `sudo-discuss-owner@sudoroom.org` | Same Postorius page |
| Announcements only | — | Mailman 3 | `sudo-announce@sudoroom.org` — https://sudoroom.org/lists/postorius/lists/sudo-announce.sudoroom.org/ | Verified 200 |
| Governance | Board | email | `board@sudoroom.org` | https://sudoroom.org/lists/ index |
| Physical access WG | — | email | `access@sudoroom.org` | Same index |
| Other topic lists | — | email | `artmurmur@`, `biohackers@`, `controllers@`, `copradio@`, `cryptoparty@`, `disasterradio@`, `bigbang-dev@`, `bigbang-user@` — all `@sudoroom.org` (~40+ lists across 5 pages of the index) | https://sudoroom.org/lists/ |
| Consensus meeting | — | in person / calendar | "Member Meeting: second Thursdays" and "Member Meeting: last Sundays" — both appear in the verified ICS, 4 of each in the next 120 days | `sudoroom.org/?ical=1` |
| Luma calendar host | Romy Ilano | Luma | `ORGANIZER;CN="Romy Ilano"` on the Luma VEVENTs | `luma.ics` (public feed metadata) |
| **Omni Commons — Commons Working Group** | — | Mailman list | `commons@lists.omnicommons.org` — https://omnicommons.org/lists/listinfo/commons | Verified 200. *"Working group for managing & scheduling the common areas."* **Owns the Omni calendar and the event request form.** Meets 2nd & 4th Mondays at 5pm. |
| Omni — event bookings | — | Mailman list | https://omnicommons.org/lists/listinfo/booking (*"Event requests are posted to this list"*) | `omnicommons.org/wiki/Commons_Working_Group` |
| Omni — A/V productions | — | Mailman list | https://omnicommons.org/lists/listinfo/productions | Same |
| Omni — general | — | email | `info@omnicommons.org` | `omnicommons.org/wiki/Working_Groups` |
| Omni — communications WG | — | email | `comms@omnicommons.org` (meets Tuesdays 6pm) | Same |
| Omni — building WG | — | email | `building@omnicommons.org` | Same |
| Omni — wiki/calendar helpers | Vicky Knox-Sironi; Jenny; Matt Senate | email | `vknoxsironi@gmail.com`; `jenny@sudomesh.org`; `mattsenate@gmail.com` | Published on `omnicommons.org/wiki/Working_Groups`: *"For help creating correct wiki pages and adding events to the calendar, contact…"* (page last edited 2019 — treat as possibly out of date) |
| Contact form | — | web | https://sudoroom.org/contact/ (no form; email + lists only). Event request form at https://sudoroom.org/event-request/ is currently broken. | Verified |
| Address | — | physical | Omni Commons, 4799 Shattuck Ave, Oakland CA 94609 — "in the back of the building" | `sudoroom.org/contact/` |

**Best outreach path:** Sudo Room is consensus-run with no single decision-maker, so post to **`sudo-discuss@sudoroom.org`** (open subscription, publicly archived — the right venue to raise a proposal) and cc `info@sudoroom.org`. Their feeds already work, so lead with the data-quality asks rather than a request for a feed: the ICS still carries the pre-2014 `549 48th St` address on every event, the export ignores `scope`/`limit` and returns 8 MB with recurrences expanded to 2058, and `/event-request/` throws "Unauthorized Access" for the public. Separately — and this is the high-leverage move — email **`commons@lists.omnicommons.org`** (the Omni Commons Working Group, which maintains the building's Airtable "Public Calendar" and the event request form, and meets 2nd & 4th Mondays at 5pm) and ask them to switch on Airtable's native iCal sync for view `viwJFaj6nFsS1poak`. That single toggle would yield one Tier-A feed covering Sudo Room, Counter Culture Labs, Liberated Lens, Food Not Bombs and the rest of the Omni collectives — and CCL currently has no feed at all, only Meetup.

## Recommended `sources.yaml` entry

```yaml
  - id: sudo-room
    name: Sudo Room
    city: Oakland
    region: east-bay
    url: https://sudoroom.org/
    address_override: "Omni Commons, 4799 Shattuck Ave, Oakland, CA 94609"
    # Both feeds ship a stale LOCATION (549 48th St, the pre-2014 address).
    # Override venue for every event from this space.
    sources:
      - adapter: ics
        url: https://sudoroom.org/?ical=1
        label: events-manager
        trust: 100
        verified: true
        notes: >
          WP Events Manager 7.2.3.1. 8 MB, 5057 VEVENTs, recurrences pre-expanded
          to 2058; 73 events land in the next 120 days. Query params (scope, limit,
          category) are IGNORED - the server always returns the full body, so clamp
          to horizon_days client-side. No RRULEs. Cache on If-Modified-Since.
        filters:
          # Feed carries cross-posted third-party events (Noisebridge, Oakland
          # Museum, bike parties, Food Not Bombs). Keep Sudo Room's own.
          categories_exclude:
            - "events happening elsewhere"
            - "noisebridge"
            - "east bay food not bombs"
            - "museum events"
            - "bike parties"
      - adapter: ics
        url: https://api.lu.ma/ics/get?entity=calendar&id=cal-bqAGiRcc3VT4oYW
        label: luma
        trust: 90
        verified: true
        notes: >
          lu.ma/sudoroom, calendar id cal-bqAGiRcc3VT4oYW. 146 KB, 269 VEVENTs,
          19 future. Better one-off/workshop coverage and a real per-event URL;
          thinner on the recurring backbone. Some entries are off-site
          (e.g. The Box Shop, 951 Hudson Ave SF) - check LOCATION before
          applying address_override.
      - adapter: rss
        url: https://sudoroom.org/events/feed/
        label: events-rss
        trust: 40
        verified: true
        notes: >
          Same event post type as the ICS. pubDate is the EVENT START, not the
          publish time. Fallback / change-detection only - no end time, no venue.
      - adapter: rss
        url: https://sfba.social/@sudoroom.rss
        label: mastodon
        trust: 10
        verified: true
        notes: Announcement signal, not an event feed. Links to luma.com slugs.
    # Dead ends - do not re-investigate:
    #   No Google Calendar embeds exist on sudoroom.org/calendar/ (0 hits for
    #     calendar.google.com). The colored entries are WP-FullCalendar categories.
    #   No JSON-LD on any page. No Tribe/The Events Calendar.
    #   wp-json exposes events-manager/v1 but only a /uploads route; wp/v2/event 404s.
    #   No Meetup group (meetup.com/sudoroom/events/rss/ -> 404).
    #   omnicommons.org/calendar -> 500. omnicommons.org/wiki/Calendar is a
    #     governance-minutes archive, not an events source.
    #
    # LEAD, high leverage: the "Omni Commons General Calendar" on
    # sudoroom.org/calendar/ is an Airtable embed, shrxXjo869sxBzHmr
    # (view viwJFaj6nFsS1poak, table tblgCW1CuG5eilxrD). readSharedViewData
    # 302s to login and downloadCsv 403s behind PerimeterX. Ask
    # commons@lists.omnicommons.org to enable Airtable's native iCal sync -
    # one feed would cover every Omni collective, including Counter Culture
    # Labs, which currently has no feed at all.
```

## Research log

- 2026-08-05 — Fetched `sudoroom.org/calendar/` raw with curl (86 KB) and grepped the source for `calendar.google.com`: **0 hits**. The only `<iframe>` on the page is `https://airtable.com/embed/shrxXjo869sxBzHmr`. Confirmed the "color-coded Google Calendars" in the brief are WP-FullCalendar `<option>` category entries (`#aa0000 - sudo room events`, `#338800 - counter culture labs`, …), and that "Omni Commons General Calendar" is the `<h2>` immediately preceding the Airtable iframe. No `gcal_ics` source exists for this space.
- Fetched `https://sudoroom.org/?ical=1` → 200, `text/calendar`, 7,967,575 bytes, 5057 VEVENTs, `PRODID:-//wp-events-plugin.com//7.2.3.1//EN`. Parsed DTSTARTs: range 2012-01-11 → 2058-02-19; 73 in the next 120 days; top titles Radio Wednesdays (18), WNB Hack and Do Whatever (17), Hardware Hack Night & Fix-it Clinic (17). Re-fetched with `&scope=future`, `&scope=future&limit=200`, and `&category=sudo-room-events` — all three returned byte-identical 7,967,575-byte bodies, so the params are ignored.
- Noted every VEVENT carries `LOCATION:Sudo Room, 549 48th St, Oakland, CA, 94609` / `GEO:37.835055;-122.264256` — the pre-Omni address — while `sudoroom.org/contact/` says "4799 Shattuck (in the back of the building)". Flagged as a required adapter override.
- Pulled the Luma calendar id `cal-bqAGiRcc3VT4oYW` out of `lu.ma/sudoroom` HTML (59 occurrences), then fetched `https://api.lu.ma/ics/get?entity=calendar&id=cal-bqAGiRcc3VT4oYW` → 200, 145,895 bytes, 269 VEVENTs, `X-WR-CALNAME:SudoRoom`, 19 future (Radio Night, WNB Hack, Hardware Hack Night through 2026-09-29). `?id=sudoroom` (slug instead of id) returns **404** — the `cal-` prefixed id is required.
- Probed WordPress structure: `sudoroom.org/wp-json/` lists an `events-manager/v1` namespace whose only route is `/uploads`; `wp/v2` has no `event` post type (`/wp/v2/event` → 404). No `tec-api-version`, no Tribe routes. Grepped `/`, `/calendar/`, `/contact/`, `/events/` for `application/ld+json` → none.
- Feed sweep: `/feed/` 200 (10 blog items, newest 2026-07-16), `/events/feed/` 200 (1715 items, event-start pubDates), `/feed.xml` 404, `/wiki/Special:RecentChanges?feed=atom` 200 (valid Atom, updated 2026-08-05T20:25:24Z).
- Social: `sfba.social/@sudoroom.rss` 200 / 20 items / newest 2026-08-02; `bsky.app/profile/sudoroom.bsky.social/rss` 200 / 17 items (resolves to `did:plc:7buslbhyxygifara7bs7kqxr`). `meetup.com/sudoroom/events/rss/` → **404**, so no Meetup group. Discord invite `discord.gg/zmEzQuzCWX` (from the wiki) resolves 200.
- Omni Commons: `omnicommons.org/calendar` and `/calendar/` 301 → `/blog/calendar/` → **500**; `/blog/`, `/blog/feed/` also 500; `/feed/`, `/wp-json/`, `/events/feed/` 404. `/occupy/` (the event request page) serves 200 and names `commons@lists.omnicommons.org`. Read `omnicommons.org/wiki/Calendar` — it is delegate-meeting minutes through 2025/08/12 with an explicit banner pointing to the (broken) live calendar, **not** 2014-era content as the brief recorded.
- Read `omnicommons.org/wiki/Working_Groups` and `/wiki/Commons_Working_Group`: the Commons WG "maintains our calendar and event request form", meets 2nd & 4th Mondays at 5pm, and runs the `commons`, `booking`, and `productions` Mailman lists (all verified 200 at `omnicommons.org/lists/listinfo/*`). Also captured `info@`, `comms@`, `building@omnicommons.org` and the three named wiki/calendar helpers.
- Attacked the Airtable house calendar three ways: embed HTML 200 but is a JS shell (title "Airtable - Public Calendar"; extracted `viwJFaj6nFsS1poak` / `tblgCW1CuG5eilxrD`); `/v0.3/view/shrxXjo869sxBzHmr/readSharedViewData` → **302 → /login**; `/v0.3/view/viwJFaj6nFsS1poak/downloadCsv` → **403** behind PerimeterX (`window._pxAppId`). Recorded as a lead with a social-engineering-free fix: ask Commons WG to turn on Airtable's built-in iCal sync.
- Contacts: `sudoroom.org/contact/` yields `info@sudoroom.org`, IRC `#sudoroom` on libera.chat, and the two Mailman lists; `sudoroom.org/lists/` (Postorius 1.3.4) enumerates ~40+ topic lists across 5 pages. Verified `sudo-discuss` info page (open subscription, publicly archived) and `sudo-announce`. `sudoroom.org/other-contacts/` → 404, and `sudoroom.org/event-request/` renders "Unauthorized Access" to logged-out visitors.
- Cross-checked Counter Culture Labs (`counterculturelabs.org`, Weebly): no gCal, no JSON-LD, no email in source, and the only event link is `meetup.com/Counter-Culture-Labs/events/calendar/` — which strengthens the case for the shared Omni feed.
