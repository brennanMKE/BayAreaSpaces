# Sequoia Fabrica

**ID:** `sequoia-fabrica` · **City:** San Francisco · **Region:** sf
**Address:** 1736 18th St, San Francisco, CA 94107 (Potrero Hill)
**Website:** https://sequoiafabrica.org/
**Status:** active — founded Nov 2023, opened Mar 2024; wiki edited 2026-08-02, Google Calendar edited 2026-08-01, Bookwhen has events booked through Jan 2027.
**Last researched:** 2026-08-05

## Summary

Volunteer-run 501(c)(3) community makerspace (EIN 93-4496044) with a wood and textile
workshop, 3D printers, laser cutter, electronics and fine-arts stations. Programming is
overwhelmingly classes and social/skill-share nights — the org's own About page says two
thirds of classes involve sewing or fiber arts, 85% are beginner-oriented, and the average
ticket is $20. Volume is modest and steady: **20 public events on Bookwhen spanning
Aug 2026 – Jan 2027** (roughly 3-5/month, heavy on recurring socials and twice-monthly
Member Applicant Orientations), plus **~5 recurring monthly member/volunteer events** on a
separate Google Calendar. Community is around thirty members and volunteers as of Jul 2025.

**The two calendars do not compete — they are disjoint by design.** The Google Calendar's
own `X-WR-CALDESC` reads: *"For member and volunteer events (separate from classes/Bookwhen
events)."* Ingest both.

## Verified feeds

Only sources you personally fetched and confirmed return real events.

| Adapter | URL | HTTP | Content-Type | Events seen | Notes |
|---|---|---|---|---|---|
| `gcal_ics` | `https://calendar.google.com/calendar/ical/c_69d095340ce714f6a0769a561fa4414c07981195eb1c9be7fde47a5cdd5450a5%40group.calendar.google.com/public/basic.ics` | 200 | `text/calendar; charset=utf-8` (78,803 B) | **89 VEVENT total**; ~7 live in a 120-day horizon (6 open-ended monthly RRULEs + 1 dated one-off 2026-08-12) | **The ID in `references/feeds.json` resolves and is real.** `X-WR-CALNAME: Sequoia Fabrica - Community Calendar`, `X-WR-TIMEZONE: America/Los_Angeles`. Live RRULEs: New Member Orientation (1TU and 3TU), Members & Guests Make Night (1MO), Community Meeting (3WE), Woodworking Grove Hangout Night (2MO), Board Member Quarterly Meeting. Most recent `LAST-MODIFIED` is `20260801T195125Z` — **actively maintained, 4 days before research date**. Mostly historical backlog (89 events back to Dec 2023), so expand RRULEs and filter to horizon or the count will mislead. Mixed `DTSTART` forms: bare-UTC `Z` and `TZID=America/Los_Angeles` both appear — handle both. |
| `bookwhen_html` *(new adapter)* | `https://bookwhen.com/sequoiafabrica` | 200 | `text/html; charset=utf-8` (67,502 B) | **20 events, 2026-08-13 → 2027-01-05** | **This is the primary public class/event calendar.** Server-rendered, statically parseable, no browser needed. Each event is a `<tr data-hook="agenda_list_item">` whose `data-event="ev-{entryid}-{YYYYMMDDHHMMSS}"` encodes the **exact local start datetime**, with the title in the row's `<button>` and the time in `td.duration`. Titles seen: Hand Embroidery Social, Upmending (upcycling + mending) Social, Member Applicant Orientation ×11, Crochet & Knitting Social, Block Printmaking Social, Let's make BioYarn!. Times are Pacific (`PDT`/`PST` labels rendered inline). Use `{entryid}` + datetime as a stable UID. Page defaults to **20 rows** — see the pagination lead below. |

Both are the space's own surfaces. `https://events.sequoiafabrica.org` is an official vanity
alias that redirects to the Bookwhen URL — either works, but prefer the canonical
`bookwhen.com/sequoiafabrica`.

## Leads (unverified)

| Adapter | URL / what to check | Why it might work | How to confirm |
|---|---|---|---|
| `ics` | `webcal://feeds.bookwhen.com/ical/lcqebfpp6u7h/{TOKEN}/public.ics?historic_month_count=1` | **Pattern and host confirmed.** `feeds.bookwhen.com` is live and returns `404 text/calendar` for a bad token, so the path shape is right. The `{account}` segment is almost certainly **`lcqebfpp6u7h`** — that value appears as the `calendar` key in the public page's `data-options` JSON. Only `{TOKEN}` is missing, and it is **not discoverable from outside**: the public page exposes no `webcal://`, no `.ics`, no feed link, and no JSON-LD. | **Brennan copies it from the Bookwhen admin → "Calendar feeds".** This is a 30-second ask, not an engineering problem. Feed is event-info-only (no booking details) and defaults to 3 months back / 12 months forward. **Once you have it, this supersedes `bookwhen_html`** — swap `verified: true` and demote the HTML parser to fallback. |
| `bookwhen_html` (pagination) | `https://bookwhen.com/sequoiafabrica/calendar_items?offset=0&limit=50&start_time=…&calendar=lcqebfpp6u7h&context=api` | Returns **200, `text/javascript`, 41 KB** — the AJAX endpoint the "Show more…" button calls. Carries the same agenda rows past the 20-row default. | Verified as reachable, but it returns **jQuery statements wrapping escaped HTML**, not JSON — you must unescape then parse. Only needed if 20 events is ever short of the horizon; today it already reaches Jan 2027, so this is likely unnecessary. |

## Dead ends

Checked and confirmed **not** usable — don't repeat these:

- **`sequoiafabrica.org` has no event data at all.** It is a **static Next.js export** (`nextExport: true`); `__NEXT_DATA__` is a 171-byte stub with `pageProps: {}` and zero occurrences of `calendar`/`event`/`ical`/`bookwhen`. **Zero JSON-LD blocks.** It links out to Bookwhen and nothing more.
- **Not WordPress** → no The Events Calendar. `?ical=1`, `/wp-json/tribe/events/v1/events`, and `tec-api-version` meta are all inapplicable.
- **All 404 on `sequoiafabrica.org`:** `/events/`, `/calendar/`, `/classes/`, `/feed/`, `/feed.xml`, `/rss.xml`, `/index.xml`, `/blog/`, `/sitemap.xml`, `/contact/`, `/about/`. Real content lives under **`/docs/*`** (`/docs/about`, `/docs/contact`, `/docs/faq`, `/docs/membership`, `/docs/partner`).
- **`events.sequoiafabrica.org` is a catch-all redirect**, not a site. `/feed`, `/feed.xml`, `/rss`, `/index.xml`, `/events.ics`, `/calendar.ics`, `/ical`, `/robots.txt`, `/sitemap.xml` **all** 200 → `https://bookwhen.com/sequoiafabrica` with identical 67,502-byte bodies. Don't mistake this for a working `.ics`.
- **Bookwhen public page has no JSON-LD** and exposes no feed/subscribe/webcal link anywhere in its HTML. Confirms the token can only come from the admin.
- **Luma:** `lu.ma/sequoiafabrica` and `lu.ma/sequoia-fabrica` both **404**. No Luma presence.
- **Eventbrite:** `eventbrite.com/o/sequoia-fabrica` **404**. No Eventbrite organizer page.
- **Open Collective:** profile at `opencollective.com/sequoia-fabrica` exists (200) but `/updates.rss` and `.rss` both **404**. Financial, not events.
- **Bluesky handle guess wrong:** `sequoiafabrica.bsky.social` does **not** resolve (400). The real handle is **`sequoiafabrica.org`** (`did:plc:cydrvu3syrrfzzeej2rzqkfp`).
- **MediaWiki API is at `/w/api.php`, not `/api.php`** (root path 404s). The wiki has **no events calendar page and no calendar IDs** — searches for `calendar`, `ical`, `google calendar` returned **zero** results. `Events_at_Sequoia_Fabrica` is a prose catalogue of *past* class topics that explicitly points to Bookwhen for the live calendar.
- **`sequoia.garden`** (official secondary solar-powered Hugo site) — `/index.xml` is a **blog** RSS: 200, `text/xml`, only **3 posts, newest 2025-08-20**. Its `/feeds` page advertises that one feed only. No events, and its "Upcoming events" heading has no embedded data.
- **No gCal iframe anywhere.** Grepped homepage, wiki, Bookwhen page and `sequoia.garden` for `@group.calendar.google.com` / `c_[hex]` — the only calendar ID in existence is the one already in `references/feeds.json`.

**Crawl policy:** `sequoiafabrica.org/robots.txt` is 1,248 bytes of **Cloudflare content-signal boilerplate comments with zero actual directives** — no `User-agent`, no `Disallow`, no content-signal values set. Nothing is restricted, but note the file is a generic EU Art. 4 rights reservation template; keep to the handoff's "summary not full description" rule.

## Social

| Platform | Handle | Profile URL | Machine-readable feed | Verified | Notes |
|---|---|---|---|---|---|
| Mastodon | `@sequoiafabrica@sfba.social` | https://sfba.social/@sequoiafabrica | `https://sfba.social/@sequoiafabrica.rss` | **Yes** — 200, `application/rss+xml`, 22,549 B, 20 items | **Feed works, but the account is stale: newest post 2025-12-23**, ~7.5 months cold. Change signal only, never structured events. Low value now. |
| Bluesky | `@sequoiafabrica.org` | https://bsky.app/profile/sequoiafabrica.org | `https://bsky.app/profile/sequoiafabrica.org/rss` | **Yes** — 200, `application/xml`, 9,534 B, 20 items | **Even staler: newest post 2025-07-08.** Bio confirms the split: *"Events: bookwhen.com/sequoiafabrica / Everything else: sequoiafabrica.org"*. |
| Instagram | `sequoia.fabrica` | https://www.instagram.com/sequoia.fabrica | none | 200 reachable | **The primary public channel** — the only social linked from every page of the main site. Human channel, not a feed. Best DM route. |
| TikTok | `@sequoiafabrica` | https://www.tiktok.com/@sequoiafabrica | none | 200 reachable | Human channel. |
| X | `sequoiafabrica` | https://x.com/sequoiafabrica | none | 200 reachable | Only linked from the Bookwhen page footer, not from the main site. Probably neglected. |
| Newsletter | MailerLite | https://dashboard.mailerlite.com/forms/1197290/139047052633965606/share | none | 200 reachable | Human channel. Announced as the way to get "news delivered directly." |
| Slack | internal | — | none | — | **Internal only.** `#events`, `#general`, and per-grove channels (`#grove-fabrication` etc.). See below. |

## Contact

| Role | Name | Channel | Value | Source |
|---|---|---|---|---|
| General / Board of Directors | — | Email | **info@sequoiafabrica.org** | `sequoiafabrica.org/docs/contact` (Cloudflare-obfuscated, decoded from `data-cfemail`); also plain-text on the Bookwhen page and wiki `Contact_and_Support` |
| General | — | Phone | **(415) 935-0478** | wiki `Contact_and_Support`; Bookwhen page footer (`+14159350478`) |
| Class / event submission | — | Google Form | `docs.google.com/forms/d/e/1FAIpQLSfCmptbJKkC6oyMlUOxGp0eH845whtUunRKk5zVZ-O2koZvrw/viewform` | wiki `Events_Hosting` — "SF Class Event Submission form", processed twice a week |
| Volunteer interest | — | Google Form | `docs.google.com/forms/d/e/1FAIpQLSfsHgM2luIKGKT77cdoAMUgmRjmYRreN-xEPOYq0S1KqDI0lg/viewform` | wiki `Volunteers` |
| Contact page | — | Web | https://sequoiafabrica.org/docs/contact | main site |
| Partnerships | — | Web | https://sequoiafabrica.org/docs/partner | main site |
| President *(low confidence)* | Max Omdal | — | — | Web search only — **not confirmed on any official page**; GuideStar lists no board members. Verify with Brennan before using. |
| Secretary *(low confidence)* | Maggie Frankel | — | — | Web search only — same caveat. |
| Co-founder *(low confidence)* | Emeline Brule | — | — | Web search only — same caveat. |
| Web/blog author | Camille Teicheira | — | — | `<meta name="author">` on `sequoia.garden` |
| Physical | — | Address | 1736 18th St, San Francisco, CA 94107 | site footer, Bookwhen, wiki |

**Best outreach path:** Brennan should **skip email entirely and post in the `#events` channel
of the Sequoia Fabrica Slack** — the wiki's `Events_Hosting` page names it as the org's actual
coordination surface for anything calendar-related, and as a founding member he is already in
it. The single concrete ask is: *"can someone paste the Bookwhen calendar-feed URL from
Admin → Calendar feeds?"* That one string converts the Bookwhen HTML parser into a real Tier A
ICS feed. He should also confirm nobody objects to the public Google Calendar being
republished. For anything needing a formal/board answer, `info@sequoiafabrica.org` is both the
general and the board address.

## Recommended `sources.yaml` entry

```yaml
  - id: sequoia-fabrica
    name: Sequoia Fabrica
    city: San Francisco
    region: sf
    url: https://sequoiafabrica.org/
    sources:
      # Public classes + social nights. THE primary public calendar.
      # Prefer the real ICS once Brennan pastes the token; until then parse the
      # server-rendered agenda table, which is stable and needs no browser.
      - adapter: ics
        url: TODO   # webcal://feeds.bookwhen.com/ical/lcqebfpp6u7h/{TOKEN}/public.ics?historic_month_count=1
                    # account segment lcqebfpp6u7h confirmed from the public page's
                    # data-options "calendar" key; TOKEN only from Bookwhen admin ->
                    # "Calendar feeds". Ask in Slack #events.
        label: bookwhen-public
        trust: 100
        verified: false
      - adapter: bookwhen_html
        url: https://bookwhen.com/sequoiafabrica
        label: bookwhen-agenda
        trust: 95
        verified: true
        notes: >
          Fallback until the ICS token lands, then demote or drop. Rows are
          <tr data-hook="agenda_list_item">; data-event="ev-{entryid}-{YYYYMMDDHHMMSS}"
          carries the exact local start time, title is the row's <button> text.
          Times are America/Los_Angeles. Page returns 20 rows by default; paginate via
          /sequoiafabrica/calendar_items (returns escaped HTML in JS, not JSON) only
          if the default window ever falls short of the horizon.
      # Member/volunteer events ONLY - disjoint from Bookwhen, per the calendar's
      # own X-WR-CALDESC. Low volume, mostly monthly RRULEs, long historical tail.
      - adapter: gcal_ics
        calendar_id: c_69d095340ce714f6a0769a561fa4414c07981195eb1c9be7fde47a5cdd5450a5@group.calendar.google.com
        label: community-calendar
        trust: 90
        verified: true
        notes: >
          89 VEVENTs back to Dec 2023; only ~7 live in a 120-day horizon. Expand
          RRULEs and filter to the horizon. Mixed DTSTART forms (bare UTC Z and
          TZID=America/Los_Angeles) both occur.
```

Note `bookwhen_html` is a **new adapter** not in the current header comment block of
`sources.yaml`. If you'd rather not add one, `llm_html` would work — but the markup is
regular enough that a ~20-line deterministic parser is both cheaper and safer, and the
adapter becomes reusable for any other Bookwhen-hosted space.

## Research log

- 2026-08-05 — Read `maker-calendar-handoff.md`, `sources.yaml`, and found the pre-existing
  Google Calendar ID in `references/feeds.json`.
- 2026-08-05 — **Fetched the Google Calendar ICS: 200, `text/calendar`, 78,803 B, 89 VEVENTs.
  It resolves and is real.** Parsed it in Python: read `X-WR-CALNAME`/`X-WR-CALDESC`, built a
  year histogram of `DTSTART`, expanded RRULEs against a 120-day horizon, and checked
  `LAST-MODIFIED`/`CREATED` (newest 2026-08-01) to prove it's still maintained. The
  `X-WR-CALDESC` is what resolved the Bookwhen-vs-gCal conflict.
- 2026-08-05 — Probed 14 paths on `sequoiafabrica.org` (`/events/`, `/calendar/`, `/feed/`,
  `/feed.xml`, `/rss.xml`, `/index.xml`, `/sitemap.xml`, `/blog/`, …) — all 404 except `/` and
  `/robots.txt`. Confirmed static Next.js export with an empty `__NEXT_DATA__` and zero JSON-LD.
- 2026-08-05 — Fetched `bookwhen.com/sequoiafabrica` (200, 67,502 B). Confirmed **no JSON-LD,
  no webcal/ICS/RSS link** anywhere in the HTML. Then parsed the raw markup and extracted all
  **20 agenda rows with exact datetimes** from `data-event` attributes, proving it's a
  deterministic Tier B parse. Recovered the Bookwhen account id **`lcqebfpp6u7h`** and the
  `/sequoiafabrica/calendar_items` AJAX endpoint from the `data-options` blob.
- 2026-08-05 — Probed `feeds.bookwhen.com/ical/{lcqebfpp6u7h|sequoiafabrica}/INVALIDTOKEN/public.ics`
  → both **404 `text/calendar`**, confirming the host is live and the path shape correct while
  the token stays private. Did **not** record any guessed feed URL as working.
- 2026-08-05 — Discovered `events.sequoiafabrica.org` referenced on the wiki `Volunteers` page;
  probed 10 paths and confirmed **every one redirects to Bookwhen** — a vanity alias, not a feed.
- 2026-08-05 — Discovered `sequoia.garden` (official secondary Hugo site) via web search;
  fetched `/`, `/feeds`, `/index.xml`. Blog-only RSS, 3 posts, stale since Aug 2025.
- 2026-08-05 — Explored `wiki.sequoiafabrica.org`: found the API at `/w/api.php` (root 404s),
  pulled `Main_Page`, `Events_at_Sequoia_Fabrica`, `Events_Hosting`, `Volunteers`,
  `Contact_and_Support`, `History`, `Groves`, `Documents` as raw wikitext. Ran API searches for
  `calendar`/`ical`/`google calendar` — **no hits**, confirming no second calendar exists.
  Verified `Special:RecentChanges?feed=atom` (200, last edit 2026-08-02 → wiki is live).
- 2026-08-05 — Decoded the Cloudflare-obfuscated email on `/docs/contact` from its
  `data-cfemail` payload → **info@sequoiafabrica.org**, matching the wiki and Bookwhen footer.
- 2026-08-05 — Social sweep: verified **Mastodon `.rss` (200, 20 items, stale since 2025-12-23)**
  and **Bluesky `.rss` (200, 20 items, stale since 2025-07-08)**; resolved the correct Bluesky
  handle via `com.atproto.identity.resolveHandle` after the `.bsky.social` guess 400'd.
  Confirmed **Luma 404, Eventbrite 404, Open Collective RSS 404**. Instagram/TikTok/X/MailerLite
  reachable but feedless — logged as human channels.
