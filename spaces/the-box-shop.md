# The Box Shop

**ID:** `the-box-shop` · **City:** San Francisco · **Region:** sf
**Address:** 951 Hudson Ave, San Francisco, CA 94124 (current, being vacated) → 1265 Van Dyke Ave, San Francisco, CA 94124 (new, move during 2026)
**Website:** https://boxshopsf.org/
**Status:** moving — still operating and running public events at 951 Hudson Ave as of Aug 2026; lease ends 2026 with no renewal, relocating to a purchased property at 1265 Van Dyke Ave.
**Last researched:** 2026-08-05

## Summary

The Box Shop is a collaborative industrial-arts workspace and studio compound in Bayview-Hunters Point, founded by sculptor Charles Gadeken in 2003. It is the fabrication home of large-scale Burning Man and public art — *Entwined*, Flaming Lotus Girls, Dana Albany, dozens of mutant vehicles — with 100+ studio artists and shipping-container studios plus outdoor yard space. Public event volume is low but steady: roughly monthly open houses / maker markets plus a handful of larger fundraiser parties and art previews, on the order of **8–12 public events per year**. Their own site self-describes as "Where San Francisco builds big art. Home to cocktail nights, artist salons, open studios, and the occasional warehouse rager."

Canonical name is **The Box Shop** (site title "Box Shop San Francisco", Instagram display name "Box Shop SF"). **Do not confuse with "The Box SF"** (`theboxsf.com`, Eventbrite org `326800703`) — a completely unrelated vintage event space / antique shop downtown that also sells tickets in SF.

## Move status

Confirmed:

- **Old address:** 951 Hudson Ave, SF 94124, near India Basin Shoreline Park / Hunters Point Blvd. Still the address printed on every page of `boxshopsf.org` as of 2026-08-05, and still the `LOCATION:` on every VEVENT in their Luma ICS.
- **New address:** **1265 Van Dyke Ave, San Francisco, CA 94124** — purchased (escrow closed), 6,300 sq ft warehouse + 7,200 sq ft outdoor yard. Reported by [SFist 2025-02-13](https://sfist.com/2025/02/13/facing-eviction-the-birthplace-of-entwined-and-other-burning-man-art-has-found-a-new-home/) and [SF Examiner / SF Weekly](https://www.sfweekly.com/art/box-shop-fundraiser-halfway-to-goal-for-big-bayview-move/article_4a90b9ca-e695-4076-8189-1822ac9cca27.html). A `1265 Van Dyke, LLC` (CA entity 202565418440) exists, consistent with the purchase.
- **Their own words, on `boxshopsf.org` homepage (fetched 2026-08-05):** "The Box Shop's lease will end in **2026** with no option to renew… **In 2026, we're moving to our new home on Van Dyke Avenue.**" and "We have already raised over $2.3 million… we still need to raise $3 million to close the remaining gap."
- **Move-related events on their own Luma calendar:** `Van Dyke Town Hall` (2026-02-02), `Exit Through The Box Shop: Opulent Temple x Box Shop` (2026-05-30, billed as a farewell/fundraiser), `Playa Preview: Final Open House` (2026-07-12).
- **Still operating at Hudson.** Their one upcoming listed event, `Mutant Zoo: Feathers and Fur!` on **2026-08-08**, is at the Hudson Ave site and its own copy reads: "As The Box Shop prepares to leave its longtime home for a permanent new location, this is a chance to experience one of San Francisco's largest art spaces before this era comes to an end."
- **Fundraiser is live and public:** https://givebutter.com/v7T7Qp — "Save The Box Shop", organized by **The Box Shop, 501(c)(3) Public Charity, EIN 88-4154586**. Portal showed **$821,045.24 raised** on 2026-08-05 (this is only the public donation portal; total campaign incl. grants is reported at ~$2.8M of $5.5M). A $1.7M state grant came via State Sen. Scott Wiener.

Could NOT confirm:

- **The actual hand-over date.** Sources say variously "fall 2025", "this fall", "by end of 2026". Their site says only "in 2026". No published date for last day at Hudson or first day at Van Dyke.
- **Whether Van Dyke is open yet.** No event has ever been listed at a Van Dyke address in any feed I fetched. Assume Hudson through at least Aug 2026.
- **Whether the website address will be updated at move time.** Treat `951 Hudson Ave` on the site as *stale-by-design* and re-check before the calendar publishes a location.
- **Nonprofit/steward naming.** Press refers to a steward entity **"Qbox"**; Givebutter says the 501(c)(3) is literally "The Box Shop". Both may be true (for-profit shop + nonprofit arm, per SF Standard). Not resolved.

## Verified feeds

All fetched directly on 2026-08-05.

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `ics` | `https://api.lu.ma/ics/get?entity=calendar&id=cal-Jtv8unXGuMkfqFs` | 200 | `text/calendar; charset=utf-8` (5,956 B) | **11 VEVENTs**, real `DTSTART`s from 2025-07-13 to 2026-07-12 | **Best source.** Their own Luma calendar (`luma.com/boxshop`, `cal-Jtv8unXGuMkfqFs`). Has `SUMMARY`, `LOCATION` (full street address), `GEO`, `ORGANIZER`, stable `evt-*@events.lu.ma` UIDs. ⚠️ **0 upcoming events right now** — newest is 2026-07-12. Will trip a naive "source returned 0" health gate. |
| `jsonld` | `https://boxshopsf.org/events/<slug>` (e.g. `/events/flg-heavy-pettng-zoo-benefit`) | 200 | `text/html` | 1 (`@type: Event`, `startDate: 2026-08-08T18:00:00-0700`, `endDate: 2026-08-08T23:59:00-0700`) | **Recommended site adapter.** Squarespace emits clean schema.org/Event on each *detail* page. `location` is empty — hardcode the space address. Get slugs from the RSS row below or from `sitemap.xml`. **robots-allowed.** |
| `rss` (index only) | `https://boxshopsf.org/events?format=rss` | 200 | `application/rss+xml` (4,004 B) | 1 item, real title + `<link>` + full description | ⚠️ **Carries no event date** — `pubDate` is when the post was published (2026-06-29), not the event date. Use it purely to enumerate `/events/<slug>` URLs, then JSON-LD for the dates. **robots-allowed.** |
| `ics` (per-event) | `https://boxshopsf.org/events/<slug>?format=ical` | 200 | `text/calendar` | 1 valid VEVENT, `DTSTART:20260809T010000Z`, `UID:...@squarespace.com` | Works, but one HTTP request per event **and `?format=ical` is `Disallow`ed in their robots.txt**. Prefer JSON-LD. |
| `ics`/`jsonld` (backfill) | `https://boxshopsf.org/past-events?format=rss` / `?format=json` | 200 | `application/rss+xml` (85 KB) / `application/json` (263 KB) | **30 past events** with real epoch-ms `startDate`s back through 2023 | Past only (`upcoming: []`). Useful once for backfill / adapter regression testing. `?format=json` is robots-disallowed; the RSS is not. |
| `jsonld` (site JSON) | `https://boxshopsf.org/events?format=json` | 200 | `application/json` (25,706 B) | 1 upcoming (`startDate: 1786237200711` → 2026-08-08T18:00 PT, `endDate` present) | Cleanest single-request shape (`upcoming[]` / `past[]` arrays). ⚠️ **`?format=json` is explicitly `Disallow`ed in `boxshopsf.org/robots.txt`.** Per the project's own "be a good citizen" rule, do not use it in the nightly job. |

**robots.txt note:** `boxshopsf.org/robots.txt` (Squarespace default) disallows `?format=json`, `?format=json-pretty`, `?format=ical`, `?format=page-context`, `?format=main-content` for **all** user-agents. `?format=rss` and normal page URLs are allowed. The long list of AI user-agents at the top of the file has no `Disallow` line of its own, so it collapses into the `User-agent: *` group — there is no blanket AI block here.

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| `nextdata` | `https://www.eventbrite.com/o/the-box-shop-85448047483` | Their real Eventbrite organizer page (`organizer.id = 85448047483`, `organizer.name = "The Box Shop"`). I confirmed the parse path: `__NEXT_DATA__.props.pageProps.upcomingEvents[]` + `upcomingEventsTotal`. **But `upcomingEventsTotal` was `0` on 2026-08-05**, so no event ever came back. No JSON-LD on the organizer page (unlike Humanmade's). | Re-fetch when they announce a ticketed event and check `upcomingEventsTotal > 0`. |
| — | Whatever page/calendar appears for **1265 Van Dyke** | The new site may get its own Luma calendar, its own Squarespace section, or a new domain at reopening. | Re-run this whole check after the move lands. Watch `luma.com/boxshop` and `boxshopsf.org/sitemap.xml` for new slugs. |
| `llm_html` / low-trust | `https://dothebay.com/venues/the-box-shop-sf` and `https://dothebay.com/venues/the-box-shop` | Two DoTheBay venue slugs exist for them; DoTheBay carries SF art-party listings. `curl` returns 200; WebFetch got 403 so I never read the body. | Fetch with a browser UA and look for a subscribe/ICS link. **Cross-check only, never authoritative.** |
| low-trust | `https://sf.funcheap.com/` (they carried `exit-box-shop-final-party-djs-sf`) | Funcheap covers their big parties secondhand. | Search Funcheap for "Box Shop"; only useful to catch events the space never posted itself. |
| low-trust | SF/Bay Burner regional event listings | Community may repost Box Shop fundraisers. | Not pursued — I found no single canonical SF Burners calendar carrying their events. Genuinely low value given they run their own Luma. |
| human | Newsletter — email signup form on `https://boxshopsf.org/contact` | Likely the first place move/reopening news lands. | Subscribe manually. |

## Dead ends

Everything below was actually fetched on 2026-08-05.

- **`theboxshop.org`** — connection refused on :443. Not theirs.
- **`theboxshopsf.com`** — NXDOMAIN, could not resolve.
- **`boxshopsf.com`** — 200, but 301-redirects to `boxshopsf.org`. It is an alias, not a separate site.
- **Collection-level Squarespace ICS: `boxshopsf.org/events?format=ical` and `/events/?format=ical`** — return **200 but `text/html`** (the rendered page, ~160–205 KB). Squarespace does **not** expose a whole-calendar ICS here; `?format=ical` only works on an individual event URL. This is exactly the silent-empty-calendar failure mode — do not wire it up.
- **`boxshopsf.org/feed`, `/rss`** — 404. Not WordPress.
- **`boxshopsf.org/wp-json/tribe/events/v1/events`** — 404. Not The Events Calendar. No `tec-api-version` anywhere.
- **`boxshopsf.org/?ical=1`** — 200 but `text/html` (the homepage). Not a TEC subscribe link.
- **Google Calendar** — no `<iframe>` and no `calendar.google.com` string anywhere in the homepage, `/events`, `/contact`, or `/past-events` HTML. No embedded gCal exists.
- **`__NEXT_DATA__` on their own site** — none. It is Squarespace 7.1, not Next.js.
- **Bluesky** — `https://bsky.app/profile/boxshopsf.bsky.social/rss` returns **200 but an empty body** (no `<rss>`, no items). `https://bsky.app/profile/boxshopsf.com/rss` → 404. No Bluesky presence found.
- **Mastodon** — `https://sfba.social/@boxshopsf.rss` → 404. No Mastodon presence found.
- **Meetup** — `https://www.meetup.com/boxshopsf/events/rss/` → 404 (`text/plain`). No Meetup group. (Published-endpoint test only; no page scraping.)
- **Luma slug guess `lu.ma/boxshopsf`** → 404. The correct slug is **`boxshop`** (`luma.com/boxshop` → 200).
- **Ticket Tailor / Bookwhen / Withfriends / Zeffy / Patreon / Discord** — zero references in any page HTML I grepped. They use **Eventbrite** for tickets and **Givebutter** for donations.
- **The Instagram link printed on their own site** is `instagram.com/explore/locations/1582657/the-box-shop/` — a *location tag*, not the profile. Do not use it as the handle.
- **`eventbrite.com/o/heavy-petting-zoo-art-car-19767303258`** — this is the organizer of the Aug 8 2026 event *at* the Box Shop. It is a partner collective (Heavy Petting Zoo), **not** the Box Shop. Co-produced events land under the partner's org, which is why the Box Shop's own organizer page reads 0.
- **`theboxsf.com` / Eventbrite org `326800703` ("The Box SF")** — different business entirely. Excluded.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Instagram | `@boxshopsf` | https://www.instagram.com/boxshopsf/ | none | Profile: **yes** (200, og:title `Box Shop SF (@boxshopsf)`) | **7,651 followers, 166 posts, 668 following** as of 2026-08-05. Almost certainly their primary channel for this community, and the place move news breaks first. No public feed — needs an LLM/browser pass later if you want it. Could not read post dates without a logged-in render. |
| Facebook | `boxshopsf` | https://www.facebook.com/boxshopsf/ | none | Page exists (200) | Not machine-readable. Some of their event announcements route through here (found `l.facebook.com` share wrappers pointing at the Givebutter campaign). |
| Luma | `boxshop` | https://luma.com/boxshop | **YES** — `https://api.lu.ma/ics/get?entity=calendar&id=cal-Jtv8unXGuMkfqFs` | **yes** | This is the one real feed. See Verified feeds. |
| Eventbrite | `the-box-shop-85448047483` | https://www.eventbrite.com/o/the-box-shop-85448047483 | `__NEXT_DATA__` only | Page yes, events **no** (0 upcoming) | See Leads. |
| Bluesky | — | — | — | no | Searched/probed, none found. |
| Mastodon | — | — | — | no | Searched/probed, none found. |
| Meetup | — | — | — | no | No group. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| General inquiries | — | email | `info@boxshopsf.org` | `boxshopsf.org/contact`, `/who-we-are` |
| General inquiries | — | web form | https://boxshopsf.org/contact | site nav "Contact Us → General Inquiries" |
| Studio rental | — | web form | https://boxshopsf.org/studioform | site nav "Contact Us → Studio Request Form" |
| Founder / Artist | **Charles ("Charlie") Gadeken** | — | initiated The Box Shop in 2003; personal site `charlesgadeken.com` (has a `/boxshopsf-home` page, 200) | `boxshopsf.org/who-we-are`; SF Standard 2024-09-08 |
| **Events organizer** | **Kyana** | Luma | `ORGANIZER;CN="Kyana"` on every VEVENT in their Luma ICS; listed host on `luma.com/tlkf1vt2`, `/m24szqdm` | Luma ICS `api.lu.ma/ics/get?entity=calendar&id=cal-Jtv8unXGuMkfqFs` |
| Co-host | **Alita Edgar** | Luma | co-host on Box Shop open houses ("Hosted by Kyana & Alita Edgar") | same ICS; `luma.com/playapreview` `__NEXT_DATA__` hosts |
| Best DM channel | — | Instagram | https://www.instagram.com/boxshopsf/ (`@boxshopsf`) | verified profile fetch |
| Donations / nonprofit | The Box Shop, 501(c)(3), **EIN 88-4154586** | Givebutter | https://givebutter.com/v7T7Qp | Givebutter campaign page |
| Physical (current) | — | — | 951 Hudson Ave, San Francisco, CA 94124 | site + every Luma VEVENT `LOCATION` |
| Physical (new) | — | — | 1265 Van Dyke Ave, San Francisco, CA 94124 | SFist, SF Examiner/SF Weekly, homepage copy |

**Best outreach path:** Email `info@boxshopsf.org` and explicitly ask for **Kyana**, who is the named `ORGANIZER` on every event in their Luma calendar and is therefore the person who actually maintains the schedule. The ask is easy and low-effort for them — *keep posting to `luma.com/boxshop` and we'll pick it up automatically; just tell us the new Van Dyke address when you're in* — because their Luma feed already works and the only gap is that some events (Eventbrite-ticketed, partner-collective ones like Mutant Zoo) never get mirrored onto it. If email goes unanswered, DM `@boxshopsf` on Instagram; for a Burning-Man-adjacent art space with 7.6k IG followers and 166 posts, that is a live channel and email may not be.

## Recommended `sources.yaml` entry

```yaml
  - id: the-box-shop
    name: The Box Shop
    city: San Francisco
    region: sf
    url: https://boxshopsf.org/
    notes: >
      Moving during 2026 from 951 Hudson Ave to 1265 Van Dyke Ave, both SF 94124.
      Do NOT publish a hardcoded address after the move without re-checking; the
      website and the Luma VEVENT LOCATION fields both still say Hudson Ave.
      Low volume (~8-12 public events/year: monthly-ish open houses plus big
      fundraiser parties), so expect long stretches with zero upcoming events.
      Exempt this space from the "0 events when previous run had >0" health gate,
      or it will alert every month.
    sources:
      - adapter: ics
        url: https://api.lu.ma/ics/get?entity=calendar&id=cal-Jtv8unXGuMkfqFs
        label: luma
        trust: 100
        verified: true   # 2026-08-05: 200 text/calendar, 11 VEVENTs, real DTSTARTs
                         # (2025-07-13 .. 2026-07-12). 0 UPCOMING as of that date.
      - adapter: jsonld
        url: https://boxshopsf.org/events?format=rss
        label: squarespace-events
        trust: 95
        verified: true   # 2026-08-05: RSS 200 application/rss+xml, 1 item.
        notes: >
          Two-step adapter. The RSS itself carries NO event date (pubDate is the
          post date), so use it only to enumerate item <link> URLs, then GET each
          https://boxshopsf.org/events/<slug> and parse the schema.org/Event
          JSON-LD, which has correct startDate/endDate with -0700 offsets.
          Confirmed on /events/flg-heavy-pettng-zoo-benefit -> 2026-08-08T18:00:00-0700.
          JSON-LD "location" is empty; fill the address from this space record.
          Catches Eventbrite-ticketed partner events (e.g. Flaming Lotus Girls /
          Heavy Petting Zoo) that never reach the Luma calendar.
          DO NOT use ?format=json or ?format=ical: both are Disallow'ed in
          https://boxshopsf.org/robots.txt. ?format=rss is allowed.

      # --- Not wired up ---
      # Eventbrite organizer page. Real and parseable via __NEXT_DATA__ at
      # props.pageProps.upcomingEvents[] / upcomingEventsTotal, but returned
      # upcomingEventsTotal: 0 on 2026-08-05, so nothing was ever confirmed.
      # Enable only after seeing a nonzero count.
      # - adapter: nextdata
      #   url: https://www.eventbrite.com/o/the-box-shop-85448047483
      #   label: eventbrite-organizer
      #   trust: 80
      #   verified: false
```

## Research log

- 2026-08-05 — Searched for the space and disambiguated it from **The Box SF** (`theboxsf.com`), an unrelated downtown event space. Established canonical domain by probing four candidates: `theboxshop.org` (connection refused), `theboxshopsf.com` (NXDOMAIN), `boxshopsf.com` (301 → `boxshopsf.org`), `boxshopsf.org` (200). Site title is "Box Shop San Francisco"; the org calls itself "The Box Shop".
- 2026-08-05 — Identified the platform as **Squarespace 7.1** (no WordPress, no Tribe, no `__NEXT_DATA__`, no gCal iframe). Probed 13 endpoints with `curl -w '%{http_code} %{content_type} %{size_download}'`: `/feed`, `/rss`, `/wp-json/tribe/events/v1/events` all 404; `?ical=1` and `/events?format=ical` both 200-but-`text/html` (the classic silent-empty trap); `?format=json`, `?format=rss` both real. Pulled `sitemap.xml` (2 event collections: `/events` with 1 slug, `/past-events` with ~38).
- 2026-08-05 — Parsed `/events?format=json` (1 upcoming: Mutant Zoo, 2026-08-08, epoch-ms start/end; `location` lat/lng is Squarespace's unset New York default, ignore it) and `/past-events?format=json` (30 past events with real dates). Confirmed `<script type="application/ld+json">` `@type: Event` on the *detail* page but only `@type: WebSite` on the list page. Read `robots.txt`: `?format=json` and `?format=ical` are Disallow'ed for all agents, `?format=rss` is not — so the compliant path is RSS-for-slugs → JSON-LD-for-dates.
- 2026-08-05 — Grepped every page's HTML for outbound platform links. That surfaced **Luma** (`luma.com/playapreview`, `/muralfest`, `/tlkf1vt2`, `/m24szqdm`, `/s7vecpgs`, `/wxx2nznb`) — not mentioned anywhere in the visible site nav. Guessed `luma.com/boxshopsf` (404), then found `luma.com/boxshop` (200) and pulled `calendar.api_id = cal-Jtv8unXGuMkfqFs` out of its `__NEXT_DATA__`. Tested `https://api.lu.ma/ics/get?entity=calendar&id=cal-Jtv8unXGuMkfqFs` → **200 `text/calendar`, 11 VEVENTs with real DTSTARTs, full street addresses, GEO, stable UIDs.** This is the single best source and is not linked from their website at all.
- 2026-08-05 — Probed social feed endpoints: Bluesky `boxshopsf.bsky.social/rss` (200 but empty body), `boxshopsf.com/rss` (404), Mastodon `sfba.social/@boxshopsf.rss` (404), Meetup `meetup.com/boxshopsf/events/rss/` (404). Confirmed Instagram `@boxshopsf` via `og:` meta (7,651 followers / 166 posts) — note their own site links a *location tag* URL, not the profile.
- 2026-08-05 — Traced Eventbrite: the Aug 8 event belongs to `heavy-petting-zoo-art-car-19767303258`, a partner collective. Found the Box Shop's real organizer page at `/o/the-box-shop-85448047483` and confirmed via `__NEXT_DATA__` that `organizer.name = "The Box Shop"` and `upcomingEventsTotal = 0`. No JSON-LD on Eventbrite organizer pages anymore, so this needs the `nextdata` adapter, not `jsonld`.
- 2026-08-05 — Move research: fetched SFist (2025-02-13), Mission Local (2026-05), SF Standard (2024-09-08), plus SF Examiner/SF Weekly via search (the Examiner URL itself 429'd). Read `boxshopsf.org/save-the-box-shop` and the homepage directly for their own current framing. Fetched `givebutter.com/v7T7Qp` and extracted `raised: 821045.24` and the 501(c)(3) EIN. Old and new addresses confirmed from three independent sources; the actual move date is not published anywhere I could find.
