"""Cross-source and cross-space collision handling.

The same event legitimately arrives twice: a space's own calendar and a partner
or aggregator feed, or two sources registered for one space. Merge them into one
entry without losing the better copy.

Responsibilities:

- Match candidates on start time plus fuzzy title similarity (``rapidfuzz``),
  scoped so that unrelated spaces holding a class with the same generic name
  ("Open Shop Night") are not collapsed together.
- Break ties by the ``trust`` value in ``sources.yaml``: higher wins, and a
  space's own site always outranks an aggregator or a partner's calendar.
- Preserve the surviving event's UID so subscribers do not see a churn event.

Implemented by issue 0015 (dedupe across sources and spaces). Stub only —
scaffolded by issue 0004.
"""
