# Lower 48 Woodshop

**ID:** `lower-48` · **City:** Oakland · **Region:** east-bay
**Address:** 1212 19th Street, Oakland, CA 94607
**Website:** https://www.lower48.org
**Status:** active — org is live (IRS record current to tax period 2025-12, Instagram post 2026-02-14, site up and carrying the correct current address), but **zero upcoming classes are listed on their own stated booking channel** as of 2026-08-05.
**Last researched:** 2026-08-05

## Summary

Lower 48 (trading as **Lower 48 Woodshop**) is a small 501(c)(3) nonprofit **woodturning school** in West Oakland, founded in 2016 by **Jolie Karno**. Its mission is to make woodturning accessible, explicitly prioritising women, BIPOC and LGBTQIA+ students, backed by a scholarship programme. It is a *teaching* shop, not a membership makerspace: the entire public programme is a short fixed catalogue of hands-on classes — Introduction to Spindle Turning, Bowl Turning, Boxes (endgrain), Make Your Own Pen, plus private and group bookings — capped at **4 students or fewer** per class (Eventbrite listings historically said 2). Volume is therefore very low and irregular: a handful of small classes when Jolie schedules them, and **nothing at all is scheduled right now**.

There is **no machine-readable event source of any kind** for this space. That is the headline finding, and it is not a search failure — it is verified across their website, their Eventbrite organizer, and every social and booking platform checked below.

## Ground truth

**Confirmed:**

- **Name.** `Lower 48 Woodshop` is the site title and the public brand; the footer on every page reads `LOWER 48 WOODSHOP`. The IRS/Business Master File name is the bare **`Lower 48`**. Eventbrite registers them as **`The Lower 48 Woodshop`**. Their own prose uses both "Lower 48" and "The Lower 48". The wiki spelling "Lower 48 Woodshop" is correct.
- **Canonical domain: `www.lower48.org`.** `https://lower48.org` → **301** to `https://www.lower48.org/`; `http://lower48.org` → 301 to `http://www.lower48.org/`. The apex does not serve content. Their own `sitemap.xml` and internal JSON-LD both emit `http://www.lower48.org` (note: **http**, not https, in the canonical `url` field — a Squarespace config quirk, harmless).
- **Platform: Squarespace** (`server: Squarespace`, `websiteId 53214cfce4b08eb850961242`, "Powered by Squarespace" footer). Not WordPress, not Wix, not Webflow, not Next.js.
- **Address: 1212 19th Street, Oakland, CA 94607.** Confirmed **three independent ways**: (1) the site footer on every page and the `/contact-lower48` page; (2) the `/faqs` page ("our address: 1212 19th St, Oakland", nearest BART = West Oakland); (3) the **IRS nonprofit record** — EIN **81-4587057**, name `Lower 48`, address `1212 19TH ST, Oakland CA 94607-2328` (ProPublica Nonprofit Explorer API).
- **Operating status: active, with a caveat.** Positive signals: IRS record carries `tax_period 2025-12` (i.e. they are still filing); Instagram `@lower48woodshop` most recent post **2026-02-14**; the website is current and correctly addressed; the Eventbrite organizer profile is live with 328 followers. Caveat: **`upcomingEventsTotal` is 0** on their Eventbrite organizer, which their own Classes page names as the way to sign up. So: the organisation is alive, the class schedule is currently empty.
- **They are NOT in the NIMBY complex, and have not been for years.** See *Host venue* below. The NIMBY connection is real but **historical** — it is literally where the name comes from.
- **Disambiguation.** "Lower 48" is a common phrase for the contiguous US and there is heavy noise (Lower 48 Brewing in Idaho, Lower 48 Records, various outfitters). The only correct handle set is `lower48.org` + `@lower48woodshop` + Eventbrite org `14103153826` + EIN `81-4587057`, all of which cross-reference each other. No other Bay Area entity called "Lower 48" surfaced.
- **Jolie Karno is the founder and the only named person.** "Founder + Instructor" on `/about-us`; also **Co-Department Head of Woodworking at The Crucible**, where she has taught woodturning since 2012, and formerly taught at the closed TechShop SF.

**Could NOT confirm:**

- **The name of the host building at 1212 19th St.** Their `/contact-lower48` says only "within a fabrication community in West Oakland" and `/faqs` says "the woodshop is in a larger maker space… people make all kinds of things from 3D printing to soy-sauce." Property records describe 1212 19th St as a 26,217 sq ft industrial light/manufacturing building. I could not find a public name, website or calendar for the host. **This is the single most valuable open question** — see *Leads*.
- **Whether any class is actually scheduled or taught right now.** Eventbrite says 0. Their Instagram highlight is labelled "We are teaching," and there was a post in Feb 2026, but I could not read post captions without a logged-in render.
- **Phone number.** `(415) 424-8911` appears in a Yelp search snippet. It is **not printed anywhere on their own site** (there is no `tel:` link on any page) and Yelp returned **403** to every fetch, so I never read the source. Treat as unverified.
- **`every.org/lower48`** (their donation page, linked from their own site) — returned **429 "Vercel Security Checkpoint"** on three attempts. Never read.
- **Wayback history.** `web.archive.org/cdx` timed out at 40 s and then returned **503** during this pass. No snapshot-diff evidence was obtainable; re-run later if the site's edit history matters.

## Verified feeds

**None. There are zero machine-readable event sources for this space.**

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| — | — | — | — | **0** | No event feed exists. Every candidate is documented below in *Dead ends*. |

For the record, these endpoints **were** fetched and **do** return valid machine-readable data — they are simply **not event sources**, and must not be wired up as if they were:

| Endpoint | HTTP | Content-Type | Bytes | Items | Why it is not an event source |
|---|---|---|---|---|---|
| `https://www.lower48.org/press-lower48?format=rss` | 200 | `application/rss+xml; charset=UTF-8` | 16,389 | **3** | Squarespace *blog* collection ("Press"). Items are press clippings, not events, and carry **no start date** — `pubDate` is the post date. Newest item **2022-11-13**; the other two are 2022-08-01 and **2017-11-13**. Change-detection at best; realistically abandoned. |
| `https://www.youtube.com/feeds/videos.xml?channel_id=UCiSXxhDgphpZdw08d4BLAyA` | 200 | `text/xml; charset=UTF-8` | 17,992 | **12** | Real Atom feed for their YouTube channel. No events. **Newest video 2020-03-14** — dead for over six years. |
| `https://www.eventbrite.com/o/the-lower-48-woodshop-14103153826` | 200 | `text/html; charset=utf-8` | 147,680 | **0** | `__NEXT_DATA__` parses cleanly: `props.pageProps.organizer.name = "The Lower 48 Woodshop"`, `id = 14103153826`, `upcomingEventsTotal = 0`, `hasMoreUpcoming = false`, `upcomingEvents = []`, `metrics.totalEvents = 0`, `followers = "328"`. Verified twice, cache-busted. **Correct URL, correct parse path, zero events.** |

⚠️ **Trap logged:** a web-search result title for that Eventbrite URL reads *"The Lower 48 Woodshop Events - **2 Upcoming Activities** and Tickets"*. That is a **stale search-index title**. Two live fetches minutes apart both returned `upcomingEventsTotal: 0`. Do not trust SERP snippets as evidence of a populated feed.

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| `nextdata` | `https://www.eventbrite.com/o/the-lower-48-woodshop-14103153826` | **The single highest-value lead.** Their own Classes page states verbatim: *"Our classes are currently offered through Eventbrite."* The organizer page is real, the parse path is confirmed, and past events existed (a "Bowl Turning" listing dated **2023-10-08** is still indexed). The only missing ingredient is a scheduled class. | Re-fetch monthly and check `props.pageProps.upcomingEventsTotal > 0`. Enable the source the first time it is non-zero. Note the project's Eventbrite ToS caution — prefer asking them to keep posting there. |
| — | **The host building at 1212 19th St** | Same pattern as Omni Commons → Sudo Room. If the "larger maker space / fabrication community" at 1212 19th St runs a tenant-wide calendar, it covers Lower 48 *and* every other tenant, which is far higher leverage than Lower 48 alone. I could not name it. | Ask Jolie directly ("what's the building called, and does it have a shared calendar?"). Also try Oakland business-licence records and the OfficeSpace.com listing for 1212 19th St for a landlord/operator name. |
| `ics` / `jsonld` | `https://www.thecrucible.org/` woodturning class catalogue | Jolie Karno is **Co-Department Head of Woodworking at The Crucible** and teaches woodturning there. Some of her teaching output is scheduled on The Crucible's catalogue, which is a real, populated class system. | **Out of scope for `lower-48`** — those are Crucible events, not Lower 48 events, and would double-count. But The Crucible is already on the "still unverified" east-bay list in `sources.yaml` and this is a good reason to promote it. Do not attribute Crucible classes to Lower 48. |
| human | Newsletter signup (Squarespace form `636ff3a5b820755b1c3d9e38`), present in the footer of every page | For a space this size a newsletter is the most likely place a new class announcement lands first, ahead of Eventbrite going live. | Subscribe manually with the project address. |
| human | Instagram `@lower48woodshop` | Almost certainly the primary live channel — it is the only channel with 2026 activity. Instagram has **no public feed**, so this can never be an adapter. | Manual watch, or a logged-in/browser pass if the project ever builds one. |
| — | `https://www.every.org/lower48` | Their donation platform, linked from their own site. Every.org has a public API and may carry an org profile with current activity/contact data. | Retry — it returned 429 behind a Vercel challenge on every attempt today. |
| — | `https://thelower48.etsy.com` (→ `etsy.com/shop/thelower48`) | Instagram bio link. Shop activity is a liveness signal (Etsy shows "last updated"). | Not an event source. Fetch returned **403** to curl; check in a browser. |

## Dead ends

Everything below was **actually fetched on 2026-08-05**, not assumed.

**Site structure / platform**

- **No events collection exists.** `sitemap.xml` (13,936 B) lists **20 URLs**, all ordinary pages: `/press-lower48` (+3 items), `/about-us`, `/contact-us`, `/donate`, `/home-1`, `/classes`, `/testimonials`, `/testimonials-1`, `/lower48home`, `/volunteer-lower48`, `/donate-lower48`, `/our-story-lower48`, `/testimonials-lower48`, `/classes-lower48`, `/contact-lower48`, `/scholarships-lower48`, `/faqs`. **No `/events`, no `/calendar`.**
- **`https://www.lower48.org/?format=rss`** → **400**, `application/octet-stream`, body `Unknown response format for page type`. Root is an index page, not a collection.
- **`/classes-lower48?format=rss`** → **400**, same body. The Classes page is a static page, not a collection — so there is no per-class feed.
- **`/events?format=rss`, `/blog?format=rss`, `/news?format=rss`, `/calendar?format=rss`, `/workshops?format=rss`** → all **404** returning `text/html` (73,502 B — the Squarespace 404 page). None of these collections exist.
- **JSON-LD: no `schema.org/Event` anywhere.** Every page carries exactly **one** `<script type="application/ld+json">` block and it is `@type: WebSite` (site name + logo). Checked on `/`, `/classes-lower48`, `/contact-lower48`, `/about-us`. The `jsonld` adapter has nothing to bite on.
- **Google Calendar: none.** **Zero `<iframe>` elements** on any of the 11 pages fetched, and zero occurrences of `calendar.google.com` anywhere in the HTML. There is no embedded gCal to extract an ID from.
- **WordPress / The Events Calendar: N/A.** Squarespace, not WP. No `tec-api-version`, no `/wp-json/`, no `?ical=1` route.
- **`__NEXT_DATA__` on their own site: none.** Squarespace, not Next.js.
- **`/about-lower48`** (linked from the nav) → **302**. Legacy redirect.
- **`/classes`** is an **orphaned legacy page** — in the sitemap but not in the nav (the nav points at `/classes-lower48`). It still renders a broken Squarespace embed placeholder reading *"Embed Block — Enter a valid embed URL or code."* A booking widget that once lived there is gone. Do not parse this page.

**Booking / ticketing platforms — all absent**

- **Luma** — `lu.ma/lower48` and `lu.ma/lower48woodshop` both 301 → `luma.com/...` → **404**. No Luma calendar.
- **Bookwhen** — `bookwhen.com/lower48` → **404**.
- **Sawyer, Acuity, Punchpass, Calendly, Ticket Tailor, Square/Square Online, Gumroad** — **zero references** in the HTML of all 11 pages fetched. Grepped explicitly. They are not using any of them.
- **Meetup** — `meetup.com/lower48/events/rss/` and `meetup.com/lower48woodshop/events/rss/` → both **404** `text/plain` with `{"message":"Group not found"}`. No Meetup group. (Published-endpoint probe only; no page scraping.)

**Social feeds**

- **Bluesky** — `bsky.app/profile/lower48.org/rss` → **404**; `bsky.app/profile/lower48woodshop.bsky.social/rss` → **404**. No Bluesky presence found.
- **Mastodon** — `sfba.social/@lower48.rss` → **404**; `sfba.social/@lower48woodshop.rss` → **404**. No Mastodon presence found.
- **X / Twitter** — their **Eventbrite profile advertises `https://x.com/lower48woodshop`**, but that URL returns **404**. The account is gone or renamed. Dead link on their own profile.
- **Facebook** — `facebook.com/Lower48woodshop` exists (confirmed via search index) but returns **400** to every non-browser fetch, at `www.`, `m.` and `/about/`. Not machine-readable regardless.
- **YouTube** — channel real (`UCiSXxhDgphpZdw08d4BLAyA`), Atom feed works, but **last upload 2020-03-14**. Abandoned.

**Third-party**

- **`nimbyspace.org`** — every path (`/`, `/makers/lower-48-2/`, `/feed/`, `/events/`, `/?ical=1`, `/wp-json/tribe/events/v1/events`) returns **HTTP 503** with a 51-byte body: `<script src=https://cf-oss.gname.net/s.js></script>`. That is a registrar parking/interstitial shim, not a website. The domain is effectively gone. See *Host venue*.
- **Yelp** (`yelp.com/biz/lower-48-woodshop-oakland`) — **403** to both WebFetch and curl. Listing exists per search index ("Updated July 2026", 12 reviews, 33 photos) but I never read it. Aggregator, never authoritative.
- **`every.org/lower48`** — **429**, `<title>Vercel Security Checkpoint</title>`. Blocked.
- **`thelower48.etsy.com`** — 302 → `etsy.com/shop/thelower48`, then **403**. Blocked.
- **`web.archive.org/cdx`** — 40 s timeout, then **503**. No history obtainable this pass.

**robots.txt — read this before touching the site again**

`https://www.lower48.org/robots.txt` (200, `text/plain`, 1,518 B) is the Squarespace default, but **unlike The Box Shop's it is NOT a collapsed group**. The AI user-agent block here carries its own `Disallow: /`:

```
User-agent: anthropic-ai
User-agent: ClaudeBot
User-agent: GPTBot
User-agent: CCBot
… (25 agents total)
Disallow: /
```

**Consequences, per this project's own convention ("`robots.txt` disallow means we do not fetch it"):**

1. **Do not use WebFetch on `lower48.org`** — it identifies as ClaudeBot, which is fully disallowed here. Every fetch in this research pass used `curl` with the project's own user-agent (`bayarea-maker-calendar/0.1`), which falls under the permissive `User-agent: *` group. The nightly pipeline's UA is likewise fine.
2. The `*` group additionally disallows `?format=json`, `?format=json-pretty`, `?format=ical`, `?format=page-context`, `?format=main-content`, plus `/config`, `/search`, `/api/`, `/static/`, and `?author=`/`?tag=`/`?month=`/`?view=`. **`?format=rss` is allowed.**
3. **`?format=ical` was therefore never tested** — deliberately, because it is disallowed. Based on the Box Shop precedent it would return 200 `text/html` anyway, but that is inference, not a measurement. Do not record it as tested.

## Host venue (NIMBY)

**Lower 48 is not a NIMBY tenant. NIMBY no longer exists.** The `sources.yaml` discovery note ("small woodshop inside the NIMBY complex") is **roughly seven years out of date** — exactly the wiki-staleness failure that note warned about.

What is actually true:

- **The NIMBY connection is the origin story, and it is where the name comes from.** From their own `/our-story-lower48` and `/about-us`: *"Founded in 2016, her one-woman project got its start as part of the NIMBY maker space in East Oakland. Back then her shop was the bottom of two 48-foot shipping containers stacked on top of each other. Since her shop was underneath, visitors were told to walk back to 'the lower 48'. The name stuck."*
- **NIMBY closed on 2019-09-30**, after 15 years at 8410 Amelia St, Oakland 94621 — the artists declined to renew as warehouse rents rose with the cannabis industry ([Oakland North, 2019-09-30](https://oaklandnorth.net/2019/09/30/diy-art-space-closes-as-cannabis-industry-edges-in-on-oakland-warehouses/)).
- **`nimbyspace.org` is gone.** Every path returns **503** with a registrar parking shim (`cf-oss.gname.net`). The `nimbyspace.org/makers/lower-48-2/` page that still surfaces in search results is **unreachable** — it is a search-index ghost. **There is no NIMBY tenant calendar, because there is no NIMBY.**
- **Their own `/about-us` page is stale on this point**, still saying in the present tense *"There are two stacked 48-foot containers at NIMBY. We are in the bottom 48-foot container."* The `/contact-lower48` page and the footer of every page are the current truth: **1212 19th Street, West Oakland**. Do not let the About page's copy pull you back to East Oakland.

**The Omni-Commons-style lead has moved, not vanished.** Lower 48 is now a tenant *inside another shared fabrication building* — `/contact-lower48`: *"Lower 48's woodshop is located within a fabrication community in West Oakland… 1212 is a professional space and is not open to the public. Please email us ahead of time to schedule an appointment."* and `/faqs`: *"The woodshop is in a larger maker space… people make all kinds of things from 3D printing to soy-sauce."* **I could not identify this building by name**, and property records only show a 26,217 sq ft industrial building. Because it is explicitly *not open to the public*, it is less likely than Omni to run a public tenant-wide calendar — but it is the right question to ask Jolie, and it is the only remaining high-leverage lead for this space.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Instagram | `@lower48woodshop` | https://www.instagram.com/lower48woodshop/ | **none** (no public feed) | Profile: **yes** | **The primary live channel.** ~1,227 followers, ~755 following. Bio: *"We are a kickass nonprofit woodshop in Oakland, CA."* Bio link → `thelower48.etsy.com`. **Most recent post ≈ 2026-02-14** — the only 2026 activity found anywhere. Has a story highlight titled *"We are teaching."* Linked from every page of their site. Could not read captions without a logged-in render. |
| Facebook | `Lower48woodshop` | https://www.facebook.com/Lower48woodshop/ | none | Page exists (via search index); **400** to all fetches | Linked from every page of their site and from their Eventbrite profile. Not machine-readable. |
| YouTube | `Lower48Woodshop` (`UCiSXxhDgphpZdw08d4BLAyA`) | https://www.youtube.com/c/Lower48Woodshop | `https://www.youtube.com/feeds/videos.xml?channel_id=UCiSXxhDgphpZdw08d4BLAyA` (200, 12 entries) | **yes** | Feed works but is **abandoned — last upload 2020-03-14**. Not events. Notable content: Adam Savage's Maker Tour episode about Lower 48 (2017). |
| Eventbrite | `the-lower-48-woodshop-14103153826` | https://www.eventbrite.com/o/the-lower-48-woodshop-14103153826 | `__NEXT_DATA__` only | Page **yes**, events **no** (0 upcoming) | Their own stated class-signup channel. 328 followers. Profile `socials` block lists `website: http://www.lower48.org/`, `facebook`, and a **dead** `x: https://x.com/lower48woodshop`. |
| Etsy | `thelower48` | https://www.etsy.com/shop/thelower48 | none | Redirect resolves; body **403** | Instagram bio link. Commerce, not events. |
| X / Twitter | `lower48woodshop` (claimed) | https://x.com/lower48woodshop | — | **404 — account gone** | Advertised on their Eventbrite profile but does not exist. Stale link. |
| Bluesky | — | — | — | no | Two handle guesses probed, both 404. |
| Mastodon | — | — | — | no | Two handle guesses probed on `sfba.social`, both 404. |
| Meetup | — | — | — | no | No group (`{"message":"Group not found"}`). |
| Patreon | — | — | — | inconclusive | A `becomePatronButton.bundle.js` script tag loads on every page (Squarespace social block), but **no Patreon URL appears anywhere**. Probably a leftover from a removed block. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| **General / best contact** | — | email | **`hello@lower48.org`** | `/contact-lower48` ("We would love to hear from you!") and `/faqs` ("The best way to contact us is through email at hello@lower48.org") |
| **Founder + Instructor / Director** | **Jolie Karno** | email | **`jolie@lower48.org`** | `/classes` ("contact Jolie Karno at jolie@lower48.org"); role from `/about-us` and `/our-story-lower48` |
| Volunteer coordination | — | web form | https://www.lower48.org/volunteer-lower48 | Squarespace form on that page |
| Newsletter | — | signup form | Footer of every page (Squarespace form id `636ff3a5b820755b1c3d9e38`) | site HTML |
| Best DM channel | — | Instagram | https://www.instagram.com/lower48woodshop/ (`@lower48woodshop`) | verified profile |
| Physical | — | address | **1212 19th Street, Oakland, CA 94607** — *by appointment only, not open to the public* | site footer, `/contact-lower48`, `/faqs`, and the IRS record |
| Nonprofit identity | **Lower 48** | IRS | **EIN 81-4587057**, 501(c)(3), ruling date 2017-01, NTEE **B30** (Vocational/Technical Schools), latest tax period **2025-12** | ProPublica Nonprofit Explorer API |
| Donations | — | web | https://www.every.org/lower48 (linked from site) and a PayPal donate button on `/donate-lower48` | `/donate-lower48`, `/classes-lower48` |
| Phone | — | tel | `(415) 424-8911` — **UNVERIFIED**, from a Yelp search snippet only; no phone number appears anywhere on their own site | Yelp SERP snippet (Yelp itself returned 403) |

**Best outreach path:** Email **`jolie@lower48.org`**, cc **`hello@lower48.org`**. Jolie Karno is the founder, the instructor, and — for a one-woman nonprofit school — effectively the entire schedule. There is no events coordinator to route around. The ask has **two parts**, and the second matters more than the first: (1) *keep listing classes on your existing Eventbrite organizer page and we'll pick them up automatically, no work for you* — the plumbing already works, it is just empty; and (2) *what is the building at 1212 19th St called, and does it run a shared calendar for its tenants?* — that could unlock several spaces at once, the same way the Omni Commons Airtable would. If email goes unanswered, DM **`@lower48woodshop`** on Instagram: it is the only channel with any 2026 activity, and for a shop this size it is plausibly the only one she checks.

## Recommended `sources.yaml` entry

```yaml
  - id: lower-48
    name: Lower 48 Woodshop
    city: Oakland
    region: east-bay
    url: https://www.lower48.org/
    address_override: "1212 19th St, Oakland, CA 94607"
    health:
      allow_zero: true
    notes: >
      NO MACHINE-READABLE EVENT SOURCE EXISTS as of 2026-08-05. Verified, not
      assumed - see spaces/lower-48.md for the full probe list. Squarespace site
      with NO events collection (sitemap has 20 URLs, none of them events;
      /events, /calendar, /blog, /news, /workshops all 404), only @type:WebSite
      JSON-LD, zero iframes so no embedded gCal, and no Luma / Bookwhen /
      Sawyer / Acuity / Punchpass / Calendly / Ticket Tailor / Meetup anywhere.
      Their own Classes page says "Our classes are currently offered through
      Eventbrite" - that organizer page is real and parseable but has
      upcomingEventsTotal: 0.
      Tiny nonprofit woodturning SCHOOL, not a membership makerspace. Classes cap
      at 4 students. Volume is very low and bursty, hence allow_zero.
      CORRECTION to the discovery note: they are NOT in the NIMBY complex. NIMBY
      closed 2019-09-30 and nimbyspace.org is a parked 503. Lower 48 started
      there in 2016 (bottom of two stacked 48-ft shipping containers - that is
      where the name comes from) and has long since moved to 1212 19th St in West
      Oakland, inside an unnamed private fabrication building that is explicitly
      not open to the public. Their own /about-us page is stale and still
      describes NIMBY in the present tense; the footer address is correct.
      ROBOTS: www.lower48.org/robots.txt gives ClaudeBot / anthropic-ai / GPTBot
      / CCBot et al. their own "Disallow: /" group - this is NOT the collapsed
      no-op group seen on boxshopsf.org. Fetch only with the project user-agent
      (falls under "*"). ?format=json / json-pretty / ical / page-context /
      main-content are Disallow'ed for everyone; ?format=rss is allowed.
    sources: []
    # --- NOTHING IS WIRED UP. Do not enable any of the below without a
    #     fresh fetch that returns a real, dated event. ---
    #
    # BEST LEAD. Correct URL and correct adapter, both confirmed by fetching:
    # __NEXT_DATA__ -> props.pageProps.organizer.name = "The Lower 48 Woodshop",
    # id 14103153826, 328 followers. But upcomingEventsTotal = 0 and
    # upcomingEvents = [] on 2026-08-05, confirmed twice with cache-busting.
    # Enable ONLY after observing a nonzero count. NB a stale search-engine
    # result title claims "2 Upcoming Activities" - it is wrong; the live page
    # says 0. Registered as `nextdata`, NOT `jsonld`: Eventbrite organizer pages
    # no longer emit JSON-LD (same correction as humanmade).
    # - adapter: nextdata
    #   url: https://www.eventbrite.com/o/the-lower-48-woodshop-14103153826
    #   label: eventbrite-organizer
    #   trust: 100
    #   verified: false
    #   enabled: false
    #   health:
    #     require_nonzero_once: true
    #     allow_zero: true
    #
    # NOT AN EVENT SOURCE. Squarespace "Press" blog collection: 200
    # application/rss+xml, 16,389 B, 3 items, and pubDate is the POST date, not
    # an event date. Newest item is 2022-11-13; oldest is 2017. Effectively
    # abandoned. Keep only if you want a staleness tripwire on the site.
    # - adapter: rss
    #   url: https://www.lower48.org/press-lower48?format=rss
    #   label: press-blog
    #   trust: 10
    #   verified: false
    #   enabled: false
    #
    # NOT AN EVENT SOURCE. Real Atom feed, 200 text/xml, 12 entries, but the
    # newest upload is 2020-03-14. Liveness signal only, and a dead one.
    # - adapter: rss
    #   url: https://www.youtube.com/feeds/videos.xml?channel_id=UCiSXxhDgphpZdw08d4BLAyA
    #   label: youtube
    #   trust: 10
    #   verified: false
    #   enabled: false
    #
    # WHAT MUST HAPPEN FIRST, in order of leverage:
    #   1. Ask Jolie Karno (jolie@lower48.org, cc hello@lower48.org) to keep
    #      posting classes to the Eventbrite organizer above. The adapter is
    #      already written and proven - the page is simply empty. One reply and
    #      one scheduled class turns this space on.
    #   2. Ask her what the building at 1212 19th St is called and whether it
    #      runs a tenant-wide calendar. Same shape as the Omni Commons Airtable
    #      lead under sudo-room, and potentially worth several spaces.
    #   3. Subscribe to the site newsletter (footer form on every page); for a
    #      shop this size that is likely where a new class lands first.
    #   4. Failing all of the above, DM @lower48woodshop on Instagram - the only
    #      channel with any 2026 activity (last post 2026-02-14).
    #
    # Dead ends - do not re-investigate (all fetched 2026-08-05):
    #   lower48.org -> 301 to www.lower48.org (www is canonical)
    #   /?format=rss and /classes-lower48?format=rss -> 400 "Unknown response
    #     format for page type" (they are pages, not collections)
    #   /events /calendar /blog /news /workshops + ?format=rss -> 404 text/html
    #   JSON-LD is @type:WebSite only, on every page. No Event objects anywhere.
    #   Zero <iframe> elements site-wide; zero hits for calendar.google.com
    #   /about-lower48 -> 302; /classes is an orphan legacy page with a BROKEN
    #     Squarespace embed placeholder ("Enter a valid embed URL or code")
    #   lu.ma/lower48 and lu.ma/lower48woodshop -> 404 via luma.com
    #   bookwhen.com/lower48 -> 404
    #   meetup.com/lower48 and /lower48woodshop /events/rss/ -> 404 Group not found
    #   bsky.app/profile/{lower48.org,lower48woodshop.bsky.social}/rss -> 404
    #   sfba.social/@{lower48,lower48woodshop}.rss -> 404
    #   x.com/lower48woodshop -> 404 (advertised on their Eventbrite profile,
    #     but the account is gone - stale link on their own page)
    #   facebook.com/Lower48woodshop -> exists but 400 to all fetches, and is
    #     not machine-readable in any case
    #   nimbyspace.org (all paths incl. /feed/, /events/, /?ical=1, wp-json
    #     tribe) -> 503 registrar parking shim. NIMBY is gone; there is no
    #     tenant calendar to chase.
    #   ?format=ical was deliberately NOT tested - robots.txt disallows it.
    # Blocked, never read, retry later: yelp.com biz page (403),
    #   every.org/lower48 (429 Vercel checkpoint), thelower48.etsy.com (403),
    #   web.archive.org/cdx (timeout then 503).
```

## Research log

- **2026-08-05** — Established the canonical domain by probing four forms with `curl -w`: `https://www.lower48.org` (**200**, 91,754 B), `https://lower48.org` (**301** → www), `http://lower48.org` (**301** → www), plus `robots.txt` (200, 1,518 B) and `sitemap.xml` (200, 13,936 B). Identified the platform as **Squarespace** (`server: Squarespace`, `websiteId 53214cfce4b08eb850961242`).
- **2026-08-05** — **Read `robots.txt` first and it changed how the rest of the pass was run.** Unlike The Box Shop's file, the AI-agent group here carries its own `Disallow: /` covering ClaudeBot, anthropic-ai, GPTBot, CCBot and 21 others. So **WebFetch was not used against this host at all**; every page was pulled with `curl` under `bayarea-maker-calendar/0.1`, which falls in the permissive `*` group. `?format=ical` and `?format=json` are Disallow'ed and were therefore deliberately **not** probed.
- **2026-08-05** — Pulled `sitemap.xml`: **20 URLs, none of them events.** Fetched 11 pages (`/`, `/classes`, `/classes-lower48`, `/contact-us`, `/contact-lower48`, `/about-us`, `/our-story-lower48`, `/faqs`, `/donate-lower48`, `/volunteer-lower48`, `/scholarships-lower48`) at 2 s intervals and text-extracted each. Confirmed the address **1212 19th St, Oakland CA 94607** in the footer of all 11, plus the contact page's "within a fabrication community in West Oakland… not open to the public" and the FAQ's "the woodshop is in a larger maker space… from 3D printing to soy-sauce."
- **2026-08-05** — Probed the Squarespace collection routes actually permitted by robots: root `?format=rss` → **400** (`Unknown response format for page type`), `/classes-lower48?format=rss` → **400**, and `/events`, `/blog`, `/news`, `/calendar`, `/workshops` all → **404** `text/html`. The one collection that exists is `/press-lower48?format=rss` → **200 `application/rss+xml`, 16,389 B, 3 items** — press clippings with **no event dates**, newest **2022-11-13**, oldest 2017. Checked JSON-LD on four pages: exactly one block each, all `@type: WebSite`. Grepped all 11 pages for `<iframe>` (**zero**) and `calendar.google.com` (**zero hits**) — there is no embedded Google Calendar.
- **2026-08-05** — Grepped every page's HTML for outbound platform links. That surfaced the **Eventbrite organizer** `https://www.eventbrite.com/o/the-lower-48-woodshop-14103153826`, linked only from `/classes-lower48` behind the "Click here To sign up for classes" button. Fetched it and parsed `__NEXT_DATA__`: `organizer.name = "The Lower 48 Woodshop"`, `id = 14103153826`, `followers = 328`, **`upcomingEventsTotal = 0`**, `hasMoreUpcoming = false`, `upcomingEvents = []`, `metrics.totalEvents = 0`, and **zero `ld+json` blocks** (confirming `nextdata`, not `jsonld`). Re-fetched with `Cache-Control: no-cache` after a search snippet claimed "2 Upcoming Activities" — **still 0**. Logged the SERP title as a trap.
- **2026-08-05** — Probed booking and social endpoints: `lu.ma/lower48` and `lu.ma/lower48woodshop` (301 → luma.com → **404**), `bookwhen.com/lower48` (**404**), Meetup `/lower48/` and `/lower48woodshop/events/rss/` (**404 `{"message":"Group not found"}`**), Bluesky ×2 (**404**), Mastodon `sfba.social` ×2 (**404**), `x.com/lower48woodshop` (**404 — dead, despite being advertised on their own Eventbrite profile**), Facebook ×3 variants (**400** to all fetches). Grepped all site HTML for Sawyer / Acuity / Punchpass / Calendly / Ticket Tailor / Square / Gumroad: **zero references**.
- **2026-08-05** — Resolved the YouTube channel to `UCiSXxhDgphpZdw08d4BLAyA` and pulled its Atom feed: **200 `text/xml`, 17,992 B, 12 entries — newest 2020-03-14.** Abandoned. Instagram `@lower48woodshop` read via WebFetch (a different host, not robots-blocked): ~1,227 followers, bio *"We are a kickass nonprofit woodshop in Oakland, CA"*, bio link `thelower48.etsy.com`, **most recent post ≈ 2026-02-14**, story highlight *"We are teaching."* That February post is the only 2026-dated activity found anywhere and is the main evidence the space is still live.
- **2026-08-05** — **Chased the NIMBY lead and closed it.** Search established NIMBY closed **2019-09-30** at 8410 Amelia St (Oakland North). Probed `nimbyspace.org` at six paths including `/feed/`, `/events/`, `/?ical=1` and the Tribe REST route: **all 503 with a 51-byte `cf-oss.gname.net` registrar parking shim.** The `nimbyspace.org/makers/lower-48-2/` page still in search results is unreachable. Their own `/our-story-lower48` supplied the real history — founded 2016 inside NIMBY in the **lower of two stacked 48-foot shipping containers**, which is literally where the name comes from — and their `/about-us` still describes NIMBY in the **present tense**, which is how the stale wiki claim survives. **There is no tenant-wide NIMBY calendar because there is no NIMBY.** The equivalent lead has moved to the unnamed West Oakland building at 1212 19th St, which I could not identify.
- **2026-08-05** — Confirmed identity and operating status independently of the website via the **ProPublica Nonprofit Explorer API**: EIN **81-4587057**, name `Lower 48`, address `1212 19TH ST, Oakland CA 94607-2328`, 501(c)(3), ruling date 2017-01, NTEE **B30** (Vocational/Technical Schools), latest **tax period 2025-12**. This is the strongest available evidence that the org is currently active, and it corroborates the address from a source that has nothing to do with their CMS.
- **2026-08-05** — Blocked and unresolved: Yelp biz page (**403** to both WebFetch and curl — so the `(415) 424-8911` phone number remains an unverified SERP snippet, and no phone appears anywhere on their own site), `every.org/lower48` (**429**, Vercel Security Checkpoint, three attempts), `thelower48.etsy.com` (**403**), and `web.archive.org/cdx` (40 s timeout then **503**, so no snapshot history was obtainable this pass).
