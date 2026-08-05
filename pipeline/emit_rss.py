"""RSS/Atom output for the "what's on this week" feed.

Builds ``out/feed.xml`` with ``feedgen`` from the same deduped, enriched event
set that ``emit_ics`` publishes, so the two never disagree.

Responsibilities:

- One item per upcoming event, using the enriched one-line summary rather than
  the full source description — a ~300-character summary sidesteps the copyright
  question and reads better merged.
- Link back to the space's own page on every item; that is the whole point.
- Stable ``guid`` derived from the event UID, so an item is not re-announced
  every night.

Implemented by issue 0018 (emit RSS). Stub only — scaffolded by issue 0004.
"""
