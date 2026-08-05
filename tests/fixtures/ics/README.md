# ICS fixtures (issue 0007)

Hand-authored, **not** captured. Each file isolates exactly one documented trap
from the 2026-08-05 source survey, so a failing test names the trap rather than
pointing at an 8 MB export where twenty things are true at once. Captured feeds
would be more "real" and much less useful: Sudo Room's is 8 MB, Maker Nexus's is
11 MB, and none of them can be committed or diffed sensibly.

Every fixture is dated against a pinned **today = 2026-08-05**, the date the
source survey was run. Tests pass that date explicitly; nothing here depends on
the wall clock.

| File | The trap it isolates | Recorded in |
|---|---|---|
| `allday-value-date.ics` | `DTSTART;VALUE=DATE` all-day, exclusive `DTEND` | handoff, "Extraction, in order" |
| `floating-no-tzid.ics` | floating local time, no `TZID`, no `X-WR-TIMEZONE` | handoff |
| `sequoia-mixed-dtstart.ics` | bare-UTC `Z` **and** `TZID=` in one feed | `spaces/sequoia-fabrica.md` |
| `noisebridge-unbounded-rrule.ics` | five RRULEs with no `UNTIL`, feed dead since 2024 | `spaces/noisebridge.md` |
| `sudoroom-preexpanded.ics` | recurrences pre-expanded to 2058, history to 2012 | `spaces/sudo-room.md` |
| `luma-multiday.ics` | Luma's multi-day-as-all-day encoding | handoff, "Platform notes" |
| `ace-tribe.ics` | stable composite UID, `CATEGORIES`, `LAST-MODIFIED` | `spaces/ace-makerspace.md` |
| `homepage.html` | 200 with `text/html` — the `?ical=1` homepage case | CLAUDE.md invariants |
