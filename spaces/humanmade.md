# Humanmade

**ID:** `humanmade` · **City:** San Francisco · **Region:** sf
**Address:** 655 Bryant Street, San Francisco, CA 94107 (SoMa)
**Website:** https://www.humanmade.org/
**Status:** active — Instagram posting through early Aug 2026, hours M–F 9am–9pm / Sat–Sun 10am–6pm, 2026 accelerator cohort running; but public *project workshops* look paused (Eventbrite organizer shows 0 upcoming).

## Summary

Humanmade is a nonprofit open-access advanced manufacturing training center in SoMa (~13,500 sq ft: wood shop, metal shop, CNC, 3D printing, laser, textiles, electronics). It runs three distinct event streams: beginner project workshops (glass etching, rug tufting, garment making — historically sold on Eventbrite), founder/accelerator programming (Founder Sessions, Accelerator Kickoff, Demo Day — on Luma), and hosted third-party events at the venue (e.g. SF Hardware Meetup). Volume is low-to-moderate: their own Luma calendar carries 7 events over ~18 months, and the Eventbrite archive from Nov 2025 showed roughly a dozen workshops listed at once. Right now the only *scheduled* Humanmade-run events anywhere machine-readable are two on Luma.

## Verified feeds

Only sources you personally fetched and confirmed return real events.

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `ics` | `https://api.lu.ma/ics/get?entity=calendar&id=cal-ExbgBW04yBHC7bp` | 200 | `text/calendar; charset=utf-8` | 7 VEVENTs, 2 in the future (2026-08-13 Founder Session – Beyond the Prototype; 2026-11-13 Hardware Accelerator Demo Day) | **The non-Eventbrite route.** Humanmade's own public Luma calendar (`access_level: public`, host `Humanmade Information` / `usr-n7U6VlUhzjMahin`). Every VEVENT has `LOCATION:655 Bryant St, San Francisco, CA 94107, USA`, `GEO`, stable `UID:evt-...@events.lu.ma`, and a `luma.com/<slug>` link in `DESCRIPTION`. Fetched successfully with the project's own UA, no browser needed. Caveat: Luma names it "Personal" and `slug` is `null`, so there is no public `luma.com/<name>` page — the ICS is the only surface. Covers founder/accelerator events only, **not** project workshops. |
| `ics` | `https://api.lu.ma/ics/get?entity=calendar&id=cal-tFAzNGOZ9xn6kT2` | 200 | `text/calendar; charset=utf-8` | 77 VEVENTs total; 3 historically at Humanmade (119th / 123rd / 134th SF Hardware Meetup @ Humanmade); 3 future but none currently at Humanmade | Third-party: SF Hardware Meetup (`luma.com/sf-hardware-meetup`), which rotates venues and lands at Humanmade several times a year. `LOCATION` is **not** populated with the address — filter on `SUMMARY` containing `@ Humanmade`. Trust low; this is a partner group, not Humanmade. |
| `jsonld` | Individual Eventbrite event pages, e.g. `https://www.eventbrite.com/e/make-it-wear-it-own-it-a-2-day-garment-workshop-tickets-1990043595966` | 200 | `text/html; charset=utf-8` | 1 event per page | Confirmed: 2 `<script type="application/ld+json">` blocks, one with `"@type": "EducationEvent"` carrying `name`, `description`, `url`, `image`, `eventStatus`, `location` (Place → PostalAddress with the full 655 Bryant St address), and organizer. **Per-event pages still emit clean JSON-LD.** The organizer *listing* page no longer does (see Dead ends). |

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| `nextdata` | `https://www.eventbrite.com/o/humanmade-57286899753` → `<script id="__NEXT_DATA__">` → `props.pageProps.upcomingEvents` | Shape confirmed present and parseable today; the array is simply empty (`upcomingEventsTotal: 0`, `hasMoreUpcoming: false`). Same blob also carries `organizer.bio`, `organizer.metrics.followers` (191), and `collections` (5). | Re-fetch once Humanmade schedules workshops again and assert `len(upcomingEvents) > 0`, then record the per-event key names. Do **not** wire the `jsonld` adapter at this URL. |
| `nextdata` + `jsonld` (two-step) | Organizer `__NEXT_DATA__` → event URLs → fetch each event page → parse `EducationEvent` JSON-LD | Gets the richest data (address, offers, status) and both halves are individually confirmed working. | Costs N+1 requests; rate-limit to 2s/request. Only worth it if the organizer blob turns out to lack times/prices. |
| ask | Email Humanmade and ask them to put workshops on the same Luma calendar they already use, and/or give the calendar a public slug | They *already run a public Luma calendar*. Adding workshops to it is a zero-cost ask that eliminates the Eventbrite ToS question entirely. Nonprofit with a training mission = good candidate. | See Contact below. |
| `ics` | Meetup official RSS `https://www.meetup.com/hardwarestartupsf/events/rss/` | 200, valid `application/rss+xml`, correct `<channel>` for "Events - SF Hardware", `lastBuildDate` current. | **Zero `<item>` elements today** — the group publishes to Luma, not Meetup. Redundant with the SF Hardware Luma ICS above. Skip. |
| change-signal | `https://linktr.ee/humanmadesf` (in Instagram bio) | Their actual "what's on right now" surface. Currently links a Jotform, the workforce program, a Luma RSVP, and donate. | Poll the linktree `__NEXT_DATA__` for new `luma.com/*` or `eventbrite.com/*` URLs as a cheap discovery signal. Not a feed. |

## Dead ends

- **`https://www.eventbrite.com/o/humanmade-57286899753` has NO JSON-LD.** 200, 151 KB, but `grep application/ld+json` → **0 blocks**. Eventbrite migrated the organizer profile to a Next.js app (`/organizer-profile/_next/...`, `buildId` `5bLzESWhODLae_gctfpa1`); the data now lives only in `__NEXT_DATA__`. **The `adapter: jsonld` entry currently in `sources.yaml` for this URL will silently return zero events forever.**
- **Even historically it wasn't `@type: Event`.** The Wayback snapshot `web.archive.org/web/20251101093332/…` (the only capture; fetch with `--compressed`) *does* have 2 JSON-LD blocks — but they are `ProfilePage` and `ItemList`, with events nested as `itemListElement[].item` (`startDate`/`endDate`/`url`/`description`/`image`). A naive "walk for `@type: Event`" would have missed them. That snapshot listed ~14 workshops (rug tufting, glass etching, laser-etched wallet, electroplating, Halloween magnets…), which is what the handoff's "lists upcoming workshops" claim was based on. **That claim is now stale.**
- **Humanmade's own Eventbrite organizer has 0 upcoming events** as of 2026-08-05. Confirmed three ways: plain curl, browser-UA curl, and WebFetch — all return `upcomingEventsTotal: 0`, `metrics.totalEvents: 0`. `?tab=past` returns the same blob (past events are client-fetched). Search engines still show a cached "4 Upcoming Activities" title; the live page disagrees.
- **Eventbrite organizer RSS: gone.** `/o/humanmade-57286899753.rss` → 404, `/rss/organizer_list_events/57286899753` → 404, `/o/humanmade-57286899753/rss` → 404.
- **Eventbrite per-event ICS: gone.** `/calendar?eid=1990043595966` → 404, `/e/1990043595966/add-to-calendar` → 404.
- **humanmade.org has no calendar.** Squarespace (`Static.SQUARESPACE_CONTEXT`). `sitemap.xml` lists 40 URLs and contains **no** `/events`, `/calendar`, or blog collection. `/events` → 404, `/calendar` → 404, `/feed` → 404, `/?format=rss` → **400**. `/project-workshops?format=json` → 200 but `collection.typeName: "page"`, `items: 0` — it's a static marketing page describing two workshop *types* (Glass Etching, Rug Tufting) with durations but **no dates**, and it explicitly says "Interested in past workshops? Visit our Eventbrite." So: confirmed, program marketing, not a calendar.
- **No embedded Google Calendar.** Zero `<iframe>` elements on the homepage, `/project-workshops`, `/contact`, or `/book-equipment`. Nothing pointing at `calendar.google.com`.
- **No class-booking platform.** Grepped the homepage, `/project-workshops`, `/contact`, and `/book-equipment` for bookwhen, sawyer, activityhero, tickettailor, acuity, punchpass, calendly, skedda, momence, mindbody, fareharbor, xola, peek, wildapricot, humanitix, luma/lu.ma — **only** hits are `eventbrite` (3, all pointing at the organizer page). `/book-equipment` links to no external booking host at all.
- **No Luma vanity slug.** `luma.com/humanmade` → 404, `lu.ma/humanmade` → 404. The working calendar has `slug: null`; you must use the `cal-` API ID.
- **No Mastodon, no Bluesky.** `sfba.social/@humanmade.rss` → 404, `sfba.social/@humanmadesf.rss` → 404, `bsky.app/profile/humanmade.org/rss` → 404, `bsky.app/profile/humanmadesf.bsky.social/rss` → 404.
- **DoTheBay is useless here.** `dothebay.com/venues/humanmade` → 200 via curl but the listing is client-rendered (0 event titles extractable, 0 JSON-LD); WebFetch gets 403 (Cloudflare). `.rss` and `.ics` variants return **406** with the right content-type but a 1-byte body. Aggregator anyway — discovery only.
- **Broken link on their own linktree:** "Workshops at Humanmade" points to `https://www.eventbrite.humanmade.com`, which **does not resolve** (DNS failure). Worth mentioning when you email them — it's a free goodwill opener.

### ToS note

Eventbrite's Terms prohibit scraping. The organizer page no longer offers JSON-LD, so the only remaining Eventbrite route is parsing `__NEXT_DATA__` from a public page — strictly *more* scraper-shaped than the JSON-LD parse the handoff contemplated, and the blob is an undocumented internal payload that will break without notice. **Given that a real, verified, first-party ICS now exists (Luma), the honest recommendation is to ship the Luma feed and treat Eventbrite as an ask-them-first item rather than an engineering item.**

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Instagram | @humanmadesf | https://www.instagram.com/humanmadesf/ | none | fetched 200 | ~3,706 followers, active through Aug 2026. Story highlights for "Events" and "Workshops". Bio link → linktr.ee/humanmadesf. Best DM channel. |
| Linktree | humanmadesf | https://linktr.ee/humanmadesf | `__NEXT_DATA__` (scrapeable, not a feed) | 200 | Where the Luma RSVP link surfaced. Change-signal only. |
| LinkedIn | Humanmade | https://www.linkedin.com/company/humanmade/ | none | link present in site footer | Human channel. Good for reaching Ryan Spurlock. |
| Facebook | Humanmade.org | https://www.facebook.com/Humanmade/ | none | link present in site footer | Human channel, lower activity. |
| Luma | Humanmade Information (`usr-n7U6VlUhzjMahin`) | no public profile page (`slug: null`) | **yes** — see Verified feeds | 200 | This is the account to ask about. |
| Mastodon | — | — | — | searched, none found | |
| Bluesky | — | — | — | searched, none found | |
| Newsletter | — | signup form in footer of every humanmade.org page | none | — | Human channel. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| General inquiries & access plans | — | email | info@humanmade.org | humanmade.org/contact footer + body |
| Billing | — | email | billing@humanmade.org | humanmade.org/contact |
| Main line | — | phone | (415) 484-1437 | humanmade.org/contact |
| Contact form | — | web | https://www.humanmade.org/contact (Squarespace form) | fetched, `<form>` present |
| Physical | — | address | 655 Bryant Street, San Francisco, CA 94107 | site footer |
| Founder & CEO | Ryan Spurlock | via info@ / LinkedIn | — | humanmade.org/team |
| **Director of Programs and Operations** | **Sandra Spurlock** | via info@ | — | humanmade.org/team |
| **Program Manager and Client Support & Outreach Coordinator** | **Amber Anderson** | via info@ | — | humanmade.org/team |
| Innovation Lab Manager | Emily Coker | via info@ | — | humanmade.org/team |
| Lead NGMT Program Instructor | Jasper Thomas | via info@ | — | humanmade.org/team |
| Board Chair | Sarayah Rogers | — | — | humanmade.org/team |
| Luma calendar owner | "Humanmade Information" | Luma host account | `usr-n7U6VlUhzjMahin` | luma.com/zcnnzm6y `__NEXT_DATA__` |

**Best outreach path:** Email info@humanmade.org addressed to **Amber Anderson (Program Manager & Outreach Coordinator)**, cc'ing **Sandra Spurlock (Director of Programs and Operations)** — Amber owns outreach and Sandra owns programming, so between them they control both the workshop schedule and the Luma account. The ask is concrete and cheap: *"you already publish a public Luma calendar; would you put project workshops on it too, and give it a public slug?"* Open with the free favor — their Linktree's "Workshops at Humanmade" button points at `www.eventbrite.humanmade.com`, which doesn't resolve.

## Recommended `sources.yaml` entry

```yaml
  - id: humanmade
    name: Humanmade
    city: San Francisco
    region: sf
    url: https://www.humanmade.org/
    sources:
      - adapter: ics
        url: https://api.lu.ma/ics/get?entity=calendar&id=cal-ExbgBW04yBHC7bp
        label: luma-humanmade
        trust: 100
        verified: true
        notes: >
          Humanmade's own public Luma calendar (cal-ExbgBW04yBHC7bp, host
          "Humanmade Information"). No vanity slug, so the cal- API ID is the
          only handle. Covers founder sessions and accelerator events; does NOT
          currently include project workshops. Luma names it "Personal" -
          X-WR-CALNAME is not useful, do not use it as the source label.
      - adapter: nextdata
        url: https://www.eventbrite.com/o/humanmade-57286899753
        label: eventbrite-organizer
        trust: 90
        verified: false
        notes: >
          CORRECTED 2026-08-05: this page no longer emits JSON-LD (Eventbrite
          moved it to Next.js). Events live at
          props.pageProps.upcomingEvents in <script id="__NEXT_DATA__">.
          As of 2026-08-05 upcomingEventsTotal is 0, so this cannot be verified
          and must not be allowed to trip the "source went to zero" health gate
          until it has been non-zero at least once. Eventbrite ToS restricts
          scraping - prefer asking them to publish workshops on Luma instead.
      - adapter: ics
        url: https://api.lu.ma/ics/get?entity=calendar&id=cal-tFAzNGOZ9xn6kT2
        label: sf-hardware-meetup
        trust: 40
        verified: true
        filters:
          # SF Hardware Meetup rotates venues; LOCATION is not populated in this
          # feed, so filter on the title instead of the address.
          title_contains: ["@ Humanmade"]
        notes: >
          Third-party group that hosts at Humanmade several times a year
          (119th, 123rd, 134th meetups). 77 VEVENTs in the feed. Low trust:
          this is a partner's calendar, not Humanmade's.
```

## Research log

- 2026-08-05 — Fetched `https://www.eventbrite.com/o/humanmade-57286899753` with both a project UA and a Chrome UA (200, 151 KB). Grepped for `application/ld+json`: **zero blocks**. Found `<script id="__NEXT_DATA__">`; parsed it to `props.pageProps` with keys `organizer`, `upcomingEvents`, `hasMoreUpcoming`, `upcomingEventsTotal`, `collections`. `upcomingEventsTotal: 0`. Re-checked with `?tab=past` and `?page=1&tab=past` — same. WebFetch independently reported "no upcoming events are listed."
- 2026-08-05 — Pulled the only Wayback snapshot (`20251101093332`, needs `--compressed`). It **does** carry JSON-LD, but as `ProfilePage` + `ItemList`/`ListItem`, not top-level `@type: Event`, listing ~14 workshops. Confirms the handoff's claim was true in Nov 2025 and that Eventbrite changed the page since.
- 2026-08-05 — Fetched a real Eventbrite event page (garment workshop, id 1990043595966): 200, 2 JSON-LD blocks, one a full `EducationEvent` with `location.address.streetAddress = "655 Bryant Street, San Francisco, CA 94107"`. Organizer backlink confirms `/o/humanmade-57286899753` is the right and only organizer ID.
- 2026-08-05 — Searched Eventbrite's SF destination page for "humanmade"; parsed `window.__SERVER_DATA__`. Exactly one upcoming event at the HumanMade venue (Deep Tech Founder/VC Breakfast, 2026-08-07) and it is organized by a third party, not by Humanmade. Confirms Humanmade itself has nothing scheduled there.
- 2026-08-05 — Probed Eventbrite legacy feeds: `.rss`, `/rss`, `/rss/organizer_list_events/<id>`, `/calendar?eid=`, `/e/<id>/add-to-calendar` — all 404.
- 2026-08-05 — Fetched humanmade.org (Squarespace), `/project-workshops`, `/contact`, `/book-equipment`, `/team`, `sitemap.xml`. No events collection in the sitemap; `/events` and `/calendar` 404; `?format=rss` 400; `/feed` 404; `?format=json` shows `typeName: page`, `items: 0`. Zero iframes → no embedded gCal. Grepped for 16 class-booking platforms → only Eventbrite.
- 2026-08-05 — Read `/team` for staff names and roles; scraped emails/phone from `/contact`.
- 2026-08-05 — Checked Instagram @humanmadesf (active, Aug 2026) → bio link `linktr.ee/humanmadesf` → parsed its `__NEXT_DATA__` → found `luma.com/zcnnzm6y`. **This was the break.** That event's `__NEXT_DATA__` gave calendar `cal-ExbgBW04yBHC7bp` (`access_level: public`) and host `Humanmade Information`. `https://api.lu.ma/ics/get?entity=calendar&id=cal-ExbgBW04yBHC7bp` → 200 `text/calendar`, 7 VEVENTs with real dates, addresses and stable UIDs, 2 in the future.
- 2026-08-05 — Same linktree exposed a broken button: `https://www.eventbrite.humanmade.com` fails DNS resolution.
- 2026-08-05 — Followed the SF Hardware Meetup connection: `luma.com/7d18zu5c` → calendar `cal-tFAzNGOZ9xn6kT2` (`sf-hardware-meetup`) → ICS 200, 77 VEVENTs, 3 titled "@ Humanmade". Also tested the official Meetup RSS `meetup.com/hardwarestartupsf/events/rss/`: 200 valid RSS, 0 items.
- 2026-08-05 — Negative checks: `luma.com/humanmade` and `lu.ma/humanmade` 404; Mastodon and Bluesky `.rss` probes all 404; `dothebay.com/venues/humanmade` 200 but JS-rendered with `.rss`/`.ics` returning 406.
