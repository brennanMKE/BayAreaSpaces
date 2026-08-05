"""Timezone discipline, stable UIDs, and the post-parse filters.

Turns adapter output into the canonical event schema, and is the chokepoint
where the two invariants that quietly ruin the calendar are enforced.

Responsibilities:

- **Never let a naive datetime past this module.** Parse to an aware datetime
  immediately, store UTC, carry the original tz string alongside. Assert and
  fail the run rather than guessing a zone. (Python leaves this to discipline
  where Go would enforce it structurally — this assertion is that enforcement.)
- **Stable UIDs.** If a source supplies one — every ICS feed does — keep it,
  namespaced as ``{space_id}:{source_uid}``. Otherwise
  ``sha1(space_id + start_utc + normalize(title))[:16]``, where ``normalize``
  lowercases, strips punctuation and collapses whitespace. Never include a
  scrape timestamp, page position, or any model output in a UID: if UIDs churn,
  every subscriber sees every event as new every night.
- Handle ``VALUE=DATE`` all-day events, mixed ``DTSTART`` forms within a single
  feed (bare-UTC ``Z`` alongside ``TZID=``), and Luma's multi-day-as-all-day
  behavior.
- Apply ``sources.yaml`` filters. **Filters drop events silently**, so a
  ``location_contains`` filter must decide explicitly what happens to an empty
  or non-address ``LOCATION`` — 15% of Frontier Tower's events set it to a bare
  Luma URL. Default to keeping them (``location_allow_when_missing``).

Implemented by issue 0009 (timezone discipline and stable UIDs) and issue 0010
(filters, with explicit handling for missing location). Stub only — scaffolded
by issue 0004.
"""
