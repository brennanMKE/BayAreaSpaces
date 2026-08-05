"""Output validation — the last check before anything replaces a live file.

Deterministic assertions over the emitted artifacts, kept separate from
``health`` because these are questions about *our* output rather than about the
sources.

Responsibilities:

- Round-trip ``out/*.ics`` back through ``icalendar``: assert it parses, that the
  event count is within tolerance of what was emitted, and that no ``VEVENT`` is
  missing ``UID``, ``DTSTART`` or ``URL``.
- Assert every ``DTSTART`` is timezone-aware — a naive datetime reaching the
  output means ``normalize`` was bypassed.
- Assert UID stability against the previous run: a large UID turnover with a
  similar event count means subscribers are about to see every event as new.
- Validate ``out/feed.xml`` and ``out/health.json`` parse before publishing.

Nothing is written over a live artifact until these pass. An agent may propose,
but this module and ``health`` are deterministic code and stay that way.

Implemented alongside issue 0011 (round-trip ICS validation), and extended by
issues 0017-0018. Stub only — scaffolded by issue 0004.
"""
