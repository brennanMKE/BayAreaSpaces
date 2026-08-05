# Google Calendar fixtures (issue 0008)

Hand-authored, **not** captured — the real feeds are 79 KB and 11 MB, and neither
can be committed or diffed sensibly. Each file isolates one thing the `gcal_ics`
wrapper has to get right, dated against the pinned **today = 2026-08-05**.

| File | What it isolates | Recorded in |
|---|---|---|
| `sequoia-community-calendar.ics` | `X-WR-CALDESC` (the line that proved the calendar is disjoint from Bookwhen), open-ended monthly RRULEs, mixed `DTSTART` forms, history outside the horizon | `spaces/sequoia-fabrica.md` |
| `maker-nexus-classes.ics` | pre-expanded instances with no RRULEs, HTML `DESCRIPTION`, history back to 2023-12, and one event past the 120-day horizon | `spaces/maker-nexus.md` |
| `not-found.html` | Google's 404 page — the negative control a bogus calendar id produces | `issues/0008.md` |

`X-WR-CALNAME` is present in both and is **never** a source label: Maker Nexus's
calendar is named "Amilia Published Classes" and Humanmade's Luma calendar is
named "Personal". The registry's `label` is authoritative.
