# Space research notes

One file per space, all researched and verified by direct fetch on **2026-08-05**. Each
file follows the same template: summary, verified feeds (with HTTP status, content type and
event counts), unverified leads, dead ends, social accounts, contacts, and a recommended
`sources.yaml` entry.

Dead ends are recorded deliberately. They stop the next person repeating a search that has
already failed.

## Coverage

| Space | Region | Verified feeds | Tier | Notes |
|---|---|---|---|---|
| [Ace Makerspace](ace-makerspace.md) | east-bay | 3 (+7 category ICS) | A | Best source on the list. REST + ICS + JSON-LD, 92 upcoming. |
| [The Crucible](the-crucible.md) | east-bay | 4 | B | Largest clean catalog: 271 in horizon, no filtering needed. |
| [Sudo Room](sudo-room.md) | east-bay | 4 | A | 8 MB export, 73 in horizon. Stale address on every event. |
| [Lower 48](lower-48.md) | east-bay | 0 | — | No feed exists. Eventbrite organizer is real but empty. |
| [Noisebridge](noisebridge.md) | sf | 2 | A + C | No single source covers it. Wiki → RRULEs is the backbone. |
| [Sequoia Fabrica](sequoia-fabrica.md) | sf | 2 | A + B | Bookwhen (public classes) and gCal (members) are disjoint — need both. |
| [Frontier Tower](frontier-makerspace.md) | sf | 2 | A | One tower-wide calendar by policy. No per-floor feeds. |
| [Humanmade](humanmade.md) | sf | 2 | A | Own Luma calendar found; Eventbrite is empty and mis-adaptered. |
| [The Box Shop](the-box-shop.md) | sf | 2 | A + B | Mid-move. Luma calendar is unlinked from their site. |
| [Hacker Dojo](hacker-dojo.md) | peninsula | 1 | A | Own events app dead >1yr. Meetup is the only working source. |
| [Maker Nexus](maker-nexus.md) | south-bay | 3 | A | 171 future events. Needs filtering or it swamps everything. |

## Outreach

Contacts were gathered so a shared-calendar conversation can happen with the right person.
Full tables, sources and caveats are in each file.

### The ask ladder

Ask for the highest rung a space will plausibly do. Rung 1 is a materially better pitch than
rung 2, because it isn't a favor to us — it's infrastructure the space owns, and for the
hackerspace-lineage spaces it carries cultural weight that "help my side project" doesn't.

1. **Publish a [SpaceAPI](https://spaceapi.io) endpoint** with `feeds.calendar`. One small
   static JSON file, joins a standard used by 249 spaces worldwide, and every aggregator
   benefits rather than just us. It also carries `contact` and `location`, so the space's
   registry entry becomes self-maintaining — when they move or switch platforms, the JSON
   updates and we follow automatically instead of finding out via a health gate three nights
   later. Example to show them: `https://london.hackspace.org.uk/spaceapi`.
2. **Give us a stable ICS URL.**
3. **Let us keep parsing what you already publish** — and tell us what to exclude.

**SpaceAPI status, checked 2026-08-05:** of 249 registered spaces worldwide (overwhelmingly
European), **Sudo Room is the only Bay Area space listed, and its endpoint is dead** —
`api.sudoroom.org/status.json` redirects to a WordPress 404. Noisebridge isn't registered at
all. So there is nothing to harvest today; this is an outreach lever, not a data source.

That dead endpoint is the best opener on the list. They believed in this enough to register
once, and it rotted — offering to fix it and point `feeds.calendar` at the ICS they already
publish is a fix they'd want regardless of us. Same move that works for Humanmade's broken
Linktree link.

Note for whoever wires this up later: SpaceAPI's top-level `events` field is **not** calendar
events — it's recent occurrences in the space (door opened, member checkin). The calendar
lives at `feeds.calendar`.

| Space | Contact | The ask |
|---|---|---|
| **Ace Makerspace** | `officers@acemakerspace.org`, attn Sylvia Gonzalez (Chair & CEO), cc `info@` | Nothing needed — their feeds work. "We're ingesting your public feeds correctly; tell us what to change or exclude." **Use this one as the demo for everyone else.** |
| **The Crucible** | `info@thecrucible.org` attn Melissa Gray (Senior Programs Manager), cc `registrar@` | Their catalog already parses cleanly, so this is a courtesy plus one question: does Salesforce hold per-*meeting* session times? The site exposes one start per multi-week run. Friendly opener: their contact page's mailto is misspelled `registrar@thecrucibile.org`. Leadership is mid-transition — Doug Yeiser is *interim* ED, superseding the older Seth Steward announcement. |
| **Lower 48** | Jolie Karno `jolie@lower48.org`, cc `hello@lower48.org`; Instagram `@lower48woodshop` is the only channel with 2026 activity | Two asks: keep posting classes to their Eventbrite organizer (the adapter is proven, the page is just empty), and what is the shared fabrication building at 1212 19th St called — does it run a tenant-wide calendar? Also ask permission explicitly: their robots.txt disallows AI agents by name. |
| **Sudo Room** | `sudo-discuss@sudoroom.org` (open, publicly archived), cc `info@` | Data quality: every event carries the pre-2014 `549 48th St` address; the export ignores `scope`/`limit` and returns 8 MB expanded to 2058; `/event-request/` throws "Unauthorized Access" at the public. |
| **Omni Commons** ⭐ | `commons@lists.omnicommons.org` (Commons WG, meets 2nd & 4th Mon 5pm) | **Highest leverage on the list.** Switch on Airtable's native iCal sync for the "Public Calendar" view. One toggle yields a Tier-A feed covering Sudo Room, Counter Culture Labs, Liberated Lens and the rest — and CCL has no feed at all today. |
| **Noisebridge** | `secretary@noisebridge.net`, and the **Tuesday 7pm consensus meeting** (in person + Jitsi) | Put the standing weekly schedule into the existing Luma calendar as recurring events, and either revive or unpublish the dead "Noisebridge Daily" gCal their front page still embeds. No calendar owner exists by design, so the meeting is where this lands. Mailing lists are offline. |
| **Sequoia Fabrica** | Slack `#events` — Brennan is a founding member | Two minutes of self-service: paste the Bookwhen ICS token from admin → "Calendar feeds". Everything else about this space already works. |
| **Frontier Tower** | `events@frontiertower.io` (the address their own organizer guide gives hosts) | A read-only key for the Frontier OS `events:listEvents` / `events:listLocations` API — `listLocations` would give canonical room names and end the location-string guesswork. Failing that, a consistent location string or Luma tag for maker events. |
| **Humanmade** | `info@humanmade.org`, attn Amber Anderson (Program Manager & Outreach), cc Sandra Spurlock (Director of Programs & Ops) | Cheapest ask of the batch: they already run a public Luma calendar — put project workshops on it too, and give it a public slug. Free opener: their Linktree's "Workshops at Humanmade" button points at `www.eventbrite.humanmade.com`, which fails DNS. |
| **The Box Shop** | `info@boxshopsf.org`; **Kyana** is the named organizer on every Luma event, Alita Edgar co-hosts. Instagram DM `@boxshopsf` is likely faster | Confirm the Van Dyke move date so published addresses don't go stale, and keep using the Luma calendar. Note it isn't linked from their own site. |
| **Hacker Dojo** | `info@hackerdojo.com` (note `.com`), attn Qi Diaz (ED), naming Eva Carrender (events). Discord is a live back channel | Strongest pitch of the batch: their events app has 525'd for over a year, they cross-post by hand to two platforms, and the only public feed truncates at 10 events. Offer the merged feed, ask for the internal list they already keep. |
| **Maker Nexus** | Jen Harte (Business Development & Events Manager), cc Sarah Kramer (Education Manager) | An Amilia API key — `app.amilia.com/api/v3/store/makernexus/activities` returns **401, not 404**, so it exists and is merely gated. Also ask whether a members-only calendar exists. |

## What this survey changed

- **11 spaces, 30 sources, 25 verified.** Nine registry `TODO`s resolved to verified URLs;
  one remains (the Sequoia Fabrica Bookwhen token, obtainable only from their admin panel).
- **Two `gcal_ics` stubs deleted** — the Sudo Room Google Calendars the brief described do
  not exist; that page embeds Airtable and WP-FullCalendar categories.
- **Four sources corrected**: Humanmade's Eventbrite adapter (`jsonld` → `nextdata`),
  Frontier's venue filter (matched ~9 of 262 events), Ace's `luma.com/acemakers` (a
  different organization entirely), and Noisebridge's `noisebridge.today` (a parked domain).
- **Silent-failure traps documented** — endpoints returning HTTP 200 with the wrong content,
  one returning 404 with a valid RSS body, one returning 404 with an RSS *content-type* over
  an HTML body, and one returning 200 with valid JSON containing 98 of 353 products because
  a sort parameter was omitted.
- **Four new health-gate cases** and three new adapters (`bookwhen_html`, `json`,
  `embedded_json`), all now in `sources.yaml` and `CLAUDE.md`.
- **19 additional Bay Area spaces discovered** via `makernexuswiki.com`, listed at the
  bottom of `sources.yaml`.
