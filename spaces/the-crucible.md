# The Crucible

**ID:** `the-crucible` · **City:** Oakland · **Region:** east-bay
**Address:** 1260 7th St, Oakland, CA 94607
**Website:** https://www.thecrucible.org/
**Status:** active — 216 live courses and 271 class offerings inside a 120-day horizon; leadership is in transition (Doug Yeiser, *Interim* Executive Director) after a 2026 financial crunch that was resolved by a $500K Kelson Foundation match completed 2026-02-05.
**Last researched:** 2026-08-05

## Summary

The Crucible is the largest nonprofit industrial-arts education center in the US — a 56,000 sq ft facility in West Oakland running blacksmithing, welding, glass blowing, flameworking, fusing, ceramics, jewelry, enameling, foundry, neon, leather, woodworking, kinetics/electronics and a bike shop. Its output is overwhelmingly a **paid class catalog**: 216 distinct courses with 478 scheduled offerings, of which **271 fall inside a 120-day horizon (~68/month)**. That makes it the second-largest catalog in the registry after Maker Nexus, but far cleaner — there are no equipment-safety checkouts or orientation entries to filter out. Large public events (Fire Arts Festival, Hot Couture, Fire & Light Soirée) are **historical**: the current public-event program is much smaller — free monthly tours on Eventbrite, plus occasional one-offs (Gifty holiday craft show, Heavy Metal artisan fair, iron pours, community bike rides) announced as blog-style posts with **no structured dates at all**.

The site is WordPress + WooCommerce (Avada theme). **The Events Calendar is not installed** — the `tribe-*` CSS classes on the page are inert Avada compatibility styles, and every Tribe endpoint 404s. Classes are WooCommerce *variable products* whose variations are keyed on a `pa_class-date` attribute carrying the exact start datetime. Two independent machine-readable routes expose that data and they cross-validate to within 3 events.

## Verified feeds

Only sources I personally fetched and confirmed return real events.

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `embedded_json` (**new adapter — see below**) | `https://www.thecrucible.org/course-search/` → `<script id="ac-course-data" type="application/json">` | 200 | `text/html` (blob is `application/json`) | 216 courses / **478 offerings** / **271 inside 120 days** | **Best source.** 132 KB blob, the entire live catalog. Fields: `id`, `title`, `url`, `price`, `member_price`, `department`, `audience`, `level`, `day[]`, `format[]`, `time_of_day[]`, `hours`, `start_dates[]` (**Unix epoch seconds**), `next_date`, `next_date_txt`, `stocks[]`, `is_in_stock`, `image_url`. Timestamps decode **exactly** to the product page's `Class.Date` values (verified: `1786064400` → `08/06/26 6:00 PM`, page says `08/06/26 6:00 pm`). Stable `id` = WP post ID → use `{space_id}:{id}:{start_epoch}` as UID. |
| `embedded_json` | `https://www.thecrucible.org/shop/` (same `ac-course-data` blob) | 200 | `text/html` | 216 — **byte-identical to `/course-search/`** | Confirmed identical via Python object equality. Mirror only; do **not** ingest both. |
| `json` | `https://www.thecrucible.org/wp-json/wc/store/v1/products?per_page=100&orderby=date` | 200 | `application/json` | **353 products**, 1531 `pa_class-date` terms, **274 inside 120 days** | WooCommerce Store API v1, open and unauthenticated. Paginate on `X-WP-TotalPages` (4 pages). Superset of the blob — covers **all 216** blob course IDs plus dormant/retired products. Richer: full `description`, `short_description`, `categories`, `prices`, `is_in_stock`, `permalink`, per-variation IDs. Dates live in `attributes[].terms[].name` as `MM/DD/YY H:MM am/pm` local. **274 vs the blob's 271 — independent cross-validation.** |
| `nextdata` | `https://www.eventbrite.com/o/the-crucible-2700512180` | 200 | `text/html` | **7 upcoming** | `props.pageProps.upcomingEvents`, `upcomingEventsTotal: 7`, `hasMoreUpcoming: false`. All 7 are **"Free Crucible Tour"** (2026-08-20 → 2026-11-19). Real `start_date` + `start_time` + `timezone: America/Los_Angeles` + full `primary_venue.address` (`1260 7th St, Oakland, CA 94607`, lat/long). **This is the only source for their free public tours** and the only one carrying a location string. |
| `rss` | `https://www.thecrucible.org/category/upcoming-events/feed/` | 200 | `application/rss+xml` | 3 items | Curated "currently promoted public events" — exactly matches the `/events/` page's Upcoming section. **`pubDate` is the POST date, not the event date** (e.g. "Mosaic Heart Rocks (JUL 16)" has `pubDate: Tue, 14 Jul 2026`). Not an event source on its own; it is the correct **seed list for `llm_html`** and a change-detection signal. Filtering is real — bogus category 404s (see Dead ends). |

Human-channel feeds (real, but not event data):

| Adapter | URL | HTTP | Content-Type | Items | Notes |
|---|---|---|---|---|---|
| `rss` | `https://www.thecrucible.org/feed/` | 200 | `application/rss+xml` | 10 posts, newest 2026-06-30 | Blog/press releases. Low cadence. Where the financial-recovery and leadership news appeared. |
| `rss` | `https://www.thecrucible.org/event/feed/` | 200 | `application/rss+xml` | 10 items | The `event` CPT archive feed. `pubDate` is the publish date; the actual event date appears only inside the title text (`(JUL 16)`) or the prose body. Change detection only. |
| `rss` | `https://www.youtube.com/feeds/videos.xml?channel_id=UCRreKq5nHbkGnuaa7-VRSuw` | 200 | `text/xml` | 15 entries | Studio/tutorial video. Not events. |

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| `llm_html` | The 3 `/event/<slug>/` URLs from `category/upcoming-events/feed/`, fetched and passed to the local model | The public-events program has **no structured dates anywhere** — dates live only in prose (`Thursdays @ 6-8 PM`, then a bullet list of dates). This is exactly the Tier C case `llm_html` exists for. | Run one page (`/event/free-public-tours/`) through the extractor and check it recovers all 20 tour dates it lists in prose. |
| `nextdata` | `https://www.eventbrite.com/cc/gather-make-the-crucible-4809924` | Second Eventbrite collection listed on the organizer page ("Gather & Make"). Currently contributes 0 to `upcomingEventsTotal`, so it is either empty or feeds the same 7 tours. | Fetched: 200, 370 KB, **but no `__NEXT_DATA__` block** — collection pages use a different shell than organizer pages. Re-check with a DOM-aware fetch, or just rely on the organizer page since `hasMoreUpcoming: false`. |
| — | **Salesforce** as their system of record | Staff page lists a **"Salesforce Data Coordinator" (Omar Abdelmegeid)**. If class scheduling lives in Salesforce and WooCommerce is only the storefront, a Salesforce-side export would be authoritative and would carry per-meeting session times the website never exposes. | Ask. Do not probe. |
| — | Per-meeting session times for multi-week courses | The catalog gives one start datetime per *offering*; a 5-week, 10-meeting course is a single entry. Meeting-level times exist somewhere (students get a schedule). | Ask `registrar@thecrucible.org` whether a per-session export exists. |
| — | Whether `stocks[]` is index-aligned with `start_dates[]` | Both are arrays on each course and both had length 2 on the sampled course, which would let us publish remaining-seat counts. **Not verified** — do not assume the ordering matches. | Compare `stocks`/`start_dates` pairing against the Store API's per-variation stock for 5+ courses. |
| `json` | `https://www.thecrucible.org/wp-json/wc/store/v1/products/<id>/…` variation endpoints | Store API exposes `variations[].id` but the listing does not include per-variation stock or price. | Check whether `wc/store/v1/products?type=variation` or a `/variations` sub-route exists. |

## Dead ends

Things I checked that do **not** work. Several are the silent-failure kind this project cares about.

- **`https://www.thecrucible.org/wp-json/tribe/events/v1/events`** (also `/categories`, `/venues`) — **HTTP 404**. The Events Calendar is **not installed**. The `tribe-events-list-widget` / `tribe-bar-form` strings on their pages are inert **Avada theme compatibility CSS**, not plugin output — confirmed by grepping `wp-content/plugins/` for any `events`/`tribe` path (zero hits) and by finding the strings only inside `<style>` selectors. **Do not register a `tribe_rest` source for this space on the strength of those class names.**
- **`https://www.thecrucible.org/?post_type=tribe_events&ical=1`** — **HTTP 200 with `text/html`**, 1.4 MB. Serves a rendered page, not a calendar. The classic trap.
- **`https://www.thecrucible.org/events/?ical=1`** — **HTTP 200 with `text/html`**. Diffed against `/events/` with no query string: only **20 differing lines**, all cachebusters (`ExactMetricsDefaultLocations`, `?ver=` on a JS bundle, `__wc_fb_page_generated`). Functionally the same page. A byte-comparison alone would say "different" and mislead you — compare *content*, not bytes.
- **`https://www.thecrucible.org/events/list/`** — HTTP 404 (serving a 1.36 MB HTML error page).
- **`https://www.thecrucible.org/events/feed/`** — HTTP 200 but a **670-byte empty comments feed**. The events base slug is `/event/` (singular) for the CPT and `/events/` is a plain page.
- **`https://www.thecrucible.org/?post_type=tribe_events&feed=rss2`** — **HTTP 301**. Redirects to `/feed/`, the blog. Do not mistake the redirect target's 10 real items for event data.
- **Bogus category feed control — `https://www.thecrucible.org/category/this-does-not-exist-zzz/feed/`** — returns **HTTP 404 with `Content-Type: application/rss+xml` and a 1.35 MB body that is actually an HTML error page**, 0 `<item>`s. A *new* variant of the project's content-type-lies collection: here the **header lies** and both the status and the parse result are needed to reject it. This control does confirm `/category/upcoming-events/feed/` is genuinely filtered (3 items) rather than silently falling back to the full list (10 items).
- **Woo Store API default ordering — `?per_page=100` with no `orderby`** — returns **HTTP 200, valid JSON, `X-WP-Total: 98`**. Adding **`orderby=date` returns `X-WP-Total: 353`**, which matches `product-sitemap.xml` (354 URLs). The default catalog query silently drops **72% of products** and covers only 40 of the blob's 216 live courses. **This is the single most dangerous finding for this space: a fetcher that omits `orderby=date` gets a 200, valid JSON, and a quietly amputated catalog.** Negative controls run: bogus product ID `99999999` → clean 404 JSON; `page=99` → 200 with body `[]` (not an error, so paginate on `X-WP-TotalPages`, never until-error).
- **`schema.org/Event` JSON-LD** — **none anywhere on the site.** Product pages emit exactly one `schema.org/Course` node (Yoast), and it has **no `startDate`, no `offers`, no `hasCourseInstance`** — just `name`, `description`, `provider`. Registering `jsonld` against a product page would return zero events forever without erroring. `/event/` and `/events/` pages emit only `WebPage`/`Organization`/`BreadcrumbList`.
- **Server-side filtering on `/course-search/`** — there is none. Negative control: `?department=Blacksmithing`, `?department=ZZZ-NOT-A-DEPT` and `?ac_department=blacksmithing` **all return the identical 216-course blob**. Filtering is 100% client-side JavaScript over the embedded blob. There is **no XHR/fetch endpoint at all** — the only `admin-ajax.php` references belong to Popup Maker and Facebook-for-WooCommerce.
- **Search-index platforms** — no Algolia index, no Elasticsearch, no Typesense, no FacetWP/SearchWP. The single `algolia` string on the page is an unrelated vendor mention.
- **`__NEXT_DATA__` on thecrucible.org** — none; it is WordPress/PHP.
- **Google Calendar** — zero `calendar.google.com` occurrences across the homepage, `/events/`, `/classes/`, `/shop/`, `/course-search/` and `/contact-hours-location/`. The only iframes are Google Maps. No public gCal exists to derive a `basic.ics` from.
- **Luma** — no `lu.ma`/`luma.com` links anywhere on the site. `api.lu.ma/ics/get?entity=calendar&id=cal-thecrucible` → 404 JSON (probe of a *shape*, not a claim of a real calendar).
- **Meetup** — `https://www.meetup.com/the-crucible/events/rss/` → **404**. No Meetup group.
- **Mastodon** — `https://sfba.social/@thecrucible.rss` → 404. No account found.
- **Bluesky** — `https://bsky.app/profile/thecrucible.org/rss` → 404; `thecrucible.bsky.social` → 403. No account found; no Bluesky link on the site.
- **Arts-nonprofit registration/CRM platforms** — grepped the catalog, shop, events and contact pages for **ASAP Connected, Sawyer, Jackrabbit, ACTIVE, CourseStorm, Regpack, Ungerboeck, Altru, Blackbaud, Neon CRM, Bloomerang, Tessitura, Ticket Tailor, Mindbody, Acuity**: **zero hits for all of them.** Registration is in-house **WooCommerce**; the back-office CRM appears to be **Salesforce** (see Leads). Eventbrite is used only for the free tours.
- **`wp-json/wp/v2/roster`** — HTTP 200 with `[]`. The `roster` CPT is REST-exposed but empty; staff data is rendered from a separate `person` CPT that is **not** REST-exposed (absent from `wp-json/wp/v2/types`).

## Volume and filtering

**Sessions/month:** ~68 class offerings per month inside the horizon. In-horizon breakdown (2026-08-05 → 2026-12-03): **Aug 70, Sep 76, Oct 79, Nov 46** — 271 total. Raw counts are much larger and misleading: the blob holds **478** `start_dates` and the Store API holds **1531** `pa_class-date` terms, most of them historical going back to 2022.

**Publishing horizon:** the catalog runs to **2026-12-20**, i.e. roughly **4.5 months ahead**. The Nov/Dec taper is a real publishing boundary, not a decline — expect the far end of a 120-day window to thin out legitimately, same as Maker Nexus. `allow_zero` is **not** appropriate here; this space should never be near zero.

**Courses appear ONCE per offering, not per meeting.** This is the good case. Each element of `start_dates` is one *run* of a course, and multi-week courses collapse into a single entry:
- `Glass Fusing and Slumping Lab - 5 weeks` — `hours: 20.0`, `day: ["Tuesday; Thursday"]`, **1 start_date** (i.e. ~10 meetings → 1 event).
- `Youth Blacksmithing Immersion` — `hours: 35.0`, `day: ["Monday to Friday"]`, **1 start_date** (5 meetings → 1 event).
- `Woodworking I` — `hours: 40.0`, **5 start_dates** = five separate runs of the course across the year.

Offerings-per-course distribution: `{0: 9, 1: 101, 2: 36, 3: 27, 4: 16, 5: 10, 6: 11, 7: 4, 8: 2}`.

**Consequences for normalization:**
- **`hours` is total course hours, not one meeting's duration.** Do **not** compute `DTEND = DTSTART + hours` — a 40-hour course would render as a 40-hour block. Publish as a timed start with no reliable end, or derive an end only for single-meeting courses (`format: Weekend`/`Weekday` with `hours <= 8`).
- **9 courses have an empty `start_dates` array** (e.g. `Metal Furniture Fabrication`, `Foundry Fundamentals`). Skip, do not emit a dateless event.
- **27 courses have `is_in_stock: false`** but still carry future dates. Decide explicitly whether a sold-out class belongs in a public calendar; default to keeping it (it is still a real public event).
- **Store API sentinel:** two `pa_class-date` terms are literally **`01/01/70 12:00 am`**. Naive two-digit-year parsing maps these to **2070**, which lands them past every horizon check without erroring. Drop the epoch sentinel explicitly.

**What would swamp the calendar:** very little, and this is the pleasant surprise. There are **no equipment-safety checkouts, no orientations, no private-rental or team-build entries, and no per-meeting duplication** in the public catalog — none of the Maker Nexus problems. The closest thing is **17 open-studio "Lab" courses** (`Blacksmithing Lab`, `Jewelry Lab`, `Neon Lab`, …), which are legitimate recurring studio-access sessions and probably worth keeping. Youth programming is only **6 of 271** in-horizon offerings. **No filter is required for this space.** An optional department or audience filter is documented below for a maker-only view.

**Data-hygiene gotcha:** WooCommerce product *categories* are polluted — 60+ of the 118 distinct category names are one-off per-product categories identical to the class title (`Wheel Throwing II`, `Youth Kandi Cuffs and Perlers`, …). Filter on the blob's **`department`** field, never on Store API `categories`.

Verbatim `department` values observed (from `ac-course-data`, **exactly as they appear, HTML entities included**):
- `Jewelry` (26 courses)
- `Woodworking` (25)
- `Ceramics` (20)
- `Glass Flameworking` (20)
- `Welding` (19)
- `Glass Blowing` (19)
- `Enameling` (18)
- `Blacksmithing` (18)
- `Glass Fusing &amp; Slumping` (12) ← **literally contains `&amp;`, not `&`**
- `Leather` (12)
- `Foundry` (9)
- `Kinetics &amp; Electronics` (5)
- `Bike Shop` (5)
- `Neon &amp; Light` (3)
- `Moldmaking` (2)
- `Fire &amp; Performance` (1)
- `Stoneworking` (1)
- `Machine Shop` (1)

Verbatim `department` **taxonomy slugs** (from `department-sitemap.xml`, 22 terms — note these do **not** all match the blob values, e.g. `leather-textiles-fine-art` vs blob `Leather`):
- `artist-resources` `bike-shop` `blacksmithing` `ceramics` `enameling` `fire-performance` `foundry` `glass-blowing` `glass-casting-coldworking` `glass-flameworking` `glass-fusing-slumping` `jewelry` `kinetics-electronics` `leather-textiles-fine-art` `machine-shop` `moldmaking` `neon-light` `stoneworking` `team-builds` `textiles-leather-fine-art` `welding` `woodworking`

Verbatim `audience` values: `Ages 16+` (147), `Ages 12-18` (27), `Ages 18+` (24), `Ages 14-18` (7), `Ages 8-11` (6), `Family` (5)
Verbatim `level` values: `Entry Level` (157), `Continuing` (59)
Verbatim `format` values: `Weekend` (125), `Weekday` (89), `Weeklong` (50)
Verbatim `time_of_day` values: `Evening` (83), `All Day` (78), `Morning` (72), `Afternoon` (63)
Verbatim `day` values: `Saturday`, `Sunday`, `Monday to Friday`, `Saturday; Sunday`, `Thursday`, `Wednesday`, `Tuesday`, `Monday`, `Tuesday; Thursday`, `Friday`, `Monday; Tuesday; Wednesday; Thursday`, `Monday; Wednesday`, `Tuesday; Wednesday; Thursday`, `Monday, Tuesday, Wednesday, Thursday ` ← **last one uses commas not semicolons and has a trailing space; a naive `split('; ')` will mis-parse it**

Verbatim location strings observed:
- **The class catalog has NO location field of any kind** — not in `ac-course-data`, not in the Store API, not in the product HTML. Every class is at the single facility. `address_override` is **mandatory** for these sources.
- `1260 7th St, Oakland, CA 94607` — Eventbrite `primary_venue.address.localized_address_display`
- `1260 7th St` / `Oakland, CA 94607` — Eventbrite `localized_multi_line_address_display`
- `The Crucible` — Eventbrite `primary_venue.name` (venue id `296566147`, lat `37.804989`, long `-122.290605`)
- `1260 7th St. Oakland, CA 94607` — as written on `/contact-hours-location/` (note the **period** after `St`)

Because there is exactly one venue and no per-event location data, **do not write a `location_contains` filter for this space.** There is nothing to filter against and it would drop everything.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Instagram | `@the_crucible` | https://www.instagram.com/the_crucible/ | none | Link present sitewide (footer) | Almost certainly the live announcement channel, as with most spaces in this registry. Best DM target. |
| Facebook | `thecrucible94607` | https://www.facebook.com/thecrucible94607/ | none | Link present sitewide; also in their `schema.org/Organization` `sameAs` | Listed in JSON-LD `sameAs`, so it is the org's own declared profile. |
| YouTube | `@TheCrucibleOakland` (`UCRreKq5nHbkGnuaa7-VRSuw`) | https://www.youtube.com/@TheCrucibleOakland | `https://www.youtube.com/feeds/videos.xml?channel_id=UCRreKq5nHbkGnuaa7-VRSuw` | **Yes** — 200, 15 entries | Studio/tutorial content. Not events. |
| X / Twitter | `@TheCrucible` | https://twitter.com/TheCrucible | none | Link present; also in `schema.org/Organization` `sameAs` (as `x.com/TheCrucible`) | Declared in their own JSON-LD. Activity not assessed. |
| Eventbrite | `the-crucible-2700512180` | https://www.eventbrite.com/o/the-crucible-2700512180 | `__NEXT_DATA__` (see Verified feeds) | **Yes** — 200, 7 upcoming | Used only for the free monthly tours. Bio confirms "nation's largest nonprofit industrial arts education center", 56,000 sq ft. |
| Mastodon | — | — | — | Not found (404) | No account discovered. |
| Bluesky | — | — | — | Not found (404 / 403) | No account discovered. |
| Meetup | — | — | — | Not found (404) | No group. |
| LinkedIn / TikTok | — | — | — | Not found | No links anywhere on the site. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| General enquiries | — | Email | `info@thecrucible.org` | `mailto:` on `/contact-hours-location/`, `/about-the-crucible/`, `/people/staff/` |
| Registration / class enrolment | — | Email | `registrar@thecrucible.org` | `/contact-hours-location/` body text. **NB: the page's own `mailto:` link is misspelled `registrar@thecrucibile.org`** (extra `i`). Use the spelling in the visible text; flag the typo to them as a friendly opener. |
| Private events, rentals, team builds | — | Email | `private@thecrucible.org` | `mailto:` on `/contact-hours-location/` |
| Main line | — | Phone | (510) 444-0919 | `/contact-hours-location/` |
| Interim Executive Director | Doug Yeiser | via `info@` | no direct address published | `/people/staff/` |
| **Senior Programs Manager** | **Melissa Gray** | via `info@` | no direct address published | `/people/staff/` |
| Community Program Manager | Ismael Plasencia | via `info@` | no direct address published | `/people/staff/` |
| Youth Program Manager | Samantha Espinoza | via `info@` | no direct address published | `/people/staff/` |
| Program Manager (Open For Business, CREATE, ERV) | David Banks | via `info@` | no direct address published | `/people/staff/` |
| Development Director | Anne-Marie Stephenson | via `info@` | no direct address published | `/people/staff/` |
| Director: Studio Operations & Facilities | Cfay Irons | via `info@` | no direct address published | `/people/staff/` |
| Systems and Purchasing Studio Manager | Jesse Hamrah | via `info@` | no direct address published | `/people/staff/` |
| **Salesforce Data Coordinator** | **Omar Abdelmegeid** | via `info@` | no direct address published | `/people/staff/` — the technical counterpart if they want to push a feed rather than have us pull one |
| Blacksmithing Department Head | Aaron Williams | via `info@` | no direct address published | `/people/` |
| Neon & Light Department Co-Head | Kirsten Kelly | via `info@` | no direct address published | `/people/staff/` |
| Board | Liz Fosslien, Vanita Lee-Tatum, Brian Fitzpatrick, Mary Gilles, Dan Kuppe, Charlie Spaeth, Jocelyn Parrish | — | no direct addresses published | `/people/` |
| Physical | — | Mail | 1260 7th St, Oakland, CA 94607 | `/contact-hours-location/` |
| Contact hub | — | Web | https://www.thecrucible.org/contact-hours-location/ | — |

**Best outreach path:** Email **Melissa Gray, Senior Programs Manager**, via `info@thecrucible.org`, cc `registrar@thecrucible.org`. She owns programming, which is what the calendar is made of, and the registrar owns the WooCommerce class-date data we are already reading. Frame it as we do with Ace: *"we're already ingesting your public course catalog correctly from your own site, here's the merged East Bay calendar, tell us what to exclude"* — not a request for work. Two concrete asks worth making in the same email: (1) the **`registrar@thecrucibile.org` typo** on their contact page, which is free goodwill; (2) whether **Salesforce** (they employ a Salesforce Data Coordinator) holds per-meeting session times, since the website only exposes one start per multi-week course. Avoid leading with the Interim ED — leadership is mid-transition and this is an operational, not a strategic, request.

## Recommended `sources.yaml` entry

**This space needs one new adapter.** `embedded_json`: parse a named `<script type="application/json" id="...">` blob out of a server-rendered HTML page and map its bespoke shape. It is not covered by anything existing — `jsonld` is schema.org-specific, `nextdata` is hardcoded to `__NEXT_DATA__` and Eventbrite's shape, and `json` expects a standalone JSON *document* at a URL. Practically, `embedded_json` is `nextdata` generalized with a configurable `script_id` and field map; `nextdata` could be reimplemented on top of it. Add `embedded_json` to the adapter list in the `sources.yaml` header comment.

```yaml
  - id: the-crucible
    name: The Crucible
    city: Oakland
    region: east-bay
    url: https://www.thecrucible.org/
    address_override: "1260 7th St, Oakland, CA 94607"
    # MANDATORY: the class catalog carries no location field of any kind.
    sources:
      - adapter: embedded_json      # NEW adapter - see spaces/the-crucible.md
        url: https://www.thecrucible.org/course-search/
        script_id: ac-course-data
        label: course-catalog-blob
        trust: 100
        verified: true
        notes: >
          Confirmed 2026-08-05: 132 KB <script id="ac-course-data"
          type="application/json"> blob. 216 courses, 478 offerings, 271 inside
          120 days. start_dates[] are Unix epoch SECONDS, local America/
          Los_Angeles; decoding verified against the product page's Class.Date
          attribute to the minute. UID: {id}:{start_epoch}.
          Each start_date is one RUN of a course, not one meeting - a 5-week
          20-hour course is ONE entry. Do NOT compute DTEND from `hours`
          (total course hours, not session length).
          Skip the 9 courses with an empty start_dates array.
          /shop/ carries a byte-identical blob - do not ingest both.
          There is NO XHR endpoint and NO server-side filtering: ?department=
          and a bogus ?department=ZZZ both return the identical 216-course
          blob. All filtering is client-side JS.
        # No filters required: no orientations, no safety checkouts, no private
        # rentals, no per-meeting duplication. ~68 offerings/month is
        # comparable to Ace Makerspace, not a Maker Nexus firehose.
        #
        # OPTIONAL maker-only or adult-only views. Verbatim values 2026-08-05.
        # NB the blob stores HTML entities: it is "Glass Fusing &amp; Slumping",
        # never "Glass Fusing & Slumping". Unescape before matching, or match
        # the escaped form exactly.
        # filters:
        #   categories_exclude: ["Bike Shop"]          # department field
        #   title_excludes: ["Youth "]                 # 6 of 271 in horizon
      - adapter: json
        url: https://www.thecrucible.org/wp-json/wc/store/v1/products
        params: { per_page: 100, orderby: date }
        label: woocommerce-store-api
        trust: 90
        verified: true
        notes: >
          Confirmed 2026-08-05: 353 products over 4 pages, 1531 pa_class-date
          terms, 274 inside 120 days (cross-validates the blob's 271).
          CRITICAL: `orderby=date` IS REQUIRED. Without it the endpoint returns
          HTTP 200 and valid JSON with X-WP-Total: 98 - 72% of the catalog
          silently missing, covering only 40 of the blob's 216 live courses.
          Paginate on X-WP-TotalPages (page=99 returns 200 with [], not an
          error, so never paginate until-error).
          Dates: attributes[].terms[].name as "MM/DD/YY H:MM am/pm" local.
          DROP the literal sentinel "01/01/70 12:00 am" - naive %y parsing maps
          it to 2070 and it sails past every horizon check.
          Richer than the blob (full description, prices, stock, permalink) but
          categories are polluted with 60+ one-off per-product categories equal
          to the class title - filter on the blob's `department`, never on this.
      - adapter: nextdata
        url: https://www.eventbrite.com/o/the-crucible-2700512180
        label: eventbrite-organizer
        trust: 70
        verified: true
        notes: >
          Confirmed 2026-08-05: props.pageProps.upcomingEvents, 7 events,
          upcomingEventsTotal 7, hasMoreUpcoming false. All "Free Crucible
          Tour", 2026-08-20..2026-11-19. The ONLY source for the free public
          tours and the only one carrying a location (primary_venue.address =
          1260 7th St, Oakland, CA 94607, with lat/long). Real start_date +
          start_time + timezone. Eventbrite ToS restricts scraping - this is
          their published organizer page and low volume; keep the rate limit.
      - adapter: rss
        url: https://www.thecrucible.org/category/upcoming-events/feed/
        label: public-events-seed
        pubdate_means: post_date
        rss_mode: seed_list
        trust: 20
        verified: true
        enabled: false
        health:
          allow_zero: true
        notes: >
          3 items. pubDate is the POST date, NOT the event date - dates live
          only in the title text ("(JUL 16)") and the prose body. NOT an event
          source. Correct use is as the seed list for llm_html over the linked
          /event/<slug>/ pages, which is the only route to the public-event
          programme (Gifty, iron pours, artisan fairs, community bike rides).
          Filtering is real: a bogus category returns 404 with 0 items.
    # TRAPS, verified 2026-08-05 - do not wire these up:
    #   /?post_type=tribe_events&ical=1  -> 200 text/html, a rendered 1.4 MB page.
    #   /events/?ical=1                  -> 200 text/html, same page as /events/
    #     modulo 20 cachebuster lines. A byte-compare says "different"; a
    #     content-compare says "identical". Compare content.
    #   /category/<bogus>/feed/          -> 404 but Content-Type is
    #     application/rss+xml with a 1.35 MB HTML body. Here the HEADER lies.
    #   wc/store/v1/products WITHOUT orderby=date -> 200, valid JSON, 98 of 353
    #     products. The worst kind: correct-looking and quietly amputated.
    #
    # The Events Calendar is NOT INSTALLED. Every wp-json/tribe/* route 404s.
    # The tribe-* class names on their pages are inert Avada compatibility CSS.
    # Do not let those class names talk anyone into a tribe_rest source.
    #
    # No schema.org/Event anywhere. Product pages carry schema.org/Course with
    # no startDate, no offers, no hasCourseInstance - a `jsonld` adapter here
    # would return empty forever without ever erroring.
    #
    # Also absent: Google Calendar (0 hits sitewide, only Maps iframes), Luma,
    # Meetup group, Mastodon, Bluesky, LinkedIn, TikTok, and every one of 15
    # arts-nonprofit booking/CRM platforms probed (ASAP Connected, Sawyer,
    # Jackrabbit, ACTIVE, CourseStorm, Regpack, Ungerboeck, Altru, Blackbaud,
    # Neon CRM, Bloomerang, Tessitura, Ticket Tailor, Mindbody, Acuity).
    # Registration is in-house WooCommerce; the back office looks like
    # Salesforce (they employ a "Salesforce Data Coordinator").
    #
    # LEAD: ask whether Salesforce holds per-MEETING session times. The website
    # exposes only one start per multi-week course run, so a 10-meeting class
    # publishes as a single event. That is the right default for a merged
    # calendar, but the finer data exists somewhere.
```

`robots.txt`: **no `Crawl-delay`**, so the global `rate_limit_seconds: 2` default applies — no per-space override needed. Disallows are limited to `/wp-content/uploads/wc-logs/`, `/wp-content/uploads/woocommerce_transient_files/`, `/wp-content/uploads/woocommerce_uploads/`, `/*?add-to-cart=` and `/wp-admin/` (with `/wp-admin/admin-ajax.php` explicitly allowed). A Yoast block then re-states `User-agent: * / Disallow:` (allow all). **Every source above is permitted**; none touches a disallowed path.

## Research log

- 2026-08-05 — Read `CLAUDE.md`, `sources.yaml` and `spaces/ace-makerspace.md` for adapter names, trust semantics, the `filters:`/`health:` conventions and the project's catalogue of silent-failure traps.
- 2026-08-05 — Fetched `robots.txt` (no `Crawl-delay`; only WooCommerce/wp-admin paths disallowed) and `/course-search/` (200, **1.81 MB** — a size that immediately suggested a server-rendered catalog rather than an XHR-backed UI).
- 2026-08-05 — Enumerated inline `<script>` blocks by length. Found **`<script id="ac-course-data" type="application/json">`, 132 KB**, holding the entire 216-course catalog with epoch `start_dates`. Confirmed there is **no** fetch/XHR/Algolia/Elasticsearch/FacetWP endpoint and no `__NEXT_DATA__` — the search UI filters this blob client-side.
- 2026-08-05 — **Negative control on server-side filtering:** requested `/course-search/` with no params, `?department=Blacksmithing`, `?department=ZZZ-NOT-A-DEPT` and `?ac_department=blacksmithing`. All four returned the identical 216-course blob, proving the query params do nothing server-side.
- 2026-08-05 — Probed 9 WordPress/Tribe endpoints. `wp-json/tribe/events/v1/{events,categories,venues}` all **404**; `?post_type=tribe_events&ical=1` **200 `text/html`**; `/events/list/` 404; `?post_type=tribe_events&feed=rss2` **301** to the blog feed. Confirmed TEC is **not installed** by grepping `wp-content/plugins/` (no `events`/`tribe` path) and locating the `tribe-*` strings inside Avada `<style>` selectors.
- 2026-08-05 — Diffed `/events/` against `/events/?ical=1`: 20 differing lines, all cachebusters. Recorded as a trap where byte-compare and content-compare disagree.
- 2026-08-05 — Pulled `wp-json/wp/v2/types` (no `tribe_events`, no `event`, no `person`; `product` and `roster` are public) and `sitemap_index.xml`, which revealed `pa_class-date-sitemap.xml` (740 URLs) and `department-sitemap.xml` (22 terms) — the route into the WooCommerce class-date attribute.
- 2026-08-05 — Confirmed classes are **variable WooCommerce products** keyed on `pa_class-date`. Read `data-product_variations` off a product page and the matching `wc/store/v1/products/210220` payload; both give `Class.Date` terms like `08/06/26 6:00 pm`.
- 2026-08-05 — **Verified the blob's epoch timestamps decode exactly**: `1786064400` → `08/06/26 6:00 PM` and `1790902800` → `10/01/26 6:00 PM`, matching the product page's `Class.Date` terms to the minute.
- 2026-08-05 — Paginated `wc/store/v1/products` and got only **98** products, covering just 40 of the blob's 216 courses. Retested with `orderby=date` and got **353** (matching `product-sitemap.xml`'s 354 URLs) covering **all 216**. Recorded as the most dangerous trap for this space. Negative controls: bogus product ID → 404 JSON; `page=99` → 200 with `[]`.
- 2026-08-05 — Parsed all 1531 `pa_class-date` terms: 343 future, **274 inside 120 days**, cross-validating the blob's 271. Found two `01/01/70 12:00 am` sentinels that naive `%y` parsing maps to 2070.
- 2026-08-05 — Analysed catalog structure: 478 offerings, offerings-per-course distribution, and the multi-session question. Established from `hours` vs `day` vs `start_dates` count (`Glass Fusing and Slumping Lab - 5 weeks`: 20 h, Tue+Thu, **1** start_date) that **one start_date is one course RUN, not one meeting**.
- 2026-08-05 — Extracted verbatim `department` / `audience` / `level` / `format` / `day` / `time_of_day` values. Noted the HTML entities (`Glass Fusing &amp; Slumping`) and the one malformed `day` value using commas plus a trailing space. Confirmed the catalog has **no location field at all**, so `address_override` is mandatory and no `location_contains` filter is possible.
- 2026-08-05 — Followed the "RSVP on Eventbrite" button on `/events/` to collection `free-crucible-tours-1343509`, extracted `organizer_id: 2700512180`, then parsed `__NEXT_DATA__` on the organizer page: **7 upcoming Free Crucible Tours** with real `start_date`/`start_time`/`timezone` and full venue address. Second collection `gather-make-the-crucible-4809924` fetched but has no `__NEXT_DATA__`.
- 2026-08-05 — Tested 6 RSS paths. `/events/feed/` is a 670-byte empty comments feed; `/event/feed/` and `/category/events/feed/` carry 10 items whose `pubDate` is the post date; `/category/upcoming-events/feed/` carries the 3 curated upcoming items. **Negative control:** a bogus category returned **404 with `Content-Type: application/rss+xml` and a 1.35 MB HTML body** — a new variant where the header lies, and proof that the `upcoming-events` filtering is genuine.
- 2026-08-05 — Grepped home/events/classes/shop/course-search/contact for `calendar.google.com` (**0**), `lu.ma` (**0**), and 15 arts-nonprofit booking/CRM platforms (**0 for all**). Probed Mastodon (404), Bluesky (404/403), Meetup (404) and a shape-only Luma probe (404). Resolved the YouTube channel ID `UCRreKq5nHbkGnuaa7-VRSuw` and confirmed its feed (200, 15 entries).
- 2026-08-05 — Harvested contacts by `curl` + `grep` on `mailto:` (WebFetch redacts addresses): `info@`, `registrar@`, `private@`, phone (510) 444-0919, address 1260 7th St. Spotted the **`registrar@thecrucibile.org` typo** in the page's own `mailto:` href. Parsed `/people/staff/` and `/people/` into 15 named staff with roles, including Senior Programs Manager Melissa Gray and a Salesforce Data Coordinator.
- 2026-08-05 — Read the blog feed and searched the web for operating status: 2026-01 `$500,000` fundraising sprint, 2026-02-05 Kelson Foundation `$500K` match completed, 2026-06-30 annual report describing "two years holding on". `/people/staff/` lists **Doug Yeiser as *Interim* Executive Director**, superseding the older `/new-ed-seth-steward/` announcement. Recorded status as active with leadership in transition.
