"""Merged and per-space iCalendar output.

Produces the Phase 1 deliverable: the artifact that makes every later decision
easier to judge and the demo that makes outreach persuasive.

Responsibilities:

- One ``VCALENDAR`` with ``PRODID:-//brennan.sstools.co//maker-calendar//EN``,
  ``X-WR-CALNAME:Bay Area Makerspaces`` and
  ``X-WR-TIMEZONE:America/Los_Angeles``.
- **Emit expanded instances, not RRULEs.** Cross-client recurrence handling is
  where this goes wrong, and expansion is already in memory from ``ics``.
- Every ``VEVENT`` carries ``URL`` pointing at the space's own page and
  ``ORGANIZER`` naming the space — the point is to drive people to the spaces.
- Prefix ``SUMMARY`` with the space: ``[Ace] Sewing 101 Bootcamp``.
- ``DESCRIPTION`` truncated to roughly 300 characters plus a link.
- Also write per-space files under ``out/spaces/{space_id}.ics``.
- Hand the result to ``verify`` before replacing the live file.

Implemented by issue 0011 (emit merged ICS with round-trip validation). Stub
only — scaffolded by issue 0004.
"""
