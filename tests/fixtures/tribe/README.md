# Tribe REST fixtures (issue 0019)

Hand-authored, **not** captured. Each file isolates one documented behaviour of
The Events Calendar's REST API from the 2026-08-05 source survey, so a failing
test names the behaviour rather than pointing at a 92-event capture where a
dozen things are true at once.

Every fixture is dated against a pinned **today = 2026-08-05**, the survey date.
Tests pass that date explicitly; nothing here depends on the wall clock. The
event counts are deliberately small (5 across two pages, standing in for Ace's
real 92 across two pages) — pagination is what is under test, not volume.

| File | What it isolates | Recorded in |
|---|---|---|
| `ace-page-1.json` | page 1 of 2 with `next_rest_url`; `&#036;20.00`, "sliding scale &#036;10-30" and "free for members" costs; venue, organizer, categories, `is_virtual`, `ticketed` | `spaces/ace-makerspace.md` |
| `ace-page-2.json` | the last page: no `next_rest_url`, an all-day multi-day off-site event, and one event past the 120-day horizon (TEC defaults `end_date` to +2 years) | `spaces/ace-makerspace.md` |
| `empty-page.json` | HTTP 200 with `"events": []` — the end of the feed, **not** an error | issue 0019 |
| `rest-no-route.json` | WordPress's `rest_no_route` error object: valid JSON, no `events` array. **The Crucible's case** — `tribe-*` CSS class names, TEC not installed, every `wp-json/tribe/*` route 404s | `sources.yaml`, the-crucible |
| `rendered-page.html` | HTTP 200 with a rendered page instead of JSON. Note the inert `tribe-theme-avada` body class, which is exactly what must not be read as evidence | CLAUDE.md invariants |
