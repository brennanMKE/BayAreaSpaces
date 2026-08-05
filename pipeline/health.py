"""Publish gates, ``health.json``, and alerting.

**Health gates run before anything is published.** The failure that bites is not
a crash — it is a source quietly going wrong while the run succeeds — so this
module can block publication and alert instead.

Global gates: a source dropping to zero, or a total count drop over 40%
night-over-night, blocks the publish step.

Counting events is necessary but not sufficient. The 2026-08-05 source survey
found four cases a count-based gate gets wrong, each with a per-source override
in ``sources.yaml``:

- ``max_stale_days`` — an abandoned feed that keeps generating. Noisebridge's old
  gCal has 28 VEVENTs, dead since Jan 2024, five RRULEs with **no ``UNTIL``**: it
  invents ~5 events a week forever at a constant rate and passes every
  count-based gate. Checked against ``LAST-MODIFIED``.
- ``allow_zero`` — legitimately empty. The Box Shop runs ~8-12 events a year.
- ``ignore_count_drop`` — capped or truncated feed. Hacker Dojo's Meetup iCal has
  a hard 10-event cap and no pagination, so nightly deltas are noise.
- ``require_nonzero_once`` — never yet non-zero. Humanmade's Eventbrite organizer
  has 0 upcoming, so "went to zero" can never fire but a naive alert fires nightly.

Two further rules from the same survey:

- **Count post-expansion events inside the horizon, not VEVENTs.** Sequoia
  Fabrica has 89 VEVENTs and ~7 live ones; Maker Nexus has 3645 and 171. Raw
  counts say almost nothing about whether a feed is healthy.
- **A short publishing horizon is not a decline.** Maker Nexus posts 4-8 weeks
  ahead, so the far end of a 120-day window is legitimately empty every night.

``health.json`` is also the trigger for repair work: a source at zero for three
consecutive nights is what sends someone at that one space with OpenCode.

Implemented by issue 0016 (health gates), issue 0017 (emit ``health.json``) and
issue 0032 (health alerting). Stub only — scaffolded by issue 0004.
"""
