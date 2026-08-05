# Frontier Makerspace (Frontier Tower)

**ID:** `frontier-makerspace` · **City:** San Francisco · **Region:** sf
**Address:** 995 Market St, Floor 7, San Francisco, CA 94103
**Website:** https://frontiertower.io/
**Status:** active — 16-floor "vertical village" operating at 995 Market; Floor 7 is a 4,000 sq ft makerspace; building-wide Luma calendar is live and being updated.
**Last researched:** 2026-08-05

## Summary

Frontier Tower is a 16-floor deep-tech coworking/community building at 995 Market St (formerly branded **Berlinhouse**). Each floor is a themed community — Floor 2 "Spaceship" event hall, F04 Robotics & Hard Tech, F05 Movement & Fitness, F06 Arts & Music, **F07 Frontier Makerspace**, F08 Neuro & Biotech, F10 Accelerate, F11 Health & Longevity, F12 Crypto / Ethereum House, F14 Human Flourishing, F16 d/acc Lounge. Events are AI meetups, hackathons, demo nights, robot fights, laser-cutter trainings and community dinners.

Marketing claims "3+ events a day"; the **public Luma calendar actually carries ~30–70 events/month** and, at any given moment, only ~25–30 *upcoming* events (Luma events here are created 1–2 weeks out). Strictly makerspace-tagged events are rare — roughly 1–2/month historically. The Tower's own [Event Organizer Guide](https://frontiertower.io/event-guide) states that **all events must be created in the Frontier Tower app and then appear on lu.ma/frontiertower**, so the single Luma calendar is authoritative and per-floor sub-calendars are not part of the workflow.

## Verified feeds

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `ics` | `https://api.lu.ma/ics/get?entity=calendar&id=cal-Sl7q1nHTRXQzjP2` | 200 | `text/calendar; charset=utf-8` (154,646 B) | **262 VEVENTs; 26 upcoming** (DTSTART ≥ 2026-08-05), span 2025-03-01 → 2026-10-03 | The real "Add iCal Subscription" target for `luma.com/frontiertower`. `X-WR-CALNAME:Frontier Tower SF`, `REFRESH-INTERVAL:PT12H`. Calendar id `cal-Sl7q1nHTRXQzjP2` read from the page's `__NEXT_DATA__`. **Caveat: the ICS is truncated in the middle** — months 2025-10 through 2026-07 return 0 events in the ICS while the calendar API reports 68–123/month. It appears to carry *all upcoming* events plus the ~236 oldest past ones. Fine for a 120-day forward horizon; useless for backfill. |
| `ics` | `https://api.lu.ma/ics/get?entity=calendar&id=cal-3QxzLUEwOpGxxNc` | 200 | `text/calendar; charset=utf-8` (68,271 B) | **121 VEVENTs; 49 at Frontier Tower; 2 upcoming** | "The Biopunk Community" (`luma.com/biopunk`, biopunklab.com) — the F08 Neuro & Biotech community's own calendar and the only genuine sub-community feed found. It spans multiple venues (Studio 55 / 55 Grace St, 995 Market, 1153 Bush St), so it needs a `995 Market`/`Frontier` location filter. Currently low activity and its two upcoming events are off-site. Optional, low trust. |

Both endpoints are permitted by `api.lu.ma/robots.txt` (only `/insights/` is disallowed).

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| custom JSON | `https://api.frontiertower.io/` — "Frontier OS" API | Root returns `401 {"detail":"Authentication credentials were not provided."}` (Django-REST style), so a real API exists. The public SDK `@frontiertower/frontier-sdk` (GitHub `BerlinhouseLabs/frontier-sdk`, npm v0.25.0) documents `events:listEvents`, `events:listLocations`, `events:listRoomBookings`. `listLocations` would give the *canonical* room/floor names instead of free-text. | Ask `events@frontiertower.io` or the SDK maintainer (`chp@frontiertower.io`) for a read-only key or a public events endpoint. Allowed origins in the SDK README are `os.frontiertower.io` / `sandbox.os.frontiertower.io`. |
| `jsonld` / `llm_html` | `https://frontiertower.io/events` (200, 278 KB, server-rendered) | Server-side mirror of the same Luma events with clean floor labels ("Spaceship", "16th Floor Coffee Meetup", "10th Floor Annex"). `robots.txt` is `Allow: /`. | Only worth building as a fallback if the Luma ICS breaks; it carries no data the ICS lacks. Contains no `schema.org/Event` JSON-LD today (only Organization / CoworkingSpace / FAQPage). |
| human | `https://frontiertower.notion.site/citizen-wiki` (members wiki), `https://frontiertower.notion.site/` | Likely lists floor leads and per-floor contacts. | Client-rendered Notion; plain GET returns just "Notion". Needs a browser, or just ask in outreach. |
| human | Telegram `https://t.me/+M0KxFTd3LnJkNzky` (main), `https://t.me/+yOHm7n9ZbJMwMDI1` (Ethereum House F12) | Invite-link groups; primary community channel. | Join by hand. Not a feed. |

## Dead ends

- **No per-floor Luma sub-calendars exist.** Every Frontier Tower event I sampled (`monthly-robotics-meetup`, `mass-arts-5`, `vcn-47-fast-local`, `asimov-devlabs-12`, `wild-ai-sf`, `otherminds-community-meet`) resolves to the single calendar `cal-Sl7q1nHTRXQzjP2` / slug `frontiertower`. The Event Organizer Guide requires it. Only outside partners (viva.city, Biopunk) keep separate calendars and cross-post.
- `https://luma.com/frontiertowersf` → `cal-o8Bopzo97A598P5`, name "Frontier Tower SF", 1 event total, 0 upcoming. Stale duplicate, points at `berlinhouse.com/membership`. Do not use.
- `https://luma.com/berlinhouse` → `cal-c3ICpR21AFn5743`, "Berlinhouse SF" (the predecessor brand, same building). 9 events total, **0 upcoming**. Dormant.
- `https://luma.com/makerspace` → "RWI Makerspace Labs" (rw.institute) — **unrelated**.
- `https://luma.com/frontierbio` → "Frontier Bio", a vascular-graft company in Hayward — **unrelated**.
- `https://luma.com/frontiersyndicate` → "The Frontier Syndicate", an SF frontier-tech VC community — **unrelated** to the tower.
- 404 on Luma: `frontiermakerspace`, `frontier-makerspace`, `ftmakerspace`, `frontiertowermakerspace`, `ftarts`, `frontier-arts`, `artsandmusic`, `ftrobotics`, `frontier-robotics`, `hardtechrobotics`, `humanflourishing`, `ftlongevity`, `dacc`, `spaceship`, `laserfrydays`, `edgeai-robotics` and ~20 other guessed floor slugs.
- **`https://www.frontiermakerspace.com/` — do not crawl.** It is the makerspace's real site (registered 2025-06-07, NameCheap, still active; Google has indexed real content) but `robots.txt` is `User-Agent: * / Disallow: /`, every plain GET returns a 494-byte Joken/Cowboy JS bot-challenge shell, and a `HEAD` request 302s to `http://survey-smiles.com`. Off-limits and unparseable. Use it as a human/outreach reference only.
- **Meetup `https://www.meetup.com/frontier-makerspace/events/rss/`** — 200, `application/rss+xml`, valid RSS **with zero `<item>` elements**. The matching iCal endpoint `.../events/ical/` is 200 `text/calendar` with zero `VEVENT`s. Both are live published endpoints (no ToS problem), the group just has no upcoming events. Cheap to re-check monthly; worthless today.
- **Eventbrite**: `https://www.eventbrite.com/o/frontier-tower` → 404. No organizer page.
- **Ticket Tailor**: no presence found.
- **Mastodon / Bluesky**: none. `sfba.social/@frontiertower.rss`, `mastodon.social/@frontiertower.rss`, `bsky.app/profile/frontiertower.bsky.social/rss`, `bsky.app/profile/frontiertower.io/rss` all 404.
- **Google Calendar**: no `calendar.google.com` iframe anywhere on `frontiertower.io` (homepage, /events, /about, /office, /event-guide, /start-a-community, /developers all checked).
- **Site feeds**: `frontiertower.io/feed/`, `/rss`, `/rss.xml`, `/calendar`, `/contact`, `/press`, `/press-kit` → all 404. `/sitemap.xml` is 200 but lists only 11 marketing pages.
- **`__NEXT_DATA__` on `luma.com/frontiertower`** does parse and is useful for the calendar id and an `event_start_ats` array, but Luma's undocumented `api.lu.ma` endpoints are the wrong long-term dependency. `api.lu.ma/search/get-results` is 401 (sign-in required); `api.lu.ma/calendar/get-featured-calendars` returns Luma's global featured list, not sub-calendars.
- `https://os.frontiertower.io/` is an SPA that returns the same 21 KB shell for every path; `api.frontiertower.io/api/events`, `/v1/events`, `/locations` → `404 {"error":"Resource not found"}`.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| X / Twitter | `@frontiertower` | https://x.com/frontiertower | none | n/a | Confirmed in site JSON-LD `sameAs` and in Luma calendar metadata. |
| Instagram | `@frontiertower` | https://www.instagram.com/frontiertower/ | none | n/a | Same sources. A separate makerspace-flavored IG presence exists but no distinct handle confirmed. |
| LinkedIn | `/company/frontiertower` | https://www.linkedin.com/company/frontiertower | none | n/a | |
| Telegram | invite link | https://t.me/+M0KxFTd3LnJkNzky | none | link present on every page | **Primary community channel.** Best DM/announce surface. |
| Telegram | Ethereum House (F12) | https://t.me/+yOHm7n9ZbJMwMDI1 | none | from Linktree | Floor-specific. |
| Linktree | `frontiertower` | https://linktr.ee/frontiertower | none | yes (fetched) | Confirms `lu.ma/frontiertower` as *the* events calendar; also links citizen wiki, app onboarding, membership. |
| X / Instagram | `@berlinhouse_sf` | https://x.com/berlinhouse_sf | none | from Luma `berlinhouse` calendar metadata | Legacy brand account. |
| Mastodon | — | — | — | checked, none | |
| Bluesky | — | — | — | checked, none | |
| Newsletter | "Subscribe to our newsletter" form in the `frontiertower.io` footer | https://frontiertower.io/ | none | n/a | Human channel. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| Events / programming | — | email | **`events@frontiertower.io`** | frontiertower.io/event-guide (§06, §13) |
| General / support | — | email | `support@frontiertower.io` | site footer + JSON-LD `ContactPoint` |
| Sponsorship | — | email | `sponsorship@frontiertower.io` | frontiertower.io homepage |
| Developer / SDK maintainer | — | email | `chp@frontiertower.io` | npm maintainer record for `@frontiertower/frontier-sdk` |
| Inquiry form (routes to the right person; "Hosting an event" option) | — | web form | https://frontiertower.io/office | fetched page |
| Community pitch form | — | web form | https://frontiertower.io/start-a-community | fetched page |
| Membership application | — | web form | https://frontiertower.io/apply | fetched page |
| Makerspace / robotics event hosts | Tony Loehr, Anna H | Luma host credits | hosts of "Monthly Robotics Meetup" and "LASER FRYDAYS: Laser Cutter Training" at Floor 7 Makerspace | ICS `DESCRIPTION` "Hosted by" lines |
| Robotics & Hard Tech floor leads | Xenia Bulatov, Jakob Drzazga | Luma organizer | organizers of "Robotics & Hard Tech Floor Welcome", "Robot Fights", "LeKiwi Robot Build Day" | ICS `ORGANIZER;CN=` |
| Arts & Music programming | Gage Olesen | Luma host | hosts MASS ARTS series and Wild AI SF | Luma event pages |
| Co-founder / most prolific personal organizer | Laurence Ion | Luma organizer (21 events) | — | ICS `ORGANIZER;CN=` |
| Biotech / Biopunk community | Elliot Roth ("Biopunk Community Lab", 13+4 events) | Luma organizer | biopunklab.com | ICS `ORGANIZER;CN=` |
| Physical address | — | — | 995 Market St, San Francisco, CA 94103 (Floor 7 = makerspace) | JSON-LD `PostalAddress` |
| Community chat | — | Telegram | https://t.me/+M0KxFTd3LnJkNzky | site footer / Linktree |

**Best outreach path:** Email **`events@frontiertower.io`** — it is the address the Tower's own Event Organizer Guide tells hosts to use, so it reaches whoever owns the calendar. Two concrete asks: (1) a public read-only key or endpoint for the Frontier OS `events:listEvents` / `events:listLocations` API, which would solve the location-string mess permanently, and (2) a convention (a Luma tag, or a consistent `Makerspace / Floor 7` location string) so maker-relevant events can be picked out reliably. Cc `support@frontiertower.io`, and follow up in the main Telegram group, which is where the community actually is.

## Filtering

Sub-calendars found (prefer these over filtering the firehose):

| Floor / community | Calendar URL | Verified |
|---|---|---|
| **None per-floor.** All tower events land on the single `Frontier Tower SF` calendar by policy | `https://luma.com/frontiertower` → `https://api.lu.ma/ics/get?entity=calendar&id=cal-Sl7q1nHTRXQzjP2` | ✅ verified, 262 events |
| F08 Neuro & Biotech — "The Biopunk Community" (partner calendar, multi-venue) | `https://luma.com/biopunk` → `https://api.lu.ma/ics/get?entity=calendar&id=cal-3QxzLUEwOpGxxNc` | ✅ verified, 121 events / 49 at 995 Market |
| Berlinhouse SF (predecessor brand) | `https://luma.com/berlinhouse` → `cal-c3ICpR21AFn5743` | ⚠️ verified but dormant — 9 events, 0 upcoming |
| "Frontier Tower SF" duplicate | `https://luma.com/frontiertowersf` → `cal-o8Bopzo97A598P5` | ❌ stale, 1 event, 0 upcoming |

Location strings observed in real events, verbatim — use these to write the filter (counts from the 262-event ICS pull on 2026-08-05):

- `Frontier Tower | Berlinhouse, 995 Market St, San Francisco, CA 94103, USA` (64) — no floor info
- `Frontier Tower @ Spaceship / Floor 2 995 Market Street, San Francisco` (32)
- `Frontier Tower @ Lounge / Floor 16 995 Market Street, San Francisco` (26)
- `Frontier Tower 🧑‍🚀, 995 Market St, San Francisco, CA 94103, USA` (12) — note the astronaut emoji, and no floor info
- `995 Market St, San Francisco, California` (11)
- `Frontier Tower @ Human Flourishing 995 Market Street, San Francisco` (10)
- `Frontier Tower @ Spaceship 995 Market Street, San Francisco` (6)
- `995 Market St, San Francisco, CA 94103, USA` (5)
- `Frontier Tower @ Arts & Music 995 Market Street, San Francisco` (5)
- `Frontier Tower @ Artificial Intelligence 995 Market Street, San Francisco` (5)
- `BerlinHouse, 995 Market St, San Francisco, CA 94103, USA` (3)
- `Frontier Tower @ Longevity & Health 995 Market Street, San Francisco` (3)
- `Frontier Tower @ 9th Floor Annex 995 Market Street, San Francisco` (3)
- `Frontier Tower @ Rooftop 995 Market Street, San Francisco` (2)
- `Frontier Tower @ Ethereum & Decentralized Tech 995 Market Street, San Francisco` (2)
- `Frontier Tower @ Hard Tech & Robotics 995 Market Street, San Francisco` (2)
- `Frontier Tower @Floor14 995 Market Street, San Francisco` (2)
- `Frontier Tower @ Makerspace / Floor 7 995 Market Street, San Francisco` (2)
- `Frontier Tower @ Human Flourishing (Full Floor) 995 Market Street, San Francisco` (2)
- `Frontier Tower @ Frontier Makerspace / Floor 7 995 Market Street, San Francisco` (1)
- `Frontier Tower | Berlinhouse FL 7 — Makerspace` (1) — em dash, no street address
- `Frontier Tower @ Makerspace 995 Market Street, San Francisco` (1)
- `Frontier Tower @Floor 7 995 Market Street, San Francisco` (1) — Floor 7 = makerspace, but the word "Makerspace" is absent
- `Frontier Tower @ Ethereum Foundation / Floor 12 995 Market Street, San Francisco` (1)
- `Frontier Tower @ Ethereum House 995 Market Street, San Francisco` (1)
- `Frontier Tower @ Berlinhouse Builders 995 Market Street, San Francisco` (1)
- `Frontier Tower @ Biotech 995 Market Street, San Francisco` (1)
- `Frontier Tower @ 16th Floor Coffee Meetup 995 Market Street, San Francisco` (1)
- `Frontier Tower @ 15th Floor Annex` (1) — no street address at all
- `Frontier Tower @ 10th Floor Annex 995 Market Street, San Francisco` (1)
- `Frontier Tower @ Lounge on 16th 995 Market Street, San Francisco` (1)
- `Frontier Tower @ Lounge / Floor 14 995 Market Street, San Francisco` (1)
- `Frontier Tower @ Floor14 995 Market Street, San Francisco` (1)
- `Frontier Tower @Floor 14 995 Market Street, San Francisco` (1)
- `Frontier Tower Floor 14 995 Market Street, San Francisco` (1)
- `Frontier Tower \ Floor 14 @ 995 Market Street, San Francisco` (1) — backslash
- `Flourishing (Floor 14) @ Frontier Tower, 995 Market Street, San Francisco` (1) — venue name first
- `Frontier Tower / Floor 2 995 Market Street, San Francisco` (1)
- `Frontier Tower @ Human Flourishing Full Floor 995 Market Street, San Francisco` (1)
- `995 Market Street, SF @ Spaceship / Floor 2` (1)
- `Frontier Tower @ Lounge / Floor 16 995 Market Street, SF` (1)
- `Frontier Tower @ Arts and Music Floor 995 Market Street, San Francisco` (seen on `luma.com/mass-arts-5`, in the ICS gap) — **note: both `Arts & Music` and `Arts and Music` spellings are in use**
- Off-site, must be excluded: `2121 Larimer St, Denver, CO 80205, USA`, `2 Embarcadero Ctr, San Francisco, CA 94111, USA`, `466 Eddy St, San Francisco, CA 94109, USA`

**What this means for the filter — read before writing it:**

1. **40 of 262 events (15%) have `LOCATION` set to a bare `https://luma.com/event/evt-…` URL.** Luma emits this when the host obfuscates the address behind registration. Any `location_contains` filter silently drops these, and several are makerspace events (`LASER FRYDAYS: Laser Cutter Training`, `Frontier Makerspace All-Hands & Demo Night`, `The SF Bay Area LeRobot Hackathon`). Fall back to the title when `LOCATION` starts with `https://`.
2. **The three strings in the current `sources.yaml` guess are wrong or near-useless.** `Arts and Music` never appears in the ICS (it is `Arts & Music`); `Robotics` only ever appears inside `Hard Tech & Robotics` (2 events); `Makerspace` matches 5 events across 4 spellings. The `Makerspace`/`Arts`/`Robotics` filter would yield roughly **9 of 262 events (3%)**.
3. **The two most common strings carry no floor information at all** (`Frontier Tower | Berlinhouse, 995 Market St…`, 64 events, and the emoji variant, 12). Floor cannot be recovered from them.
4. **It is not actually a firehose.** ~26–30 upcoming events at any moment and ~30–70/month, because Luma events here are created 1–2 weeks ahead. The site's "50+ events every month" figure matches the data; "3+ per day" does not.
5. **Recommendation:** ingest the whole calendar with a `995 Market` / `Frontier Tower` venue filter to drop off-site events, then drop administrative noise by title (`Hold`, `TBA`, `Placeholder`, `Members Only`) rather than trying to isolate the makerspace. If Brennan wants a maker-only view, filter on the union of `Makerspace`, `Floor 7`, `FL 7`, `Arts & Music`, `Arts and Music`, `Hard Tech & Robotics` and accept ~4% recall — or ask the Tower for the `listLocations` API and do it properly.

## Recommended `sources.yaml` entry

```yaml
  - id: frontier-makerspace
    name: Frontier Makerspace (Frontier Tower)
    city: San Francisco
    region: sf
    url: https://frontiertower.io/
    sources:
      - adapter: ics
        url: https://api.lu.ma/ics/get?entity=calendar&id=cal-Sl7q1nHTRXQzjP2
        label: luma-frontiertower
        trust: 100
        verified: true   # 2026-08-05: 200 text/calendar, 262 VEVENTs, 26 upcoming
        notes: >
          Canonical calendar. The Tower's Event Organizer Guide requires every event
          to be created in the Frontier Tower app, from which it lands here, so there
          are no per-floor sub-calendars. The ICS carries all upcoming events but is
          truncated for the middle of its history (2025-10..2026-07 missing) - fine
          for a 120-day forward horizon, useless for backfill.
        filters:
          # Keep the building, drop the off-site events (Denver, Embarcadero, Eddy St).
          location_contains: ["Frontier Tower", "995 Market", "Berlinhouse"]
          # 15% of events set LOCATION to a bare https://luma.com/event/... URL when the
          # host hides the address; those must bypass location_contains or they vanish.
          location_allow_when_missing: true
          # Administrative noise on this calendar, safe to drop by title.
          title_excludes: ["Hold -", "TBA (", "Placeholder"]
          # --- Optional maker-only view. Verbatim strings observed 2026-08-05.
          #     Yields ~4% of the calendar; prefer the building-wide filter above.
          # location_contains: ["Makerspace", "Floor 7", "FL 7", "Floor7",
          #                     "Arts & Music", "Arts and Music", "Hard Tech & Robotics"]
      - adapter: ics
        url: https://api.lu.ma/ics/get?entity=calendar&id=cal-3QxzLUEwOpGxxNc
        label: luma-biopunk-community
        trust: 60
        verified: true   # 2026-08-05: 200 text/calendar, 121 VEVENTs, 49 at 995 Market
        notes: >
          F08 Neuro & Biotech partner community. Multi-venue, so the location filter
          is mandatory. Low current volume; drop it if it stops earning its keep.
        filters:
          location_contains: ["Frontier Tower", "995 Market", "Berlinhouse"]
```

## Research log

- 2026-08-05 — Fetched `luma.com/frontiertower` (200, 1.09 MB), parsed `__NEXT_DATA__`, extracted calendar id `cal-Sl7q1nHTRXQzjP2`; confirmed the iCal subscription endpoint `https://api.lu.ma/ics/get?entity=calendar&id=cal-Sl7q1nHTRXQzjP2` returns 200 `text/calendar` / 262 VEVENTs (`content-disposition: FrontierTowerSF.ics`). Re-fetched to confirm stability.
- 2026-08-05 — Cross-checked the ICS against `api.lu.ma/calendar/get-items?...&period=future` and against `event_start_ats` (1,150 entries) from `__NEXT_DATA__`; built a month histogram that exposed the ICS truncation gap (2025-10 → 2026-07 present in the API, absent from the ICS) and confirmed 26–27 upcoming events either way.
- 2026-08-05 — Tabulated every verbatim `LOCATION` string in the ICS with counts; found 44 distinct real strings plus 40 events whose LOCATION is a `luma.com/event/` URL. This is the filtering evidence above.
- 2026-08-05 — Hunted per-floor sub-calendars: probed ~40 Luma slugs (`frontiermakerspace`, `ftarts`, `hardtechrobotics`, `humanflourishing`, `dacc`, `spaceship`, …), resolved five that exist (`berlinhouse`, `makerspace`, `frontierbio`, `frontiersyndicate`, `frontiertowersf`, `biopunk`), and read each one's `__NEXT_DATA__` calendar object. Only `biopunk` is a genuine Frontier Tower community feed. Also resolved five individual Luma event pages (`monthly-robotics-meetup`, `mass-arts-5`, `vcn-47-fast-local`, `asimov-devlabs-12`, `wild-ai-sf`, `otherminds-community-meet`) — all belong to `cal-Sl7q1nHTRXQzjP2`.
- 2026-08-05 — Fetched `frontiertower.io` home, `/events`, `/about`, `/office`, `/event-guide`, `/start-a-community`, `/developers`, `/apply`, `/sitemap.xml`, `/robots.txt`. Extracted JSON-LD (Organization / CoworkingSpace / FAQPage — **no Event objects**), the `events@` / `support@` / `sponsorship@` addresses, and the Event Organizer Guide rule that all events funnel to lu.ma/frontiertower. Confirmed `/feed/`, `/rss`, `/rss.xml`, `/calendar`, `/contact`, `/press-kit` are 404 and that no Google Calendar iframe exists anywhere.
- 2026-08-05 — Followed the Frontier OS trail: `api.frontiertower.io` root → 401 (auth-gated REST API); npm `@frontiertower/frontier-sdk` v0.25.0 → maintainer `chp@frontiertower.io`, GitHub org `BerlinhouseLabs`; README documents `events:listEvents` / `events:listLocations` permissions. Logged as the best long-term source if they'll grant a key.
- 2026-08-05 — Tested Meetup's published endpoints: `meetup.com/frontier-makerspace/events/rss/` → 200 `application/rss+xml`, valid RSS, **zero items**; `.../events/ical/` → 200 `text/calendar`, zero VEVENTs. No page scraping performed.
- 2026-08-05 — Negative checks: Eventbrite organizer 404; no Ticket Tailor; Mastodon (`sfba.social`, `mastodon.social`) and Bluesky RSS all 404; `api.lu.ma/search/get-results` 401.
- 2026-08-05 — `frontiermakerspace.com`: WHOIS shows an active NameCheap registration (created 2025-06-07, updated 2026-07-28), but `robots.txt` is `Disallow: /`, GETs return a Joken/Cowboy JS bot-challenge stub, and HEAD 302s to `survey-smiles.com`. Marked do-not-crawl.
- 2026-08-05 — Fetched `linktr.ee/frontiertower` to confirm the official channel list (Telegram main + Ethereum House, citizen wiki, app onboarding) and that `lu.ma/frontiertower` is the sole advertised events calendar.
